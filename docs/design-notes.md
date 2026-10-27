# stellaris-data-extraction — design notes

**Archive, not instruction. Nothing here is loaded into a session.** The full former CLAUDE.md, verbatim: schema, extraction steps, parser quirks and the determinism proof.

The rules an agent must have in context are in `../AGENTS.md`. Read this for *why*; put
new rules there and new evidence here.

---

# Stellaris Data Extraction — Claude Context

## Keeping This File Current

**After every session where extraction logic changes, a new table is added, a parser fix is made, or a new constant is extended, update the relevant sections of this file.** This file travels with the repo and is the authoritative hand-off for the next session.

---

## Goal

**Short-term:** Build a tool that generates valid species setups (including traits) for vanilla Stellaris.

**Long-term:** Generate full empires and output proper files usable directly in Stellaris (save files or mods).

The extraction work here (parsing game scripts → SQLite) is the foundation — it captures species classes, portraits, traits, ethics, and policy rules from vanilla game files so the planner/generator (`stellaris-empire-planner`) has an accurate source of truth.

---

## Code Conventions

- **All code in English** — variable names, comments, print output — even when conversing in German.
- Python 3.10+ (`str | None` union syntax, `match` statements if needed).

---

## CLI Behaviour

```bash
python3 stellaris-extract.py /path/to/Stellaris               # SQL → stdout, progress → stderr
python3 stellaris-extract.py /path/to/Stellaris --out a.sql   # SQL → file, progress → stdout
python3 stellaris-extract.py /path/to/Stellaris --sqlite a.db
python3 stellaris-extract.py /path/to/Stellaris --out a.sql --sqlite a.db
```

Game install: `~/.local/share/Steam/steamapps/common/Stellaris`

Regenerate the DB consumed by the planner:
```bash
python3 stellaris-extract.py ~/.local/share/Steam/steamapps/common/Stellaris --sqlite sqlite/stellaris.sqlite
```

---

## Project Layout

```
stellaris-extract.py     thin CLI entry point (implementation lives in extractor/)
extractor/
  __init__.py            __version__
  cli.py                 main(): arg parsing, pipeline order, summary box
  clausewitz.py          tokenize + Parser + parse_file
  localisation.py        loc loading (load_localisation) + _clean_loc text cleanup
  schema.py              SCHEMA (all CREATE/DROP TABLE statements)
  writer.py              Writer — SQL dump and/or SQLite from the same INSERTs
  values.py              generic value/block helpers (_to_list, _as_blocks, _resolve_*, …)
  dds.py                 pixel reader for uncompressed DDS textures (district colours)
  modifiers.py           modifier labels/direction, scripted variables & scaling values
  triggers.py            scripted-trigger index, requirement/swap-signal evaluation
  misc.py                DLC metadata, named colors, planet classes
  species.py             species classes, archetypes, portrait sets
  traits.py              traits, trait modifiers, trait categories
  government.py          ethics, policy restrictions, civics, authorities, origins
  jobs.py                pop jobs, job swaps
  colony_types.py        colony designations
  buildings.py           building reference data + building-effect helpers
  empires.py             prescripted empires, initializers, rooms, flag assets
  precursors.py          precursors, relics, deposits, home systems
  tech.py                repeatable + fallen-empire technologies
  traditions.py          tradition trees, ascension perks, edicts, council agendas
  federations.py         federation types, level perks, federation laws
  diplomacy.py           Galactic Community resolutions, subject agreement term names
  shroud.py              Shroud patrons, accords, callings and deeds
tests/                   unit tests (stdlib unittest) for the pure helpers
scripts/test             test-runner wrapper (`python3 -m unittest discover`)
build-sqlite.sh          rebuild sqlite/ from the committed dump (no game files needed)
sql/                     SQL dump output (checked into Git)
sqlite/                  SQLite DB output (gitignored)
```

Cross-module imports keep the original (underscore-prefixed) helper names; the package is internal, `stellaris-extract.py` is the only public entry point.

## Tests & Determinism

- `scripts/test` runs the unit tests (pure helpers: parser, loc cleanup, value helpers, SQL escaping). No dependencies beyond stdlib. Extend them when touching those helpers.
- Output is **deterministic**: two runs produce byte-identical dumps. Job-swap sources iterate `sorted(...)` (extractor/jobs.py) because set order varies with Python's hash seed — spurious diffs would pollute stellaris-data-comparison. Keep any new "pick/iterate from a set" extraction code sorted, and verify with two runs + `cmp` when in doubt.
- **Prove determinism with explicit hash seeds, not repeated runs.** Two plain runs only *probably* draw different seeds, so a pass proves little. Instead diff `PYTHONHASHSEED=1` against `PYTHONHASHSEED=2` — that is a test which can actually fail. Confirmed 2026-07-27: the pre-refactor monolith diverges between the two seeds (job_swap row order), the `extractor/` package does not, so the `sorted(...)` iteration is what fixed it. Expect a **one-off `sql/stellaris.sql` diff** in job_swap ordering versus any dump generated before that fix — it is the fix landing, not a regression.

---

## Extraction Steps (pipeline order in `extractor/cli.py`)

| What                        | Source                                              |
|-----------------------------|-----------------------------------------------------|
| Game version (`meta`)       | `launcher-settings.json` (`version` / `rawVersion`) |
| DLC metadata                | `dlc_metadata/dlc_info.txt` + `dlc/*/*.dlc` (category) + `dlc_dates.py` (release date) |
| Species classes + loc       | `common/species_classes/`                           |
| Species archetypes          | `common/species_archetypes/`                        |
| Portrait sets               | `common/portrait_sets/`                             |
| Traits + modifiers          | `common/traits/` (species-trait files only)         |
| Named colors + categories   | `common/named_colors/`                              |
| Ethics + tags               | `common/ethics/`                                    |
| Policy restrictions         | `common/policies/`                                  |
| Precursors + rewards        | `common/precursor_civilizations/`, `common/relics/`, `common/static_modifiers/`, `common/technology/`, `events/precursor_*` |
| Deposits (dictionary)       | `common/deposits/`                                  |
| Precursor home systems      | `common/solar_system_initializers/`                 |
| Civics                      | `common/governments/civics/0[0-3]_*.txt`            |
| Government types            | `common/governments/*.txt`                          |
| Authorities                 | `common/governments/authorities/`                   |
| Origins                     | `common/governments/civics/00/01_origins*.txt`      |
| Planet classes              | `common/planet_classes/`                            |
| Prescripted empires         | `prescripted_countries/`                            |
| Initializers + flags + rooms| `common/solar_system_initializers/`, `flags/`, rooms regex-scanned from `prescripted_countries/` |
| Jobs                        | `common/pop_jobs/` (+ `common/inline_scripts/`)     |
| Colony types                | `common/colony_types/` (+ `common/inline_scripts/colony_types/`) |
| Buildings reference         | `common/buildings/`                                 |
| Job swaps                   | `common/pop_jobs/` + `common/scripted_triggers/`    |
| Repeatable techs            | `common/technology/`                                |
| Fallen empire techs         | `common/technology/00_fallen_empire_tech.txt` + `localisation/english/fallen_empire_l_english.yml` |
| Traditions + ascension perks| `common/tradition_categories/`, `common/traditions/`, `common/ascension_perks/`, `common/defines/00_defines.txt` (whitelisted keys) |
| Federations                 | `common/federation_types/`, `common/federation_perks/`, `common/federation_law_categories/`, `common/federation_laws/` |

Localisation: `localisation/english/*.yml`

Shared loop helpers in `cli.py`: `_iter_parsed(files, progress)` (per-file parse with warning on failure) and `_merge_dir(dir, progress)` (merge all `*.txt` of a dir into one dict, later files win). Note their scope: only *parse* failures are downgraded to warnings; an exception inside an `extract_*` call now propagates (extractor bugs should be loud, not swallowed).

---

## Parser (`tokenize` + `Parser` class)

Clausewitz script → nested Python dicts/lists.

**Key behaviour:**
- Duplicate keys → promoted to list: `option = { … } option = { … }` → `{'option': [dict1, dict2]}`
- `key { }` without `=` is valid (parsed same as `key = { }`)
- Anonymous sub-blocks (`{ key = X }` inside a list, as in `in_breach_of = { { key = X } { key = Y } }`) — the `{` token is consumed as a key; the parser recurses into the sub-block and discards the result. **This was a bug fix** — previously the stray `}` would terminate the WRONG outer block, making subsequent keys (like `valid`) unreachable.
- `@variable` references resolved per-file from top-level `@key = value` entries.
- **Comparisons** (`num_ascension_perks > 2`, `years_passed >= 5`) parse as `Cmp(op, value)` values. Before, a comparison flipped the whole block into a token list, silently dropping its `key = value` entries (or the comparison, depending on which came last). The fix left every pre-existing table byte-identical.
- **`if` chains**: an `if` value is an `IfBlock` (a `dict` subclass) whose `.chain` lists the `else_if`/`else` blocks directly following it. Duplicate-key promotion turns several `if`s into a list and loses which `else` belongs to which; `.chain` keeps it. Invisible to every reader that treats it as a dict (`else_if`/`else` are still stored under their own keys too).

---

## DB Schema

All tables use `CREATE TABLE IF NOT EXISTS` except those that are fully regenerated each run (use `DROP TABLE IF EXISTS` first):

**Regenerated each run:** `modifier_label`, `ethic_tag_label`, `ethic`, `ethic_modifier`, `ethic_tag`, `ethic_policy_restriction`, `civic`, `civic_modifier`, `civic_requirement`, `granted_trait`, `origin`, `origin_modifier`, `origin_requirement`, `planet_class`, `prescripted_empire`, `prescripted_empire_item`, `precursor`, `precursor_relic`, `precursor_relic_modifier`, `precursor_relic_passive_text`, `precursor_relic_resource`, `precursor_relic_active_modifier`, `precursor_reward_modifier`, `precursor_building`, `precursor_building_resource`, `precursor_building_modifier`, `precursor_technology`, `precursor_homeworld_reward`, `deposit`, `deposit_resource`, `deposit_modifier`, `precursor_system`, `precursor_planet`, `precursor_planet_deposit`, `precursor_planet_modifier`, `precursor_megastructure`, `technology`, `tech_repeatable`, `tech_repeatable_modifier`, `tech_fallen_empire`, `game_define`, `tradition_category`, `tradition_category_desc`, `tradition`, `tradition_prereq`, `tradition_swap`, `tradition_modifier`, `ascension_perk`, `edict`, `council_agenda`, `federation_type`, `federation_type_perk`, `federation_perk`, `federation_perk_modifier`, `federation_law_category`, `federation_law`, `resolution_group`, `resolution_category`, `resolution`, `agreement_term`, `agreement_term_value`, `agreement_preset`, `patron`, `patron_accord`, `patron_accord_modifier`, `patron_calling_of`, `patron_deed_of`, `calling`, `calling_tier`, `deed`, `starbase_part`, `starbase_level`, `starbase_module_resource`, `starbase_building_resource`, `strategic_resource`, `personality`, `attitude`, `opinion_modifier`

Drop order matters for FK integrity:
```sql
DROP TABLE IF EXISTS ethic_policy_restriction;
DROP TABLE IF EXISTS ethic_tag;
DROP TABLE IF EXISTS ethic_modifier;
DROP TABLE IF EXISTS ethic;
DROP TABLE IF EXISTS ethic_tag_label;
DROP TABLE IF EXISTS modifier_label;
```

Full schema: see the `SCHEMA` string in `extractor/schema.py`.

---

## Key Constants (extend as new cases are found)

### `INVERTED_MODIFIERS` (frozenset)
Modifier keys where a more positive value is WORSE (stored with `positive_is_good = 0`):
- Upkeep/cost: `leaders_upkeep_mult`, `planet_pops_*_upkeep_mult`, `pop_resettlement_cost_mult`
- Resource consumption: `pop_amenities_usage_add`, `pop_amenities_usage_mult`, `pop_housing_usage_mult`
- Administrative: `species_empire_size_mult`, `empire_size_pops_mult`
- Influence costs: `country_claim_influence_cost_mult`, `country_starbase_influence_cost_mult`
- Edict costs: `edicts_upkeep_mult`, `edicts_cost_mult`
- Costs / fees: `terraforming_cost_mult`, `country_trade_fee`
- War: `country_war_exhaustion_mult`
- Combat: `army_damage_taken_mult`, `gravity_snare_cooldown_mult`, `gravity_snare_cost_mult`, `ships_upkeep_mult`, `shroud_delve_cooldown`
- Trait slots: `negative_leader_traits_species`

### `MODIFIER_LOC_OVERRIDES` (dict)
Modifier keys whose loc key doesn't follow `MOD_<UPPER>` / `mod_<lower>` pattern:
```python
'species_leader_exp_gain': 'MOD_LEADER_SPECIES_EXP_GAIN'
```

### `_ETHIC_SKIP_KEYS` (frozenset)
Keys inside `country_modifier` blocks that aren't real numeric modifiers: `show_only_custom_tooltip`, `custom_tooltip`

---

## DLC (`extract_dlc`, misc.py)

`dlc_info.txt` lists every DLC (36 in 4.5.2), installed or not. Its block key (`dlc029`) follows
release order. `category` comes from the DLC's own `dlc/<dir>/<key>.dlc` descriptor, matched by
name. Only installed DLCs have one, so the 5 uninstalled ones (early portrait packs, sign-up
bonus, soundtrack) have none. The game files hold no release date, so `release_date` comes from a
hand table in `dlc_dates.py`, keyed by Steam app id and fetched from the Steam store API. Ids
the store no longer lists stay NULL. **Add a row there for each new DLC.** `host_checks` counts
`host_has_dlc = "<name>"` in `common/` and `events/`. A gameplay DLC with 0 checks has been
merged into the base game: Utopia, Synthetic Dawn and Humanoids in 4.5.2. Saves leave those out
of `required_dlcs`.

## Ethics Extraction (`extract_ethics`)

- Parses `country_modifier` block: resolves `@variable` refs (e.g. `@ethic_attunement1`), skips non-numeric keys.
- Populates `modifier_label` for any new modifier keys found in ethics.
- Parses `tags` block: inserts into `ethic_tag` and `ethic_tag_label` (loc key = tag key directly, e.g. `ETHIC_ALLOWS_SLAVERY`).

---

## Policy Restriction Extraction (`extract_policy_restrictions`)

Reads `common/policies/*.txt`. For each `policy > option > valid` block, calls `_find_blocked_ethics()`.

### `_find_blocked_ethics(block) → set[str]`

Recursive. Returns ethics blocked by the valid condition:
- `NOR { has_ethic = "ethic_X" }` → ethic_X is blocked
- `NOT { has_ethic = "ethic_X" }` → ethic_X is blocked
- `OR { … NOR { … } }` → recurse into OR (NOR can be nested inside OR)
- Does NOT recurse into AND, custom_tooltip, etc. (avoids false positives)
- Only collects keys starting with `ethic_` (ignores civic-based conditions)

Result stored in `ethic_policy_restriction(policy_key, option_key, option_label, ethic_key)`.

**Known gap:** positive requirements (`has_ethic` outside NOR/NOT = "must have this ethic to use option") are not extracted — only blocks/prohibitions are.

---

## Civics Extraction (`extract_civics`)

Reads `common/governments/civics/*.txt`, once per file, tagging each with a `civic_type` derived from the filename: `00_civics`→**normal**, `01_special_civics`→ **special**, `02_gestalt_civics`→**gestalt**, `03_corporate_civics`→**corporate**. Runs late in `main()` (after `scripted_vars` is built, so `@var` modifier values resolve), threading a running `sort_order` across files so the global order is stable.

Per civic → `civic(key, name, description, civic_type, required_dlc, selectable, locked, ascension_lock, ascension_blocks, icon)` (ascension columns: see *Ascension-path restrictions* under Origins):
- **name** = loc[key]; **description** = `_clean_loc(loc[description_key])` (the `description = …` field's effects tooltip; cleaned but keeps some `£icon£` tag names).
- **required_dlc** from `playable.host_has_dlc`.
- **selectable = 0** when the civic is gated to a non-player country (a `potential.country_type`, or `playable = { always = no }`) — e.g. fallen-empire civics; the planner only loads `selectable = 1`.
- **`civic_modifier`**: the numeric `modifier { }` block (resolving `@vars`), reusing `_write_modifier_label`.
- **`granted_trait(owner_key, trait_key)`** (`_granted_traits`): the founder-species traits a civic or origin grants via its top-level `traits = { trait = … }` (Tankbound → `trait_tankbound`, Necrophage → `trait_necrophage`). Nested/conditional `traits` blocks aren't followed.
- **`trait_requirement`** (`traits.py` `_trait_requirements`): a species trait's `allowed_`/`forbidden_` `origins`/`civics`/`ethics` and `allowed_planet_classes`, same row shape as `civic_requirement` (each `allowed_*` list is one `require_any` set). E.g. Sedentary ✗ the nomad origins, Familial ✗ Gestalt, Aquatic → Ocean homeworld.
- **`civic_requirement`** (`_civic_requirements` + `_trigger_values`): eligibility parsed from the **top level** of `potential`/`possible`, for kinds authority/ethics/civics/origin, plus the species gates `species_class` (Tankbound → Necroid/Toxoid/Aquatic/Infernal), `archetype` (`species_archetype`, mostly NOT MACHINE), `planet_class` (`preferred_planet_class`, homeworld — all NOT pc_volcanic) and `trait` (`traits`, e.g. Pyrolatry NOT Aquatic). A gate repeated in one block (duplicate key → list) is read entry by entry. Operators: `require` (bare `value`), `forbid` (`NOT`/`NOR`), `require_any` (`OR`, with the set tied by `group_id`). Deeply nested OR-of-different-kinds gates (rare) aren't unpacked.

Counts (last run): ~265 civics (81 normal / 80 gestalt / 52 corporate / 52 special, of which most special are non-selectable), 721 modifiers, 1159 requirement rows.

---

## Origins Extraction (`extract_origins`)

Reads `common/governments/civics/00_origins.txt` (playable) + `01_origins_non_playable.txt` (forced `selectable = 0`), filtering to `is_origin = yes`. Same shape as civics → `origin(key, name, description, required_dlc, selectable, ascension_lock, ascension_blocks, starting_colony, forces_home_system, icon)` + `origin_modifier` (numeric `modifier{}`, mostly sparse — origin effects are largely narrative/initializer/event-driven) + `origin_requirement` (**reuses `_civic_requirements`** — mostly civic conflicts in `possible.civics`). **selectable = 0** also for swap-variant forms whose `potential` is gated on already having an `origin` (the Legendary Leader death/imperial/dictatorial forms) or a `country_type`. ~70 origins, 54 selectable, 88 modifiers, 378 requirement rows.

**Ship-type gate (origins + civics):** `_CIVIC_GATE_KINDS` includes `graphical_culture → ship_type`; a `possible.graphical_culture` `NOR`/`OR` of the biological cultures (`biogenesis_01/02`) is collapsed to a single `organic` value, so an origin/civic carries `ship_type forbid organic` (most) or `ship_type require_any organic` (e.g. Wilderness, the gestalt bio-civics). 7 origins / 9 civics forbid, a few force.

**Ascension-path restrictions (origins + civics):** `ascension_restrictions()` (pure; `compute_ascension_restrictions` feeds it the files) reads the four path perks' `potential`/`possible` in `common/ascension_perks/00_ascension_paths.txt` → `ascension_lock` (forbidden from three paths, locked to the fourth) / `ascension_blocks` (comma list). `has_origin`/`has_civic`/`has_valid_civic` are collected with scripted triggers expanded (`is_natural_design_empire = no` → Natural Design); `NOT`/`NOR` and `= no` flip polarity, a negation that is only one alternative of an OR doesn't count, and `if`/`limit` blocks are skipped. An origin forbidden from all four starts already ascended and is resolved to its path through `02_council_agendas_ascensions.txt` (there the `if`/`limit` mentions *are* read); a civic forbidden from all four blocks every path (`ascension_blocks` = all four — Natural Design / Innate Design). 4.5.2: 7 origins, 15 civics (Chosen ×4 and Entropy Drinkers ×4 → Psionic, Augmentation Bazaars → Cybernetic, Galvanic ×4 → no Synthetic).

**Nomadic gate (origins + civics):** a top-level `is_nomadic = yes|no` in `potential`/`possible` becomes `gameplay require nomadic` (nomads only: Voidfarers, Forever Cruise, Heirs of the Khan, The Sacred Path, 13 civics) or `gameplay forbid nomadic` (settled only). No row means both — since 4.5 that includes Knights of the Toxic God and Storm Chasers, which show nomads a `swap_type` description variant instead.

## Planet Classes Extraction (`extract_planet_classes`)

Merges `common/planet_classes/*.txt` → `planet_class(key, name, climate, colonizable, is_standard, ideal)`. Keeps the **colonizable** worlds (the standard climates + special/engineered types) plus a curated set of standard **uninhabitable** bodies (`_STANDARD_UNINHABITABLE`: barren/molten/toxic/frozen/gas_giant/asteroid) for home-system context; stars and one-off event worlds are skipped. `climate` (wet/dry/cold) is null for special/uninhabitable; `is_standard = initial = yes` (the default startable climates). Habitability is species-preference-based (the traits' `pc_<class>_habitability` modifiers, in `trait_modifier`), not per-class, so it isn't stored; `ideal = yes` classes (Gaia, ring world, ecumenopolis, hive world, …) are the exception the empire dashboard needs for its estimate, as are the defines `HABITABILITY_WRONG_PLANET` / `_OPPOSITE_PLANET` (0.7 / 0.4: the game's yellow and red). ~30 classes.

**Loc fix shipped here:** `_LOC_ENTRY` changed `^\s+`→`^\s*` so **unindented** loc entries are read — the Infernals Species Pack file puts entries at column 0, so e.g. `origin_red_giant`/`origin_cosmic_dawn` names (and other Infernals loc) were being dropped. The `l_english:` header still never matches (no `"value"`); `#` comments aren't word-initial.

## Prescripted Empires Extraction (`extract_prescripted_empires`)

Reads `prescripted_countries/*.txt` (top-level dir, not under `common/`) → the ~52 built-in empires usable as starting points. `prescripted_empire` holds the scalar setup (species class/portrait, authority, government, origin, planet_class, graphical_culture, resolved name); `prescripted_empire_item` holds the repeated picks (trait/civic/ethic). Same structure as a `user_empire_designs.txt` entry, so the planner parses that user file with the same shape. `name` resolves the loc key (prescripted) or is the literal (user files).

---

## Precursor Extraction (`extract_precursors`)

The 9 precursors (5 base + Baol/Zroni from Ancient Relics + Inetian/adAkkaria from Cosmic Storms). Each grants up to four things: a **home system**, a **permanent empire modifier**, a **relic** (7 of 9 — First League & Irassian have none), and a **building** (Zroni & adAkkaria only).

**Why a config spine (`PRECURSORS` list):** the precursor→reward mapping is *not* in any single game file. Rewards are granted by `add_relic` / `add_modifier` buried in dig-site event chains (`events/precursor_events*.txt`, `events/ancient_relics_arcsite_events_*.txt`). So `PRECURSORS` hand-encodes the verified mapping (like `TRAIT_CATEGORIES`); the *effect numbers and names* are then pulled live from the game files. Extend `PRECURSORS` if a future DLC adds more.

**Data sources merged at runtime:** `common/precursor_civilizations/*.txt` (picture), `common/relics/relics.txt` (relic effects), all `common/static_modifiers/*.txt` (the permanent reward modifiers). Building names come from loc only.

**Relic passive parsing (`_relic_passive_rows`)** handles both Clausewitz forms of `triggered_country_modifier`: a nested `modifier = { … }` block *and* modifiers written directly as keys of the block (e.g. `r_reality_perforator`). The `potential` qualifier is rendered by `_relic_condition` into a **parseable** `condition` string (`NULL` = always): top-level entries are ANDed (comma-separated) and an `OR` sub-block is parenthesised, e.g. `(is_machine_empire = yes OR is_wilderness_empire = yes)`. The planner parses this to highlight effects per empire type, so keep it parseable.

**Relic full picture (passive / triumph / cost / cooldown).** A relic isn't just its numeric passive — `extract_precursors` also captures:
- **`precursor_relic_passive_text`** (`_relic_passive_texts`) — non-numeric passives via `custom_tooltip` inside `triggered_country_modifier`, e.g. the Cybrex War Forge's "Permits building Cybrex Warform armies…" (it has *no* numeric passive, so it showed nothing before). Each row carries the block's `condition` (its `potential`, NULL = always) just like `precursor_relic_modifier`, so a conditional passive (the Cryo Core's `is_wilderness_empire = yes` district) is shown with its qualifier and dims under the empire filter — rather than appearing to always apply.
- **`active_effect`** — the triumph (active) tooltip (`_relic_active_text`): the non-generic `custom_tooltip`s of `active_effect`, skipping the generic cooldown ones. Relics whose active is a pure resource payout (War Forge) have no descriptive tooltip and rely on the structured `produces` rows instead — do **not** trust the loc here (`relic_cybrex_active` says "5000 Alloys" but the script produces 1000, +1000/use).
- **`precursor_relic_resource`** (`_relic_resources`) — both `kind='cost'` (`resources.cost`) and `kind='produces'` (the **direct** `add_resource` children of `active_effect` — nested ones like the tempest invocator's `on_cancel` refund are excluded). `scales = 1` when a `multiplier`/`mult` (e.g. `cybrex_count`) is present, so the War Forge is cost 3000 unity + 2000 minerals(scales), produces 1000 alloys(scales).
- **`precursor_relic_active_modifier`** (+ `active_mode`, `active_detail`, `group_label`) via `_relic_active_outcomes` — the temporary modifiers a triumph grants, in three **modes**:
  - **`direct`**: an `add_modifier` anywhere in active_effect (`_collect_add_modifiers`, recursive — the Tempest Invocator's is nested in `start_storm_area_placing > on_confirm`), excluding `relic_activation_cooldown`. Yuht Cryo Core → `yuht_cryo_core` = −20% ship upkeep (10 years); Tempest Invocator → `tempest_invocator_active_modifier` = `country_storm_repulsion_influence_add` 1.0 (= +100%, via the `MOD_COUNTRY_STORM_REPELLING_ADD` loc override). detail "For 10 years." Durations also resolve `months` (the Weather Manipulator).
  - **`random`**: a `country_event`'s `(locked_)random_list` — Reality Perforator → `precursor.105` → one of `reality_perforator_1..4` (+40% weapon/shield/armor/hull) at random, or all as a jackpot. detail "One at random for 2–5 years, or all of them for 1 year." (single entries vs the multi-`add_modifier` jackpot entry).
  - **`choice`**: a `country_event`'s `option`s, each `add_modifier`ing a named guidance — Zro Crystal → `ancrel.7006` → `group_label` "Martial Guidance" (ship_weapon_damage +20%) / "Statecraft Guidance" (stability +2, edict upkeep −10%, gov ethic attraction +25%), 5 years. Non-modifier options (the ruler level) are appended to detail ("…or gains 1 level").

  `main()` builds the **events index** (`id → event`) from the precursor/ancrel event files, indexing **all** event types (`country_event`, `ship_event`, `planet_event`, `fleet_event`) — relic actives fire `country_event`s, but the home-world discovery rewards live in `ship_event`/`planet_event` blocks. Durations resolve `@triumph_duration` etc. and convert days→years. `_resolve_static_modifier` skips a modifier with `show_only_custom_tooltip = yes` (the game hides its numbers — e.g. the Weather Manipulator's storm field, conveyed by the active_effect tooltip instead). Note the script values can differ from the wiki (Statecraft stability is **+2**, not the wiki's +3) — trust the files.
- **`triumph_cooldown_days`** — the days of the `relic_activation_cooldown` `add_modifier` *inside* `active_effect` (recursively found — this is the value the game applies, e.g. the War Forge's short 900-day cooldown), falling back to `activation_duration`.

`@activation_cost` / `@triumph_duration` etc. live in **global** `common/scripted_variables/`, not relics.txt — `main()` parses those into `scripted_vars` and passes them in for cost/cooldown resolution.

**Duplicate definitions:** `artifact_irassian_research_completed` is defined twice in `10_static_modifiers_ancient_relics.txt`; the parser promotes it to a list, and the extractor takes the **later** definition (game load order → `0.075`, not `0.1`). Same list-guard applies to relics.

**Reuses `modifier_label`** via the shared `_write_modifier_label()` helper, so the display project gets human-readable effect strings for free.

**Precursor buildings (`precursor_building` + `_resource` + `_modifier`).** Two kinds, distinguished by `precursor_building.source`: **'homeworld'** = the unique buildings granted on the home planet (Zroni Equilibrator, adAkkaria Patriotic Institute); **'technology'** = empire-wide buildings unlocked by the precursor's "Secrets of …" tech (`tech_key` set; scope 'planet' or 'starbase'). Both go through `_write_precursor_building`, parsing the def from `buildings` (loaded in `main()` from **both** `common/buildings/` and `common/starbase_buildings/`): restrictions (`empire_limit` from `base_cap_amount = N` or `empire_limit = { base = N }` via `_building_cap`, `planet_limit`, `base_buildtime` → `build_time_days`, `category`, `can_demolish`), `resources` → `cost`/`upkeep`/ `produces` rows (`_building_resources` — prefers the unconditional cost block over `is_wilderness_empire` variants), and modifiers (`_building_modifiers`) from `planet_modifier`/`country_modifier` **and** the `triggered_*_modifier` blocks (scope `planet`/`country`/`system`/`station`), **aggregated per (scope, key)** into `modifier_value` (base, no perks) + `ae_value` (total with the Archaeo-Engineers AP). `_building_modifiers` reads each block's ascension-perk gate (`_perk_gate`) and sums correctly for **both** shapes: 'replacement' (a `NOT archaeoengineers` base block + an `archaeoengineers` block → e.g. Cybrex +100% base / +150% AE) and 'additive' (an un-perk-gated base + an `archaeoengineers` bonus that stacks → e.g. League +50 base / +100 AE, Baol +0.1 / +0.225). A block gated by a **different** perk (e.g. Vultaum's advanced-logic under `ap_cosmogenesis`) becomes its own row with that perk key in `condition` (so it isn't mistaken for an always-on effect). **Jobs** come from the building's `jobs/*` `inline_script` (`_building_inline_jobs`): the **first** `triggered_planet_modifier` (regular-empire; gestalt/FE get calculator/archivist equivalents) gives the `job_<x>_add` keys, count = the building's `AMOUNT` → `scope='job'` rows. Starbase building names use the `sm_<key>` loc key. Buildings whose effect is a dynamic `custom_tooltip` (`[Root.Get…Effect]`, e.g. Zroni Storm Caster's storm spawn) have no modifier rows — only cost/upkeep. Non-`jobs/` inline_scripts are skipped.

**Precursor technologies (`precursor_technology`).** The 7 Ancient-Relics precursors (not the Cosmic Storms ones) each have a `tech_secrets_*` tech (from `common/technology/`, loaded into `technologies`) granted by the discovery special project — cost (`@tier4cost3` = 12000), tier 4, area society, category archaeostudies — unlocking one building (`unlocks_building_key`). Mapped via each PRECURSORS entry's `technology` = (tech_key, building_key, scope).

**`precursor_homeworld_reward`** (`_homeworld_reward_rows`) — the one-time "Homeworld reward" granted when a precursor's home world is finally found, parsed from each entry's `homeworld_event` (a `ship_event`/`planet_event` whose option grants it; `None` for Baol/adAkkaria, whose capstone grants only the relic). Two shapes share the table: a **scaled resource burst** (`add_monthly_resource_mult` — `multiplier` = the ×monthly-output factor, clamped to `[min_amount, max_amount]`, resolving `@tier*` scripted vars) and the **flat minor-artifacts payout** (`resource = 'minor_artifacts'`, `multiplier = NULL`, bounds from the `(small|medium|large)_artifact_reward` scripted effects, e.g. large = 750..1500). Rewards repeat identically across the event's options (one per end state), so rows dedupe by resource. Separate from `precursor_reward_modifier` (the *permanent* empire modifier) and the relic.

**`precursor_full` VIEW:** convenience header — one row per precursor with its (0..1) relic and building pre-joined. Multi-row effects stay relational (join `precursor_relic_modifier` on `relic_key`, `precursor_reward_modifier` on `key`).

**Known minor gaps:** Yuht & Cybrex relics have no active-effect text (Yuht has no `custom_tooltip`; Cybrex's only one is the generic cooldown). `num_digsites` only set where the arc sites group cleanly (Baol 4, Zroni 6).

---

## Precursor Home Systems & Deposits (`extract_deposits`, `extract_precursor_systems`)

The home systems each precursor leads you to (planets, deposits, megastructures).

**`deposit` dictionary** — extracts *all* of `common/deposits/` into `deposit` (+ `deposit_resource` for produces/cost/upkeep, `deposit_modifier` for the `planet_modifier`s of blockers and features — values resolved through the scripted variables: the strategic-resource features (Bubbling Swamp, Fuming Bog, …) give `district_farming_max_add = @DISTRICTS_FROM_SR_DEPOSITS` (3), which a literal-only read dropped: 21 rows in 4.5.2, the dashboard's farming caps 3 short). An orbital deposit's `habitat_modifier` (what a habitat in its system gets: `district_hab_mining|energy|science_max_add` = 1 + floor(size / 2)) goes to `deposit_habitat_modifier`, for the dashboard's habitat sites. `is_blocker` = category contains `blocker`; `clear_time` = the `time` field; `prerequisites` = the technologies a blocker needs before clearing (comma-separated; 12 blockers in 4.5.2). So `d_minerals_10` → produces 10 minerals, `d_ruined_arcology` → blocker, −1 district, 360-day clear costing 750 energy + 250 minerals. A kind with several blocks goes through `_primary_resource_block`: the unconditional blocks merged (Crystal Rift: unity + physics), else the block gated on a megastructure's **starting overclock mode** (`_default_overclocks`: highest flat `ai_weight`, 100 when absent, ties to the first listed — so the Arc Furnace shows *balanced* smelting, its 4.4 figures). Deposits whose blocks are all otherwise gated (nanites, living metal, virtual power) still get no `deposit_resource` rows.

**`PRECURSOR_SYSTEMS` config** maps each precursor → its initializer key(s). **Zroni** spans 3 (`zroni_legacy_initializer_01/02/03`); **Cybrex** = just `cybrex_system` (Cybrex Alpha, a ruined debris field). Inetian = `weathermanipulators_home_system`, adAkkaria = `adSalivul_system` + `adSul_system`. All `common/solar_system_initializers/*.txt` are parsed and merged; the config selects the ones we care about.

**Gotcha — two Cybrex systems; keep only the precursor one.** The Cybrex *precursor* home is **`cybrex_system` (Cybrex Alpha)**: it has the **ruined ring world** (four `spawn_megastructure = { type = ring_world_ruined }` in the *system-level* `init_effect`, lines ~1393+) but no habitable planets — just the star + debris ambients. The separate **`cybrex_beta`** (a *rebuilt* ringworld, flag `ring_world_built`) belongs to the **Contingency end-game crisis** (`events/crisis_events_3.txt`), not the dig, so it is **excluded** from `PRECURSOR_SYSTEMS`. (The ring world is captured as a megastructure, not planets; the four ruined sections are one `precursor_megastructure` row with `count = 4`.)

**Planet parsing:** `precursor_planet.id` is the synthetic `<initializer>#<index>` (deterministic → works in both SQL dump and SQLite, no autoincrement/lastrowid). Planet class → loc (`pc_relic` → "Relic World", `pc_nuked` → "Tomb World"). Moons are flattened into the planet list with `is_moon = 1`; `is_homeworld` = any planet flag containing `homeworld`.

**`_as_blocks()` vs `_to_list()` — important:** `_to_list` returns a *dict's keys*, which is wrong for dict-valued sub-blocks (`init_effect`, `add_modifier`, `spawn_megastructure`). Use `_as_blocks` for those — it wraps a single dict as `[dict]` and filters lists to dicts. (Sub-blocks are promoted to a list by the parser only when they appear more than once.)

**Megastructures live at two levels.** A planet's `init_effect` *can* hold `spawn_megastructure`, but the **system-level** `init_effect` does too — the Yuht ruined hyper relay (`hyper_relay_ruined`) is system-level, so both scopes are scanned. Cybrex's ruined ringworld surfaces as `ring_world_ruined` + the `pc_cybrex`/`pc_ringworld_*` planet sections.

**Deposit sources on a planet:** the top-level `deposit` key *and* `add_deposit`/`set_deposit` inside `init_effect`(s). Planet `add_modifier`s are resolved through `static_mods` into `precursor_planet_modifier` (e.g. the First League homeworld's +10 society/physics/engineering research).

---

## Authorities Extraction (`extract_authorities`, government.py)

`common/governments/authorities/` → `authority` (+ `authority_modifier`, `authority_requirement` via `_authority_requirements`/`_trigger_values` — same require/forbid/require_any shape as civics). `required_dlc` partly hand-mapped via `_AUTH_DLC`.

## Jobs Extraction (`extract_jobs`, jobs.py)

`common/pop_jobs/` → `job` (+ `job_resource`, `job_modifier`): category, flavour description, base produces/upkeep, and non-resource effects (amenities, crime, …) for the plain-empire state. Many jobs keep their figures in an **inline script**, under `resources` (`output/*`: foundry/researcher/…) or at job level (`jobs/job_telepath_resources`) — `_job_resource_blocks` expands both (cached, possibly nested). Per-file `@vars` overlay the global `scripted_vars`. `job_resource` is the sum of the blocks that apply to a plain empire (`_eval_job_resources` with an empty state; drone jobs are evaluated as gestalt), so civic-, flag- or building-gated extras drop out (Pleasure Seekers' civilian upkeep). Two corrections: a `mult = value:…` block is dropped when an unconditional block already gives its resources (`_drop_scaled_duplicates`: polytechnic's stockpile bonus), and when no block of a kind applies, `_fallback_block` shows the first of a set of alternatives every empire pays one of (organic/lithoid/infernal, hive/machine, an `= no` complement). Known gaps: subject-tier upkeep (`overlord_beholder`) and `overlord_offspring_drone_feeder` show none.

`job.budget_source` is the job's `resources.category` (`_job_budget_source`, inline scripts followed): the key the country budget books its output under, `planet_<x>`. Drone jobs reuse the organic jobs' categories (`technician_drone` → `planet_technician`, `fabricator` → `planet_metallurgists`), and some jobs share the generic `planet_jobs` (`patrol_drone`) — so the job key cannot be derived from the budget source. 4.5.2: 265 of 365 jobs have one.

## Colony Types (`extract_colony_types`, colony_types.py)

`common/colony_types/` → `colony_type` (+ `colony_type_modifier`): the designations' localised name and description (`col_<x>`, `col_<x>_desc`, `$ref$`s expanded) and their unconditional `planet_modifier`s — own blocks plus the `colony_types/colony_type_planet_modifier*` inline scripts filled with `MODIFIER`/`MODIFIER2` (`colony_type_modifiers`). `triggered_planet_modifier` extras (Adaptability finished +5 %, GalCom resolutions) are left out. 4.5.2: 101 designations, 175 modifier rows.

## Buildings Reference (`extract_buildings_ref`, buildings.py)

`common/buildings/` → `building` (+ `building_resource`, `building_job`, `building_modifier`, `building_unlock`): category, description, build cost/upkeep, provided jobs, the other modifiers (`_building_modifiers`, base value without perks; a row gated by a different perk carries it in `condition`; `job_*_add` keys stay in `building_job`) and the civics/origins a building hard-requires (`building_unlocks`: positive `has_civic`/`has_valid_civic`/`has_origin` in potential/allow/can_build via `_gate_owners` — an alternative inside an OR with non-civic conditions, e.g. Fear of the Dark's early Factory, doesn't count; an OR of one civic's variants does). Reuses the precursor building helpers (`_building_resources`, `_building_inline_jobs`). 4.5.2: 1035 modifier rows, 201 unlock rows over 72 civics/origins.

**Costs behind inline scripts.** 4.5.2 buildings declare their cost only through `resources = { inline_script = { script = buildings/nomadic_cost_switcher REGULAR_RESOURCE = minerals COST = @b1_minerals … } }`, so before 2026-10-22 `building_resource` had no `cost` rows for them (Research Lab, Foundry, …). `_building_resources` now expands `resources.inline_script` (`expand_inline`, the script read through `script_loader`) and picks per kind the unconditional block, else the first — the switcher's first is the regular empire's (`is_nomadic = no`). Research Lab I: 400 minerals. 567 cost rows.

**`district_resource`, `ship_size_resource`** (`extract_resources`, buildings.py): cost and upkeep of every district and ship size with a `resources` block, read **per file** (`@base_cost` is 300 in `02_rural_districts.txt`, 30 in `05_wilderness_districts.txt` — the merged dict would keep the last). Ship sizes gate their cost on `country_uses_bio_ships` (food instead of minerals) and take the first block: mining and research station 100 minerals + 1 energy upkeep, outpost 100 alloys, city district 500 minerals — the save's construction items and stockpile agree. For the empire dashboard (one-off spending, trade-offs). **`starbase_module_resource`, `starbase_building_resource`** (`extract_resources` again, on `common/starbase_modules|starbase_buildings`): cost, upkeep and the unconditional `produces` block (solar panel network 6 energy, trade hub 8 trade, hydroponics bay 10 food); their `construction_days` go to `build_time` (180). Names in `starbase_part` (`sm_<key>`; weapon variants have none); `starbase_level` counts each level's module and building slots (starport 2 / 1). **`strategic_resource`, `personality`, `attitude`, `opinion_modifier`** (`extract_economy`, economy.py): market lots (`market_price / market_amount` = the base price per unit in trade, the market's currency), base storage, the AI's `ai_weight`/`ai_wants` (base values; their `modifier` factors are conditions on the empire and left out); each personality's `trade_willingness`; whether an attitude trades (`behaviour.trade`); opinion modifiers' base opinion, cap and monthly decay (`opinion_trade_gift`: +1 accumulative, max 100, decay 2). How the AI combines these into a deal's acceptance is engine code — the empire dashboard fits it to in-game numbers. `game_define` also carries the expansion and claim costs, the market fee, the pop-growth constants, the AI's trade constants and what ethics cost a federation's cohesion (`DEFINE_KEYS` in traditions.py). `ethic.category_value` / `category_opposite` place each ethic on its axis (fanatic 0, regular 1 | 3, fanatic 4; the second pole marked) — the empire dashboard's ethics distance. **`technology_requirement`** (`tech_requirement`, tech.py): per non-repeatable tech its `prerequisites` (JSON list), `potential` as a normalised trigger (DLC triggers mapped), `is_rare`, `is_dangerous` — what the dashboard needs to list techs within reach (the tier rule, 6 of the tier below per field, it keeps itself). Districts also get `district_job` (their `jobs/*` inline scripts via `_building_inline_jobs`: 200 miners per mining district, the save's job positions' unit; city districts none — their zones add jobs), and districts, ship sizes and buildings `build_time` (`base_buildtime`: rural districts 240 days, city 480, buildings 360, corvette 60; stations have none — the save's constructor orders count to 100).

## Council Positions (`extract_council_positions`, government.py)

`common/governments/councilors/` → `council_position` (+ `council_position_modifier`): **every** seat (179 on 4.5.2): `civic_key` for the 151 a civic opens, NULL for the base seats (defense, research, state and their nomadic forms), the ruler's per authority (`possible = { always = no }`: the authority grants it) and the gestalt nodes; `possible_json` is the normalised `possible`. The planner reads the civic ones (`WHERE civic_key IS NOT NULL`), the empire dashboard all. Name = loc[key], description = `<key>_desc` with `$TITLE$` → name, `leader_classes` from `leader_class`, `effect` = the `custom_tooltip` / `custom_tooltip_with_modifiers` loc (some seats have no modifiers, only that text). Modifier values are **per leader level** (the game's own `000_councilor_documentation.txt`: "multiplied by assigned Leader's level"); `@vars` resolve through the global scripted variables.

## Job Swaps (`extract_job_swaps`, jobs.py)

Per base job, each `swap_type` variant + trigger → which civic/origin/… forces the swap → `job_swap`, plus effective resources/modifiers on both sides → `job_swap_resource` / `job_swap_modifier`. Trigger conditions resolve through the `common/scripted_triggers/` index (`_scripted_trigger_index`; `_collect_swap_sources` finds the forcing source, `_signals` the positive/negative gates). Gestalt-only civics are attributed only to gestalt jobs (`attribute_ok`). Swap sources iterate **sorted** — see Tests & Determinism.

## Empire Assets (empires.py)

- `extract_prescripted_empires` — see its section above.
- `extract_initializers` — `common/solar_system_initializers/` → `initializer` (key + loc name), for the empire builder's home-system pick.
- `extract_flag_assets` — scans `flags/` (`flag_asset`: icon files per category + backgrounds; .dds names are what `empire_flag` blocks reference).
- `extract_flag_colors` — `flags/colors.txt` (`flag_color`: each swatch's `map` rgb, the galaxy map's territory colour; read by regex, the parser drops `rgb { … }`).
- `extract_rooms` — distinct `room = "…"` values regex-scanned from `prescripted_countries/` → `room` (rooms are gfx entities with no loc).

---

## Modifier Label Lookup Order

For any modifier key, label is looked up as:
1. `MODIFIER_LOC_OVERRIDES.get(mod_key)` → explicit override loc key
2. `loc['MOD_' + mod_key.upper()]`
3. `loc['mod_' + mod_key]`
4. `loc[mod_key]`

Unresolved `$KEY$` inline references in labels are discarded (label not stored if `'$' in label`).

`_clean_loc` also resolves scripted-loc concept tags `['concept_x']` / `['concept_x', display text]`, strips leftover `[Command.Chain]` tags, and converts literal `\n`/`\t`/`\r` escapes to spaces — relevant for longer text fields like relic descriptions/active effects. **`£icon£` refs are rendered as their word** (`£alloys£` → "alloys"), not stripped — otherwise text like "Produces £alloys£5000" would lose the resource and read "Produces 5000". (`§X` colour codes are still stripped.)

---

## Modifier Coverage

~97% of visible trait modifiers have labels. Gaps are typically:
- Very new modifiers added in recent patches
- Modifiers with non-standard loc key patterns → add to `MODIFIER_LOC_OVERRIDES`
- Attunement modifiers (`add_attunement_*`) — shroud-specific, no standard label

---

## Counts (last run)

| Table                     | Rows |
|---------------------------|------|
| DLC                       | 32   |
| Species classes           | 42   |
| Species archetypes        | 6    |
| Traits (base)             | 350  |
| Traits (DLC)              | 95   |
| Trait modifiers           | 673  |
| Modifier labels           | 113  |
| Ethics                    | 17   |
| Ethic modifiers           | 43   |
| Ethic tags                | 53   |
| Tag labels                | 31   |
| Policy restrictions       | 23   |
| Precursors                | 9    |
| Precursor relics          | 7    |
| Precursor relic modifiers | 11   |
| Precursor relic resources | 9    |
| Precursor relic active mods | 5  |
| Precursor reward modifiers | 19  |
| Precursor buildings       | 2    |
| Deposits (dictionary)     | 575  |
| Deposit resources         | 337  |
| Deposit modifiers         | 471  |
| Precursor systems         | 12   |
| Precursor planets         | 83   |
| Precursor planet deposits | 56   |
| Precursor planet modifiers | 3   |
| Precursor megastructures  | 2    |
| Repeatable techs          | 24   |
| Repeatable tech modifiers | 24   |
| Fallen empire techs       | 35   |
| Tradition trees           | 32   |
| Traditions                | 224  |
| Tradition/perk swaps      | 199  |
| Ascension perks           | 49   |
| Edicts                    | 171  |
| Council agendas           | 90   |
| Federation types          | 6    |
| Federation perks          | 60   |
| Federation law categories | 16   |
| Federation laws           | 64   |
| Resolutions               | 278  |
| Resolution categories     | 37   |
| Agreement terms           | 17   |
| Agreement term values     | 46   |
| Agreement presets         | 56   |
| Shroud patrons            | 14   |
| Patron accords            | 36   |
| Callings                  | 29   |
| Deeds                     | 56   |

---

## Technologies (`extract_repeatable_techs`, `extract_fallen_empire_techs`, tech.py)

Two tech slices the planner shows on their own pages; both share `_tech_common` (area/category/label/description resolution) and `_sort_key` (area → category → key, via `_AREA_SORT`/`_CATEGORY_SORT`), so extend those rather than either function.

- **`anomaly_category`** (`extract_anomaly_levels`, buildings.py) — each category's `level`; the dashboard picks the research days from `SCIENCE_SHIP_ANOMALY_RESEARCH_DAYS` by scientist level minus anomaly level.
- **`static_modifier` / `static_modifier_modifier`** (`extract_static_modifiers`, precursors.py) — every `common/static_modifiers` entry with name, `_desc` and `custom_tooltip` text, and its numeric effects (`_resolve_static_modifier`: none for `show_only_custom_tooltip`). The dashboard explains the modifiers a save puts on the empire (`country.timed_modifier`).
- **`district`, `zone`** (`extract_district_zone_names`; costs: `district_resource`, above, buildings.py) — display names, for the empire dashboard's colony view (`zone_research_unity` → "Archives"). Districts also get `color`, their colour in game: the opaque 1-px border of the grid-box icon `gfx/interface/icons/districts/grid_box/<icon>.dds` (`icon` from the district, else its key; read at (0,0) by `dds.py` — the fill inside is translucent and lighter). 140 of 147 in 4.5.2; the six resort specialisations and `district_ark_food` have no such icon (NULL).
- **`game_start_script`** (`game_start.py`) — the scripts that build a new empire's capital, for the planner's starting-capital preview: `generate_start_buildings_and_districts` and `void_dweller_home_planet_setup` with every `game_start/*` inline script expanded, as an ordered effect list (if-chains, `while`, buildings ± zone, districts, zones, variables, carrier/country flags). Read with an order-preserving parser (`parse_ordered`), since the dict parser loses the order of repeated keys and these effects depend on it. Limits are normalised trigger trees with `owner`/species scopes flattened (`any_owned_species` read as the founder species) and parameter-free scripted triggers inlined; `check_variable`/`has_zone` become structured leaves. Effects on pops, deposits, blockers and resources are dropped, and an if/while left without effects goes with them; anything else unmodelled stays as `{'e': 'unknown'}`.
- **`government_type`** (`extract_government_types`, government.py) — key and localised name of every government in `common/governments/*.txt` (`gov_representative_democracy` → "Representative Democracy"), for the dashboard; the save stores the key at `country.government.type`. 170 in 4.5.2. District caps and building slots are computed by the game from modifiers and are not extracted.
- **`technology`** (`extract_techs`) — every non-repeatable tech with label, description, tier, **base** `cost` and its `modifier` block (`technology_modifier`, shared writer with the repeatables): `@tierXcostY` resolved from the global scripted variables (and the tech files' own `@` definitions); a `cost = { factor = … inline_script = … }` block takes its `factor`. Start techs have no cost (NULL). In game the cost is higher: empire size (`EMPIRE_SIZE_TECH_COST_PENALTY`, in `game_define`) and more — 4.5.2 showed 1575 for a 1500 tech at empire size 50, an unexplained ×1.05. Read by the empire dashboard, not the planner. 655 techs.
- **`tech_repeatable`** (+ `tech_repeatable_modifier`) — identified by `weight_groups` containing `repeatable` (not by key prefix). `levels = -1` means unlimited. Modifier values resolve `@scripted_vars`; a list-valued modifier takes the **last** definition (game load order), and `_write_modifier_label` is reused so labels come for free. 24 techs / 24 modifiers.
- **`tech_fallen_empire`** — the techs defined in `00_fallen_empire_tech.txt`. There is no flag marking them, so `cli.main` loads `common/technology/` **per file** (not via `_merge_dir`) purely to capture that file's keys as `fe_tech_keys`. `building_key` is resolved from the **raw, pre-resolution** loc: many FE tech labels are a sole `$building_xxx$` reference, so `main` re-scans `fallen_empire_l_english.yml` with `_LOC_ENTRY`/`_LOC_REF` before `$KEY$` resolution collapses them into the building's display name. 35 techs.

---

## Traditions & Ascension Perks (`extract_traditions`, traditions.py)

Conditions are stored as **normalised JSON trigger trees**, not requirement rows, so the planner can evaluate them against an empire profile (`src/lib/triggers.ts` there). `normalize_trigger` (triggers.py, pure, tested in `tests/test_triggers.py`):

- `{"op":"and"|"or"|"not","args":[…]}` | `{"leaf":k,"value":v[,"cmp":">"]}`; `NOR` → `not(or)`, `NOT`/`NAND` → `not(and)`.
- `custom_tooltip = { … }` / `hidden_trigger` unwrap to **one** condition (one alternative inside an `OR`); text keys (`fail_text`, …) and bare-string tooltips drop.
- A lone `if` is an implication `(limit ∧ body) ∨ ¬limit`; an `if`/`else_if`/`else` chain (via `IfBlock.chain`) is an OR of branches, each guarded by the negated earlier limits. An `else_if` without `limit` acts as `else`.
- Scripted triggers stay leaves (the planner knows the empire-type ones), **except** pure DLC checks (`has_machine_age_dlc` = only `host_has_dlc`), inlined to `host_has_dlc` via `dlc_trigger_map`. `@variables` resolve per file.
- Scopes/iterators (`owner = {…}`) become opaque `{"leaf":k,"value":"scope"}`; `{"leaf":"complex"}` marks what can't be expressed.

Data notes:

- Map traditions to trees via each category's `traditions` list, **never by file name** (`00_mercantile.txt` is Commerce, `00_unyielding.txt` Fortification). `02_flexible_dummy` (`potential = { always = no }`) is skipped.
- `tradition_prereq` = positive `has_tradition` leaves of a `possible` that name a sibling (negated ones are exclusions). These edges are the tree layout — the GUI `tree_template` isn't needed.
- `tradition_swap` is identified as `<owner>#<n>`: `name` is optional and not unique (the Subterfuge finisher has two swaps with one name). Ascension perks use the same swap format, so the table (and `tradition_modifier`, whose `owner_key` may be a swap id) is shared.
- Descriptions: traditions `<key>_delayed` (the `<key>_desc` loc is stale effect text), perks `<key>_desc`; `tooltip` = the `custom_tooltip(_with_modifiers)` strings plus those directly in `on_enabled`.
- **No tier column**: a perk's `num_ascension_perks` bound differs per empire type (Mind Over Matter: 2, but 3 for synthetic empires; Biomorphosis: 0 for Clone Army), so the planner computes it per profile (`perkTier`).
- Ascension trees opened by a **country flag** (Machine Age / BioGenesis trees) get it from situation/event outcomes; not traced further.
- **Edicts / council agendas** (`extract_edicts_agendas`): `edict` (name/desc from `edict_<key>`, cost/upkeep per resource with per-file `@vars`, length −1 = until cancelled) and `council_agenda` (name/desc `council_agenda_<key>_name/_desc`, cost, launch-effect tooltip, `finish_modifier` resolved via `common/static_modifiers` into `tradition_modifier` as `<key>#finish`, duration default `COUNCIL_AGENDA_FINISH_MODIFIER_DURATION`). `unlocked_by_json` = positive `has_(active_)tradition` / `has_ascension_perk` leaves of potential+allow with scripted triggers expanded (`unlock_keys`; `has_psionic_ascension` → `ap_mind_over_matter`). Game triggers name a **swapped** tradition by its swap `name` (`tr_domination_drone_network`), so the planner matches swap loc keys too. `tradition.unlocks_agenda` is now the agenda **key** (Logistics names Open Markets, which actually gates on Commerce — the planner shows it and lets the profile filter it).

## Policies & Factions (`extract_politics`, politics.py)

`common/policies` → `policy` + `policy_option` (an option's modifiers in `tradition_modifier`,
owner = option key). One policy can sit in two categories (`war_philosophy`: diplomacy,
military), stored comma-joined. Option names repeat across policies in a few cases, and the
first one wins. `common/pop_faction_types` → `pop_faction_type` + `faction_demand`, including
the demands added by `pop_faction_types/demand_*` inline scripts (their `$PARAM$`s filled in,
`expand_inline`): GalCom pleased/outraged and the shared Cybernetic Creed ones. A demand's
`potential`/`trigger` read the owner (`owner = { … }` beside `exists = owner`), so
`owner_scope` unwraps that block before normalising. Otherwise the scope would stay opaque.
4.5.2: 60 policies, 222 options, 21 faction types, 303 demands.

## Situations, special projects, anomaly outcomes (situations.py)

`common/situations` → `situation` + `situation_approach`. `common/special_projects` (anonymous
`special_project = {…}` blocks, `key`) → `special_project`. Anomaly categories
(`extract_anomaly_levels`) carry name, description and `outcomes_json`, as projects do: the event
ids their `on_success` fires (`event_ids`: weighted `1 = anomaly.35`, `anomaly_event = …`,
`ship_event = { id = … }`), with each event's title from `<id>.name`. Event loc keys contain dots,
so `_LOC_ENTRY` accepts `.` in keys (before 2026-10-18 it did not, and every dotted key was
missing). The dashboard shows outcomes only behind a spoiler setting.

## Action timers (cooldowns.py)

`action_timer`: for each decision, artifact action and astral action (`potential`, `allow`,
`effect`) and each enclave event option (`leviathans_events_1.txt`, `overlord_enclaves_events.txt`:
`allow`/`trigger`, the rest as effect), relics (`possible` with its `inline_script` expanded,
`active_effect`), diplomatic actions (`potential`/`possible`, `on_accept`/`on_decline`) and
federation laws (`potential`/`allow`, `on_enact`): the timed modifiers (`add_modifier` with `days`,
`@vars` from `common/scripted_variables`) and timed flags (`set_timed_*_flag`) it sets, and the
modifiers and flags it needs absent (`NOT = { has_* }`) — in its triggers, or in the `limit` of an
`if` in its effect that sets the same timer (Satisfying Insults). Timers set by the events an
action fires (`fired()`, two levels, every `events/*.txt` indexed) count as its effects: Find
Military Applications adds its Combat Drugs modifier in `ancrel.10010`'s option. Enclave options
are not followed — their events are menus, and a "back" option would collect every other option's
timers (40 effect rows → 463).
`role = cooldown` for those its triggers wait on, `bonus` for those only an effect's `limit` waits
on (the action stays possible, it just does not set the timer again: Insult with Satisfying
Insults, checked in game 2026-10-25), `effect` for those it only sets. A waited-on key need not
be a timer (`NOT = { has_country_flag = attacked_xuracorp }` is permanent); the dashboard only
matches keys the save holds with an end date. `days` is null where the length is an unresolved
`@var` (the traders' `@traderDealDuration`). 4.5.2: 531 rows.
`astral_action_cooldown`: each astral action's own `cooldown` (days; the engine tracks it, no
modifier) — 8 in 4.5.2.

## Scripted triggers, edict prerequisites, species rights

`scripted_trigger`: every parameter-free `common/scripted_triggers` definition, normalised, so a
reader can evaluate a `some_trigger = yes` leaf by its body (1,575 of 1,587 in 4.5.2; those with
`$PARAM$` are skipped, `has_parameters`). `edict.prerequisites_json`: the techs an edict needs.
`species_right` (rights.py): every option of `common/species_rights/<kind>/`, with `kind` as the
save names the field and `kind_order` the game's order. Their triggers are in the species' scope
with the country as `from`; `unwrap_from` folds `from = { … }` in. `description` is the game's
tooltip (`<key>_tooltip`); `is_slavery` / `is_purge` mark the citizenships whose species take a
slavery or purge type (the other types do nothing without one); `species_right_modifier` holds
the unconditional `pop_group_modifier` entries (`right_modifiers`; triggered ones left out).

## Prescripted names (`extract_prescripted_names`, misc.py)

`prescripted_name`: every `PRESCRIPTED_*` / `EMPIRE_DESIGN_*` loc key with its text (528 in 4.5.2).
Saves name a prescripted empire's ruler, species and empire by these keys. Also the quoted entries
of `common/random_names/` that are loc keys (`random_name_keys`; 320 in 4.5.2, 848 rows in all):
saves name some systems by them (`VOIDWORM_SYSTEM_12` = "Papqaqtet").

## Empire focus and leader traits (focus.py, leaders.py)

`common/country_focus` → `focus_category`, `focus_card` and `focus_reward`. A card inherits the
fields of its `base = <card>` (`with_base`). Its requirement is the loc of its condition's
`success_text`, whose dotted `$…req$` references `inline_refs` resolves. A card also stores its
`add_focus_progress` amounts (`progress_json`: 10 / 25 / 50 by tier). A reward stores every
`add_research_option` of its effect (`options_json`) with the condition under which it is the
one offered: an `if`'s limit, or for `else` the earlier limits failing (`research_options`). The
empire decides which one it gets; it is not a choice. The leader
files of `common/traits`, which the species traits skip (`is_species_trait_file`), go to
`leader_trait` + `leader_trait_modifier`: the untriggered modifier blocks by scope (country, self,
councilor, fleet, army, planet, sector). The save's keys are these (`trait_ruler_eye_for_talent_2`).
4.5.2: 147 cards, 30 rewards, 672 leader traits.

## Federations (`extract_federations`, federations.py)

- **Types**: `federation_type` per `common/federation_types/` entry; `imperial_loyalists_federation` (`potential = { always = no }`, event-only) is skipped. `levels.level_<n> = { experience perks }` → `federation_type_perk` rows (`experience` = XP to the next level via per-file `@xp_to_level_N`, NULL at the top).
- **Who may form it**: `unlocked_by_json` / `excluded_by_json` = `unlock_keys(allow, idx, FEDERATION_LEAVES[, negated=True])` — traditions/swaps/perks **plus** `has_ethic`/`has_civic`/`has_valid_civic`, scripted triggers expanded (`is_militarist` → `ethic_militarist`, `ethic_fanatic_militarist`). The finishers name their **swap** (`tr_harmony_federations_finish`), as for edicts. `negated` collects what sits under `NOT`/`NOR` (Barbaric Despoilers can't form 4 of the 6 types). `if`/`else` branches are all walked, so Trade League's no-Megacorp-DLC fallback (`civic_corporate_dominion`) is listed alongside Merchant Guilds.
- **Perks**: only those a type grants (`temp_remove`/`neutral`/`hostile` are unreferenced). Modifiers from `members_/federation_/leader_/envoy_modifier` and `member_/leader_triggered_modifier` (with `potential_json`) → `federation_perk_modifier.scope`; labels via `_write_modifier_label`. `effect` = top-level `custom_tooltip` + each block's `description` loc.
- **Laws**: ordered by each category's `laws` list (never by file). Laws whose potential requires `imperial_loyalists_federation` are skipped (9, incl. the event-only `succession_type_none` / `succession_term_perpetual`). `types_json` = positive `has_federation_type` in the potential (per-type variants such as `fleet_contribution_low_hegemony`); `required_centralization` from `required_centralization = { base = N }`; `cohesion_growth` per month.

## Job-naming scripted loc (`scripted_loc_jobs`, localisation.py)

Loc like `$researcher_type_plural_with_icon$` → `[GetResearcherPluralWithIcon]`, a `common/scripted_loc` `defined_text` that picks the job per empire type at runtime. `_clean_loc` resolves such commands (only those whose loc key is a `job_…`) to the `default`, else the first (regular-empire) variant — so "Amenities Produced per 100" reads "… per 100 Researchers" everywhere (also fixed "Resources Produced from Miners", "additional Priest Jobs", …). The map is module state set once per run by `set_scripted_loc`, like the scripted-modifier meta.

---

## Companion repo: Stellaris Modding Insights

A sibling repo collects hard-won, poorly-documented Stellaris findings — engine quirks, parser/format gotchas, things only obvious in hindsight: `../stellaris-modding-insights` (sibling checkout, https://github.com/mkoester/stellaris-modding-insights). It's community-facing notes organised as `docs/NN-topic.md` with a TL;DR index in `README.md`. Especially relevant here: how the game scripts (`common/`, `events/`, `localisation/`) are structured and parsed.

**When you discover something noteworthy** while working in this repo — a surprising Clausewitz-script/localisation parsing quirk, an undocumented file-format detail, a non-obvious gotcha, or anything that cost real time to figure out — proactively suggest recording it in the insights repo. Don't write there silently: propose what to add (which `docs/` file, a one-line TL;DR) and let the user confirm, since it's a separate, published repo. Keep project-specific extraction mechanics in this file; route generally-useful Stellaris modding knowledge to the insights repo.

## Resolutions and agreement terms (`extract_diplomacy`, diplomacy.py)

For the dashboard, which reads both from saves by key (`resolution.<id>.type`, a subject agreement's
`term_data`). **Resolutions**: `resolution` per `common/resolutions/` entry with its category — the
first `resolution_categories` entry listing it in `resolution_types` (one resolution is in none) —,
`level`, `target`, the influence cost, `repeal` (`fire_and_forget = yes`: repeals one in force and
is never kept active) and the normalized `potential_json` / `allow_json` (the dashboard's list of
what is proposable: a tier's `allow` names the tier below with `is_active_resolution`, a repeal's
`potential` the one it repeals; DLCs, global flags, the Politics tradition, perks) and
`valid_target_json` (for `target = 1`: which empire it may name, in the target's scope) and
`ai_weight_json` (`{base, modifiers: [{factor, add, trigger}]}`, triggers in the AI's scope — the
dashboard predicts each AI's vote from it);
`resolution_category.group_key` → `resolution_group`.
**A resolution's effects are not here**: its `modifier = { <its own key> = 1 }` names the static
modifier of the same key (`static_modifier` / `static_modifier_modifier`, 102 resolutions in
4.5.2). **Agreements**: names only — `agreement_term` (with `term_type` and `hidden`),
`agreement_term_value` (`term_key`), `agreement_preset`; values of hidden terms often have no
localisation (`yes_access`), their name is NULL.

## The Shroud (`extract_shroud`, shroud.py)

For the dashboard (a country's `patron_relations` in saves name patrons, callings and deeds by
key). `patron` per `common/patrons/patron_types.txt` entry: `category` (major = the four cardinal
ones at (±1, 0)/(0, ±1), minor = the eight on the diagonals at (±0.7, ±0.7), great = the two at
the centre), `color` from `{ r g b a }` (minor patrons are black), the covenant's name and
description; `patron_accord` (+ `patron_accord_modifier` from each passive accord's
`triggered_modifier`); which callings and deeds each draws from (`patron_calling_of`,
`patron_deed_of`). `calling_tier`: one row per `tier` block, numbered from `first_tier`, with the
attunement reward (`@calling_attunement_tierN` = 100/200/300) and `<calling>_tier<N>_trigger_desc`
where the calling has a trigger. `deed.attunement` is towards its patron; the game shows the
opposite patron's deeds as negative ("Exploiting −20" for the Composer of Strands).

