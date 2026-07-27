"""Scripted-trigger index and trigger-block evaluation (requirements, swap signals)."""

from pathlib import Path
from .clausewitz import parse_file
from .values import _as_blocks, _to_list


def _trigger_values(sub) -> list[tuple[str, list[str]]]:
    """Parse a civic gating sub-block (ethics / authority / civics / origin) into
    [(operator, [values])]: a bare `value =` → 'require'; `NOT`/`NOR` → 'forbid';
    `OR` → 'require_any'."""
    out: list[tuple[str, list[str]]] = []
    if not isinstance(sub, dict):
        return out
    direct = [v for v in _to_list(sub.get('value')) if isinstance(v, str)]
    if direct:
        out.append(('require', direct))
    for neg in ('NOT', 'NOR'):
        for nb in _as_blocks(sub.get(neg)):
            vals = [v for v in _to_list(nb.get('value')) if isinstance(v, str)]
            if vals:
                out.append(('forbid', vals))
    for ob in _as_blocks(sub.get('OR')):
        vals = [v for v in _to_list(ob.get('value')) if isinstance(v, str)]
        if vals:
            out.append(('require_any', vals))
    return out


def _scripted_trigger_index(install_dir: Path) -> dict[str, dict]:
    """name → parsed block for every common/scripted_triggers definition."""
    idx: dict[str, dict] = {}
    sdir = install_dir / 'common' / 'scripted_triggers'
    if not sdir.is_dir():
        return idx
    for f in sorted(sdir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        for k, v in data.items():
            if isinstance(v, dict):
                idx[k] = v
    return idx


def _collect_swap_sources(trigger, idx: dict[str, dict]) -> set[tuple[str, str]]:
    """Recursively collect ('civic'|'origin', key) referenced by a swap trigger, expanding
    nested scripted-trigger calls (`some_trigger = yes`). Best-effort: NOT/NOR negations
    aren't distinguished and building-gated swaps (has_active_building) aren't followed."""
    out: set[tuple[str, str]] = set()
    seen: set[str] = set()

    def vals(v):
        return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)]

    civic_keys = ('has_civic', 'has_valid_civic', 'has_country_civic')
    origin_keys = ('has_origin', 'has_valid_origin')

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in civic_keys:
                    for x in vals(v):
                        out.add(('civic', x))
                elif k in origin_keys:
                    for x in vals(v):
                        out.add(('origin', x))
                elif isinstance(v, (dict, list)):
                    walk(v)
                elif str(v).strip() == 'yes' and k in idx and k not in seen:
                    seen.add(k)
                    walk(idx[k])
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(trigger)
    return out


# Trigger keys that don't assert an empire/planet condition (structural scopes/counters).
_SIGNAL_SKIP = {'exists', 'count', 'limit', 'years', 'amount', 'percentage'}


def _signals(node, idx: dict[str, dict]) -> tuple[set[str], set[str]]:
    """(positive, negative) normalised condition tokens a trigger asserts, expanding nested
    scripted_triggers down to leaf checks so equivalent phrasings unify (e.g. both
    `industrial_jobs_are_catalytic_trigger = yes` and `owner = { is_catalytic_empire = yes }`
    reduce to the same `has_valid_civic=civic_*` set). `= no` / NOT / NOR flip the sign."""
    pos: set[str] = set()
    neg: set[str] = set()
    seen: set[str] = set()

    def handle(k: str, v, negate: bool) -> None:
        if k in _SIGNAL_SKIP:
            return
        if k in ('NOT', 'NOR', 'NAND'):
            walk(v, not negate)
            return
        if k in ('OR', 'AND', 'hidden_trigger', 'custom_tooltip', 'count_trigger'):
            walk(v, negate)
            return
        if isinstance(v, dict):
            walk(v, negate)            # scope, e.g. owner = { … }
            return
        if isinstance(v, list):
            for x in v:
                if isinstance(x, (dict, list)):
                    walk(x, negate)
                else:
                    handle(k, x, negate)
            return
        s = str(v).strip()
        if k in idx and s in ('yes', 'no') and k not in seen:
            seen.add(k)
            walk(idx[k], negate if s == 'yes' else not negate)
            return
        if s == 'yes':
            (neg if negate else pos).add(k)
        elif s == 'no':
            (pos if negate else neg).add(k)
        else:
            (neg if negate else pos).add(f"{k}={s}")

    def walk(n, negate: bool) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                handle(k, v, negate)
        elif isinstance(n, list):
            for x in n:
                walk(x, negate)

    walk(node, False)
    return pos, neg
