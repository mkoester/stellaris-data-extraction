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
import json
import sqlite3
import argparse
from pathlib import Path
from io import StringIO

_LOC_INLINE_REF = re.compile(r'\$(\w+)\$')
_LOC_FMT_CODE   = re.compile(r'§.')          # §X / §! colour codes
_LOC_ICON       = re.compile(r'£(\w+?)(?:\|\w+)?£')  # £icon£ (or £icon|frame£)
# Scripted-loc concept tag: ['concept_key'], ['type:key'] (e.g. ['technology:tech_x'],
# ['building:building_x']), each optionally with ', inline display text'.
_LOC_CONCEPT    = re.compile(r"\['([\w:]+)'(?:,\s*([^\]]*))?\]")

def _concept_sub(m: re.Match) -> str:
    """['concept_x'] / ['type:key'] → the inline text if given, else a $key$ ref the
    expansion loop resolves (['technology:tech_psionic_aura'] → $tech_psionic_aura$)."""
    token, inline = m.group(1), m.group(2)
    if inline:
        return inline
    key = token.split(':', 1)[1] if ':' in token else token
    return f'${key}$'
# Any remaining bracket command, e.g. [This.GetName] — not statically resolvable.
_LOC_COMMAND    = re.compile(r'\[[^\]]*\]')

# Icons that are pure decoration / category markers — their name is never useful
# text (it only ever sits next to the thing it depicts), so drop them outright.
_ICON_DROP      = re.compile(r'^(job|mod|trigger|leader|relic|tradition|edict|ascension)_|^building$|_node$')

def _render_icon(m: re.Match) -> str:
    """£icon£ → its name as words, unless it's a decoration icon (dropped). Resource
    icons survive so a label like "£unity£ Produced per 100" keeps the resource. A
    `mod_planet_<stat>_<add|mult>` icon wraps a stat (e.g. mod_planet_stability_add)
    — render the stat so those labels keep it, rather than dropping the whole icon."""
    name = m.group(1)
    mp = re.match(r'^mod_planet_(.+?)_(?:add|mult)$', name)
    if mp:
        return ' ' + mp.group(1).replace('_', ' ').title() + ' '
    if _ICON_DROP.search(name):
        return ' '
    name = re.sub(r'^sr_', '', name)   # strategic-resource icon prefix (sr_living_metal
                                       # → "Living Metal", matching its $sr_…$ text twin)
    return ' ' + name.replace('_', ' ').title() + ' '

def _wnorm(w: str) -> str:
    """Word key for de-duplication: lowercased, punctuation-free, plural-folded."""
    w = re.sub(r'\W', '', w.lower())
    return w[:-1] if len(w) > 3 and w.endswith('s') else w

def _dedup_adjacent(s: str) -> str:
    """Collapse an icon name that just repeats the phrase beside it — single word
    ("Official Official Capacity" → "Official Capacity") or multi-word ("Consumer
    Goods Consumer Goods" → "Consumer Goods"). Keeps the *later* copy (the
    proper-cased / plural loc name). Icon names are ≤3 words, so that bounds the
    repeat length we look for."""
    toks = s.split()                       # any-whitespace split (drops icon padding)
    norm = [_wnorm(t) for t in toks]
    out: list[str] = []
    i, n = 0, len(toks)
    while i < n:
        dropped = False
        for k in range(min(3, (n - i) // 2), 0, -1):
            a, b = norm[i:i + k], norm[i + k:i + 2 * k]
            if all(a) and a == b:          # phrase repeats immediately → skip first copy
                i += k
                dropped = True
                break
        if not dropped:
            out.append(toks[i])
            i += 1
    return ' '.join(out)

# A $@scripted_variable$ ref, optionally with a Paradox number-format spec after '|'
# (e.g. $@cost|%0$). These only resolve when the caller passes the `variables` map.
_SCRIPTED_VAR_REF = re.compile(r'\$(@\w+)(?:\|([^$]*))?\$')

def _fmt_scripted(value: float, fmt: str | None) -> str:
    """Format a scripted-variable value per its loc format spec (the part after '|'):
    `%` = percentage (×100, trailing %); a digit sets decimal places; `+` forces a sign.
    E.g. -0.25 with `%0` → "-25%", 1.5 with `1` → "1.5"."""
    fmt = fmt or ''
    pct = '%' in fmt
    dm = re.search(r'\d', fmt)
    decimals = int(dm.group()) if dm else (0 if pct else 2)
    v = value * 100 if pct else value
    out = f'{v:.{decimals}f}'
    if '+' in fmt and value > 0:
        out = '+' + out
    return out + ('%' if pct else '')

def _clean_loc(s: str, loc: dict[str, str], keep_newlines: bool = False,
               variables: dict[str, float] | None = None) -> str:
    """Expand $KEY$ refs and ['concept', …] scripted-loc tags (up to 6 rounds),
    strip §X colour codes and [command] tags, and resolve £icon£ refs.

    Icons are pictures. Decoration/category icons (job/mod/trigger/building/…) are
    dropped; resource icons are rendered as their name so a label keeps its resource
    even when the loc doesn't repeat it ("£unity£ Produced per 100" → "Unity Produced
    per 100"). An icon name that simply repeats the word next to it is then collapsed
    ("£official£ Official Capacity" → "Official Capacity"). With keep_newlines=True
    the loc's \\n line breaks are preserved (tooltip rules stay on separate lines);
    otherwise everything collapses to one space-joined line (names, labels). When
    `variables` is given, $@scripted_var|fmt$ refs are resolved to formatted numbers."""
    # Concept tags and $KEY$ refs are interleaved each round: a $KEY$ can expand to text
    # that itself contains ['concept'] tags (and vice-versa), so resolve both until the
    # string stops changing — otherwise nested concepts survive only to be stripped as
    # [commands] below, leaving gaps (e.g. "Homeworld has a .").
    for _ in range(6):
        before = s
        s = _LOC_CONCEPT.sub(_concept_sub, s)   # ['concept'] / ['type:key'] tags
        s = _LOC_INLINE_REF.sub(lambda m: loc.get(m.group(1), m.group(0)), s)  # $KEY$
        if s == before:
            break
    if variables is not None:
        s = _SCRIPTED_VAR_REF.sub(
            lambda m: _fmt_scripted(variables[m.group(1)], m.group(2))
            if m.group(1) in variables else m.group(0), s)
    s = _LOC_COMMAND.sub('', s)        # drop leftover [Command.Chain] tags
    s = _LOC_FMT_CODE.sub('', s)       # strip §X colour codes
    s = _LOC_ICON.sub(_render_icon, s) # £icon£ → word (or dropped, if decoration)
    # Real newlines BEFORE de-dup so a \n actually separates tokens (otherwise
    # "Housing§!.\n§YGenerator" glues into one token and the repeated "Housing"
    # survives). Per line: remember its indentation ($t$ indents sub-items), then
    # de-dup its content.
    s = re.sub(r'\\n', '\n', s)        # literal \n → real line break (rule separator)
    s = re.sub(r'\\[tr]', ' ', s)      # \t \r → space
    rows: list[tuple[int, str]] = []
    for raw in s.split('\n'):
        content = _dedup_adjacent(raw)
        if content:
            rows.append((len(raw) - len(raw.lstrip()), content))
    if not keep_newlines:
        return re.sub(r'\s+', ' ', ' '.join(c for _, c in rows)).strip()
    # Tooltip: one rule per line, "- " bullet stripped. A more-indented line is a
    # sub-item of the line above (e.g. a tech under "Additional Starting
    # Technologies:") — fold it back up instead of leaving it dangling.
    out: list[str] = []
    out_indents: list[int] = []
    for indent, content in rows:
        body = re.sub(r'^[-•]\s*', '', content).strip()
        # More indented than the line it would attach to → it's a sub-item; fold it up.
        # Compare to that line's indent (not the previous row's) so several siblings at
        # the same sub-indent all join: "Terms:" + a + b + c. After a colon the first
        # joins with a space, the rest as a comma list.
        if out and indent > out_indents[-1]:
            out[-1] += (' ' if out[-1].rstrip().endswith(':') else ', ') + body
        else:
            out.append(body)
            out_indents.append(indent)
    return '\n'.join(out)


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
-- Key/value metadata about the extracted game (e.g. game_version, game_version_raw).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

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

-- Civics: the government civics an empire picks at creation (and can later swap).
-- `civic_type` is derived from the source file (normal / gestalt / corporate /
-- special). `selectable` is 0 for civics gated to non-player country types (fallen
-- empires, etc.). `description` is the cleaned effects tooltip; the numeric effects
-- live in civic_modifier and the eligibility rules in civic_requirement.
DROP TABLE IF EXISTS civic_requirement;
DROP TABLE IF EXISTS civic_modifier;
DROP TABLE IF EXISTS civic;
CREATE TABLE civic (
    key          TEXT PRIMARY KEY,    -- e.g. "civic_technocracy"
    name         TEXT,                -- localised name
    description  TEXT,                -- cleaned effects tooltip
    civic_type   TEXT NOT NULL,       -- 'normal' | 'gestalt' | 'corporate' | 'special'
    required_dlc TEXT,                -- from playable host_has_dlc; NULL = base game
    selectable   INTEGER NOT NULL DEFAULT 1,  -- 0 = not player-pickable (fallen empire, …)
    locked       INTEGER NOT NULL DEFAULT 0,  -- 1 = `modification = no`: can't be added/removed after game start
    icon         TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE civic_modifier (
    civic_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (civic_key, modifier_key),
    FOREIGN KEY (civic_key) REFERENCES civic(key)
);

-- Eligibility rules parsed from a civic's potential/possible blocks. `kind` is what
-- is being constrained (authority / ethic / civic / origin); `operator` is 'require'
-- (must have), 'forbid' (must not have), or 'require_any' (must have one of an OR set,
-- grouped by `group_id`); `value` is the constrained key.
CREATE TABLE civic_requirement (
    civic_key  TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- 'authority' | 'ethic' | 'civic' | 'origin'
    operator   TEXT NOT NULL,        -- 'require' | 'forbid' | 'require_any'
    value      TEXT NOT NULL,        -- e.g. 'auth_corporate', 'ethic_materialist'
    group_id   INTEGER NOT NULL DEFAULT 0,  -- ties 'require_any' values from one OR set together
    PRIMARY KEY (civic_key, kind, operator, value),
    FOREIGN KEY (civic_key) REFERENCES civic(key)
);

-- Authorities (the empire's government type: Democratic, Imperial, Hive Mind, …).
-- One per empire, picked at creation. `country_modifier` numeric effects live in
-- authority_modifier; the ethics/archetype eligibility rules in authority_requirement.
-- `selectable` is 0 for authorities gated to a specific non-player country_type
-- (e.g. auth_ancient_machine_intelligence → ai_empire). `required_dlc` from a
-- playable `has_*` flag (corporate → MegaCorp).
DROP TABLE IF EXISTS authority_requirement;
DROP TABLE IF EXISTS authority_modifier;
DROP TABLE IF EXISTS authority;
CREATE TABLE authority (
    key          TEXT PRIMARY KEY,    -- e.g. "auth_imperial"
    name         TEXT,                -- localised name
    description  TEXT,                -- cleaned description tooltip
    required_dlc TEXT,                -- e.g. "MegaCorp"; NULL = base game
    selectable   INTEGER NOT NULL DEFAULT 1,  -- 0 = not player-pickable
    icon         TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE authority_modifier (
    authority_key  TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (authority_key, modifier_key),
    FOREIGN KEY (authority_key) REFERENCES authority(key)
);

-- Eligibility rules parsed from an authority's `possible` block: kind 'ethic' (e.g.
-- forbids Gestalt) or 'archetype' (Machine Intelligence needs MACHINE, Hive Mind
-- forbids it) × operator require/forbid/require_any.
CREATE TABLE authority_requirement (
    authority_key TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- 'ethic' | 'archetype'
    operator      TEXT NOT NULL,      -- 'require' | 'forbid' | 'require_any'
    value         TEXT NOT NULL,      -- e.g. 'ethic_gestalt_consciousness', 'MACHINE'
    group_id      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (authority_key, kind, operator, value),
    FOREIGN KEY (authority_key) REFERENCES authority(key)
);

-- Origins: the empire's starting scenario (one per empire). Like civics, the numeric
-- effects (where present — most origins are narrative/event-driven) live in
-- origin_modifier and the eligibility rules in origin_requirement. `selectable` is 0
-- for the non-playable file and for swap-variant forms (gated on already having an
-- origin) — the planner only loads selectable = 1.
DROP TABLE IF EXISTS origin_requirement;
DROP TABLE IF EXISTS origin_modifier;
DROP TABLE IF EXISTS origin;
CREATE TABLE origin (
    key            TEXT PRIMARY KEY,  -- e.g. "origin_shattered_ring"
    name           TEXT,              -- localised name
    description    TEXT,              -- cleaned effects tooltip
    required_dlc   TEXT,              -- from playable host_has_dlc; NULL = base game
    selectable     INTEGER NOT NULL DEFAULT 1,
    ascension_lock   TEXT,            -- locked to this single ascension path (blocks all others); NULL = unrestricted
    ascension_blocks TEXT,            -- comma-separated ascension paths blocked (when it still allows others); NULL = none
    starting_colony  TEXT,            -- forced starting world class (e.g. pc_gaia, pc_shattered_ring_habitable); NULL = normal
    forces_home_system INTEGER NOT NULL DEFAULT 0,  -- 1 = origin forces a fixed home system (a non custom-empire initializer)
    icon             TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE origin_modifier (
    origin_key     TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (origin_key, modifier_key),
    FOREIGN KEY (origin_key) REFERENCES origin(key)
);

-- Eligibility rules parsed from an origin's potential/possible blocks (same shape as
-- civic_requirement): kind authority/ethic/civic/origin × operator
-- require/forbid/require_any. For origins these are mostly civic conflicts.
CREATE TABLE origin_requirement (
    origin_key TEXT NOT NULL,
    kind       TEXT NOT NULL,
    operator   TEXT NOT NULL,
    value      TEXT NOT NULL,
    group_id   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (origin_key, kind, operator, value),
    FOREIGN KEY (origin_key) REFERENCES origin(key)
);

-- Planet classes (the worlds): the colonizable climates and special habitable types,
-- plus the standard uninhabitable system bodies. `climate` groups habitability
-- (wet/dry/cold; NULL for special/uninhabitable); `is_standard` = a default startable
-- climate (initial = yes). Habitability itself is species-preference-based, not a
-- per-class number, so it's not stored.
DROP TABLE IF EXISTS planet_class;
CREATE TABLE planet_class (
    key         TEXT PRIMARY KEY,   -- e.g. "pc_continental", "pc_gaia", "pc_barren"
    name        TEXT,               -- localised, e.g. "Continental World"
    climate     TEXT,               -- 'wet' | 'dry' | 'cold' | NULL
    colonizable INTEGER NOT NULL DEFAULT 0,
    is_standard INTEGER NOT NULL DEFAULT 0,  -- initial = yes (default startable climate)
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Home-system initializers usable as a custom-empire start (`usage = custom_empire`
-- in common/solar_system_initializers/*.txt). `name` is the localised system name.
DROP TABLE IF EXISTS initializer;
CREATE TABLE initializer (
    key        TEXT PRIMARY KEY,   -- e.g. "sol_system_initializer"
    name       TEXT,               -- localised, e.g. "Sol"
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- Diplomacy/portrait rooms an empire can pick (the backdrop behind the ruler). Rooms
-- are gfx entities with no localisation, so the set is the distinct `room = "…"` values
-- used by the prescripted empires; `label` is derived from the key.
DROP TABLE IF EXISTS room;
CREATE TABLE room (
    key        TEXT PRIMARY KEY,   -- e.g. "default_room", "machine_room"
    label      TEXT,               -- derived, e.g. "Default", "Slaving Despots"
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- Empire flag assets scanned from the flags/ directory: each icon category's files and
-- the background files (the colours come from named_color). Icons/backgrounds are .dds.
DROP TABLE IF EXISTS flag_asset;
CREATE TABLE flag_asset (
    kind       TEXT NOT NULL,      -- 'icon' | 'background'
    category   TEXT NOT NULL,      -- e.g. "pirate"; "backgrounds" for backgrounds
    file       TEXT NOT NULL,      -- e.g. "flag_pirate_2.dds"
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, category, file)
);

-- Prescripted (predefined) empires from prescripted_countries/*.txt — the built-in
-- empires the game ships, usable as starting points. Scalar setup in
-- prescripted_empire; the repeated picks (traits/civics/ethics) in
-- prescripted_empire_item. Same shape as a user_empire_designs.txt entry.
DROP TABLE IF EXISTS prescripted_empire_item;
DROP TABLE IF EXISTS prescripted_empire;
CREATE TABLE prescripted_empire (
    key                TEXT PRIMARY KEY,   -- e.g. "tzynn", "humans1"
    name               TEXT,              -- localised display name
    adjective          TEXT,              -- empire adjective, e.g. "Tzynn"
    ship_prefix        TEXT,              -- e.g. "TIS"
    species_class      TEXT,              -- phenotype, e.g. "REP", "HUM", "MACHINE"
    species_portrait   TEXT,
    species_name       TEXT,              -- species (singular) name
    species_plural     TEXT,              -- species plural
    species_adjective  TEXT,              -- species adjective
    secondary_class    TEXT,              -- second founding species (Syncretic/Necrophage/…); NULL if none
    secondary_portrait TEXT,
    secondary_name     TEXT,
    secondary_plural   TEXT,
    secondary_adjective TEXT,
    ruler_name         TEXT,              -- starting leader's name
    planet_name        TEXT,              -- homeworld name
    system_name        TEXT,              -- home system / star name
    authority          TEXT,              -- e.g. "auth_imperial"
    government         TEXT,              -- e.g. "gov_star_empire"
    origin             TEXT,              -- e.g. "origin_default"
    planet_class       TEXT,              -- e.g. "pc_desert"
    initializer        TEXT,              -- home-system initializer, e.g. "sol_system_initializer"
    graphical_culture  TEXT,              -- ship appearance, e.g. "reptilian_01"
    sort_order         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE prescripted_empire_item (
    empire_key TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- 'trait' | 'secondary_trait' | 'civic' | 'ethic'
    value      TEXT NOT NULL,      -- e.g. "trait_strong", "civic_slaver_guilds", "ethic_authoritarian"
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (empire_key, kind, value),
    FOREIGN KEY (empire_key) REFERENCES prescripted_empire(key)
);

-- Precursors: the ancient civilisations whose archaeology chains grant rewards.
-- The precursor<->reward mapping is spread across event files (no single source
-- file joins them), so the spine is the hand-curated PRECURSORS config; the
-- effect numbers and display names below are pulled from the game files.
DROP TABLE IF EXISTS precursor_homeworld_reward;
DROP TABLE IF EXISTS precursor_technology;
DROP TABLE IF EXISTS precursor_building_modifier;
DROP TABLE IF EXISTS precursor_building_resource;
DROP TABLE IF EXISTS precursor_building;
DROP TABLE IF EXISTS precursor_reward_modifier;
DROP TABLE IF EXISTS precursor_relic_active_modifier;
DROP TABLE IF EXISTS precursor_relic_resource;
DROP TABLE IF EXISTS precursor_relic_passive_text;
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
    active_effect TEXT,                    -- localised triumph (active) ability tooltip
    active_mode   TEXT,                    -- 'direct' | 'random' | 'choice' (how active mods are applied)
    active_detail TEXT,                    -- how the active modifiers are granted (random/duration), e.g.
                                           --   "One at random for 2–5 years, or all of them for 1 year"
    triumph_cooldown_days INTEGER,         -- cooldown after activating the triumph effect
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

-- Temporary modifiers a triumph can grant via its event (e.g. the Vultaum Reality
-- Perforator's random combat buffs). Resolved by following active_effect's
-- country_event → the event's (locked_)random_list → add_modifier → static modifier.
CREATE TABLE precursor_relic_active_modifier (
    relic_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,          -- e.g. "ship_weapon_damage"
    modifier_value REAL NOT NULL,
    group_label    TEXT,                   -- for 'choice' actives, the option name (e.g. "Martial Guidance")
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (relic_key, modifier_key, group_label),
    FOREIGN KEY (relic_key) REFERENCES precursor_relic(relic_key)
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

-- Non-numeric passive effects (a relic's triggered_country_modifier custom_tooltip),
-- e.g. the Cybrex Warform armies, or the Cryo Core's wilderness-only district. Like
-- precursor_relic_modifier, `condition` holds the 'potential' when it only applies
-- to some empire types (NULL = always).
CREATE TABLE precursor_relic_passive_text (
    relic_key  TEXT NOT NULL,
    text       TEXT NOT NULL,
    condition  TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (relic_key, text),
    FOREIGN KEY (relic_key) REFERENCES precursor_relic(relic_key)
);

-- Resources involved in the triumph (active) ability: 'cost' = spent to activate
-- (resources.cost), 'produces' = gained (active_effect add_resource). scales = 1
-- when the amount grows with use (a multiplier, e.g. Cybrex cybrex_count: the War
-- Forge produces 1000 alloys at first use, +1000 each subsequent use).
CREATE TABLE precursor_relic_resource (
    relic_key  TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- 'cost' | 'produces'
    resource   TEXT NOT NULL,              -- e.g. "unity", "minerals", "alloys"
    amount     REAL NOT NULL,
    scales     INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (relic_key, kind, resource),
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

-- Precursor buildings, of two kinds (see `source`): 'homeworld' = unique buildings
-- granted on the precursor's home planet; 'technology' = empire-wide buildable
-- buildings unlocked by the precursor's "Secrets of …" technology (tech_key) — for
-- those, scope is 'planet' or 'starbase' (orbital).
CREATE TABLE precursor_building (
    precursor_key   TEXT NOT NULL,
    building_key    TEXT NOT NULL,         -- e.g. "building_zroni_equilibrator", "cybrex_mining_hub"
    name            TEXT,                  -- localised building name
    scope           TEXT,                  -- "planet" or "starbase"
    source          TEXT NOT NULL DEFAULT 'homeworld',  -- 'homeworld' | 'technology'
    tech_key        TEXT,                  -- unlocking tech (source='technology'); else NULL
    category        TEXT,                  -- e.g. "government", "resource"
    build_time_days INTEGER,               -- base_buildtime (resolving @vars)
    empire_limit    INTEGER,               -- base_cap_amount: max buildable per empire (NULL = uncapped)
    planet_limit    INTEGER,               -- planet_limit: max buildable per planet (NULL = uncapped)
    can_demolish    INTEGER,               -- 1 = demolishable, 0 = not
    PRIMARY KEY (precursor_key, building_key),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

-- A precursor building's resources block: 'cost' (one-off to build), 'upkeep'
-- (monthly), 'produces' (monthly output). Amounts resolve @scripted_variables.
CREATE TABLE precursor_building_resource (
    building_key TEXT NOT NULL,
    kind         TEXT NOT NULL,            -- 'cost' | 'upkeep' | 'produces'
    resource     TEXT NOT NULL,            -- e.g. "minerals", "energy", "sr_zro"
    amount       REAL NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (building_key, kind, resource)
);

-- A precursor building's modifiers (one row per scope+modifier, with both the base
-- and the Archaeo-Engineers-boosted value already resolved). scope: 'planet' =
-- planet_modifier, 'country' = country_modifier, 'system'/'station' = the orbital
-- starbase building's triggered_system_/station_modifier, 'job' = jobs added via
-- the building's jobs/* inline_script (job_<x>_add, the regular-empire variant —
-- gestalt/FE empires get the calculator/archivist equivalents).
--   modifier_value = the value without ascension perks (the default).
--   ae_value       = the *total* value with the Archaeo-Engineers ascension perk
--                    (NULL when the perk makes no difference). Correctly handles
--                    both 'replacement' buildings (a NOT-AE base block + an AE block
--                    — e.g. Cybrex +100% → +150%) and 'additive' ones (an always-on
--                    base + an AE bonus on top — e.g. League +50 → +100).
--   condition      = NULL normally, or a *different* ascension perk's key (e.g.
--                    'ap_cosmogenesis') when the modifier only applies with that perk.
CREATE TABLE precursor_building_modifier (
    building_key   TEXT NOT NULL,
    scope          TEXT NOT NULL,          -- 'planet' | 'country' | 'system' | 'station' | 'job'
    modifier_key   TEXT NOT NULL,          -- e.g. "ship_shield_damage_mult", "job_biologist_add"
    modifier_value REAL NOT NULL,          -- value without ascension perks
    ae_value       REAL,                   -- total value with Archaeo-Engineers (NULL = no change)
    condition      TEXT,                   -- NULL, or a different required ascension-perk key
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (building_key, scope, modifier_key, condition)
);

-- A precursor's "Secrets of …" technology: a permanent research option granted by
-- the discovery special project (cannot appear in normal research draws). Each
-- unlocks one empire-wide building (unlocks_building_key → precursor_building).
CREATE TABLE precursor_technology (
    precursor_key       TEXT NOT NULL,
    tech_key            TEXT NOT NULL,      -- e.g. "tech_secrets_cybrex"
    name                TEXT,               -- localised tech name
    cost                INTEGER,            -- research cost (resolving @vars)
    tier                INTEGER,
    area                TEXT,               -- e.g. "society"
    category            TEXT,               -- e.g. "archaeostudies"
    unlocks_building_key TEXT,              -- the building this tech unlocks
    PRIMARY KEY (precursor_key, tech_key),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);

-- The one-time "Homeworld reward" granted by the event that fires when a
-- precursor's home world is finally discovered (separate from the permanent
-- empire modifier in precursor_reward_modifier and the relic). Two reward shapes
-- share this table: a scaled resource burst (add_monthly_resource_mult — a
-- multiplier on the empire's current monthly output of that resource, clamped to
-- [min, max]); and the flat random minor_artifacts payout (resource =
-- 'minor_artifacts', multiplier = NULL, min/max = the payout bounds, e.g.
-- 750..1500 for a large reward). Not every precursor grants every shape.
CREATE TABLE precursor_homeworld_reward (
    precursor_key TEXT NOT NULL,
    resource      TEXT NOT NULL,           -- e.g. "unity", "engineering_research", "minor_artifacts"
    multiplier    REAL,                    -- add_monthly_resource_mult value (×output); NULL = flat amount
    min_amount    REAL NOT NULL,
    max_amount    REAL NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (precursor_key, resource),
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

-- Deposit dictionary: every orbital/planetary/blocker deposit, so home-system
-- planet deposits resolve to exact resource yields, modifiers and clear costs.
DROP TABLE IF EXISTS deposit_modifier;
DROP TABLE IF EXISTS deposit_resource;
DROP TABLE IF EXISTS deposit;
CREATE TABLE deposit (
    key         TEXT PRIMARY KEY,       -- e.g. "d_minerals_10", "d_ruined_arcology"
    name        TEXT,                   -- localised name
    category    TEXT,                   -- e.g. "orbital_mining_deposits", "deposit_cat_blockers"
    is_blocker  INTEGER NOT NULL DEFAULT 0,
    clear_time  INTEGER,                -- days to clear (blockers)
    source_file TEXT
);
CREATE TABLE deposit_resource (
    deposit_key TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'produces' | 'cost' | 'upkeep'
    resource    TEXT NOT NULL,          -- e.g. "minerals", "physics_research"
    amount      REAL NOT NULL,
    PRIMARY KEY (deposit_key, kind, resource),
    FOREIGN KEY (deposit_key) REFERENCES deposit(key)
);
CREATE TABLE deposit_modifier (
    deposit_key    TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    PRIMARY KEY (deposit_key, modifier_key),
    FOREIGN KEY (deposit_key) REFERENCES deposit(key)
);

-- Precursor home systems (1..n per precursor) and their planets/deposits.
-- planet.id is the synthetic "<initializer>#<index>" so child rows resolve in
-- both the SQL dump and SQLite without relying on autoincrement/lastrowid.
DROP TABLE IF EXISTS precursor_megastructure;
DROP TABLE IF EXISTS precursor_planet_modifier;
DROP TABLE IF EXISTS precursor_planet_deposit;
DROP TABLE IF EXISTS precursor_planet;
DROP TABLE IF EXISTS precursor_system;
CREATE TABLE precursor_system (
    precursor_key TEXT NOT NULL,
    initializer   TEXT NOT NULL,         -- e.g. "fen_habbanis_system"
    name          TEXT,                  -- localised system name
    star_class    TEXT,                  -- e.g. "sc_g"
    sort_order    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (precursor_key, initializer),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);
CREATE TABLE precursor_planet (
    id                 TEXT PRIMARY KEY,  -- "<initializer>#<index>"
    precursor_key      TEXT NOT NULL,
    initializer        TEXT NOT NULL,
    name               TEXT,
    planet_class       TEXT,              -- e.g. "pc_relic", "pc_nuked", "pc_cybrex"
    planet_class_label TEXT,              -- localised, e.g. "Relic World", "Tomb World"
    size               INTEGER,
    is_moon            INTEGER NOT NULL DEFAULT 0,
    is_homeworld       INTEGER NOT NULL DEFAULT 0,  -- carries a *_homeworld planet flag
    flags              TEXT,              -- space-joined planet flags
    sort_order         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
);
CREATE TABLE precursor_planet_deposit (
    planet_id   TEXT NOT NULL,
    deposit_key TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (planet_id)   REFERENCES precursor_planet(id),
    FOREIGN KEY (deposit_key) REFERENCES deposit(key)
);
CREATE TABLE precursor_planet_modifier (
    planet_id       TEXT NOT NULL,
    static_modifier TEXT NOT NULL,        -- e.g. "first_league_homeworld"
    modifier_key    TEXT NOT NULL,
    modifier_value  REAL NOT NULL,
    PRIMARY KEY (planet_id, static_modifier, modifier_key),
    FOREIGN KEY (planet_id) REFERENCES precursor_planet(id)
);
CREATE TABLE precursor_megastructure (
    precursor_key TEXT NOT NULL,
    initializer   TEXT NOT NULL,
    type          TEXT NOT NULL,          -- e.g. "hyper_relay_ruined"
    type_label    TEXT,                   -- localised, e.g. "Ruined Hyper Relay"
    count         INTEGER NOT NULL DEFAULT 1,  -- e.g. 4 for the Cybrex ruined ring sections
    PRIMARY KEY (precursor_key, initializer, type),
    FOREIGN KEY (precursor_key) REFERENCES precursor(key)
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

# Leading whitespace is optional: most files indent entries, but some DLC files
# (e.g. the Infernals Species Pack) put them at column 0. The `l_english:` header
# never matches (it has no "value"); `#` comments aren't word-initial.
_LOC_ENTRY = re.compile(r'^\s*(\w[\w_]*):(\d*)\s+"([^"]*)"', re.MULTILINE)


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
    # No MOD_ loc of its own; the closest is the "repelling" key ("Cosmic Storm
    # Repulsion Effect"). Used by the Tempest Invocator's triumph modifier.
    'country_storm_repulsion_influence_add': 'MOD_COUNTRY_STORM_REPELLING_ADD',
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
    'ships_upkeep_mult',
    'shroud_delve_cooldown',
    # Costs / fees (a reduction is good) — seen on precursor relics & rewards
    'pop_amenities_usage_mult',
    'terraforming_cost_mult',
    'country_trade_fee',
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

            # Populate modifier_label via the shared helper (icon-stripped,
            # attunement-aware, same loc lookup order for every modifier source).
            _write_modifier_label(w, loc, mod_key)

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

            # Populate modifier_label via the shared helper (icon-stripped,
            # attunement-aware, same loc lookup order for every modifier source).
            _write_modifier_label(w, loc, mod_key)

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
# Extraction: civics
# ---------------------------------------------------------------------------

# potential/possible sub-keys we turn into eligibility rules → stored `kind`.
_CIVIC_GATE_KINDS = {'authority': 'authority', 'ethics': 'ethic',
                     'civics': 'civic', 'origin': 'origin',
                     'graphical_culture': 'ship_type'}


def _trigger_values(sub) -> list[tuple[str, list[str]]]:
    """Parse a civic gating sub-block (ethics / authority / civics / origin) into
    [(operator, [values])]: a bare `value =` → 'require'; `NOT`/`NOR` → 'forbid';
    `OR` → 'require_any'."""
    out: list[tuple[str, list[str]]] = []
    if not isinstance(sub, dict):
        return out
    direct = [v for v in _to_list(sub.get('value')) if isinstance(v, str)]
    if direct:
        out.append(('require', direct))
    for neg in ('NOT', 'NOR'):
        for nb in _as_blocks(sub.get(neg)):
            vals = [v for v in _to_list(nb.get('value')) if isinstance(v, str)]
            if vals:
                out.append(('forbid', vals))
    for ob in _as_blocks(sub.get('OR')):
        vals = [v for v in _to_list(ob.get('value')) if isinstance(v, str)]
        if vals:
            out.append(('require_any', vals))
    return out


def _civic_requirements(cdef: dict) -> list[tuple[str, str, str, int]]:
    """Eligibility rows [(kind, operator, value, group_id)] from the top level of a
    civic's potential + possible blocks. (Deeply nested OR-of-different-kinds gates —
    rare — are not unpacked; those civics just carry fewer rules.)"""
    rows: list[tuple[str, str, str, int]] = []
    group = 0
    seen: set[tuple[str, str, str]] = set()
    for block_name in ('potential', 'possible'):
        block = cdef.get(block_name)
        if not isinstance(block, dict):
            continue
        for raw_kind, kind in _CIVIC_GATE_KINDS.items():
            for op, vals in _trigger_values(block.get(raw_kind)):
                gid = 0
                if op == 'require_any':
                    group += 1
                    gid = group
                for v in vals:
                    # The ship-type gate forbids the biological graphical cultures
                    # (biogenesis_01/02) — collapse them to a single 'organic' value.
                    if kind == 'ship_type' and v.startswith('biogenesis'):
                        v = 'organic'
                    if (kind, op, v) in seen:
                        continue
                    seen.add((kind, op, v))
                    rows.append((kind, op, v, gid))
    return rows


def extract_civics(data: dict, civic_type: str, loc: dict[str, str],
                   scripted_vars: dict[str, float], w: Writer, order_start: int,
                   verbose: bool) -> int:
    """Extract one civics file; all its civics share `civic_type`. Returns the next
    sort_order so successive files keep a stable global order."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = order_start
    for key, cdef in data.items():
        if key.startswith('@') or not isinstance(cdef, dict):
            continue

        playable = cdef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            d = playable.get('host_has_dlc')
            if isinstance(d, list):
                d = d[0] if d else None
            dlc = str(d).strip('"') if d else None

        # Not player-pickable when gated to a specific country type (fallen empire,
        # pre-FTL, …) or explicitly playable = no.
        selectable = 1
        pot = cdef.get('potential')
        if isinstance(pot, dict) and 'country_type' in pot:
            selectable = 0
        if isinstance(playable, dict) and str(playable.get('always')).strip() == 'no':
            selectable = 0

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        desc_key = cdef.get('description')
        desc_key = desc_key.strip('"') if isinstance(desc_key, str) else None
        raw_desc = loc.get(desc_key) if desc_key else None
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = cdef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None
        # `modification = no` → the civic can't be added/removed via a government
        # reform after the game starts (origin-bound, devouring swarm, etc.).
        locked = 1 if str(cdef.get('modification')).strip().strip('"') == 'no' else 0

        w.execute(
            """INSERT OR REPLACE INTO civic
                   (key, name, description, civic_type, required_dlc, selectable, locked, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, civic_type, dlc, selectable, locked, icon, order),
        )
        if verbose:
            print(f"  civic [{civic_type}] {key} | {name}"
                  f"{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = cdef.get('modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO civic_modifier
                           (civic_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)
                if verbose:
                    print(f"      modifier | {mk} = {value:g}")

        for kind, op, val, gid in _civic_requirements(cdef):
            w.execute(
                """INSERT OR IGNORE INTO civic_requirement
                       (civic_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )

    w.commit()
    return order


# ---------------------------------------------------------------------------
# Extraction: authorities (government types)
# ---------------------------------------------------------------------------

# A playable `has_*` flag → the DLC it gates (only the ones used by authorities).
_AUTH_DLC = {'has_megacorp': 'MegaCorp'}


def _authority_requirements(adef: dict) -> list[tuple[str, str, str, int]]:
    """Eligibility rows [(kind, operator, value, group_id)] from an authority's
    `possible` block — only the meaningful `ethics` and `species_archetype` gates
    (the `origin` NOR lists are just Legendary-Leader gov-swap plumbing). Reuses the
    civic trigger parser for value/NOR/OR handling."""
    rows: list[tuple[str, str, str, int]] = []
    group = 0
    poss = adef.get('possible')
    if not isinstance(poss, dict):
        return rows
    for raw_kind, kind in (('ethics', 'ethic'), ('species_archetype', 'archetype')):
        for op, vals in _trigger_values(poss.get(raw_kind)):
            gid = 0
            if op == 'require_any':
                group += 1
                gid = group
            for v in vals:
                rows.append((kind, op, v, gid))
    return rows


def extract_authorities(data: dict, loc: dict[str, str],
                        scripted_vars: dict[str, float], w: Writer, verbose: bool) -> None:
    """Extract common/governments/authorities → authority (+ _modifier, _requirement)."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = 0
    for key, adef in data.items():
        if key.startswith('@') or not isinstance(adef, dict):
            continue

        # Not player-pickable when gated TO a specific country type (a positive
        # `country_type = { value = … }`, e.g. ai_empire). A `NOT/NOR` country_type
        # (corporate excludes primitives) is just an exclusion → still selectable.
        selectable = 1
        pot = adef.get('potential')
        if isinstance(pot, dict):
            ct = pot.get('country_type')
            if isinstance(ct, dict) and any(isinstance(x, str) for x in _to_list(ct.get('value'))):
                selectable = 0

        playable = adef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            if str(playable.get('always')).strip() == 'no':
                selectable = 0
            for flag, name in _AUTH_DLC.items():
                if str(playable.get(flag)).strip() == 'yes':
                    dlc = name

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        raw_desc = loc.get(key + '_desc')
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = adef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None

        w.execute(
            """INSERT OR REPLACE INTO authority
                   (key, name, description, required_dlc, selectable, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, dlc, selectable, icon, order),
        )
        if verbose:
            print(f"  authority {key} | {name}"
                  f"{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = adef.get('country_modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO authority_modifier
                           (authority_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)

        for kind, op, val, gid in _authority_requirements(adef):
            w.execute(
                """INSERT OR IGNORE INTO authority_requirement
                       (authority_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: origins
# ---------------------------------------------------------------------------

def compute_ascension_restrictions(install_dir: Path) -> dict[str, dict]:
    """Per origin, its ascension-path restriction → {'lock': path|None, 'blocks':[…]}.
    `lock` = locked to this single path (blocks all others); `blocks` = the paths it
    forbids when it still allows more than one. Data-driven: each path perk's
    `potential` has a `NOR = { … has_origin = X … }` listing origins it forbids; an
    origin forbidden from three of the four paths is locked to the fourth, one
    forbidden from one or two has those in `blocks`. Auto-ascension origins (forbidden
    from *every* path perk because they start already ascended) are resolved to their
    real path via the ascension council-agenda file (origins grouped under a path)."""
    AP_PATH = {
        'ap_engineered_evolution': 'Genetic', 'ap_the_flesh_is_weak': 'Cybernetic',
        'ap_synthetic_evolution': 'Synthetic', 'ap_mind_over_matter': 'Psionic',
    }
    ALL_PATHS = ['Genetic', 'Cybernetic', 'Synthetic', 'Psionic']

    def _origins_in(node, neg=0):
        out = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == 'has_origin':
                    out += [(x, neg) for x in (v if isinstance(v, list) else [v])]
                else:
                    out += _origins_in(v, neg + (1 if k in ('NOT', 'NOR') else 0))
        elif isinstance(node, list):
            for x in node:
                out += _origins_in(x, neg)
        return out

    forbid: dict[str, set] = {}
    allow: dict[str, set] = {}
    try:
        perks = parse_file(install_dir / 'common' / 'ascension_perks' / '00_ascension_paths.txt')
    except Exception:
        return {}
    for ap, name in AP_PATH.items():
        perk = perks.get(ap, {})
        if not isinstance(perk, dict):
            continue
        for blk in (perk.get('potential', {}), perk.get('possible', {})):
            for o, neg in _origins_in(blk):
                (forbid if neg % 2 else allow).setdefault(o, set()).add(name)

    def _agenda_path(n: str):
        if re.search(r'psionic|mind_over_matter', n):                          return 'Psionic'
        if re.search(r'synthetic|virtuality|nanotech', n):                     return 'Synthetic'
        if re.search(r'flesh_is_weak|cybernetic|organo_machine|modularity', n): return 'Cybernetic'
        if re.search(r'genetic|engineered_evolution|mutation|cloning|purity', n): return 'Genetic'
        return None
    agenda_path: dict[str, set] = {}
    try:
        agendas = parse_file(install_dir / 'common' / 'council_agendas'
                             / '02_council_agendas_ascensions.txt')
        for an, body in agendas.items():
            p = _agenda_path(an)
            if p and isinstance(body, dict):
                for o, neg in _origins_in(body):
                    if neg % 2 == 0:
                        agenda_path.setdefault(o, set()).add(p)
    except Exception:
        pass

    # Locked but never positively tied to one path in a machine-readable way.
    FALLBACK = {'origin_shroudwalker_apprentice': 'Psionic'}

    out: dict[str, dict] = {}
    for o in set(forbid) | set(allow):
        blocked = forbid.get(o, set()) - allow.get(o, set())
        free = [p for p in ALL_PATHS if p not in blocked]
        lock, blocks = None, []
        if len(free) == 1 and blocked:
            lock = free[0]                           # blocked from three → locked to one
        elif not free:                               # blocked from all → auto-ascension
            cands = agenda_path.get(o) or ({FALLBACK[o]} if o in FALLBACK else set())
            if len(cands) == 1:
                lock = next(iter(cands))
        elif blocked:                                # blocks one or two, still allows others
            blocks = [p for p in ALL_PATHS if p in blocked]
        if lock or blocks:
            out[o] = {'lock': lock, 'blocks': blocks}
    return out


def _custom_empire_initializers(install_dir: Path) -> set[str]:
    """The initializer keys usable as a plain custom-empire start (`usage = custom_empire`).
    An origin whose `initializers` list includes anything else forces a fixed home
    system (its own `usage = origin` start, e.g. shattered_ring_start)."""
    custom: set[str] = set()
    idir = install_dir / 'common' / 'solar_system_initializers'
    if not idir.is_dir():
        return custom
    for f in sorted(idir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        for key, idef in data.items():
            if isinstance(idef, dict) and str(idef.get('usage')).strip() == 'custom_empire':
                custom.add(key)
    return custom


def extract_origins(data: dict, force_nonselectable: bool, loc: dict[str, str],
                    scripted_vars: dict[str, float], w: Writer, order_start: int,
                    verbose: bool, ascension: dict[str, dict],
                    init_custom: set[str]) -> int:
    """Extract one origins file. `force_nonselectable` marks every origin in it
    non-selectable (the non-playable file). Returns the next sort_order."""
    variables: dict[str, float] = dict(scripted_vars)
    for k, v in data.items():
        if k.startswith('@'):
            f = _resolve_modifier_float(v)
            if f is not None:
                variables[k] = f

    order = order_start
    for key, odef in data.items():
        if key.startswith('@') or not isinstance(odef, dict):
            continue
        if str(odef.get('is_origin')).strip() != 'yes':
            continue

        playable = odef.get('playable')
        dlc = None
        if isinstance(playable, dict):
            d = playable.get('host_has_dlc')
            if isinstance(d, list):
                d = d[0] if d else None
            dlc = str(d).strip('"') if d else None

        # Not player-pickable: the non-playable file; swap-variant forms (potential
        # gated on already having an origin); country-type-gated; or playable = no.
        selectable = 0 if force_nonselectable else 1
        pot = odef.get('potential')
        if isinstance(pot, dict) and ('origin' in pot or 'country_type' in pot):
            selectable = 0
        if isinstance(playable, dict) and str(playable.get('always')).strip() == 'no':
            selectable = 0

        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        desc_key = odef.get('description')
        desc_key = desc_key.strip('"') if isinstance(desc_key, str) else None
        raw_desc = loc.get(desc_key) if desc_key else None
        description = (_clean_loc(raw_desc, loc, keep_newlines=True, variables=variables)
                      if raw_desc else None)
        icon = odef.get('icon')
        icon = icon.strip('"') if isinstance(icon, str) else None

        asc = ascension.get(key) or {}
        starting_colony = odef.get('starting_colony')
        starting_colony = starting_colony.strip('"') if isinstance(starting_colony, str) else None
        # Origin forces a fixed home system when it lists an initializer that isn't one
        # of the standard custom-empire starts (origins like arc_welders just list the
        # standard set → no override).
        inits = [ik for ik in _to_list(odef.get('initializers')) if isinstance(ik, str)]
        forces_home_system = 1 if any(ik not in init_custom for ik in inits) else 0
        w.execute(
            """INSERT OR REPLACE INTO origin
                   (key, name, description, required_dlc, selectable, ascension_lock,
                    ascension_blocks, starting_colony, forces_home_system, icon, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, description, dlc, selectable, asc.get('lock'),
             ','.join(asc.get('blocks', [])) or None, starting_colony, forces_home_system, icon, order),
        )
        if verbose:
            print(f"  origin {key} | {name}{'' if selectable else ' (not selectable)'}")
        order += 1

        modifier = odef.get('modifier')
        if isinstance(modifier, dict):
            for m_order, (mk, mv) in enumerate(modifier.items()):
                s = str(mv).strip()
                value = variables.get(s) if s.startswith('@') else _resolve_modifier_float(mv)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR IGNORE INTO origin_modifier
                           (origin_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, value, m_order),
                )
                _write_modifier_label(w, loc, mk)

        for kind, op, val, gid in _civic_requirements(odef):
            w.execute(
                """INSERT OR IGNORE INTO origin_requirement
                       (origin_key, kind, operator, value, group_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, kind, op, val, gid),
            )

    w.commit()
    return order


# ---------------------------------------------------------------------------
# Extraction: planet classes
# ---------------------------------------------------------------------------

# Standard uninhabitable system bodies kept (beyond the colonizable worlds) so the
# reference covers home-system contents too; excludes stars and one-off event worlds.
_STANDARD_UNINHABITABLE = frozenset({
    'pc_barren', 'pc_barren_cold', 'pc_molten', 'pc_toxic', 'pc_frozen',
    'pc_gas_giant', 'pc_asteroid',
})


def extract_planet_classes(data: dict, loc: dict[str, str], w: Writer, verbose: bool):
    """Colonizable planet classes (+ the standard uninhabitable bodies) → planet_class."""
    order = 0
    for key, pdef in data.items():
        if not key.startswith('pc_') or not isinstance(pdef, dict):
            continue
        colonizable = 1 if str(pdef.get('colonizable')).strip() == 'yes' else 0
        if not colonizable and key not in _STANDARD_UNINHABITABLE:
            continue
        climate = pdef.get('climate')
        climate = climate.strip('"') if isinstance(climate, str) else None
        is_standard = 1 if str(pdef.get('initial')).strip() == 'yes' else 0
        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        w.execute(
            """INSERT OR REPLACE INTO planet_class
                   (key, name, climate, colonizable, is_standard, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key, name, climate, colonizable, is_standard, order),
        )
        if verbose:
            print(f"  planet_class {key} | {name} | climate={climate} colonizable={colonizable}")
        order += 1
    w.commit()


# ---------------------------------------------------------------------------
# Extraction: prescripted (predefined) empires
# ---------------------------------------------------------------------------

def extract_prescripted_empires(data: dict, loc: dict[str, str], w: Writer,
                                order_start: int, verbose: bool) -> int:
    """Extract one prescripted_countries file → prescripted_empire (+ _item)."""
    order = order_start
    for key, edef in data.items():
        if key.startswith('@') or not isinstance(edef, dict):
            continue
        species = edef.get('species')
        if not isinstance(species, dict):
            continue  # not an empire design

        # Resolve a name-ish field (loc key or literal) to display text. Prescripted
        # files carry localisation keys (PRESCRIPTED_…); user files carry literals.
        def loc_name(raw):
            if isinstance(raw, dict):  # name = { key = "…" }
                raw = raw.get('key') or raw.get('name')
            if not isinstance(raw, str):
                return None
            return _clean_loc(loc[raw], loc) if raw in loc else raw

        name = loc_name(edef.get('name')) or key
        ruler = edef.get('ruler')
        ruler_name = loc_name(ruler.get('name')) if isinstance(ruler, dict) else None
        # Second founding species (Syncretic Evolution / Necrophage / Tebrid …) — same
        # block shape as `species`; absent for most empires.
        sec = edef.get('secondary_species')
        sec = sec if isinstance(sec, dict) else {}

        def scalar(v):
            return v if isinstance(v, str) else None

        civics_raw = edef.get('civics')
        civics = _to_list(civics_raw) if not isinstance(civics_raw, dict) else []

        w.execute(
            """INSERT OR REPLACE INTO prescripted_empire
                   (key, name, adjective, ship_prefix, species_class, species_portrait,
                    species_name, species_plural, species_adjective,
                    secondary_class, secondary_portrait, secondary_name,
                    secondary_plural, secondary_adjective, ruler_name,
                    planet_name, system_name, authority, government,
                    origin, planet_class, initializer, graphical_culture, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, name, loc_name(edef.get('adjective')), loc_name(edef.get('ship_prefix')),
             scalar(species.get('class')), scalar(species.get('portrait')),
             loc_name(species.get('name')), loc_name(species.get('plural')),
             loc_name(species.get('adjective')),
             scalar(sec.get('class')), scalar(sec.get('portrait')),
             loc_name(sec.get('name')), loc_name(sec.get('plural')),
             loc_name(sec.get('adjective')), ruler_name,
             loc_name(edef.get('planet_name')), loc_name(edef.get('system_name')),
             scalar(edef.get('authority')), scalar(edef.get('government')),
             scalar(edef.get('origin')), scalar(edef.get('planet_class')),
             scalar(edef.get('initializer')), scalar(edef.get('graphical_culture')), order),
        )
        if verbose:
            print(f"  empire {key} | {name} | {scalar(species.get('class'))}")
        order += 1

        items = ([('trait', t) for t in _to_list(species.get('trait'))]
                 + [('secondary_trait', t) for t in _to_list(sec.get('trait'))]
                 + [('civic', c) for c in civics]
                 + [('ethic', e) for e in _to_list(edef.get('ethic'))])
        for i_order, (kind, value) in enumerate(items):
            if not isinstance(value, str):
                continue
            w.execute(
                """INSERT OR IGNORE INTO prescripted_empire_item
                       (empire_key, kind, value, sort_order)
                   VALUES (?, ?, ?, ?)""",
                (key, kind, value, i_order),
            )
    w.commit()
    return order


def extract_initializers(data: dict, loc: dict[str, str], w: Writer, order_start: int) -> int:
    """Extract one solar_system_initializers file → the initializers flagged
    `usage = custom_empire` (the home systems a custom empire can start in)."""
    order = order_start
    for key, idef in data.items():
        if key.startswith('@') or not isinstance(idef, dict):
            continue
        if str(idef.get('usage')).strip() != 'custom_empire':
            continue
        raw = idef.get('name')
        if isinstance(raw, dict):
            raw = raw.get('key') or raw.get('name')
        name = None
        if isinstance(raw, str):
            name = _clean_loc(loc[raw], loc) if raw in loc else raw
        w.execute("INSERT OR REPLACE INTO initializer (key, name, sort_order) VALUES (?, ?, ?)",
                  (key, name, order))
        order += 1
    w.commit()
    return order


def extract_rooms(install_dir: Path, w: Writer, verbose: bool) -> None:
    """The distinct diplomacy rooms the prescripted empires use → room table (rooms are
    gfx entities with no loc, so their keys are the source of truth)."""
    pdir = install_dir / 'prescripted_countries'
    rooms: set[str] = set()
    if pdir.is_dir():
        for f in sorted(pdir.glob('*.txt')):
            text = f.read_text(encoding='utf-8', errors='ignore')
            rooms.update(re.findall(r'\broom\s*=\s*"([a-z_0-9]+)"', text))

    def label(k: str) -> str:
        s = re.sub(r'_room$', '', k)
        s = re.sub(r'^personality_', '', s)
        return s.replace('_', ' ').title()

    # default_room first, then alphabetical by label.
    for order, k in enumerate(sorted(rooms, key=lambda r: (r != 'default_room', label(r)))):
        w.execute("INSERT OR REPLACE INTO room (key, label, sort_order) VALUES (?, ?, ?)",
                  (k, label(k), order))
    w.commit()
    if verbose:
        print(f"  rooms: {len(rooms)}")


def extract_flag_assets(install_dir: Path, w: Writer, verbose: bool) -> None:
    """Scan the flags/ directory → flag_asset rows (icon files per category +
    background files). The .dds files can't be rendered, but their names are what the
    empire_flag block references."""
    flags_dir = install_dir / 'flags'
    if not flags_dir.is_dir():
        print(f"  WARNING: {flags_dir} not found", file=sys.stderr)
        return
    n = 0
    for sub in sorted(p for p in flags_dir.iterdir() if p.is_dir()):
        kind = 'background' if sub.name == 'backgrounds' else 'icon'
        for order, f in enumerate(sorted(sub.glob('*.dds'))):
            w.execute(
                "INSERT OR REPLACE INTO flag_asset (kind, category, file, sort_order) VALUES (?, ?, ?, ?)",
                (kind, sub.name, f.name, order))
            n += 1
    w.commit()
    if verbose:
        print(f"  flag assets: {n}")


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
#   homeworld_event    event id whose option grants the one-time "Homeworld reward"
#                      (resource bursts + minor artifacts) on discovering the home
#                      world — parsed into precursor_homeworld_reward. None where the
#                      capstone grants only the relic (Baol, adAkkaria).
#   technology         (tech_key, building_key, scope) — the "Secrets of …" tech
#                      (granted by the discovery special project) and the empire-wide
#                      building it unlocks. None for the Cosmic Storms precursors,
#                      which have no such tech.
PRECURSORS: list[dict] = [
    {'key': 'precursor_1', 'dlc': None, 'system': 'vultaumar_system', 'num_digsites': None,
     'relic': 'r_reality_perforator',
     'rewards': [('artifact_vultaum_research_completed_suppressed', 'suppressed'),
                 ('artifact_vultaum_research_completed_public', 'public'),
                 ('artifact_vultaum_research_completed_machine', 'machine'),
                 ('artifact_vultaum_research_completed_hive', 'hive')],
     'building': None, 'homeworld_event': 'precursor.101',
     'technology': ('tech_secrets_vultaum', 'building_vultaum_reality_computer', 'planet')},
    {'key': 'precursor_2', 'dlc': None, 'system': 'yuhtaan_system', 'num_digsites': None,
     'relic': 'r_cryo_core',
     'rewards': [],  # Yuht grant no permanent empire modifier; their homeworld gets pm_yuht_cleansing
     'building': None, 'homeworld_event': 'precursor.601',
     'technology': ('tech_secrets_yuht', 'yuht_detection_array', 'starbase')},
    {'key': 'precursor_3', 'dlc': None, 'system': 'fen_habbanis_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_league_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.1101',
     'technology': ('tech_secrets_league', 'building_league_offices', 'planet')},
    {'key': 'precursor_4', 'dlc': None, 'system': 'irass_system', 'num_digsites': None,
     'relic': None,
     'rewards': [('artifact_irassian_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.1601',
     'technology': ('tech_secrets_irassian', 'irassian_naval_yards', 'starbase')},
    {'key': 'precursor_5', 'dlc': None, 'system': 'cybrex_system', 'num_digsites': None,
     'relic': 'r_war_forge',
     'rewards': [('artifact_cybrex_research_completed', 'default')],
     'building': None, 'homeworld_event': 'precursor.2101',
     'technology': ('tech_secrets_cybrex', 'cybrex_mining_hub', 'starbase')},
    {'key': 'precursor_baol_1', 'dlc': 'Ancient Relics Story Pack', 'system': 'last_baol_system',
     'num_digsites': 4,
     'relic': 'r_the_last_baol',
     'rewards': [('artifact_baol_research_completed', 'default')],
     'building': None, 'homeworld_event': None,  # capstone (ancrel.2022/2023) grants only the relic
     'technology': ('tech_secrets_baol', 'building_baol_organic_plant', 'planet')},
    {'key': 'precursor_zroni_1', 'dlc': 'Ancient Relics Story Pack',
     'system': 'zroni_legacy_initializer_01', 'num_digsites': 6,
     'relic': 'r_zro_crystal',
     'rewards': [('artifact_zroni_research_completed', 'default')],
     'building': ('building_zroni_equilibrator', 'planet'), 'homeworld_event': 'ancrel.19',
     'technology': ('tech_secrets_zroni', 'zroni_storm_caster', 'starbase')},
    {'key': 'precursor_inetian', 'dlc': 'Cosmic Storms', 'system': None, 'num_digsites': None,
     'relic': 'r_weather_manipulator',
     'rewards': [('secrets_of_the_inetian_traders', 'default'),
                 ('secrets_of_the_inetian_traders_hive_mach', 'hive_machine')],
     'building': None, 'homeworld_event': 'cstorms.193', 'technology': None},
    {'key': 'precursor_adakkaria', 'dlc': 'Cosmic Storms', 'system': 'adSul_system',
     'num_digsites': None,
     'relic': 'r_the_tempest_invocator',
     'rewards': [('patriotic_institute_true_potential_unleashed', 'default')],
     'building': ('building_adakkaria_patriotic_institute', 'planet'),
     'homeworld_event': None,  # capstone (cstorms.2355) grants only the relic
     'technology': None},
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
    # The attunement modifiers name their target only via an unresolvable colour
    # command ([This.GetCradleColor] etc.), leaving a dangling "Add Attunement with".
    # Derive the entity name from the key so it reads "Add Attunement with The …".
    if mod_key.startswith('add_attunement_'):
        entity = mod_key[len('add_attunement_'):].replace('_', ' ').title()
        label = f'Add Attunement with {entity}'
    # Per-job production modifiers (planet_<job>_<resource>_produces_*) name the job
    # via a scripted-loc command ([GetSoldierSwapPluralWithIcon]) that can't resolve,
    # so the cleaned label trails off as "<Resource> Produced per 100" / "… from".
    # The job is in the key — append it (the resource is already shown, so strip it
    # off the key tail to find the job): "Unity Produced per 100" → "… Soldiers".
    mprod = re.search(r'Produced (?:per \d+|from)$', label)
    keym = re.match(r'^planet_(.+)_produces_(?:add|mult)$', mod_key)
    if mprod and keym:
        resource_tok = label[:mprod.start()].strip().lower().replace(' ', '_')
        inner = keym.group(1)                      # e.g. "soldiers_unity"
        if resource_tok and inner.endswith('_' + resource_tok):
            job = inner[: -(len(resource_tok) + 1)]
            if job:
                label = f"{label} {job.replace('_', ' ').title()}"
    # Same dropped-job problem for per-job upkeep ("Upkeep for" → "Upkeep for X").
    keyu = re.match(r'^planet_(.+)_upkeep_(?:add|mult)$', mod_key)
    if keyu and label.endswith(' for'):
        label = f"{label} {keyu.group(1).replace('_', ' ').title()}"
    # Attunement % modifiers (<entity>_attunement_<add|mult>): the target sits in an
    # unresolvable colour command, leaving "Attunement with" — append the entity.
    am = re.match(r'^(.+)_attunement_(?:add|mult)$', mod_key)
    if am and label.rstrip().endswith('with'):
        label = f"{label.rstrip()} {am.group(1).replace('_', ' ').title()}"
    if '$' in label:
        return
    # Lower is better for any cost/upkeep modifier (a negative value is *good*, shown
    # green) — the curated set plus every `*_cost_*` / `*_upkeep_*` key (e.g.
    # planet_structures_cost_mult −15% is a saving, not a penalty). Crime is the same:
    # less crime is better, so `planet_crime_*` reductions also read as good.
    inverted = (mod_key in INVERTED_MODIFIERS
                or re.search(r'(?:_cost|_upkeep)_(?:mult|add)$|crime', mod_key))
    pig = 0 if inverted else 1
    w.execute(
        """INSERT OR REPLACE INTO modifier_label (modifier_key, label, positive_is_good)
           VALUES (?, ?, ?)""",
        (mod_key, label, pig),
    )


def _relic_condition(potential) -> str | None:
    """Render a relic passive's 'potential' block into a parseable condition string.

    Top-level entries are ANDed (comma-separated); an OR sub-block is rendered
    parenthesised as "(a = x OR b = y)" so consumers can evaluate it (e.g. against
    an empire type). None when the passive always applies (no potential / always = yes)."""
    if not isinstance(potential, dict):
        return None
    parts = []
    for key, val in potential.items():
        if key == 'always':
            continue
        if isinstance(val, str):
            parts.append(f"{key} = {val}")
        elif key == 'OR' and isinstance(val, dict):
            ors = [f"{k} = {v}" for k, v in val.items() if isinstance(v, str)]
            if ors:
                parts.append("(" + " OR ".join(ors) + ")")
        else:
            # Other nested operators: keep the operator name as a marker.
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
    """Join the non-generic custom_tooltip strings of a relic's active_effect.

    Relics whose active is a pure resource transaction (e.g. the Cybrex War Forge)
    carry no descriptive tooltip — their effect is captured structurally by
    _relic_resources('produces') instead."""
    active = relic.get('active_effect')
    tips = active.get('custom_tooltip') if isinstance(active, dict) else None
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


def _relic_passive_texts(relic: dict, loc: dict[str, str]) -> list[tuple[str, str | None]]:
    """Non-numeric passive effects (a relic's triggered_country_modifier
    custom_tooltips) as [(text, condition)]. Each block's `potential` becomes the
    condition (NULL = always), so a conditional passive (e.g. the Cryo Core's
    is_wilderness_empire = yes district) is shown with its qualifier, like the
    numeric passives — not as if it always applied."""
    blocks = relic.get('triggered_country_modifier')
    if isinstance(blocks, dict):
        blocks = [blocks]
    elif not isinstance(blocks, list):
        return []
    rows: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        condition = _relic_condition(block.get('potential'))
        tips = block.get('custom_tooltip')
        if isinstance(tips, str):
            tips = [tips]
        elif not isinstance(tips, list):
            tips = []
        for key in tips:
            raw = loc.get(key)
            if not raw:
                continue
            cleaned = _clean_loc(raw, loc)
            if cleaned and '$' not in cleaned and cleaned not in seen:
                seen.add(cleaned)
                rows.append((cleaned, condition))
    return rows


def _relic_resources(relic: dict, variables: dict[str, float]) -> list[tuple[str, str, float, int]]:
    """Resources of the triumph effect → [(kind, resource, amount, scales)].

    'cost' from `resources.cost` (resources spent to activate), 'produces' from the
    direct `add_resource` children of `active_effect` (resources gained). A block
    with a `multiplier`/`mult` scales with use (e.g. Cybrex `cybrex_count`: 1000
    alloys at first use, +1000 each use). Note the produced amount is read from the
    script (1000), not the loc tooltip — which can be stale/wrong."""
    rows: list[tuple[str, str, float, int]] = []

    def add_blocks(blocks, kind: str):
        if isinstance(blocks, dict):
            blocks = [blocks]
        elif not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            scales = 1 if ('multiplier' in block or 'mult' in block) else 0
            for res, raw in block.items():
                if res in ('multiplier', 'mult', 'category'):
                    continue
                s = str(raw).strip()
                amount = variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)
                if amount is not None:
                    rows.append((kind, res, amount, scales))

    resources = relic.get('resources')
    if isinstance(resources, dict):
        add_blocks(resources.get('cost'), 'cost')
    active = relic.get('active_effect')
    if isinstance(active, dict):
        # Only direct add_resource children are "produces" — nested ones (e.g. the
        # tempest invocator's on_cancel refund) are not part of the triumph payout.
        add_blocks(active.get('add_resource'), 'produces')
    return rows


def _relic_cooldown(relic: dict, variables: dict[str, float]) -> int | None:
    """The triumph cooldown in days. Read from the `relic_activation_cooldown`
    add_modifier inside active_effect (the value the game actually applies — e.g.
    the War Forge's short cooldown), falling back to `activation_duration`."""
    def find_days(node) -> int | None:
        if isinstance(node, list):
            for item in node:
                d = find_days(item)
                if d is not None:
                    return d
        elif isinstance(node, dict):
            if node.get('modifier') == 'relic_activation_cooldown' and 'days' in node:
                raw = str(node['days']).strip()
                val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
                return int(val) if val is not None else None
            for v in node.values():
                d = find_days(v)
                if d is not None:
                    return d
        return None

    days = find_days(relic.get('active_effect'))
    if days is not None:
        return days
    raw = str(relic.get('activation_duration', '')).strip()
    val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
    return int(val) if val is not None else None


def _find_first(node, key: str):
    """Depth-first search for the first value at `key` anywhere in node."""
    if isinstance(node, list):
        for item in node:
            r = _find_first(item, key)
            if r is not None:
                return r
    elif isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = _find_first(v, key)
            if r is not None:
                return r
    return None


def _relic_event_id(active_effect) -> str | None:
    """The country_event id a triumph's active_effect fires (e.g. precursor.105)."""
    for ce in _as_blocks(_find_first(active_effect, 'country_event')):
        if isinstance(ce.get('id'), str):
            return ce['id']
    return None


def _entry_years(m: dict, variables: dict[str, float]) -> int | None:
    """Duration of an add_modifier block in years (resolving @vars; days/360, months/12)."""
    for unit_key, divisor in (('years', 1), ('months', 12), ('days', 360)):
        if unit_key in m:
            raw = str(m[unit_key]).strip()
            val = variables.get(raw) if raw.startswith('@') else _resolve_modifier_float(raw)
            if val is not None:
                return max(1, round(val / divisor))
    return None


def _resolve_static_modifier(name: str, static_mods: dict) -> list[tuple[str, float]]:
    """A static modifier name → its numeric (modifier_key, value) effects."""
    block = static_mods.get(name)
    if isinstance(block, list):
        merged: dict = {}
        for b in block:
            if isinstance(b, dict):
                merged.update(b)
        block = merged
    if not isinstance(block, dict):
        return []
    # The game hides the numeric effects of a show_only_custom_tooltip modifier
    # (e.g. the Weather Manipulator's storm_relic_repelling_field_modifier — its
    # effect is conveyed by the active_effect's custom_tooltip text instead).
    if block.get('show_only_custom_tooltip') == 'yes':
        return []
    out: list[tuple[str, float]] = []
    for mod_key, mod_val in block.items():
        if mod_key in _NON_MODIFIER_KEYS:
            continue
        value = _resolve_modifier_float(mod_val)
        if value is not None:
            out.append((mod_key, value))
    return out


def _collect_add_modifiers(node, variables: dict[str, float]) -> list[tuple[str, int | None]]:
    """Every `add_modifier` (modifier + duration) anywhere in a tree, except the
    `relic_activation_cooldown`. Recursive so it catches modifiers nested in e.g.
    `start_storm_area_placing > on_confirm` (the Tempest Invocator)."""
    out: list[tuple[str, int | None]] = []

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
        elif isinstance(n, dict):
            for am in _as_blocks(n.get('add_modifier')):
                name = am.get('modifier')
                if isinstance(name, str) and name != 'relic_activation_cooldown':
                    out.append((name, _entry_years(am, variables)))
            for k, v in n.items():
                if k != 'add_modifier':
                    walk(v)

    walk(node)
    return out


def _relic_active_outcomes(relic: dict, events_index: dict, static_mods: dict,
                           variables: dict[str, float], loc: dict[str, str]
                           ) -> tuple[list[tuple[str, float, str | None]], str | None, str | None]:
    """The temporary modifiers a triumph grants → ([(modifier_key, value, group_label)],
    detail_text, mode). Three sources / modes:
    - **'direct'**: an `add_modifier` in active_effect (Yuht Cryo Core: −20% ship
      upkeep for @triumph_duration = 10 years);
    - **'random'**: a `country_event`'s `(locked_)random_list` (Reality Perforator →
      precursor.105 → one of reality_perforator_1..4 at random, or all as a jackpot);
    - **'choice'**: a `country_event`'s `option`s, each `add_modifier`ing a named
      guidance (Zro Crystal → ancrel.7006 → Martial / Statecraft Guidance for 5 years;
      `group_label` is the option's modifier loc name). Non-modifier options (e.g. a
      ruler level) are appended to the detail."""
    active = relic.get('active_effect')
    active_mods: list[tuple[str, float, str | None]] = []
    single_years: list[int] = []
    grouped_years: list[int] = []
    text_options: list[str] = []
    mode: str | None = None

    # Source A: an add_modifier in active_effect (recursive — the Tempest Invocator's
    # is nested in start_storm_area_placing > on_confirm), excluding the cooldown.
    for name, years in _collect_add_modifiers(active, variables):
        mode = 'direct'
        if years is not None:
            single_years.append(years)
        for mk, val in _resolve_static_modifier(name, static_mods):
            active_mods.append((mk, val, None))

    event = events_index.get(_relic_event_id(active)) if not active_mods else None

    # Source B: an event's (locked_)random_list.
    if not active_mods:
        rlist = _find_first(event, 'locked_random_list') or _find_first(event, 'random_list')
        if isinstance(rlist, dict):
            mode = 'random'
            ordered: list[str] = []
            for entries in rlist.values():          # keys are weights; values are entry blocks
                for entry in _as_blocks(entries):
                    mods = [m for m in _as_blocks(entry.get('add_modifier'))
                            if isinstance(m.get('modifier'), str)]
                    if not mods:
                        continue
                    for m in mods:
                        if m['modifier'] not in ordered:
                            ordered.append(m['modifier'])
                    years = _entry_years(mods[0], variables)
                    if years is not None:
                        (single_years if len(mods) == 1 else grouped_years).append(years)
            for name in ordered:
                for mk, val in _resolve_static_modifier(name, static_mods):
                    active_mods.append((mk, val, None))

    # Source C: an event's options (a choice). Each option add_modifiers a guidance.
    if not active_mods and isinstance(event, dict):
        for opt in _as_blocks(event.get('option')):
            opt_mods = [m for m in _as_blocks(opt.get('add_modifier'))
                        if isinstance(m.get('modifier'), str)]
            if opt_mods:
                mode = 'choice'
                for am in opt_mods:
                    name = am['modifier']
                    years = _entry_years(am, variables)
                    if years is not None:
                        single_years.append(years)
                    glabel = _clean_loc(loc[name], loc) if name in loc else None
                    for mk, val in _resolve_static_modifier(name, static_mods):
                        active_mods.append((mk, val, glabel))
            else:
                tip = opt.get('custom_tooltip')
                if isinstance(tip, str) and tip not in _GENERIC_RELIC_TOOLTIPS and tip in loc:
                    t = _clean_loc(loc[tip], loc)
                    if t and '$' not in t and t not in text_options:
                        text_options.append(t)

    if not active_mods:
        return [], None, None

    # Build the "how it's granted" detail.
    def years_word(n: int) -> str:
        return f"{n} year" if n == 1 else f"{n} years"

    dur = None
    if single_years:
        lo, hi = min(single_years), max(single_years)
        dur = f"{lo}–{hi} years" if lo != hi else years_word(lo)

    parts: list[str] = []
    if mode == 'choice':
        parts.append(f"choose one, for {dur}" if dur else "choose one")
        if text_options:
            parts.append("or " + "; ".join(t.rstrip('. ') for t in text_options))
    elif mode == 'random':
        if dur:
            parts.append(f"one at random for {dur}" if len(active_mods) > 1 else f"for {dur}")
        if grouped_years:
            parts.append(f"or all of them for {years_word(min(grouped_years))}")
    elif dur:                                          # direct
        parts.append(f"for {dur}")
    detail = ", ".join(parts)
    detail = detail[0].upper() + detail[1:] + "." if detail else None
    return active_mods, detail, mode


# Flat minor_artifacts payout bounds (min..max) for the artifact-reward scripted
# effects (common/scripted_effects/archaeology_event_effects.txt). Each is a 1/3
# random over three amounts; we keep the bounds for display.
_ARTIFACT_REWARD_BOUNDS = {
    'small_artifact_reward':  (50.0, 250.0),
    'medium_artifact_reward': (300.0, 500.0),
    'large_artifact_reward':  (750.0, 1500.0),
}


def _homeworld_reward_rows(event: dict, variables: dict[str, float]
                           ) -> list[tuple[str, float | None, float, float]]:
    """Parse a precursor's home-world discovery event into reward rows
    [(resource, multiplier, min_amount, max_amount)].

    Two shapes: `add_monthly_resource_mult` blocks (a `value` multiplier on the
    empire's current monthly output of `resource`, clamped to [min, max]) and the
    flat `*_artifact_reward = yes` minor-artifacts payout (resource =
    'minor_artifacts', multiplier = None). The reward repeats identically across
    the event's options (one per end state), so we dedupe by resource — first
    occurrence wins — and resolve @tier* scripted variables via `variables`."""
    rows: list[tuple[str, float | None, float, float]] = []
    seen: set[str] = set()

    def num(raw):
        s = str(raw).strip()
        return variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for block in _as_blocks(node.get('add_monthly_resource_mult')):
            res = block.get('resource')
            if not isinstance(res, str) or res in seen:
                continue
            lo, hi = num(block.get('min')), num(block.get('max'))
            if lo is None or hi is None:
                continue
            seen.add(res)
            rows.append((res, num(block.get('value')), lo, hi))
        for art_key, (lo, hi) in _ARTIFACT_REWARD_BOUNDS.items():
            if str(node.get(art_key)).strip() == 'yes' and 'minor_artifacts' not in seen:
                seen.add('minor_artifacts')
                rows.append(('minor_artifacts', None, lo, hi))
        for v in node.values():
            walk(v)

    walk(event)
    return rows


def _resolve_var(raw, variables: dict[str, float]) -> float | None:
    """Resolve a scalar that may be an @scripted_variable reference or a literal."""
    if raw is None:
        return None
    s = str(raw).strip()
    return variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)


def _building_cap(raw, variables: dict[str, float]) -> int | None:
    """A building build-cap → int, or None when uncapped. Accepts a flat value
    (`base_cap_amount = 1`) or a block whose `base` is the baseline cap
    (`empire_limit = { base = 1 … }`)."""
    if isinstance(raw, list):
        raw = next((b for b in reversed(raw) if isinstance(b, dict)), raw[-1] if raw else None)
    if isinstance(raw, dict):
        raw = raw.get('base')
    v = _resolve_var(raw, variables)
    return int(v) if v is not None else None


def _building_resources(bdef: dict, variables: dict[str, float]
                        ) -> list[tuple[str, str, float]]:
    """A building's resources block → [(kind, resource, amount)] for
    kind in cost/upkeep/produces, resolving @vars. Where a kind has several blocks
    (e.g. a conditional `is_wilderness_empire` cost), the unconditional one (no
    `trigger`) is the primary; the gated variants are skipped."""
    rows: list[tuple[str, str, float]] = []
    res = bdef.get('resources')
    if isinstance(res, list):
        res = next((b for b in reversed(res) if isinstance(b, dict)), None)
    if not isinstance(res, dict):
        return rows
    for kind in ('cost', 'upkeep', 'produces'):
        blocks = _as_blocks(res.get(kind))
        block = next((b for b in blocks if 'trigger' not in b), blocks[0] if blocks else None)
        if not isinstance(block, dict):
            continue
        for resource, raw in block.items():
            if resource in ('category', 'trigger'):
                continue
            amt = _resolve_var(raw, variables)
            if amt is not None:
                rows.append((kind, resource, amt))
    return rows


# Building modifier sources: (scope tag, field, is_triggered). The `triggered_`
# variants carry a `potential` gate — we keep their value but tag the condition.
_BUILDING_MOD_FIELDS = (
    ('planet', 'planet_modifier', False),
    ('country', 'country_modifier', False),
    ('planet', 'triggered_planet_modifier', True),
    ('country', 'triggered_country_modifier', True),
    ('system', 'triggered_system_modifier', True),
    ('station', 'triggered_station_modifier', True),
)
_BUILDING_MOD_SKIP = frozenset({'potential', 'trigger', 'multiplier', 'mult', 'category',
                                'modifier', 'desc', 'replace'})


def _flatten_text(node) -> str:
    """Render a parsed (nested) block back to a flat, searchable 'k = v' string."""
    if isinstance(node, dict):
        return ' '.join(f"{k} = {_flatten_text(v)}" for k, v in node.items())
    if isinstance(node, list):
        return ' '.join(_flatten_text(x) for x in node)
    return str(node)


_AE_PERK = 'ap_archaeoengineers'


def _perk_gate(block: dict) -> tuple[str, bool] | None:
    """A triggered modifier's ascension-perk gate as (perk_key, negated), or None
    when it has no such gate. `negated` is True for a NOT-wrapped requirement (the
    'base, only without the perk' variant)."""
    txt = _flatten_text(block.get('potential'))
    m = re.search(r'has_ascension_perk\s*=\s*(ap_\w+)', txt)
    if not m:
        return None
    pre = txt[:m.start()].rstrip()
    negated = bool(re.search(r'NO[RT]\s*=$', pre)) or bool(re.search(r'\bNO[RT]\b[^=]{0,16}$', pre))
    return (m.group(1), negated)


def _building_modifiers(bdef: dict, variables: dict[str, float]
                        ) -> list[tuple[str, str, float, float | None, str | None]]:
    """A building's modifiers, aggregated per (scope, key) into
    [(scope, key, base_value, ae_value, condition)].

    base_value is the value with no ascension perks; ae_value is the *total* with
    Archaeo-Engineers (None when unchanged). A block gated `NOT archaeoengineers`
    contributes to base only (it's replaced under the perk); an un-perk-gated block
    contributes to both (the AE bonus stacks on it); an `archaeoengineers` block
    contributes to the AE total only. Blocks gated by a *different* perk become their
    own rows tagged with that perk key in `condition`."""
    # acc[(scope, key)] = {'base': float, 'ae': float, 'ae_seen': bool, 'other': {perk: float}}
    acc: dict[tuple[str, str], dict] = {}

    def slot(scope, key):
        return acc.setdefault((scope, key),
                              {'base': 0.0, 'ae': 0.0, 'ae_seen': False, 'other': {}})

    for scope, field, triggered in _BUILDING_MOD_FIELDS:
        for block in _as_blocks(bdef.get(field)):
            gate = _perk_gate(block) if triggered else None
            for key, raw in block.items():
                if key in _BUILDING_MOD_SKIP:
                    continue
                val = _resolve_var(raw, variables)
                if val is None:
                    continue
                s = slot(scope, key)
                if gate is None:                       # always applies (base and AE)
                    s['base'] += val
                    s['ae'] += val
                elif gate[0] == _AE_PERK and gate[1]:  # base only (NOT archaeoengineers)
                    s['base'] += val
                elif gate[0] == _AE_PERK:              # the Archaeo-Engineers value
                    s['ae'] += val
                    s['ae_seen'] = True
                else:                                  # gated by a different perk
                    s['other'][gate[0]] = s['other'].get(gate[0], 0.0) + val

    rows: list[tuple[str, str, float, float | None, str | None]] = []
    for (scope, key), s in acc.items():
        if s['ae_seen'] or s['base']:
            ae = s['ae'] if s['ae_seen'] and abs(s['ae'] - s['base']) > 1e-9 else None
            rows.append((scope, key, s['base'], ae, None))
        for perk, v in s['other'].items():
            rows.append((scope, key, v, None, perk))
    return rows


def _building_inline_jobs(bdef: dict, variables: dict[str, float], inline_scripts_dir
                          ) -> list[tuple[str, float]]:
    """Jobs a building adds via its `jobs/*` inline_scripts → [(job_<x>_add, amount)].

    Each jobs script branches by empire type; the first triggered_planet_modifier
    is the regular-empire variant (gestalt/FE empires get calculator/archivist
    equivalents of the same count). The building supplies the count as `AMOUNT`."""
    rows: list[tuple[str, float]] = []
    for ref in _as_blocks(bdef.get('inline_script')):
        script = ref.get('script')
        if not isinstance(script, str) or not script.startswith('jobs/'):
            continue
        amount = _resolve_var(ref.get('AMOUNT'), variables)
        if amount is None:
            continue
        path = inline_scripts_dir / (script + '.txt')
        if not path.exists():
            continue
        try:
            blocks = _as_blocks(parse_file(path).get('triggered_planet_modifier'))
        except Exception:
            continue
        if not blocks:
            continue
        first = blocks[0]
        modblock = first.get('modifier') if isinstance(first.get('modifier'), dict) else first
        for k in modblock:
            if k.startswith('job_') and k.endswith('_add'):
                rows.append((k, amount))
    return rows


def extract_precursors(precursor_civ: dict, relics: dict, static_mods: dict,
                       events_index: dict, buildings: dict, technologies: dict,
                       inline_scripts_dir, scripted_vars: dict[str, float],
                       loc: dict[str, str], w: Writer, verbose: bool):
    # @variables for resolving relic costs/cooldowns: this file's own top-level
    # @defs plus the global scripted_variables (where @activation_cost etc. live).
    relic_vars: dict[str, float] = dict(scripted_vars)
    for vk, vv in relics.items():
        if vk.startswith('@'):
            f = _resolve_modifier_float(vv)
            if f is not None:
                relic_vars[vk] = f

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
                active_mods, active_detail, active_mode = _relic_active_outcomes(
                    relic, events_index, static_mods, relic_vars, loc)
                w.execute(
                    """INSERT OR REPLACE INTO precursor_relic
                           (precursor_key, relic_key, name, portrait, score, description,
                            active_effect, active_mode, active_detail, triumph_cooldown_days)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, relic_key, loc.get(relic_key), portrait, score,
                     _clean_loc(loc[relic_key + '_desc'], loc) if (relic_key + '_desc') in loc else None,
                     _relic_active_text(relic, loc),
                     active_mode,
                     active_detail,
                     _relic_cooldown(relic, relic_vars)),
                )
                for t_order, (text, condition) in enumerate(_relic_passive_texts(relic, loc)):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_passive_text
                               (relic_key, text, condition, sort_order)
                           VALUES (?, ?, ?, ?)""",
                        (relic_key, text, condition, t_order),
                    )
                for a_order, (mod_key, value, group_label) in enumerate(active_mods):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_active_modifier
                               (relic_key, modifier_key, modifier_value, group_label, sort_order)
                           VALUES (?, ?, ?, ?, ?)""",
                        (relic_key, mod_key, value, group_label, a_order),
                    )
                    _write_modifier_label(w, loc, mod_key)
                    if verbose:
                        print(f"    relic {relic_key} active | {mod_key} = {value}"
                              f"{' [' + group_label + ']' if group_label else ''}")
                for c_order, (kind, res, amount, scales) in enumerate(_relic_resources(relic, relic_vars)):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_relic_resource
                               (relic_key, kind, resource, amount, scales, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (relic_key, kind, res, amount, scales, c_order),
                    )
                    if verbose:
                        print(f"    relic {relic_key} {kind} | {res} = {amount}"
                              f"{' (scales)' if scales else ''}")
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

        # --- one-time home-world discovery reward (resource bursts + artifacts) ---
        hw_event = events_index.get(p['homeworld_event']) if p.get('homeworld_event') else None
        if hw_event:
            for hw_order, (res, mult, lo, hi) in enumerate(
                    _homeworld_reward_rows(hw_event, relic_vars)):
                w.execute(
                    """INSERT OR IGNORE INTO precursor_homeworld_reward
                           (precursor_key, resource, multiplier, min_amount, max_amount, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, res, mult, lo, hi, hw_order),
                )
                if verbose:
                    amt = f"x{mult:g}" if mult is not None else "flat"
                    print(f"    homeworld reward | {res} {amt} ({lo:g}~{hi:g})")

        # --- homeworld building (0..1): granted on the precursor's home planet ---
        if p['building']:
            building_key, scope = p['building']
            _write_precursor_building(w, key, building_key, scope, 'homeworld', None,
                                      buildings, scripted_vars, inline_scripts_dir, loc, verbose)

        # --- "Secrets of …" technology + the empire-wide building it unlocks ---
        if p.get('technology'):
            tech_key, tb_key, tb_scope = p['technology']
            tdef = technologies.get(tech_key)
            if isinstance(tdef, list):
                tdef = next((b for b in reversed(tdef) if isinstance(b, dict)), None)
            tdef = tdef if isinstance(tdef, dict) else {}
            cat = tdef.get('category')
            cat = cat[0] if isinstance(cat, list) else cat
            cost = _resolve_var(tdef.get('cost'), scripted_vars)
            tier = _resolve_var(tdef.get('tier'), scripted_vars)
            w.execute(
                """INSERT OR REPLACE INTO precursor_technology
                       (precursor_key, tech_key, name, cost, tier, area, category,
                        unlocks_building_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, tech_key, loc.get(tech_key),
                 int(cost) if cost is not None else None,
                 int(tier) if tier is not None else None,
                 tdef.get('area'), cat, tb_key),
            )
            if verbose:
                print(f"    technology {tech_key} | {loc.get(tech_key)} → {tb_key}")
            _write_precursor_building(w, key, tb_key, tb_scope, 'technology', tech_key,
                                      buildings, scripted_vars, inline_scripts_dir, loc, verbose)

    w.commit()


def _write_precursor_building(w: Writer, precursor_key: str, building_key: str, scope: str,
                              source: str, tech_key: str | None, buildings: dict,
                              scripted_vars: dict[str, float], inline_scripts_dir,
                              loc: dict[str, str], verbose: bool):
    """Write a precursor building (+ its resources/modifiers/jobs). `source` is
    'homeworld' (home-planet unique) or 'technology' (unlocked by `tech_key`)."""
    bdef = buildings.get(building_key)
    if isinstance(bdef, list):
        bdef = next((b for b in reversed(bdef) if isinstance(b, dict)), None)
    bdef = bdef if isinstance(bdef, dict) else {}

    can_dem = bdef.get('can_demolish')
    can_dem = (1 if str(can_dem).strip() == 'yes' else 0) if can_dem is not None else None
    build_time = _resolve_var(bdef.get('base_buildtime'), scripted_vars)
    # A per-empire cap is `base_cap_amount = N` or `empire_limit = { base = N … }`;
    # a per-planet cap is `planet_limit = N`. `_building_cap` handles both forms.
    empire_limit = _building_cap(bdef.get('base_cap_amount'), scripted_vars)
    if empire_limit is None:
        empire_limit = _building_cap(bdef.get('empire_limit'), scripted_vars)
    planet_limit = _building_cap(bdef.get('planet_limit'), scripted_vars)
    # The building name often loc-references its tech (e.g. "$tech_secrets_baol$").
    # Orbital starbase buildings carry their name under the `sm_` (starbase module)
    # loc key rather than the bare building key.
    raw_name = loc.get(building_key) or loc.get('sm_' + building_key)
    name = _clean_loc(raw_name, loc) if raw_name else None
    w.execute(
        """INSERT OR REPLACE INTO precursor_building
               (precursor_key, building_key, name, scope, source, tech_key, category,
                build_time_days, empire_limit, planet_limit, can_demolish)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (precursor_key, building_key, name, scope, source, tech_key, bdef.get('category'),
         int(build_time) if build_time is not None else None,
         int(empire_limit) if empire_limit is not None else None,
         int(planet_limit) if planet_limit is not None else None,
         can_dem),
    )
    if verbose:
        print(f"    building {building_key} ({scope}, {source}) | {name}")

    for r_order, (rkind, rres, ramt) in enumerate(_building_resources(bdef, scripted_vars)):
        w.execute(
            """INSERT OR IGNORE INTO precursor_building_resource
                   (building_key, kind, resource, amount, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (building_key, rkind, rres, ramt, r_order),
        )
        if verbose:
            print(f"      {rkind} | {ramt:g} {rres}")

    building_mods = (
        _building_modifiers(bdef, scripted_vars)
        + [('job', jk, jv, None, None)
           for jk, jv in _building_inline_jobs(bdef, scripted_vars, inline_scripts_dir)]
    )
    for m_order, (mscope, mkey, mval, mae, mcond) in enumerate(building_mods):
        w.execute(
            """INSERT OR IGNORE INTO precursor_building_modifier
                   (building_key, scope, modifier_key, modifier_value, ae_value,
                    condition, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (building_key, mscope, mkey, mval, mae, mcond, m_order),
        )
        _write_modifier_label(w, loc, mkey)
        if verbose:
            extra = (f" → {mae:g} w/AE" if mae is not None else "") + \
                    (f" [{mcond}]" if mcond else "")
            print(f"      {mscope} modifier | {mkey} = {mval:g}{extra}")


# ---------------------------------------------------------------------------
# Extraction: deposits (dictionary for home-system planet deposits)
# ---------------------------------------------------------------------------

def extract_deposits(data: dict, source_file: str, loc: dict[str, str],
                     w: Writer, verbose: bool):
    for key, d in data.items():
        if key.startswith('@') or not isinstance(d, dict) or not key.startswith('d_'):
            continue

        resources = d.get('resources')
        if not isinstance(resources, dict):
            resources = {}
        # Category can be top-level (blockers) or inside the resources block.
        category = d.get('category') or resources.get('category')
        is_blocker = 1 if (isinstance(category, str) and 'blocker' in category) else 0
        clear_time = None
        try:
            clear_time = int(d.get('time')) if d.get('time') is not None else None
        except (ValueError, TypeError):
            pass

        w.execute(
            """INSERT OR REPLACE INTO deposit (key, name, category, is_blocker, clear_time, source_file)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key, _clean_loc(loc[key], loc) if key in loc else None,
             category, is_blocker, clear_time, source_file),
        )

        for kind in ('produces', 'cost', 'upkeep'):
            block = resources.get(kind)
            if not isinstance(block, dict):
                continue
            for res, amt in block.items():
                value = _resolve_modifier_float(amt)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO deposit_resource (deposit_key, kind, resource, amount)
                       VALUES (?, ?, ?, ?)""",
                    (key, kind, res, value),
                )

        # Blocker/feature planet modifiers.
        for mod_block_key in ('planet_modifier', 'modifier'):
            pm = d.get(mod_block_key)
            if not isinstance(pm, dict):
                continue
            for mod_key, mod_val in pm.items():
                value = _resolve_modifier_float(mod_val)
                if value is None:
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO deposit_modifier (deposit_key, modifier_key, modifier_value)
                       VALUES (?, ?, ?)""",
                    (key, mod_key, value),
                )
                _write_modifier_label(w, loc, mod_key)

        if verbose:
            print(f"  Deposit: {key} | blocker: {is_blocker} | {category}")

    w.commit()


# ---------------------------------------------------------------------------
# Extraction: precursor home systems
# ---------------------------------------------------------------------------

# precursor key -> home system initializer(s). Some precursors span several
# systems (Zroni's three legacy worlds). Verified against the initializer files
# and the spawn_system calls in the precursor events.
# NOTE: the Cybrex home is `cybrex_system` (Cybrex Alpha) — a ruined debris field.
# The ruined ringworld `cybrex_beta` is NOT a precursor reward: it spawns during
# the Contingency end-game crisis (events/crisis_events_3.txt), so it's excluded.
PRECURSOR_SYSTEMS: dict[str, list[str]] = {
    'precursor_1':       ['vultaumar_system'],
    'precursor_2':       ['yuhtaan_system'],
    'precursor_3':       ['fen_habbanis_system'],
    'precursor_4':       ['irass_system'],
    'precursor_5':       ['cybrex_system'],
    'precursor_baol_1':  ['last_baol_system'],
    'precursor_zroni_1': ['zroni_legacy_initializer_01', 'zroni_legacy_initializer_02',
                          'zroni_legacy_initializer_03'],
    'precursor_inetian': ['weathermanipulators_home_system'],
    'precursor_adakkaria': ['adSalivul_system', 'adSul_system'],
}


def _as_blocks(v) -> list[dict]:
    """Normalise a sub-block value to a list of dicts.

    Unlike _to_list (which returns a dict's *keys*), this wraps a single dict
    as [dict] — correct for blocks like init_effect / add_modifier that are
    promoted to a list only when they appear more than once."""
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []


def _loc_name(raw, loc: dict[str, str]) -> str | None:
    """Resolve a NAME_ loc key, falling back to the literal string."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip('"')
    return _clean_loc(loc.get(raw, raw), loc) or None


def _collect_init_effects(planet: dict) -> tuple[list[str], list[str], list[str]]:
    """Scan a planet's init_effect block(s) and the top-level `deposit` key.

    Returns (deposit_keys, modifier_static_keys, megastructure_types)."""
    deposits: list[str] = []
    modifiers: list[str] = []
    megastructures: list[str] = []

    for dep in _to_list(planet.get('deposit')):
        if isinstance(dep, str):
            deposits.append(dep)

    for init in _as_blocks(planet.get('init_effect')):
        for dk in ('add_deposit', 'set_deposit'):
            for dep in _to_list(init.get(dk)):
                if isinstance(dep, str):
                    deposits.append(dep)
        for m in _as_blocks(init.get('add_modifier')):
            if isinstance(m.get('modifier'), str):
                modifiers.append(m['modifier'])
        for ms in _as_blocks(init.get('spawn_megastructure')):
            if isinstance(ms.get('type'), str):
                megastructures.append(ms['type'].strip('"'))

    return deposits, modifiers, megastructures


def _planet_flags(planet: dict) -> list[str]:
    flags = planet.get('flags')
    if isinstance(flags, dict):
        return list(flags.keys())
    return _to_list(flags)


def extract_precursor_systems(initializers: dict, static_mods: dict,
                              loc: dict[str, str], w: Writer, verbose: bool):
    for p in PRECURSORS:
        pkey = p['key']
        for sys_order, init_key in enumerate(PRECURSOR_SYSTEMS.get(pkey, [])):
            sys_block = initializers.get(init_key)
            if isinstance(sys_block, list):
                sys_block = next((b for b in reversed(sys_block) if isinstance(b, dict)), None)
            if not isinstance(sys_block, dict):
                print(f"  WARNING: system {init_key} not found for {pkey}", file=sys.stderr)
                continue

            w.execute(
                """INSERT OR REPLACE INTO precursor_system
                       (precursor_key, initializer, name, star_class, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (pkey, init_key, _loc_name(sys_block.get('name'), loc),
                 sys_block.get('class'), sys_order),
            )
            if verbose:
                print(f"  System: {pkey} | {init_key} | {_loc_name(sys_block.get('name'), loc)}")

            # Megastructures for this system, counted by type. Most sit in the
            # system-level init_effect (the Yuht ruined hyper relay; the Cybrex
            # ruined ring world's four sections), occasionally on a planet.
            mega_counts: dict[str, int] = {}
            for init in _as_blocks(sys_block.get('init_effect')):
                for ms in _as_blocks(init.get('spawn_megastructure')):
                    ms_type = ms.get('type')
                    if isinstance(ms_type, str):
                        t = ms_type.strip('"')
                        mega_counts[t] = mega_counts.get(t, 0) + 1

            # Flatten planets and their moons into one ordered list.
            entries: list[tuple[dict, bool]] = []
            for planet in _as_blocks(sys_block.get('planet')):
                entries.append((planet, False))
                for moon in _as_blocks(planet.get('moon')):
                    entries.append((moon, True))

            for idx, (planet, is_moon) in enumerate(entries):
                planet_id = f"{init_key}#{idx}"
                pclass = planet.get('class')
                pclass = pclass.strip('"') if isinstance(pclass, str) else None
                size = None
                try:
                    size = int(planet.get('size')) if planet.get('size') is not None else None
                except (ValueError, TypeError):
                    pass
                flags = _planet_flags(planet)
                is_homeworld = 1 if any('homeworld' in f for f in flags) else 0

                w.execute(
                    """INSERT OR REPLACE INTO precursor_planet
                           (id, precursor_key, initializer, name, planet_class,
                            planet_class_label, size, is_moon, is_homeworld, flags, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (planet_id, pkey, init_key, _loc_name(planet.get('name'), loc), pclass,
                     _clean_loc(loc[pclass], loc) if (pclass and pclass in loc) else None,
                     size, 1 if is_moon else 0, is_homeworld,
                     ' '.join(flags) if flags else None, idx),
                )

                deposits, modifiers, megastructures = _collect_init_effects(planet)
                for d_order, dep in enumerate(deposits):
                    w.execute(
                        """INSERT OR IGNORE INTO precursor_planet_deposit
                               (planet_id, deposit_key, sort_order) VALUES (?, ?, ?)""",
                        (planet_id, dep, d_order),
                    )
                for static_key in modifiers:
                    mod_block = static_mods.get(static_key)
                    if isinstance(mod_block, list):
                        merged: dict = {}
                        for b in mod_block:
                            if isinstance(b, dict):
                                merged.update(b)
                        mod_block = merged
                    if not isinstance(mod_block, dict):
                        continue
                    for mod_key, mod_val in mod_block.items():
                        if mod_key in _NON_MODIFIER_KEYS:
                            continue
                        value = _resolve_modifier_float(mod_val)
                        if value is None:
                            continue
                        w.execute(
                            """INSERT OR REPLACE INTO precursor_planet_modifier
                                   (planet_id, static_modifier, modifier_key, modifier_value)
                               VALUES (?, ?, ?, ?)""",
                            (planet_id, static_key, mod_key, value),
                        )
                        _write_modifier_label(w, loc, mod_key)

                # Planet-level megastructures (rare) — counted at system level.
                for ms_type in megastructures:
                    mega_counts[ms_type] = mega_counts.get(ms_type, 0) + 1

            # Write the system's megastructures, one row per type with its count.
            for ms_type, ms_count in mega_counts.items():
                w.execute(
                    """INSERT OR REPLACE INTO precursor_megastructure
                           (precursor_key, initializer, type, type_label, count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pkey, init_key, ms_type,
                     _clean_loc(loc[ms_type], loc) if ms_type in loc else None, ms_count),
                )
                if verbose:
                    print(f"    megastructure: {ms_type} x{ms_count} ({loc.get(ms_type)})")

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
    deposits_dir   = install_dir / 'common' / 'deposits'
    sysinit_dir    = install_dir / 'common' / 'solar_system_initializers'
    buildings_dir  = install_dir / 'common' / 'buildings'
    starbase_buildings_dir = install_dir / 'common' / 'starbase_buildings'
    technology_dir = install_dir / 'common' / 'technology'
    inline_scripts_dir = install_dir / 'common' / 'inline_scripts'
    civics_dir     = install_dir / 'common' / 'governments' / 'civics'
    planet_class_dir = install_dir / 'common' / 'planet_classes'
    prescripted_dir = install_dir / 'prescripted_countries'
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

    # --- game version (from launcher-settings.json) ---
    launcher = install_dir / 'launcher-settings.json'
    if launcher.exists():
        try:
            ls = json.loads(launcher.read_text(encoding='utf-8-sig', errors='replace'))
            for mkey, jkey in (('game_version', 'version'), ('game_version_raw', 'rawVersion')):
                val = ls.get(jkey)
                if val:
                    w.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (mkey, str(val)))
            w.commit()
            print(f"  Game version: {ls.get('version')}", file=progress)
        except Exception as e:
            print(f"  WARNING: failed to read launcher-settings.json: {e}", file=sys.stderr)

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
    # Building definitions — planet (common/buildings) and orbital starbase
    # (common/starbase_buildings) — precursor buildings' effects/restrictions.
    buildings: dict = {}
    for bdir in (buildings_dir, starbase_buildings_dir):
        if bdir.exists():
            for f in sorted(bdir.glob('*.txt')):
                try:
                    buildings.update(parse_file(f))
                except Exception as e:
                    print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
        else:
            print(f"  WARNING: {bdir} not found", file=progress)
    # Technology definitions (the precursor "Secrets of …" techs live here).
    technologies: dict = {}
    if technology_dir.exists():
        for f in sorted(technology_dir.glob('*.txt')):
            try:
                technologies.update(parse_file(f))
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {technology_dir} not found", file=progress)
    # Global scripted variables (e.g. @activation_cost, @triumph_duration) used to
    # resolve relic triumph costs/cooldowns.
    scripted_vars: dict[str, float] = {}
    scripted_var_dir = install_dir / 'common' / 'scripted_variables'
    if scripted_var_dir.exists():
        for f in sorted(scripted_var_dir.glob('*.txt')):
            try:
                for vk, vv in parse_file(f).items():
                    if vk.startswith('@'):
                        fv = _resolve_modifier_float(vv)
                        if fv is not None:
                            scripted_vars[vk] = fv
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    # Event index (id → event) for following relic actives that fire a country_event
    # (e.g. the Reality Perforator → precursor.105 → its random combat buffs).
    events_index: dict = {}
    events_dir = install_dir / 'events'
    relic_event_files = ('precursor_events.txt', 'precursor_events_cosmic_storms.txt',
                         'ancient_relics_arcsite_events_1.txt', 'ancient_relics_arcsite_events_2.txt')
    for fn in relic_event_files:
        f = events_dir / fn
        if not f.exists():
            continue
        try:
            data = parse_file(f)
            # Index every event type: relic actives fire country_events, but the
            # home-world discovery rewards live in ship_event/planet_event blocks.
            for ev_type in ('country_event', 'ship_event', 'planet_event', 'fleet_event'):
                for ev in _as_blocks(data.get(ev_type)):
                    if isinstance(ev.get('id'), str):
                        events_index.setdefault(ev['id'], ev)
        except Exception as e:
            print(f"  WARNING: failed to parse {fn}: {e}", file=sys.stderr)
    print(f"  {len(precursor_civ)} civ entries, {len(relics)} relics, "
          f"{len(static_mods)} static modifiers, {len(scripted_vars)} scripted vars, "
          f"{len(events_index)} events loaded", file=progress)
    extract_precursors(precursor_civ, relics, static_mods, events_index, buildings,
                       technologies, inline_scripts_dir, scripted_vars, loc, w, args.verbose)

    # --- deposits (dictionary for home-system planet deposits) ---
    dep_files = sorted(deposits_dir.glob('*.txt')) if deposits_dir.exists() else []
    print(f"\n[9/10] deposits: {len(dep_files)} file(s)", file=progress)
    for f in dep_files:
        try:
            extract_deposits(parse_file(f), f.name, loc, w, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)

    # --- precursor home systems (planets, deposits, megastructures) ---
    print(f"\n[10/10] precursor home systems", file=progress)
    initializers: dict = {}
    if sysinit_dir.exists():
        for f in sorted(sysinit_dir.glob('*.txt')):
            try:
                initializers.update(parse_file(f))
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {sysinit_dir} not found", file=progress)
    print(f"  {len(initializers)} system initializers loaded", file=progress)
    extract_precursor_systems(initializers, static_mods, loc, w, args.verbose)

    # --- civics (after scripted_vars is loaded, for @var resolution in modifiers) ---
    civic_files = (('00_civics.txt', 'normal'), ('01_special_civics.txt', 'special'),
                   ('02_gestalt_civics.txt', 'gestalt'), ('03_corporate_civics.txt', 'corporate'))
    print(f"\n[civics] {civics_dir}", file=progress)
    civic_order = 0
    for fn, ctype in civic_files:
        f = civics_dir / fn
        if not f.exists():
            continue
        try:
            civic_order = extract_civics(parse_file(f), ctype, loc, scripted_vars, w,
                                         civic_order, args.verbose)
        except Exception as e:
            print(f"  WARNING: failed to parse {fn}: {e}", file=sys.stderr)

    # --- authorities (government types) ---
    authorities_dir = install_dir / 'common' / 'governments' / 'authorities'
    print(f"\n[authorities] {authorities_dir}", file=progress)
    if authorities_dir.exists():
        for f in sorted(authorities_dir.glob('*.txt')):
            try:
                extract_authorities(parse_file(f), loc, scripted_vars, w, args.verbose)
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {authorities_dir} not found", file=progress)

    # --- origins (same dir; 00 = playable, 01 = non-playable) ---
    origin_files = (('00_origins.txt', False), ('01_origins_non_playable.txt', True))
    print(f"\n[origins] {civics_dir}", file=progress)
    ascension = compute_ascension_restrictions(install_dir)
    init_custom = _custom_empire_initializers(install_dir)
    origin_order = 0
    for fn, nonsel in origin_files:
        f = civics_dir / fn
        if not f.exists():
            continue
        try:
            origin_order = extract_origins(parse_file(f), nonsel, loc, scripted_vars, w,
                                           origin_order, args.verbose, ascension, init_custom)
        except Exception as e:
            print(f"  WARNING: failed to parse {fn}: {e}", file=sys.stderr)

    # --- planet classes (merge all files, then extract) ---
    print(f"\n[planet classes] {planet_class_dir}", file=progress)
    planet_classes: dict = {}
    if planet_class_dir.exists():
        for f in sorted(planet_class_dir.glob('*.txt')):
            try:
                planet_classes.update(parse_file(f))
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
        extract_planet_classes(planet_classes, loc, w, args.verbose)
    else:
        print(f"  WARNING: {planet_class_dir} not found", file=progress)

    # --- prescripted (predefined) empires ---
    print(f"\n[empires] {prescripted_dir}", file=progress)
    if prescripted_dir.exists():
        empire_order = 0
        for f in sorted(prescripted_dir.glob('*.txt')):
            try:
                empire_order = extract_prescripted_empires(parse_file(f), loc, w,
                                                           empire_order, args.verbose)
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    else:
        print(f"  WARNING: {prescripted_dir} not found", file=progress)

    # --- home-system initializers (custom_empire) + flag assets ---
    init_dir = install_dir / 'common' / 'solar_system_initializers'
    print(f"\n[initializers] {init_dir}", file=progress)
    if init_dir.exists():
        init_order = 0
        for f in sorted(init_dir.glob('*.txt')):
            try:
                init_order = extract_initializers(parse_file(f), loc, w, init_order)
            except Exception as e:
                print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
    print(f"\n[flags] {install_dir / 'flags'}", file=progress)
    extract_flag_assets(install_dir, w, args.verbose)
    print(f"\n[rooms] {prescripted_dir}", file=progress)
    extract_rooms(install_dir, w, args.verbose)

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
        n_prec_relic_c  = tmp.execute("SELECT COUNT(*) FROM precursor_relic_resource").fetchone()[0]
        n_prec_relic_a  = tmp.execute("SELECT COUNT(*) FROM precursor_relic_active_modifier").fetchone()[0]
        n_prec_reward_m = tmp.execute("SELECT COUNT(*) FROM precursor_reward_modifier").fetchone()[0]
        n_prec_build    = tmp.execute("SELECT COUNT(*) FROM precursor_building").fetchone()[0]
        n_deposits      = tmp.execute("SELECT COUNT(*) FROM deposit").fetchone()[0]
        n_prec_sys      = tmp.execute("SELECT COUNT(*) FROM precursor_system").fetchone()[0]
        n_prec_planet   = tmp.execute("SELECT COUNT(*) FROM precursor_planet").fetchone()[0]
        n_prec_pdep     = tmp.execute("SELECT COUNT(*) FROM precursor_planet_deposit").fetchone()[0]
        n_prec_mega     = tmp.execute("SELECT COUNT(*) FROM precursor_megastructure").fetchone()[0]
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
|  Precursor relic res : {n_prec_relic_c:<19}|
|  Precursor relic active: {n_prec_relic_a:<17}|
|  Precursor reward mods: {n_prec_reward_m:<18}|
|  Precursor buildings: {n_prec_build:<20}|
|  Deposits (dict)    : {n_deposits:<20}|
|  Precursor systems  : {n_prec_sys:<20}|
|  Precursor planets  : {n_prec_planet:<20}|
|  Precursor planet dep: {n_prec_pdep:<19}|
|  Precursor megastruct: {n_prec_mega:<19}|
+------------------------------------------+""", file=progress)
    elif sql_path:
        sql_size = Path(sql_path).stat().st_size
        print(f"\nDone. SQL file: {sql_path} ({sql_size:,} bytes)", file=progress)
    else:
        print("\nDone.", file=progress)


if __name__ == '__main__':
    main()
