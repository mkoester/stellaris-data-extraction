"""Extraction: ideologies (4.6, common/ideologies) — the ideology types per ethic, the strength
levels and what each level gives an adopter, where Ideological Strength comes from, and the
tenets an ideology codifies with their tiers ("Intensity"), gestalt variants and the edicts,
policies and agendas they unlock. Also the buildings only an ideology allows. For the planner's
Ideologies page.

Which strength level opens which tenet slot is engine logic: no file says it."""

from pathlib import Path
import json
import re
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .politics import expand_inline
from .traditions import _Ctx, dumps
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _flatten_text, _to_list
from .writer import Writer

# The modifier blocks a tenet tier (and an ideology) applies, → the scope stored.
TIER_SCOPES = {'triggered_country_modifier': 'country', 'triggered_colony_modifier': 'colony',
               'triggered_system_modifier': 'system', 'triggered_pop_group_modifier': 'pop_group',
               'triggered_leader_modifier': 'leader'}
# Keys of a strength source that are not part of its condition.
_SOURCE_KEYS = {'desc', 'max_decimals', 'polarity', 'add', 'subtract', 'mult', 'factor', 'set'}
_OPS = ('add', 'subtract', 'mult', 'factor', 'set')


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _append(out: dict, key: str, value) -> None:
    """Add `key = value` to a parsed block the way the parser does: a repeat becomes a list."""
    if key not in out:
        out[key] = value
    elif isinstance(out[key], list) and all(isinstance(x, dict) for x in out[key]):
        out[key].append(value)
    else:
        out[key] = [out[key], value]


def inline_expanded(block, load, depth: int = 0):
    """A parsed block with every `inline_script` (bare name or `{ script = … PARAM = … }`)
    replaced by its body, parameters filled in, at any depth. `load(name)` → text or None."""
    if isinstance(block, list):
        return [inline_expanded(x, load, depth) for x in block]
    if not isinstance(block, dict) or depth > 8:
        return block
    out: dict = {}
    for k, v in block.items():
        if k != 'inline_script':
            out[k] = inline_expanded(v, load, depth)
    refs = block.get('inline_script')
    for ref in refs if isinstance(refs, list) else [] if refs is None else [refs]:
        if isinstance(ref, str):
            name, params = ref, {}
        elif isinstance(ref, dict) and isinstance(ref.get('script'), str):
            name = ref['script']
            params = {k: str(v).strip('"') for k, v in ref.items() if k != 'script'}
        else:
            continue
        text = load(name.strip('"'))
        if text is None:
            continue
        for k, v in inline_expanded(expand_inline(text, params), load, depth + 1).items():
            if isinstance(v, list) and all(isinstance(x, dict) for x in v) and v:
                for x in v:
                    _append(out, k, x)
            else:
                _append(out, k, v)
    return out


def ethic_of(fanatic: str | None) -> str | None:
    """`ethic_fanatic_authoritarian` → `ethic_authoritarian` (a gestalt ethic stays as it is)."""
    if not isinstance(fanatic, str):
        return None
    return fanatic.replace('ethic_fanatic_', 'ethic_', 1)


def custom_tooltips(node) -> list[str]:
    """The `custom_tooltip` loc keys an effect or modifier block shows, in file order, skipping
    `hidden_effect`s."""
    out: list[str] = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            for k, v in n.items():
                if k == 'hidden_effect':
                    continue
                if k == 'custom_tooltip':
                    out.extend(x for x in _to_list(v) if isinstance(x, str))
                elif isinstance(v, (dict, list)):
                    walk(v)
    walk(node)
    return list(dict.fromkeys(out))


_REF = re.compile(r'\$([\w.]+)\$')
_LINK = re.compile(r"\['(\w+):([\w:]+)'")


def concept_links(raw: str | None, loc: dict[str, str]) -> list[tuple[str, str]]:
    """The `['kind:key', …]` links a loc text holds once its $KEY$ refs are expanded →
    [(kind, key)], e.g. ('edict', 'peaceful_prosperity'); a policy link's
    `policy:<policy>:<option>` gives the option. Pure."""
    if not raw:
        return []
    s = raw
    for _ in range(6):
        before = s
        s = _REF.sub(lambda m: loc.get(m.group(1), m.group(0)), s)
        if s == before:
            break
    return list(dict.fromkeys((k, key.rsplit(':', 1)[-1]) for k, key in _LINK.findall(s)))


def tier_blocks(tenet: dict) -> list[tuple[int, dict]]:
    """A tenet's tiers → [(tier number, block)], numbered from its `first_tier` (default 1)."""
    try:
        first = int(str(tenet.get('first_tier', 1)))
    except ValueError:
        first = 1
    return [(first + i, b) for i, b in enumerate(_as_blocks(tenet.get('tier')))]


def adopted_level(potential) -> int | None:
    """The strength level a block is gated on (`adopted_ideology_level = N`, at any depth)."""
    if isinstance(potential, dict):
        for k, v in potential.items():
            if k == 'adopted_ideology_level' and not isinstance(v, (dict, list)):
                try:
                    return int(str(v))
                except ValueError:
                    return None
            found = adopted_level(v)
            if found is not None:
                return found
    elif isinstance(potential, list):
        for x in potential:
            found = adopted_level(x)
            if found is not None:
                return found
    return None


def strength_source(entry: dict, variables: dict[str, float]) -> tuple[str | None, float | None, str | None, dict]:
    """A `monthly_increase` modifier → (op, value, value_ref, condition). `value_ref` names what
    only the running game knows (`value:galactic_strength_bonus`); the condition is the rest."""
    op = next((o for o in _OPS if o in entry and not isinstance(entry[o], (dict, list))), None)
    raw = str(entry[op]).strip() if op else None
    value = ref = None
    if raw is not None:
        if raw.startswith('@'):
            value = variables.get(raw)
            ref = None if value is not None else raw
        else:
            try:
                value = float(raw)
            except ValueError:
                ref = raw
    cond = {k: v for k, v in entry.items() if k not in _SOURCE_KEYS}
    return op, value, ref, cond


def humanize_ref(ref: str | None) -> str | None:
    """A script value's name as a label, for a source whose tooltip only a running game fills
    (`value:clashing_ideologies_bonus` → "Clashing ideologies bonus")."""
    if not ref:
        return None
    name = ref.split(':', 1)[-1].replace('_', ' ').strip()
    return name[:1].upper() + name[1:] if name else None


_UNRESOLVED = re.compile(r',?\s*[^,:\n]*:\s*[+-]?%(?=,|\n|$)')


def drop_unresolved(text: str | None) -> str | None:
    """Clauses whose number a running game fills in (`[GetIdeologyRivalClashBonus]`) read
    "If Rival Ideology: +%" once cleaned: drop them, and brackets left with no number
    ("(Max. +)"). A `+` written before an already signed value ("++15%") loses one. Pure."""
    if text is None:
        return None
    text = _UNRESOLVED.sub('', text)
    text = re.sub(r'\s*\((?:[^()\d]*[+%-]|\s*)\)', '', text)
    text = re.sub(r'\+([+-])(?=\d)', r'\1', text)
    return text.strip() or None


# ── Extraction ────────────────────────────────────────────────────────────────

def _load_dir(d: Path) -> tuple[dict, dict[str, float]]:
    """A folder's top-level blocks and its files' @variables."""
    out: dict = {}
    variables: dict[str, float] = {}
    for f in sorted(d.glob('*.txt')) if d.is_dir() else []:
        try:
            parsed = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        for k, v in parsed.items():
            if k.startswith('@'):
                try:
                    variables[k] = float(str(v))
                except ValueError:
                    pass
            elif k == 'inline_script':
                out.setdefault(k, [])
                out[k] += v if isinstance(v, list) else [v]
            elif isinstance(v, dict):
                out[k] = v
    return out, variables


def council_bypasses(rule) -> list[tuple[str, str | None, str | None]]:
    """[(tenet, leader class, ethic)] from the game rule can_leader_bypass_councilor_conditions:
    each AND names a leader class, an ethic and the tenet the owner must have active."""
    out = []
    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            owner = n.get('owner?') or n.get('owner')
            tenet = owner.get('has_active_tenet') if isinstance(owner, dict) else None
            if isinstance(tenet, str):
                lc, eth = n.get('leader_class'), n.get('has_ethic')
                out.append((tenet, lc if isinstance(lc, str) else None,
                            eth if isinstance(eth, str) else None))
            else:
                for v in n.values():
                    walk(v)
    walk(rule)
    return out


def extract_ideologies(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                       w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common'
    inline_dir = common / 'inline_scripts'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    def load(name: str) -> str | None:
        path = inline_dir / f'{name}.txt'
        return path.read_text(encoding='utf-8-sig', errors='replace') if path.exists() else None

    def text(key, newlines=False, variables=None):
        raw = loc.get(key) if key else None
        if not raw:
            return None
        out = _clean_loc(raw, loc, keep_newlines=newlines, variables=variables)
        return drop_unresolved(re.sub(r'^[-•]\s*', '', out))

    def trig(block, variables):
        return dumps(ctx.trig(block, variables)) if isinstance(block, dict) and block else None

    # ── Ideologies, their levels and adoption effects ──
    types, type_vars = _load_dir(common / 'ideologies')
    variables = {**scripted_vars, **type_vars}
    n_mods = 0
    sources_written = False
    for order, (key, raw) in enumerate(types.items()):
        if key == 'inline_script':
            continue
        params = {}
        for ref in _as_blocks(raw.get('inline_script')):
            params.update({k: str(v) for k, v in ref.items() if k != 'script'})
        data = inline_expanded(raw, load)
        ethic = ethic_of(data.get('ethic') if isinstance(data.get('ethic'), str) else None)
        sec = params.get('SEC_ETHIC')
        opp = params.get('OPPOSED_SEC_ETHIC')
        faction = data.get('faction') if isinstance(data.get('faction'), str) else None
        tail = (ethic or '').replace('ethic_', '').upper()
        w.execute("INSERT INTO ideology (key, ethic, secondary_ethic, opposed_secondary_ethic, faction, faction_name, reach, type_name, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (key, ethic, f'ethic_{sec}' if sec else None, f'ethic_{opp}' if opp else None,
                   faction, text(f'pft_{faction}') if faction else None,
                   data.get('reach') if isinstance(data.get('reach'), str) else None,
                   text(f'IDEOLOGY_TYPE_{tail}_CAP') or text('IDEOLOGY_TYPE_DEFAULT_CAP'), order))
        start = 0.0
        for lv, block in enumerate(_as_blocks(data.get('strength_level'))):
            end = block.get('end')
            end_v = variables.get(end) if isinstance(end, str) and end.startswith('@') else None
            if end_v is None:
                try:
                    end_v = float(str(end))
                except ValueError:
                    end_v = None
            w.execute("INSERT INTO ideology_strength_level (ideology_key, level, start_value, end_value) VALUES (?, ?, ?, ?)",
                      (key, lv, start, end_v))
            start = end_v if end_v is not None else start

        n = 0
        for field, scope in TIER_SCOPES.items():
            for block in _as_blocks(data.get(field)):
                pot = block.get('potential')
                level = adopted_level(pot)
                scaled = block.get('mult') if isinstance(block.get('mult'), str) else None
                header = block.get('trigger_true_key_override')
                for mk, mv in _block_modifier_values(block, variables):
                    w.execute("INSERT INTO ideology_modifier (ideology_key, scope, level, header, modifier_key, modifier_value, scaled_by, potential_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (key, scope, level, text(header) if isinstance(header, str) else None,
                               mk, mv, scaled, trig(pot, variables), n))
                    _write_modifier_label(w, loc, mk)
                    n += 1
        n_mods += n

        # Every ideology inlines the same common data: write its strength sources once.
        if not sources_written and isinstance(data.get('monthly_increase'), dict):
            sources_written = True
            mi = data['monthly_increase']
            groups = [(c.get('desc'), _as_blocks(c.get('modifier'))) for c in _as_blocks(mi.get('category'))]
            groups.append((None, _as_blocks(mi.get('modifier'))))
            s = 0
            for cat, entries in groups:
                for entry in entries:
                    op, value, ref, cond = strength_source(entry, variables)
                    w.execute("INSERT INTO ideology_strength_source (category, label, op, value, value_ref, condition_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (text(cat) if cat else None,
                               text(entry.get('desc')) or humanize_ref(ref), op, value, ref,
                               trig(cond, variables), s))
                    s += 1

    # The game's own explanations (its concept tooltips), numbers filled from @variables
    concepts = sorted(k for k in loc if re.fullmatch(r'concept_ideolog\w*', k) and not k.endswith('_desc')
                      and f'{k}_desc' in loc)
    for c_order, key in enumerate(concepts):
        w.execute("INSERT INTO ideology_concept (key, name, text, sort_order) VALUES (?, ?, ?, ?)",
                  (key, text(key), text(f'{key}_desc', True, variables), c_order))

    # ── Tenets ──
    tenets, tenet_vars = _load_dir(common / 'ideologies' / 'tenets')
    variables = {**scripted_vars, **tenet_vars}
    for ref in tenets.pop('inline_script', []):      # Propagandism: one tenet per ethic
        for k, v in inline_expanded({'inline_script': ref}, load).items():
            if isinstance(v, dict):
                tenets.setdefault(k, v)
    n_tiers = n_tmods = n_links = 0
    for order, (key, raw) in enumerate(tenets.items()):
        data = inline_expanded(raw, load)
        ethics = [e for e in _to_list(data.get('ethics')) if isinstance(e, str)]
        unlock_keys = custom_tooltips(data.get('on_unlock'))
        lock_keys = custom_tooltips(data.get('on_lock'))
        unlock = '\n'.join(t for t in (text(k, True, variables) for k in unlock_keys) if t) or None
        lock = '\n'.join(t for t in (text(k, True, variables) for k in lock_keys) if t) or None
        first = tier_blocks(data)
        w.execute("INSERT INTO tenet (key, name, description, ethics_json, priority, source_civic, potential_json, first_tier, unlock_text, lock_text, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (key, text(key), text(f'{key}_desc', True, variables),
                   json.dumps(ethics, separators=(',', ':')),
                   data.get('priority') if isinstance(data.get('priority'), str) else 'default',
                   data.get('source_civic') if isinstance(data.get('source_civic'), str) else None,
                   trig(data.get('potential'), variables), first[0][0] if first else None,
                   unlock, lock, order))

        link_order = 0

        def links(keys, tier):
            nonlocal link_order, n_links
            for lk in keys:
                for kind, target in concept_links(loc.get(lk), loc):
                    w.execute("INSERT INTO tenet_unlock (tenet_key, tier, kind, target_key, sort_order) VALUES (?, ?, ?, ?, ?)",
                              (key, tier, kind, target, link_order))
                    link_order += 1
                    n_links += 1
        links(unlock_keys, None)

        swaps = data.get('swappable_data') if isinstance(data.get('swappable_data'), dict) else {}
        for s_order, swap in enumerate(_as_blocks(swaps.get('swap_type'))):
            name_key = swap.get('name') if isinstance(swap.get('name'), str) else None
            desc_key = swap.get('desc') if isinstance(swap.get('desc'), str) else None
            w.execute("INSERT INTO tenet_swap (tenet_key, trigger_json, name, description, sort_order) VALUES (?, ?, ?, ?, ?)",
                      (key, trig(swap.get('trigger'), variables), text(name_key),
                       text(desc_key, True, variables), s_order))

        for tier, block in first:
            n_tiers += 1
            tip_keys: list[str] = []
            m_order = 0
            for field, scope in TIER_SCOPES.items():
                for mb in _as_blocks(block.get(field)):
                    tip_keys += [t for t in _to_list(mb.get('custom_tooltip')) if isinstance(t, str)]
                    for mk, mv in _block_modifier_values(mb, variables):
                        w.execute("INSERT INTO tenet_tier_modifier (tenet_key, tier, scope, modifier_key, modifier_value, potential_json, possible_json, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (key, tier, scope, mk, mv, trig(mb.get('potential'), variables),
                                   trig(mb.get('possible'), variables), m_order))
                        _write_modifier_label(w, loc, mk)
                        m_order += 1
                        n_tmods += 1
            tip_keys = list(dict.fromkeys(tip_keys))
            effect = '\n'.join(t for t in (text(k, True, variables) for k in tip_keys) if t) or None
            if effect:
                w.execute("INSERT INTO tenet_tier (tenet_key, tier, effect_text) VALUES (?, ?, ?)",
                          (key, tier, effect))
            links(tip_keys, tier)

    # ── Tenets that let one leader class sit on any council position (a game rule) ──
    rules, _ = _load_dir(common / 'game_rules')
    bypasses = council_bypasses(rules.get('can_leader_bypass_councilor_conditions'))
    for tenet, leader_class, ethic in bypasses:
        w.execute("INSERT INTO tenet_council_bypass (tenet_key, leader_class, ethic) VALUES (?, ?, ?)",
                  (tenet, leader_class, ethic))

    # ── Buildings only an ideology allows ──
    buildings, b_vars = _load_dir(common / 'buildings')
    variables = {**scripted_vars, **b_vars}
    n_b = 0
    for key, data in buildings.items():
        pot = data.get('potential') if isinstance(data, dict) else None
        if isinstance(pot, dict) and re.search(r'\bideology\b', _flatten_text(pot)):
            w.execute("INSERT INTO ideology_building (building_key, potential_json, sort_order) VALUES (?, ?, ?)",
                      (key, trig(pot, variables), n_b))
            n_b += 1

    print(f"  {sum(1 for k in types if k != 'inline_script')} ideologies ({n_mods} adoption "
          f"modifiers), {len(tenets)} tenets ({n_tiers} tiers, {n_tmods} modifiers, "
          f"{n_links} unlock links, {len(bypasses)} council bypasses), {n_b} ideological buildings", file=progress)
    w.commit()
