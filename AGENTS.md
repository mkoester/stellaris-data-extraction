# stellaris-data-extraction

Extracts Stellaris game data into SQL/SQLite for `stellaris-empire-planner`. Python 3.10+, stdlib
only.

Cross-project conventions come from the OKF vault; workspace rules from `../AGENTS.md`. **The DB
schema, the full pipeline step list, the project layout and the key-constant tables are in
`docs/design-notes.md`** — read it when you need the field-level detail.

## Commands

```bash
python3 stellaris-extract.py <STELLARIS_DIR>                  # SQL → stdout, progress → stderr
python3 stellaris-extract.py <STELLARIS_DIR> --out a.sql --sqlite a.db
scripts/test                                                  # unit tests, stdlib only
```

Game install: `~/.local/share/Steam/steamapps/common/Stellaris`. After any extraction change,
regenerate **both** outputs in one run:

```sh
python3 stellaris-extract.py <STELLARIS_DIR> --out sql/stellaris.sql --sqlite sqlite/stellaris.sqlite
```

The Mac's install gives a byte-identical dump to the Linux one (4.5.2, checked 2026-10-25), so
either machine may regenerate it. A fresh clone has no `sqlite/` (gitignored): `mkdir sqlite`
first, or the run stops at "directory does not exist".

**⚠ The committed dump `sql/stellaris.sql` is what the deployment builds from** (`build-sqlite.sh`
on the server). Regenerating only the gitignored SQLite works locally and then 500s in production
on missing columns. Check: `git status` lists `sql/stellaris.sql` as modified.

**All code is English** — names, comments and output — even when the session is in German.

## ⚠ Determinism

Output must be **byte-identical between runs**. Set iteration order varies with Python's hash seed,
so any "pick or iterate from a set" extraction code must go through `sorted(...)`.

**Prove it with explicit hash seeds, not repeated runs.** Two plain runs only *probably* draw
different seeds, so a pass proves almost nothing:

```sh
PYTHONHASHSEED=1 … > a.sql ; PYTHONHASHSEED=2 … > b.sql ; cmp a.sql b.sql
```

That is a check that can actually fail — the pre-refactor monolith diverges between the two seeds
and the `extractor/` package does not.

## ⚠ The Clausewitz parser

The game's script format has quirks that produce silently wrong trees rather than errors:

- **Duplicate keys are promoted to a list** — `option = {…} option = {…}` becomes
  `{'option': [d1, d2]}`. Code reading such a key must handle both shapes.
- **`key { }` without `=` is valid** and parses the same as `key = { }`.
- **Anonymous sub-blocks** (`{ key = X }` inside a list) consume the `{` as a key and recurse. This
  was a bug fix: previously the stray `}` terminated the **wrong outer block**, making later keys
  unreachable — which reads as the game not defining them.
- **`@variable` references resolve per file**, from that file's top-level `@key = value` entries.
- **Comparisons** (`num_ascension_perks > 2`) parse as `Cmp(op, value)`. This was a bug fix: a
  comparison used to turn its block into a token list, silently dropping the block's other keys.
- **Typed colours lose their values** — `map = rgb { 151 14 18 }` parses as `{'map': 'rgb'}`, the
  block dropped without an error (the keys after it survive). `flags/colors.txt` is read by regex
  for that reason (`flag_colors()` in `empires.py`).
- **`if` values are `IfBlock`s** whose `.chain` keeps their own `else_if`/`else` — duplicate-key
  promotion loses that pairing once a block has several `if`s.

## Conventions

**Civics and origins have a second extractor** — `../stellaris-mod-FirstContact-MeaningfulLabels/tools/gen_empire_intel.py`,
standalone by design. Changing which civic/origin files are read or how an origin is recognised here
means changing it there too, and vice versa (see the workspace file).

Keep pure helpers (parser, localisation cleanup, value helpers, SQL escaping) free of I/O — they
are what `scripts/test` covers, and the coverage only holds while that boundary does.
