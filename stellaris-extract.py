#!/usr/bin/env python3
"""
stellaris-extract.py

Parses Stellaris vanilla scripts and stores the data in a structured SQLite
database (and optionally a .sql dump for review).

Extracts: DLC metadata, species classes, species archetypes, portrait sets,
traits, and named colors.

Usage:
    python3 stellaris-extract.py <STELLARIS_INSTALL_DIR> [OPTIONS]

Examples:
    # Print SQL to stdout
    python3 stellaris-extract.py ~/.steam/steam/steamapps/common/Stellaris

    # Write SQL to a file
    python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql

    # Write directly to SQLite
    python3 stellaris-extract.py /path/to/Stellaris --sqlite stellaris.sqlite

    # Both at once
    python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql --sqlite stellaris.sqlite
"""

import re
import sys
import sqlite3
import argparse
from pathlib import Path
from io import StringIO


# ---------------------------------------------------------------------------
# Clausewitz parser
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Split Clausewitz script text into tokens: keys, =, {, }, values."""
    # Strip comments
    text = re.sub(r'#[^\n]*', '', text)
    # Tokens: quoted strings, @variable names, operators, identifiers
    # @identifier is kept as a single token so variable references survive parsing
    pattern = r'"[^"]*"|@[a-zA-Z_]\w*|[{}=<>!]|[^\s{}=<>!@"]+'
    return re.findall(pattern, text)


class Parser:
    """Recursive Clausewitz parser -> nested Python dicts/lists."""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_block(self) -> dict | list:
        """Parse the content between { }. Returns a dict, or a list if no = found."""
        result = {}
        items_list = []
        is_list = None  # determined on first element

        while self.peek() not in (None, '}'):
            key = self.consume()

            if self.peek() == '=':
                self.consume()  # skip '='
                is_list = False
                value = self.parse_value()
                # Duplicate keys -> promote to list
                if key in result:
                    if not isinstance(result[key], list):
                        result[key] = [result[key]]
                    result[key].append(value)
                else:
                    result[key] = value
            else:
                # No '=' -> plain list entry (e.g. portrait IDs)
                is_list = True
                items_list.append(key.strip('"'))

        return items_list if is_list else result

    def parse_value(self):
        tok = self.peek()
        if tok == '{':
            self.consume()  # '{'
            val = self.parse_block()
            if self.peek() == '}':
                self.consume()  # '}'
            return val
        else:
            return self.consume().strip('"')

    def parse(self) -> dict:
        """Parse the entire file as a top-level block."""
        result = {}
        while self.peek() is not None:
            key = self.consume()
            if self.peek() == '=':
                self.consume()  # skip optional '='
                value = self.parse_value()
            elif self.peek() == '{':
                # Clausewitz allows `key { }` without '=' (e.g. named_colors)
                value = self.parse_value()
            else:
                continue  # bare key with no value, skip
            if key in result:
                if not isinstance(result[key], list):
                    result[key] = [result[key]]
                result[key].append(value)
            else:
                result[key] = value
        return result


def parse_file(path: Path) -> dict:
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    tokens = tokenize(text)
    return Parser(tokens).parse()


# ---------------------------------------------------------------------------
# Schema & Writer
# ---------------------------------------------------------------------------

SCHEMA = """\
CREATE TABLE IF NOT EXISTS dlc (
    name             TEXT PRIMARY KEY,   -- matches host_has_dlc values, e.g. "Toxoids Species Pack"
    steam_id         TEXT,
    affects_gameplay INTEGER DEFAULT 0   -- 0/1
);

CREATE TABLE IF NOT EXISTS species_class (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    key                  TEXT UNIQUE NOT NULL,   -- e.g. "MAM"
    label                TEXT,                   -- display name from localisation, e.g. "Mammalian"
    archetype            TEXT,                   -- e.g. "BIOLOGICAL"
    graphical_culture    TEXT,                   -- e.g. "mammalian_01"
    move_pop_sound_effect TEXT,
    portrait_modding      INTEGER DEFAULT 1,     -- boolean (0/1), default yes per game docs
    possible_block       TEXT,                   -- raw text of the possible condition
    source_file          TEXT
);

CREATE TABLE IF NOT EXISTS portrait_set (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    set_key           TEXT NOT NULL,             -- e.g. "mammalians"
    species_class_key TEXT,                      -- e.g. "MAM" (from species_class = MAM)
    portrait_id       TEXT NOT NULL,             -- e.g. "mam1", "rep3"
    required_dlc      TEXT,                      -- NULL = base game; FK to dlc.name
    source_file       TEXT,
    UNIQUE(set_key, portrait_id),
    FOREIGN KEY(species_class_key) REFERENCES species_class(key),
    FOREIGN KEY(required_dlc)      REFERENCES dlc(name)
);

CREATE TABLE IF NOT EXISTS species_class_attribute (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    species_class_key TEXT NOT NULL,
    attribute_key     TEXT NOT NULL,
    attribute_value   TEXT NOT NULL,
    FOREIGN KEY(species_class_key) REFERENCES species_class(key)
);

CREATE TABLE IF NOT EXISTS trait (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT UNIQUE NOT NULL,
    cost         INTEGER,               -- base point cost; NULL if not specified
    category     TEXT,                  -- normal, cyborg, robotic, psionic, advanced_genetic, …
    initial      INTEGER DEFAULT 1,     -- 0=no, 1=yes; whether selectable at species creation
    randomized   INTEGER DEFAULT 1,     -- 0=no, 1=yes
    hidden       INTEGER DEFAULT 0,     -- 0=no, 1=yes
    required_dlc TEXT,                  -- NULL = base game; FK to dlc.name
    source_file  TEXT,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE IF NOT EXISTS trait_archetype (
    trait_key TEXT NOT NULL,
    archetype TEXT NOT NULL,          -- BIOLOGICAL, LITHOID, ROBOT, MACHINE
    PRIMARY KEY (trait_key, archetype),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE IF NOT EXISTS trait_species_class (
    trait_key         TEXT NOT NULL,
    species_class_key TEXT NOT NULL,  -- e.g. LITHOID, PSIONIC, PLANT
    PRIMARY KEY (trait_key, species_class_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE IF NOT EXISTS trait_opposite (
    trait_key    TEXT NOT NULL,
    opposite_key TEXT NOT NULL,       -- mutually exclusive trait
    PRIMARY KEY (trait_key, opposite_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE IF NOT EXISTS trait_tag (
    trait_key TEXT NOT NULL,
    tag       TEXT NOT NULL,          -- e.g. organic, positive, pop_output, research
    PRIMARY KEY (trait_key, tag),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE IF NOT EXISTS species_archetype (
    key                     TEXT PRIMARY KEY,
    trait_points            INTEGER,   -- base point budget for species creation
    max_traits              INTEGER,   -- pick limit (cost=0 traits don't count)
    robotic                 INTEGER DEFAULT 0,
    inherit_trait_points_from TEXT,    -- resolved before insert; kept for reference
    base_trait              TEXT,      -- canonical always-assigned trait (cost 0, free pick slot)
    source_file             TEXT,
    FOREIGN KEY (inherit_trait_points_from) REFERENCES species_archetype(key)
);

CREATE TABLE IF NOT EXISTS named_color (
    key         TEXT PRIMARY KEY,  -- e.g. "trait_bg_psionic"
    r           INTEGER NOT NULL,
    g           INTEGER NOT NULL,
    b           INTEGER NOT NULL,
    a           INTEGER NOT NULL DEFAULT 255,
    source_file TEXT
);

CREATE TABLE IF NOT EXISTS trait_category (
    key           TEXT PRIMARY KEY,  -- matches trait.category, e.g. "psionic"
    label         TEXT NOT NULL,     -- display name, e.g. "Psionic Traits"
    color_key     TEXT,              -- FK to named_color.key, e.g. "trait_bg_psionic"
    display_order INTEGER NOT NULL DEFAULT 99,
    FOREIGN KEY (color_key) REFERENCES named_color(key)
);
"""


def _sql_str(v) -> str:
    """Return an escaped SQL string literal."""
    return "'" + str(v).replace("'", "''") + "'"


def _sql_int(v) -> str:
    return str(int(v))


class Writer:
    """
    Abstracts SQL output: either to a .sql file or directly to SQLite.
    Both modes can be active simultaneously.
    """

    def __init__(self, sql_path: str | None, sqlite_path: str | None, stdout: bool = False):
        self._buf = StringIO() if (sql_path or stdout) else None
        self._sql_path = sql_path
        self._stdout = stdout
        self._conn = None

        if sqlite_path:
            parent = Path(sqlite_path).parent
            if not parent.exists():
                raise SystemExit(f"Error: directory does not exist: {parent}")
            self._conn = sqlite3.connect(sqlite_path)

        # Write schema to both targets
        if self._buf is not None:
            self._buf.write("-- Generated by stellaris-extract.py\n")
            self._buf.write("-- Schema\n\n")
            self._buf.write(SCHEMA)
            self._buf.write("\n-- Data\n\n")
        if self._conn is not None:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        """Execute an INSERT into both the SQL buffer and SQLite."""
        if self._buf is not None:
            rendered = self._render(sql.strip(), params)
            self._buf.write(rendered + ";\n")
        if self._conn is not None:
            self._conn.execute(sql, params)

    def _render(self, sql: str, params: tuple) -> str:
        """Replace ? placeholders with escaped literals for the SQL file."""
        parts = sql.split('?')
        result = parts[0]
        for val, part in zip(params, parts[1:]):
            if val is None:
                result += 'NULL'
            elif isinstance(val, int):
                result += _sql_int(val)
            else:
                result += _sql_str(val)
            result += part
        return result

    def commit(self):
        if self._conn is not None:
            self._conn.commit()

    def close(self):
        if self._buf is not None:
            content = self._buf.getvalue()
            if self._stdout:
                sys.stdout.write(content)
            else:
                Path(self._sql_path).write_text(content, encoding='utf-8')
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()


# ---------------------------------------------------------------------------
# Extraction: DLC metadata
# ---------------------------------------------------------------------------

def extract_dlc(data: dict, w: Writer, verbose: bool):
    dlcs = data.get('dlcs', {})
    if not isinstance(dlcs, dict):
        return
    for _key, dlc_data in dlcs.items():
        if not isinstance(dlc_data, dict):
            continue
        name             = dlc_data.get('name')
        steam_id         = dlc_data.get('steam_id') or None
        affects_gameplay = 1 if dlc_data.get('affects_gameplay', 'no') == 'yes' else 0
        if not name:
            continue
        w.execute("""
            INSERT OR IGNORE INTO dlc (name, steam_id, affects_gameplay)
            VALUES (?, ?, ?)
        """, (name, steam_id, affects_gameplay))
        if verbose:
            print(f"  DLC: {name} | steam_id: {steam_id} | gameplay: {affects_gameplay}")
    w.commit()


# ---------------------------------------------------------------------------
# Trait category configuration
# The category→color mapping is derived from common/inline_scripts/trait/
# (e.g. icon_psionic.txt references trait_bg_psionic). There is no single
# game file that encodes this mapping explicitly for all categories, so it
# is defined here as authoritative configuration.
# ---------------------------------------------------------------------------

TRAIT_CATEGORIES: list[tuple[str, str, str, int]] = [
    # (key,          label,               color_key,              display_order)
    ('normal',    'Species Traits',    'trait_bg_default',      0),
    ('phenotype', 'Phenotype Traits',  'trait_bg_society',      1),
    ('robotic',   'Synthetic Traits',  'trait_bg_machine',      2),
    ('psionic',   'Psionic Traits',    'trait_bg_psionic',      3),
    ('overtuned', 'Overtuned Traits',  'trait_bg_engineering',  4),
    ('cyborg',    'Cybernetic Traits', 'trait_bg_physics',      5),
    ('malleable', 'Malleable Traits',  'trait_bg_specialist',   6),
    ('mutation',  'Mutation Traits',   'trait_bg_negative',     7),
]


def extract_trait_categories(w: Writer, verbose: bool):
    for key, label, color_key, order in TRAIT_CATEGORIES:
        w.execute("""
            INSERT OR REPLACE INTO trait_category (key, label, color_key, display_order)
            VALUES (?, ?, ?, ?)
        """, (key, label, color_key, order))
        if verbose:
            print(f"  Category: {key} | {label} | {color_key}")
    w.commit()


# ---------------------------------------------------------------------------
# Localisation parsing (for species class display names)
# ---------------------------------------------------------------------------

_LOC_ENTRY = re.compile(r'^\s+(\w[\w_]*):(\d*)\s+"([^"]*)"', re.MULTILINE)


_LOC_REF = re.compile(r'^\$(\w+)\$$')


def load_localisation(loc_dir: Path) -> dict[str, str]:
    """
    Parse all .yml localisation files, returning {key: label}.
    When the same key appears multiple times, the entry with the lowest
    version number wins (e.g. :0 beats :1). Single-token $KEY$ references
    are resolved one level deep; unresolvable references are dropped (NULL).
    """
    best: dict[str, tuple[int, str]] = {}  # key → (version, label)
    for f in sorted(loc_dir.rglob('*.yml')):
        try:
            text = f.read_text(encoding='utf-8-sig', errors='replace')
        except Exception:
            continue
        for m in _LOC_ENTRY.finditer(text):
            key, ver_str, label = m.group(1), m.group(2), m.group(3)
            ver = int(ver_str) if ver_str else 0
            if key not in best or ver < best[key][0]:
                best[key] = (ver, label)
    labels = {k: lbl for k, (_, lbl) in best.items()}
    # Resolve single-token $KEY$ references one level deep
    for key, lbl in list(labels.items()):
        ref = _LOC_REF.match(lbl)
        if ref:
            resolved = labels.get(ref.group(1))
            if resolved and not _LOC_REF.match(resolved):
                labels[key] = resolved
            else:
                del labels[key]  # unresolvable — fall back to NULL in DB
    return labels


# ---------------------------------------------------------------------------
# Extraction: species_classes
# ---------------------------------------------------------------------------

KNOWN_TOP_KEYS = {
    'archetype', 'graphical_culture', 'move_pop_sound_effect',
    'portrait_modding', 'possible',
    'species_name', 'species_female_name', 'species_plural',
    'species_adjective', 'species_female_adjective',
    'species_name_noun', 'species_female_name_noun',
    'gender', 'playable', 'randomized',
    'pops_will_join', 'pops_will_not_join',
    'default_names', 'portrait_groups',
}

# Keys handled explicitly and excluded from the generic attribute table
EXTRACTED_KEYS = {
    'archetype', 'graphical_culture', 'move_pop_sound_effect',
    'portrait_modding', 'possible',
}


def extract_species_classes(data: dict, source_file: str, loc: dict[str, str],
                             w: Writer, verbose: bool):
    for class_key, class_data in data.items():
        if not isinstance(class_data, dict):
            continue

        label             = loc.get(class_key)
        archetype         = class_data.get('archetype')
        graphical_culture = class_data.get('graphical_culture')
        move_pop_sound    = class_data.get('move_pop_sound_effect')
        portrait_modding  = 0 if class_data.get('portrait_modding', 'yes') == 'no' else 1
        possible_raw      = str(class_data.get('possible', ''))

        w.execute("""
            INSERT OR REPLACE INTO species_class
                (key, label, archetype, graphical_culture, move_pop_sound_effect,
                 portrait_modding, possible_block, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (class_key, label, archetype, graphical_culture, move_pop_sound,
              portrait_modding, possible_raw, source_file))

        if verbose:
            print(f"  Class: {class_key} | archetype: {archetype} | culture: {graphical_culture}")

        # All remaining fields go into the generic attribute table
        for attr_key, attr_val in class_data.items():
            if attr_key in EXTRACTED_KEYS:
                continue
            if isinstance(attr_val, (dict, list)):
                attr_val = str(attr_val)
            w.execute("""
                INSERT INTO species_class_attribute (species_class_key, attribute_key, attribute_value)
                VALUES (?, ?, ?)
            """, (class_key, attr_key, str(attr_val)))

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: portrait_sets
# ---------------------------------------------------------------------------

def _portrait_set_insert(w: Writer, set_key: str, species_class_key, portrait_id: str,
                         required_dlc, source_file: str):
    w.execute("""
        INSERT OR IGNORE INTO portrait_set
            (set_key, species_class_key, portrait_id, required_dlc, source_file)
        VALUES (?, ?, ?, ?, ?)
    """, (set_key, species_class_key, portrait_id, required_dlc, source_file))


def _dlc_from_condition(cond_block: dict) -> str | None:
    """Extract host_has_dlc from a randomizable/playable sub-block."""
    for sub_key in ('randomizable', 'playable'):
        sub = cond_block.get(sub_key)
        if isinstance(sub, dict):
            dlc = sub.get('host_has_dlc')
            if dlc:
                return dlc
    return None


def extract_portrait_sets(data: dict, source_file: str, w: Writer, verbose: bool):
    for set_key, set_data in data.items():
        if not isinstance(set_data, dict):
            continue

        species_class_key = set_data.get('species_class') or None

        # Base portraits (no DLC required)
        base_portraits = _to_list(set_data.get('portraits'))
        for pid in base_portraits:
            _portrait_set_insert(w, set_key, species_class_key, pid, None, source_file)

        # DLC-gated portraits inside conditional_portraits blocks
        cond_raw = set_data.get('conditional_portraits')
        cond_blocks = _to_list(cond_raw) if cond_raw else []
        for block in cond_blocks:
            if not isinstance(block, dict):
                continue
            required_dlc = _dlc_from_condition(block)
            for pid in _to_list(block.get('portraits')):
                _portrait_set_insert(w, set_key, species_class_key, pid, required_dlc, source_file)

        if verbose:
            n_base = len(base_portraits)
            n_cond = sum(len(_to_list(b.get('portraits'))) for b in cond_blocks if isinstance(b, dict))
            print(f"  Set: {set_key} | class: {species_class_key} | {n_base} base + {n_cond} conditional portrait(s)")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: traits
# ---------------------------------------------------------------------------

# Skip leader traits and documentation files — only species trait files wanted
_LEADER_FILE_TOKENS = {
    'leader', 'admiral', 'general', 'governor', 'scientist',
    'ambassador', 'delegate', 'paragon', 'subclass', 'npc_crisis',
    'starting_ruler', 'documentation',
}

def is_species_trait_file(path: Path) -> bool:
    name = path.stem.lower()
    return not any(tok in name for tok in _LEADER_FILE_TOKENS)


def _parse_cost(cost_raw) -> int | None:
    if isinstance(cost_raw, str):
        try:
            return int(cost_raw)
        except ValueError:
            return None
    if isinstance(cost_raw, dict):
        base = cost_raw.get('base')
        try:
            return int(base) if base is not None else None
        except (ValueError, TypeError):
            return None
    return None


def _to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        # Flatten one level: duplicate keys in source files produce nested lists
        result = []
        for item in value:
            if isinstance(item, list):
                result.extend(item)
            else:
                result.append(item)
        return result
    if isinstance(value, dict):
        return list(value.keys())
    return []


def extract_traits(data: dict, source_file: str, w: Writer, verbose: bool):
    for trait_key, trait_data in data.items():
        if not isinstance(trait_data, dict):
            continue

        cost         = _parse_cost(trait_data.get('cost'))
        category     = trait_data.get('category')
        initial      = 0 if trait_data.get('initial',    'yes') == 'no' else 1
        randomized   = 0 if trait_data.get('randomized', 'yes') == 'no' else 1
        hidden       = 1 if trait_data.get('hidden',     'no')  == 'yes' else 0
        required_dlc = trait_data.get('host_has_dlc') or None

        w.execute("""
            INSERT OR IGNORE INTO trait
                (key, cost, category, initial, randomized, hidden, required_dlc, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (trait_key, cost, category, initial, randomized, hidden, required_dlc, source_file))

        for arch in _to_list(trait_data.get('allowed_archetypes')):
            w.execute(
                "INSERT OR IGNORE INTO trait_archetype (trait_key, archetype) VALUES (?, ?)",
                (trait_key, arch))

        for sc in _to_list(trait_data.get('species_class')):
            w.execute(
                "INSERT OR IGNORE INTO trait_species_class (trait_key, species_class_key) VALUES (?, ?)",
                (trait_key, sc))

        for opp in _to_list(trait_data.get('opposites')):
            w.execute(
                "INSERT OR IGNORE INTO trait_opposite (trait_key, opposite_key) VALUES (?, ?)",
                (trait_key, opp))

        for tag in _to_list(trait_data.get('tags')):
            w.execute(
                "INSERT OR IGNORE INTO trait_tag (trait_key, tag) VALUES (?, ?)",
                (trait_key, tag))

        if verbose:
            archs = _to_list(trait_data.get('allowed_archetypes'))
            print(f"  Trait: {trait_key} | cost: {cost} | archetypes: {archs}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: species_archetypes
# ---------------------------------------------------------------------------

# ROBOT has no dedicated portrait class in the game files; its base trait is
# assigned by game code rather than a species_class entry.
_ROBOT_BASE_TRAIT = 'trait_mechanical'


def extract_species_archetypes(
    data: dict,
    source_file: str,
    archetype_base_traits: dict[str, str],
    w: Writer,
    verbose: bool,
):
    # Collect @variable definitions (top-level keys starting with @)
    variables: dict[str, int] = {}
    for key, val in data.items():
        if key.startswith('@'):
            try:
                variables[key] = int(val)
            except (ValueError, TypeError):
                pass

    def resolve_int(raw) -> int | None:
        if raw is None:
            return None
        s = str(raw)
        if s.startswith('@'):
            return variables.get(s)
        try:
            return int(s)
        except ValueError:
            return None

    # First pass: parse each archetype block
    arch_values: dict[str, dict] = {}
    for arch_key, arch_data in data.items():
        if arch_key.startswith('@') or not isinstance(arch_data, dict):
            continue
        trait_points = resolve_int(arch_data.get('species_trait_points'))
        max_traits   = resolve_int(arch_data.get('species_max_traits'))
        robotic      = 1 if arch_data.get('robotic') == 'yes' else 0
        inherit_from = arch_data.get('inherit_trait_points_from') or None
        base_trait   = archetype_base_traits.get(arch_key)
        if base_trait is None and arch_key == 'ROBOT':
            base_trait = _ROBOT_BASE_TRAIT
        arch_values[arch_key] = {
            'trait_points': trait_points,
            'max_traits':   max_traits,
            'robotic':      robotic,
            'inherit_from': inherit_from,
            'base_trait':   base_trait,
        }

    # Second pass: resolve inheritance (single level is enough for vanilla)
    for av in arch_values.values():
        parent = arch_values.get(av['inherit_from'] or '') if av['inherit_from'] else None
        if parent:
            if av['trait_points'] is None:
                av['trait_points'] = parent['trait_points']
            if av['max_traits'] is None:
                av['max_traits'] = parent['max_traits']
            if av['base_trait'] is None:
                av['base_trait'] = parent['base_trait']

    for arch_key, av in arch_values.items():
        w.execute("""
            INSERT OR REPLACE INTO species_archetype
                (key, trait_points, max_traits, robotic,
                 inherit_trait_points_from, base_trait, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (arch_key, av['trait_points'], av['max_traits'], av['robotic'],
              av['inherit_from'], av['base_trait'], source_file))
        if verbose:
            print(f"  Archetype: {arch_key} | pts: {av['trait_points']} "
                  f"| max: {av['max_traits']} | robotic: {av['robotic']} "
                  f"| base: {av['base_trait']}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: named_colors
# ---------------------------------------------------------------------------

def extract_named_colors(data: dict, source_file: str, w: Writer, verbose: bool):
    for color_key, color_data in data.items():
        if not isinstance(color_data, dict):
            continue
        color_vals = _to_list(color_data.get('color'))
        if len(color_vals) < 3:
            continue
        try:
            r, g, b = int(color_vals[0]), int(color_vals[1]), int(color_vals[2])
            a = int(color_vals[3]) if len(color_vals) > 3 else 255
        except (ValueError, TypeError):
            continue
        w.execute("""
            INSERT OR REPLACE INTO named_color (key, r, g, b, a, source_file)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (color_key, r, g, b, a, source_file))
        if verbose:
            print(f"  Color: {color_key} | rgba({r},{g},{b},{a})")
    w.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Stellaris Data Extractor -> SQL / SQLite',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Print SQL to stdout
  python3 stellaris-extract.py /path/to/Stellaris

  # Write SQL to a file
  python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql

  # Write directly to SQLite
  python3 stellaris-extract.py /path/to/Stellaris --sqlite stellaris.sqlite

  # Both at once
  python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql --sqlite stellaris.sqlite
"""
    )
    ap.add_argument('install_dir',
                    help='Path to the Stellaris installation directory')
    ap.add_argument('--out', default=None, metavar='FILE',
                    help='Write SQL statements to this file')
    ap.add_argument('--sqlite', metavar='FILE', default=None,
                    help='Write directly to this SQLite database')
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='Verbose output')
    args = ap.parse_args()

    install_dir = Path(args.install_dir)
    if not install_dir.exists():
        print(f"ERROR: directory not found: {install_dir}", file=sys.stderr)
        sys.exit(1)

    dlc_info       = install_dir / 'dlc_metadata' / 'dlc_info.txt'
    species_dir    = install_dir / 'common' / 'species_classes'
    archetypes_dir = install_dir / 'common' / 'species_archetypes'
    portrait_dir   = install_dir / 'common' / 'portrait_sets'
    traits_dir     = install_dir / 'common' / 'traits'
    colors_dir     = install_dir / 'common' / 'named_colors'
    loc_dir        = install_dir / 'localisation' / 'english'

    if not species_dir.exists():
        print(f"ERROR: {species_dir} not found -- is this really the Stellaris directory?",
              file=sys.stderr)
        sys.exit(1)

    sql_path    = args.out
    sqlite_path = args.sqlite
    stdout_mode = not sql_path and not sqlite_path

    # In stdout mode progress goes to stderr so SQL output stays clean
    progress = sys.stderr if stdout_mode else sys.stdout

    if sql_path:
        print(f"SQL output   : {sql_path}", file=progress)
    if sqlite_path:
        print(f"SQLite DB    : {sqlite_path}", file=progress)

    w = Writer(sql_path, sqlite_path, stdout=stdout_mode)

    # --- DLC metadata ---
    print(f"\n[0/5] DLC metadata", file=progress)
    if dlc_info.exists():
        try:
            data = parse_file(dlc_info)
            extract_dlc(data, w, args.verbose)
            if not args.verbose:
                n = sum(1 for v in data.get('dlcs', {}).values() if isinstance(v, dict))
                print(f"  Loaded {n} DLC entries", file=progress)
        except Exception as e:
            print(f"  WARNING: failed to parse dlc_info.txt: {e}", file=sys.stderr)
    else:
        print(f"  dlc_info.txt not found, skipping", file=progress)

    # --- species_classes (+ localisation labels) ---
    sc_files = sorted(species_dir.glob('*.txt'))
    print(f"\n[1/5] species_classes: {len(sc_files)} file(s)", file=progress)
    loc: dict[str, str] = {}
    if loc_dir.exists():
        loc_files = list(loc_dir.rglob('*.yml'))
        print(f"  Localisation: {len(loc_files)} file(s) in {loc_dir.name}/", file=progress)
        loc = load_localisation(loc_dir)
    else:
        print(f"  WARNING: localisation dir not found: {loc_dir}", file=progress)
    archetype_base_traits: dict[str, str] = {'ROBOT': _ROBOT_BASE_TRAIT}
    for f in sc_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            # Collect archetype → base_trait before writing to DB
            for class_data in data.values():
                if isinstance(class_data, dict):
                    arch  = class_data.get('archetype')
                    trait = class_data.get('trait')
                    if arch and trait and arch not in archetype_base_traits:
                        archetype_base_traits[arch] = str(trait).strip('"')
            extract_species_classes(data, f.name, loc, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- species_archetypes ---
    sa_files = sorted(archetypes_dir.glob('*.txt')) if archetypes_dir.exists() else []
    print(f"\n[2/5] species_archetypes: {len(sa_files)} file(s)", file=progress)
    for f in sa_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_species_archetypes(data, f.name, archetype_base_traits, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- portrait_sets ---
    ps_files = sorted(portrait_dir.glob('*.txt')) if portrait_dir.exists() else []
    print(f"\n[3/5] portrait_sets: {len(ps_files)} file(s)", file=progress)
    for f in ps_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_portrait_sets(data, f.name, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- traits ---
    tr_files = sorted(
        f for f in traits_dir.glob('*.txt') if is_species_trait_file(f)
    ) if traits_dir.exists() else []
    print(f"\n[4/5] traits: {len(tr_files)} file(s)", file=progress)
    for f in tr_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_traits(data, f.name, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- named colors + trait categories ---
    co_files = sorted(colors_dir.glob('*.txt')) if colors_dir.exists() else []
    print(f"\n[5/5] named_colors: {len(co_files)} file(s)", file=progress)
    for f in co_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_named_colors(data, f.name, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    # trait_category references named_color (FK), so it runs after colors are loaded
    print(f"  Writing {len(TRAIT_CATEGORIES)} trait categories", file=progress)
    extract_trait_categories(w, args.verbose)

    w.close()

    # --- Summary ---
    if sqlite_path:
        tmp = sqlite3.connect(sqlite_path)
        n_dlcs          = tmp.execute("SELECT COUNT(*) FROM dlc").fetchone()[0]
        n_classes       = tmp.execute("SELECT COUNT(*) FROM species_class").fetchone()[0]
        n_archetypes    = tmp.execute("SELECT COUNT(*) FROM species_archetype").fetchone()[0]
        n_sets          = tmp.execute("SELECT COUNT(DISTINCT set_key) FROM portrait_set").fetchone()[0]
        n_portraits     = tmp.execute("SELECT COUNT(*) FROM portrait_set WHERE required_dlc IS NULL").fetchone()[0]
        n_portraits_dlc = tmp.execute("SELECT COUNT(*) FROM portrait_set WHERE required_dlc IS NOT NULL").fetchone()[0]
        n_attrs         = tmp.execute("SELECT COUNT(*) FROM species_class_attribute").fetchone()[0]
        n_traits        = tmp.execute("SELECT COUNT(*) FROM trait WHERE required_dlc IS NULL").fetchone()[0]
        n_traits_dlc    = tmp.execute("SELECT COUNT(*) FROM trait WHERE required_dlc IS NOT NULL").fetchone()[0]
        n_colors        = tmp.execute("SELECT COUNT(*) FROM named_color").fetchone()[0]
        n_categories    = tmp.execute("SELECT COUNT(*) FROM trait_category").fetchone()[0]
        n_labeled       = tmp.execute("SELECT COUNT(*) FROM species_class WHERE label IS NOT NULL").fetchone()[0]
        tmp.close()
        print(f"""
+------------------------------------------+
|  Extraction complete                     |
+------------------------------------------+
|  DLCs               : {n_dlcs:<20}|
|  Species classes    : {n_classes:<20}|
|    with labels      : {n_labeled:<20}|
|  Species archetypes : {n_archetypes:<20}|
|  Portrait sets      : {n_sets:<20}|
|  Portraits (base)   : {n_portraits:<20}|
|  Portraits (DLC)    : {n_portraits_dlc:<20}|
|  Attributes         : {n_attrs:<20}|
|  Traits (base)      : {n_traits:<20}|
|  Traits (DLC)       : {n_traits_dlc:<20}|
|  Named colors       : {n_colors:<20}|
|  Trait categories   : {n_categories:<20}|
+------------------------------------------+""", file=progress)
    elif sql_path:
        sql_size = Path(sql_path).stat().st_size
        print(f"\nDone. SQL file: {sql_path} ({sql_size:,} bytes)", file=progress)
    else:
        print("\nDone.", file=progress)


if __name__ == '__main__':
    main()
