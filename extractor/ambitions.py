"""Extraction: ambitions (the game's keys still say "crisis") — the paths an ascension perk
starts, their levels with the currency and requirements each needs and the menace perks it
grants, and the objectives that earn the currency with their reward formula. For the dashboard,
which reads a country's path and level from saves by key."""

from pathlib import Path
import json
import sys
from .clausewitz import parse_file
from .game_start import parse_ordered
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .traditions import _Ctx, dumps
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    try:
        return float(str(v))
    except (ValueError, TypeError):
        return None


def _pairs(block) -> dict:
    """An ordered block's direct `key = scalar` entries (the first of each key)."""
    out = {}
    for k, _, v in block if isinstance(block, list) else []:
        if not isinstance(v, list):
            out.setdefault(k, v)
    return out


_OPS = {'base': 'base', 'min': 'min', 'max': 'max', 'round_to': 'round_to', 'add': 'add',
        'subtract': 'subtract', 'mult': 'multiply', 'multiply': 'multiply', 'divide': 'divide'}


def reward_steps(entries, variables: dict[str, float] | None = None) -> list[dict]:
    """An objective's `reward` (ordered entries, or a bare number) → the formula as steps in
    file order, e.g. [{"op":"base","value":700},
    {"op":"subtract","by":"count_playable_country","mult":25}, {"op":"round_to","value":50},
    {"op":"min","value":100}]. `by` is a trigger the game counts (`num_galaxy_systems`, …) or
    `galaxy_setup:<setting>`; `var` a country variable or script value only the running game
    knows (`root.hyperthermia_volcanic_var`). A key not understood stays as {"op": key, "raw": …}."""
    variables = variables or {}

    def num(v):
        return variables.get(v) if isinstance(v, str) and v.startswith('@') else _num(v)

    if not isinstance(entries, list):
        n = num(entries)
        return [{'op': 'base', 'value': n}] if n is not None else []
    steps: list[dict] = []
    for key, _, value in entries:
        if key == 'round' and value == 'yes':
            steps.append({'op': 'round'})
        elif key in _OPS and not isinstance(value, list):
            op, n = _OPS[key], num(value)
            if n is not None:
                steps.append({'op': op, 'value': n})
            elif isinstance(value, str) and value.startswith('trigger:'):
                steps.append({'op': op, 'by': value.split(':', 1)[1]})
            else:
                steps.append({'op': op, 'var': value})
        elif key == 'complex_trigger_modifier' and isinstance(value, list):
            p = _pairs(value)
            by = p.get('trigger')
            if by == 'check_galaxy_setup_value':
                params = next((v for k, _, v in value if k == 'parameters'), [])
                by = f"galaxy_setup:{_pairs(params).get('setting')}"
            mult = num(p.get('mult'))
            steps.append({'op': _OPS.get(p.get('mode', 'add'), p.get('mode')), 'by': by,
                          **({'mult': mult} if mult is not None else {})})
        else:
            steps.append({'op': key, 'raw': value if not isinstance(value, list) else None})
    return steps


def allow_tooltips(allow) -> list[str]:
    """A level's `allow` → the loc keys of its `custom_tooltip`s' success texts (the
    requirement as the game words it), in file order, hidden triggers skipped."""
    out: list[str] = []

    def walk(block):
        if not isinstance(block, dict):
            return
        for k, v in block.items():
            if k == 'hidden_trigger':
                continue
            for b in _as_blocks(v) if k == 'custom_tooltip' else []:
                key = b.get('success_text') or b.get('fail_text')
                if isinstance(key, str):
                    out.append(key)
            if k in ('AND', 'OR', 'NOT', 'NOR', 'NAND'):
                for b in _as_blocks(v):
                    walk(b)
    walk(allow)
    return out


def crisis_paths_of_perks(perks: dict) -> list[tuple[str, str]]:
    """Ascension perks (parsed) → [(perk key, path key)] for each `activate_crisis_progression`."""
    out = []

    def find(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == 'activate_crisis_progression' and isinstance(x, str):
                    yield x
                else:
                    yield from find(x)
        elif isinstance(v, list):
            for x in v:
                yield from find(x)
    for perk, data in perks.items():
        for path in dict.fromkeys(find(data)):
            out.append((perk, path))
    return out


def _strip_trigger_marker(text: str | None) -> str | None:
    """'$TRIGGER_PASS$Has …' resolves to an icon in game; drop whatever it cleaned to."""
    if text is None:
        return None
    for m in ('$TRIGGER_PASS$', '$TRIGGER_FAIL$'):
        text = text.replace(m, '')
    return text.strip()


# ── Extraction ────────────────────────────────────────────────────────────────

def _parse_dir(d: Path) -> dict:
    out: dict = {}
    for f in sorted(d.glob('*.txt')) if d.is_dir() else []:
        try:
            out.update({k: v for k, v in parse_file(f).items()
                        if isinstance(v, dict) and not k.startswith('@')})
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    return out


def _ordered_dir(d: Path, scripted_vars: dict[str, float]) -> tuple[dict[str, list], dict[str, float]]:
    """Top-level blocks in file order, and the @variables they see (scripted plus the files')."""
    out: dict[str, list] = {}
    variables = dict(scripted_vars)
    for f in sorted(d.glob('*.txt')) if d.is_dir() else []:
        for key, _, value in parse_ordered(f.read_text(encoding='utf-8-sig', errors='replace')):
            if isinstance(value, list):
                out.setdefault(key, value)
            elif key.startswith('@') and _num(value) is not None:
                variables[key] = _num(value)
    return out, variables


def extract_ambitions(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                      w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    def text(key, newlines=False):
        raw = loc.get(key) if key else None
        return _clean_loc(raw, loc, keep_newlines=newlines) if raw else None

    paths = _parse_dir(common / 'crisis_paths')
    levels = _parse_dir(common / 'crisis_levels')
    objectives, file_vars = _ordered_dir(common / 'crisis_objectives', scripted_vars)
    objective_data = _parse_dir(common / 'crisis_objectives')
    perks = _parse_dir(common / 'menace_perks')

    for order, (key, data) in enumerate(paths.items()):
        currency = data.get('crisis_currency') if isinstance(data.get('crisis_currency'), str) else None
        w.execute("INSERT INTO crisis_path (key, currency, name, description, sort_order) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (key, currency, text(f'{currency}_crisis_description_title'),
                   text(f'{currency}_crisis_description', True), order))
        for n, lv in enumerate(x for x in _to_list(data.get('levels')) if isinstance(x, str)):
            w.execute("INSERT INTO crisis_path_level (path_key, level_key, level, sort_order) "
                      "VALUES (?, ?, ?, ?)", (key, lv, n + 1, n))
        for n, ob in enumerate(x for x in _to_list(data.get('objectives')) if isinstance(x, str)):
            w.execute("INSERT INTO crisis_path_objective (path_key, objective_key, sort_order) "
                      "VALUES (?, ?, ?)", (key, ob, n))

    for perk, path in crisis_paths_of_perks(_parse_dir(common / 'ascension_perks')):
        w.execute("INSERT INTO crisis_path_perk (path_key, perk_key) VALUES (?, ?)", (path, perk))

    for order, (key, data) in enumerate(levels.items()):
        allow = data.get('allow') if isinstance(data.get('allow'), dict) else None
        reqs = [_strip_trigger_marker(text(k)) for k in allow_tooltips(allow)]
        w.execute(
            "INSERT INTO crisis_level (key, name, required_currency, requirements_json, "
            "allow_json, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
            (key, text(key), _num(data.get('required_crisis_currency')),
             json.dumps([r for r in reqs if r], separators=(',', ':')),
             dumps(ctx.trig(allow, scripted_vars)), order))
        for n, p in enumerate(x for x in _to_list(data.get('perks')) if isinstance(x, str)):
            w.execute("INSERT INTO crisis_level_perk (level_key, perk_key, sort_order) "
                      "VALUES (?, ?, ?)", (key, p, n))

    for order, (key, entries) in enumerate(objectives.items()):
        data = objective_data.get(key, {})
        reward = next((v for k, _, v in entries if k == 'reward'), None)
        pot = data.get('potential') if isinstance(data.get('potential'), dict) else None
        w.execute(
            "INSERT INTO crisis_objective (key, name, description, reward_json, recurring, "
            "potential_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, text(f'{key}_name'), text(f'{key}_desc', True),
             json.dumps(reward_steps(reward, file_vars), separators=(',', ':')),
             int(data.get('recurring') == 'yes'), dumps(ctx.trig(pot, scripted_vars)), order))

    n_mods = 0
    for order, (key, data) in enumerate(perks.items()):
        w.execute("INSERT INTO menace_perk (key, name, description, sort_order) VALUES (?, ?, ?, ?)",
                  (key, text(key), text(f'{key}_desc', True), order))
        n = 0
        for scope, field in (('country', 'modifier'), ('federation', 'federation_modifier')):
            for block in _as_blocks(data.get(field)):
                for mk, mv in _block_modifier_values(block, scripted_vars):
                    w.execute(
                        "INSERT INTO menace_perk_modifier (perk_key, scope, modifier_key, "
                        "modifier_value, sort_order) VALUES (?, ?, ?, ?, ?)", (key, scope, mk, mv, n))
                    _write_modifier_label(w, loc, mk)
                    n += 1
        n_mods += n

    print(f"  {len(paths)} ambition paths, {len(levels)} levels, {len(objectives)} objectives, "
          f"{len(perks)} menace perks ({n_mods} modifiers)", file=progress)
    w.commit()
