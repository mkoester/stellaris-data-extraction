"""Modifier metadata: good/bad direction, loc labels, scripted variables & scaling."""

from pathlib import Path
import re
from .clausewitz import parse_file
from .localisation import _clean_loc
from .values import _as_blocks, _resolve_modifier_float
from .writer import Writer


# ---------------------------------------------------------------------------
# Extraction: trait modifiers (flat modifier {} block only — Phase 1)
# ---------------------------------------------------------------------------

# Modifiers where a higher (more positive) value is worse for the empire/species.
# Extend this set whenever a new inverted modifier is discovered.
# Modifier keys whose loc key doesn't follow the automatic MOD_<UPPER> / mod_<lower> pattern.
# Maps modifier_key → explicit loc key. Extend when new mismatches are discovered.
MODIFIER_LOC_OVERRIDES: dict[str, str] = {
    'species_leader_exp_gain': 'MOD_LEADER_SPECIES_EXP_GAIN',
    # No MOD_ loc of its own; the closest is the "repelling" key ("Cosmic Storm
    # Repulsion Effect"). Used by the Tempest Invocator's triumph modifier.
    'country_storm_repulsion_influence_add': 'MOD_COUNTRY_STORM_REPELLING_ADD',
}

INVERTED_MODIFIERS: frozenset[str] = frozenset({
    # Upkeep / resource costs
    'leaders_upkeep_mult',
    'planet_pops_consumer_goods_upkeep_mult',
    'planet_pops_organics_alloys_upkeep_mult',
    'planet_pops_organics_food_upkeep_mult',
    'planet_pops_organics_minerals_upkeep_mult',
    'planet_pops_robotics_upkeep_mult',
    'planet_pops_upkeep_mult',
    'pop_resettlement_cost_mult',
    # Resource consumption
    'pop_amenities_usage_add',
    'pop_housing_usage_mult',
    # Administrative burden / empire size
    'species_empire_size_mult',
    'empire_size_pops_mult',
    'empire_size_mult',
    'empire_size_branch_office_mult',
    'empire_size_colonies_mult',
    'empire_size_districts_mult',
    'empire_size_systems_mult',
    'empire_size_penalty_mult',
    # Factions (the game pairs −unmet with +met as a bonus; +unmet is a civic drawback)
    'faction_demands_unmet_mult',
    # Influence costs
    'country_claim_influence_cost_mult',
    'country_starbase_influence_cost_mult',
    # Edict costs
    'edicts_upkeep_mult',
    'edicts_cost_mult',
    # War / exhaustion
    'country_war_exhaustion_mult',
    # Combat (taking more damage / longer cooldowns / higher costs = worse)
    'army_damage_taken_mult',
    'gravity_snare_cooldown_mult',
    'gravity_snare_cost_mult',
    'ships_upkeep_mult',
    'shroud_delve_cooldown',
    # Costs / fees (a reduction is good) — seen on precursor relics & rewards
    'pop_amenities_usage_mult',
    'terraforming_cost_mult',
    'country_trade_fee',
    # Trait slot counts (more negative trait slots allowed = worse)
    'negative_leader_traits_species',
})

# Authoritative good/bad for modifiers that the game defines in common/scripted_modifiers
# (each block may set `good = yes|no`: yes → a positive value is green, no → positive is red).
# Engine built-in modifiers (e.g. tradition_cost_empire_size_mult) aren't here — those fall
# back to the INVERTED_MODIFIERS set + the cost/upkeep/crime heuristic. Populated in main().
_SCRIPTED_MOD_GOOD: dict[str, bool] = {}


def _load_scripted_modifier_meta(install_dir: Path) -> None:
    """Populate _SCRIPTED_MOD_GOOD from common/scripted_modifiers/*.txt (explicit `good` only)."""
    sdir = install_dir / 'common' / 'scripted_modifiers'
    if not sdir.is_dir():
        return
    for f in sorted(sdir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        for key, block in data.items():
            if isinstance(block, dict) and 'good' in block:
                _SCRIPTED_MOD_GOOD[key] = str(block['good']).strip() == 'yes'


def _load_scripted_variables(install_dir: Path) -> dict[str, float]:
    """All @variables (resolved to float) from common/scripted_variables/*.txt — where
    shared trait/job values like @adaptive_base_output, @auto_penalty, @cyborg_hab_preference
    live (referenced by trait modifier blocks but not defined in the trait files)."""
    out: dict[str, float] = {}
    sdir = install_dir / 'common' / 'scripted_variables'
    if not sdir.is_dir():
        return out
    for f in sorted(sdir.glob('*.txt')):
        try:
            for vk, vv in parse_file(f).items():
                if vk.startswith('@'):
                    fv = _resolve_modifier_float(vv)
                    if fv is not None:
                        out[vk] = fv
        except Exception:
            continue
    return out


_MULT_VALUE_RE = re.compile(r'(?:\w+\.)?value:(\w+)')


def _load_scaling_values(install_dir: Path) -> dict[str, dict]:
    """Script `value:` multipliers used to scale a trait modifier, each capped by `max`:
    name → {max, divide, granularity, unit}. Two kinds — pop counters
    (`complex_trigger_modifier { trigger = count_owned_pop_amount, limit { has_trait } }`,
    shown per 100 pops, e.g. counter_familial/counter_noxious) and colony-age values
    (`modifier { add = trigger:colony_age_years }`, shown per year, e.g. genetic_memory_mult).
    Plus planet-size values (`base = 2  mult = planet.trigger:planet_size  divide = 3`, shown
    per planet size with `base` as the factor, e.g. hollow_bones_mult → 2/3 per size)."""
    out: dict[str, dict] = {}
    sdir = install_dir / 'common' / 'script_values'
    if not sdir.is_dir():
        return out
    for f in sorted(sdir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        for name, blk in data.items():
            if not isinstance(blk, dict):
                continue
            mx = _resolve_modifier_float(blk.get('max'))   # may be None (uncapped)
            div = _resolve_modifier_float(blk.get('divide')) or 1.0
            ctm = next((b for b in _as_blocks(blk.get('complex_trigger_modifier'))), None)
            trig = ctm.get('trigger') if isinstance(ctm, dict) else None
            if trig == 'count_owned_pop_amount':
                if mx is None:
                    continue
                params = ctm.get('parameters')
                limit = params.get('limit') if isinstance(params, dict) else None
                trait, negated = None, False
                if isinstance(limit, dict):
                    if isinstance(limit.get('has_trait'), str):
                        trait = limit['has_trait']
                    elif isinstance(limit.get('NOT'), dict) and isinstance(limit['NOT'].get('has_trait'), str):
                        trait, negated = limit['NOT']['has_trait'], True
                lab = trait.replace('trait_', '').replace('_', ' ').title() if trait else ''
                lab = ('non-' + lab) if negated else lab
                out[name] = {'max': mx, 'divide': div, 'granularity': 100.0,
                             'unit': f"100 {lab} pops".replace('  ', ' ')}
            elif trig == 'num_negative_traits':
                # Invasive Species: scaled per negative trait, with no `max` (uncapped).
                out[name] = {'max': mx, 'divide': div, 'granularity': 1.0,
                             'unit': 'negative trait'}
            elif 'planet_size' in str(blk.get('mult', '')):
                # Hollow Bones: base × planet size / divide, uncapped.
                out[name] = {'max': mx, 'divide': div,
                             'granularity': _resolve_modifier_float(blk.get('base')) or 1.0,
                             'unit': 'planet size'}
            elif mx is not None and any('colony_age_years' in str(m.get('add', ''))
                                        for m in _as_blocks(blk.get('modifier'))):
                out[name] = {'max': mx, 'divide': div, 'granularity': 1.0,
                             'unit': 'year of colony age'}
    return out


def _block_scaling_value(block: dict) -> str | None:
    """The script value name a block scales by (`mult = …value:X`, any X), or None."""
    if isinstance(block, dict):
        m = block.get('mult')
        if isinstance(m, str):
            mm = _MULT_VALUE_RE.match(m.strip())
            if mm:
                return mm.group(1)
    return None


# Sub-keys inside a modifier block that aren't stat modifiers: structural/scope keys, and
# the add/subtract/from of habitability-style `modifier = { add = N from = {…} }` calcs.
_MOD_BLOCK_SKIP = {'potential', 'trigger', 'desc', 'mult', 'multiplier', 'weight', 'modifier',
                   'add', 'subtract', 'from', 'prev', 'this'}


def _block_modifier_values(block: dict, variables: dict[str, float]) -> list[tuple[str, float]]:
    """[(modifier_key, value)] in a modifier block — its direct numeric stats plus those in
    a nested `modifier = { … }` sub-block; non-numeric/scope entries are skipped."""
    out: list[tuple[str, float]] = []
    for k, v in block.items():
        if k == 'modifier' and isinstance(v, dict):
            out += _block_modifier_values(v, variables)
            continue
        if k in _MOD_BLOCK_SKIP or isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        val = variables.get(s) if s.startswith('@') else _resolve_modifier_float(v)
        if val is not None and val != 0:
            out.append((k, val))
    return out


def _write_modifier_label(w: Writer, loc: dict[str, str], mod_key: str):
    """Populate modifier_label for a modifier key (same lookup order as traits/ethics)."""
    override = MODIFIER_LOC_OVERRIDES.get(mod_key)
    raw_label = (
        (loc.get(override) if override else None) or
        loc.get('MOD_' + mod_key.upper()) or
        loc.get('mod_' + mod_key) or
        loc.get(mod_key)
    )
    if not raw_label:
        return
    label = _clean_loc(raw_label, loc)
    # The attunement modifiers name their target only via an unresolvable colour
    # command ([This.GetCradleColor] etc.), leaving a dangling "Add Attunement with".
    # Derive the entity name from the key so it reads "Add Attunement with The …".
    if mod_key.startswith('add_attunement_'):
        entity = mod_key[len('add_attunement_'):].replace('_', ' ').title()
        label = f'Add Attunement with {entity}'
    # Per-job production modifiers (planet_<job>_<resource>_produces_*) name the job
    # via a scripted-loc command ([GetSoldierSwapPluralWithIcon]) that can't resolve,
    # so the cleaned label trails off as "<Resource> Produced per 100" / "… from".
    # The job is in the key — append it (the resource is already shown, so strip it
    # off the key tail to find the job): "Unity Produced per 100" → "… Soldiers".
    mprod = re.search(r'Produced (?:per \d+|from)$', label)
    keym = re.match(r'^planet_(.+)_produces_(?:add|mult)$', mod_key)
    if mprod and keym:
        resource_tok = label[:mprod.start()].strip().lower().replace(' ', '_')
        inner = keym.group(1)                      # e.g. "soldiers_unity"
        if resource_tok and inner.endswith('_' + resource_tok):
            job = inner[: -(len(resource_tok) + 1)]
            if job:
                label = f"{label} {job.replace('_', ' ').title()}"
    # Same dropped-job problem for per-job upkeep ("Upkeep for" → "Upkeep for X").
    keyu = re.match(r'^planet_(.+)_upkeep_(?:add|mult)$', mod_key)
    if keyu and label.endswith(' for'):
        label = f"{label} {keyu.group(1).replace('_', ' ').title()}"
    # Attunement % modifiers (<entity>_attunement_<add|mult>): the target sits in an
    # unresolvable colour command, leaving "Attunement with" — append the entity.
    am = re.match(r'^(.+)_attunement_(?:add|mult)$', mod_key)
    if am and label.rstrip().endswith('with'):
        label = f"{label.rstrip()} {am.group(1).replace('_', ' ').title()}"
    if '$' in label:
        return
    # Some modifiers localise to a runtime-only command that strips to nothing (e.g.
    # planet_crime_add = "[GetCrimeDeviancy]"). Fall back to a prettified key, dropping the
    # scope prefix and value suffix so it reads as a name ("Crime", not "Planet Crime").
    if not label.strip():
        label = re.sub(r'^(planet|pop|country|army|ship|species|leader)_', '', mod_key)
        label = re.sub(r'_(add|mult|perc)$', '', label).replace('_', ' ').title()
    # Good/bad colour: prefer the game's own `good` flag (common/scripted_modifiers) where it
    # defines the modifier. Otherwise fall back: lower is better for any cost/upkeep/fee key —
    # the curated set plus every `_cost_`/`_upkeep_` anywhere (e.g. tradition_cost_empire_size_mult
    # −25% is a saving, not a penalty) — and for crime (less crime is better).
    if mod_key in _SCRIPTED_MOD_GOOD:
        pig = 1 if _SCRIPTED_MOD_GOOD[mod_key] else 0
    else:
        inverted = (mod_key in INVERTED_MODIFIERS
                    or re.search(r'_(cost|upkeep|fee)(_|$)|crime', mod_key))
        pig = 0 if inverted else 1
    w.execute(
        """INSERT OR REPLACE INTO modifier_label (modifier_key, label, positive_is_good)
           VALUES (?, ?, ?)""",
        (mod_key, label, pig),
    )
