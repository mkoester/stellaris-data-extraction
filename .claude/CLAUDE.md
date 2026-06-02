# Stellaris Data Extraction — Claude Context

## Keeping This File Current

**After every session where extraction logic changes, a new table is added, a parser fix is made, or a new constant is extended, update the relevant sections of this file.** This file travels with the repo and is the authoritative hand-off for the next session.

---

## Purpose

Parses Stellaris vanilla game files → SQLite DB consumed by `stellaris-empire-planner`.

```
python3 stellaris-extract.py <STELLARIS_DIR> --sqlite sqlite/stellaris.sqlite
```

Game install: `/home/mk/.local/share/Steam/steamapps/common/Stellaris`

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

**Regenerated each run:** `modifier_label`, `ethic_tag_label`, `ethic`, `ethic_modifier`, `ethic_tag`, `ethic_policy_restriction`

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

## Modifier Label Lookup Order

For any modifier key, label is looked up as:
1. `MODIFIER_LOC_OVERRIDES.get(mod_key)` → explicit override loc key
2. `loc['MOD_' + mod_key.upper()]`
3. `loc['mod_' + mod_key]`
4. `loc[mod_key]`

Unresolved `$KEY$` inline references in labels are discarded (label not stored if `'$' in label`).

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
