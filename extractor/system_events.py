"""Extraction: the events entering a system can fire (for the empire dashboard's map and
events tab).

`common/on_actions` → `on_entering_system_first_time` (a country's first ship there),
`on_entering_system` and `on_entering_system_fleet` (every entry; those events guard themselves
with a country flag) list them — from the Gray Goo factory to the Asteroid Hive's "Crystalline
Asteroids" and Helitio's "Missing Orbitals". Most of them test a star flag on the system entered (`FROM = { has_star_flag = guardians_hive_system }`), so a system's flags tell
which one it fires. Kept: events with a window and at least one such flag; the hidden ones and
those testing something else (an origin's own systems, a timeline) cannot be told from a save.
Their other conditions (owner, global flags) are not kept: a match is "likely".
"""

from pathlib import Path
import json
import re
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc
from .values import _as_blocks
from .writer import Writer

# a ship's first entry; any ship's or fleet's entry — those events guard themselves (a country
# flag set once: fircon.5000 "Missing Orbitals" on entering Helitio's Solarpunk borders)
ON_ACTIONS = ('on_entering_system_first_time', 'on_entering_system', 'on_entering_system_fleet')
EVENT_TYPES = ('ship_event', 'fleet_event', 'country_event', 'planet_event', 'event')
_NEGATIONS = {'not', 'nor', 'nand'}


# ── Pure helpers ───────────────────────────────────────────────────────────────

def on_action_events(data: dict, name: str = ON_ACTIONS[0]) -> list[str]:
    """The event ids an on_action lists, in file order, once each (several blocks merge)."""
    out: list[str] = []
    for block in _as_blocks(data.get(name)):
        events = block.get('events')
        for e in events if isinstance(events, list) else [events]:
            if isinstance(e, str) and e not in out:
                out.append(e)
    return out


def from_star_flags(trigger) -> tuple[list[str], list[str], list[str]]:
    """The star flags a trigger asks of FROM (the system entered): those it needs all of, those
    of which it needs one (an `OR`), and those it must not have (`NOT`, `NOR`). Sorted. A flag on
    another scope (the owner's capital, a planet) is not the system's and is skipped."""
    every: set[str] = set()
    some: set[str] = set()
    forbid: set[str] = set()

    def walk(node, mode: str | None):
        # mode: None outside FROM; 'all', 'any' or 'not' within it
        if isinstance(node, list):
            for x in node:
                walk(x, mode)
        elif isinstance(node, dict):
            for k, v in node.items():
                key = k.lower()
                if key == 'has_star_flag':
                    if mode is not None:
                        target = {'all': every, 'any': some, 'not': forbid}[mode]
                        for f in v if isinstance(v, list) else [v]:
                            if isinstance(f, str):
                                target.add(f.strip('"'))
                elif key == 'from':
                    walk(v, 'all')
                elif mode is None:
                    walk(v, None)
                elif key in _NEGATIONS:
                    walk(v, 'not' if mode != 'not' else 'all')
                elif key == 'or':
                    walk(v, 'any' if mode == 'all' else mode)
                elif key == 'and':
                    walk(v, mode)
                else:
                    walk(v, None)  # another scope within the system

    walk(trigger, None)
    return sorted(every), sorted(some), sorted(forbid)


def _values(node, key: str) -> set[str]:
    """Every value of `key` anywhere below: `set_country_flag = x` or `= { flag = x days = 3 }`."""
    out: set[str] = set()
    if isinstance(node, list):
        for x in node:
            out |= _values(x, key)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k.lower() == key:
                for x in v if isinstance(v, list) else [v]:
                    f = x.get('flag') if isinstance(x, dict) else x
                    if isinstance(f, str):
                        out.add(f.strip('"'))
            else:
                out |= _values(v, key)
    return out


def _owner_lacks(trigger) -> set[str]:
    """Country flags the trigger requires the owner not to have: `owner = { NOT = { has_country_flag
    = x } }` (or the NOT outside the owner)."""
    out: set[str] = set()

    def walk(node, owner: bool, negated: bool):
        if isinstance(node, list):
            for x in node:
                walk(x, owner, negated)
        elif isinstance(node, dict):
            for k, v in node.items():
                key = k.lower()
                if key == 'has_country_flag' and owner and negated:
                    for f in v if isinstance(v, list) else [v]:
                        if isinstance(f, str):
                            out.add(f.strip('"'))
                elif key in _NEGATIONS:
                    walk(v, owner, not negated)
                else:
                    walk(v, owner or key == 'owner', negated)

    walk(trigger, False, False)
    return out


def once_flag(event: dict) -> str | None:
    """The country flag that makes an event fire once per empire: one its trigger requires the
    owner not to have and the event sets itself (apoc.1's `encountered_first_gateway` — only the
    first gateway found shows the popup; fircon.5000's `found_helito`)."""
    both = _owner_lacks(event.get('trigger')) & _values(
        {k: v for k, v in event.items() if k != 'trigger'}, 'set_country_flag')
    return sorted(both)[0] if both else None


def _desc_key(desc) -> str | None:
    """The first plain description: a key, or the `text` of the first conditional desc."""
    for d in desc if isinstance(desc, list) else [desc]:
        if isinstance(d, str):
            return d.strip('"')
        if isinstance(d, dict) and isinstance(d.get('text'), str):
            return d['text'].strip('"')
    return None


_NAME_CMD = re.compile(r'\[[^\]]*GetName\]')
_DOTTED_REF = re.compile(r'\$([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\$')


def expand_dotted(s: str, loc: dict[str, str]) -> str:
    """`$leviathans.3202.base$` → its text: event loc keys hold dots, which `_clean_loc`'s
    `$KEY$` refs do not; a few rounds for nested ones, an unknown key left as it is."""
    for _ in range(4):
        before = s
        s = _DOTTED_REF.sub(lambda m: loc.get(m.group(1), m.group(0)), s)
        if s == before:
            break
    return s


def _text(loc: dict[str, str], key: str | None) -> str | None:
    raw = loc.get(key) if key else None
    if not raw:
        return None
    # a scope's name ([From.GetName], [hiver_system.GetName]) is the system or its owner: "…"
    return _clean_loc(_NAME_CMD.sub('…', expand_dotted(raw, loc)), loc, keep_newlines=True)


# ── Extraction ─────────────────────────────────────────────────────────────────

def extract_system_entry_events(install_dir: Path, loc: dict[str, str], w: Writer,
                                progress=sys.stderr) -> None:
    ids: list[str] = []
    source: dict[str, str] = {}
    for f in sorted((install_dir / 'common' / 'on_actions').glob('*.txt')):
        data = parse_file(f)
        for name in ON_ACTIONS:
            for e in on_action_events(data, name):
                if e not in ids:
                    ids.append(e)
                    source[e] = name
    wanted = set(ids)
    # parse only the event files that define one of them
    events: dict[str, dict] = {}
    pattern = re.compile(r'\bid\s*=\s*"?([A-Za-z_0-9]+\.\d+)"?')
    for f in sorted((install_dir / 'events').glob('*.txt')):
        text = f.read_text(encoding='utf-8-sig', errors='replace')
        if not any(m.group(1) in wanted for m in pattern.finditer(text)):
            continue
        data = parse_file(f)
        for kind in EVENT_TYPES:
            for ev in _as_blocks(data.get(kind)):
                i = ev.get('id')
                if isinstance(i, str) and i.strip('"') in wanted:
                    events.setdefault(i.strip('"'), ev)
    kept = 0
    for order, i in enumerate(ids):
        ev = events.get(i)
        if not ev or ev.get('hide_window') == 'yes':
            continue
        need, some, forbid = from_star_flags(ev.get('trigger'))
        if not need and not some:
            continue
        title = ev.get('title')
        w.execute(
            'INSERT INTO system_entry_event (event_id, on_action, title, description, '
            'star_flags_json, any_star_flags_json, not_star_flags_json, once_flag, sort_order) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (i, source[i], _text(loc, title.strip('"') if isinstance(title, str) else None),
             _text(loc, _desc_key(ev.get('desc'))), json.dumps(need),
             json.dumps(some) if some else None, json.dumps(forbid) if forbid else None,
             once_flag(ev), order))
        kept += 1
    print(f"  {len(ids)} events on entering a system, {kept} kept (a window and a star flag to test)",
          file=progress)
    w.commit()
