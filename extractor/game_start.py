"""Extraction: the scripts that build a new empire's capital (buildings, districts, zones).

The game runs `generate_start_buildings_and_districts` (and `void_dweller_home_planet_setup` for
Void Dwellers' habitat) at game start: about 80 `game_start/*` inline scripts whose effects
depend on order (a zone is added before the building that goes into it, variables count rural
districts up and down). The dict parser loses the order of repeated keys, so effects are read
with an order-preserving parser here; limits are trigger blocks and use the dict parser.

The result is an effect list the planner runs against a configured empire:
  {'e': 'if', 'branches': [{'limit': tree|None, 'body': [...]}, ...]}
  {'e': 'while', 'count': n|var, 'limit': tree|None, 'body': [...]}
  {'e': 'add_building', 'building': k[, 'zone': z]}   {'e': 'remove_building', 'building': k}
  {'e': 'add_district', 'district': k, 'amount': n|var}   {'e': 'remove_district', 'district': k}
  {'e': 'add_zone', 'district': k, 'zone': z[, 'slot': n]}
  {'e': 'set_variable'|'change_variable', 'which': v, 'value': n|var}   {'e': 'clear_variable', 'which': v}
  {'e': 'set_flag'|'remove_flag', 'flag': f}
  {'e': 'unknown', 'key': k}   -- an effect not modelled, kept so the planner can say so
"""

import json
import sys
from pathlib import Path
from .clausewitz import Cmp, Parser, tokenize
from .triggers import _scripted_trigger_index, dlc_trigger_map, has_parameters, normalize_trigger

# The scripted effects that run the setup, in the file that defines them.
SETUP_EFFECTS = ('generate_start_buildings_and_districts', 'void_dweller_home_planet_setup')

# Scopes whose triggers apply to the one empire, species and capital the planner models. At game
# start an empire owns its founder species (and a second one for a few origins, which the planner
# does not model), so `any_owned_species` reads as the founder species.
_SCOPES = ('owner', 'owner?', 'planet', 'this', 'founder_species', 'owner_species', 'species',
           'last_created_species', 'any_owned_species')

# Effects that do not change buildings, districts or zones (deposits, modifiers, the country,
# the rest of the system, graphics, resources).
_IGNORED = {
    'optimize_memory', 'solar_system', 'random_system_planet', 'every_system_planet',
    'add_deposit', 'remove_deposit', 'add_modifier', 'remove_modifier',
    'set_planet_flag', 'remove_planet_flag', 'set_planet_entity', 'set_planet_size',
    'save_event_target_as', 'give_starting_resources_effect',
    'validate_planet_buildings_and_districts', 'export_modifier_to_variable',
    'species_subspecies', 'last_created_species', 'create_species', 'add_research_option',
    'give_technology', 'create_pop_group', 'random_owned_pop_group',
    'weighted_random_owned_pop_group', 'generate_civic_secondary_pops', 'every_deposit',
    'random_deposit', 'add_blocker', 'clear_blockers', 'add_planet_devastation', 'set_colony_type',
    'carrier_event', 'validate_and_repair_planet_buildings_and_districts', 'add_resource',
    'begin_event_chain', 'country_event',
}


def parse_ordered(text: str) -> list[tuple]:
    """Script text → [(key, op, value)] in file order; a block value is itself such a list,
    except under `limit`, which is a trigger block and parses to the usual dict."""
    p = Parser(tokenize(text))

    def block() -> list[tuple]:
        out = []
        while p.peek() not in (None, '}'):
            key = p.consume()
            if key == '{':           # anonymous sub-block: skip, as the dict parser does
                p.parse_block()
                if p.peek() == '}':
                    p.consume()
                continue
            if p.peek() not in ('=', '<', '>', '!'):
                out.append((key.strip('"'), None, None))
                continue
            op = p.consume()
            if op != '=' and p.peek() == '=':
                op += p.consume()
            if p.peek() == '{':
                if key == 'limit':
                    value = p.parse_value()
                else:
                    p.consume()
                    value = block()
                    if p.peek() == '}':
                        p.consume()
            else:
                value = p.consume().strip('"')
            out.append((key, op, value))
        return out

    return block()


def expand_inline_scripts(entries: list[tuple], load, depth: int = 0) -> list[tuple]:
    """Replace every `inline_script = name` (or `= { script = name PARAM = v }`) by the script's
    own entries, recursively. `load(name)` gives a script's text or None."""
    if depth > 20:
        raise ValueError('inline_script nesting too deep')
    out = []
    for key, op, value in entries:
        if key == 'inline_script':
            params = {}
            name = value
            if isinstance(value, list):
                params = {k: v for k, _, v in value if k != 'script' and isinstance(v, str)}
                name = next((v for k, _, v in value if k == 'script'), None)
            text = load(name) if isinstance(name, str) else None
            if text is None:
                print(f"  WARNING: inline script {name} not found", file=sys.stderr)
                continue
            for k, v in params.items():
                text = text.replace(f'${k}$', v)
            out += expand_inline_scripts(parse_ordered(text), load, depth + 1)
        elif isinstance(value, list):
            out.append((key, op, expand_inline_scripts(value, load, depth + 1)))
        else:
            out.append((key, op, value))
    return out


def inline_scripted_triggers(block, idx: dict[str, dict], dlc: dict[str, str],
                             seen: frozenset = frozenset()):
    """A trigger block with its scopes from `_SCOPES` flattened and its parameter-free scripted
    triggers (`is_anglers_empire = yes`) replaced by their bodies, so the tree holds only base
    checks. DLC-only triggers stay, for `normalize_trigger` to turn into `host_has_dlc`."""
    if isinstance(block, list):
        return [inline_scripted_triggers(x, idx, dlc, seen) for x in block]
    if not isinstance(block, dict):
        return block
    out: dict = {}

    def add(k, v):
        if k in out:
            out[k] = (out[k] if isinstance(out[k], list) else [out[k]]) + [v]
        else:
            out[k] = v

    for k, vs in block.items():
        for v in (vs if isinstance(vs, list) and k not in ('which',) else [vs]):
            if k in _SCOPES and isinstance(v, dict):
                add('AND', inline_scripted_triggers(v, idx, dlc, seen))
            elif (isinstance(v, str) and v in ('yes', 'no') and k in idx and k not in dlc
                    and k not in seen and not has_parameters(idx[k])):
                body = inline_scripted_triggers(idx[k], idx, dlc, seen | {k})
                add('AND' if v == 'yes' else 'NOT', body)
            elif isinstance(v, (dict, list)) and k in ('AND', 'OR', 'NOT', 'NOR', 'NAND'):
                add(k, inline_scripted_triggers(v, idx, dlc, seen))
            else:
                add(k, v)
    return out


def _structured_leaf(k: str, v) -> dict | None:
    """Trigger checks whose value is a block the planner reads: variables, zones, modifiers."""
    if not isinstance(v, dict):
        return None
    if k in ('check_variable', 'check_modifier_value'):
        target = v.get('which') if k == 'check_variable' else v.get('modifier')
        cmp = v.get('value')
        if isinstance(cmp, Cmp):
            return {'leaf': k, 'which': target, 'cmp': cmp.op, 'value': cmp.value}
        return {'leaf': k, 'which': target, 'cmp': '=', 'value': cmp}
    if k == 'has_zone' and isinstance(v.get('zone'), str):
        return {'leaf': 'has_zone', 'value': v['zone']}
    return None


def _number_or_var(v):
    """'2' → 2, '-1' → -1, a variable name stays a string."""
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return v


def _fields(value) -> dict:
    return {k: v for k, _, v in value} if isinstance(value, list) else {}


def normalize_effects(entries: list[tuple], limit_of) -> list[dict]:
    """Ordered effect entries → the effect list described in the module docstring.
    `limit_of(dict)` normalises a limit block."""
    out: list[dict] = []
    for i, (key, op, value) in enumerate(entries):
        if key == 'if' and isinstance(value, list):
            branches = [_branch(value, limit_of)]
            for k2, _, v2 in entries[i + 1:]:
                if k2 not in ('else_if', 'else') or not isinstance(v2, list):
                    break
                branches.append(_branch(v2, limit_of, is_else=k2 == 'else'))
                if k2 == 'else':
                    break
            out.append({'e': 'if', 'branches': branches})
        elif key in ('else_if', 'else'):
            continue  # consumed with its if
        elif key == 'while' and isinstance(value, list):
            f = _fields(value)
            lim = f.get('limit')
            out.append({'e': 'while', 'count': _number_or_var(f.get('count', 1)),
                        'limit': limit_of(lim) if isinstance(lim, dict) else None,
                        'body': normalize_effects([x for x in value if x[0] not in ('count', 'limit')],
                                                  limit_of)})
        elif key in ('planet', 'owner') and isinstance(value, list):
            out += normalize_effects(value, limit_of)  # the capital, or its country's flags
        elif key == 'add_building':
            f = _fields(value)
            node = {'e': 'add_building', 'building': value if isinstance(value, str) else f.get('building')}
            if f.get('zone'):
                node['zone'] = f['zone']
            out.append(node)
        elif key == 'add_building_if_not_present':
            out.append({'e': 'add_building', 'building': _fields(value).get('BUILDING'), 'unique': True})
        elif key == 'remove_building':
            out.append({'e': 'remove_building', 'building': value})
        elif key == 'add_district':
            out.append({'e': 'add_district', 'district': value, 'amount': 1})
        elif key in ('add_district_and_planet_size_if_needed_effect',
                     'add_district_and_planet_size_if_needed_effect_amount',
                     'add_district_if_possible_effect'):
            f = _fields(value)
            out.append({'e': 'add_district', 'district': f.get('district'),
                        'amount': _number_or_var(f.get('amount', 1))})
        elif key == 'remove_all_buildings':
            out.append({'e': 'remove_all_buildings'})
        elif key == 'remove_zone':
            f = _fields(value)
            out.append({'e': 'remove_zone', 'district': f.get('district'), 'zone': f.get('zone')})
        elif key == 'remove_district':
            out.append({'e': 'remove_district', 'district': value})
        elif key == 'add_zone':
            f = _fields(value)
            node = {'e': 'add_zone', 'district': f.get('district'), 'zone': f.get('zone')}
            if 'zone_slot' in f:
                node['slot'] = _number_or_var(f['zone_slot'])
            out.append(node)
        elif key in ('set_variable', 'change_variable', 'add_variable'):
            f = _fields(value)
            out.append({'e': 'change_variable' if key == 'add_variable' else key,
                        'which': f.get('which'), 'value': _number_or_var(f.get('value'))})
        elif key == 'clear_variable':
            out.append({'e': 'clear_variable', 'which': value})
        elif key in ('set_carrier_flag', 'set_timed_carrier_flag', 'set_country_flag'):
            out.append({'e': 'set_flag', 'flag': value if isinstance(value, str) else _fields(value).get('flag')})
        elif key in ('remove_carrier_flag', 'remove_country_flag'):
            out.append({'e': 'remove_flag', 'flag': value})
        elif key not in _IGNORED and '.' not in key \
                and not key.startswith('event_target:'):  # other objects' scopes
            out.append({'e': 'unknown', 'key': key})
    return [e for e in out if _does_something(e)]


def _does_something(e: dict) -> bool:
    """False for an if or while whose bodies hold no effect left (e.g. one that only made pops)."""
    if e['e'] == 'if':
        return any(b['body'] for b in e['branches'])
    if e['e'] == 'while':
        return bool(e['body'])
    return True


def _branch(value: list[tuple], limit_of, is_else: bool = False) -> dict:
    lim = None if is_else else _fields(value).get('limit')
    return {'limit': limit_of(lim) if isinstance(lim, dict) else None,
            'body': normalize_effects([x for x in value if x[0] != 'limit'], limit_of)}


def setup_effects(effects_text: str, load, idx: dict[str, dict]) -> dict[str, list[dict]]:
    """The scripted-effects file's text → {setup effect name: effect list}. Pure, given `load`."""
    dlc = dlc_trigger_map(idx)

    def limit_of(block: dict):
        return normalize_trigger(inline_scripted_triggers(block, idx, dlc), dlc,
                                 structured=_structured_leaf)

    out = {}
    for key, _, value in parse_ordered(effects_text):
        if key in SETUP_EFFECTS and isinstance(value, list):
            out[key] = normalize_effects(expand_inline_scripts(value, load), limit_of)
    return out


def extract_game_start(install_dir: Path, inline_scripts_dir: Path, w, verbose: bool) -> None:
    """The game-start capital setup scripts → `game_start_script` (one row per setup effect)."""
    from .buildings import script_loader
    sdir = install_dir / 'common' / 'scripted_effects'
    found: dict[str, list[dict]] = {}
    for f in sorted(sdir.glob('*.txt')):
        text = f.read_text(encoding='utf-8-sig', errors='replace')
        if any(f'{name} =' in text for name in SETUP_EFFECTS):
            found.update(setup_effects(text, script_loader(inline_scripts_dir),
                                       _scripted_trigger_index(install_dir)))
    for name in SETUP_EFFECTS:
        if name not in found:
            print(f"  WARNING: scripted effect {name} not found", file=sys.stderr)
            continue
        w.execute("INSERT INTO game_start_script (key, effects_json) VALUES (?, ?)",
                  (name, json.dumps(found[name], separators=(',', ':'))))
    w.commit()
    if verbose:
        print(f"  game start scripts: {sorted(found)}")
