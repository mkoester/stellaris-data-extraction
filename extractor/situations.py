"""Extraction: situations with their approaches, special projects, and the outcome events of
anomalies and projects (for the empire dashboard's situations tab)."""

from pathlib import Path
import json
import re
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc
from .values import _as_blocks
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

_EVENT_ID = re.compile(r'^[A-Za-z_]+\.\d+$')


def event_ids(block) -> list[str]:
    """Event ids an effect block fires, in order and once each: `1 = anomaly.35` (anomaly
    outcomes by weight), `anomaly_event = anomaly.65`, `ship_event = { id = anomaly.2501 }`."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str) and _EVENT_ID.match(node) and node not in out:
            out.append(node)

    walk(block)
    return out


def outcomes_json(block, loc: dict[str, str]) -> str | None:
    """The events an outcome block fires, with their titles (`<id>.name`) where they have one."""
    ids = event_ids(block)
    if not ids:
        return None
    return json.dumps([{'event': i, 'name': _text(loc, f'{i}.name') or None} for i in ids])


def _text(loc: dict[str, str], key: str | None, newlines: bool = False) -> str | None:
    raw = loc.get(key) if key else None
    return _clean_loc(raw, loc, keep_newlines=newlines) if raw else None


def _key(v) -> str | None:
    return v.strip('"') if isinstance(v, str) else None


# ── Extraction ────────────────────────────────────────────────────────────────

def _files(d: Path) -> list[Path]:
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return []
    return sorted(f for f in d.glob('*.txt') if not f.name.startswith('99_README'))


def extract_situations(install_dir: Path, loc: dict[str, str], w: Writer,
                       progress=sys.stderr) -> None:
    common = install_dir / 'common'
    n_sit = n_appr = 0
    for f in _files(common / 'situations'):
        for key, data in parse_file(f).items():
            if key.startswith('@') or not isinstance(data, dict):
                continue
            w.execute(
                """INSERT OR REPLACE INTO situation (key, name, description, category)
                   VALUES (?, ?, ?, ?)""",
                (key, _text(loc, key), _text(loc, f'{key}_desc', True), _key(data.get('category'))))
            n_sit += 1
            for order, a in enumerate(_as_blocks(data.get('approach'))):
                name = _key(a.get('name'))
                if not name:
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO situation_approach
                       (situation_key, key, name, tooltip, is_default, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, name, _text(loc, name), _text(loc, f'{name}_tooltip', True),
                     1 if a.get('default') == 'yes' else 0, order))
                n_appr += 1

    n_proj = 0
    for f in _files(common / 'special_projects'):
        for p in _as_blocks(parse_file(f).get('special_project')):
            key = _key(p.get('key'))
            if not key:
                continue
            try:
                days = int(float(str(p.get('days_to_research'))))
            except ValueError:
                days = None
            w.execute(
                """INSERT OR REPLACE INTO special_project
                   (key, name, description, days, department, outcomes_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, _text(loc, key), _text(loc, f'{key}_DESC', True), days,
                 _key(p.get('tech_department')), outcomes_json(p.get('on_success'), loc)))
            n_proj += 1

    print(f"  {n_sit} situations, {n_appr} approaches, {n_proj} special projects", file=progress)
    w.commit()
