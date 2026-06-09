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

_LOC_INLINE_REF = re.compile(r'\$(\w+)\$')
_LOC_FMT_CODE   = re.compile(r'§.|£\w+£')
# Scripted-loc concept tag: ['concept_key'] or ['concept_key', inline display text].
_LOC_CONCEPT    = re.compile(r"\['(\w+)'(?:,\s*([^\]]*))?\]")
# Any remaining bracket command, e.g. [This.GetName] — not statically resolvable.
_LOC_COMMAND    = re.compile(r'\[[^\]]*\]')

def _clean_loc(s: str, loc: dict[str, str]) -> str:
    """Expand $KEY$ refs and ['concept', …] scripted-loc tags (up to 4 rounds),
    then strip §X/£icon£ format codes and any leftover [command] tags."""
    # Concept tags: keep the inline display text when given, else fall back to the
    # concept's own name ($concept_KEY$, resolved by the loop below).
    s = _LOC_CONCEPT.sub(lambda m: m.group(2) if m.group(2) else f'${m.group(1)}$', s)
    for _ in range(4):
        expanded = _LOC_INLINE_REF.sub(lambda m: loc.get(m.group(1), m.group(0)), s)
        if expanded == s:
            break
        s = expanded
    s = _LOC_COMMAND.sub('', s)  # drop leftover [Command.Chain] tags
    s = re.sub(r'\\[nrt]', ' ', s)  # literal \n \t \r escapes -> space
    return re.sub(r'\s+', ' ', _LOC_FMT_CODE.sub('', s)).strip()


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

            if key == '{':
                # Anonymous sub-block (e.g. in_breach_of = { { key = X } { key = Y } })
                # Recurse to consume its content and closing '}', then discard.
                self.parse_block()
                if self.peek() == '}':
                    self.consume()
            elif self.peek() == '=':
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

CREATE TABLE IF NOT EXISTS trait_modifier (
    trait_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trait_key, modifier_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

DROP TABLE IF EXISTS modifier_label;
CREATE TABLE modifier_label (
    modifier_key     TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    positive_is_good INTEGER NOT NULL DEFAULT 1  -- 0 when a higher value is worse for the empire
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

DROP TABLE IF EXISTS ethic_policy_restriction;
DROP TABLE IF EXISTS ethic_tag;
DROP TABLE IF EXISTS ethic_modifier;
DROP TABLE IF EXISTS ethic;
CREATE TABLE ethic (
    key             TEXT PRIMARY KEY,   -- e.g. "ethic_authoritarian"
    label           TEXT,               -- from localisation, e.g. "Authoritarian"
    cost            INTEGER NOT NULL DEFAULT 1,  -- 1 = regular, 2 = fanatic
    category        TEXT,               -- "col" for normal empire ethics
    fanatic_variant TEXT,               -- key of the fanatic variant (if this is regular)
    regular_variant TEXT,               -- key of the regular variant (if this is fanatic)
    gestalt         INTEGER DEFAULT 0   -- 1 for ethic_gestalt_consciousness
);

CREATE TABLE ethic_modifier (
    ethic_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ethic_key, modifier_key),
    FOREIGN KEY (ethic_key) REFERENCES ethic(key)
);

DROP TABLE IF EXISTS ethic_tag_label;
CREATE TABLE ethic_tag_label (
    tag   TEXT PRIMARY KEY,
    label TEXT NOT NULL
);

CREATE TABLE ethic_tag (
    ethic_key  TEXT NOT NULL,
    tag        TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ethic_key, tag),
    FOREIGN KEY (ethic_key) REFERENCES ethic(key)
);

CREATE TABLE ethic_policy_restriction (
    policy_key   TEXT NOT NULL,   -- e.g. "interference"
    option_key   TEXT NOT NULL,   -- e.g. "interference_aggressive"
    option_label TEXT,            -- localised label, e.g. "Aggressive Interference"
    ethic_key    TEXT NOT NULL,   -- e.g. "ethic_xenophile"
    PRIMARY KEY (policy_key, option_key, ethic_key),
    FOREIGN KEY (ethic_key) REFERENCES ethic(key)
);

-- Precursors: the ancient civilisations whose archaeology chains grant rewards.
-- The precursor<->reward mapping is spread across event files (no single source
-- file joins them), so the spine is the hand-curated PRECURSORS config; the
-- effect numbers and display names below are pulled from the game files.
DROP TABLE IF EXISTS precursor_building;
DROP TABLE IF EXISTS precursor_reward_modifier;
DROP TABLE IF EXISTS precursor_relic_modifier;
DROP TABLE IF EXISTS precursor_relic;
DROP TABLE IF EXISTS precursor;
CREATE TABLE precursor (
    key                TEXT PRIMARY KEY,   -- e.g. "precursor_1", "precursor_baol_1"
    name               TEXT,               -- localised, e.g. "Vultaum"
    required_dlc       TEXT,               -- NULL = base game; FK to dlc.name
    system_initializer TEXT,               -- home system, e.g. "vultaumar_system"
    picture            TEXT,               -- GFX_precursor_*
    num_digsites       INTEGER,            -- archaeological_site_type count in the chain
    sort_order         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE precursor_relic (
    precursor_key TEXT PRIMARY KEY,        -- 0..1 relic per precursor (First League/Irassian have none)
    relic_key     TEXT NOT NULL,           -- e.g. "r_reality_perforator"
    name          TEXT,                    -- localised relic name, e.g. "Vultaum Reality Perforator"
    portrait      TEXT,                    -- GFX_relic_*
    score         INTEGER,                 -- relic score value
    description   TEXT,                    -- flavour text (<relic_key>_desc)
    active_effect TEXT,                    -- localised active-ability tooltip
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

-- Relic passive effects (triggered_country_modifier). condition holds the
-- 'potential' qualifier when the passive only applies to some empire types.
CREATE TABLE precursor_relic_modifier (
    relic_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    condition      TEXT,                   -- NULL = always; else e.g. "is_machine_empire = yes"
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (relic_key, modifier_key, condition),
    FOREIGN KEY (relic_key) REFERENCES precursor_relic(relic_key)
);

-- The permanent empire modifier granted for completing the chain. Vultaum has
-- several variants (player choice + empire type); 'variant' distinguishes them.
CREATE TABLE precursor_reward_modifier (
    precursor_key  TEXT NOT NULL,
    static_modifier TEXT NOT NULL,         -- e.g. "artifact_cybrex_research_completed"
    variant        TEXT NOT NULL DEFAULT 'default',
    name           TEXT,                   -- localised modifier name (if any)
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (precursor_key, static_modifier, modifier_key),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

CREATE TABLE precursor_building (
    precursor_key TEXT NOT NULL,
    building_key  TEXT NOT NULL,           -- e.g. "building_zroni_equilibrator"
    name          TEXT,                    -- localised building name
    scope         TEXT,                    -- "planet" or "starbase"
    PRIMARY KEY (precursor_key, building_key),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

-- Convenience header view: one row per precursor with its (0..1) relic and
-- building pre-joined. The multi-row effects stay in their child tables —
-- join precursor_relic_modifier on relic_key, precursor_reward_modifier on key.
DROP VIEW IF EXISTS precursor_full;
CREATE VIEW precursor_full AS
SELECT
    p.key, p.name, p.required_dlc, p.system_initializer, p.num_digsites, p.picture,
    pr.relic_key, pr.name AS relic_name, pr.score AS relic_score,
    pr.description AS relic_description, pr.active_effect AS relic_active_effect,
    pb.building_key, pb.name AS building_name, pb.scope AS building_scope
FROM precursor p
LEFT JOIN precursor_relic    pr ON pr.precursor_key = p.key
LEFT JOIN precursor_building pb ON pb.precursor_key = p.key
ORDER BY p.sort_order;
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
# Extraction: trait modifiers (flat modifier {} block only — Phase 1)
# ---------------------------------------------------------------------------

# Modifiers where a higher (more positive) value is worse for the empire/species.
# Extend this set whenever a new inverted modifier is discovered.
# Modifier keys whose loc key doesn't follow the automatic MOD_<UPPER> / mod_<lower> pattern.
# Maps modifier_key → explicit loc key. Extend when new mismatches are discovered.
MODIFIER_LOC_OVERRIDES: dict[str, str] = {
    'species_leader_exp_gain': 'MOD_LEADER_SPECIES_EXP_GAIN',
}

INVERTED_MODIFIERS: frozenset[str] = frozenset({
    # Upkeep / resource costs
    'leaders_upkeep_mult',
    'planet_pops_consumer_goods_upkeep_mult',
    'planet_pops_organics_alloys_upkeep_mult',
    'planet_pops_organics_food_upkeep_mult',
    'planet_pops_organics_minerals_upkeep_mult',
    'planet_pops_robotics_upkeep_mult',
    'planet_pops_upkeep_mult',
    'pop_resettlement_cost_mult',
    # Resource consumption
    'pop_amenities_usage_add',
    'pop_housing_usage_mult',
    # Administrative burden / empire size
    'species_empire_size_mult',
    'empire_size_pops_mult',
    # Influence costs
    'country_claim_influence_cost_mult',
    'country_starbase_influence_cost_mult',
    # Edict costs
    'edicts_upkeep_mult',
    'edicts_cost_mult',
    # War / exhaustion
    'country_war_exhaustion_mult',
    # Combat (taking more damage / longer cooldowns / higher costs = worse)
    'army_damage_taken_mult',
    'gravity_snare_cooldown_mult',
    'gravity_snare_cost_mult',
    # Trait slot counts (more negative trait slots allowed = worse)
    'negative_leader_traits_species',
})


def extract_trait_modifiers(data: dict, source_file: str, loc: dict[str, str],
                             w: Writer, verbose: bool):
    # Collect @variable definitions (float) from this file's top level
    variables: dict[str, float] = {}
    for key, val in data.items():
        if key.startswith('@'):
            try:
                variables[key] = float(val)
            except (ValueError, TypeError):
                pass

    def resolve_float(raw) -> float | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if s.startswith('@'):
            return variables.get(s)
        try:
            return float(s)
        except ValueError:
            return None

    for trait_key, trait_data in data.items():
        if trait_key.startswith('@') or not isinstance(trait_data, dict):
            continue

        modifier = trait_data.get('modifier')
        if modifier is None:
            continue

        # Multiple modifier = {} blocks in one trait get promoted to a list by the parser
        if isinstance(modifier, list):
            merged: dict = {}
            for m in modifier:
                if isinstance(m, dict):
                    merged.update(m)
            modifier = merged
        if not isinstance(modifier, dict):
            continue

        for order, (mod_key, mod_val) in enumerate(modifier.items()):
            # Duplicate values within one block are unusual; take the last
            if isinstance(mod_val, list):
                mod_val = mod_val[-1]
            value = resolve_float(mod_val)
            if value is None:
                continue

            w.execute(
                """INSERT OR IGNORE INTO trait_modifier
                       (trait_key, modifier_key, modifier_value, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (trait_key, mod_key, value, order),
            )

            # Lookup order: explicit override, MOD_<UPPER>, mod_<lower>, key as-is
            override = MODIFIER_LOC_OVERRIDES.get(mod_key)
            raw_label = (
                (loc.get(override) if override else None) or
                loc.get('MOD_' + mod_key.upper()) or
                loc.get('mod_' + mod_key) or
                loc.get(mod_key)
            )
            if raw_label:
                label = _clean_loc(raw_label, loc)
                if '$' not in label:  # skip if references still unresolved
                    pig = 0 if mod_key in INVERTED_MODIFIERS else 1
                    w.execute(
                        """INSERT OR REPLACE INTO modifier_label
                               (modifier_key, label, positive_is_good)
                           VALUES (?, ?, ?)""",
                        (mod_key, label, pig),
                    )

            if verbose:
                print(f"  Modifier: {trait_key} | {mod_key} = {value}")

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

# ---------------------------------------------------------------------------
# Extraction: policy restrictions (ethics-based)
# ---------------------------------------------------------------------------

def _find_blocked_ethics(block) -> set:
    """
    Recursively walk a parsed valid{} block and return the set of ethic keys
    that are blocked by NOR/NOT conditions.

    The parser promotes duplicate keys to lists, so has_ethic inside a NOR block
    may be a string (single) or list (multiple).
    We recurse into OR blocks because NOR can be nested inside an OR.
    We do NOT recurse into AND/custom_tooltip/etc. to avoid false positives.
    """
    blocked = set()

    if isinstance(block, list):
        for item in block:
            blocked |= _find_blocked_ethics(item)
        return blocked

    if not isinstance(block, dict):
        return blocked

    for key, value in block.items():
        if key in ('NOR', 'NOT'):
            # Ethics listed directly inside NOR/NOT are blocked
            if isinstance(value, dict):
                raw = value.get('has_ethic', [])
                if isinstance(raw, str):
                    raw = [raw]
                elif not isinstance(raw, list):
                    raw = []
                for e in raw:
                    e = str(e).strip('"')
                    if e.startswith('ethic_'):
                        blocked.add(e)
                # NOR/NOT may also contain nested NOR/NOT
                blocked |= _find_blocked_ethics(value)
            elif isinstance(value, list):
                for item in value:
                    blocked |= _find_blocked_ethics(item)
        elif key == 'OR':
            # OR can contain NOR/NOT sub-blocks — recurse without treating as blocked
            blocked |= _find_blocked_ethics(value)

    return blocked


def extract_policy_restrictions(data: dict, source_file: str,
                                loc: dict[str, str], w: Writer, verbose: bool):
    for policy_key, policy_data in data.items():
        if policy_key.startswith('@') or not isinstance(policy_data, dict):
            continue

        options = policy_data.get('option', [])
        if isinstance(options, dict):
            options = [options]
        elif not isinstance(options, list):
            continue

        for option_data in options:
            if not isinstance(option_data, dict):
                continue

            option_key = option_data.get('name', '')
            if isinstance(option_key, str):
                option_key = option_key.strip('"')
            if not option_key:
                continue

            valid = option_data.get('valid')
            if not valid:
                continue

            blocked = _find_blocked_ethics(valid)
            if not blocked:
                continue

            raw_label = loc.get(option_key)
            option_label = _clean_loc(raw_label, loc) if raw_label else None

            for ethic_key in sorted(blocked):
                w.execute(
                    """INSERT OR REPLACE INTO ethic_policy_restriction
                           (policy_key, option_key, option_label, ethic_key)
                       VALUES (?, ?, ?, ?)""",
                    (policy_key, option_key, option_label, ethic_key),
                )
                if verbose:
                    print(f"  {policy_key} > {option_key}: blocked for {ethic_key}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: ethics
# ---------------------------------------------------------------------------

# Modifier keys that are not real numeric modifiers (skip in ethic_modifier)
_ETHIC_SKIP_KEYS = frozenset({
    'show_only_custom_tooltip', 'custom_tooltip',
})

def extract_ethics(data: dict, loc: dict[str, str], w: Writer, verbose: bool):
    # Collect @variable definitions (e.g. @ethic_attunement1 = 25)
    variables: dict[str, float] = {}
    for key, val in data.items():
        if key.startswith('@'):
            try:
                variables[key] = float(val)
            except (ValueError, TypeError):
                pass

    def resolve_float(raw) -> float | None:
        if raw is None:
            return None
        s = str(raw).strip()
        if s.startswith('@'):
            return variables.get(s)
        try:
            return float(s)
        except ValueError:
            return None

    for ethic_key, ethic_data in data.items():
        if ethic_key.startswith('@') or not isinstance(ethic_data, dict):
            continue

        cost_raw = ethic_data.get('cost', 1)
        try:
            cost = int(cost_raw)
        except (ValueError, TypeError):
            cost = 1

        category = ethic_data.get('category')
        if isinstance(category, str):
            category = category.strip('"') or None

        fanatic_variant = ethic_data.get('fanatic_variant') or None
        regular_variant = ethic_data.get('regular_variant') or None
        gestalt = 1 if ethic_key == 'ethic_gestalt_consciousness' else 0

        raw_label = loc.get(ethic_key)
        label = _clean_loc(raw_label, loc) if raw_label else None

        w.execute(
            """INSERT OR REPLACE INTO ethic
               (key, label, cost, category, fanatic_variant, regular_variant, gestalt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ethic_key, label, cost, category,
             str(fanatic_variant) if fanatic_variant else None,
             str(regular_variant) if regular_variant else None,
             gestalt),
        )

        modifier = ethic_data.get('country_modifier')
        if not isinstance(modifier, dict):
            if verbose:
                print(f"  {ethic_key}: no country_modifier")
            continue

        for order, (mod_key, mod_val) in enumerate(modifier.items()):
            if mod_key in _ETHIC_SKIP_KEYS:
                continue
            if isinstance(mod_val, list):
                mod_val = mod_val[-1]
            value = resolve_float(mod_val)
            if value is None:
                continue

            w.execute(
                """INSERT OR REPLACE INTO ethic_modifier
                   (ethic_key, modifier_key, modifier_value, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (ethic_key, mod_key, value, order),
            )

            # Populate modifier_label for any new keys
            override = MODIFIER_LOC_OVERRIDES.get(mod_key)
            raw_mod_label = (
                (loc.get(override) if override else None) or
                loc.get('MOD_' + mod_key.upper()) or
                loc.get('mod_' + mod_key) or
                loc.get(mod_key)
            )
            if raw_mod_label:
                mod_label = _clean_loc(raw_mod_label, loc)
                if '$' not in mod_label:
                    pig = 0 if mod_key in INVERTED_MODIFIERS else 1
                    w.execute(
                        """INSERT OR REPLACE INTO modifier_label
                               (modifier_key, label, positive_is_good)
                           VALUES (?, ?, ?)""",
                        (mod_key, mod_label, pig),
                    )

            if verbose:
                print(f"  {ethic_key} | {mod_key} = {value}")

        # Tags (gameplay implications)
        tags_raw = ethic_data.get('tags')
        if isinstance(tags_raw, list):
            tag_list = [str(t) for t in tags_raw]
        elif isinstance(tags_raw, str):
            tag_list = [tags_raw]
        else:
            tag_list = []

        for order, tag in enumerate(tag_list):
            if not tag.startswith('ETHIC_'):
                continue
            w.execute(
                """INSERT OR IGNORE INTO ethic_tag (ethic_key, tag, sort_order)
                   VALUES (?, ?, ?)""",
                (ethic_key, tag, order),
            )
            raw_tag_label = loc.get(tag)
            if raw_tag_label:
                tag_label = _clean_loc(raw_tag_label, loc)
                if '$' not in tag_label:
                    w.execute(
                        """INSERT OR REPLACE INTO ethic_tag_label (tag, label)
                           VALUES (?, ?)""",
                        (tag, tag_label),
                    )
            if verbose:
                print(f"  {ethic_key} | tag: {tag}")

    w.commit()


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
PRECURSORS: list[dict] = [
    {'key': 'precursor_1', 'dlc': None, 'system': 'vultaumar_system', 'num_digsites': None,
     'relic': 'r_reality_perforator',
     'rewards': [('artifact_vultaum_research_completed_suppressed', 'suppressed'),
                 ('artifact_vultaum_research_completed_public', 'public'),
                 ('artifact_vultaum_research_completed_machine', 'machine'),
                 ('artifact_vultaum_research_completed_hive', 'hive')],
     'building': None},
    {'key': 'precursor_2', 'dlc': None, 'system': 'yuhtaan_system', 'num_digsites': None,
     'relic': 'r_cryo_core',
     'rewards': [],  # Yuht grant no permanent empire modifier; their homeworld gets pm_yuht_cleansing
     'building': None},
    {'key': 'precursor_3', 'dlc': None, 'system': 'fen_habbanis_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_league_research_completed', 'default')],
     'building': None},
    {'key': 'precursor_4', 'dlc': None, 'system': 'irass_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_irassian_research_completed', 'default')],
     'building': None},
    {'key': 'precursor_5', 'dlc': None, 'system': 'cybrex_system', 'num_digsites': None,
     'relic': 'r_war_forge',
     'rewards': [('artifact_cybrex_research_completed', 'default')],
     'building': None},
    {'key': 'precursor_baol_1', 'dlc': 'Ancient Relics Story Pack', 'system': 'last_baol_system',
     'num_digsites': 4,
     'relic': 'r_the_last_baol',
     'rewards': [('artifact_baol_research_completed', 'default')],
     'building': None},
    {'key': 'precursor_zroni_1', 'dlc': 'Ancient Relics Story Pack',
     'system': 'zroni_legacy_initializer_01', 'num_digsites': 6,
     'relic': 'r_zro_crystal',
     'rewards': [('artifact_zroni_research_completed', 'default')],
     'building': ('building_zroni_equilibrator', 'planet')},
    {'key': 'precursor_inetian', 'dlc': 'Cosmic Storms', 'system': None, 'num_digsites': None,
     'relic': 'r_weather_manipulator',
     'rewards': [('secrets_of_the_inetian_traders', 'default'),
                 ('secrets_of_the_inetian_traders_hive_mach', 'hive_machine')],
     'building': None},
    {'key': 'precursor_adakkaria', 'dlc': 'Cosmic Storms', 'system': 'adSul_system',
     'num_digsites': None,
     'relic': 'r_the_tempest_invocator',
     'rewards': [('patriotic_institute_true_potential_unleashed', 'default')],
     'building': ('building_adakkaria_patriotic_institute', 'planet')},
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


def _resolve_modifier_float(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[-1]
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        return None


def _write_modifier_label(w: Writer, loc: dict[str, str], mod_key: str):
    """Populate modifier_label for a modifier key (same lookup order as traits/ethics)."""
    override = MODIFIER_LOC_OVERRIDES.get(mod_key)
    raw_label = (
        (loc.get(override) if override else None) or
        loc.get('MOD_' + mod_key.upper()) or
        loc.get('mod_' + mod_key) or
        loc.get(mod_key)
    )
    if not raw_label:
        return
    label = _clean_loc(raw_label, loc)
    if '$' in label:
        return
    pig = 0 if mod_key in INVERTED_MODIFIERS else 1
    w.execute(
        """INSERT OR REPLACE INTO modifier_label (modifier_key, label, positive_is_good)
           VALUES (?, ?, ?)""",
        (mod_key, label, pig),
    )


def _relic_condition(potential) -> str | None:
    """Render a relic passive's 'potential' block into a short condition string.

    None when the passive always applies (no potential, or always = yes)."""
    if not isinstance(potential, dict):
        return None
    parts = []
    for key, val in potential.items():
        if key == 'always':
            continue
        if isinstance(val, str):
            parts.append(f"{key} = {val}")
        else:
            # OR { ... } and other nested blocks: keep the operator as a marker
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
    """Join the non-generic custom_tooltip strings of a relic's active_effect."""
    active = relic.get('active_effect')
    if not isinstance(active, dict):
        return None
    tips = active.get('custom_tooltip')
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


def extract_precursors(precursor_civ: dict, relics: dict, static_mods: dict,
                       loc: dict[str, str], w: Writer, verbose: bool):
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
                w.execute(
                    """INSERT OR REPLACE INTO precursor_relic
                           (precursor_key, relic_key, name, portrait, score, description, active_effect)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (key, relic_key, loc.get(relic_key), portrait, score,
                     _clean_loc(loc[relic_key + '_desc'], loc) if (relic_key + '_desc') in loc else None,
                     _relic_active_text(relic, loc)),
                )
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

        # --- building (0..1) ---
        if p['building']:
            building_key, scope = p['building']
            w.execute(
                """INSERT OR REPLACE INTO precursor_building
                       (precursor_key, building_key, name, scope)
                   VALUES (?, ?, ?, ?)""",
                (key, building_key, loc.get(building_key), scope),
            )
            if verbose:
                print(f"    building {building_key} ({scope}) | {loc.get(building_key)}")

    w.commit()


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
    ethics_dir     = install_dir / 'common' / 'ethics'
    policies_dir   = install_dir / 'common' / 'policies'
    precursor_dir  = install_dir / 'common' / 'precursor_civilizations'
    relics_dir     = install_dir / 'common' / 'relics'
    static_mod_dir = install_dir / 'common' / 'static_modifiers'
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
    print(f"\n[4/6] traits: {len(tr_files)} file(s)", file=progress)
    for f in tr_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_traits(data, f.name, w, args.verbose)
            extract_trait_modifiers(data, f.name, loc, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- named colors + trait categories ---
    co_files = sorted(colors_dir.glob('*.txt')) if colors_dir.exists() else []
    print(f"\n[5/6] named_colors: {len(co_files)} file(s)", file=progress)
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

    # --- ethics ---
    et_files = sorted(ethics_dir.glob('*.txt')) if ethics_dir.exists() else []
    print(f"\n[6/7] ethics: {len(et_files)} file(s)", file=progress)
    for f in et_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_ethics(data, loc, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- policy restrictions ---
    po_files = sorted(policies_dir.glob('*.txt')) if policies_dir.exists() else []
    print(f"\n[7/7] policies: {len(po_files)} file(s)", file=progress)
    for f in po_files:
        print(f"  Reading: {f.name}", file=progress)
        try:
            data = parse_file(f)
            extract_policy_restrictions(data, f.name, loc, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- precursors ---
    print(f"\n[8/8] precursors", file=progress)
    # Merge the precursor_civilizations files (base + DLC) into one dict.
    precursor_civ: dict = {}
    if precursor_dir.exists():
        for f in sorted(precursor_dir.glob('*.txt')):
            try:
                precursor_civ.update(parse_file(f))
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {precursor_dir} not found", file=progress)
    # Relic definitions (relic effects live here).
    relics: dict = {}
    relics_file = relics_dir / 'relics.txt'
    if relics_file.exists():
        try:
            relics = parse_file(relics_file)
        except Exception as e:
            print(f"  WARNING: failed to parse relics.txt: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {relics_file} not found", file=progress)
    # Static modifiers (the permanent empire reward modifiers) across all files.
    static_mods: dict = {}
    if static_mod_dir.exists():
        for f in sorted(static_mod_dir.glob('*.txt')):
            try:
                static_mods.update(parse_file(f))
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {static_mod_dir} not found", file=progress)
    print(f"  {len(precursor_civ)} civ entries, {len(relics)} relics, "
          f"{len(static_mods)} static modifiers loaded", file=progress)
    extract_precursors(precursor_civ, relics, static_mods, loc, w, args.verbose)

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
        n_modifiers     = tmp.execute("SELECT COUNT(*) FROM trait_modifier").fetchone()[0]
        n_mod_labels    = tmp.execute("SELECT COUNT(*) FROM modifier_label").fetchone()[0]
        n_colors        = tmp.execute("SELECT COUNT(*) FROM named_color").fetchone()[0]
        n_categories    = tmp.execute("SELECT COUNT(*) FROM trait_category").fetchone()[0]
        n_labeled       = tmp.execute("SELECT COUNT(*) FROM species_class WHERE label IS NOT NULL").fetchone()[0]
        n_ethics        = tmp.execute("SELECT COUNT(*) FROM ethic").fetchone()[0]
        n_ethic_mods    = tmp.execute("SELECT COUNT(*) FROM ethic_modifier").fetchone()[0]
        n_ethic_tags    = tmp.execute("SELECT COUNT(*) FROM ethic_tag").fetchone()[0]
        n_tag_labels    = tmp.execute("SELECT COUNT(*) FROM ethic_tag_label").fetchone()[0]
        n_policy_restr  = tmp.execute("SELECT COUNT(*) FROM ethic_policy_restriction").fetchone()[0]
        n_precursors    = tmp.execute("SELECT COUNT(*) FROM precursor").fetchone()[0]
        n_prec_relics   = tmp.execute("SELECT COUNT(*) FROM precursor_relic").fetchone()[0]
        n_prec_relic_m  = tmp.execute("SELECT COUNT(*) FROM precursor_relic_modifier").fetchone()[0]
        n_prec_reward_m = tmp.execute("SELECT COUNT(*) FROM precursor_reward_modifier").fetchone()[0]
        n_prec_build    = tmp.execute("SELECT COUNT(*) FROM precursor_building").fetchone()[0]
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
|  Trait modifiers    : {n_modifiers:<20}|
|  Modifier labels    : {n_mod_labels:<20}|
|  Named colors       : {n_colors:<20}|
|  Trait categories   : {n_categories:<20}|
|  Ethics             : {n_ethics:<20}|
|  Ethic modifiers    : {n_ethic_mods:<20}|
|  Ethic tags         : {n_ethic_tags:<20}|
|  Tag labels         : {n_tag_labels:<20}|
|  Policy restrictions: {n_policy_restr:<20}|
|  Precursors         : {n_precursors:<20}|
|  Precursor relics   : {n_prec_relics:<20}|
|  Precursor relic mods: {n_prec_relic_m:<19}|
|  Precursor reward mods: {n_prec_reward_m:<18}|
|  Precursor buildings: {n_prec_build:<20}|
+------------------------------------------+""", file=progress)
    elif sql_path:
        sql_size = Path(sql_path).stat().st_size
        print(f"\nDone. SQL file: {sql_path} ({sql_size:,} bytes)", file=progress)
    else:
        print("\nDone.", file=progress)


if __name__ == '__main__':
    main()
