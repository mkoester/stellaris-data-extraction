# Stellaris Portrait Extractor

## Goal

Parse Stellaris vanilla scripts and store species classes, portraits, and
attributes in a structured SQLite database (with SQL dump for Git).

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
species_class         -- key, archetype, graphical_culture, move_pop_sound_group,
                      --   portrait_modding (0/1), possible_block, source_file
portrait_set          -- set_key, species_class_key, portrait_id, source_file
                      --   set_key e.g. "mammalians", species_class_key e.g. "MAM"
species_class_attribute -- species_class_key, attribute_key, attribute_value
```

Portraits are defined in `portrait_sets/` files (not in `species_classes/`). Each set
has a `species_class = MAM` field that links it to a species class.

---


## Source Files Parsed

| Directory | Files |
|---|---|
| `common/species_classes/` | `00_species_classes.txt`, `01_base_species_classes.txt` |
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
Parser.parse()          top-level file -> dict
Parser.parse_block()    { } block -> dict or list
                          - list: no '=' found (e.g. plain portrait IDs)
                          - dict: key = value pairs
Writer                  dual-output: StringIO buffer (.sql / stdout) + sqlite3 connection
```

---

## Next Steps

1. Possibly extend to `common/species_names/` for name list data
2. Consider capturing `conditional_portraits` and `non_randomized_portraits` from portrait_sets
