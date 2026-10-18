"""Extraction: the empire focus (common/country_focus): categories, cards and rewards."""

from pathlib import Path
import json
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc, inline_refs
from .values import _as_blocks
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def research_options(block) -> list[str]:
    """The techs an effect block offers (`add_research_option`), every if/else branch included."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k == 'add_research_option':
                    for t in (v if isinstance(v, list) else [v]):
                        if isinstance(t, str) and t not in out:
                            out.append(t)
                else:
                    walk(v)
        chain = getattr(node, 'chain', None)  # an IfBlock keeps its else_if/else apart
        for _, blk in chain or []:
            walk(blk)

    walk(block)
    return out


def requirement_keys(block) -> list[str]:
    """The `success_text` loc keys of a card's `condition_for_achievement` tooltips."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k == 'success_text' and isinstance(v, str):
                    out.append(v.strip('"'))
                else:
                    walk(v)

    walk(block)
    return out


def with_base(cards: dict[str, dict]) -> dict[str, dict]:
    """Cards with the fields of their `base = <card>` filled in (99_HOW_TO_FOCUS.txt)."""
    out = {}
    for key, d in cards.items():
        b = cards.get(d.get('base')) if isinstance(d.get('base'), str) else None
        out[key] = {**(b or {}), **d}
    return out


def _text(loc: dict[str, str], key, newlines: bool = False) -> str | None:
    key = key.strip('"') if isinstance(key, str) else None
    raw = loc.get(key) if key else None
    return _clean_loc(inline_refs(raw, loc), loc, keep_newlines=newlines) or None if raw else None


def _num(v) -> float | None:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


# ── Extraction ────────────────────────────────────────────────────────────────

def _entries(d: Path):
    for f in sorted(d.glob('*.txt')) if d.is_dir() else []:
        for key, data in parse_file(f).items():
            if not key.startswith('@') and isinstance(data, dict):
                yield key, data


def extract_focus(install_dir: Path, loc: dict[str, str], w: Writer, progress=sys.stderr) -> None:
    base = install_dir / 'common' / 'country_focus'
    n = {'cat': 0, 'card': 0, 'reward': 0}
    for order, (key, d) in enumerate(_entries(base / 'card_categories')):
        w.execute(
            """INSERT OR REPLACE INTO focus_category (key, name, description, selectable, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, _text(loc, d.get('name')), _text(loc, d.get('desc'), True),
             0 if d.get('selectable') == 'no' else 1, order))
        n['cat'] += 1
    cards = with_base(dict(_entries(base / 'focus_cards')))
    for order, (key, d) in enumerate(cards.items()):
        reqs = [t for t in (_text(loc, k) for k in requirement_keys(d.get('condition_for_achievement'))) if t]
        w.execute(
            """INSERT OR REPLACE INTO focus_card (key, category, tier, name, hint, requirement, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, d.get('category'), _num(d.get('tier')), _text(loc, d.get('name')),
             _text(loc, d.get('hint'), True), '\n'.join(reqs) or None, order))
        n['card'] += 1
    for order, (key, d) in enumerate(_entries(base / 'focus_rewards')):
        techs = research_options(_as_blocks(d.get('on_success')))
        w.execute(
            """INSERT OR REPLACE INTO focus_reward (key, category, cost, techs_json, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, d.get('category'), _num(d.get('cost')), json.dumps(techs) if techs else None, order))
        n['reward'] += 1
    print(f"  {n['cat']} focus categories, {n['card']} cards, {n['reward']} rewards", file=progress)
    w.commit()
