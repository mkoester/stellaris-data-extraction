"""Extraction: federation types (with their level perks) and federation laws."""

from pathlib import Path
import json
import sys
from .modifiers import _block_modifier_values, _write_modifier_label
from .traditions import UNLOCK_LEAVES, _Ctx, _load, dumps, unlock_keys
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _resolve_var, _to_list
from .writer import Writer


# What decides who may form a federation type: traditions/perks, ethics and civics.
FEDERATION_LEAVES = UNLOCK_LEAVES + ('has_ethic', 'has_civic', 'has_valid_civic')

# Perk modifier blocks → the scope the planner shows them under; *_triggered_* blocks
# carry their own potential.
PERK_MODIFIER_SCOPES = {
    'members_modifier': 'members',
    'member_triggered_modifier': 'members',
    'federation_modifier': 'federation',
    'leader_modifier': 'leader',
    'leader_triggered_modifier': 'leader',
    'envoy_modifier': 'envoy',
}

# Event-only federation types: their laws are skipped along with them.
EVENT_TYPES = ('imperial_loyalists_federation',)


# ── Pure helpers ───────────────────────────────────────────────────────────────

def never_available(tree: dict | None) -> bool:
    """A normalised potential that can never hold (`always = no`)."""
    return tree == {'leaf': 'always', 'value': 'no'}


def level_perks(levels, variables: dict[str, float]) -> list[tuple[int, float | None, list[str]]]:
    """A type's `levels` block → [(level, XP to the next level or None, perk keys)], in order.
    Level keys are `level_<n>`."""
    out = []
    for k, v in (levels or {}).items():
        if not (k.startswith('level_') and k[6:].isdigit() and isinstance(v, dict)):
            continue
        xp = _resolve_var(v.get('experience'), variables) if v.get('experience') is not None else None
        perks = [p for p in _to_list(v.get('perks')) if isinstance(p, str)]
        out.append((int(k[6:]), xp, perks))
    return sorted(out, key=lambda x: x[0])


def law_types(potential, idx: dict[str, dict]) -> list[str]:
    """The federation types a law's potential limits it to (positive has_federation_type)."""
    return unlock_keys(potential, idx, leaves=('has_federation_type',))


def perk_effect_keys(data: dict) -> list[str]:
    """Loc keys of a perk's effect text: top-level custom_tooltip strings, then the
    `description` of each modifier block."""
    keys = [x for x in _to_list(data.get('custom_tooltip')) if isinstance(x, str)]
    for k in PERK_MODIFIER_SCOPES:
        for b in _as_blocks(data.get(k)):
            if isinstance(b.get('description'), str):
                keys.append(b['description'])
    return list(dict.fromkeys(keys))


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_federations(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                        w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    idx = _scripted_trigger_index(install_dir)
    ctx = _Ctx(loc, dlc_trigger_map(idx), w)

    def keys_json(keys: list[str]) -> str | None:
        return json.dumps(keys) if keys else None

    # --- types + their level perks ---
    types = _load(common / 'federation_types', scripted_vars)
    used_perks: list[str] = []
    n_types = 0
    for order, (key, (data, tvars, _)) in enumerate(types.items()):
        potential = ctx.trig(data.get('potential'), tvars)
        if never_available(potential):
            continue
        n_types += 1
        allow = data.get('allow')
        try:
            intel = int(float(str(data.get('base_intel')))) if data.get('base_intel') is not None else None
        except ValueError:
            intel = None
        w.execute(
            """INSERT INTO federation_type (key, name, description, base_intel, potential_json,
               allow_json, unlocked_by_json, excluded_by_json, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(key), ctx.text(f'{key}_desc', True, tvars), intel, dumps(potential),
             dumps(ctx.trig(allow, tvars)),
             keys_json(unlock_keys(allow, idx, FEDERATION_LEAVES)),
             keys_json(unlock_keys(allow, idx, FEDERATION_LEAVES, negated=True)), order))
        n = 0
        for level, xp, perks in level_perks(data.get('levels'), tvars):
            for perk in perks:
                w.execute(
                    """INSERT INTO federation_type_perk (type_key, level, experience, perk_key, sort_order)
                       VALUES (?, ?, ?, ?, ?)""", (key, level, xp, perk, n))
                n += 1
                if perk not in used_perks:
                    used_perks.append(perk)

    # --- perks (only those a type grants) ---
    perks = _load(common / 'federation_perks', scripted_vars)
    for order, key in enumerate(used_perks):
        if key not in perks:
            print(f"  WARNING: federation perk {key} not defined", file=sys.stderr)
            continue
        data, pvars, _ = perks[key]
        effect = '\n'.join(t for t in (ctx.text(k, True, pvars) for k in perk_effect_keys(data)) if t)
        w.execute(
            """INSERT INTO federation_perk (key, name, description, effect, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, ctx.text(key), ctx.text(f'{key}_desc', True, pvars), effect or None, order))
        n = 0
        for block_key, scope in PERK_MODIFIER_SCOPES.items():
            for b in _as_blocks(data.get(block_key)):
                pot = dumps(ctx.trig(b.get('potential'), pvars))
                for mk, mv in _block_modifier_values(b, pvars):
                    w.execute(
                        """INSERT INTO federation_perk_modifier
                           (perk_key, scope, modifier_key, modifier_value, potential_json, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?)""", (key, scope, mk, mv, pot, n))
                    _write_modifier_label(w, loc, mk)
                    n += 1

    # --- law categories + laws, in each category's `laws` order ---
    cats = _load(common / 'federation_law_categories', scripted_vars)
    laws = _load(common / 'federation_laws', scripted_vars)
    n_laws = 0
    for order, (ckey, (cdata, cvars, _)) in enumerate(cats.items()):
        w.execute(
            """INSERT INTO federation_law_category (key, name, description, potential_json, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (ckey, ctx.text(ckey), ctx.text(f'{ckey}_desc', True, cvars),
             dumps(ctx.trig(cdata.get('potential'), cvars)), order))
        for lkey in (x for x in _to_list(cdata.get('laws')) if isinstance(x, str)):
            if lkey not in laws:
                print(f"  WARNING: federation law {lkey} not defined", file=sys.stderr)
                continue
            data, lvars, _ = laws[lkey]
            only = law_types(data.get('potential'), idx)
            if any(t in EVENT_TYPES for t in only):
                continue
            centr = None
            for b in _as_blocks(data.get('required_centralization')):
                centr = _resolve_var(b.get('base'), lvars)
            cohesion = (_resolve_var(data.get('cohesion_growth'), lvars)
                        if data.get('cohesion_growth') is not None else None)
            w.execute(
                """INSERT INTO federation_law (key, category_key, name, description, potential_json,
                   allow_json, types_json, required_centralization, cohesion_growth, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lkey, ckey, ctx.text(lkey), ctx.text(f'{lkey}_desc', True, lvars),
                 dumps(ctx.trig(data.get('potential'), lvars)),
                 dumps(ctx.trig(data.get('allow'), lvars)), keys_json(only),
                 int(centr) if centr is not None else None, cohesion, n_laws))
            n_laws += 1

    print(f"  {n_types} federation types, {len(used_perks)} perks, {len(cats)} law categories, "
          f"{n_laws} laws", file=progress)
    w.commit()
