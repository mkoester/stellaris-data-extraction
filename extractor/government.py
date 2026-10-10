"""Extraction: ethics, policy restrictions, civics, authorities, origins."""

from pathlib import Path
import re
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _write_modifier_label
from .triggers import _trigger_values
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

        raw_label = loc.get(ethic_key)
        label = _clean_loc(raw_label, loc) if raw_label else None

        w.execute(
            """INSERT OR REPLACE INTO ethic
               (key, label, cost, category, fanatic_variant, regular_variant, gestalt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ethic_key, label, cost, category,
             str(fanatic_variant) if fanatic_variant else None,
             str(regular_variant) if regular_variant else None,
             gestalt),
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
                   verbose: bool) -> int:
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

        w.execute(
            """INSERT OR REPLACE INTO civic
                   (key, name, description, civic_type, required_dlc, selectable, locked, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, civic_type, dlc, selectable, locked, icon, order),
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

def compute_ascension_restrictions(install_dir: Path) -> dict[str, dict]:
    """Per origin, its ascension-path restriction → {'lock': path|None, 'blocks':[…]}.
    `lock` = locked to this single path (blocks all others); `blocks` = the paths it
    forbids when it still allows more than one. Data-driven: each path perk's
    `potential` has a `NOR = { … has_origin = X … }` listing origins it forbids; an
    origin forbidden from three of the four paths is locked to the fourth, one
    forbidden from one or two has those in `blocks`. Auto-ascension origins (forbidden
    from *every* path perk because they start already ascended) are resolved to their
    real path via the ascension council-agenda file (origins grouped under a path)."""
    AP_PATH = {
        'ap_engineered_evolution': 'Genetic', 'ap_the_flesh_is_weak': 'Cybernetic',
        'ap_synthetic_evolution': 'Synthetic', 'ap_mind_over_matter': 'Psionic',
    }
    ALL_PATHS = ['Genetic', 'Cybernetic', 'Synthetic', 'Psionic']

    def _origins_in(node, neg=0):
        out = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'has_origin':
                    out += [(x, neg) for x in (v if isinstance(v, list) else [v])]
                else:
                    out += _origins_in(v, neg + (1 if k in ('NOT', 'NOR') else 0))
        elif isinstance(node, list):
            for x in node:
                out += _origins_in(x, neg)
        return out

    forbid: dict[str, set] = {}
    allow: dict[str, set] = {}
    try:
        perks = parse_file(install_dir / 'common' / 'ascension_perks' / '00_ascension_paths.txt')
    except Exception:
        return {}
    for ap, name in AP_PATH.items():
        perk = perks.get(ap, {})
        if not isinstance(perk, dict):
            continue
        for blk in (perk.get('potential', {}), perk.get('possible', {})):
            for o, neg in _origins_in(blk):
                (forbid if neg % 2 else allow).setdefault(o, set()).add(name)

    def _agenda_path(n: str):
        if re.search(r'psionic|mind_over_matter', n):                          return 'Psionic'
        if re.search(r'synthetic|virtuality|nanotech', n):                     return 'Synthetic'
        if re.search(r'flesh_is_weak|cybernetic|organo_machine|modularity', n): return 'Cybernetic'
        if re.search(r'genetic|engineered_evolution|mutation|cloning|purity', n): return 'Genetic'
        return None
    agenda_path: dict[str, set] = {}
    try:
        agendas = parse_file(install_dir / 'common' / 'council_agendas'
                             / '02_council_agendas_ascensions.txt')
        for an, body in agendas.items():
            p = _agenda_path(an)
            if p and isinstance(body, dict):
                for o, neg in _origins_in(body):
                    if neg % 2 == 0:
                        agenda_path.setdefault(o, set()).add(p)
    except Exception:
        pass

    # Locked but never positively tied to one path in a machine-readable way.
    FALLBACK = {'origin_shroudwalker_apprentice': 'Psionic'}

    out: dict[str, dict] = {}
    for o in set(forbid) | set(allow):
        blocked = forbid.get(o, set()) - allow.get(o, set())
        free = [p for p in ALL_PATHS if p not in blocked]
        lock, blocks = None, []
        if len(free) == 1 and blocked:
            lock = free[0]                           # blocked from three → locked to one
        elif not free:                               # blocked from all → auto-ascension
            cands = agenda_path.get(o) or ({FALLBACK[o]} if o in FALLBACK else set())
            if len(cands) == 1:
                lock = next(iter(cands))
        elif blocked:                                # blocks one or two, still allows others
            blocks = [p for p in ALL_PATHS if p in blocked]
        if lock or blocks:
            out[o] = {'lock': lock, 'blocks': blocks}
    return out


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
