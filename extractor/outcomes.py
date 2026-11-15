"""Extraction: what anomalies, special projects and dig-site chapters actually do, for the empire
dashboard's situations tab.

Each one's events are followed through their options, `random_list`s, `if` chains, scripted
effects and follow-up events, down to the effects a player gets: resources, months of income, tech
progress, deposits, modifiers, traits, specimens, relics, further special projects. The result is a
tree per anomaly category, special project and dig site (table `outcome`), from which the dashboard
derives what kind of outcome it is — fixed, random, a choice, depending on the empire.

Effect nodes (`t` is the kind; names resolved from localisation where the game has one):
  res {r, n}            add_resource
  months {r, n, min, max}  add_monthly_resource_mult: n months of income, clamped
  tech {k, name, p}     add_tech_progress (p = share), give_technology (p absent)
  option {k, name}      add_research_option
  deposit {k, name}, clear_deposits, modifier {k, name, days}, trait {k, name}, xp {n},
  specimen {k, name}, relic {k, name}, pc {k, name}, megastructure {k, name}, leader, pops,
  species, fleet {mine}, kill_leader, destroy, project {k, name}, note {text}
  random {b: [{w, cond, w_if?, e}]}  a random_list (locked too), a weighted anomaly outcome,
                        `random = { chance }`; w_if: a conditional branch's weight when its
                        modifiers' `add`s hold
  if {b: [e…], else}    an if/else_if/else chain (branches whose effects are all equal collapse)
  event {id, title, delay?, e, o: [{name, cond, e}]}  a visible event: immediate/after in e
  later {days, e}       a hidden event fired at least a year later
  ref {id, title}       an event already shown higher up, or past the depth limit
"""

from pathlib import Path
import json
import re
import sys
from .game_start import parse_ordered
from .localisation import _clean_loc
from .mods import top_level_spans

_EVENT_ID = re.compile(r'^[A-Za-z_][\w]*\.\d+$')
_EVENT_KEYS = frozenset({
    'event', 'ship_event', 'fleet_event', 'country_event', 'planet_event', 'pop_event',
    'leader_event', 'observer_event', 'system_event', 'colony_event', 'starbase_event',
    'carrier_event', 'astral_rift_event', 'situation_event', 'first_contact_event',
    'pop_group_event', 'espionage_operation_event', 'bypass_event', 'agreement_event',
    'anomaly_event'})
# blocks that test rather than do
_TRIGGERS = frozenset({
    'limit', 'trigger', 'allow', 'potential', 'ai_chance', 'ai_weight', 'weight', 'modifier',
    'desc', 'title', 'picture', 'mean_time_to_happen', 'abort_trigger', 'exclusive_trigger',
    'response_text', 'is_triggered_only', 'location', 'show_sound', 'trackable'})
_OPTION_CONDITIONS = ('trigger', 'allow', 'exclusive_trigger', 'potential')
_SKIPPED = re.compile(r'^tutorial\.')   # the tutorial's popups: a side event, unless the root
_LATER_DAYS = 360
MAX_DEPTH = 10
MAX_EVENTS = 60   # events followed per root; the rest become refs
# A visible event fired from this many places is a shared side event (story.5, the first-contact
# catalogue, 175 references in 4.5) rather than this outcome's own: a ref, marked `shared`.
# Hidden ones stay expanded — ancrel.15055 (57) adds a dig site's artifact deposits.
SHARED_REFS = 10
_ID_REF = re.compile(r'\bid\s*=\s*"?([A-Za-z_]\w*\.\d+)')


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def _int_or_float(x: float) -> int | float:
    return int(x) if x == int(x) else round(x, 4)


def _get(entries: list[tuple], key: str):
    """The first value of `key` in an ordered block, or None."""
    return next((v for k, _, v in entries if k == key), None)


def _is_block(v) -> bool:
    """An ordered block ([(key, op, value)]) — not a `limit` parsed as a dict or a token list."""
    return isinstance(v, list) and all(isinstance(x, tuple) and len(x) == 3 for x in v)


def expand_inline(entries: list[tuple], load, depth: int = 0) -> list[tuple]:
    """`inline_script` entries replaced by the script's own (parameters filled in), recursively;
    values that are not ordered blocks are left alone."""
    out = []
    for key, op, value in entries:
        if key == 'inline_script' and depth < 20:
            name, params = value, {}
            if _is_block(value):
                params = {k: v for k, _, v in value if k != 'script' and isinstance(v, str)}
                name = _get(value, 'script')
            text = load(name) if isinstance(name, str) else None
            if text is not None:
                out += expand_inline(parse_ordered(_strip_params(text, params)), load, depth + 1)
        elif _is_block(value):
            out.append((key, op, expand_inline(value, load, depth + 1)))
        else:
            out.append((key, op, value))
    return out


def _strip_params(text: str, params: dict[str, str]) -> str:
    """A scripted effect's body with `$PARAM$` / `$PARAM|default$` filled in."""
    def sub(m):
        return params.get(m.group(1), m.group(2)[1:] if m.group(2) else '')
    return re.sub(r'\$(\w+)(\|[^$]*)?\$', sub, text)


def event_index(text: str) -> dict[str, str]:
    """An events file's text → {event id: that event's own text}; also `@name` → value."""
    out: dict[str, str] = {}
    for key, start, end in top_level_spans(text):
        span = text[start:end]
        if key.startswith('@'):
            m = re.match(r'@\w+\s*=\s*(\S+)', span)
            if m:
                out[key] = m.group(1)
            continue
        m = re.search(r'\bid\s*=\s*"?([\w.]+)', span)
        if m:
            out[m.group(1)] = span
    return out


def definition_index(text: str) -> dict[str, str]:
    """A scripted-effects file's text → {name: body text inside its outer braces}."""
    out: dict[str, str] = {}
    for key, start, end in top_level_spans(text):
        span = text[start:end]
        a, b = span.find('{'), span.rfind('}')
        if 0 <= a < b:
            out[key] = span[a + 1:b]
    return out


class Outcomes:
    """Follows effect blocks into effect trees. Inputs are text indexes, so it is testable
    without the game: `events` id → event text, `effects` scripted effect → body text,
    `variables` @name → number."""

    def __init__(self, events: dict[str, str], effects: dict[str, str], loc: dict[str, str],
                 variables: dict[str, float] | None = None, load_inline=None,
                 shared: frozenset[str] = frozenset()):
        self.events = events
        self.shared = shared
        self.effects = effects
        self.loc = loc
        self.variables = dict(variables or {})
        self.load_inline = load_inline or (lambda name: None)
        self._parsed: dict[str, list] = {}

    # names ---------------------------------------------------------------------------------

    def text(self, key: str | None) -> str | None:
        raw = self.loc.get(key) if key else None
        return _clean_loc(raw, self.loc) if raw else None

    def _named(self, t: str, key: str) -> dict:
        key = key.strip('"')
        return {'t': t, 'k': key, 'name': self.text(key)}

    def title(self, eid: str) -> str | None:
        """An event's title as the player sees it: its `title` key's text (a block's first
        `text`), else `<id>.name`."""
        entries = self.event(eid)
        t = _get(entries, 'title') if entries else None
        if isinstance(t, list):
            t = next((v for k, _, v in t if k == 'text'), None)
        return (self.text(t) if isinstance(t, str) else None) or self.text(f'{eid}.name')

    def value(self, v) -> float | None:
        if isinstance(v, str) and v.startswith('@'):
            v = self.variables.get(v, v)
        return _num(v)

    # parsing -------------------------------------------------------------------------------

    def event(self, eid: str) -> list | None:
        if eid not in self._parsed:
            text = self.events.get(eid)
            entries = None
            if text:
                parsed = expand_inline(parse_ordered(text), self.load_inline)
                entries = next((v for k, _, v in parsed if _is_block(v)), None)
            self._parsed[eid] = entries
        return self._parsed[eid]

    def scripted(self, name: str, params: dict[str, str]) -> list | None:
        body = self.effects.get(name)
        if body is None:
            return None
        try:
            return expand_inline(parse_ordered(_strip_params(body, params)), self.load_inline)
        except (ValueError, IndexError):
            return None

    # walking -------------------------------------------------------------------------------

    def root(self, block, kind: str = 'effect') -> list[dict]:
        """An `on_success`: an anomaly's weighted list of events (`1 = anomaly.35`,
        `0 = { modifier = {…} anomaly_event = … }`), a bare event id, or a project's effects."""
        ctx = _Ctx()
        if isinstance(block, str):
            return self.follow(block, 0, ctx, 0)
        if not isinstance(block, list):
            return []
        weighted = [(k, v) for k, _, v in block if _num(k) is not None]
        if kind == 'anomaly' and weighted:
            return self._random([(_num(k), v) for k, v in weighted], 0, ctx)
        return self.walk(block, 0, ctx)

    def walk(self, entries: list[tuple], depth: int, ctx: '_Ctx', sdepth: int = 0) -> list[dict]:
        out: list[dict] = []
        if not _is_block(entries):
            return out
        i = 0
        while i < len(entries):
            key, _op, value = entries[i]
            i += 1
            if key == 'if':
                chain = [value]
                has_else = False
                while i < len(entries) and entries[i][0] in ('else_if', 'else'):
                    has_else = has_else or entries[i][0] == 'else'
                    chain.append(entries[i][2])
                    i += 1
                out += self._if(chain, has_else, depth, ctx, sdepth)
            elif key in ('else_if', 'else'):
                continue
            else:
                out += self.effect(key, value, depth, ctx, sdepth)
        return _dedupe(out)

    def _if(self, chain, has_else, depth, ctx, sdepth) -> list[dict]:
        branches = [self.walk(b, depth, ctx, sdepth) if _is_block(b) else [] for b in chain]
        if not any(branches):
            return []
        if has_else and all(b == branches[0] for b in branches):
            return branches[0]
        return [{'t': 'if', 'b': branches, 'else': has_else}]

    def _random(self, weighted: list[tuple[float, object]], depth, ctx, sdepth=0) -> list[dict]:
        branches = []
        for w, v in weighted:
            if isinstance(v, str):
                e = self.follow(v, depth, ctx, sdepth) if _EVENT_ID.match(v) else []
                cond = False
            elif _is_block(v):
                e = self.walk(v, depth, ctx, sdepth)
                cond = _get(v, 'modifier') is not None
            else:
                continue
            b = {'w': _int_or_float(w), 'cond': cond, 'e': e}
            if cond:
                b['w_if'] = _int_or_float(w + self._weight_adds(v))
            branches.append(b)
        if len(branches) == 1 and not branches[0]['cond']:
            return branches[0]['e']
        if not any(b['e'] for b in branches):
            return []
        return [{'t': 'random', 'b': branches}]

    def _weight_adds(self, entries: list[tuple]) -> float:
        """What a weighted entry's `modifier = { add = N … }` blocks add when they hold — the
        Mineral Cluster's outcomes are `0` plus 1 for a scientist without a given trait."""
        total = 0.0
        for k, _, v in entries:
            if k == 'modifier' and _is_block(v):
                total += self.value(_get(v, 'add')) or 0
        return total

    def effect(self, key: str, value, depth: int, ctx: '_Ctx', sdepth: int) -> list[dict]:
        if key in _TRIGGERS:
            return []
        if key in _EVENT_KEYS:
            if isinstance(value, str):
                return self.follow(value, depth, ctx, sdepth)
            if _is_block(value):
                eid = _get(value, 'id')
                days = (self.value(_get(value, 'days')) or 0) + (self.value(_get(value, 'random')) or 0) / 2
                return self.follow(eid, depth, ctx, sdepth, int(days)) if isinstance(eid, str) else []
            return []
        if key in ('random_list', 'locked_random_list') and _is_block(value):
            return self._random([(_num(k), v) for k, _, v in value if _num(k) is not None],
                                depth, ctx, sdepth)
        if key == 'random' and _is_block(value):
            chance = self.value(_get(value, 'chance'))
            e = self.walk([x for x in value if x[0] != 'chance'], depth, ctx, sdepth)
            if not e:
                return []
            if chance is None or chance >= 100:
                return e
            return [{'t': 'random', 'b': [{'w': _int_or_float(chance), 'cond': False, 'e': e},
                                          {'w': _int_or_float(100 - chance), 'cond': False, 'e': []}]}]
        if key == 'custom_tooltip' and isinstance(value, str):
            raw = self.loc.get(value)
            text = self.text(value) if raw and '[' not in raw and '$' not in raw else None
            return [{'t': 'note', 'text': text}] if text else []
        if key == 'add_resource' and _is_block(value):
            mult = _get(value, 'mult')
            out = []
            for r, _, n in value:
                if r == 'mult':
                    continue
                v = self.value(n)
                if v is not None:
                    e = {'t': 'res', 'r': r, 'n': _int_or_float(v)}
                    if mult is not None:
                        e['scaled'] = True
                    out.append(e)
            return out
        if key == 'add_monthly_resource_mult' and _is_block(value):
            e = {'t': 'months', 'r': _get(value, 'resource')}
            for k in ('value', 'min', 'max'):
                v = self.value(_get(value, k))
                if v is not None:
                    e['n' if k == 'value' else k] = _int_or_float(v)
            return [e] if isinstance(e['r'], str) else []
        if key in ('add_tech_progress', 'give_technology'):
            tech = _get(value, 'tech') if _is_block(value) else value
            if not isinstance(tech, str):
                return []
            e = self._named('tech', tech)
            p = self.value(_get(value, 'progress')) if _is_block(value) else None
            if key == 'add_tech_progress' and p is not None:
                e['p'] = _int_or_float(p)
            return [e]
        if key == 'add_research_option' and isinstance(value, str):
            return [self._named('option', value)]
        if key == 'add_deposit' and isinstance(value, str):
            return [self._named('deposit', value)]
        if key == 'clear_deposits' and value == 'yes':
            return [{'t': 'clear_deposits'}]
        if key == 'add_modifier' and _is_block(value):
            m = _get(value, 'modifier')
            if not isinstance(m, str):
                return []
            e = self._named('modifier', m)
            for unit, f in (('days', 1), ('months', 30), ('years', 360)):
                v = self.value(_get(value, unit))
                if v is not None:
                    e['days'] = -1 if v < 0 else int(v * f)
            return [e]
        if key in ('add_trait', 'add_trait_no_notify'):
            t = _get(value, 'trait') if _is_block(value) else value
            return [self._named('trait', t)] if isinstance(t, str) and '$' not in t else []
        if key == 'add_experience':
            v = self.value(value)
            return [{'t': 'xp', 'n': _int_or_float(v)}] if v else []
        if key == 'give_specimen' and _is_block(value):
            k = _get(value, 'key')
            return [self._named('specimen', k)] if isinstance(k, str) else []
        if key == 'add_relic' and isinstance(value, str):
            return [self._named('relic', value)]
        if key == 'change_pc' and isinstance(value, str):
            return [self._named('pc', value)]
        if key == 'spawn_megastructure' and _is_block(value):
            k = _get(value, 'type')
            return [self._named('megastructure', k)] if isinstance(k, str) else []
        if key == 'enable_special_project' and _is_block(value):
            k = _get(value, 'name')
            return [self._named('project', k)] if isinstance(k, str) else []
        if key == 'create_leader':
            return [{'t': 'leader'}]
        if key in ('create_pop_group', 'create_pop'):
            return [{'t': 'pops'}]
        if key == 'modify_species':
            return [{'t': 'species'}]
        if key == 'kill_leader':
            return [{'t': 'kill_leader'}]
        if key in ('destroy_ship', 'destroy_fleet'):
            return [{'t': 'destroy'}]
        if key == 'create_fleet' and _is_block(value):
            return [{'t': 'fleet', 'mine': _owner_is_us(value)}]
        if key == 'hidden_effect' and _is_block(value):
            return self.walk(value, depth, ctx, sdepth)
        if key in self.effects and sdepth < 8:
            params = {k: v for k, _, v in value if isinstance(v, str)} if _is_block(value) else {}
            if _is_block(value) or value == 'yes':
                body = self.scripted(key, params)
                return self.walk(body, depth, ctx, sdepth + 1) if body else []
            return []
        if _is_block(value):   # a scope: owner = {…}, from = {…}, random_owned_planet = {…}
            return self.walk(value, depth, ctx, sdepth)
        return []

    def follow(self, eid: str, depth: int, ctx: '_Ctx', sdepth: int = 0, delay: int = 0) -> list[dict]:
        eid = eid.strip('"')
        entries = self.event(eid)
        if entries is None or (depth > 0 and _SKIPPED.match(eid)):
            return []
        hidden = _get(entries, 'hide_window') == 'yes'
        if not hidden and eid in self.shared and depth > 0:
            return [{'t': 'ref', 'id': eid, 'title': self.title(eid), 'shared': True}]
        if eid in ctx.path or depth >= MAX_DEPTH or ctx.count >= MAX_EVENTS:
            return [] if hidden else [{'t': 'ref', 'id': eid, 'title': self.title(eid)}]
        ctx.path.append(eid)
        ctx.count += 1
        try:
            pre = self.walk(_get(entries, 'immediate') or [], depth + 1, ctx, sdepth)
            post = self.walk(_get(entries, 'after') or [], depth + 1, ctx, sdepth)
            specimen = _get(entries, 'specimen')
            if isinstance(specimen, str):
                pre.append(self._named('specimen', specimen))
            options = []
            for k, _, v in entries:
                if k != 'option' or not _is_block(v):
                    continue
                name = _get(v, 'name')
                options.append({
                    'name': self.text(name) if isinstance(name, str) else None,
                    'cond': any(_get(v, k) is not None for k in _OPTION_CONDITIONS),
                    'e': self.walk(v, depth + 1, ctx, sdepth)})
        finally:
            ctx.path.pop()
        options = _distinct_options(options)
        if hidden:
            e = pre + [x for o in options[:1] for x in o['e']] + post
            if not e:
                return []
            return [{'t': 'later', 'days': delay, 'e': e}] if delay >= _LATER_DAYS else e
        node = {'t': 'event', 'id': eid, 'title': self.title(eid), 'e': pre + post, 'o': options}
        if delay >= _LATER_DAYS:
            node['delay'] = delay
        return [node]


class _Ctx:
    def __init__(self):
        self.path: list[str] = []
        self.count = 0


def _owner_is_us(entries: list[tuple]) -> bool:
    """Whether a `create_fleet` block gives the fleet to the scope's own empire (root, owner,
    prev…) rather than to a country an event made or names by target."""
    def owners(block):
        for k, _, v in block:
            if k in ('set_owner', 'owner') and isinstance(v, str):
                yield v
            elif _is_block(v):
                yield from owners(v)
    found = list(owners(entries))
    return bool(found) and all(re.search(r'root|owner|^prev|^this|^from', o) and 'event_target' not in o
                               for o in found)


def _distinct_options(options: list[dict]) -> list[dict]:
    """Options alike in name, condition and effect once: variants the game tells apart only by
    `exclusive_trigger` (distar.3057's ten "Excellent." — one per species trait modified)."""
    out, seen = [], set()
    for o in options:
        s = json.dumps(o, sort_keys=True)
        if s not in seen:
            out.append(o)
            seen.add(s)
    return out


def _dedupe(effects: list[dict]) -> list[dict]:
    """Repeated identical effects once (a scripted effect's branches adding the same trait)."""
    out, seen = [], set()
    for e in effects:
        s = json.dumps(e, sort_keys=True)
        if e['t'] in ('event', 'random', 'if', 'res', 'months', 'xp') or s not in seen:
            out.append(e)
            seen.add(s)
    return out


def tree_json(tree: list[dict]) -> str | None:
    return json.dumps(tree, ensure_ascii=False, separators=(',', ':')) if tree else None


# ── Extraction ────────────────────────────────────────────────────────────────

def _read(f: Path) -> str:
    return f.read_text(encoding='utf-8-sig', errors='replace')


def _ordered_definitions(d: Path) -> dict[str, list]:
    """Every top-level definition of a `common/` folder, parsed in file order (later files win)."""
    out: dict[str, list] = {}
    if not d.is_dir():
        print(f"  WARNING: {d} not found", file=sys.stderr)
        return out
    for f in sorted(d.glob('*.txt')):
        if f.name.startswith('99_README'):
            continue
        for k, _, v in parse_ordered(_read(f)):
            if _is_block(v):
                out[k] = v
    return out


def load_outcomes(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                  load_inline) -> Outcomes:
    events: dict[str, str] = {}
    variables = dict(scripted_vars)
    refs: dict[str, int] = {}
    files = sorted((install_dir / 'events').glob('*.txt')) + sorted((install_dir / 'common').glob('*/*.txt'))
    for f in files:
        for m in _ID_REF.finditer(_read(f)):
            refs[m.group(1)] = refs.get(m.group(1), 0) + 1
    for f in sorted((install_dir / 'events').glob('*.txt')):
        for k, v in event_index(_read(f)).items():
            if k.startswith('@'):
                n = _num(v)
                if n is not None:
                    variables.setdefault(k, n)
            else:
                events[k] = v
    effects: dict[str, str] = {}
    for f in sorted((install_dir / 'common' / 'scripted_effects').glob('*.txt')):
        effects.update(definition_index(_read(f)))
    shared = frozenset(k for k, n in refs.items() if n >= SHARED_REFS)
    return Outcomes(events, effects, loc, variables, load_inline, shared)


def extract_outcomes(install_dir: Path, oc: Outcomes, w, progress=sys.stderr) -> None:
    """`outcome` rows for every anomaly category, special project and dig site; `dig_site` rows
    with each site type's name and chapters."""
    common = install_dir / 'common'
    counts = {'anomaly': 0, 'project': 0, 'dig_site': 0}

    def put(kind, key, tree):
        j = tree_json(tree)
        if j:
            w.execute("INSERT OR REPLACE INTO outcome (kind, key, tree_json) VALUES (?, ?, ?)",
                      (kind, key, j))
            counts[kind] += 1

    for key, cat in sorted(_ordered_definitions(common / 'anomalies').items()):
        if not key.startswith('@'):
            put('anomaly', key, oc.root(_get(cat, 'on_success'), 'anomaly'))

    projects: dict[str, list] = {}
    d = common / 'special_projects'
    for f in sorted(d.glob('*.txt')):
        for k, _, v in parse_ordered(_read(f)):
            if k == 'special_project' and _is_block(v):
                pk = _get(v, 'key')
                if isinstance(pk, str):
                    projects[pk.strip('"')] = v
    for key, p in sorted(projects.items()):
        put('project', key, oc.root(_get(p, 'on_success') or []))

    for key, site in sorted(_ordered_definitions(common / 'archaeological_site_types').items()):
        if key.startswith('@'):
            continue
        chapters = []
        for k, _, v in site:
            if k == 'stage' and _is_block(v):
                ev = _get(v, 'event')
                diff = oc.value(_get(v, 'difficulty'))
                chapters.append({'t': 'chapter', 'n': len(chapters) + 1,
                                 'difficulty': _int_or_float(diff) if diff is not None else None,
                                 'e': oc.follow(ev, 0, _Ctx()) if isinstance(ev, str) else []})
        desc = _get(site, 'desc')
        w.execute("INSERT OR REPLACE INTO dig_site (key, name, description, stages) VALUES (?, ?, ?, ?)",
                  (key, oc.text(key), _clean_loc(oc.loc[desc.strip('"')], oc.loc, keep_newlines=True)
                   if isinstance(desc, str) and desc.strip('"') in oc.loc else None, len(chapters)))
        put('dig_site', key, chapters)

    print(f"  outcomes: {counts['anomaly']} anomaly categories, {counts['project']} special projects, "
          f"{counts['dig_site']} dig sites", file=progress)
    w.commit()
