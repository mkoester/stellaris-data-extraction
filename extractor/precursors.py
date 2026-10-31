"""Extraction: precursor civilizations, relics, deposits, home systems."""

import sys
from .buildings import _building_cap, _building_inline_jobs, _building_modifiers, _building_resources
from .localisation import _clean_loc
from .modifiers import _write_modifier_label
from .values import _as_blocks, _find_first, _resolve_modifier_float, _resolve_var, _to_list
from .writer import Writer


# ---------------------------------------------------------------------------
# Extraction: precursors
# ---------------------------------------------------------------------------
#
# The precursor<->reward mapping is NOT encoded in any single game file: it is
# scattered across events/precursor_events*.txt and the ancient_relics arcsite
# events, where rewards are granted by add_relic / add_modifier deep inside dig
# event chains. This config is therefore the authoritative spine (verified
# against those events); the effect numbers and display names attached to each
# relic/modifier/building are pulled from the game files at extraction time.
#
# Per precursor:
#   key, dlc           required DLC display name (None = base game)
#   system             home system initializer key
#   num_digsites       sites in the archaeology chain (None where not clearly grouped)
#   relic              relic key, or None (First League & Irassian have no relic)
#   rewards            (static_modifier_key, variant) — the permanent empire modifier(s)
#   building           (building_key, scope) or None
#   homeworld_event    event id whose option grants the one-time "Homeworld reward"
#                      (resource bursts + minor artifacts) on discovering the home
#                      world — parsed into precursor_homeworld_reward. None where the
#                      capstone grants only the relic (Baol, adAkkaria).
#   technology         (tech_key, building_key, scope) — the "Secrets of …" tech
#                      (granted by the discovery special project) and the empire-wide
#                      building it unlocks. None for the Cosmic Storms precursors,
#                      which have no such tech.
PRECURSORS: list[dict] = [
    {'key': 'precursor_1', 'dlc': None, 'system': 'vultaumar_system', 'num_digsites': None,
     'relic': 'r_reality_perforator',
     'rewards': [('artifact_vultaum_research_completed_suppressed', 'suppressed'),
                 ('artifact_vultaum_research_completed_public', 'public'),
                 ('artifact_vultaum_research_completed_machine', 'machine'),
                 ('artifact_vultaum_research_completed_hive', 'hive')],
     'building': None, 'homeworld_event': 'precursor.101',
     'technology': ('tech_secrets_vultaum', 'building_vultaum_reality_computer', 'planet')},
    {'key': 'precursor_2', 'dlc': None, 'system': 'yuhtaan_system', 'num_digsites': None,
     'relic': 'r_cryo_core',
     'rewards': [],  # Yuht grant no permanent empire modifier; their homeworld gets pm_yuht_cleansing
     'building': None, 'homeworld_event': 'precursor.601',
     'technology': ('tech_secrets_yuht', 'yuht_detection_array', 'starbase')},
    {'key': 'precursor_3', 'dlc': None, 'system': 'fen_habbanis_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_league_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.1101',
     'technology': ('tech_secrets_league', 'building_league_offices', 'planet')},
    {'key': 'precursor_4', 'dlc': None, 'system': 'irass_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_irassian_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.1601',
     'technology': ('tech_secrets_irassian', 'irassian_naval_yards', 'starbase')},
    {'key': 'precursor_5', 'dlc': None, 'system': 'cybrex_system', 'num_digsites': None,
     'relic': 'r_war_forge',
     'rewards': [('artifact_cybrex_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.2101',
     'technology': ('tech_secrets_cybrex', 'cybrex_mining_hub', 'starbase')},
    {'key': 'precursor_baol_1', 'dlc': 'Ancient Relics Story Pack', 'system': 'last_baol_system',
     'num_digsites': 4,
     'relic': 'r_the_last_baol',
     'rewards': [('artifact_baol_research_completed', 'default')],
     'building': None, 'homeworld_event': None,  # capstone (ancrel.2022/2023) grants only the relic
     'technology': ('tech_secrets_baol', 'building_baol_organic_plant', 'planet')},
    {'key': 'precursor_zroni_1', 'dlc': 'Ancient Relics Story Pack',
     'system': 'zroni_legacy_initializer_01', 'num_digsites': 6,
     'relic': 'r_zro_crystal',
     'rewards': [('artifact_zroni_research_completed', 'default')],
     'building': ('building_zroni_equilibrator', 'planet'), 'homeworld_event': 'ancrel.19',
     'technology': ('tech_secrets_zroni', 'zroni_storm_caster', 'starbase')},
    {'key': 'precursor_inetian', 'dlc': 'Cosmic Storms', 'system': None, 'num_digsites': None,
     'relic': 'r_weather_manipulator',
     'rewards': [('secrets_of_the_inetian_traders', 'default'),
                 ('secrets_of_the_inetian_traders_hive_mach', 'hive_machine')],
     'building': None, 'homeworld_event': 'cstorms.193', 'technology': None},
    {'key': 'precursor_adakkaria', 'dlc': 'Cosmic Storms', 'system': 'adSul_system',
     'num_digsites': None,
     'relic': 'r_the_tempest_invocator',
     'rewards': [('patriotic_institute_true_potential_unleashed', 'default')],
     'building': ('building_adakkaria_patriotic_institute', 'planet'),
     'homeworld_event': None,  # capstone (cstorms.2355) grants only the relic
     'technology': None},
]

# Keys inside a relic's triggered_country_modifier / static modifier that are not
# numeric empire modifiers.
_NON_MODIFIER_KEYS = frozenset({
    'potential', 'modifier', 'custom_tooltip', 'show_only_custom_tooltip',
    'icon', 'icon_frame',
})

# Generic relic active-effect tooltips shared by all relics (the cooldown note);
# excluded from the stored per-relic active_effect text.
_GENERIC_RELIC_TOOLTIPS = frozenset({
    'relic_triumph_cooldown', 'relic_triumph_cooldown_veryshort',
})


def _relic_condition(potential) -> str | None:
    """Render a relic passive's 'potential' block into a parseable condition string.

    Top-level entries are ANDed (comma-separated); an OR sub-block is rendered
    parenthesised as "(a = x OR b = y)" so consumers can evaluate it (e.g. against
    an empire type). None when the passive always applies (no potential / always = yes)."""
    if not isinstance(potential, dict):
        return None
    parts = []
    for key, val in potential.items():
        if key == 'always':
            continue
        if isinstance(val, str):
            parts.append(f"{key} = {val}")
        elif key == 'OR' and isinstance(val, dict):
            ors = [f"{k} = {v}" for k, v in val.items() if isinstance(v, str)]
            if ors:
                parts.append("(" + " OR ".join(ors) + ")")
        else:
            # Other nested operators: keep the operator name as a marker.
            parts.append(key)
    return ", ".join(parts) if parts else None


def _relic_passive_rows(relic: dict) -> list[tuple[str, float, str | None]]:
    """Extract (modifier_key, value, condition) from a relic's triggered_country_modifier blocks.

    Handles both forms: a nested `modifier = { ... }` and modifiers written
    directly as keys of the triggered_country_modifier block."""
    blocks = relic.get('triggered_country_modifier')
    if blocks is None:
        return []
    if isinstance(blocks, dict):
        blocks = [blocks]
    rows: list[tuple[str, float, str | None]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        condition = _relic_condition(block.get('potential'))
        # Form A: nested modifier = { ... }
        nested = block.get('modifier')
        sources = [nested] if isinstance(nested, dict) else []
        # Form B: modifiers written directly as keys of the block
        direct = {k: v for k, v in block.items() if k not in _NON_MODIFIER_KEYS}
        if direct:
            sources.append(direct)
        for src in sources:
            for mod_key, mod_val in src.items():
                value = _resolve_modifier_float(mod_val)
                if value is None:
                    continue
                rows.append((mod_key, value, condition))
    return rows


def _relic_active_text(relic: dict, loc: dict[str, str]) -> str | None:
    """Join the non-generic custom_tooltip strings of a relic's active_effect.

    Relics whose active is a pure resource transaction (e.g. the Cybrex War Forge)
    carry no descriptive tooltip — their effect is captured structurally by
    _relic_resources('produces') instead."""
    active = relic.get('active_effect')
    tips = active.get('custom_tooltip') if isinstance(active, dict) else None
    if isinstance(tips, str):
        tips = [tips]
    elif not isinstance(tips, list):
        tips = []
    texts = []
    for key in tips:
        if key in _GENERIC_RELIC_TOOLTIPS:
            continue
        raw = loc.get(key)
        if raw:
            cleaned = _clean_loc(raw, loc)
            if cleaned and '$' not in cleaned:
                texts.append(cleaned)
    return " ".join(texts) if texts else None


def _relic_passive_texts(relic: dict, loc: dict[str, str]) -> list[tuple[str, str | None]]:
    """Non-numeric passive effects (a relic's triggered_country_modifier
    custom_tooltips) as [(text, condition)]. Each block's `potential` becomes the
    condition (NULL = always), so a conditional passive (e.g. the Cryo Core's
    is_wilderness_empire = yes district) is shown with its qualifier, like the
    numeric passives — not as if it always applied."""
    blocks = relic.get('triggered_country_modifier')
    if isinstance(blocks, dict):
        blocks = [blocks]
    elif not isinstance(blocks, list):
        return []
    rows: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        condition = _relic_condition(block.get('potential'))
        tips = block.get('custom_tooltip')
        if isinstance(tips, str):
            tips = [tips]
        elif not isinstance(tips, list):
            tips = []
        for key in tips:
            raw = loc.get(key)
            if not raw:
                continue
            cleaned = _clean_loc(raw, loc)
            if cleaned and '$' not in cleaned and cleaned not in seen:
                seen.add(cleaned)
                rows.append((cleaned, condition))
    return rows


def _relic_resources(relic: dict, variables: dict[str, float]) -> list[tuple[str, str, float, int]]:
    """Resources of the triumph effect → [(kind, resource, amount, scales)].

    'cost' from `resources.cost` (resources spent to activate), 'produces' from the
    direct `add_resource` children of `active_effect` (resources gained). A block
    with a `multiplier`/`mult` scales with use (e.g. Cybrex `cybrex_count`: 1000
    alloys at first use, +1000 each use). Note the produced amount is read from the
    script (1000), not the loc tooltip — which can be stale/wrong."""
    rows: list[tuple[str, str, float, int]] = []

    def add_blocks(blocks, kind: str):
        if isinstance(blocks, dict):
            blocks = [blocks]
        elif not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            scales = 1 if ('multiplier' in block or 'mult' in block) else 0
            for res, raw in block.items():
                if res in ('multiplier', 'mult', 'category'):
                    continue
                s = str(raw).strip()
                amount = variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)
                if amount is not None:
                    rows.append((kind, res, amount, scales))

    resources = relic.get('resources')
    if isinstance(resources, dict):
        add_blocks(resources.get('cost'), 'cost')
    active = relic.get('active_effect')
    if isinstance(active, dict):
        # Only direct add_resource children are "produces" — nested ones (e.g. the
        # tempest invocator's on_cancel refund) are not part of the triumph payout.
        add_blocks(active.get('add_resource'), 'produces')
    return rows


def _relic_cooldown(relic: dict, variables: dict[str, float]) -> int | None:
    """The triumph cooldown in days. Read from the `relic_activation_cooldown`
    add_modifier inside active_effect (the value the game actually applies — e.g.
    the War Forge's short cooldown), falling back to `activation_duration`."""
    def find_days(node) -> int | None:
        if isinstance(node, list):
            for item in node:
                d = find_days(item)
                if d is not None:
                    return d
        elif isinstance(node, dict):
            if node.get('modifier') == 'relic_activation_cooldown' and 'days' in node:
                raw = str(node['days']).strip()
                val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
                return int(val) if val is not None else None
            for v in node.values():
                d = find_days(v)
                if d is not None:
                    return d
        return None

    days = find_days(relic.get('active_effect'))
    if days is not None:
        return days
    raw = str(relic.get('activation_duration', '')).strip()
    val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
    return int(val) if val is not None else None


def _relic_event_id(active_effect) -> str | None:
    """The country_event id a triumph's active_effect fires (e.g. precursor.105)."""
    for ce in _as_blocks(_find_first(active_effect, 'country_event')):
        if isinstance(ce.get('id'), str):
            return ce['id']
    return None


def _entry_years(m: dict, variables: dict[str, float]) -> int | None:
    """Duration of an add_modifier block in years (resolving @vars; days/360, months/12)."""
    for unit_key, divisor in (('years', 1), ('months', 12), ('days', 360)):
        if unit_key in m:
            raw = str(m[unit_key]).strip()
            val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
            if val is not None:
                return max(1, round(val / divisor))
    return None


def _resolve_static_modifier(name: str, static_mods: dict,
                             variables: dict[str, float] | None = None) -> list[tuple[str, float]]:
    """A static modifier name → its numeric (modifier_key, value) effects; an `@variable` value
    resolved from `variables` (common/scripted_variables: the leaders' `skill_*` governor effects
    are `@planet_job_mult` …), else left out."""
    block = static_mods.get(name)
    if isinstance(block, list):
        merged: dict = {}
        for b in block:
            if isinstance(b, dict):
                merged.update(b)
        block = merged
    if not isinstance(block, dict):
        return []
    # The game hides the numeric effects of a show_only_custom_tooltip modifier
    # (e.g. the Weather Manipulator's storm_relic_repelling_field_modifier — its
    # effect is conveyed by the active_effect's custom_tooltip text instead).
    if block.get('show_only_custom_tooltip') == 'yes':
        return []
    out: list[tuple[str, float]] = []
    for mod_key, mod_val in block.items():
        if mod_key in _NON_MODIFIER_KEYS:
            continue
        value = _resolve_modifier_float(mod_val)
        if value is None and variables:
            value = _resolve_var(mod_val, variables)
        if value is not None:
            out.append((mod_key, value))
    return out


def extract_static_modifiers(static_mods: dict, loc: dict[str, str], w: Writer,
                             verbose: bool, scripted_vars: dict[str, float] | None = None) -> None:
    """Every static modifier (common/static_modifiers) with its name, description and numeric
    effects — for the empire dashboard, which lists those a save puts on the empire
    (`country.timed_modifier`). A `show_only_custom_tooltip` one keeps no effects (the game hides
    them) but its tooltip text."""
    for key in sorted(k for k, v in static_mods.items() if not k.startswith('@') and isinstance(v, (dict, list))):
        block = static_mods[key]
        if isinstance(block, list):
            block = next((b for b in reversed(block) if isinstance(b, dict)), {})
        name = _clean_loc(loc[key], loc) if key in loc else None
        desc = _clean_loc(loc[key + '_desc'], loc) if key + '_desc' in loc else None
        tip_key = block.get('custom_tooltip')
        tooltip = _clean_loc(loc[tip_key], loc) if isinstance(tip_key, str) and tip_key in loc else None
        w.execute("INSERT OR REPLACE INTO static_modifier (key, name, description, tooltip) VALUES (?, ?, ?, ?)",
                  (key, name, desc, tooltip))
        for order, (mod_key, value) in enumerate(_resolve_static_modifier(key, static_mods, scripted_vars)):
            w.execute("""INSERT OR REPLACE INTO static_modifier_modifier
                         (static_key, modifier_key, modifier_value, sort_order) VALUES (?, ?, ?, ?)""",
                      (key, mod_key, value, order))
            _write_modifier_label(w, loc, mod_key)
        if verbose:
            print(f"  static_modifier: {key} = {name}")
    w.commit()


def _collect_add_modifiers(node, variables: dict[str, float]) -> list[tuple[str, int | None]]:
    """Every `add_modifier` (modifier + duration) anywhere in a tree, except the
    `relic_activation_cooldown`. Recursive so it catches modifiers nested in e.g.
    `start_storm_area_placing > on_confirm` (the Tempest Invocator)."""
    out: list[tuple[str, int | None]] = []

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
        elif isinstance(n, dict):
            for am in _as_blocks(n.get('add_modifier')):
                name = am.get('modifier')
                if isinstance(name, str) and name != 'relic_activation_cooldown':
                    out.append((name, _entry_years(am, variables)))
            for k, v in n.items():
                if k != 'add_modifier':
                    walk(v)

    walk(node)
    return out


def _relic_active_outcomes(relic: dict, events_index: dict, static_mods: dict,
                           variables: dict[str, float], loc: dict[str, str]
                           ) -> tuple[list[tuple[str, float, str | None]], str | None, str | None]:
    """The temporary modifiers a triumph grants → ([(modifier_key, value, group_label)],
    detail_text, mode). Three sources / modes:
    - **'direct'**: an `add_modifier` in active_effect (Yuht Cryo Core: −20% ship
      upkeep for @triumph_duration = 10 years);
    - **'random'**: a `country_event`'s `(locked_)random_list` (Reality Perforator →
      precursor.105 → one of reality_perforator_1..4 at random, or all as a jackpot);
    - **'choice'**: a `country_event`'s `option`s, each `add_modifier`ing a named
      guidance (Zro Crystal → ancrel.7006 → Martial / Statecraft Guidance for 5 years;
      `group_label` is the option's modifier loc name). Non-modifier options (e.g. a
      ruler level) are appended to the detail."""
    active = relic.get('active_effect')
    active_mods: list[tuple[str, float, str | None]] = []
    single_years: list[int] = []
    grouped_years: list[int] = []
    text_options: list[str] = []
    mode: str | None = None

    # Source A: an add_modifier in active_effect (recursive — the Tempest Invocator's
    # is nested in start_storm_area_placing > on_confirm), excluding the cooldown.
    for name, years in _collect_add_modifiers(active, variables):
        mode = 'direct'
        if years is not None:
            single_years.append(years)
        for mk, val in _resolve_static_modifier(name, static_mods):
            active_mods.append((mk, val, None))

    event = events_index.get(_relic_event_id(active)) if not active_mods else None

    # Source B: an event's (locked_)random_list.
    if not active_mods:
        rlist = _find_first(event, 'locked_random_list') or _find_first(event, 'random_list')
        if isinstance(rlist, dict):
            mode = 'random'
            ordered: list[str] = []
            for entries in rlist.values():          # keys are weights; values are entry blocks
                for entry in _as_blocks(entries):
                    mods = [m for m in _as_blocks(entry.get('add_modifier'))
                            if isinstance(m.get('modifier'), str)]
                    if not mods:
                        continue
                    for m in mods:
                        if m['modifier'] not in ordered:
                            ordered.append(m['modifier'])
                    years = _entry_years(mods[0], variables)
                    if years is not None:
                        (single_years if len(mods) == 1 else grouped_years).append(years)
            for name in ordered:
                for mk, val in _resolve_static_modifier(name, static_mods):
                    active_mods.append((mk, val, None))

    # Source C: an event's options (a choice). Each option add_modifiers a guidance.
    if not active_mods and isinstance(event, dict):
        for opt in _as_blocks(event.get('option')):
            opt_mods = [m for m in _as_blocks(opt.get('add_modifier'))
                        if isinstance(m.get('modifier'), str)]
            if opt_mods:
                mode = 'choice'
                for am in opt_mods:
                    name = am['modifier']
                    years = _entry_years(am, variables)
                    if years is not None:
                        single_years.append(years)
                    glabel = _clean_loc(loc[name], loc) if name in loc else None
                    for mk, val in _resolve_static_modifier(name, static_mods):
                        active_mods.append((mk, val, glabel))
            else:
                tip = opt.get('custom_tooltip')
                if isinstance(tip, str) and tip not in _GENERIC_RELIC_TOOLTIPS and tip in loc:
                    t = _clean_loc(loc[tip], loc)
                    if t and '$' not in t and t not in text_options:
                        text_options.append(t)

    if not active_mods:
        return [], None, None

    # Build the "how it's granted" detail.
    def years_word(n: int) -> str:
        return f"{n} year" if n == 1 else f"{n} years"

    dur = None
    if single_years:
        lo, hi = min(single_years), max(single_years)
        dur = f"{lo}–{hi} years" if lo != hi else years_word(lo)

    parts: list[str] = []
    if mode == 'choice':
        parts.append(f"choose one, for {dur}" if dur else "choose one")
        if text_options:
            parts.append("or " + "; ".join(t.rstrip('. ') for t in text_options))
    elif mode == 'random':
        if dur:
            parts.append(f"one at random for {dur}" if len(active_mods) > 1 else f"for {dur}")
        if grouped_years:
            parts.append(f"or all of them for {years_word(min(grouped_years))}")
    elif dur:                                          # direct
        parts.append(f"for {dur}")
    detail = ", ".join(parts)
    detail = detail[0].upper() + detail[1:] + "." if detail else None
    return active_mods, detail, mode


# Flat minor_artifacts payout bounds (min..max) for the artifact-reward scripted
# effects (common/scripted_effects/archaeology_event_effects.txt). Each is a 1/3
# random over three amounts; we keep the bounds for display.
_ARTIFACT_REWARD_BOUNDS = {
    'small_artifact_reward':  (50.0, 250.0),
    'medium_artifact_reward': (300.0, 500.0),
    'large_artifact_reward':  (750.0, 1500.0),
}


def _homeworld_reward_rows(event: dict, variables: dict[str, float]
                           ) -> list[tuple[str, float | None, float, float]]:
    """Parse a precursor's home-world discovery event into reward rows
    [(resource, multiplier, min_amount, max_amount)].

    Two shapes: `add_monthly_resource_mult` blocks (a `value` multiplier on the
    empire's current monthly output of `resource`, clamped to [min, max]) and the
    flat `*_artifact_reward = yes` minor-artifacts payout (resource =
    'minor_artifacts', multiplier = None). The reward repeats identically across
    the event's options (one per end state), so we dedupe by resource — first
    occurrence wins — and resolve @tier* scripted variables via `variables`."""
    rows: list[tuple[str, float | None, float, float]] = []
    seen: set[str] = set()

    def num(raw):
        s = str(raw).strip()
        return variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for block in _as_blocks(node.get('add_monthly_resource_mult')):
            res = block.get('resource')
            if not isinstance(res, str) or res in seen:
                continue
            lo, hi = num(block.get('min')), num(block.get('max'))
            if lo is None or hi is None:
                continue
            seen.add(res)
            rows.append((res, num(block.get('value')), lo, hi))
        for art_key, (lo, hi) in _ARTIFACT_REWARD_BOUNDS.items():
            if str(node.get(art_key)).strip() == 'yes' and 'minor_artifacts' not in seen:
                seen.add('minor_artifacts')
                rows.append(('minor_artifacts', None, lo, hi))
        for v in node.values():
            walk(v)

    walk(event)
    return rows


def extract_precursors(precursor_civ: dict, relics: dict, static_mods: dict,
                       events_index: dict, buildings: dict, technologies: dict,
                       inline_scripts_dir, scripted_vars: dict[str, float],
                       loc: dict[str, str], w: Writer, verbose: bool):
    # @variables for resolving relic costs/cooldowns: this file's own top-level
    # @defs plus the global scripted_variables (where @activation_cost etc. live).
    relic_vars: dict[str, float] = dict(scripted_vars)
    for vk, vv in relics.items():
        if vk.startswith('@'):
            f = _resolve_modifier_float(vv)
            if f is not None:
                relic_vars[vk] = f

    for order, p in enumerate(PRECURSORS):
        key = p['key']
        civ = precursor_civ.get(key, {})
        picture = civ.get('picture').strip('"') if isinstance(civ.get('picture'), str) else None
        name = loc.get(key)

        w.execute(
            """INSERT OR REPLACE INTO precursor
                   (key, name, required_dlc, system_initializer, picture, num_digsites, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, name, p['dlc'], p['system'], picture, p['num_digsites'], order),
        )
        if verbose:
            print(f"  Precursor: {key} | {name} | dlc: {p['dlc']} | system: {p['system']}")

        # --- relic (0..1) ---
        relic_key = p['relic']
        if relic_key:
            relic = relics.get(relic_key)
            if isinstance(relic, list):
                relic = next((b for b in reversed(relic) if isinstance(b, dict)), None)
            if not isinstance(relic, dict):
                print(f"  WARNING: relic {relic_key} not found for {key}", file=sys.stderr)
            else:
                score = None
                try:
                    score = int(relic.get('score')) if relic.get('score') is not None else None
                except (ValueError, TypeError):
                    pass
                portrait = relic.get('portrait')
                portrait = portrait.strip('"') if isinstance(portrait, str) else None
                active_mods, active_detail, active_mode = _relic_active_outcomes(
                    relic, events_index, static_mods, relic_vars, loc)
                w.execute(
                    """INSERT OR REPLACE INTO precursor_relic
                           (precursor_key, relic_key, name, portrait, score, description,
                            active_effect, active_mode, active_detail, triumph_cooldown_days)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, relic_key, loc.get(relic_key), portrait, score,
                     _clean_loc(loc[relic_key + '_desc'], loc) if (relic_key + '_desc') in loc else None,
                     _relic_active_text(relic, loc),
                     active_mode,
                     active_detail,
                     _relic_cooldown(relic, relic_vars)),
                )
                for t_order, (text, condition) in enumerate(_relic_passive_texts(relic, loc)):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_passive_text
                               (relic_key, text, condition, sort_order)
                           VALUES (?, ?, ?, ?)""",
                        (relic_key, text, condition, t_order),
                    )
                for a_order, (mod_key, value, group_label) in enumerate(active_mods):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_active_modifier
                               (relic_key, modifier_key, modifier_value, group_label, sort_order)
                           VALUES (?, ?, ?, ?, ?)""",
                        (relic_key, mod_key, value, group_label, a_order),
                    )
                    _write_modifier_label(w, loc, mod_key)
                    if verbose:
                        print(f"    relic {relic_key} active | {mod_key} = {value}"
                              f"{' [' + group_label + ']' if group_label else ''}")
                for c_order, (kind, res, amount, scales) in enumerate(_relic_resources(relic, relic_vars)):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_resource
                               (relic_key, kind, resource, amount, scales, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (relic_key, kind, res, amount, scales, c_order),
                    )
                    if verbose:
                        print(f"    relic {relic_key} {kind} | {res} = {amount}"
                              f"{' (scales)' if scales else ''}")
                for r_order, (mod_key, value, condition) in enumerate(_relic_passive_rows(relic)):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_modifier
                               (relic_key, modifier_key, modifier_value, condition, sort_order)
                           VALUES (?, ?, ?, ?, ?)""",
                        (relic_key, mod_key, value, condition, r_order),
                    )
                    _write_modifier_label(w, loc, mod_key)
                    if verbose:
                        print(f"    relic {relic_key} | {mod_key} = {value}"
                              f"{' [' + condition + ']' if condition else ''}")

        # --- permanent empire reward modifier(s) ---
        for static_key, variant in p['rewards']:
            mod_block = static_mods.get(static_key)
            if mod_block is None:
                print(f"  WARNING: static modifier {static_key} not found for {key}", file=sys.stderr)
                continue
            # Duplicate definitions across files are promoted to a list by the
            # parser; the later definition wins (game load order).
            if isinstance(mod_block, list):
                merged: dict = {}
                for b in mod_block:
                    if isinstance(b, dict):
                        merged.update(b)
                mod_block = merged
            if not isinstance(mod_block, dict):
                continue
            rew_name = loc.get(static_key)
            rew_name = _clean_loc(rew_name, loc) if rew_name else None
            for m_order, (mod_key, mod_val) in enumerate(mod_block.items()):
                if mod_key in _NON_MODIFIER_KEYS:
                    continue
                value = _resolve_modifier_float(mod_val)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO precursor_reward_modifier
                           (precursor_key, static_modifier, variant, name,
                            modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (key, static_key, variant, rew_name, mod_key, value, m_order),
                )
                _write_modifier_label(w, loc, mod_key)
                if verbose:
                    print(f"    reward {static_key} ({variant}) | {mod_key} = {value}")

        # --- one-time home-world discovery reward (resource bursts + artifacts) ---
        hw_event = events_index.get(p['homeworld_event']) if p.get('homeworld_event') else None
        if hw_event:
            for hw_order, (res, mult, lo, hi) in enumerate(
                    _homeworld_reward_rows(hw_event, relic_vars)):
                w.execute(
                    """INSERT OR IGNORE INTO precursor_homeworld_reward
                           (precursor_key, resource, multiplier, min_amount, max_amount, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, res, mult, lo, hi, hw_order),
                )
                if verbose:
                    amt = f"x{mult:g}" if mult is not None else "flat"
                    print(f"    homeworld reward | {res} {amt} ({lo:g}~{hi:g})")

        # --- homeworld building (0..1): granted on the precursor's home planet ---
        if p['building']:
            building_key, scope = p['building']
            _write_precursor_building(w, key, building_key, scope, 'homeworld', None,
                                      buildings, scripted_vars, inline_scripts_dir, loc, verbose)

        # --- "Secrets of …" technology + the empire-wide building it unlocks ---
        if p.get('technology'):
            tech_key, tb_key, tb_scope = p['technology']
            tdef = technologies.get(tech_key)
            if isinstance(tdef, list):
                tdef = next((b for b in reversed(tdef) if isinstance(b, dict)), None)
            tdef = tdef if isinstance(tdef, dict) else {}
            cat = tdef.get('category')
            cat = cat[0] if isinstance(cat, list) else cat
            cost = _resolve_var(tdef.get('cost'), scripted_vars)
            tier = _resolve_var(tdef.get('tier'), scripted_vars)
            w.execute(
                """INSERT OR REPLACE INTO precursor_technology
                       (precursor_key, tech_key, name, cost, tier, area, category,
                        unlocks_building_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, tech_key, loc.get(tech_key),
                 int(cost) if cost is not None else None,
                 int(tier) if tier is not None else None,
                 tdef.get('area'), cat, tb_key),
            )
            if verbose:
                print(f"    technology {tech_key} | {loc.get(tech_key)} → {tb_key}")
            _write_precursor_building(w, key, tb_key, tb_scope, 'technology', tech_key,
                                      buildings, scripted_vars, inline_scripts_dir, loc, verbose)

    w.commit()


def _write_precursor_building(w: Writer, precursor_key: str, building_key: str, scope: str,
                              source: str, tech_key: str | None, buildings: dict,
                              scripted_vars: dict[str, float], inline_scripts_dir,
                              loc: dict[str, str], verbose: bool):
    """Write a precursor building (+ its resources/modifiers/jobs). `source` is
    'homeworld' (home-planet unique) or 'technology' (unlocked by `tech_key`)."""
    bdef = buildings.get(building_key)
    if isinstance(bdef, list):
        bdef = next((b for b in reversed(bdef) if isinstance(b, dict)), None)
    bdef = bdef if isinstance(bdef, dict) else {}

    can_dem = bdef.get('can_demolish')
    can_dem = (1 if str(can_dem).strip() == 'yes' else 0) if can_dem is not None else None
    build_time = _resolve_var(bdef.get('base_buildtime'), scripted_vars)
    # A per-empire cap is `base_cap_amount = N` or `empire_limit = { base = N … }`;
    # a per-planet cap is `planet_limit = N`. `_building_cap` handles both forms.
    empire_limit = _building_cap(bdef.get('base_cap_amount'), scripted_vars)
    if empire_limit is None:
        empire_limit = _building_cap(bdef.get('empire_limit'), scripted_vars)
    planet_limit = _building_cap(bdef.get('planet_limit'), scripted_vars)
    # The building name often loc-references its tech (e.g. "$tech_secrets_baol$").
    # Orbital starbase buildings carry their name under the `sm_` (starbase module)
    # loc key rather than the bare building key.
    raw_name = loc.get(building_key) or loc.get('sm_' + building_key)
    name = _clean_loc(raw_name, loc) if raw_name else None
    w.execute(
        """INSERT OR REPLACE INTO precursor_building
               (precursor_key, building_key, name, scope, source, tech_key, category,
                build_time_days, empire_limit, planet_limit, can_demolish)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (precursor_key, building_key, name, scope, source, tech_key, bdef.get('category'),
         int(build_time) if build_time is not None else None,
         int(empire_limit) if empire_limit is not None else None,
         int(planet_limit) if planet_limit is not None else None,
         can_dem),
    )
    if verbose:
        print(f"    building {building_key} ({scope}, {source}) | {name}")

    for r_order, (rkind, rres, ramt) in enumerate(_building_resources(bdef, scripted_vars)):
        w.execute(
            """INSERT OR IGNORE INTO precursor_building_resource
                   (building_key, kind, resource, amount, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (building_key, rkind, rres, ramt, r_order),
        )
        if verbose:
            print(f"      {rkind} | {ramt:g} {rres}")

    building_mods = (
        _building_modifiers(bdef, scripted_vars)
        + [('job', jk, jv, None, None)
           for jk, jv in _building_inline_jobs(bdef, scripted_vars, inline_scripts_dir)]
    )
    for m_order, (mscope, mkey, mval, mae, mcond) in enumerate(building_mods):
        w.execute(
            """INSERT OR IGNORE INTO precursor_building_modifier
                   (building_key, scope, modifier_key, modifier_value, ae_value,
                    condition, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (building_key, mscope, mkey, mval, mae, mcond, m_order),
        )
        _write_modifier_label(w, loc, mkey)
        if verbose:
            extra = (f" → {mae:g} w/AE" if mae is not None else "") + \
                    (f" [{mcond}]" if mcond else "")
            print(f"      {mscope} modifier | {mkey} = {mval:g}{extra}")


# ---------------------------------------------------------------------------
# Extraction: deposits (dictionary for home-system planet deposits)
# ---------------------------------------------------------------------------

def _default_overclocks(megastructures: dict, overclock_types: dict,
                        variables: dict[str, float]) -> set[str]:
    """Each megastructure's starting overclock mode: the highest flat `ai_weight` (a plain
    number or a block's `base`; 100 when absent), ties to the first listed — the rule in
    megastructure_overclock_types/99_README_OVERCLOCKS.txt."""
    def weight(mode: str) -> float:
        aw = (overclock_types.get(mode) or {}).get('ai_weight')
        if isinstance(aw, dict):
            aw = aw.get('base')
        v = _resolve_var(aw, variables)
        return 100.0 if v is None else v

    out: set[str] = set()
    for m in megastructures.values():
        modes = _to_list(m.get('overclock_types')) if isinstance(m, dict) else []
        if modes:
            out.add(max(modes, key=weight))   # max keeps the first of equal weights
    return out


def _primary_resource_block(blocks: list, signals, default_overclocks: set[str]) -> dict | None:
    """The one block of a deposit's produces/cost/upkeep to show: a lone block as is, else
    the unconditional ones merged (Crystal Rift: unity + physics), else the one gated on a
    megastructure's starting overclock mode (Arc Furnace: balanced smelting). Deposits
    whose blocks are all otherwise gated (nanites, living metal, …) give None. `signals`
    maps a trigger to its (positive, negative) condition tokens."""
    dicts = [b for b in blocks if isinstance(b, dict)]
    if len(dicts) == 1:
        return dicts[0]
    plain = [b for b in dicts if 'trigger' not in b]
    if plain:
        return {k: v for b in plain for k, v in b.items()}
    want = {'has_overclock=' + m for m in default_overclocks}
    return next((b for b in dicts if signals(b['trigger'])[0] & want), None)


def extract_deposits(data: dict, source_file: str, loc: dict[str, str],
                     variables: dict[str, float], signals, default_overclocks: set[str],
                     w: Writer, verbose: bool):
    variables = {**variables, **{k: v for k, v in
                                 ((k, _resolve_modifier_float(v)) for k, v in data.items()
                                  if k.startswith('@')) if v is not None}}
    for key, d in data.items():
        if key.startswith('@') or not isinstance(d, dict) or not key.startswith('d_'):
            continue

        resources = d.get('resources')
        if not isinstance(resources, dict):
            resources = {}
        # Category can be top-level (blockers) or inside the resources block.
        category = d.get('category') or resources.get('category')
        is_blocker = 1 if (isinstance(category, str) and 'blocker' in category) else 0
        clear_time = None
        try:
            clear_time = int(d.get('time')) if d.get('time') is not None else None
        except (ValueError, TypeError):
            pass

        # technologies a blocker needs before it can be cleared (12 blockers in 4.5.2)
        prerequisites = ','.join(str(t).strip('"') for t in _to_list(d.get('prerequisites'))) or None

        w.execute(
            """INSERT OR REPLACE INTO deposit (key, name, category, is_blocker, clear_time,
               prerequisites, source_file) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, _clean_loc(loc[key], loc) if key in loc else None,
             category, is_blocker, clear_time, prerequisites, source_file),
        )

        for kind in ('produces', 'cost', 'upkeep'):
            block = _primary_resource_block(_as_blocks(resources.get(kind)), signals,
                                            default_overclocks)
            if block is None:
                continue
            for res, amt in block.items():
                value = _resolve_var(amt, variables)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO deposit_resource (deposit_key, kind, resource, amount)
                       VALUES (?, ?, ?, ?)""",
                    (key, kind, res, value),
                )

        # Blocker/feature planet modifiers, and what an orbital deposit gives a habitat.
        for mod_block_key, table in (('planet_modifier', 'deposit_modifier'),
                                     ('modifier', 'deposit_modifier'),
                                     ('habitat_modifier', 'deposit_habitat_modifier')):
            pm = d.get(mod_block_key)
            if not isinstance(pm, dict):
                continue
            for mod_key, mod_val in pm.items():
                # values may be scripted variables (`@DISTRICTS_FROM_SR_DEPOSITS` = 3: swamps,
                # bogs and other strategic-resource features); a repeated key keeps the last
                value = _resolve_var(mod_val[-1] if isinstance(mod_val, list) else mod_val, variables)
                if value is None:
                    continue
                w.execute(
                    f"""INSERT OR REPLACE INTO {table} (deposit_key, modifier_key, modifier_value)
                       VALUES (?, ?, ?)""",
                    (key, mod_key, value),
                )
                _write_modifier_label(w, loc, mod_key)

        if verbose:
            print(f"  Deposit: {key} | blocker: {is_blocker} | {category}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: precursor home systems
# ---------------------------------------------------------------------------

# precursor key -> home system initializer(s). Some precursors span several
# systems (Zroni's three legacy worlds). Verified against the initializer files
# and the spawn_system calls in the precursor events.
# NOTE: the Cybrex home is `cybrex_system` (Cybrex Alpha) — a ruined debris field.
# The ruined ringworld `cybrex_beta` is NOT a precursor reward: it spawns during
# the Contingency end-game crisis (events/crisis_events_3.txt), so it's excluded.
PRECURSOR_SYSTEMS: dict[str, list[str]] = {
    'precursor_1':       ['vultaumar_system'],
    'precursor_2':       ['yuhtaan_system'],
    'precursor_3':       ['fen_habbanis_system'],
    'precursor_4':       ['irass_system'],
    'precursor_5':       ['cybrex_system'],
    'precursor_baol_1':  ['last_baol_system'],
    'precursor_zroni_1': ['zroni_legacy_initializer_01', 'zroni_legacy_initializer_02',
                          'zroni_legacy_initializer_03'],
    'precursor_inetian': ['weathermanipulators_home_system'],
    'precursor_adakkaria': ['adSalivul_system', 'adSul_system'],
}


def _loc_name(raw, loc: dict[str, str]) -> str | None:
    """Resolve a NAME_ loc key, falling back to the literal string."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip('"')
    return _clean_loc(loc.get(raw, raw), loc) or None


def _collect_init_effects(planet: dict) -> tuple[list[str], list[str], list[str]]:
    """Scan a planet's init_effect block(s) and the top-level `deposit` key.

    Returns (deposit_keys, modifier_static_keys, megastructure_types)."""
    deposits: list[str] = []
    modifiers: list[str] = []
    megastructures: list[str] = []

    for dep in _to_list(planet.get('deposit')):
        if isinstance(dep, str):
            deposits.append(dep)

    for init in _as_blocks(planet.get('init_effect')):
        for dk in ('add_deposit', 'set_deposit'):
            for dep in _to_list(init.get(dk)):
                if isinstance(dep, str):
                    deposits.append(dep)
        for m in _as_blocks(init.get('add_modifier')):
            if isinstance(m.get('modifier'), str):
                modifiers.append(m['modifier'])
        for ms in _as_blocks(init.get('spawn_megastructure')):
            if isinstance(ms.get('type'), str):
                megastructures.append(ms['type'].strip('"'))

    return deposits, modifiers, megastructures


def _planet_flags(planet: dict) -> list[str]:
    flags = planet.get('flags')
    if isinstance(flags, dict):
        return list(flags.keys())
    return _to_list(flags)


def extract_precursor_systems(initializers: dict, static_mods: dict,
                              loc: dict[str, str], w: Writer, verbose: bool):
    for p in PRECURSORS:
        pkey = p['key']
        for sys_order, init_key in enumerate(PRECURSOR_SYSTEMS.get(pkey, [])):
            sys_block = initializers.get(init_key)
            if isinstance(sys_block, list):
                sys_block = next((b for b in reversed(sys_block) if isinstance(b, dict)), None)
            if not isinstance(sys_block, dict):
                print(f"  WARNING: system {init_key} not found for {pkey}", file=sys.stderr)
                continue

            w.execute(
                """INSERT OR REPLACE INTO precursor_system
                       (precursor_key, initializer, name, star_class, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (pkey, init_key, _loc_name(sys_block.get('name'), loc),
                 sys_block.get('class'), sys_order),
            )
            if verbose:
                print(f"  System: {pkey} | {init_key} | {_loc_name(sys_block.get('name'), loc)}")

            # Megastructures for this system, counted by type. Most sit in the
            # system-level init_effect (the Yuht ruined hyper relay; the Cybrex
            # ruined ring world's four sections), occasionally on a planet.
            mega_counts: dict[str, int] = {}
            for init in _as_blocks(sys_block.get('init_effect')):
                for ms in _as_blocks(init.get('spawn_megastructure')):
                    ms_type = ms.get('type')
                    if isinstance(ms_type, str):
                        t = ms_type.strip('"')
                        mega_counts[t] = mega_counts.get(t, 0) + 1

            # Flatten planets and their moons into one ordered list.
            entries: list[tuple[dict, bool]] = []
            for planet in _as_blocks(sys_block.get('planet')):
                entries.append((planet, False))
                for moon in _as_blocks(planet.get('moon')):
                    entries.append((moon, True))

            for idx, (planet, is_moon) in enumerate(entries):
                planet_id = f"{init_key}#{idx}"
                pclass = planet.get('class')
                pclass = pclass.strip('"') if isinstance(pclass, str) else None
                size = None
                try:
                    size = int(planet.get('size')) if planet.get('size') is not None else None
                except (ValueError, TypeError):
                    pass
                flags = _planet_flags(planet)
                is_homeworld = 1 if any('homeworld' in f for f in flags) else 0

                w.execute(
                    """INSERT OR REPLACE INTO precursor_planet
                           (id, precursor_key, initializer, name, planet_class,
                            planet_class_label, size, is_moon, is_homeworld, flags, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (planet_id, pkey, init_key, _loc_name(planet.get('name'), loc), pclass,
                     _clean_loc(loc[pclass], loc) if (pclass and pclass in loc) else None,
                     size, 1 if is_moon else 0, is_homeworld,
                     ' '.join(flags) if flags else None, idx),
                )

                deposits, modifiers, megastructures = _collect_init_effects(planet)
                for d_order, dep in enumerate(deposits):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_planet_deposit
                               (planet_id, deposit_key, sort_order) VALUES (?, ?, ?)""",
                        (planet_id, dep, d_order),
                    )
                for static_key in modifiers:
                    mod_block = static_mods.get(static_key)
                    if isinstance(mod_block, list):
                        merged: dict = {}
                        for b in mod_block:
                            if isinstance(b, dict):
                                merged.update(b)
                        mod_block = merged
                    if not isinstance(mod_block, dict):
                        continue
                    for mod_key, mod_val in mod_block.items():
                        if mod_key in _NON_MODIFIER_KEYS:
                            continue
                        value = _resolve_modifier_float(mod_val)
                        if value is None:
                            continue
                        w.execute(
                            """INSERT OR REPLACE INTO precursor_planet_modifier
                                   (planet_id, static_modifier, modifier_key, modifier_value)
                               VALUES (?, ?, ?, ?)""",
                            (planet_id, static_key, mod_key, value),
                        )
                        _write_modifier_label(w, loc, mod_key)

                # Planet-level megastructures (rare) — counted at system level.
                for ms_type in megastructures:
                    mega_counts[ms_type] = mega_counts.get(ms_type, 0) + 1

            # Write the system's megastructures, one row per type with its count.
            for ms_type, ms_count in mega_counts.items():
                w.execute(
                    """INSERT OR REPLACE INTO precursor_megastructure
                           (precursor_key, initializer, type, type_label, count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pkey, init_key, ms_type,
                     _clean_loc(loc[ms_type], loc) if ms_type in loc else None, ms_count),
                )
                if verbose:
                    print(f"    megastructure: {ms_type} x{ms_count} ({loc.get(ms_type)})")

    w.commit()
