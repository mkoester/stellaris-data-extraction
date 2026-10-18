"""Extraction: policies with their options, and pop faction types with their demands."""

from pathlib import Path
import re
import sys
from .clausewitz import Parser, tokenize
from .traditions import _Ctx, _load, dumps
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _unquote(v) -> str | None:
    return v.strip('"') if isinstance(v, str) else None


def owner_scope(block):
    """A faction demand's trigger reads the faction's owner: `owner = { … }`, maybe beside
    `exists = owner` (always true for a faction, so dropped). Unwrapped to the owner's block,
    since a scope is kept opaque otherwise."""
    if not isinstance(block, dict):
        return block
    if block.get('exists') == 'owner':
        block = {k: v for k, v in block.items() if k != 'exists'}
    if isinstance(block.get('owner'), dict) and set(block) == {'owner'}:
        return block['owner']
    return block or None


def expand_inline(text: str, params: dict[str, str]) -> dict:
    """An inline script's text with its `$PARAM$`s filled in, parsed."""
    for k, v in params.items():
        text = text.replace(f'${k}$', v)
    return Parser(tokenize(text)).parse()


def _number(v) -> float | None:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


# ── Extraction ────────────────────────────────────────────────────────────────

def _demands(data: dict, inline_dir: Path) -> list[dict]:
    """A faction's demands: its own `demand` blocks, then those its `pop_faction_types/demand_*`
    inline scripts add (GalCom pleased/outraged, the shared Cybernetic Creed ones)."""
    out = list(_as_blocks(data.get('demand')))
    for ref in _as_blocks(data.get('inline_script')):
        script = _unquote(ref.get('script'))
        if not script or not script.startswith('pop_faction_types/demand'):
            continue
        path = inline_dir / f'{script}.txt'
        if not path.exists():
            print(f"  WARNING: inline script {script} not found", file=sys.stderr)
            continue
        params = {k: str(v).strip('"') for k, v in ref.items() if k != 'script'}
        out += _as_blocks(expand_inline(path.read_text(encoding='utf-8-sig'), params).get('demand'))
    return out


def extract_politics(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                     w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    policies = _load(common / 'policies', scripted_vars)
    n_options = 0
    seen: set[str] = set()
    for order, (key, (data, pvars, _)) in enumerate(policies.items()):
        w.execute(
            """INSERT INTO policy (key, name, description, category, potential_json, allow_json,
               sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(f'policy_{key}'), ctx.text(f'policy_{key}_desc', True, pvars),
             ','.join(_to_list(data.get('category'))) or None,
             dumps(ctx.trig(data.get('potential'), pvars)),
             dumps(ctx.trig(data.get('allow'), pvars)), order))
        for o_order, opt in enumerate(_as_blocks(data.get('option'))):
            name = _unquote(opt.get('name'))
            if not name or name in seen:
                # an option name can repeat across policies (variants of one stance); the first wins
                continue
            seen.add(name)
            w.execute(
                """INSERT INTO policy_option (key, policy_key, name, description,
                   potential_json, valid_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, key, ctx.text(name), ctx.text(f'{name}_desc', True, pvars),
                 dumps(ctx.trig(opt.get('potential'), pvars)),
                 dumps(ctx.trig(opt.get('valid'), pvars)), o_order))
            ctx.modifiers(name, opt, pvars)
            n_options += 1

    factions = _load(common / 'pop_faction_types', scripted_vars)
    n_demands = 0
    for order, (key, (data, fvars, _)) in enumerate(factions.items()):
        w.execute(
            """INSERT INTO pop_faction_type (key, name, description, guiding_ethic, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, ctx.text(f'pft_{key}'), ctx.text(f'pft_{key}_desc', True, fvars),
             data.get('guiding_ethic'), order))
        for d_order, d in enumerate(_demands(data, common / 'inline_scripts')):
            title = _unquote(d.get('title'))
            unfulfilled = _unquote(d.get('unfulfilled_title'))
            w.execute(
                """INSERT INTO faction_demand (faction_key, sort_order, title, unfulfilled_title,
                   description, fulfilled_effect, unfulfilled_effect, potential_json, trigger_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, d_order, ctx.text(title) or title, ctx.text(unfulfilled) or unfulfilled,
                 ctx.text(_unquote(d.get('desc')), True, fvars),
                 _number(d.get('fulfilled_effect')), _number(d.get('unfulfilled_effect')),
                 dumps(ctx.trig(owner_scope(d.get('potential')), fvars)),
                 dumps(ctx.trig(owner_scope(d.get('trigger')), fvars))))
            n_demands += 1

    print(f"  {len(policies)} policies, {n_options} options, {len(factions)} faction types, "
          f"{n_demands} demands", file=progress)
    w.commit()
