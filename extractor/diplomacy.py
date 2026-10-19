"""Extraction: Galactic Community resolutions (with their categories and groups) and the names of
subject agreement terms, term values and presets. A resolution's effects are the static modifier
of the same key (`static_modifier`)."""

from pathlib import Path
import sys
from .traditions import _Ctx, _load
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


def yes(v) -> int:
    return 1 if v == 'yes' else 0


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
               influence_cost, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, member_of.get(key), ctx.text(key), ctx.text(f'{key}_desc', True, rvars),
             int(float(str(level))) if level is not None else None, yes(data.get('target')),
             influence_cost(data, rvars), order))

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

    print(f"  {len(resolutions)} resolutions in {len(cats)} categories, {len(terms)} agreement "
          f"terms, {len(values)} term values, {len(presets)} presets", file=progress)
    w.commit()
