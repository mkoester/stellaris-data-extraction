"""Extraction: Nomads contracts (common/missions), for the empire dashboard's contracts tab.

A contract is a mission whose category (common/missions/mission_categories) has
`is_contract = yes`. Most take their category and times from a top-level
`inline_script = contracts/…`."""

from pathlib import Path
import json
import re
import sys
from .clausewitz import Parser, parse_file, tokenize
from .localisation import _clean_loc
from .values import _as_blocks, _resolve_modifier_float
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _key(v) -> str | None:
    return v.strip('"') if isinstance(v, str) else None


def contract_categories(parsed: dict) -> set[str]:
    """The mission categories that are contracts."""
    return {k for k, v in parsed.items() if isinstance(v, dict) and v.get('is_contract') == 'yes'}


def with_inline(block: dict, load) -> dict:
    """A mission's own keys over those of its top-level `inline_script = { script … PARAM … }`
    references, `$PARAM$` substituted. Nested inline scripts (in triggers, effects) stay as
    they are: only the top level carries category and times."""
    merged: dict = {}
    refs = block.get('inline_script')
    for ref in ([{'script': refs}] if isinstance(refs, str) else _as_blocks(refs)):
        text = load(str(ref.get('script', '')).strip('"'))
        if text is None:
            continue
        for k, v in ref.items():
            if k != 'script':
                text = text.replace(f'${k}$', str(v).strip('"'))
        merged.update(Parser(tokenize(text)).parse())
    merged.update({k: v for k, v in block.items() if k != 'inline_script'})
    return merged


def days(raw, variables: dict[str, float]) -> int | None:
    """A time in days: a number or an `@variable` (`@contract_time_base` = 1800)."""
    if isinstance(raw, str) and raw.startswith('@'):
        v = variables.get(raw)
    else:
        v = _resolve_modifier_float(raw)
    return int(v) if v is not None else None


def _blocks(v) -> list:
    return v if isinstance(v, list) else [v]


def _walk(node, visit, load) -> None:
    """Every (key, value) under `node`, nested inline scripts expanded (`$PARAM$` substituted)."""
    if isinstance(node, list):
        for x in node:
            _walk(x, visit, load)
        return
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        if k == 'inline_script':
            for ref in _blocks(v):
                ref = ref if isinstance(ref, dict) else {'script': ref}
                text = load(str(ref.get('script', '')).strip('"'))
                if text is None:
                    continue
                for pk, pv in ref.items():
                    if pk != 'script':
                        text = text.replace(f'${pk}$', str(pv).strip('"'))
                _walk(Parser(tokenize(text)).parse(), visit, load)
            continue
        for b in _blocks(v):
            visit(k, b)
        _walk(v, visit, load)


def _num(v, variables: dict[str, float]) -> float | None:
    if isinstance(v, str) and v.startswith('@'):
        return variables.get(v)
    return _resolve_modifier_float(v)


def reward(on_success, variables: dict[str, float], load) -> dict | None:
    """What a contract pays its operator on success. `scaled`: each
    `contract_scaling_reward_effect` (trade plus a resource, both × `contract_bonus_value`), the
    first the full reward — military contracts carry a smaller second one for a partial success;
    `cruise`: Forever Cruise's trade (`cruise_contract_scaling_reward_effect`); `fixed`: plain
    `add_resource` (the influence `@opinion_contract_*`), one with a `mult` left out; `trust`,
    `opinion`: from the `trustN` / `opinionN` tooltips. None: nothing found. Pure."""
    out: dict = {'scaled': [], 'cruise': None, 'fixed': {}, 'trust': None, 'opinion': None}

    def visit(k, b):
        if k == 'contract_scaling_reward_effect' and isinstance(b, dict):
            out['scaled'].append({'trade': _num(b.get('TRADE'), variables),
                                  'resource': _key(b.get('RESOURCE')),
                                  'amount': _num(b.get('RESOURCE_AMOUNT'), variables)})
        elif k == 'cruise_contract_scaling_reward_effect' and isinstance(b, dict):
            out['cruise'] = _num(b.get('TRADE'), variables)
        elif k == 'add_resource' and isinstance(b, dict) and 'mult' not in b:
            for r, a in b.items():
                if (n := _num(a, variables)) is not None:
                    out['fixed'][r] = out['fixed'].get(r, 0) + n
        elif k == 'custom_tooltip' and isinstance(b, str):
            for name in ('trust', 'opinion'):
                if b.startswith(name) and b[len(name):].isdigit() and out[name] is None:
                    out[name] = int(b[len(name):])

    _walk(on_success, visit, load)
    found = out['scaled'] or out['cruise'] is not None or out['fixed'] or out['trust'] or out['opinion']
    return out if found else None


def failed_opinion(on_cancel, variables: dict[str, float], load) -> float | None:
    """The issuer's opinion change when the contract fails or is cancelled: its
    `opinion_failed_contract_<size>` modifier, whose base is `@opinion_failed_contract_<size>`."""
    found: list[float] = []

    def visit(k, b):
        if k == 'add_opinion_modifier' and isinstance(b, dict):
            m = _key(b.get('modifier')) or ''
            if m.startswith('opinion_failed_contract') and (v := variables.get(f'@{m}')) is not None:
                found.append(v)

    _walk(on_cancel, visit, load)
    return min(found) if found else None


def operator_techs(possible_operator) -> list[str]:
    """The techs `possible_operator` asks for at its top level (`has_technology = tech_…`)."""
    if not isinstance(possible_operator, dict):
        return []
    return [t for v in _blocks(possible_operator.get('has_technology')) if (t := _key(v))]


def offer_techs(potential_operator) -> list[list[str]]:
    """The techs `potential_operator` asks for before the game offers the contract at all, as
    groups of which any one tech will do: each top-level `has_technology` is a group of its own,
    a top-level `OR` of only `has_technology` one group (Deep Scan: any arkship or sensor tech).
    Pure."""
    if not isinstance(potential_operator, dict):
        return []
    def techs(b: dict) -> list[str]:
        return [t for v in _blocks(b.get('has_technology')) if (t := _key(v))]
    groups = [[t] for t in techs(potential_operator)]
    for b in _blocks(potential_operator.get('OR')):
        if isinstance(b, dict) and set(b) == {'has_technology'} and (ts := techs(b)):
            groups.append(ts)
    return groups


def desc_key(m: dict, key: str) -> str:
    """The operator's description key: `desc_operator` or `desc` (a block's first `text`), else
    `<key>_desc` — the game's default."""
    for field in ('desc_operator', 'desc'):
        for v in _blocks(m.get(field)):
            k = _key(v.get('text')) if isinstance(v, dict) else _key(v)
            if k:
                return k
    return f'{key}_desc'


# A contract's scopes in its description: `from` the issuer, `fromfrom` the contract's system.
# {issuer} and {system} are filled in by the dashboard; the rest become plain words.
_DESC_SCOPES = {
    'from.getname': '{issuer}',
    'fromfrom.getname': '{system}',
    'from.capital_scope.getname': "{issuer}'s capital",
    'from.capital_scope.solar_system.getname': 'home',  # "… in the home system"
    'capital_scope.getname': 'our capital',
    'owner.getscientistcapital': 'our capital',
}
_SCOPE_TAG = re.compile(r'\[([A-Za-z_.:0-9]+)\]')


def desc_scopes(text: str) -> str:
    """Scope tags the dashboard can fill ([From.GetName] → {issuer}); others are left to
    `_clean_loc`, which drops them. Pure."""
    return _SCOPE_TAG.sub(lambda m: _DESC_SCOPES.get(m.group(1).lower(), m.group(0)), text)


def steps(counter) -> int | None:
    """The progress a contract needs: its first counter's `max` (Espionage Support 2: 2)."""
    if not isinstance(counter, dict):
        return None
    for v in counter.values():
        if isinstance(v, dict) and (n := _resolve_modifier_float(v.get('max'))) is not None:
            return int(n)
    return None


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_contracts(install_dir: Path, loc: dict[str, str], load, w: Writer,
                      progress=sys.stderr, scripted_vars: dict[str, float] | None = None) -> None:
    root = install_dir / 'common' / 'missions'
    cats: set[str] = set()
    for f in sorted((root / 'mission_categories').glob('*.txt')):
        cats |= contract_categories(parse_file(f))
    n = 0
    for f in sorted((root / 'missions').glob('*.txt')):
        if f.name.startswith('99_README'):
            continue
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
            m = with_inline(data, load)
            category = _key(m.get('category'))
            if category not in cats:
                continue
            raw = loc.get(key)
            r = reward(m.get('on_success'), variables, load)
            techs = operator_techs(m.get('possible_operator'))
            offer = offer_techs(m.get('potential_operator'))
            w.execute(
                """INSERT OR REPLACE INTO contract
                   (key, name, category, time_to_accept, time_to_complete, event_chain, steps,
                    reward_json, failed_opinion, operator_techs_json, offer_techs_json, description,
                    location)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, _clean_loc(raw, loc) if raw else None, category,
                 days(m.get('time_to_accept'), variables), days(m.get('time_to_complete'), variables),
                 _key(m.get('event_chain')), steps(m.get('counter')),
                 json.dumps(r) if r else None, failed_opinion(m.get('on_cancel'), variables, load),
                 json.dumps(techs) if techs else None, json.dumps(offer) if offer else None,
                 _clean_loc(desc_scopes(d), loc) if (d := loc.get(desc_key(m, key))) else None,
                 int(m.get('location') == 'yes')))
            n += 1
    print(f"  {n} contracts ({len(cats)} contract categories)", file=progress)
