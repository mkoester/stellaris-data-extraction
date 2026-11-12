"""Extraction: Galactic Community resolutions (with their categories and groups) and the names of
subject agreement terms, term values and presets. A resolution's effects are the static modifier
of the same key (`static_modifier`)."""

import json
from pathlib import Path
import sys
from .clausewitz import Parser, tokenize
from .traditions import _Ctx, _load, dumps
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _resolve_var, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def category_members(categories: dict[str, dict]) -> dict[str, str]:
    """Resolution key → its category, from each category's `resolution_types` list (the first
    category naming a resolution wins)."""
    out: dict[str, str] = {}
    for cat, data in categories.items():
        for r in _to_list(data.get('resolution_types')):
            if isinstance(r, str):
                out.setdefault(r, cat)
    return out


def influence_cost(data: dict, variables: dict[str, float]) -> float | None:
    """`resources = { cost = { influence = N } }` → N (an @variable resolved), else None."""
    for res in _as_blocks(data.get('resources')):
        for cost in _as_blocks(res.get('cost')):
            if cost.get('influence') is not None:
                return _resolve_var(cost['influence'], variables)
    return None


def ai_weight_model(data: dict, variables: dict[str, float], trig) -> dict | None:
    """`ai_weight = { base modifier = { factor|add … <trigger> desc } }` → `{base, modifiers:
    [{factor, add, trigger}]}`, each trigger normalized by `trig(block, variables)` (the AI's own
    scope; `from` is the proposer). None without an ai_weight."""
    blocks = _as_blocks(data.get('ai_weight'))
    if not blocks:
        return None
    aw = blocks[0]
    mods = []
    for m in _as_blocks(aw.get('modifier')):
        cond = {k: v for k, v in m.items() if k not in ('factor', 'add', 'desc')}
        mods.append({
            'factor': _resolve_var(m['factor'], variables) if m.get('factor') is not None else None,
            'add': _resolve_var(m['add'], variables) if m.get('add') is not None else None,
            'trigger': trig(cond, variables) if cond else None
        })
    base = aw.get('base', aw.get('weight'))
    return {'base': _resolve_var(base, variables) if base is not None else None, 'modifiers': mods}


def yes(v) -> int:
    return 1 if v == 'yes' else 0


def flat_modifiers(block, variables: dict[str, float]) -> dict[str, float]:
    """A modifier block's numeric entries (@variables resolved); nested blocks are left out."""
    out: dict[str, float] = {}
    for b in _as_blocks(block):
        for k, v in b.items():
            if isinstance(v, (str, int, float)):
                x = _resolve_var(v, variables)
                if x is not None:
                    out[k] = float(x)
    return out


def keyed_levels(tokens: list[str]) -> list[str]:
    """`levels = { { … } { … } }` with each anonymous block keyed `level = { … }`: the parser
    drops anonymous blocks (`clausewitz.Parser`), and a specialist type's levels are nothing else."""
    out: list[str] = []
    depth = 0
    inside = None  # the depth of an open `levels` block's content
    for i, t in enumerate(tokens):
        if t == '{':
            if inside is not None and depth == inside:
                out += ['level', '=']
            if inside is None and i >= 2 and tokens[i - 2] == 'levels' and tokens[i - 1] == '=':
                inside = depth + 1
            depth += 1
        elif t == '}':
            depth -= 1
            if inside is not None and depth == inside - 1:
                inside = None
        out.append(t)
    return out


def specialist_levels(data: dict, variables: dict[str, float]) -> list[dict]:
    """A specialist type's `levels = { level = { experience_needed_for_next_level perks } … }`
    (anonymous blocks keyed by `keyed_levels`) → one dict per level, numbered from 1."""
    out = []
    levels = data.get('levels') if isinstance(data.get('levels'), dict) else {}
    for n, lvl in enumerate(_as_blocks(levels.get('level')), start=1):
        xp = _resolve_var(lvl.get('experience_needed_for_next_level'), variables)
        out.append({'level': n, 'xp_next': float(xp) if xp is not None else None,
                    'perks': [p for p in _to_list(lvl.get('perks')) if isinstance(p, str)]})
    return out


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_diplomacy(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                      w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    # --- resolution groups and categories, in file order ---
    groups = _load(common / 'resolution_groups', scripted_vars)
    for order, key in enumerate(groups):
        w.execute("INSERT INTO resolution_group (key, name, sort_order) VALUES (?, ?, ?)",
                  (key, ctx.text(key), order))
    cats = {k: d for k, (d, _, _) in _load(common / 'resolution_categories', scripted_vars).items()}
    for order, (key, data) in enumerate(cats.items()):
        group = data.get('group') if isinstance(data.get('group'), str) else None
        w.execute(
            "INSERT INTO resolution_category (key, group_key, name, sort_order) VALUES (?, ?, ?, ?)",
            (key, group, ctx.text(key), order))

    # --- resolutions ---
    member_of = category_members(cats)
    resolutions = _load(common / 'resolutions', scripted_vars)
    for order, (key, (data, rvars, _)) in enumerate(resolutions.items()):
        level = data.get('level')
        w.execute(
            """INSERT INTO resolution (key, category_key, name, description, level, target,
               influence_cost, repeal, potential_json, allow_json, valid_target_json,
               ai_weight_json, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, member_of.get(key), ctx.text(key), ctx.text(f'{key}_desc', True, rvars),
             int(float(str(level))) if level is not None else None, yes(data.get('target')),
             influence_cost(data, rvars), yes(data.get('fire_and_forget')),
             dumps(ctx.trig(data.get('potential'), rvars)),
             dumps(ctx.trig(data.get('allow'), rvars)),
             dumps(ctx.trig(data.get('valid_target'), rvars)),
             dumps(ai_weight_model(data, rvars, ctx.trig)), order))

    # --- subject agreement terms, their values and presets: names only ---
    terms = _load(common / 'agreement_terms', scripted_vars)
    for order, (key, (data, _, _)) in enumerate(terms.items()):
        w.execute(
            """INSERT INTO agreement_term (key, name, term_type, hidden, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, ctx.text(key), data.get('term_type') if isinstance(data.get('term_type'), str)
             else None, yes(data.get('hidden')), order))
    values = _load(common / 'agreement_term_values', scripted_vars)
    for order, (key, (data, _, _)) in enumerate(values.items()):
        term = data.get('term') if isinstance(data.get('term'), str) else None
        w.execute(
            "INSERT INTO agreement_term_value (key, term_key, name, sort_order) VALUES (?, ?, ?, ?)",
            (key, term, ctx.text(key), order))
    presets = _load(common / 'agreement_presets', scripted_vars)
    for order, key in enumerate(presets):
        w.execute("INSERT INTO agreement_preset (key, name, sort_order) VALUES (?, ?, ?)",
                  (key, ctx.text(key), order))

    # --- subject specialisations: types, levels and perks ---
    types: dict = {}
    for f in sorted((common / 'specialist_subject_types').glob('*.txt')):
        parsed = Parser(keyed_levels(tokenize(f.read_text(encoding='utf-8-sig', errors='replace')))).parse()
        local = {k: float(v) for k, v in parsed.items() if k.startswith('@') and _resolve_var(v, {}) is not None}
        for k, v in parsed.items():
            if not k.startswith('@'):
                types[k] = (v, {**scripted_vars, **local}, f.name)
    n_types = 0
    for key, (data, tvars, _) in types.items():
        if key == 'any' or not isinstance(data, dict) or not data.get('levels'):
            continue
        ethic = data.get('preferred_ethic') if isinstance(data.get('preferred_ethic'), str) else None
        w.execute("INSERT INTO specialist_type (key, name, preferred_ethic, sort_order) VALUES (?, ?, ?, ?)",
                  (key, ctx.text(key), ethic, n_types))
        for lvl in specialist_levels(data, tvars):
            w.execute("INSERT INTO specialist_level (type_key, level, xp_next, perks_json) VALUES (?, ?, ?, ?)",
                      (key, lvl['level'], lvl['xp_next'], json.dumps(lvl['perks'])))
        n_types += 1
    perks = _load(common / 'specialist_subject_perks', scripted_vars)
    for order, (key, (data, pvars, _)) in enumerate(perks.items()):
        sub = flat_modifiers(data.get('subject_modifier'), pvars)
        over = {**flat_modifiers(data.get('overlord_modifier'), pvars),
                **flat_modifiers(data.get('overlord_relay_network_modifier'), pvars)}
        w.execute(
            """INSERT INTO specialist_perk (key, name, description, main_perk, subject_json,
               overlord_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(key), ctx.text(f'{key}_desc', True, pvars), yes(data.get('main_perk')),
             json.dumps(sub) if sub else None, json.dumps(over) if over else None, order))

    print(f"  {len(resolutions)} resolutions in {len(cats)} categories, {len(terms)} agreement "
          f"terms, {len(values)} term values, {len(presets)} presets, {n_types} specialist types",
          file=progress)
    w.commit()
