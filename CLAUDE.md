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

Game install: `/home/mk/.local/share/Steam/steamapps/common/Stellaris`

Regenerate the DB consumed by the planner:
```bash
python3 stellaris-extract.py /home/mk/.local/share/Steam/steamapps/common/Stellaris --sqlite sqlite/stellaris.sqlite
```

---

## Project Layout

```
stellaris-extract.py     main script
sql/                     SQL dump output (checked into Git)
sqlite/                  SQLite DB output (gitignored)
```

---

## Extraction Steps

| Step   | What                        | Source dir                        |
|--------|-----------------------------|-----------------------------------|
| [0/7]  | DLC metadata                | `dlc_metadata/dlc_info.txt`       |
| [1/7]  | Species classes + loc       | `common/species_classes/`         |
| [2/7]  | Species archetypes          | `common/species_archetypes/`      |
| [3/7]  | Portrait sets               | `common/portrait_sets/`           |
| [4/7]  | Traits + modifiers          | `common/traits/`                  |
| [5/7]  | Named colors + categories   | `common/named_colors/`            |
| [6/7]  | Ethics + tags               | `common/ethics/`                  |
| [7/7]  | Policy restrictions         | `common/policies/`                |
| [8/8]  | Precursors + rewards        | `common/precursor_civilizations/`, `common/relics/`, `common/static_modifiers/` |
| [9/10] | Deposits (dictionary)       | `common/deposits/`                |
| [10/10]| Precursor home systems      | `common/solar_system_initializers/` |

Localisation: `localisation/english/*.yml`

---

## Parser (`tokenize` + `Parser` class)

Clausewitz script → nested Python dicts/lists.

**Key behaviour:**
- Duplicate keys → promoted to list: `option = { … } option = { … }` → `{'option': [dict1, dict2]}`
- `key { }` without `=` is valid (parsed same as `key = { }`)
- Anonymous sub-blocks (`{ key = X }` inside a list, as in `in_breach_of = { { key = X } { key = Y } }`) — the `{` token is consumed as a key; the parser recurses into the sub-block and discards the result. **This was a bug fix** — previously the stray `}` would terminate the WRONG outer block, making subsequent keys (like `valid`) unreachable.
- `@variable` references resolved per-file from top-level `@key = value` entries.

---

## DB Schema

All tables use `CREATE TABLE IF NOT EXISTS` except those that are fully regenerated each run (use `DROP TABLE IF EXISTS` first):

**Regenerated each run:** `modifier_label`, `ethic_tag_label`, `ethic`, `ethic_modifier`, `ethic_tag`, `ethic_policy_restriction`, `precursor`, `precursor_relic`, `precursor_relic_modifier`, `precursor_reward_modifier`, `precursor_building`, `deposit`, `deposit_resource`, `deposit_modifier`, `precursor_system`, `precursor_planet`, `precursor_planet_deposit`, `precursor_planet_modifier`, `precursor_megastructure`

Drop order matters for FK integrity:
```sql
DROP TABLE IF EXISTS ethic_policy_restriction;
DROP TABLE IF EXISTS ethic_tag;
DROP TABLE IF EXISTS ethic_modifier;
DROP TABLE IF EXISTS ethic;
DROP TABLE IF EXISTS ethic_tag_label;
DROP TABLE IF EXISTS modifier_label;
```

Full schema: see `SCHEMA` string at top of `stellaris-extract.py`.

---

## Key Constants (extend as new cases are found)

### `INVERTED_MODIFIERS` (frozenset)
Modifier keys where a more positive value is WORSE (stored with `positive_is_good = 0`):
- Upkeep/cost: `leaders_upkeep_mult`, `planet_pops_*_upkeep_mult`, `pop_resettlement_cost_mult`
- Resource consumption: `pop_amenities_usage_add`, `pop_housing_usage_mult`
- Administrative: `species_empire_size_mult`, `empire_size_pops_mult`
- Influence costs: `country_claim_influence_cost_mult`, `country_starbase_influence_cost_mult`
- Edict costs: `edicts_upkeep_mult`, `edicts_cost_mult`
- War: `country_war_exhaustion_mult`
- Combat: `army_damage_taken_mult`, `gravity_snare_cooldown_mult`, `gravity_snare_cost_mult`
- Trait slots: `negative_leader_traits_species`

### `MODIFIER_LOC_OVERRIDES` (dict)
Modifier keys whose loc key doesn't follow `MOD_<UPPER>` / `mod_<lower>` pattern:
```python
'species_leader_exp_gain': 'MOD_LEADER_SPECIES_EXP_GAIN'
```

### `_ETHIC_SKIP_KEYS` (frozenset)
Keys inside `country_modifier` blocks that aren't real numeric modifiers:
`show_only_custom_tooltip`, `custom_tooltip`

---

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

## Precursor Extraction (`extract_precursors`)

The 9 precursors (5 base + Baol/Zroni from Ancient Relics + Inetian/adAkkaria from
Cosmic Storms). Each grants up to four things: a **home system**, a **permanent
empire modifier**, a **relic** (7 of 9 — First League & Irassian have none), and a
**building** (Zroni & adAkkaria only).

**Why a config spine (`PRECURSORS` list):** the precursor→reward mapping is *not*
in any single game file. Rewards are granted by `add_relic` / `add_modifier` buried
in dig-site event chains (`events/precursor_events*.txt`,
`events/ancient_relics_arcsite_events_*.txt`). So `PRECURSORS` hand-encodes the
verified mapping (like `TRAIT_CATEGORIES`); the *effect numbers and names* are then
pulled live from the game files. Extend `PRECURSORS` if a future DLC adds more.

**Data sources merged at runtime:** `common/precursor_civilizations/*.txt` (picture),
`common/relics/relics.txt` (relic effects), all `common/static_modifiers/*.txt`
(the permanent reward modifiers). Building names come from loc only.

**Relic passive parsing (`_relic_passive_rows`)** handles both Clausewitz forms of
`triggered_country_modifier`: a nested `modifier = { … }` block *and* modifiers
written directly as keys of the block (e.g. `r_reality_perforator`). The `potential`
qualifier is rendered by `_relic_condition` into a **parseable** `condition` string
(`NULL` = always): top-level entries are ANDed (comma-separated) and an `OR`
sub-block is parenthesised, e.g. `(is_machine_empire = yes OR is_wilderness_empire = yes)`.
The planner parses this to highlight effects per empire type, so keep it parseable.

**Duplicate definitions:** `artifact_irassian_research_completed` is defined twice in
`10_static_modifiers_ancient_relics.txt`; the parser promotes it to a list, and the
extractor takes the **later** definition (game load order → `0.075`, not `0.1`).
Same list-guard applies to relics.

**Reuses `modifier_label`** via the shared `_write_modifier_label()` helper, so the
display project gets human-readable effect strings for free.

**`precursor_full` VIEW:** convenience header — one row per precursor with its
(0..1) relic and building pre-joined. Multi-row effects stay relational (join
`precursor_relic_modifier` on `relic_key`, `precursor_reward_modifier` on `key`).

**Known minor gaps:** Yuht & Cybrex relics have no active-effect text (Yuht has no
`custom_tooltip`; Cybrex's only one is the generic cooldown). `num_digsites` only
set where the arc sites group cleanly (Baol 4, Zroni 6).

---

## Precursor Home Systems & Deposits (`extract_deposits`, `extract_precursor_systems`)

The home systems each precursor leads you to (planets, deposits, megastructures).

**`deposit` dictionary** — extracts *all* of `common/deposits/` into `deposit`
(+ `deposit_resource` for produces/cost/upkeep, `deposit_modifier` for blocker
`planet_modifier`s). `is_blocker` = category contains `blocker`; `clear_time` =
the `time` field. So `d_minerals_10` → produces 10 minerals, `d_ruined_arcology`
→ blocker, −1 district, 360-day clear costing 750 energy + 250 minerals.

**`PRECURSOR_SYSTEMS` config** maps each precursor → its initializer key(s).
**Zroni** spans 3 (`zroni_legacy_initializer_01/02/03`); **Cybrex** = just
`cybrex_system` (Cybrex Alpha, a ruined debris field). Inetian =
`weathermanipulators_home_system`, adAkkaria = `adSalivul_system` + `adSul_system`.
All `common/solar_system_initializers/*.txt` are parsed and merged; the config
selects the ones we care about.

**Gotcha — two Cybrex systems; keep only the precursor one.** The Cybrex
*precursor* home is **`cybrex_system` (Cybrex Alpha)**: it has the **ruined ring
world** (four `spawn_megastructure = { type = ring_world_ruined }` in the
*system-level* `init_effect`, lines ~1393+) but no habitable planets — just the
star + debris ambients. The separate **`cybrex_beta`** (a *rebuilt* ringworld,
flag `ring_world_built`) belongs to the **Contingency end-game crisis**
(`events/crisis_events_3.txt`), not the dig, so it is **excluded** from
`PRECURSOR_SYSTEMS`. (The ring world is captured as a megastructure, not planets; the four ruined
sections are one `precursor_megastructure` row with `count = 4`.)

**Planet parsing:** `precursor_planet.id` is the synthetic `<initializer>#<index>`
(deterministic → works in both SQL dump and SQLite, no autoincrement/lastrowid).
Planet class → loc (`pc_relic` → "Relic World", `pc_nuked` → "Tomb World"). Moons
are flattened into the planet list with `is_moon = 1`; `is_homeworld` = any
planet flag containing `homeworld`.

**`_as_blocks()` vs `_to_list()` — important:** `_to_list` returns a *dict's keys*,
which is wrong for dict-valued sub-blocks (`init_effect`, `add_modifier`,
`spawn_megastructure`). Use `_as_blocks` for those — it wraps a single dict as
`[dict]` and filters lists to dicts. (Sub-blocks are promoted to a list by the
parser only when they appear more than once.)

**Megastructures live at two levels.** A planet's `init_effect` *can* hold
`spawn_megastructure`, but the **system-level** `init_effect` does too — the Yuht
ruined hyper relay (`hyper_relay_ruined`) is system-level, so both scopes are
scanned. Cybrex's ruined ringworld surfaces as `ring_world_ruined` + the
`pc_cybrex`/`pc_ringworld_*` planet sections.

**Deposit sources on a planet:** the top-level `deposit` key *and*
`add_deposit`/`set_deposit` inside `init_effect`(s). Planet `add_modifier`s are
resolved through `static_mods` into `precursor_planet_modifier` (e.g. the First
League homeworld's +10 society/physics/engineering research).

---

## Modifier Label Lookup Order

For any modifier key, label is looked up as:
1. `MODIFIER_LOC_OVERRIDES.get(mod_key)` → explicit override loc key
2. `loc['MOD_' + mod_key.upper()]`
3. `loc['mod_' + mod_key]`
4. `loc[mod_key]`

Unresolved `$KEY$` inline references in labels are discarded (label not stored if `'$' in label`).

`_clean_loc` also resolves scripted-loc concept tags `['concept_x']` / `['concept_x', display text]`, strips leftover `[Command.Chain]` tags, and converts literal `\n`/`\t`/`\r` escapes to spaces — relevant for longer text fields like relic descriptions/active effects.

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

---

## Companion repo: Stellaris Modding Insights

A sibling repo collects hard-won, poorly-documented Stellaris findings — engine quirks, parser/format gotchas, things only obvious in hindsight:
`../stellaris-modding-insights` (`/home/mk/Projects/stellaris-modding-insights`, https://github.com/mkoester/stellaris-modding-insights). It's community-facing notes organised as `docs/NN-topic.md` with a TL;DR index in `README.md`. Especially relevant here: how the game scripts (`common/`, `events/`, `localisation/`) are structured and parsed.

**When you discover something noteworthy** while working in this repo — a surprising Clausewitz-script/localisation parsing quirk, an undocumented file-format detail, a non-obvious gotcha, or anything that cost real time to figure out — proactively suggest recording it in the insights repo. Don't write there silently: propose what to add (which `docs/` file, a one-line TL;DR) and let the user confirm, since it's a separate, published repo. Keep project-specific extraction mechanics in this file; route generally-useful Stellaris modding knowledge to the insights repo.
