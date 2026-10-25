"""Extraction: the timers of decisions, artifact and astral actions, relics, diplomatic actions,
federation laws and enclave deals — the timed modifiers and timed flags an action sets, and those
it waits on (for the empire dashboard's upcoming list: "available again", "ends")."""

from pathlib import Path
import sys
import re
from .clausewitz import Parser, parse_file, tokenize
from .localisation import _clean_loc
from .values import _as_blocks
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

_GATES = {'has_modifier': 'modifier', 'has_planet_flag': 'flag', 'has_country_flag': 'flag'}
_TIMED_FLAGS = ('set_timed_planet_flag', 'set_timed_country_flag')
_NEGATE = {'NOT', 'NOR'}


def _children(node) -> list:
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        return list(node.values()) + [b for _, b in getattr(node, 'chain', [])]
    return []


def _key(v) -> str | None:
    return v.strip('"') if isinstance(v, str) and not v.startswith('@') else None


def _days(block: dict, variables: dict[str, float] | None = None) -> int | None:
    """`days`, `months` (30 days) or `years` (360 days) of a timer, where a number or a known
    `@variable` (relics' `@triumph_duration` lives in common/scripted_variables)."""
    for k, scale in (('days', 1), ('months', 30), ('years', 360)):
        v = block.get(k)
        if isinstance(v, str) and v.startswith('@') and variables:
            v = variables.get(v)
        try:
            return int(float(str(v))) * scale
        except (TypeError, ValueError):
            continue
    return None


def gates(trigger) -> set[tuple[str, str]]:
    """The modifiers and flags a trigger requires to be absent (`NOT = { has_modifier = x }`):
    an action waiting for a cooldown to run out."""
    out: set[tuple[str, str]] = set()

    def walk(node, negated: bool):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _GATES and negated:
                    for x in v if isinstance(v, list) else [v]:
                        if _key(x):
                            out.add((_GATES[k], _key(x)))
                else:
                    walk(v, negated != (k in _NEGATE))
            for _, b in getattr(node, 'chain', []):
                walk(b, negated)
        elif isinstance(node, list):
            for x in node:
                walk(x, negated)

    walk(trigger, False)
    return out


def effect_gates(effect) -> set[tuple[str, str]]:
    """The gates inside an effect's `limit` blocks: `if = { limit = { NOT = { has_modifier = x } }
    add_modifier = x }` (Satisfying Insults) waits on x as an `allow` would."""
    out: set[tuple[str, str]] = set()

    def walk(node):
        if isinstance(node, dict):
            if 'limit' in node:
                for b in _as_blocks(node['limit']):
                    out.update(gates(b))
        for c in _children(node):
            walk(c)

    walk(effect)
    return out


def expand_inline(node, load) -> list:
    """The parsed bodies of the `inline_script = { script = x PARAM = y }` references inside a
    block, `$PARAM$` substituted (relics' `possible`: `relics/activation_checks`)."""
    out: list = []

    def walk(n):
        if isinstance(n, dict):
            for ref in _as_blocks(n.get('inline_script')) + [
                    {'script': v} for v in ([n.get('inline_script')] if isinstance(n.get('inline_script'), str) else [])]:
                text = load(str(ref.get('script', '')).strip('"'))
                if text is None:
                    continue
                for k, v in ref.items():
                    if k != 'script':
                        text = text.replace(f'${k}$', str(v).strip('"'))
                out.append(Parser(tokenize(text)).parse())
        for c in _children(n):
            walk(c)

    walk(node)
    return out


def timers(effect, variables: dict[str, float] | None = None) -> dict[tuple[str, str], int | None]:
    """The timed modifiers (`add_modifier = { modifier days }`) and timed flags
    (`set_timed_*_flag = { flag days }`) an effect sets, with their length in days."""
    out: dict[tuple[str, str], int | None] = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                for b in _as_blocks(v) if k == 'add_modifier' or k in _TIMED_FLAGS else []:
                    key = _key(b.get('modifier' if k == 'add_modifier' else 'flag'))
                    days = _days(b, variables)
                    if key and (days is not None or k in _TIMED_FLAGS or 'days' in b):
                        out[('modifier' if k == 'add_modifier' else 'flag', key)] = days
        for c in _children(node):
            walk(c)

    walk(effect)
    return out


def action_timers(gate_blocks: list, effect_blocks: list,
                  variables: dict[str, float] | None = None) -> list[tuple[str, str, str, int | None]]:
    """(kind, key, role, days): `cooldown` for a timer the action waits on (whether it sets it
    or something else does; in its triggers or in its effect's `limit`s), `effect` for one it
    only sets."""
    waits: set[tuple[str, str]] = set()
    for b in gate_blocks:
        waits |= gates(b)
    sets: dict[tuple[str, str], int | None] = {}
    for b in effect_blocks:
        sets.update(timers(b, variables))
        waits |= {g for g in effect_gates(b) if g in sets}
    return sorted(
        [(k, key, 'cooldown', sets.get((k, key))) for k, key in waits]
        + [(k, key, 'effect', d) for (k, key), d in sets.items() if (k, key) not in waits])


# ── Extraction ────────────────────────────────────────────────────────────────

# Folders of actions the player takes: the kind recorded, the trigger blocks it is gated by and
# the effect blocks it runs.
_ACTION_DIRS = (
    ('decisions', 'decision', ('potential', 'allow'), ('effect',)),
    ('artifact_actions', 'artifact_action', ('potential', 'allow'), ('effect',)),
    ('astral_actions', 'astral_action', ('potential', 'allow'), ('effect',)),
    ('relics', 'relic', ('possible',), ('active_effect',)),
    ('diplomatic_actions', 'diplomatic_action', ('potential', 'possible'), ('on_accept', 'on_decline')),
    ('federation_laws', 'federation_law', ('potential', 'allow'), ('on_enact',)),
)
_LAW_CATEGORY = re.compile(r'\blaw_category_[a-z_0-9]+')
# Enclave deals are event options (4.5.2: traders, artisans and curators in leviathans_events_1,
# the Overlord enclaves in overlord_enclaves_events).
_ENCLAVE_EVENTS = ('leviathans_events_1.txt', 'overlord_enclaves_events.txt')


def _text(loc: dict[str, str], key: str | None) -> str | None:
    raw = loc.get(key) if key else None
    return _clean_loc(raw, loc) if raw else None


def _files(d: Path) -> list[Path]:
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return []
    return sorted(f for f in d.glob('*.txt') if not f.name.startswith(('99_README', 'example')))


def _insert(w: Writer, kind: str, key: str, name: str | None, rows) -> int:
    for timer_kind, timer_key, role, days in rows:
        w.execute(
            """INSERT OR REPLACE INTO action_timer
               (timer_kind, timer_key, action_kind, action_key, action_name, role, days)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timer_kind, timer_key, kind, key, name, role, days))
    return len(rows)


def _name(loc: dict[str, str], kind: str, key: str, data: dict) -> str | None:
    if kind == 'diplomatic_action':  # action_insult → ACTION_INSULT_TITLE "Insult"
        return _text(loc, f'{key.upper()}_TITLE') or _text(loc, key)
    if kind == 'federation_law':  # "Succession Term: 10 Years"
        m = _LAW_CATEGORY.search(str(data.get('allow')))
        cat = _text(loc, m.group(0)) if m else None
        return f'{cat}: {_text(loc, key)}' if cat and _text(loc, key) else _text(loc, key)
    return _text(loc, key)


def astral_cooldowns(data: dict, variables: dict[str, float]) -> dict[str, int]:
    """An astral action's own `cooldown` (days; the engine tracks it, no modifier), per action."""
    local = {k: v for k, v in data.items() if k.startswith('@')}
    out: dict[str, int] = {}
    for key, block in data.items():
        if key.startswith('@') or not isinstance(block, dict) or 'cooldown' not in block:
            continue
        v = block['cooldown']
        v = local.get(v, variables.get(v)) if isinstance(v, str) and v.startswith('@') else v
        try:
            out[key] = int(float(str(v)))
        except (TypeError, ValueError):
            continue
    return out


def extract_action_timers(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                          load_script, w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    n = n_astral = 0
    for folder, kind, gate_keys, effect_keys in _ACTION_DIRS:
        for f in _files(common / folder):
            data_file = parse_file(f)
            for key, data in data_file.items():
                if key.startswith('@') or not isinstance(data, dict):
                    continue
                triggers = [data.get(k) for k in gate_keys]
                triggers += [t for b in triggers for t in expand_inline(b, load_script)]
                rows = action_timers(triggers, [data.get(k) for k in effect_keys], scripted_vars)
                n += _insert(w, kind, key, _name(loc, kind, key, data), rows)
            if kind == 'astral_action':
                for key, days in sorted(astral_cooldowns(data_file, scripted_vars).items()):
                    w.execute("INSERT OR REPLACE INTO astral_action_cooldown (key, name, days) "
                              "VALUES (?, ?, ?)", (key, _text(loc, key), days))
                    n_astral += 1
    for name in _ENCLAVE_EVENTS:
        f = install_dir / 'events' / name
        if not f.is_file():
            print(f"  WARNING: {f} not found", file=sys.stderr)
            continue
        for event in _as_blocks(parse_file(f).get('country_event')):
            for option in _as_blocks(event.get('option')):
                key = _key(option.get('name'))
                if not key:
                    continue
                rest = {k: v for k, v in option.items() if k not in ('allow', 'trigger')}
                rows = action_timers([option.get('allow'), option.get('trigger')], [rest])
                n += _insert(w, 'enclave', key,
                             _text(loc, key) or _text(loc, _key(event.get('title'))), rows)
    print(f"  {n} action timers, {n_astral} astral action cooldowns", file=progress)
    w.commit()
