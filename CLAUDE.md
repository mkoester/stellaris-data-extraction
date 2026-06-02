# Stellaris Data Extraction

## Goal

**Short-term:** Build a tool that generates valid species setups (including traits) for vanilla Stellaris.

**Long-term:** Generate full empires and output proper files usable directly in Stellaris (save files or mods).

The extraction work here (parsing game scripts → SQLite) is the foundation — it captures species classes, portraits, traits, ethics, and policy rules from vanilla game files so the planner/generator has an accurate source of truth.

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

---

## Project Layout

```
stellaris-extract.py     main script
sql/                     SQL dump output (checked into Git)
sqlite/                  SQLite DB output (gitignored)
.claude/CLAUDE.md        technical reference: schema, extraction steps, parser details, constants
```

For schema, extraction steps, parser architecture, and key constants — see `.claude/CLAUDE.md`.
