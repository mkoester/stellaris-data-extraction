# Stellaris Data Extraction

## Goal

**Short-term:** Build a tool that generates valid species setups (including traits) for vanilla Stellaris.

**Long-term:** Generate full empires and output proper files usable directly in Stellaris (save files or mods).

The data extraction work here (parsing game scripts → SQLite) is the foundation — it captures species classes, portraits, traits, etc. from vanilla game files so the generator has an accurate source of truth to work from.

---

## Current Script: `stellaris_portraits.py`

### Usage

```bash
# Print SQL to stdout (pipe-friendly, nothing written to disk)
python3 stellaris_portraits.py /path/to/Stellaris

# Write SQL to a file (ideal for Git review)
python3 stellaris_portraits.py /path/to/Stellaris --out portraits.sql

# Write directly to SQLite
python3 stellaris_portraits.py /path/to/Stellaris --sqlite ./sqlite/stellaris_portraits.sqlite

# Both at once
python3 stellaris_portraits.py /path/to/Stellaris --out portraits.sql --sqlite ./sqlite/stellaris_portraits.sqlite
```

**Rules:**
- Only the path given → SQL goes to **stdout**, progress/summary to **stderr**
- `--out` or `--sqlite` (or both) given → SQL goes to file(s), progress/summary to **stdout**
- No `--no-sql` flag — it was removed; stdout mode replaces that use case

### Tested on

- CachyOS desktop
- Stellaris at `~/.local/share/Steam/steamapps/common/Stellaris`

---

## SQLite Schema

```sql
dlc                     -- name (PK, matches host_has_dlc), steam_id, affects_gameplay
species_class           -- key, archetype, graphical_culture, move_pop_sound_effect,
                        --   portrait_modding (0/1), possible_block, source_file
portrait_set            -- set_key, species_class_key, portrait_id, required_dlc, source_file
                        --   required_dlc NULL = base game; conditional_portraits blocks captured too
species_class_attribute -- species_class_key, attribute_key, attribute_value

species_archetype       -- key (BIOLOGICAL/LITHOID/ROBOT/MACHINE/…),
                        --   trait_points, max_traits (from common/species_archetypes/),
                        --   robotic (0/1), inherit_trait_points_from,
                        --   base_trait (canonical always-assigned trait, 0 cost, free slot),
                        --   source_file
                        --   ROBOT has no portrait class; base_trait hardcoded to trait_mechanical

trait                   -- key, cost (base points), category, initial (0/1), randomized (0/1),
                        --   hidden (0/1), required_dlc, source_file
trait_archetype         -- trait_key, archetype (BIOLOGICAL/LITHOID/ROBOT/MACHINE/…)
trait_species_class     -- trait_key, species_class_key (e.g. LITHOID, PSIONIC)
trait_opposite          -- trait_key, opposite_key (mutually exclusive pairs)
trait_tag               -- trait_key, tag (e.g. organic, positive, pop_output)
```

DLC metadata comes from `dlc_metadata/dlc_info.txt`. The `host_has_dlc` field in trait
and portrait definitions names the required DLC exactly as it appears in `dlc.name`.

Portraits are defined in `portrait_sets/` files (not in `species_classes/`). Both the
base `portraits = {}` block and `conditional_portraits = {}` blocks are extracted.

Traits come from `common/traits/`; leader trait files are skipped (filtered by filename).
Some source files contain duplicate keys (Stellaris bug) — the parser merges them;
`_to_list()` flattens the resulting nested lists.

---


## Source Files Parsed

| Directory | Files |
|---|---|
| `common/species_classes/` | `00_species_classes.txt`, `01_base_species_classes.txt` |
| `common/species_archetypes/` | `00_species_archetypes.txt` |
| `common/portrait_sets/` | `00_portrait_sets.txt` |

---

## Project Layout

```
stellaris_portraits.py   main script
sql/                     SQL dump output (checked into Git)
sqlite/                  SQLite DB output (gitignored)
.gitignore               ignores *.sqlite, *.sqlite3, *.db; keeps sql/
```

---

## Code Conventions

- **All code in English**: variable names, comments, print output — even when conversing in German.
- Python 3.10+ (uses `str | None` union syntax).

---

## Parser Architecture

```
tokenize(text)          strip comments, split into tokens
                          @identifier kept as single token (e.g. @robot_trait_points)
                          so @variable references survive into the parsed dict
Parser.parse()          top-level file -> dict
Parser.parse_block()    { } block -> dict or list
                          - list: no '=' found (e.g. plain portrait IDs)
                          - dict: key = value pairs
Writer                  dual-output: StringIO buffer (.sql / stdout) + sqlite3 connection
```

`@variable` resolution happens per-extractor after parsing (see `extract_species_archetypes`).
Inheritance (`inherit_trait_points_from`) is resolved in a second pass within that function.

---

## Next Steps

- Species generator / validator (uses extracted DB as source of truth)
- `common/species_names/` for name list data
- `conditional_portraits` and `non_randomized_portraits` from portrait_sets
- Full empire generation + Stellaris file output
