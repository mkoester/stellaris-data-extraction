"""Extraction: what the empire dashboard needs to show ship designs and plan starbases.

- Ship designs (`ship_size`, `section_template`, `component_template`): a design in the save names
  its size, each section's template and each slot's component; these tables give their base stats
  (hull, speed, evasion; the sections' slots; the components' power, modifiers and — from
  `component_templates/*.csv`, where the game keeps them — weapon stats) and costs. Empire-wide
  modifiers (techs, traditions) are not applied: these are base values.
- Starbase parts (`starbase_part` columns, `starbase_part_modifier`): each module's and building's
  category, `potential` / `possible` and its modifiers per scope (station, system, planet,
  country, orbit, …), the `triggered_*` ones with their `potential` as a trigger.
"""

import csv
import io
import json
from pathlib import Path
import sys

from .buildings import _building_resources
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values
from .traditions import owner_scope
from .triggers import _scripted_trigger_index, dlc_trigger_map, normalize_trigger
from .values import _resolve_modifier_float, _resolve_var, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def blocks_of(value) -> list[dict]:
    """A key's value as its blocks: duplicate keys are promoted to a list by the parser."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [b for b in value if isinstance(b, dict)]
    return []


def file_variables(data: dict, scripted_vars: dict[str, float]) -> dict[str, float]:
    """The @variables a file's definitions see: the scripted ones, then the file's own."""
    local = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                local[k] = f
    return local


def modifier_dict(blocks, variables: dict[str, float]) -> dict[str, float]:
    """Every `modifier = {…}` block's stats summed by key (several blocks happen)."""
    out: dict[str, float] = {}
    for b in blocks_of(blocks):
        for k, v in _block_modifier_values(b, variables):
            out[k] = out.get(k, 0.0) + v
    return out


def resources_json(block: dict, variables: dict[str, float]) -> str | None:
    """`resources = { cost = {…} upkeep = {…} }` → {"cost": {res: amount}, …}; None if empty."""
    out: dict[str, dict[str, float]] = {}
    for kind, resource, amount in _building_resources(block, variables):
        out.setdefault(kind, {})[resource] = amount
    return json.dumps(out, sort_keys=True) if out else None


def dumps(value) -> str | None:
    return json.dumps(value, sort_keys=True) if value else None


def size_row(key: str, data: dict, variables: dict[str, float]) -> tuple:
    """(class, hitpoints, speed, size_multiplier, is_station, section_slots_json,
    required_sets_json, modifiers_json) of a ship size."""
    slots = data.get('section_slots')
    return (data.get('class') if isinstance(data.get('class'), str) else None,
            _resolve_var(data.get('max_hitpoints'), variables),
            _resolve_var(data.get('max_speed'), variables),
            _resolve_var(data.get('size_multiplier'), variables),
            1 if data.get('is_space_station') == 'yes' else 0,
            dumps([str(k).strip('"') for k in slots]) if isinstance(slots, dict) else None,
            dumps([str(s) for s in _to_list(data.get('required_component_set'))]),
            dumps(modifier_dict(data.get('modifier'), variables)))


UTILITY_SIZES = ('small', 'medium', 'large', 'aux')


def section_row(block: dict, variables: dict[str, float]) -> tuple:
    """(ship_size, slot, component_slots_json, utility_slots_json, modifiers_json) of a section
    template: its weapon slots in order as [{name, template}] (`template` the slot's kind,
    `small_turret`, `point_defence_turret`, …) and its utility slots per size."""
    weapons = [{'name': str(s.get('name')), 'template': str(s.get('template'))}
               for s in blocks_of(block.get('component_slot'))]
    utility = {}
    for size in UTILITY_SIZES:
        n = _resolve_var(block.get(f'{size}_utility_slots'), variables)
        if n:
            utility[size] = int(n)
    return (block.get('ship_size') if isinstance(block.get('ship_size'), str) else None,
            block.get('fits_on_slot') if isinstance(block.get('fits_on_slot'), str) else None,
            dumps(weapons), dumps(utility), dumps(modifier_dict(block.get('modifier'), variables)))


COMPONENT_KINDS = {'utility_component_template': 'utility',
                   'weapon_component_template': 'weapon',
                   'strike_craft_component_template': 'strike_craft'}


def component_row(kind: str, block: dict, variables: dict[str, float],
                  weapon_stats: dict[str, dict]) -> tuple:
    """(kind, size, component_set, type, power, tags_json, modifiers_json, weapon_json) of a
    component. A weapon's stats are its row in the weapon CSVs; its power there too when the
    template sets none."""
    key = str(block.get('key'))
    stats = weapon_stats.get(key)
    power = _resolve_var(block.get('power'), variables)
    if power is None and stats is not None:
        power = stats.get('power')
    tags = block.get('tags')
    return (kind,
            block.get('size') if isinstance(block.get('size'), str) else None,
            block.get('component_set') if isinstance(block.get('component_set'), str) else None,
            block.get('type') if isinstance(block.get('type'), str) else None,
            power,
            dumps([str(t) for t in _to_list(tags)]) if tags is not None else None,
            dumps(modifier_dict(block.get('modifier'), variables)),
            dumps({k: v for k, v in stats.items() if v} if stats else None))


def weapon_csv(text: str) -> dict[str, dict[str, float]]:
    """`component_templates/*.csv` (`;`-separated, `#` comments, a `key;cost;power;…;end` header)
    → key → {column: value}, numbers only. Pure."""
    out: dict[str, dict[str, float]] = {}
    header: list[str] | None = None
    for row in csv.reader(io.StringIO(text), delimiter=';'):
        if not row or not row[0] or row[0].lstrip().startswith('#'):
            continue
        if row[0] == 'key':
            header = row
            continue
        if header is None:
            continue
        stats: dict[str, float] = {}
        for col, raw in zip(header[1:], row[1:]):
            if col in ('', 'end'):
                continue
            try:
                stats[col] = float(raw)
            except ValueError:
                pass
        out[row[0]] = stats
    return out


# Starbase part modifier blocks: `<scope>_modifier` and `triggered_<scope>_modifier`.
PART_SCOPES = ('station', 'system', 'planet', 'country', 'orbit', 'ship', 'defense_platform',
               'waystation_network_system', 'waystation_network_country')


def part_modifier_rows(data: dict, variables: dict[str, float], trig
                       ) -> list[tuple[str, str, float, str | None]]:
    """A starbase part's modifiers → [(scope, modifier_key, value, trigger_json)] in file order of
    the scopes above; `trig(block)` normalises a `triggered_*` block's `potential` (JSON, or None
    for an unconditional block)."""
    rows: list[tuple[str, str, float, str | None]] = []
    for scope in PART_SCOPES:
        for b in blocks_of(data.get(f'{scope}_modifier')):
            rows += [(scope, k, v, None) for k, v in _block_modifier_values(b, variables)]
        for b in blocks_of(data.get(f'triggered_{scope}_modifier')):
            t = trig(b.get('potential'))
            cond = json.dumps(t, sort_keys=True) if t else None
            rows += [(scope, k, v, cond) for k, v in _block_modifier_values(b, variables)]
    return rows


# ── Extraction ────────────────────────────────────────────────────────────────

def _parsed(d: Path, pattern: str = '*.txt'):
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return
    for f in sorted(d.glob(pattern)):
        try:
            yield f, parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)


def extract_ship_designs(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                         w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    text = lambda key: _clean_loc(loc[key], loc) if loc.get(key) else None  # noqa: E731

    n_sizes = 0
    for _, data in _parsed(common / 'ship_sizes'):
        variables = file_variables(data, scripted_vars)
        for key, block in data.items():
            if key.startswith('@') or not isinstance(block, dict):
                continue
            w.execute("INSERT OR REPLACE INTO ship_size (key, name, class, hitpoints, speed, size_multiplier, is_station, section_slots_json, required_sets_json, modifiers_json, resources_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (key, text(key), *size_row(key, block, variables),
                       resources_json(block, variables)))
            n_sizes += 1

    n_sections = 0
    for _, data in _parsed(common / 'section_templates'):
        variables = file_variables(data, scripted_vars)
        for block in blocks_of(data.get('ship_section_template')):
            key = block.get('key')
            if not isinstance(key, str):
                continue
            w.execute("INSERT OR REPLACE INTO section_template (key, ship_size, slot, component_slots_json, utility_slots_json, modifiers_json, resources_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (key, *section_row(block, variables), resources_json(block, variables)))
            n_sections += 1

    weapon_stats: dict[str, dict[str, float]] = {}
    cdir = common / 'component_templates'
    for f in sorted(cdir.glob('*.csv')) if cdir.is_dir() else []:
        weapon_stats.update(weapon_csv(f.read_text(encoding='utf-8-sig', errors='replace')))
    n_components = 0
    for _, data in _parsed(cdir):
        variables = file_variables(data, scripted_vars)
        for field, kind in COMPONENT_KINDS.items():
            for block in blocks_of(data.get(field)):
                key = block.get('key')
                if not isinstance(key, str):
                    continue
                w.execute("INSERT OR REPLACE INTO component_template (key, name, kind, size, component_set, type, power, tags_json, modifiers_json, weapon_json, resources_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (key, text(key), *component_row(kind, block, variables, weapon_stats),
                           resources_json(block, variables)))
                n_components += 1
    w.commit()
    print(f"  {n_sizes} ship sizes, {n_sections} section templates, {n_components} components "
          f"({len(weapon_stats)} weapon stat rows)", file=progress)


def extract_starbase_parts(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                           w: Writer, progress=sys.stderr) -> None:
    """`starbase_part` (name from `sm_<key>`, category, conditions) and `starbase_part_modifier`."""
    triggers = _scripted_trigger_index(install_dir)
    dlc = dlc_trigger_map(triggers)
    n_parts = n_rows = 0
    for kind, folder in (('module', 'starbase_modules'), ('building', 'starbase_buildings')):
        for _, data in _parsed(install_dir / 'common' / folder):
            variables = file_variables(data, scripted_vars)
            # `owner = { … }` is the country's own conditions (techs, perks, civics): kept as a
            # tree the dashboard can answer, not an opaque scope
            norm = lambda b: normalize_trigger(b, dlc, variables)  # noqa: E731
            hook = owner_scope(norm)
            trig = lambda b: normalize_trigger(b, dlc, variables, hook) if b else None  # noqa: E731
            for key, block in data.items():
                if key.startswith('@') or not isinstance(block, dict):
                    continue
                cat = block.get('category')
                name = _clean_loc(loc[f'sm_{key}'], loc) if loc.get(f'sm_{key}') else None
                w.execute("INSERT OR REPLACE INTO starbase_part (key, kind, name, category, potential_json, possible_json) VALUES (?, ?, ?, ?, ?, ?)",
                          (key, kind, name, cat if isinstance(cat, str) else None,
                           dumps(trig(block.get('potential'))), dumps(trig(block.get('possible')))))
                n_parts += 1
                for order, (scope, mod, value, cond) in enumerate(
                        part_modifier_rows(block, variables, trig)):
                    w.execute("INSERT OR REPLACE INTO starbase_part_modifier (key, scope, modifier_key, value, trigger_json, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                              (key, scope, mod, value, cond, order))
                    n_rows += 1
    w.commit()
    print(f"  {n_parts} starbase parts, {n_rows} modifier rows", file=progress)
