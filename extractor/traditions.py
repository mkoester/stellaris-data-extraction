"""Extraction: tradition trees, ascension perks and the defines behind their costs."""

from pathlib import Path
import json
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc, join_texts
from .modifiers import _block_modifier_values, _write_modifier_label
from .triggers import _scripted_trigger_index, dlc_trigger_map, normalize_trigger
from .values import _as_blocks, _to_list
from .writer import Writer


# common/defines keys the planner's cost calculator and slot limits need.
DEFINE_KEYS = ('TRADITION_COST_AMOUNTS', 'TRADITION_COST_TRADITION', 'TRADITION_COST_TRADITION_EXP',
               'EMPIRE_SIZE_TRADITION_COST_PENALTY', 'EMPIRE_SIZE_BASE',
               'TRADITION_CATEGORIES_MAX', 'ASCENSION_PERKS_SLOTS',
               # the empire dashboard's due dates for research and council agendas
               'EMPIRE_SIZE_TECH_COST_PENALTY', 'EMPIRE_SIZE_COUNCIL_AGENDA_COST_PENALTY',
               # … and its habitability colours (green / yellow / red, as in game)
               'HABITABILITY_WRONG_PLANET', 'HABITABILITY_OPPOSITE_PLANET',
               # … its systems tab (outpost influence, claims, starbase capacity)
               'EXPANSION_COST_BASE', 'EXPANSION_COST_DISTANCE', 'EXPANSION_COST_MAX',
               'CLAIM_COST_BASE', 'CLAIM_COST_DISTANCE', 'CLAIM_COST_MAX', 'STARBASE_CAPACITY_BASE',
               # … the market fee
               'MARKET_TRADE_FEE_BASE', 'MARKET_TRADE_FEE_MIN',
               # … pop growth
               'BASE_POP_GROWTH', 'BASE_POP_DECLINE', 'COLONY_MONTHLY_GROWTH', 'COLONY_POPS_REQUIRED',
               'LOGISTIC_POP_GROWTH_R', 'LOGISTIC_POP_GROWTH_FLOOR', 'LOGISTIC_POP_GROWTH_MIN_POPS',
               'LOGISTIC_POP_GROWTH_LOWER_THRESHOLD', 'LOGISTIC_CEILING_DEFAULT', 'GROWTH_SCALE_DEFAULT',
               'GROWTH_SCALE_MAGNITUDE_OFFSET', 'OVERCROWDING_NO_GROWTH_THRESHOLD',
               'OVERCROWDING_DECLINE_THRESHOLD', 'OVERCROWDING_NO_ASSEMBLY_THRESHOLD',
               'OVERCROWDING_MACHINE_DECLINE_THRESHOLD', 'BASE_POP_ASSEMBLY',
               # … the empire size the save stores floored
               'EMPIRE_SIZE_FLOOR', 'EMPIRE_SIZE_FROM_DISTRICTS', 'EMPIRE_SIZE_FROM_SYSTEMS',
               'EMPIRE_SIZE_FROM_COLONIES', 'EMPIRE_SIZE_FROM_BRANCH_OFFICES', 'EMPIRE_SIZE_FROM_POPS',
               # … and the AI's view of a trade deal
               'TRADE_VALUE_RESOURCE', 'TRADE_VALUE_RESOURCE_INCOME_BASE',
               'TRADE_VALUE_RESOURCE_MONTHLY_MULT', 'OFFER_TRADE_MIN_RESOURCE_THRESHOLD',
               'TRADE_RESOURCE_STOCKPILE_UNDER', 'TRADE_RESOURCE_STOCKPILE_OVER',
               'TRADE_RESOURCE_FRACTION_MIN', 'TRADE_RESOURCE_FRACTION_MAX', 'TRADE_MAX_RESOURCE_PERCENT',
               'TRADE_MIN_RESOURCE_DELTA', 'TRADE_MIN_RESOURCE_STOCKPILE', 'MIN_WILLINGNESS_TO_OFFER_TRADE',
               'OFFER_TRADE_ACCEPTANCE_ATTITUDE', 'BROKEN_TRADE_DEAL_FACTOR', 'MIN_GIFT_SIZE',
               'MAX_GIFT_SIZE', 'MIN_TRADE_DEAL_LENGTH_YEARS', 'MAX_TRADE_DEAL_LENGTH_YEARS')


# ── Pure helpers ───────────────────────────────────────────────────────────────

def pick_defines(defines: dict, keys=DEFINE_KEYS) -> dict[str, float]:
    """The whitelisted numeric defines anywhere in a parsed defines file. A block value
    (`TRADITION_COST_AMOUNTS = { 300 }`) takes its first entry."""
    out: dict[str, float] = {}

    def walk(node):
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if k in keys:
                raw = v[0] if isinstance(v, list) and v else v
                try:
                    out[k] = float(str(raw))
                except ValueError:
                    pass
            elif isinstance(v, dict):
                walk(v)

    walk(defines)
    return out


def required_dlc(tree: dict | None) -> str | None:
    """The DLC a potential requires unconditionally: a `host_has_dlc` leaf at its top level."""
    if not tree:
        return None
    top = tree['args'] if tree.get('op') == 'and' else [tree]
    for node in top:
        if node.get('leaf') == 'host_has_dlc':
            return node['value']
    return None


def prereqs(tree: dict | None, siblings: set[str]) -> list[str]:
    """Tree edges: traditions of the same tree that a `possible` asks for."""
    seen: list[str] = []

    def walk(node):
        if not node or node.get('op') == 'not':
            return  # a negated has_tradition is an exclusion, not an edge
        if node.get('leaf') == 'has_tradition':
            if node['value'] in siblings and node['value'] not in seen:
                seen.append(node['value'])
        for a in node.get('args', []):
            walk(a)

    walk(tree)
    return seen


def tooltip_keys(data: dict) -> list[str]:
    """Loc keys of the display-only effect texts: custom_tooltip(_with_modifiers) strings and
    the custom_tooltip strings directly inside on_enabled."""
    keys: list[str] = []
    for k in ('custom_tooltip', 'custom_tooltip_with_modifiers'):
        keys += [x for x in _to_list(data.get(k)) if isinstance(x, str)]
    for block in _as_blocks(data.get('on_enabled')):
        keys += [x for x in _to_list(block.get('custom_tooltip')) if isinstance(x, str)]
    return keys


# ── Extraction ────────────────────────────────────────────────────────────────

def _load(d: Path, scripted_vars: dict[str, float]) -> dict[str, tuple[dict, dict, str]]:
    """key → (data, @variables visible to it, file name), files in name order."""
    out: dict[str, tuple[dict, dict, str]] = {}
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return out
    for f in sorted(d.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        local = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                try:
                    local[k] = float(str(v))
                except ValueError:
                    pass
        for k, v in data.items():
            if isinstance(v, dict) and not k.startswith('@'):
                out[k] = (v, local, f.name)
    return out


class _Ctx:
    def __init__(self, loc, dlc_triggers, w: Writer):
        self.loc, self.dlc, self.w = loc, dlc_triggers, w

    def text(self, key: str | None, newlines: bool = False, variables=None) -> str | None:
        raw = self.loc.get(key) if key else None
        return _clean_loc(raw, self.loc, keep_newlines=newlines, variables=variables) if raw else None

    def trig(self, block, variables) -> dict | None:
        return normalize_trigger(block, self.dlc, variables)

    def tooltip(self, data: dict, variables) -> str | None:
        return join_texts(self.text(k, True, variables) for k in tooltip_keys(data))

    def modifiers(self, owner: str, data: dict, variables) -> None:
        rows: list[tuple[str, float, str | None]] = []
        for block in _as_blocks(data.get('modifier')):
            rows += [(k, v, None) for k, v in _block_modifier_values(block, variables)]
        for block in _as_blocks(data.get('triggered_modifier')):
            pot = dumps(self.trig(block.get('potential'), variables))
            rows += [(k, v, pot) for k, v in _block_modifier_values(block, variables)]
        for order, (k, v, pot) in enumerate(rows):
            self.w.execute(
                """INSERT INTO tradition_modifier
                   (owner_key, modifier_key, modifier_value, potential_json, sort_order)
                   VALUES (?, ?, ?, ?, ?)""", (owner, k, v, pot, order))
            _write_modifier_label(self.w, self.loc, k)

    def swaps(self, owner: str, owner_name: str | None, data: dict, variables,
              desc_suffix: str) -> int:
        """Write the owner's tradition_swaps. `name` is optional and not unique (two
        Subterfuge-finisher swaps share one), so a swap is identified as `<owner>#<n>`."""
        blocks = _as_blocks(data.get('tradition_swap'))
        for order, sw in enumerate(blocks):
            key = sw.get('name') if isinstance(sw.get('name'), str) else None
            inherit_effects = sw.get('inherit_effects') == 'yes'
            weight = 1.0
            for wb in _as_blocks(sw.get('weight')):
                try:
                    weight = float(str(wb.get('factor', 1)))
                except ValueError:
                    pass
            own_name = self.text(key) if key and sw.get('inherit_name') != 'yes' else None
            swap_id = f'{owner}#{order}'
            self.w.execute(
                """INSERT INTO tradition_swap
                   (id, owner_key, loc_key, name, description, trigger_json, inherit_effects,
                    weight, tooltip, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (swap_id, owner, key, own_name or owner_name,
                 self.text(key + desc_suffix, True, variables) if key else None,
                 dumps(self.trig(sw.get('trigger'), variables)), int(inherit_effects), weight,
                 self.tooltip(sw, variables), order))
            if not inherit_effects:
                self.modifiers(swap_id, sw, variables)
        return len(blocks)


def dumps(tree) -> str | None:
    return json.dumps(tree, separators=(',', ':')) if tree is not None else None


def extract_traditions(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                       w: Writer, verbose: bool, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    # Defines
    defines_file = common / 'defines' / '00_defines.txt'
    if defines_file.exists():
        for k, v in sorted(pick_defines(parse_file(defines_file)).items()):
            w.execute("INSERT OR REPLACE INTO game_define (key, value) VALUES (?, ?)", (k, v))

    # Tradition trees
    categories = _load(common / 'tradition_categories', scripted_vars)
    traditions = _load(common / 'traditions', scripted_vars)
    n_trad = n_swap = 0
    for cat_order, (ckey, (cdata, cvars, _)) in enumerate(categories.items()):
        potential = ctx.trig(cdata.get('potential'), cvars)
        if potential == {'leaf': 'always', 'value': 'no'}:
            continue  # placeholder tree (02_flexible_dummy)
        adopt, finish = cdata.get('adoption_bonus'), cdata.get('finish_bonus')
        members = [x for x in _to_list(cdata.get('traditions')) if isinstance(x, str)]
        w.execute(
            """INSERT INTO tradition_category
               (key, name, potential_json, required_dlc, adopt_key, finish_key, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ckey, ctx.text(ckey), dumps(potential), required_dlc(potential), adopt, finish,
             cat_order))
        desc_order = 0
        descs = cdata.get('desc')
        for d in descs if isinstance(descs, list) else [descs]:
            if isinstance(d, str):
                trig, text_key = None, d
            elif isinstance(d, dict) and isinstance(d.get('text'), str):
                trig, text_key = ctx.trig(d.get('trigger'), cvars), d['text']
            else:
                continue
            w.execute(
                """INSERT INTO tradition_category_desc
                   (category_key, trigger_json, description, sort_order) VALUES (?, ?, ?, ?)""",
                (ckey, dumps(trig), ctx.text(text_key, True, cvars), desc_order))
            desc_order += 1

        siblings = set(members)
        for order, (tkey, role) in enumerate([(adopt, 'adopt'), *((m, 'normal') for m in members),
                                              (finish, 'finish')]):
            if tkey not in traditions:
                print(f"  WARNING: {ckey}: tradition {tkey} not found", file=sys.stderr)
                continue
            tdata, tvars, _ = traditions[tkey]
            possible = ctx.trig(tdata.get('possible'), tvars)
            name = ctx.text(tkey)
            agenda = tdata.get('unlocks_agenda')
            w.execute(
                """INSERT INTO tradition
                   (key, category_key, role, name, description, possible_json, unlocks_agenda,
                    tooltip, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tkey, ckey, role, name, ctx.text(tkey + '_delayed', True, tvars),
                 dumps(possible),
                 agenda if isinstance(agenda, str) else None,
                 ctx.tooltip(tdata, tvars), order))
            for req in prereqs(possible, siblings):
                w.execute("INSERT OR IGNORE INTO tradition_prereq (tradition_key, requires_key) "
                          "VALUES (?, ?)", (tkey, req))
            ctx.modifiers(tkey, tdata, tvars)
            n_swap += ctx.swaps(tkey, name, tdata, tvars, '_delayed')
            n_trad += 1
        if verbose:
            print(f"  {ckey}: {len(members)} traditions")

    # Ascension perks
    perks = _load(common / 'ascension_perks', scripted_vars)
    for order, (pkey, (pdata, pvars, fname)) in enumerate(perks.items()):
        potential = ctx.trig(pdata.get('potential'), pvars)
        possible = ctx.trig(pdata.get('possible'), pvars)
        name = ctx.text(pkey)
        w.execute(
            """INSERT INTO ascension_perk
               (key, name, description, is_path, potential_json, possible_json,
                required_dlc, tooltip, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pkey, name, ctx.text(pkey + '_desc', True, pvars), int('paths' in fname),
             dumps(potential), dumps(possible),
             required_dlc(potential), ctx.tooltip(pdata, pvars), order))
        ctx.modifiers(pkey, pdata, pvars)
        n_swap += ctx.swaps(pkey, name, pdata, pvars, '_desc')
        if verbose:
            print(f"  {pkey}")

    print(f"  {len(categories)} tree(s), {n_trad} traditions, {len(perks)} ascension perks, "
          f"{n_swap} swaps", file=progress)
    w.commit()


UNLOCK_LEAVES = ('has_tradition', 'has_active_tradition', 'has_ascension_perk')


def unlock_keys(block, idx: dict[str, dict], leaves=UNLOCK_LEAVES,
                negated: bool = False) -> list[str]:
    """Values of `leaves` a block asks for in a positive context (outside NOT/NOR, `= no`),
    expanding scripted triggers such as has_psionic_ascension → ap_mind_over_matter.
    `negated` collects the ones inside a NOT/NOR instead (what the block rules out)."""
    out: list[str] = []
    seen: set[str] = set()

    def walk(node, inside_not: bool):
        if isinstance(node, list):
            for x in node:
                walk(x, inside_not)
            return
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            if k == 'NAND':
                continue
            neg = inside_not or k in ('NOT', 'NOR')
            if neg and not negated:
                continue
            for item in (v if isinstance(v, list) else [v]):
                if k in leaves and isinstance(item, str):
                    if inside_not == negated and item not in out:
                        out.append(item)
                elif isinstance(item, dict):
                    walk(item, neg)
                elif item == 'yes' and k in idx and k not in seen:
                    seen.add(k)
                    walk(idx[k], inside_not)

    walk(block, False)
    return out


def cost_amounts(block, variables: dict[str, float] | None = None) -> dict[str, float]:
    """A resources cost/upkeep block → {resource: amount}; multipliers are ignored."""
    variables = variables or {}
    out: dict[str, float] = {}
    for b in _as_blocks(block):
        for k, v in b.items():
            if k in ('multiplier', 'trigger') or isinstance(v, (dict, list)):
                continue
            s = str(v).strip()
            try:
                out[k] = variables[s] if s.startswith('@') else float(s)
            except (KeyError, ValueError):
                pass
    return out


def extract_edicts_agendas(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                           static_mods: dict, w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    idx = _scripted_trigger_index(install_dir)
    ctx = _Ctx(loc, dlc_trigger_map(idx), w)

    def unlocked_by(d: dict) -> str | None:
        keys = unlock_keys([d.get('potential'), d.get('allow')], idx)
        return json.dumps(keys) if keys else None

    edicts = _load(common / 'edicts', scripted_vars)
    for order, (key, (data, evars, _)) in enumerate(edicts.items()):
        res = data.get('resources') if isinstance(data.get('resources'), dict) else {}
        costs = {k: cost_amounts(res.get(k), evars) for k in ('cost', 'upkeep')}
        costs = {k: v for k, v in costs.items() if v}
        length = data.get('length')
        try:
            length = int(float(evars.get(length, length))) if length is not None else None
        except ValueError:
            length = None
        w.execute(
            """INSERT INTO edict (key, name, description, category, length, cost_json,
               potential_json, allow_json, unlocked_by_json, prerequisites_json, tooltip, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(f'edict_{key}') or ctx.text(key),
             ctx.text(f'edict_{key}_desc', True, evars) or ctx.text(key + '_desc', True, evars),
             res.get('category'), length,
             json.dumps(costs, sort_keys=True) if costs else None,
             dumps(ctx.trig(data.get('potential'), evars)), dumps(ctx.trig(data.get('allow'), evars)),
             unlocked_by(data),
             json.dumps(sorted(set(_to_list(data.get('prerequisites'))))) if data.get('prerequisites') else None,
             ctx.tooltip(data, evars), order))
        ctx.modifiers(key, data, evars)

    finish_default = cooldown_default = None
    defines_file = common / 'defines' / '00_defines.txt'
    if defines_file.exists():
        defs = pick_defines(parse_file(defines_file),
                            ('COUNCIL_AGENDA_FINISH_MODIFIER_DURATION', 'COUNCIL_AGENDA_COOLDOWN'))
        finish_default = defs.get('COUNCIL_AGENDA_FINISH_MODIFIER_DURATION')
        cooldown_default = defs.get('COUNCIL_AGENDA_COOLDOWN')

    def day_count(v, avars):
        try:
            return int(float(avars.get(v, v))) if v is not None else None
        except (ValueError, TypeError):
            return None

    agendas = _load(common / 'council_agendas', scripted_vars)
    for order, (key, (data, avars, _)) in enumerate(agendas.items()):
        cost = data.get('agenda_cost')
        try:
            cost = float(avars.get(cost, cost)) if cost is not None else None
        except (ValueError, TypeError):
            cost = None
        days = day_count(data.get('agenda_finish_modifier_duration', finish_default), avars)
        cooldown = day_count(data.get('agenda_cooldown', cooldown_default), avars)
        effect_keys = [x for b in _as_blocks(data.get('effect'))
                       for x in _to_list(b.get('custom_tooltip')) if isinstance(x, str)]
        effect = '\n'.join(t for t in (ctx.text(k, True, avars) for k in effect_keys) if t) or None
        w.execute(
            """INSERT INTO council_agenda (key, name, description, cost, finish_days, cooldown_days,
               potential_json, allow_json, unlocked_by_json, effect_tooltip, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(f'council_agenda_{key}_name'),
             ctx.text(f'council_agenda_{key}_desc', True, avars), cost, days, cooldown,
             dumps(ctx.trig(data.get('potential'), avars)), dumps(ctx.trig(data.get('allow'), avars)),
             unlocked_by(data), effect, order))
        ctx.modifiers(key, {'modifier': data.get('modifier')}, avars)
        finish = data.get('finish_modifier')
        if isinstance(finish, str) and isinstance(static_mods.get(finish), dict):
            ctx.modifiers(f'{key}#finish', {'modifier': static_mods[finish]}, avars)

    print(f"  {len(edicts)} edicts, {len(agendas)} council agendas", file=progress)
    w.commit()

