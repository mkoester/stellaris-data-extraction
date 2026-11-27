"""Extraction: situations with their approaches, special projects, and the outcome events of
anomalies and projects (for the empire dashboard's situations tab)."""

from pathlib import Path
import json
import re
import sys
from .clausewitz import parse_file
from .contracts import with_inline
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .values import _as_blocks, _resolve_modifier_float
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


_SET_FLAG = re.compile(r'\bset_country_flag\s*=\s*"?(\w+)')


def flags_set(event_text: str) -> list[str]:
    """The country flags an event's own text sets, sorted, once each (comments ignored). Any
    branch counts: an `if` or an option that sets it is still how the flag comes."""
    text = re.sub(r'#[^\n]*', '', event_text)
    return sorted(set(_SET_FLAG.findall(text)))


def outcomes_json(block, loc: dict[str, str], title=None) -> str | None:
    """The events an outcome block fires, with their titles where they have one: `title(id)`
    (the event's own `title` key, `Outcomes.title`), else `<id>.name`."""
    ids = event_ids(block)
    if not ids:
        return None
    return json.dumps([{'event': i, 'name': (title(i) if title else None) or _text(loc, f'{i}.name') or None}
                       for i in ids])


def _text(loc: dict[str, str], key: str | None, newlines: bool = False) -> str | None:
    raw = loc.get(key) if key else None
    return _clean_loc(raw, loc, keep_newlines=newlines) if raw else None


def stage_keys(data: dict) -> list[str]:
    """A situation's stages in file order — the save's `stage` index (0 = the first)."""
    stages = data.get('stages')
    return [k for k, v in stages.items() if isinstance(v, dict)] if isinstance(stages, dict) else []


def project_number(v, variables: dict[str, float]) -> float | None:
    """A special project's `days_to_research` or `cost`: a number, an `@variable`, or a block's
    `base` (`cost = { base = 2000 modifier = {…} }`, the modifiers left out). Pure."""
    if isinstance(v, dict):
        v = v.get('base')
    s = str(v).strip() if v is not None else ''
    if s.startswith('@'):
        return variables.get(s)
    try:
        return float(s)
    except ValueError:
        return None


def stage_modifiers(stage: dict, variables: dict[str, float]) -> list[tuple[str, float]]:
    """A stage's `modifier = { … }` block as [(key, value)], `@variables` resolved."""
    block = stage.get('modifier')
    return _block_modifier_values(block, variables) if isinstance(block, dict) else []


def _key(v) -> str | None:
    return v.strip('"') if isinstance(v, str) else None


def chain_counters(data: dict) -> list[dict]:
    """An event chain's `counter = { <key> = { max = N } }` as [{"key", "max"}], in file order.
    Pure."""
    block = data.get('counter')
    if not isinstance(block, dict):
        return []
    return [{'key': k, 'max': int(f) if (f := _resolve_modifier_float(v.get('max'))) is not None else None}
            for k, v in block.items() if isinstance(v, dict)]


def extract_event_chains(install_dir: Path, loc: dict[str, str], w: Writer, progress=sys.stderr) -> None:
    n = 0
    for f in _files(install_dir / 'common' / 'event_chains'):
        for key, data in parse_file(f).items():
            if key.startswith('@') or not isinstance(data, dict):
                continue
            counters = chain_counters(data)
            w.execute(
                """INSERT OR REPLACE INTO event_chain (key, title, category, counters_json)
                   VALUES (?, ?, ?, ?)""",
                (key, _text(loc, f'{key}_title'), _key(data.get('situation_log_category')),
                 json.dumps(counters) if counters else None))
            n += 1
    print(f"  {n} event chains", file=progress)


def requirements(block) -> dict | None:
    """A project's `requirements = { … }` as plain values: `carries_colony = 1` (an arkship),
    `shipclass_science_ship = 1`, `leader = scientist`. A nested block stays as parsed. Pure."""
    if not isinstance(block, dict) or not block:
        return None
    return {k: (int(f) if (f := _resolve_modifier_float(v)) is not None and f == int(f) else
                f if f is not None else _key(v) if isinstance(v, str) else v)
            for k, v in block.items()}


def branches_on_arkship(block) -> bool:
    """Whether a project's outcome depends on the arkship type that does it."""
    return any(t in repr(block) for t in ('is_science_arkship', 'is_military_arkship', 'is_civilian_arkship'))


# ── Extraction ────────────────────────────────────────────────────────────────

def _files(d: Path) -> list[Path]:
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return []
    return sorted(f for f in d.glob('*.txt') if not f.name.startswith('99_README'))


def is_permanent(data: dict) -> bool:
    """`permanent = yes`: the situation does not end at either extreme (99_README_SITUATIONS.txt).
    4.5.4: every nomad's Operational Reserves and the Forever Cruise's Passenger Satisfaction."""
    return data.get('permanent') == 'yes'


def extract_situations(install_dir: Path, loc: dict[str, str], w: Writer,
                       progress=sys.stderr, scripted_vars: dict[str, float] | None = None,
                       title=None, load=None, events: dict[str, str] | None = None) -> None:
    """`events`: an event's own text by id (`Outcomes.events`), for the country flags each
    stage's `on_first_enter` events set (`situation_stage_flag`). `load`: an inline script's text
    by name (`script_loader`). A special project made of a
    top-level `inline_script` (4.5.4 `06_projects_nomads.txt`: 44, the onboard contracts' research
    projects among them) is expanded with it; without it such projects have no key and are left out."""
    common = install_dir / 'common'
    n_sit = n_appr = n_stage = n_mod = 0
    for f in _files(common / 'situations'):
        parsed = parse_file(f)
        variables = dict(scripted_vars or {})
        for k, v in parsed.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, data in parsed.items():
            if key.startswith('@') or not isinstance(data, dict):
                continue
            w.execute(
                """INSERT OR REPLACE INTO situation (key, name, description, category, permanent)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, _text(loc, key), _text(loc, f'{key}_desc', True), _key(data.get('category')),
                 1 if is_permanent(data) else 0))
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
            for order, stage in enumerate(stage_keys(data)):
                w.execute(
                    """INSERT OR REPLACE INTO situation_stage (situation_key, key, name, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, stage, _text(loc, stage), order))
                n_stage += 1
                for m_order, (mk, mv) in enumerate(stage_modifiers(data['stages'][stage], variables)):
                    w.execute(
                        "INSERT OR REPLACE INTO situation_stage_modifier (situation_key, stage_key, modifier_key, modifier_value, sort_order) VALUES (?, ?, ?, ?, ?)",
                        (key, stage, mk, mv, m_order))
                    _write_modifier_label(w, loc, mk)
                    n_mod += 1
                for ev in event_ids(data['stages'][stage].get('on_first_enter')):
                    for flag in flags_set((events or {}).get(ev, '')):
                        w.execute("INSERT OR IGNORE INTO situation_stage_flag (situation_key, stage_key, flag, event_id) VALUES (?, ?, ?, ?)",
                                  (key, stage, flag, ev))

    n_proj = 0
    for f in _files(common / 'special_projects'):
        parsed = parse_file(f)
        variables = dict(scripted_vars or {})
        for k, v in parsed.items():
            if k.startswith('@') and (fv := _resolve_modifier_float(v)) is not None:
                variables[k] = fv
        for p in _as_blocks(parsed.get('special_project')):
            if load is not None and 'inline_script' in p:
                p = with_inline(p, load)
            key = _key(p.get('key'))
            if not key:
                continue
            days = project_number(p.get('days_to_research'), variables)
            w.execute(
                """INSERT OR REPLACE INTO special_project
                   (key, name, description, days, cost, department, outcomes_json,
                    event_chain, requirements_json, arkship_branches)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, _text(loc, key), _text(loc, f'{key}_DESC', True),
                 None if days is None else int(days), project_number(p.get('cost'), variables),
                 _key(p.get('tech_department')), outcomes_json(p.get('on_success'), loc, title),
                 _key(p.get('event_chain')),
                 json.dumps(r) if (r := requirements(p.get('requirements'))) else None,
                 int(branches_on_arkship(p.get('on_success')))))
            n_proj += 1

    print(f"  {n_sit} situations, {n_appr} approaches, {n_stage} stages ({n_mod} modifiers), "
          f"{n_proj} special projects",
          file=progress)
    w.commit()
