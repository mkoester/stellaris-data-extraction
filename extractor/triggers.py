"""Scripted-trigger index and trigger-block evaluation (requirements, swap signals)."""

from pathlib import Path
from .clausewitz import parse_file
from .values import _as_blocks, _to_list


def _trigger_values(sub) -> list[tuple[str, list[str]]]:
    """Parse a civic gating sub-block (ethics / authority / civics / origin) into
    [(operator, [values])]: a bare `value =` → 'require'; `NOT`/`NOR` → 'forbid';
    `OR` → 'require_any'."""
    out: list[tuple[str, list[str]]] = []
    # A gate repeated in one block (`species_archetype = {…} species_archetype = {…}`)
    # is promoted to a list by the parser; each entry gates independently.
    if isinstance(sub, list):
        return [r for item in sub for r in _trigger_values(item)]
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


# ---------------------------------------------------------------------------
# Trigger trees as data (traditions, ascension perks)
# ---------------------------------------------------------------------------
# A trigger block normalises to a JSON-serialisable tree the planner evaluates:
#   {'op': 'and'|'or'|'not', 'args': [...]}  |  {'leaf': key, 'value': v[, 'cmp': '>']}
# Scripted triggers stay leaves (the planner knows the empire-type ones), except those
# that are only a DLC check, which are inlined to `host_has_dlc` so DLC reads uniformly.

_LOGIC = {'AND': 'and', 'OR': 'or', 'NOT': 'not_and', 'NOR': 'not_or', 'NAND': 'not_and'}
# Keys inside trigger blocks that carry display text, not conditions.
_TEXT_KEYS = {'fail_text', 'success_text', 'text', 'tooltip'}
# Opaque leaf for constructs the tree can't express faithfully.
COMPLEX = {'leaf': 'complex', 'value': 'yes'}


def dlc_trigger_map(idx: dict[str, dict]) -> dict[str, str]:
    """Scripted triggers that are nothing but `host_has_dlc = "X"` → X
    (e.g. has_machine_age_dlc → "The Machine Age")."""
    out: dict[str, str] = {}
    for name in sorted(idx):
        body = idx[name]
        if isinstance(body, dict) and list(body) == ['host_has_dlc'] \
                and isinstance(body['host_has_dlc'], str):
            out[name] = body['host_has_dlc']
    return out


def _and(args: list) -> dict | None:
    args = [a for a in args if a is not None]
    if not args:
        return None
    return args[0] if len(args) == 1 else {'op': 'and', 'args': args}


def _not(node: dict | None) -> dict | None:
    if node is None:
        return None
    if node.get('op') == 'not':
        return node['args'][0]
    return {'op': 'not', 'args': [node]}


def normalize_trigger(block, dlc_triggers: dict[str, str] | None = None,
                      variables: dict[str, float] | None = None) -> dict | None:
    """Parsed trigger block → normalised tree (None for an empty block). Pure."""
    from .clausewitz import Cmp
    dlc_triggers = dlc_triggers or {}
    variables = variables or {}

    def scalar(v) -> str:
        s = str(v).strip()
        if s.startswith('@') and s in variables:
            f = variables[s]
            return str(int(f)) if f == int(f) else str(f)
        return s

    def leaf(k: str, v) -> dict | None:
        if isinstance(v, Cmp):
            return {'leaf': k, 'cmp': v.op, 'value': scalar(v.value)}
        if isinstance(v, (dict, list)):
            # A scope or iterator (owner = {…}, any_country = {…}): kept opaque.
            return {'leaf': k, 'value': 'scope'}
        s = scalar(v)
        if k in dlc_triggers and s in ('yes', 'no'):
            node = {'leaf': 'host_has_dlc', 'value': dlc_triggers[k]}
            return node if s == 'yes' else _not(node)
        return {'leaf': k, 'value': s}

    def if_chain(d: dict) -> dict | None:
        """The `if` blocks of d, each with its else_if/else chain (IfBlock.chain).
        A lone if is an implication; a chain is an OR of branches, each guarded by the
        negation of the earlier limits."""
        parts = []
        for b in _to_list_blocks(d.get('if')):
            chain = [('if', b), *getattr(b, 'chain', [])]
            if len(chain) == 1:
                lim, body = walk(b.get('limit')), walk_body(b)
                parts.append({'op': 'or', 'args': [_and([lim, body]), _not(lim)]}
                             if lim and body else body)
                continue
            branches, prev = [], []
            for kind, blk in chain:
                lim = walk(blk.get('limit')) if kind != 'else' else None
                branches.append(_and([*(_not(p) for p in prev if p), lim, walk_body(blk)]))
                prev.append(lim)
            if all(prev):  # every branch is limited, so none may be taken: no condition
                branches.append(_and([_not(p) for p in prev]))
            branches = [x for x in branches if x is not None]
            parts.append(branches[0] if len(branches) == 1 else {'op': 'or', 'args': branches})
        if not parts and (d.get('else_if') or d.get('else')):
            return COMPLEX  # an else without a parsed if (not from the parser)
        return _and(parts)

    def walk_body(b: dict) -> dict | None:
        return walk({k: v for k, v in b.items() if k != 'limit'})

    def walk(node) -> dict | None:
        return _and(walk_args(node))

    def walk_args(node) -> list:
        """The conditions of a block, un-combined (its caller decides and/or)."""
        if not isinstance(node, dict):
            return []
        args: list = []
        for k, v in node.items():
            if k in _TEXT_KEYS or k in ('if', 'else_if', 'else'):
                continue  # if-chains are handled together below
            for item in (v if isinstance(v, list) else [v]):
                if k in _LOGIC:
                    kids = walk_args(item)
                    if not kids:
                        continue
                    if _LOGIC[k] in ('or', 'not_or'):
                        inner = kids[0] if len(kids) == 1 else {'op': 'or', 'args': kids}
                    else:
                        inner = _and(kids)
                    args.append(_not(inner) if _LOGIC[k].startswith('not') else inner)
                elif k in ('custom_tooltip', 'hidden_trigger', 'custom_tooltip_fail'):
                    if isinstance(item, dict):  # a bare string tooltip is display-only
                        args.append(walk(item))  # one condition, even inside an OR
                else:
                    args.append(leaf(k, item))
        args.append(if_chain(node))
        return [a for a in args if a is not None]

    return walk(block)


def _to_list_blocks(v) -> list[dict]:
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []



def has_parameters(block) -> bool:
    """Whether a parsed block uses `$PARAM$` placeholders anywhere (a parameterised trigger)."""
    if isinstance(block, dict):
        return any('$' in str(k) or has_parameters(v) for k, v in block.items())
    if isinstance(block, list):
        return any(has_parameters(x) for x in block)
    return '$' in str(block)


def extract_scripted_triggers(install_dir, w) -> None:
    """Every parameter-free scripted trigger, normalised, into `scripted_trigger`."""
    import json
    idx = _scripted_trigger_index(install_dir)
    dlc = dlc_trigger_map(idx)
    for key in sorted(idx):
        if has_parameters(idx[key]):
            continue
        tree = normalize_trigger(idx[key], dlc)
        w.execute("INSERT INTO scripted_trigger (key, trigger_json) VALUES (?, ?)",
                  (key, json.dumps(tree, separators=(',', ':')) if tree else None))
    w.commit()
