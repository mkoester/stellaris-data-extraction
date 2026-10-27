"""Extraction: ethics, policy restrictions, civics, authorities, origins."""

from pathlib import Path
import json
import re
from .clausewitz import IfBlock, parse_file
from .localisation import _clean_loc, join_texts
from .modifiers import _write_modifier_label
from .triggers import _scripted_trigger_index, _trigger_values, dlc_trigger_map, normalize_trigger
from .values import _resolve_modifier_float, _to_list
from .writer import Writer


# ---------------------------------------------------------------------------
# Extraction: policy restrictions (ethics-based)
# ---------------------------------------------------------------------------

def _find_blocked_ethics(block) -> set:
    """
    Recursively walk a parsed valid{} block and return the set of ethic keys
    that are blocked by NOR/NOT conditions.

    The parser promotes duplicate keys to lists, so has_ethic inside a NOR block
    may be a string (single) or list (multiple).
    We recurse into OR blocks because NOR can be nested inside an OR.
    We do NOT recurse into AND/custom_tooltip/etc. to avoid false positives.
    """
    blocked = set()

    if isinstance(block, list):
        for item in block:
            blocked |= _find_blocked_ethics(item)
        return blocked

    if not isinstance(block, dict):
        return blocked

    for key, value in block.items():
        if key in ('NOR', 'NOT'):
            # Ethics listed directly inside NOR/NOT are blocked
            if isinstance(value, dict):
                raw = value.get('has_ethic', [])
                if isinstance(raw, str):
                    raw = [raw]
                elif not isinstance(raw, list):
                    raw = []
                for e in raw:
                    e = str(e).strip('"')
                    if e.startswith('ethic_'):
                        blocked.add(e)
                # NOR/NOT may also contain nested NOR/NOT
                blocked |= _find_blocked_ethics(value)
            elif isinstance(value, list):
                for item in value:
                    blocked |= _find_blocked_ethics(item)
        elif key == 'OR':
            # OR can contain NOR/NOT sub-blocks — recurse without treating as blocked
            blocked |= _find_blocked_ethics(value)

    return blocked


def extract_policy_restrictions(data: dict, source_file: str,
                                loc: dict[str, str], w: Writer, verbose: bool):
    for policy_key, policy_data in data.items():
        if policy_key.startswith('@') or not isinstance(policy_data, dict):
            continue

        options = policy_data.get('option', [])
        if isinstance(options, dict):
            options = [options]
        elif not isinstance(options, list):
            continue

        for option_data in options:
            if not isinstance(option_data, dict):
                continue

            option_key = option_data.get('name', '')
            if isinstance(option_key, str):
                option_key = option_key.strip('"')
            if not option_key:
                continue

            valid = option_data.get('valid')
            if not valid:
                continue

            blocked = _find_blocked_ethics(valid)
            if not blocked:
                continue

            raw_label = loc.get(option_key)
            option_label = _clean_loc(raw_label, loc) if raw_label else None

            for ethic_key in sorted(blocked):
                w.execute(
                    """INSERT OR REPLACE INTO ethic_policy_restriction
                           (policy_key, option_key, option_label, ethic_key)
                       VALUES (?, ?, ?, ?)""",
                    (policy_key, option_key, option_label, ethic_key),
                )
                if verbose:
                    print(f"  {policy_key} > {option_key}: blocked for {ethic_key}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: ethics
# ---------------------------------------------------------------------------

# Modifier keys that are not real numeric modifiers (skip in ethic_modifier)
_ETHIC_SKIP_KEYS = frozenset({
    'show_only_custom_tooltip', 'custom_tooltip',
})

def _ethic_axis(ethic_data: dict) -> tuple[int | None, int]:
    """An ethic's place on its category's axis (`category_value`, fanatic 0 … fanatic 4) and
    whether it is the axis' opposite pole (`category_opposite`), per 99_documentation.txt."""
    raw = ethic_data.get('category_value')
    try:
        value = int(raw) if raw is not None else None
    except (ValueError, TypeError):
        value = None
    return value, 1 if ethic_data.get('category_opposite') == 'yes' else 0


def extract_ethics(data: dict, loc: dict[str, str], w: Writer, verbose: bool):
    # Collect @variable definitions (e.g. @ethic_attunement1 = 25)
    variables: dict[str, float] = {}
    for key, val in data.items():
        if key.startswith('@'):
            try:
                variables[key] = float(val)
            except (ValueError, TypeError):
                pass

    def resolve_float(raw) -> float | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if s.startswith('@'):
            return variables.get(s)
        try:
            return float(s)
        except ValueError:
            return None

    for ethic_key, ethic_data in data.items():
        if ethic_key.startswith('@') or not isinstance(ethic_data, dict):
            continue

        cost_raw = ethic_data.get('cost', 1)
        try:
            cost = int(cost_raw)
        except (ValueError, TypeError):
            cost = 1

        category = ethic_data.get('category')
        if isinstance(category, str):
            category = category.strip('"') or None

        fanatic_variant = ethic_data.get('fanatic_variant') or None
        regular_variant = ethic_data.get('regular_variant') or None
        gestalt = 1 if ethic_key == 'ethic_gestalt_consciousness' else 0
        category_value, category_opposite = _ethic_axis(ethic_data)

        raw_label = loc.get(ethic_key)
        label = _clean_loc(raw_label, loc) if raw_label else None

        w.execute(
            """INSERT OR REPLACE INTO ethic
               (key, label, cost, category, fanatic_variant, regular_variant, gestalt,
                category_value, category_opposite)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ethic_key, label, cost, category,
             str(fanatic_variant) if fanatic_variant else None,
             str(regular_variant) if regular_variant else None,
             gestalt, category_value, category_opposite),
        )

        modifier = ethic_data.get('country_modifier')
        if not isinstance(modifier, dict):
            if verbose:
                print(f"  {ethic_key}: no country_modifier")
            continue

        for order, (mod_key, mod_val) in enumerate(modifier.items()):
            if mod_key in _ETHIC_SKIP_KEYS:
                continue
            if isinstance(mod_val, list):
                mod_val = mod_val[-1]
            value = resolve_float(mod_val)
            if value is None:
                continue

            w.execute(
                """INSERT OR REPLACE INTO ethic_modifier
                   (ethic_key, modifier_key, modifier_value, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (ethic_key, mod_key, value, order),
            )

            # Populate modifier_label via the shared helper (icon-stripped,
            # attunement-aware, same loc lookup order for every modifier source).
            _write_modifier_label(w, loc, mod_key)

            if verbose:
                print(f"  {ethic_key} | {mod_key} = {value}")

        # Tags (gameplay implications)
        tags_raw = ethic_data.get('tags')
        if isinstance(tags_raw, list):
            tag_list = [str(t) for t in tags_raw]
        elif isinstance(tags_raw, str):
            tag_list = [tags_raw]
        else:
            tag_list = []

        for order, tag in enumerate(tag_list):
            if not tag.startswith('ETHIC_'):
                continue
            w.execute(
                """INSERT OR IGNORE INTO ethic_tag (ethic_key, tag, sort_order)
                   VALUES (?, ?, ?)""",
                (ethic_key, tag, order),
            )
            raw_tag_label = loc.get(tag)
            if raw_tag_label:
                tag_label = _clean_loc(raw_tag_label, loc)
                if '$' not in tag_label:
                    w.execute(
                        """INSERT OR REPLACE INTO ethic_tag_label (tag, label)
                           VALUES (?, ?)""",
                        (tag, tag_label),
                    )
            if verbose:
                print(f"  {ethic_key} | tag: {tag}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: civics
# ---------------------------------------------------------------------------

# potential/possible sub-keys we turn into eligibility rules → stored `kind`.
_CIVIC_GATE_KINDS = {'authority': 'authority', 'ethics': 'ethic',
                     'civics': 'civic', 'origin': 'origin',
                     'graphical_culture': 'ship_type',
                     'species_class': 'species_class',
                     'species_archetype': 'archetype',
                     'preferred_planet_class': 'planet_class',
                     'traits': 'trait'}


def _granted_traits(cdef: dict) -> list[str]:
    """Traits a civic/origin gives the founder species: its top-level
    `traits = { trait = … }` (one trait, or several promoted to a list). Nested,
    conditional `traits` blocks are not followed."""
    block = cdef.get('traits')
    if not isinstance(block, dict):
        return []
    return [t for t in _to_list(block.get('trait')) if isinstance(t, str)]


def _write_granted_traits(w: Writer, owner_key: str, cdef: dict) -> None:
    for t in _granted_traits(cdef):
        w.execute("INSERT OR IGNORE INTO granted_trait (owner_key, trait_key) VALUES (?, ?)",
                  (owner_key, t))


def _civic_requirements(cdef: dict) -> list[tuple[str, str, str, int]]:
    """Eligibility rows [(kind, operator, value, group_id)] from the top level of a
    civic's potential + possible blocks. (Deeply nested OR-of-different-kinds gates —
    rare — are not unpacked; those civics just carry fewer rules.)"""
    rows: list[tuple[str, str, str, int]] = []
    group = 0
    seen: set[tuple[str, str, str]] = set()
    for block_name in ('potential', 'possible'):
        block = cdef.get(block_name)
        if not isinstance(block, dict):
            continue
        for raw_kind, kind in _CIVIC_GATE_KINDS.items():
            for op, vals in _trigger_values(block.get(raw_kind)):
                gid = 0
                if op == 'require_any':
                    group += 1
                    gid = group
                for v in vals:
                    # The ship-type gate forbids the biological graphical cultures
                    # (biogenesis_01/02) — collapse them to a single 'organic' value.
                    if kind == 'ship_type' and v.startswith('biogenesis'):
                        v = 'organic'
                    if (kind, op, v) in seen:
                        continue
                    seen.add((kind, op, v))
                    rows.append((kind, op, v, gid))
        # Nomadic gameplay: `is_nomadic = yes` → nomads only, `= no` → settled only,
        # absent → both (Knights of the Toxic God and Storm Chasers since 4.5).
        nomadic = block.get('is_nomadic')
        if nomadic in ('yes', 'no'):
            row = ('gameplay', 'require' if nomadic == 'yes' else 'forbid', 'nomadic')
            if row not in seen:
                seen.add(row)
                rows.append((*row, 0))
    return rows


def extract_civics(data: dict, civic_type: str, loc: dict[str, str],
                   scripted_vars: dict[str, float], w: Writer, order_start: int,
                   verbose: bool, ascension: dict[str, dict]) -> int:
    """Extract one civics file; all its civics share `civic_type`. Returns the next
    sort_order so successive files keep a stable global order."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = order_start
    for key, cdef in data.items():
        if key.startswith('@') or not isinstance(cdef, dict):
            continue

        playable = cdef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            d = playable.get('host_has_dlc')
            if isinstance(d, list):
                d = d[0] if d else None
            dlc = str(d).strip('"') if d else None

        # Not player-pickable when gated to a specific country type (fallen empire,
        # pre-FTL, …) or explicitly playable = no.
        selectable = 1
        pot = cdef.get('potential')
        if isinstance(pot, dict) and 'country_type' in pot:
            selectable = 0
        if isinstance(playable, dict) and str(playable.get('always')).strip() == 'no':
            selectable = 0

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        desc_key = cdef.get('description')
        desc_key = desc_key.strip('"') if isinstance(desc_key, str) else None
        raw_desc = loc.get(desc_key) if desc_key else None
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = cdef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None
        # `modification = no` → the civic can't be added/removed via a government
        # reform after the game starts (origin-bound, devouring swarm, etc.).
        locked = 1 if str(cdef.get('modification')).strip().strip('"') == 'no' else 0

        asc = ascension.get(key) or {}
        w.execute(
            """INSERT OR REPLACE INTO civic
                   (key, name, description, civic_type, required_dlc, selectable, locked,
                    ascension_lock, ascension_blocks, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, civic_type, dlc, selectable, locked, asc.get('lock'),
             ','.join(asc.get('blocks', [])) or None, icon, order),
        )
        if verbose:
            print(f"  civic [{civic_type}] {key} | {name}"
                  f"{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = cdef.get('modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO civic_modifier
                           (civic_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)
                if verbose:
                    print(f"      modifier | {mk} = {value:g}")

        for kind, op, val, gid in _civic_requirements(cdef):
            w.execute(
                """INSERT OR IGNORE INTO civic_requirement
                       (civic_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )
        _write_granted_traits(w, key, cdef)

    w.commit()
    return order


# ---------------------------------------------------------------------------
# Extraction: authorities (government types)
# ---------------------------------------------------------------------------

# A playable `has_*` flag → the DLC it gates (only the ones used by authorities).
_AUTH_DLC = {'has_megacorp': 'MegaCorp'}


def _authority_requirements(adef: dict) -> list[tuple[str, str, str, int]]:
    """Eligibility rows [(kind, operator, value, group_id)] from an authority's
    `possible` block — only the meaningful `ethics` and `species_archetype` gates
    (the `origin` NOR lists are just Legendary-Leader gov-swap plumbing). Reuses the
    civic trigger parser for value/NOR/OR handling."""
    rows: list[tuple[str, str, str, int]] = []
    group = 0
    poss = adef.get('possible')
    if not isinstance(poss, dict):
        return rows
    for raw_kind, kind in (('ethics', 'ethic'), ('species_archetype', 'archetype')):
        for op, vals in _trigger_values(poss.get(raw_kind)):
            gid = 0
            if op == 'require_any':
                group += 1
                gid = group
            for v in vals:
                rows.append((kind, op, v, gid))
    return rows


def extract_authorities(data: dict, loc: dict[str, str],
                        scripted_vars: dict[str, float], w: Writer, verbose: bool) -> None:
    """Extract common/governments/authorities → authority (+ _modifier, _requirement)."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = 0
    for key, adef in data.items():
        if key.startswith('@') or not isinstance(adef, dict):
            continue

        # Not player-pickable when gated TO a specific country type (a positive
        # `country_type = { value = … }`, e.g. ai_empire). A `NOT/NOR` country_type
        # (corporate excludes primitives) is just an exclusion → still selectable.
        selectable = 1
        pot = adef.get('potential')
        if isinstance(pot, dict):
            ct = pot.get('country_type')
            if isinstance(ct, dict) and any(isinstance(x, str) for x in _to_list(ct.get('value'))):
                selectable = 0

        playable = adef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            if str(playable.get('always')).strip() == 'no':
                selectable = 0
            for flag, name in _AUTH_DLC.items():
                if str(playable.get(flag)).strip() == 'yes':
                    dlc = name

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        raw_desc = loc.get(key + '_desc')
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = adef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None

        w.execute(
            """INSERT OR REPLACE INTO authority
                   (key, name, description, required_dlc, selectable, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, dlc, selectable, icon, order),
        )
        if verbose:
            print(f"  authority {key} | {name}"
                  f"{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = adef.get('country_modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO authority_modifier
                           (authority_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)

        for kind, op, val, gid in _authority_requirements(adef):
            w.execute(
                """INSERT OR IGNORE INTO authority_requirement
                       (authority_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: origins
# ---------------------------------------------------------------------------

ASCENSION_PATH_PERKS = {
    'ap_engineered_evolution': 'Genetic', 'ap_the_flesh_is_weak': 'Cybernetic',
    'ap_synthetic_evolution': 'Synthetic', 'ap_mind_over_matter': 'Psionic',
}
ASCENSION_PATHS = ['Genetic', 'Cybernetic', 'Synthetic', 'Psionic']
_OWNER_TRIGGERS = ('has_origin', 'has_civic', 'has_valid_civic')
# Conditional or effect-side blocks: what they test is not a gate on the perk itself.
_SKIP_BLOCKS = ('if', 'else_if', 'else', 'limit', 'tradition_swap', 'ai_weight')


def _only_owner_tests(node, triggers: dict[str, dict], seen: frozenset = frozenset()) -> bool:
    """True when a block tests nothing but civics/origins (directly, through nested
    OR/AND, or through scripted triggers that do) — e.g. the variants of one civic."""
    if isinstance(node, list):
        return bool(node) and all(_only_owner_tests(x, triggers, seen) for x in node)
    if not isinstance(node, dict) or isinstance(node, IfBlock) or not node:
        return False
    for k, v in node.items():
        if k in _OWNER_TRIGGERS:
            continue
        if k in ('OR', 'AND') and _only_owner_tests(v, triggers, seen):
            continue
        if (k in triggers and k not in seen and str(v).strip() == 'yes'
                and _only_owner_tests(triggers[k], triggers, seen | {k})):
            continue
        return False
    return True


def _gate_owners(node, triggers: dict[str, dict], neg: int = 0, disj: bool = False,
                 seen: frozenset = frozenset()) -> list[tuple[str, bool, bool]]:
    """The civic/origin keys a trigger tree tests, as (key, negated, in_disjunction).
    NOT/NOR and `<scripted_trigger> = no` flip polarity; scripted triggers are expanded
    through `triggers` (is_natural_design_empire → has_valid_civic = civic_natural_design).
    `in_disjunction` marks a test that is only one alternative (an OR branch, or the
    inside of a NOT = { AND … }), so a negated one there is not a hard ban."""
    out: list[tuple[str, bool, bool]] = []
    if isinstance(node, list):
        for x in node:
            out += _gate_owners(x, triggers, neg, disj, seen)
        return out
    if not isinstance(node, dict) or isinstance(node, IfBlock):
        return out
    for k, v in node.items():
        if k in _SKIP_BLOCKS:
            continue
        if k in _OWNER_TRIGGERS:
            out += [(x, neg % 2 == 1, disj) for x in _to_list(v) if isinstance(x, str)]
        elif k in ('NOT', 'NOR'):
            out += _gate_owners(v, triggers, neg + 1, disj or (k == 'NOT' and neg % 2 == 0
                                and isinstance(v, dict) and len(v) > 1), seen)
        elif k in ('OR', 'AND'):
            # OR is a disjunction when positive, AND when negated (De Morgan) — unless
            # every alternative is itself a civic/origin test (OR of a civic's variants).
            branchy = (k == 'OR') == (neg % 2 == 0) and not _only_owner_tests(v, triggers)
            out += _gate_owners(v, triggers, neg, disj or branchy, seen)
        elif k in triggers and k not in seen and str(v).strip() in ('yes', 'no'):
            flip = 1 if str(v).strip() == 'no' else 0
            out += _gate_owners(triggers[k], triggers, neg + flip, disj, seen | {k})
        elif isinstance(v, (dict, list)):
            out += _gate_owners(v, triggers, neg, disj, seen)    # custom_tooltip & co.
    return out


def _named_origins(node, neg: int = 0) -> list[str]:
    """Every origin a block names outside a NOT/NOR — `if`/`limit` included, since the
    agenda file ties an origin to its path in exactly such conditional blocks."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'has_origin':
                out += [x for x in _to_list(v) if isinstance(x, str) and neg % 2 == 0]
            else:
                out += _named_origins(v, neg + (1 if k in ('NOT', 'NOR') else 0))
    elif isinstance(node, list):
        for x in node:
            out += _named_origins(x, neg)
    return out


def _agenda_path(name: str) -> str | None:
    if re.search(r'psionic|mind_over_matter', name):                          return 'Psionic'
    if re.search(r'synthetic|virtuality|nanotech', name):                     return 'Synthetic'
    if re.search(r'flesh_is_weak|cybernetic|organo_machine|modularity', name): return 'Cybernetic'
    if re.search(r'genetic|engineered_evolution|mutation|cloning|purity', name): return 'Genetic'
    return None


# Locked but never positively tied to one path in a machine-readable way.
_ASCENSION_FALLBACK = {'origin_shroudwalker_apprentice': 'Psionic'}


def ascension_restrictions(perks: dict, triggers: dict[str, dict],
                           agendas: dict) -> dict[str, dict]:
    """Per origin or civic, its ascension-path restriction → {'lock': path|None,
    'blocks': […]}. Pure: takes the parsed path-perk file, the scripted-trigger index
    and the parsed ascension council-agenda file.

    Each path perk's `potential`/`possible` names what it forbids (`NOR = { has_origin
    = X }`, `is_natural_design_empire = no`, …). Forbidden from three of the four paths
    → `lock` on the fourth; from one or two → those in `blocks`. Forbidden from all
    four: an origin starts already ascended, resolved to its path via the agenda file
    (origins grouped under a path); a civic simply blocks every path (`blocks` = all)."""
    forbid: dict[str, set] = {}
    allow: dict[str, set] = {}
    for ap, path in ASCENSION_PATH_PERKS.items():
        perk = perks.get(ap)
        if not isinstance(perk, dict):
            continue
        for blk in (perk.get('potential', {}), perk.get('possible', {})):
            for key, negated, disj in _gate_owners(blk, triggers):
                if not negated:
                    allow.setdefault(key, set()).add(path)
                elif not disj:
                    forbid.setdefault(key, set()).add(path)

    agenda_path: dict[str, set] = {}
    for name, body in agendas.items():
        path = _agenda_path(name)
        if path and isinstance(body, dict):
            for key in _named_origins(body):
                agenda_path.setdefault(key, set()).add(path)

    out: dict[str, dict] = {}
    for key in sorted(forbid):
        blocked = forbid[key] - allow.get(key, set())
        free = [p for p in ASCENSION_PATHS if p not in blocked]
        lock, blocks = None, []
        if len(free) == 1 and blocked:
            lock = free[0]                           # blocked from three → locked to one
        elif not free and key.startswith('origin_'):  # blocked from all → auto-ascension
            cands = agenda_path.get(key) or ({_ASCENSION_FALLBACK[key]}
                                             if key in _ASCENSION_FALLBACK else set())
            if len(cands) == 1:
                lock = next(iter(cands))
        elif blocked:                                # some or (a civic) all paths
            blocks = [p for p in ASCENSION_PATHS if p in blocked]
        if lock or blocks:
            out[key] = {'lock': lock, 'blocks': blocks}
    return out


def compute_ascension_restrictions(install_dir: Path) -> dict[str, dict]:
    """ascension_restrictions() over the installed game files ({} if they're missing)."""
    try:
        perks = parse_file(install_dir / 'common' / 'ascension_perks' / '00_ascension_paths.txt')
    except Exception:
        return {}
    try:
        agendas = parse_file(install_dir / 'common' / 'council_agendas'
                             / '02_council_agendas_ascensions.txt')
    except Exception:
        agendas = {}
    return ascension_restrictions(perks, _scripted_trigger_index(install_dir), agendas)


def leader_traits_needed(block):
    """`any_owned_leader = { has_trait = X }` (or an OR of traits): a seat that opens with a leader
    who has that trait (the enclaves' seats). Rewritten as `has_owned_leader_trait = X` leaves, which
    a reader can check against the empire's leaders; a scope stays opaque otherwise."""
    if not isinstance(block, dict) or set(block) != {'any_owned_leader'}:
        return block
    inner = block['any_owned_leader']
    if not isinstance(inner, dict):
        return block
    traits = inner.get('has_trait')
    if traits is None and isinstance(inner.get('OR'), dict) and set(inner) == {'OR'}:
        traits = inner['OR'].get('has_trait')
    elif set(inner) != {'has_trait'}:
        return block
    traits = [t for t in (traits if isinstance(traits, list) else [traits]) if isinstance(t, str)]
    return {'OR': {'has_owned_leader_trait': traits}} if traits else block


def extract_council_positions(install_dir: Path, loc: dict[str, str],
                              scripted_vars: dict[str, float], w: Writer, verbose: bool) -> None:
    """common/governments/councilors → council_position (+ _modifier): every seat, with the
    civic that opens it (if any) and its `possible`. Modifier values are per leader level;
    `effect` is the custom tooltip some seats show instead of (or beside) their modifiers."""
    cdir = install_dir / 'common' / 'governments' / 'councilors'
    if not cdir.is_dir():
        return
    dlc_triggers = dlc_trigger_map(_scripted_trigger_index(install_dir))
    order = 0
    for f in sorted(cdir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@') and (fv := _resolve_modifier_float(v)) is not None:
                variables[k] = fv
        for key, cdef in data.items():
            if key.startswith('@') or not isinstance(cdef, dict):
                continue
            civic = cdef.get('civic') if isinstance(cdef.get('civic'), str) else None
            possible = normalize_trigger(leader_traits_needed(cdef.get('possible')), dlc_triggers,
                                         variables)
            name = _clean_loc(loc[key], loc) if key in loc else None
            raw_desc = loc.get(f'{key}_desc')
            desc = (_clean_loc(raw_desc.replace('$TITLE$', name or ''), loc)
                    if raw_desc else None)
            classes = [c for c in _to_list(cdef.get('leader_class')) if isinstance(c, str)]
            tts = [loc[t] for t in (cdef.get('custom_tooltip'),
                                    cdef.get('custom_tooltip_with_modifiers'))
                   if isinstance(t, str) and t in loc]
            effect = join_texts(_clean_loc(t, loc, keep_newlines=True, variables=variables)
                                for t in tts)
            w.execute(
                """INSERT OR REPLACE INTO council_position
                       (key, civic_key, possible_json, name, description, leader_classes, effect,
                        sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, civic, json.dumps(possible, separators=(',', ':')) if possible else None,
                 name, desc, ','.join(classes) or None, effect, order))
            order += 1
            modifier = cdef.get('modifier')
            for m_order, (mk, mv) in enumerate((modifier or {}).items()
                                               if isinstance(modifier, dict) else []):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO council_position_modifier
                           (position_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order))
                _write_modifier_label(w, loc, mk)
    w.commit()
    if verbose:
        print(f"  council positions: {order}")


def _custom_empire_initializers(install_dir: Path) -> set[str]:
    """The initializer keys usable as a plain custom-empire start (`usage = custom_empire`).
    An origin whose `initializers` list includes anything else forces a fixed home
    system (its own `usage = origin` start, e.g. shattered_ring_start)."""
    custom: set[str] = set()
    idir = install_dir / 'common' / 'solar_system_initializers'
    if not idir.is_dir():
        return custom
    for f in sorted(idir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        for key, idef in data.items():
            if isinstance(idef, dict) and str(idef.get('usage')).strip() == 'custom_empire':
                custom.add(key)
    return custom


def extract_origins(data: dict, force_nonselectable: bool, loc: dict[str, str],
                    scripted_vars: dict[str, float], w: Writer, order_start: int,
                    verbose: bool, ascension: dict[str, dict],
                    init_custom: set[str]) -> int:
    """Extract one origins file. `force_nonselectable` marks every origin in it
    non-selectable (the non-playable file). Returns the next sort_order."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = order_start
    for key, odef in data.items():
        if key.startswith('@') or not isinstance(odef, dict):
            continue
        if str(odef.get('is_origin')).strip() != 'yes':
            continue

        playable = odef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            d = playable.get('host_has_dlc')
            if isinstance(d, list):
                d = d[0] if d else None
            dlc = str(d).strip('"') if d else None

        # Not player-pickable: the non-playable file; swap-variant forms (potential
        # gated on already having an origin); country-type-gated; or playable = no.
        selectable = 0 if force_nonselectable else 1
        pot = odef.get('potential')
        if isinstance(pot, dict) and ('origin' in pot or 'country_type' in pot):
            selectable = 0
        if isinstance(playable, dict) and str(playable.get('always')).strip() == 'no':
            selectable = 0

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        desc_key = odef.get('description')
        desc_key = desc_key.strip('"') if isinstance(desc_key, str) else None
        raw_desc = loc.get(desc_key) if desc_key else None
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = odef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None

        asc = ascension.get(key) or {}
        starting_colony = odef.get('starting_colony')
        starting_colony = starting_colony.strip('"') if isinstance(starting_colony, str) else None
        # Origin forces a fixed home system when it lists an initializer that isn't one
        # of the standard custom-empire starts (origins like arc_welders just list the
        # standard set → no override).
        inits = [ik for ik in _to_list(odef.get('initializers')) if isinstance(ik, str)]
        forces_home_system = 1 if any(ik not in init_custom for ik in inits) else 0
        w.execute(
            """INSERT OR REPLACE INTO origin
                   (key, name, description, required_dlc, selectable, ascension_lock,
                    ascension_blocks, starting_colony, forces_home_system, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, dlc, selectable, asc.get('lock'),
             ','.join(asc.get('blocks', [])) or None, starting_colony, forces_home_system, icon, order),
        )
        if verbose:
            print(f"  origin {key} | {name}{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = odef.get('modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO origin_modifier
                           (origin_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)

        for kind, op, val, gid in _civic_requirements(odef):
            w.execute(
                """INSERT OR IGNORE INTO origin_requirement
                       (origin_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )
        _write_granted_traits(w, key, odef)

    w.commit()
    return order


def extract_government_types(data: dict, loc: dict[str, str], w: Writer, verbose: bool) -> None:
    """common/governments/*.txt → government_type: the name the game gives an empire's
    government (`gov_representative_democracy` → "Representative Democracy"), for the empire
    dashboard. Keys without localisation keep a NULL name."""
    for key in sorted(k for k, v in data.items() if isinstance(v, dict) and not k.startswith('@')):
        name = _clean_loc(loc[key], loc) if key in loc else None
        w.execute("INSERT OR REPLACE INTO government_type (key, name) VALUES (?, ?)", (key, name))
        if verbose:
            print(f"  government_type: {key} = {name}")
    w.commit()
