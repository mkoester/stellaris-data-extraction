"""Extraction: what the empire dashboard needs to value resources and predict the AI's trades —
the strategic resources' market lots and AI weights, the AI personalities' trade willingness,
which attitudes trade at all, opinion modifiers (a gift's "Favorable Trade Deal"), and the names
of starbase modules and buildings (their costs go to `starbase_*_resource`)."""

from pathlib import Path
import sys
from .traditions import _Ctx, _load
from .values import _resolve_var, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def base_value(block, variables: dict[str, float]) -> float | None:
    """`x = { base = 500 modifier = {…} }` or `x = { weight = 1.5 … }` → the unconditional value
    (500, 1.5); the `modifier` factors are conditions on the empire and left out."""
    if not isinstance(block, dict):
        return _resolve_var(block, variables)
    raw = block.get('base', block.get('weight'))
    return _resolve_var(raw, variables) if raw is not None else None


def flag(value) -> int:
    return 1 if value == 'yes' else 0


def resource_row(data: dict, variables: dict[str, float]) -> tuple:
    """(category, tradable, market_amount, market_price, max, ai_weight, ai_wants)."""
    return (data.get('category') if isinstance(data.get('category'), str) else None,
            flag(data.get('tradable')),
            _resolve_var(data.get('market_amount'), variables),
            _resolve_var(data.get('market_price'), variables),
            _resolve_var(data.get('max'), variables),
            base_value(data.get('ai_weight'), variables),
            base_value(data.get('ai_wants'), variables))


def attitude_trades(data: dict) -> int:
    """An attitude's `behaviour = { trade = yes }`: the AI considers trade deals at all."""
    b = data.get('behaviour')
    return flag(b.get('trade')) if isinstance(b, dict) else 0


def slot_count(value) -> int:
    """`module_slots = { 1 2 }` → 2 (the slots' numbers, one per slot); absent → 0."""
    return len(_to_list(value)) if value is not None else 0


def opinion_row(data: dict, variables: dict[str, float]) -> tuple:
    """(opinion, accumulative, min, max, decay): the base values; `modifier` factors left out."""
    return (base_value(data.get('opinion'), variables), flag(data.get('accumulative')),
            _resolve_var(data.get('min'), variables), _resolve_var(data.get('max'), variables),
            base_value(data.get('decay'), variables))


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_economy(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                    w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    ctx = _Ctx(loc, {}, w)

    resources = _load(common / 'strategic_resources', scripted_vars)
    for order, (key, (data, rvars, _)) in enumerate(resources.items()):
        w.execute(
            """INSERT INTO strategic_resource (key, name, category, tradable, market_amount,
               market_price, max, ai_weight, ai_wants, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(key), *resource_row(data, rvars), order))

    personalities = _load(common / 'personalities', scripted_vars)
    for key, (data, pvars, _) in personalities.items():
        w.execute("INSERT INTO personality (key, name, trade_willingness) VALUES (?, ?, ?)",
                  (key, ctx.text(f'personality_{key}'),
                   _resolve_var(data.get('trade_willingness'), pvars)))

    attitudes = _load(common / 'attitudes', scripted_vars)
    for key, (data, _, _) in attitudes.items():
        w.execute("INSERT INTO attitude (key, name, trade) VALUES (?, ?, ?)",
                  (key, ctx.text(f'attitude_{key}'), attitude_trades(data)))

    opinions = _load(common / 'opinion_modifiers', scripted_vars)
    for key, (data, ovars, _) in opinions.items():
        w.execute(
            """INSERT INTO opinion_modifier (key, name, opinion, accumulative, min, max, decay)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", (key, ctx.text(key), *opinion_row(data, ovars)))

    levels = _load(common / 'starbase_levels', scripted_vars)
    for key, (data, _, _) in levels.items():
        w.execute(
            """INSERT INTO starbase_level (key, ship_size, next_level, module_slots, building_slots)
               VALUES (?, ?, ?, ?, ?)""",
            (key, data.get('ship_size') if isinstance(data.get('ship_size'), str) else None,
             data.get('next_level') if isinstance(data.get('next_level'), str) else None,
             slot_count(data.get('module_slots')), slot_count(data.get('building_slots'))))

    n_parts = 0
    for kind, folder in (('module', 'starbase_modules'), ('building', 'starbase_buildings')):
        for key in _load(common / folder, scripted_vars):
            w.execute("INSERT OR REPLACE INTO starbase_part (key, kind, name) VALUES (?, ?, ?)",
                      (key, kind, ctx.text(f'sm_{key}')))
            n_parts += 1
    w.commit()
    print(f"  {len(resources)} resources, {len(personalities)} personalities, "
          f"{len(attitudes)} attitudes, {len(opinions)} opinion modifiers, "
          f"{len(levels)} starbase levels, {n_parts} starbase modules and buildings", file=progress)
