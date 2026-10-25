"""Extraction: the timers of decisions, artifact and astral actions and enclave deals — the
timed modifiers and timed flags an action sets, and those it waits on (for the empire
dashboard's upcoming list: "available again", "ends")."""

from pathlib import Path
import sys
from .clausewitz import parse_file
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


def _days(block: dict) -> int | None:
    """`days`, `months` (30 days) or `years` (360 days) of a timer, where a number."""
    for k, scale in (('days', 1), ('months', 30), ('years', 360)):
        try:
            return int(float(str(block[k]))) * scale
        except (KeyError, ValueError):
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


def timers(effect) -> dict[tuple[str, str], int | None]:
    """The timed modifiers (`add_modifier = { modifier days }`) and timed flags
    (`set_timed_*_flag = { flag days }`) an effect sets, with their length in days."""
    out: dict[tuple[str, str], int | None] = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                for b in _as_blocks(v) if k == 'add_modifier' or k in _TIMED_FLAGS else []:
                    key = _key(b.get('modifier' if k == 'add_modifier' else 'flag'))
                    days = _days(b)
                    if key and (days is not None or k in _TIMED_FLAGS or 'days' in b):
                        out[('modifier' if k == 'add_modifier' else 'flag', key)] = days
        for c in _children(node):
            walk(c)

    walk(effect)
    return out


def action_timers(gate_blocks: list, effect_blocks: list) -> list[tuple[str, str, str, int | None]]:
    """(kind, key, role, days): `cooldown` for a timer the action waits on (whether it sets it
    or something else does), `effect` for one it only sets."""
    waits: set[tuple[str, str]] = set()
    for b in gate_blocks:
        waits |= gates(b)
    sets: dict[tuple[str, str], int | None] = {}
    for b in effect_blocks:
        sets.update(timers(b))
    return sorted(
        [(k, key, 'cooldown', sets.get((k, key))) for k, key in waits]
        + [(k, key, 'effect', d) for (k, key), d in sets.items() if (k, key) not in waits])


# ── Extraction ────────────────────────────────────────────────────────────────

# Folders of actions the player takes, with the action kind recorded.
_ACTION_DIRS = (('decisions', 'decision'), ('artifact_actions', 'artifact_action'),
                ('astral_actions', 'astral_action'))
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


def extract_action_timers(install_dir: Path, loc: dict[str, str], w: Writer,
                          progress=sys.stderr) -> None:
    common = install_dir / 'common'
    n = 0
    for folder, kind in _ACTION_DIRS:
        for f in _files(common / folder):
            for key, data in parse_file(f).items():
                if key.startswith('@') or not isinstance(data, dict):
                    continue
                rows = action_timers([data.get('potential'), data.get('allow')],
                                     [data.get('effect')])
                n += _insert(w, kind, key, _text(loc, key), rows)
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
    print(f"  {n} action timers", file=progress)
    w.commit()
