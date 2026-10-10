"""Extraction: species traits, trait modifiers, trait categories."""

from pathlib import Path
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _block_scaling_value, _write_modifier_label
from .triggers import _signals
from .values import _as_blocks, _resolve_modifier_float, _to_list
from .writer import Writer


# ---------------------------------------------------------------------------
# Trait category configuration
# The category→color mapping is derived from common/inline_scripts/trait/
# (e.g. icon_psionic.txt references trait_bg_psionic). There is no single
# game file that encodes this mapping explicitly for all categories, so it
# is defined here as authoritative configuration.
# ---------------------------------------------------------------------------

TRAIT_CATEGORIES: list[tuple[str, str, str, int]] = [
    # (key,          label,               color_key,              display_order)
    ('normal',    'Species Traits',    'trait_bg_default',      0),
    ('phenotype', 'Phenotype Traits',  'trait_bg_society',      1),
    ('robotic',   'Synthetic Traits',  'trait_bg_machine',      2),
    ('psionic',   'Psionic Traits',    'trait_bg_psionic',      3),
    ('overtuned', 'Overtuned Traits',  'trait_bg_engineering',  4),
    ('cyborg',    'Cybernetic Traits', 'trait_bg_physics',      5),
    ('malleable', 'Malleable Traits',  'trait_bg_specialist',   6),
    ('mutation',  'Mutation Traits',   'trait_bg_negative',     7),
]


def extract_trait_categories(w: Writer, verbose: bool):
    for key, label, color_key, order in TRAIT_CATEGORIES:
        w.execute("""
            INSERT OR REPLACE INTO trait_category (key, label, color_key, display_order)
            VALUES (?, ?, ?, ?)
        """, (key, label, color_key, order))
        if verbose:
            print(f"  Category: {key} | {label} | {color_key}")
    w.commit()


# ---------------------------------------------------------------------------
# Extraction: traits
# ---------------------------------------------------------------------------

# Skip leader traits and documentation files — only species trait files wanted
_LEADER_FILE_TOKENS = {
    'leader', 'admiral', 'general', 'governor', 'scientist',
    'ambassador', 'delegate', 'paragon', 'subclass', 'npc_crisis',
    'starting_ruler', 'documentation',
}

def is_species_trait_file(path: Path) -> bool:
    name = path.stem.lower()
    return not any(tok in name for tok in _LEADER_FILE_TOKENS)


def _parse_cost(cost_raw) -> int | None:
    if isinstance(cost_raw, str):
        try:
            return int(cost_raw)
        except ValueError:
            return None
    if isinstance(cost_raw, dict):
        base = cost_raw.get('base')
        try:
            return int(base) if base is not None else None
        except (ValueError, TypeError):
            return None
    return None


# Trait fields that gate the trait on the empire's other picks → requirement kind.
# `allowed_*` means "only with one of these" (require_any), `forbidden_*` rules them out.
_TRAIT_GATE_KINDS = {'origins': 'origin', 'civics': 'civic', 'ethics': 'ethic',
                     'planet_classes': 'planet_class'}


def _trait_requirements(tdef: dict) -> list[tuple[str, str, str, int]]:
    """Eligibility rows [(kind, operator, value, group_id)] from a trait's
    allowed_/forbidden_ origins, civics, ethics and allowed_planet_classes. Each
    allowed_* list is one OR set; group ids number those sets per trait."""
    rows: list[tuple[str, str, str, int]] = []
    group = 0
    for field, kind in _TRAIT_GATE_KINDS.items():
        allowed = [v for v in _to_list(tdef.get(f'allowed_{field}')) if isinstance(v, str)]
        if allowed:
            group += 1
            rows += [(kind, 'require_any', v, group) for v in allowed]
        rows += [(kind, 'forbid', v, 0)
                 for v in _to_list(tdef.get(f'forbidden_{field}')) if isinstance(v, str)]
    return rows


def extract_traits(data: dict, source_file: str, loc: dict[str, str], w: Writer, verbose: bool):
    for trait_key, trait_data in data.items():
        if not isinstance(trait_data, dict):
            continue

        cost         = _parse_cost(trait_data.get('cost'))
        category     = trait_data.get('category')
        initial      = 0 if trait_data.get('initial',    'yes') == 'no' else 1
        randomized   = 0 if trait_data.get('randomized', 'yes') == 'no' else 1
        hidden       = 1 if trait_data.get('hidden',     'no')  == 'yes' else 0
        required_dlc = trait_data.get('host_has_dlc') or None
        raw_desc     = loc.get(trait_key + '_desc')
        description  = _clean_loc(raw_desc, loc, keep_newlines=True) if raw_desc else None
        # Behavioural effect from custom_tooltip: keep only the non-numeric lines (the numeric
        # "Label: ±N%" lines just restate modifiers, already shown as chips). Surfaces things
        # like Rooted's "Pops will not Automatically Resettle" that aren't a modifier. Split the
        # RAW loc on its \n first (else _clean_loc folds an indented "- …" sub-item up into the
        # line above, re-introducing the number); drop numeric lines and dangling ":" headers.
        ct_key = trait_data.get('custom_tooltip') or trait_data.get('custom_tooltip_with_modifiers')
        effect = None
        if isinstance(ct_key, str) and loc.get(ct_key):
            kept = []
            for raw_line in loc[ct_key].split('\\n'):
                ln = (_clean_loc(raw_line, loc) or '').strip().lstrip('-').strip()
                if ln and not ln.endswith(':') and not any(c.isdigit() for c in ln):
                    kept.append(ln)
            effect = '\n'.join(kept) or None

        w.execute("""
            INSERT OR IGNORE INTO trait
                (key, cost, category, initial, randomized, hidden, required_dlc, description, effect, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trait_key, cost, category, initial, randomized, hidden, required_dlc, description, effect, source_file))

        for arch in _to_list(trait_data.get('allowed_archetypes')):
            w.execute(
                "INSERT OR IGNORE INTO trait_archetype (trait_key, archetype) VALUES (?, ?)",
                (trait_key, arch))

        for sc in _to_list(trait_data.get('species_class')):
            w.execute(
                "INSERT OR IGNORE INTO trait_species_class (trait_key, species_class_key) VALUES (?, ?)",
                (trait_key, sc))

        for opp in _to_list(trait_data.get('opposites')):
            w.execute(
                "INSERT OR IGNORE INTO trait_opposite (trait_key, opposite_key) VALUES (?, ?)",
                (trait_key, opp))

        for tag in _to_list(trait_data.get('tags')):
            w.execute(
                "INSERT OR IGNORE INTO trait_tag (trait_key, tag) VALUES (?, ?)",
                (trait_key, tag))

        # Portraits explicitly allowed to pick this trait beyond its species_class gating.
        for pid in _to_list(trait_data.get('portrait_override')):
            w.execute(
                "INSERT OR IGNORE INTO trait_portrait_override (trait_key, portrait_id) VALUES (?, ?)",
                (trait_key, pid))

        for kind, op, val, gid in _trait_requirements(trait_data):
            w.execute(
                """INSERT OR IGNORE INTO trait_requirement
                       (trait_key, kind, operator, value, group_id) VALUES (?, ?, ?, ?, ?)""",
                (trait_key, kind, op, val, gid))

        if verbose:
            archs = _to_list(trait_data.get('allowed_archetypes'))
            print(f"  Trait: {trait_key} | cost: {cost} | archetypes: {archs}")

    w.commit()


def _trait_scaling_effects(tdef: dict, variables: dict[str, float], scaling: dict[str, dict]
                           ) -> list[tuple[str, float, float | None, str]]:
    """[(modifier_key, rate_per_unit, value_at_cap_or_None, unit)] for a trait's scaling
    modifiers (a block scaled by a script value). Familial → (pop_happiness, 0.001, 0.2, '100
    Familial pops'); Genetic Memory → (pop_environment_tolerance, 0.005, 0.25, 'year of colony
    age'); Invasive Species → (logistic_growth_mult, 0.05, None, 'negative trait') →
    '+5% Pop Growth Speed per negative trait' (uncapped, so no max)."""
    out: list[tuple[str, float, float | None, str]] = []
    for bk in ('modifier', 'triggered_species_modifier', 'triggered_pop_group_modifier',
               'triggered_planet_growth_habitability_modifier'):
        for block in _as_blocks(tdef.get(bk)):
            vname = _block_scaling_value(block)
            sv = scaling.get(vname) if vname else None
            if not sv:
                continue
            div = sv['divide'] or 1.0
            for k, base in _block_modifier_values(block, variables):
                cap = round(base * sv['max'], 6) if sv['max'] is not None else None
                out.append((k, round(base * sv['granularity'] / div, 6), cap, sv['unit']))
    # Budding-style growth: a `triggered_planet_pop_group_modifier_for_species` block with
    # `divide_over_pop_groups = no` applies its modifier per pop, shown per 100 pops (uncapped),
    # e.g. Plantoid/Lithoid Budding's `bonus_pop_growth = 0.02` → "+0.02 … per 100 pops". The
    # divide=no filter also excludes Lithoid Budding's conditional crater block (no such flag).
    for block in _as_blocks(tdef.get('triggered_planet_pop_group_modifier_for_species')):
        if str(block.get('divide_over_pop_groups', '')).strip().lower() != 'no':
            continue
        for k, base in _block_modifier_values(block, variables):
            out.append((k, round(base, 6), None, '100 pops'))
    return out


def _trait_modifier_pairs(tdef: dict, variables: dict[str, float], idx: dict[str, dict],
                          load_script, scaling: dict[str, dict],
                          seen: set[str] | None = None) -> dict[str, float]:
    """{modifier_key: summed value} a trait grants in a plain empire. Sources, in order:
    the top-level `modifier` block; `triggered_planet_growth_habitability_modifier` (the
    species' inherent growth/habitability — its `potential` only re-checks the species' own
    type + no-deficit, so it's taken unconditionally); `triggered_species_modifier` /
    `triggered_pop_group_modifier` (base variant — potential with no positive special
    requirement, like job swaps); `assembly_score` (pop-assembly → planet_pop_assembly_add);
    the `resources` block (pops produce, e.g. lithoid byproducts); and `inline_script` refs."""
    if seen is None:
        seen = set()
    out: dict[str, float] = {}

    def add(block: dict, scale: float = 1.0) -> None:
        if _block_scaling_value(block) in scaling:
            return  # scaled block (per pop / per year / per negative trait) → handled by
            #          _trait_scaling_effects as a "per X" row, not a flat modifier
        for k, v in _block_modifier_values(block, variables):
            out[k] = round(out.get(k, 0.0) + v * scale, 4)

    for block in _as_blocks(tdef.get('modifier')):
        add(block)
    for block in _as_blocks(tdef.get('triggered_planet_growth_habitability_modifier')):
        # Inherent only when gated by the species' own type (has_trait, always true for it,
        # e.g. Egg Laying) — NOT by a planet condition (Lithoid's extra +50% needs a lithoid
        # crater deposit, so it must not stack onto the unconditional base +50%).
        pot = block.get('potential')
        if pot is not None and not all(p.startswith('has_trait=') for p in _signals(pot, idx)[0]):
            continue
        add(block)
    for key in ('triggered_species_modifier', 'triggered_pop_group_modifier'):
        for block in _as_blocks(tdef.get(key)):
            pot = block.get('potential')
            if pot is not None and _signals(pot, idx)[0]:
                continue  # gated by a positive special condition → not the base variant
            add(block)
    # (`assembly_score` is deliberately NOT read: it's an internal biogenesis pop-assembly
    # *scoring* weight present on many ordinary traits — Intelligent, Strong, … — not a
    # player-facing "+N pop assembly" modifier.)
    # Pop resources: produces (e.g. lithoid byproducts) shown +, upkeep (e.g. the base
    # traits' food/minerals/energy/alloys pop cost) shown −. The first block per kind is the
    # default/base variant (later ones are phototrophic/voidling/… cases); a `value:` scope
    # mult is treated as its base of 1 (lithoid_trait_mult / budding_multiplier are base 1).
    res = tdef.get('resources')
    if isinstance(res, dict):
        for kind, sign in (('produces', 1.0), ('upkeep', -1.0)):
            first = next((b for b in _as_blocks(res.get(kind))), None)
            if first is not None:
                mv = _resolve_modifier_float(first.get('mult'))
                add(first, (mv if mv is not None else 1.0) * sign)
    isc = tdef.get('inline_script')
    for sn in (isc if isinstance(isc, list) else [isc]):
        if isinstance(sn, str) and sn not in seen:
            seen.add(sn)
            for k, v in _trait_modifier_pairs(load_script(sn), variables, idx,
                                              load_script, scaling, seen).items():
                out[k] = round(out.get(k, 0.0) + v, 4)
    return {k: v for k, v in out.items() if v != 0}


def extract_trait_modifiers(data: dict, source_file: str, loc: dict[str, str],
                            variables_base: dict[str, float], idx: dict[str, dict],
                            inline_scripts_dir: Path, scaling: dict[str, dict],
                            w: Writer, verbose: bool):
    # File-local @vars layered on the global scripted_variables base.
    variables = dict(variables_base)
    for key, val in data.items():
        if key.startswith('@'):
            fv = _resolve_modifier_float(val)
            if fv is not None:
                variables[key] = fv

    script_cache: dict[str, dict] = {}

    def load_script(name: str) -> dict:
        if name not in script_cache:
            p = inline_scripts_dir / (name + '.txt')
            try:
                script_cache[name] = parse_file(p) if p.exists() else {}
            except Exception:
                script_cache[name] = {}
        return script_cache[name]

    for trait_key, trait_data in data.items():
        if trait_key.startswith('@') or not isinstance(trait_data, dict):
            continue
        pairs = _trait_modifier_pairs(trait_data, variables, idx, load_script, scaling)
        for order, (mod_key, value) in enumerate(pairs.items()):
            w.execute(
                """INSERT OR IGNORE INTO trait_modifier
                       (trait_key, modifier_key, modifier_value, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (trait_key, mod_key, value, order),
            )
            # Populate modifier_label via the shared helper (icon-stripped,
            # attunement-aware, same loc lookup order for every modifier source).
            _write_modifier_label(w, loc, mod_key)
            if verbose:
                print(f"  Modifier: {trait_key} | {mod_key} = {value}")

        for order, (mod_key, rate, cap, unit) in enumerate(
                _trait_scaling_effects(trait_data, variables, scaling)):
            w.execute(
                """INSERT OR IGNORE INTO trait_scaling_modifier
                       (trait_key, modifier_key, rate, cap, unit, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (trait_key, mod_key, rate, cap, unit, order),
            )
            _write_modifier_label(w, loc, mod_key)

    w.commit()
