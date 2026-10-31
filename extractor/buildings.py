"""Extraction: building reference data and building effect helpers."""

from pathlib import Path
import re
import sys
from .clausewitz import parse_file
from .dds import dds_pixel, hex_color
from .government import _gate_owners
from .localisation import _clean_loc
from .modifiers import _write_modifier_label
from .politics import expand_inline
from .situations import outcomes_json
from .triggers import _scripted_trigger_index
from .values import _as_blocks, _flatten_text, _resolve_modifier_float, _resolve_var
from .writer import Writer


def extract_buildings_ref(install_dir: Path, loc: dict[str, str],
                          scripted_vars: dict[str, float], inline_scripts_dir: Path,
                          w: Writer, verbose: bool) -> None:
    """Extract common/buildings → building (+ building_resource, building_job,
    building_modifier, building_unlock): each building's category, description,
    build/upkeep resources, the jobs it provides, its other modifiers and the civics /
    origins it requires. Reuses the precursor building helpers (`_building_resources`,
    `_building_inline_jobs`, `_building_modifiers`)."""
    bdir = install_dir / 'common' / 'buildings'
    if not bdir.is_dir():
        print(f"  WARNING: {bdir} not found", file=sys.stderr)
        return
    triggers = _scripted_trigger_index(install_dir)
    order = 0
    for f in sorted(bdir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, bdef in data.items():
            if key.startswith('@') or not isinstance(bdef, dict):
                continue
            raw_name = loc.get(key)
            if not raw_name:
                continue  # skip un-named/internal building entries
            name = _clean_loc(raw_name, loc)
            raw_desc = loc.get(key + '_desc')
            desc = _clean_loc(raw_desc, loc, keep_newlines=True) if raw_desc else None
            category = bdef.get('category')
            category = category if isinstance(category, str) else None
            w.execute(
                "INSERT OR REPLACE INTO building (key, name, category, description, sort_order) VALUES (?, ?, ?, ?, ?)",
                (key, name, category, desc, order))
            order += 1
            for r_order, (kind, resource, amt) in enumerate(
                    _building_resources(bdef, variables, script_loader(inline_scripts_dir))):
                w.execute(
                    """INSERT OR IGNORE INTO building_resource
                           (building_key, kind, resource, amount, sort_order) VALUES (?, ?, ?, ?, ?)""",
                    (key, kind, resource, amt, r_order))
            _write_build_time(w, key, bdef, variables)
            for j_order, (jk, amt) in enumerate(_building_inline_jobs(bdef, variables, inline_scripts_dir)):
                job = re.sub(r'^job_', '', re.sub(r'_add$', '', jk))
                w.execute(
                    "INSERT OR IGNORE INTO building_job (building_key, job, amount, sort_order) VALUES (?, ?, ?, ?)",
                    (key, job, amt, j_order))
            # Jobs are building_job rows already; the rest are the building's effects.
            mods = [m for m in _building_modifiers(bdef, variables, script_loader(inline_scripts_dir))
                    if not re.fullmatch(r'job_\w+_add', m[1])]
            for m_order, (scope, mk, base, _ae, cond) in enumerate(mods):
                w.execute(
                    """INSERT OR IGNORE INTO building_modifier
                           (building_key, scope, modifier_key, value, condition, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, scope, mk, base, cond, m_order))
                _write_modifier_label(w, loc, mk)
            for owner in building_unlocks(bdef, triggers):
                w.execute("INSERT OR IGNORE INTO building_unlock (owner_key, building_key) VALUES (?, ?)",
                          (owner, key))
    w.commit()
    if verbose:
        print(f"  buildings: {order}")


def script_loader(inline_scripts_dir: Path):
    """name → an inline script's text, or None when the game has no such script."""
    def load(name: str) -> str | None:
        path = inline_scripts_dir / f'{name}.txt'
        return path.read_text(encoding='utf-8-sig') if path.exists() else None
    return load


def extract_resources(directory: Path, table: str, scripted_vars: dict[str, float],
                      inline_scripts_dir: Path, w: Writer, verbose: bool,
                      jobs_table: str | None = None) -> None:
    """Every definition in `directory` with a resources block → `table` (key, kind, resource,
    amount): districts' and ship sizes' cost and upkeep; its `base_buildtime` → `build_time`;
    with `jobs_table`, the jobs its `jobs/*` inline scripts add (districts). Per file, since
    `@variables` resolve per file (4.5.2 `@base_cost`: 300 in rural districts, 30 in wilderness
    ones)."""
    n = 0
    for f in sorted(directory.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, ddef in sorted(data.items()):
            if key.startswith('@') or not isinstance(ddef, dict):
                continue
            for kind, resource, amt in _building_resources(ddef, variables, script_loader(inline_scripts_dir)):
                w.execute(f"INSERT OR REPLACE INTO {table} (key, kind, resource, amount) VALUES (?, ?, ?, ?)",
                          (key, kind, resource, amt))
                n += 1
            _write_build_time(w, key, ddef, variables)
            if jobs_table:
                for jk, amt in _building_inline_jobs(ddef, variables, inline_scripts_dir):
                    job = re.sub(r'^job_', '', re.sub(r'_add$', '', jk))
                    w.execute(f"INSERT OR IGNORE INTO {jobs_table} (key, job, amount) VALUES (?, ?, ?)",
                              (key, job, amt))
    w.commit()
    if verbose:
        print(f"  {table}: {n} rows")


def _write_build_time(w: Writer, key: str, ddef: dict, variables: dict[str, float]) -> None:
    """A definition's `base_buildtime` (starbase modules and buildings: `construction_days`; days,
    before modifiers) → build_time, when it has one."""
    days = _resolve_var(ddef.get('base_buildtime', ddef.get('construction_days')), variables)
    if days is not None:
        w.execute("INSERT OR REPLACE INTO build_time (key, days) VALUES (?, ?)", (key, days))


def _building_cap(raw, variables: dict[str, float]) -> int | None:
    """A building build-cap → int, or None when uncapped. Accepts a flat value
    (`base_cap_amount = 1`) or a block whose `base` is the baseline cap
    (`empire_limit = { base = 1 … }`)."""
    if isinstance(raw, list):
        raw = next((b for b in reversed(raw) if isinstance(b, dict)), raw[-1] if raw else None)
    if isinstance(raw, dict):
        raw = raw.get('base')
    v = _resolve_var(raw, variables)
    return int(v) if v is not None else None


def _building_resources(bdef: dict, variables: dict[str, float], load_script=None
                        ) -> list[tuple[str, str, float]]:
    """A building's (district's, ship size's) resources block → [(kind, resource, amount)] for
    kind in cost/upkeep/produces, resolving @vars. Where a kind has several blocks
    (e.g. a conditional `is_wilderness_empire` cost), the unconditional one (no
    `trigger`) is the primary, else the first — the regular empire's: 4.5.2's
    `buildings/nomadic_cost_switcher` and the stations' `country_uses_bio_ships = no` come
    first. Blocks from `resources.inline_script` count too: `load_script(name)` → the
    script's text, or None (4.5.2 buildings declare their cost only so)."""
    rows: list[tuple[str, str, float]] = []
    res = bdef.get('resources')
    if isinstance(res, list):
        res = next((b for b in reversed(res) if isinstance(b, dict)), None)
    if not isinstance(res, dict):
        return rows
    inlined = _expand_inline_refs(res, load_script)
    for kind in ('cost', 'upkeep', 'produces'):
        blocks = [b for src in (res, *inlined) for b in _as_blocks(src.get(kind)) if isinstance(b, dict)]
        block = next((b for b in blocks if 'trigger' not in b), blocks[0] if blocks else None)
        if not isinstance(block, dict):
            continue
        for resource, raw in block.items():
            if resource in ('category', 'trigger', 'mult'):
                continue
            amt = _resolve_var(raw, variables)
            if amt is not None:
                rows.append((kind, resource, amt))
    return rows


# Building modifier sources: (scope tag, field, is_triggered). The `triggered_`
# variants carry a `potential` gate — we keep their value but tag the condition.
_BUILDING_MOD_FIELDS = (
    ('planet', 'planet_modifier', False),
    ('country', 'country_modifier', False),
    ('planet', 'triggered_planet_modifier', True),
    ('country', 'triggered_country_modifier', True),
    ('system', 'triggered_system_modifier', True),
    ('station', 'triggered_station_modifier', True),
)
_BUILDING_MOD_SKIP = frozenset({'potential', 'trigger', 'multiplier', 'mult', 'category',
                                'modifier', 'desc', 'replace'})


_AE_PERK = 'ap_archaeoengineers'


def building_unlocks(bdef: dict, triggers: dict[str, dict]) -> list[str]:
    """The civics/origins a building hard-requires, sorted: positive `has_civic` /
    `has_valid_civic` / `has_origin` tests in potential/allow/can_build that aren't one
    alternative among other conditions (Fear of the Dark's early Factory is an OR with
    `country_uses_consumer_goods`, so it doesn't count; an OR of one civic's variants does)."""
    out: set[str] = set()
    for blk in ('potential', 'allow', 'can_build'):
        for key, negated, disj in _gate_owners(bdef.get(blk, {}), triggers):
            if not negated and not disj and key.startswith(('civic_', 'origin_')):
                out.add(key)
    return sorted(out)


def _perk_gate(block: dict) -> tuple[str, bool] | None:
    """A triggered modifier's ascension-perk gate as (perk_key, negated), or None
    when it has no such gate. `negated` is True for a NOT-wrapped requirement (the
    'base, only without the perk' variant)."""
    txt = _flatten_text(block.get('potential'))
    m = re.search(r'has_ascension_perk\s*=\s*(ap_\w+)', txt)
    if not m:
        return None
    pre = txt[:m.start()].rstrip()
    negated = bool(re.search(r'NO[RT]\s*=$', pre)) or bool(re.search(r'\bNO[RT]\b[^=]{0,16}$', pre))
    return (m.group(1), negated)


def _expand_inline_refs(block: dict, load_script, only: frozenset[str] | None = None) -> list[dict]:
    """The `inline_script`s a block names (those in `only`, when given), their `$PARAM$`s filled
    in and parsed; [] without a `load_script` (name → text or None)."""
    return [expand_inline(text, {k: str(v).strip('"') for k, v in ref.items() if k != 'script'})
            for ref in _as_blocks(block.get('inline_script'))
            if load_script and isinstance(ref.get('script'), str)
            and (only is None or ref['script'].strip('"') in only)
            for text in [load_script(ref['script'].strip('"'))] if text]


# The building-level inline scripts whose modifiers count. Only these: the others (capital,
# wilderness, jobs/*_add, …) were never read, and reading them changes 4.5.2's values too.
_MODIFIER_SCRIPTS = frozenset({'jobs/fe_automation'})


def _building_modifiers(bdef: dict, variables: dict[str, float], load_script=None
                        ) -> list[tuple[str, str, float, float | None, str | None]]:
    """A building's modifiers, aggregated per (scope, key) into
    [(scope, key, base_value, ae_value, condition)]. Blocks from the `_MODIFIER_SCRIPTS` the
    building inlines count too (`load_script` as for `_building_resources`): 4.5.3 moved the
    Fallen Empire buildings' job automation into `jobs/fe_automation`.

    base_value is the value with no ascension perks; ae_value is the *total* with
    Archaeo-Engineers (None when unchanged). A block gated `NOT archaeoengineers`
    contributes to base only (it's replaced under the perk); an un-perk-gated block
    contributes to both (the AE bonus stacks on it); an `archaeoengineers` block
    contributes to the AE total only. Blocks gated by a *different* perk become their
    own rows tagged with that perk key in `condition`."""
    # acc[(scope, key)] = {'base': float, 'ae': float, 'ae_seen': bool, 'other': {perk: float}}
    acc: dict[tuple[str, str], dict] = {}

    def slot(scope, key):
        return acc.setdefault((scope, key),
                              {'base': 0.0, 'ae': 0.0, 'ae_seen': False, 'other': {}})

    sources = (bdef, *_expand_inline_refs(bdef, load_script, _MODIFIER_SCRIPTS))
    for scope, field, triggered in _BUILDING_MOD_FIELDS:
        for block in (b for src in sources for b in _as_blocks(src.get(field))):
            gate = _perk_gate(block) if triggered else None
            for key, raw in block.items():
                if key in _BUILDING_MOD_SKIP:
                    continue
                val = _resolve_var(raw, variables)
                if val is None:
                    continue
                s = slot(scope, key)
                if gate is None:                       # always applies (base and AE)
                    s['base'] += val
                    s['ae'] += val
                elif gate[0] == _AE_PERK and gate[1]:  # base only (NOT archaeoengineers)
                    s['base'] += val
                elif gate[0] == _AE_PERK:              # the Archaeo-Engineers value
                    s['ae'] += val
                    s['ae_seen'] = True
                else:                                  # gated by a different perk
                    s['other'][gate[0]] = s['other'].get(gate[0], 0.0) + val

    rows: list[tuple[str, str, float, float | None, str | None]] = []
    for (scope, key), s in acc.items():
        if s['ae_seen'] or s['base']:
            ae = s['ae'] if s['ae_seen'] and abs(s['ae'] - s['base']) > 1e-9 else None
            rows.append((scope, key, s['base'], ae, None))
        for perk, v in s['other'].items():
            rows.append((scope, key, v, None, perk))
    return rows


def _building_inline_jobs(bdef: dict, variables: dict[str, float], inline_scripts_dir
                          ) -> list[tuple[str, float]]:
    """Jobs a building adds via its `jobs/*` inline_scripts → [(job_<x>_add, amount)].

    Each jobs script branches by empire type; the first triggered_planet_modifier
    is the regular-empire variant (gestalt/FE empires get calculator/archivist
    equivalents of the same count). The building supplies the count as `AMOUNT`."""
    rows: list[tuple[str, float]] = []
    for ref in _as_blocks(bdef.get('inline_script')):
        script = ref.get('script')
        if not isinstance(script, str) or not script.startswith('jobs/'):
            continue
        amount = _resolve_var(ref.get('AMOUNT'), variables)
        if amount is None:
            continue
        path = inline_scripts_dir / (script + '.txt')
        if not path.exists():
            continue
        try:
            blocks = _as_blocks(parse_file(path).get('triggered_planet_modifier'))
        except Exception:
            continue
        if not blocks:
            continue
        first = blocks[0]
        modblock = first.get('modifier') if isinstance(first.get('modifier'), dict) else first
        for k in modblock:
            if k.startswith('job_') and k.endswith('_add'):
                rows.append((k, amount))
    return rows


def _district_color(install_dir: Path, key: str, ddef: dict) -> str | None:
    """A district's colour in game: the opaque border of its grid-box icon
    (`gfx/interface/icons/districts/grid_box/<icon>.dds`, `icon` defaulting to the key)."""
    icon = ddef.get('icon') if isinstance(ddef.get('icon'), str) else key
    path = install_dir / 'gfx' / 'interface' / 'icons' / 'districts' / 'grid_box' / f'{icon}.dds'
    return hex_color(dds_pixel(path.read_bytes(), 0, 0)) if path.exists() else None


def extract_district_zone_names(install_dir: Path, districts: dict, zones: dict, loc: dict[str, str],
                                w: Writer, verbose: bool) -> None:
    """Display names of districts and zones (district specialisations), for the empire
    dashboard's colony view: `district_city` → "City District", `zone_research_unity` →
    "Archives". Keys without localisation keep a NULL name. Districts also get their colour."""
    blocks = lambda data: sorted((k, v) for k, v in data.items() if isinstance(v, dict) and not k.startswith('@'))
    for key, ddef in blocks(districts):
        name = _clean_loc(loc[key], loc) if key in loc else None
        color = _district_color(install_dir, key, ddef)
        w.execute("INSERT OR REPLACE INTO district (key, name, color) VALUES (?, ?, ?)", (key, name, color))
        if verbose:
            print(f"  district: {key} = {name} {color}")
    for key, _ in blocks(zones):
        name = _clean_loc(loc[key], loc) if key in loc else None
        w.execute("INSERT OR REPLACE INTO zone (key, name) VALUES (?, ?)", (key, name))
        if verbose:
            print(f"  zone: {key} = {name}")
    w.commit()


def extract_anomaly_levels(categories: dict, loc: dict[str, str], w: Writer, verbose: bool) -> None:
    """Anomaly categories (common/anomalies) with their `level` (with the scientist's level it picks
    the research time from SCIENCE_SHIP_ANOMALY_RESEARCH_DAYS), name and outcome events — for the
    empire dashboard."""
    for key in sorted(k for k, v in categories.items() if isinstance(v, dict) and not k.startswith('@')):
        cat = categories[key]
        try:
            level = int(str(cat.get('level')).strip())
        except (ValueError, TypeError):
            level = None
        desc = cat.get('desc').strip('"') if isinstance(cat.get('desc'), str) else f'{key}_desc'
        raw_name, raw_desc = loc.get(key), loc.get(desc)
        w.execute(
            """INSERT OR REPLACE INTO anomaly_category (key, level, name, description, outcomes_json)
               VALUES (?, ?, ?, ?, ?)""",
            (key, level, _clean_loc(raw_name, loc) if raw_name else None,
             _clean_loc(raw_desc, loc, keep_newlines=True) if raw_desc else None,
             outcomes_json(cat.get('on_success'), loc)))
        if verbose:
            print(f"  anomaly_category: {key} level={level}")
    w.commit()
