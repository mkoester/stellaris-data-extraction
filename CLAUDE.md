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
portrait              -- species_class_key, portrait_id, position_in_class
portrait_set          -- set_key, portrait_id, source_file
species_class_attribute -- species_class_key, attribute_key, attribute_value
```

---

## Known Bug

**Portraits are not extracted from real Stellaris files.**

Last run output:
```
Species classes    : 42
Portraits (class)  : 0        <-- should be hundreds
Portrait set entries: 170
Attributes         : 191
```

The mock test data used a simple list block:
```
portraits = {
    mam1
    mam2
}
```

The real Stellaris files likely use a different structure, e.g.:
```
portraits = {
    portrait = { ... }
    portrait = "mam1"
}
```
or nested groups. **Next step: inspect the actual file structure:**

```bash
head -80 ~/.local/share/Steam/steamapps/common/Stellaris/common/species_classes/00_species_classes.txt
```

Then fix `extract_species_classes()` in the parser accordingly.

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

1. **Fix portrait extraction** — inspect real file format, update `extract_species_classes()`
2. Possibly extend to `common/species_names/` for name list data
3. Evaluate additional data from `common/portrait_sets/` (already working)
