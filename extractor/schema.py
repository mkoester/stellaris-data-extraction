"""The SQLite schema (single executescript string)."""


# ---------------------------------------------------------------------------
# Schema & Writer
# ---------------------------------------------------------------------------

SCHEMA = """\
-- Every table is dropped before it is (re)created, so a regen always rebuilds a clean
-- schema in-place — no stale rows, and column changes take effect without deleting the
-- .sqlite file first. DROPs list children before parents.
DROP TABLE IF EXISTS meta;
DROP TABLE IF EXISTS dlc;
DROP TABLE IF EXISTS species_class_attribute;
DROP TABLE IF EXISTS portrait_set;
DROP TABLE IF EXISTS species_class;

-- Key/value metadata about the extracted game (e.g. game_version, game_version_raw).
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE dlc (
    name             TEXT PRIMARY KEY,   -- matches host_has_dlc values, e.g. "Toxoids Species Pack"
    steam_id         TEXT,
    affects_gameplay INTEGER DEFAULT 0   -- 0/1
);

CREATE TABLE species_class (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    key                  TEXT UNIQUE NOT NULL,   -- e.g. "MAM"
    label                TEXT,                   -- display name from localisation, e.g. "Mammalian"
    archetype            TEXT,                   -- e.g. "BIOLOGICAL"
    base_trait           TEXT,                   -- the class's auto-included base trait, e.g. trait_organic / trait_infernal (Thermophile / INF)
    playable             INTEGER DEFAULT 1,      -- 0 when `playable = { always = no }` (NPC-only: Swarm, AI, Paragon, all PRE_*)
    graphical_culture    TEXT,                   -- e.g. "mammalian_01"
    move_pop_sound_effect TEXT,
    portrait_modding      INTEGER DEFAULT 1,     -- boolean (0/1), default yes per game docs
    possible_block       TEXT,                   -- raw text of the possible condition
    source_file          TEXT
);

CREATE TABLE portrait_set (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    set_key           TEXT NOT NULL,             -- e.g. "mammalians"
    species_class_key TEXT,                      -- e.g. "MAM" (from species_class = MAM)
    portrait_id       TEXT NOT NULL,             -- e.g. "mam1", "rep3"
    required_dlc      TEXT,                      -- NULL = base game; FK to dlc.name
    selectable        INTEGER NOT NULL DEFAULT 1,-- 0 when the block's `playable = { always = no }` (randomizable/NPC-only)
    source_file       TEXT,
    UNIQUE(set_key, portrait_id),
    FOREIGN KEY(species_class_key) REFERENCES species_class(key),
    FOREIGN KEY(required_dlc)      REFERENCES dlc(name)
);

CREATE TABLE species_class_attribute (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    species_class_key TEXT NOT NULL,
    attribute_key     TEXT NOT NULL,
    attribute_value   TEXT NOT NULL,
    FOREIGN KEY(species_class_key) REFERENCES species_class(key)
);

-- Drop the trait family up-front (children before parent): these older tables use
-- CREATE IF NOT EXISTS, so without an explicit DROP they accumulate stale rows across
-- runs (renamed/removed modifier keys would otherwise linger).
DROP TABLE IF EXISTS trait_requirement;
DROP TABLE IF EXISTS trait_scaling_modifier;
DROP TABLE IF EXISTS trait_portrait_override;
DROP TABLE IF EXISTS trait_modifier;
DROP TABLE IF EXISTS trait_tag;
DROP TABLE IF EXISTS trait_opposite;
DROP TABLE IF EXISTS trait_species_class;
DROP TABLE IF EXISTS trait_archetype;
DROP TABLE IF EXISTS trait;
CREATE TABLE trait (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT UNIQUE NOT NULL,
    cost         INTEGER,               -- base point cost; NULL if not specified
    category     TEXT,                  -- normal, cyborg, robotic, psionic, advanced_genetic, …
    initial      INTEGER DEFAULT 1,     -- 0=no, 1=yes; whether selectable at species creation
    randomized   INTEGER DEFAULT 1,     -- 0=no, 1=yes
    hidden       INTEGER DEFAULT 0,     -- 0=no, 1=yes
    required_dlc TEXT,                  -- NULL = base game; FK to dlc.name
    description  TEXT,                  -- cleaned flavour text from trait_<key>_desc loc
    effect       TEXT,                  -- non-numeric behavioural lines from custom_tooltip (e.g. Rooted's "Pops will not Automatically Resettle"); numeric lines dropped (those are modifier chips)
    source_file  TEXT,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

CREATE TABLE trait_archetype (
    trait_key TEXT NOT NULL,
    archetype TEXT NOT NULL,          -- BIOLOGICAL, LITHOID, ROBOT, MACHINE
    PRIMARY KEY (trait_key, archetype),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE trait_species_class (
    trait_key         TEXT NOT NULL,
    species_class_key TEXT NOT NULL,  -- e.g. LITHOID, PSIONIC, PLANT
    PRIMARY KEY (trait_key, species_class_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE trait_opposite (
    trait_key    TEXT NOT NULL,
    opposite_key TEXT NOT NULL,       -- mutually exclusive trait
    PRIMARY KEY (trait_key, opposite_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

-- Specific portraits that may pick this trait even though their species class normally
-- wouldn't grant it (the trait's `portrait_override` list). This is how individual portraits
-- get cross-phenotype "bonus" traits — e.g. plantoid pla17 / lithoid lith_human get the
-- Humanoid traits, aquatic aqu7 gets Budding, humanoid_hp_16 gets the psionic traits.
CREATE TABLE trait_portrait_override (
    trait_key   TEXT NOT NULL,
    portrait_id TEXT NOT NULL,
    PRIMARY KEY (trait_key, portrait_id),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

-- Empire-creation gates on a species trait: its allowed_/forbidden_ origins, civics and
-- ethics plus allowed_planet_classes (homeworld). Same shape as civic_requirement:
-- allowed_* → 'require_any' (group 1), forbidden_* → 'forbid'. E.g. Sedentary is
-- forbidden for the nomad origins; Aquatic needs an Ocean homeworld.
CREATE TABLE trait_requirement (
    trait_key TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- 'origin' | 'civic' | 'ethic' | 'planet_class'
    operator  TEXT NOT NULL,          -- 'require_any' | 'forbid'
    value     TEXT NOT NULL,
    group_id  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trait_key, kind, operator, value),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE trait_tag (
    trait_key TEXT NOT NULL,
    tag       TEXT NOT NULL,          -- e.g. organic, positive, pop_output, research
    PRIMARY KEY (trait_key, tag),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

CREATE TABLE trait_modifier (
    trait_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trait_key, modifier_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

-- Scaling effects (Familial, Noxious, Genetic Memory, Invasive Species): a modifier scaled
-- by a script `value:` — per 100 pops of a kind, per year of colony age, or per negative
-- trait. e.g. "+0.1% Happiness per 100 Familial pops (max +20%)", "+5% Pop Growth Speed per
-- negative trait" (cap NULL = uncapped).
CREATE TABLE trait_scaling_modifier (
    trait_key    TEXT NOT NULL,
    modifier_key TEXT NOT NULL,        -- e.g. "pop_happiness"
    rate         REAL NOT NULL,        -- value per one display unit (per 100 pops / per year / per trait)
    cap          REAL,                 -- value at the scaling value's max; NULL when uncapped
    unit         TEXT NOT NULL,        -- the "per X" unit, e.g. "100 Familial pops", "negative trait"
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trait_key, modifier_key),
    FOREIGN KEY (trait_key) REFERENCES trait(key)
);

DROP TABLE IF EXISTS modifier_label;
CREATE TABLE modifier_label (
    modifier_key     TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    positive_is_good INTEGER NOT NULL DEFAULT 1  -- 0 when a higher value is worse for the empire
);

DROP TABLE IF EXISTS species_archetype;
CREATE TABLE species_archetype (
    key                     TEXT PRIMARY KEY,
    trait_points            INTEGER,   -- base point budget for species creation
    max_traits              INTEGER,   -- pick limit (cost=0 traits don't count)
    robotic                 INTEGER DEFAULT 0,
    inherit_trait_points_from TEXT,    -- resolved before insert; kept for reference
    base_trait              TEXT,      -- canonical always-assigned trait (cost 0, free pick slot)
    source_file             TEXT,
    FOREIGN KEY (inherit_trait_points_from) REFERENCES species_archetype(key)
);

DROP TABLE IF EXISTS trait_category;
DROP TABLE IF EXISTS named_color;
CREATE TABLE named_color (
    key         TEXT PRIMARY KEY,  -- e.g. "trait_bg_psionic"
    r           INTEGER NOT NULL,
    g           INTEGER NOT NULL,
    b           INTEGER NOT NULL,
    a           INTEGER NOT NULL DEFAULT 255,
    source_file TEXT
);

CREATE TABLE trait_category (
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
DROP TABLE IF EXISTS granted_trait;
DROP TABLE IF EXISTS civic_requirement;
DROP TABLE IF EXISTS civic_modifier;
DROP TABLE IF EXISTS council_position_modifier;
DROP TABLE IF EXISTS council_position;
DROP TABLE IF EXISTS civic;
CREATE TABLE civic (
    key          TEXT PRIMARY KEY,    -- e.g. "civic_technocracy"
    name         TEXT,                -- localised name
    description  TEXT,                -- cleaned effects tooltip
    civic_type   TEXT NOT NULL,       -- 'normal' | 'gestalt' | 'corporate' | 'special'
    required_dlc TEXT,                -- from playable host_has_dlc; NULL = base game
    selectable   INTEGER NOT NULL DEFAULT 1,  -- 0 = not player-pickable (fallen empire, …)
    locked       INTEGER NOT NULL DEFAULT 0,  -- 1 = `modification = no`: can't be added/removed after game start
    ascension_lock   TEXT,            -- locked to this single ascension path (blocks all others); NULL = unrestricted
    ascension_blocks TEXT,            -- comma-separated ascension paths blocked; all four = no ascension path at all (Natural Design); NULL = none
    icon         TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);

-- The extra council seat a civic opens (common/governments/councilors, `civic = …`).
-- Its modifiers scale with the assigned leader's level (value = per level).
CREATE TABLE council_position (
    key            TEXT PRIMARY KEY,  -- e.g. "councilor_agrarian_idyll"
    civic_key      TEXT NOT NULL,
    name           TEXT,              -- e.g. "Elder Farmer"
    description    TEXT,
    leader_classes TEXT,              -- comma list: commander,official,scientist
    effect         TEXT,              -- custom tooltip (shown instead of / beside the modifiers)
    sort_order     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (civic_key) REFERENCES civic(key)
);
CREATE TABLE council_position_modifier (
    position_key   TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,     -- per leader level
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (position_key, modifier_key),
    FOREIGN KEY (position_key) REFERENCES council_position(key)
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
    kind       TEXT NOT NULL,        -- 'authority' | 'ethic' | 'civic' | 'origin' | 'ship_type' | 'gameplay'
                                     -- | 'species_class' | 'archetype' | 'planet_class' (homeworld) | 'trait'
    operator   TEXT NOT NULL,        -- 'require' | 'forbid' | 'require_any'
    value      TEXT NOT NULL,        -- e.g. 'auth_corporate', 'ethic_materialist'
    group_id   INTEGER NOT NULL DEFAULT 0,  -- ties 'require_any' values from one OR set together
    PRIMARY KEY (civic_key, kind, operator, value),
    FOREIGN KEY (civic_key) REFERENCES civic(key)
);

-- Species traits a civic or origin gives the founder species (its top-level
-- `traits = { trait = … }`), e.g. Tankbound → trait_tankbound, Necrophage →
-- trait_necrophage. Keyed by civic OR origin key, hence no foreign key. The planner
-- applies the granted trait's opposites (Tankbound rules out Weak / Hollow Bones /
-- Photoadaptive).
CREATE TABLE granted_trait (
    owner_key TEXT NOT NULL,          -- civic or origin key
    trait_key TEXT NOT NULL,
    PRIMARY KEY (owner_key, trait_key)
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
-- civic_requirement): kind authority/ethic/civic/origin/archetype/… × operator
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

-- Pop jobs (reference / wiki): every job, its category (worker/specialist/ruler/drone
-- …), flavour description, and its base produced/upkeep resources in job_resource.
DROP TABLE IF EXISTS job_modifier;
DROP TABLE IF EXISTS job_resource;
DROP TABLE IF EXISTS job;
CREATE TABLE job (
    key         TEXT PRIMARY KEY,   -- e.g. "miner", "metallurgist"
    name        TEXT,               -- localised, e.g. "Miner"
    category    TEXT,               -- worker | specialist | ruler | complex_drone | …
    description TEXT,               -- cleaned flavour tooltip
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE job_resource (
    job_key  TEXT NOT NULL,
    kind     TEXT NOT NULL,         -- 'produces' | 'upkeep'
    resource TEXT NOT NULL,         -- e.g. "minerals", "alloys"
    amount   REAL NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_key, kind, resource),
    FOREIGN KEY (job_key) REFERENCES job(key)
);
-- A job's non-resource effects (planet/country modifiers): Amenities, Crime, Stability,
-- Planetary defense armies, Ethics attraction, Naval cap, … from planet_modifier /
-- country_modifier / triggered_planet_modifier blocks. Label + good/bad via modifier_label.
CREATE TABLE job_modifier (
    job_key    TEXT NOT NULL,
    modifier   TEXT NOT NULL,        -- modifier key, e.g. "planet_crime_add"
    value      REAL NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_key, modifier),
    FOREIGN KEY (job_key) REFERENCES job(key)
);

-- Buildings (reference / wiki): every building, its category, description, build/upkeep
-- resources (building_resource), the jobs it provides (building_job), its other
-- modifiers (building_modifier) and the civics/origins it needs (building_unlock).
DROP TABLE IF EXISTS building_unlock;
DROP TABLE IF EXISTS building_modifier;
DROP TABLE IF EXISTS building_job;
DROP TABLE IF EXISTS building_resource;
DROP TABLE IF EXISTS building;
CREATE TABLE building (
    key         TEXT PRIMARY KEY,   -- e.g. "building_foundry_1"
    name        TEXT,               -- localised, e.g. "Alloy Foundries"
    category    TEXT,               -- manufacturing | research | resource | amenity | …
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE building_resource (
    building_key TEXT NOT NULL,
    kind         TEXT NOT NULL,     -- 'cost' | 'upkeep' | 'produces'
    resource     TEXT NOT NULL,
    amount       REAL NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (building_key, kind, resource),
    FOREIGN KEY (building_key) REFERENCES building(key)
);
CREATE TABLE building_job (
    building_key TEXT NOT NULL,
    job          TEXT NOT NULL,     -- display name from job_<x>_add (e.g. "metallurgist")
    amount       REAL NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (building_key, job),
    FOREIGN KEY (building_key) REFERENCES building(key)
);
CREATE TABLE building_modifier (
    building_key TEXT NOT NULL,
    scope        TEXT NOT NULL,     -- 'planet' | 'country' | 'system' | 'station'
    modifier_key TEXT NOT NULL,
    value        REAL NOT NULL,     -- without ascension perks
    condition    TEXT,              -- an ascension perk the row needs; NULL = always
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (building_key, sort_order),
    FOREIGN KEY (building_key) REFERENCES building(key)
);
-- A civic/origin a building hard-requires (potential/allow/can_build, scripted triggers
-- expanded; an optional path inside an OR with other conditions doesn't count).
CREATE TABLE building_unlock (
    owner_key    TEXT NOT NULL,     -- civic or origin key
    building_key TEXT NOT NULL,
    PRIMARY KEY (owner_key, building_key),
    FOREIGN KEY (building_key) REFERENCES building(key)
);

-- Job replacements: a civic/origin swaps a base job for a variant (Catalytic Processing:
-- Metallurgist → Catalytic Technician). Derived from each base job's `swap_type` blocks,
-- resolving the swap `trigger` through scripted_triggers to a has_civic / has_origin.
DROP TABLE IF EXISTS job_swap_modifier;
DROP TABLE IF EXISTS job_swap_resource;
DROP TABLE IF EXISTS job_swap;
CREATE TABLE job_swap (
    source_kind  TEXT NOT NULL,     -- 'civic' | 'origin'
    source_key   TEXT NOT NULL,     -- e.g. "civic_catalytic_processing"
    base_job     TEXT NOT NULL,     -- e.g. "foundry"
    base_name    TEXT,              -- e.g. "Metallurgist"
    swapped_job  TEXT NOT NULL,     -- e.g. "catalytic_technician"
    swapped_name TEXT,              -- e.g. "Catalytic Technician"
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_kind, source_key, base_job, swapped_job)
);
-- Each swap's effective resources, for both sides of the tooltip: the base job's
-- produces/upkeep evaluated in a plain empire (side='base') and with the swap condition
-- active (side='swapped', e.g. Catalytic Technician → alloys from Food, not Minerals).
CREATE TABLE job_swap_resource (
    source_key  TEXT NOT NULL,
    base_job    TEXT NOT NULL,
    swapped_job TEXT NOT NULL,
    side        TEXT NOT NULL,      -- 'base' | 'swapped'
    kind        TEXT NOT NULL,      -- 'produces' | 'upkeep'
    resource    TEXT NOT NULL,
    amount      REAL NOT NULL,
    PRIMARY KEY (source_key, base_job, swapped_job, side, kind, resource)
);
-- Same, for the job's non-resource modifiers (Crime, Stability, Amenities, …) on each
-- side of the swap (e.g. Enforcer −25 Crime/+0.2 Stability → Educator −20 Crime/+10% Ethics).
CREATE TABLE job_swap_modifier (
    source_key  TEXT NOT NULL,
    base_job    TEXT NOT NULL,
    swapped_job TEXT NOT NULL,
    side        TEXT NOT NULL,      -- 'base' | 'swapped'
    modifier    TEXT NOT NULL,
    value       REAL NOT NULL,
    PRIMARY KEY (source_key, base_job, swapped_job, side, modifier)
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
    selectable         INTEGER NOT NULL DEFAULT 1,  -- 0 = design-only template (playable = empire_design_never)
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

DROP TABLE IF EXISTS tech_fallen_empire;
CREATE TABLE tech_fallen_empire (
    key          TEXT PRIMARY KEY,
    label        TEXT,
    description  TEXT,
    area         TEXT NOT NULL,
    area_label   TEXT,
    category     TEXT,
    category_label TEXT,
    building_key TEXT,               -- FK to building.key; NULL for ship-component techs
    sort_order   INTEGER NOT NULL DEFAULT 0
);

DROP TABLE IF EXISTS tech_repeatable_modifier;
DROP TABLE IF EXISTS tech_repeatable;
CREATE TABLE tech_repeatable (
    key          TEXT PRIMARY KEY,
    label        TEXT,
    description  TEXT,
    area         TEXT NOT NULL,          -- physics / society / engineering
    area_label   TEXT,                   -- Physics / Society / Engineering
    category     TEXT,                   -- field key, e.g. "industry"
    category_label TEXT,                 -- localised, e.g. "Industry"
    levels       INTEGER,                -- -1 = unlimited
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE tech_repeatable_modifier (
    tech_key       TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tech_key, modifier_key),
    FOREIGN KEY (tech_key) REFERENCES tech_repeatable(key)
);
-- Whitelisted values from common/defines (tradition cost formula, slot limits).
DROP TABLE IF EXISTS game_define;
CREATE TABLE game_define (
    key   TEXT PRIMARY KEY,                -- e.g. "TRADITION_COST_TRADITION_EXP"
    value REAL NOT NULL
);

-- Traditions and ascension perks. Conditions are stored as normalised JSON trigger
-- trees (extractor/triggers.py normalize_trigger) that the planner evaluates against an
-- empire profile: {"op":"and"|"or"|"not","args":[…]} | {"leaf":k,"value":v[,"cmp":">"]}.
DROP TABLE IF EXISTS tradition_modifier;
DROP TABLE IF EXISTS tradition_swap;
DROP TABLE IF EXISTS tradition_prereq;
DROP TABLE IF EXISTS tradition;
DROP TABLE IF EXISTS tradition_category_desc;
DROP TABLE IF EXISTS tradition_category;
DROP TABLE IF EXISTS ascension_perk;
CREATE TABLE tradition_category (
    key            TEXT PRIMARY KEY,       -- e.g. "tradition_expansion"
    name           TEXT,
    potential_json TEXT,                   -- who may adopt the tree; NULL = everyone
    required_dlc   TEXT,                   -- FK to dlc.name; top-level DLC gate of potential
    adopt_key      TEXT NOT NULL,          -- FK to tradition.key
    finish_key     TEXT NOT NULL,          -- FK to tradition.key
    sort_order     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);
-- Per-empire-type description variants (`desc = { trigger text }`), first match wins.
CREATE TABLE tradition_category_desc (
    category_key TEXT NOT NULL,
    trigger_json TEXT,                     -- NULL = unconditional
    description  TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (category_key, sort_order),
    FOREIGN KEY (category_key) REFERENCES tradition_category(key)
);
CREATE TABLE tradition (
    key            TEXT PRIMARY KEY,       -- e.g. "tr_expansion_colonization_fever"
    category_key   TEXT NOT NULL,
    role           TEXT NOT NULL,          -- adopt / finish / normal
    name           TEXT,
    description    TEXT,                   -- flavour text (loc <key>_delayed)
    possible_json  TEXT,
    unlocks_agenda TEXT,                   -- council_agenda.key (base variant)
    tooltip        TEXT,                   -- custom_tooltip text (effects not expressed as modifiers)
    sort_order     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (category_key) REFERENCES tradition_category(key)
);
-- Tree edges: tradition_key needs requires_key (has_tradition in its possible).
CREATE TABLE tradition_prereq (
    tradition_key TEXT NOT NULL,
    requires_key  TEXT NOT NULL,
    PRIMARY KEY (tradition_key, requires_key)
);
CREATE TABLE ascension_perk (
    key             TEXT PRIMARY KEY,      -- e.g. "ap_the_flesh_is_weak"
    name            TEXT,
    description     TEXT,                  -- flavour text (loc <key>_desc)
    is_path         INTEGER NOT NULL DEFAULT 0,  -- 1 = from 00_ascension_paths.txt
    potential_json  TEXT,                  -- shown at all
    possible_json   TEXT,                  -- may be picked; its num_ascension_perks bound is
                                           -- the tier, which can differ per empire type
    required_dlc    TEXT,
    tooltip         TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (required_dlc) REFERENCES dlc(name)
);
-- tradition_swap: variant replacing a tradition or ascension perk (owner_key) when its
-- trigger holds; the highest weight wins among several true ones.
CREATE TABLE tradition_swap (
    id              TEXT PRIMARY KEY,      -- "<owner_key>#<n>" (`name` is optional and not unique)
    owner_key       TEXT NOT NULL,         -- tradition.key or ascension_perk.key
    loc_key         TEXT,                  -- the swap's `name`, if any
    name            TEXT,                  -- localised (owner's name when inherit_name / unnamed)
    description     TEXT,                  -- NULL = owner's
    trigger_json    TEXT,
    inherit_effects INTEGER NOT NULL DEFAULT 0,  -- 1 = owner's modifiers apply, not its own
    weight          REAL NOT NULL DEFAULT 1,
    tooltip         TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0
);
-- Edicts and council agendas; unlocked_by_json lists the traditions/perks their potential/allow
-- ask for (has_(active_)tradition / has_ascension_perk, scripted triggers expanded).
DROP TABLE IF EXISTS edict;
DROP TABLE IF EXISTS council_agenda;
CREATE TABLE edict (
    key            TEXT PRIMARY KEY,       -- e.g. "map_the_stars"
    name           TEXT,
    description    TEXT,
    category       TEXT,                   -- resources.category: edicts / campaigns / rare_edicts …
    length         INTEGER,                -- days; -1 = perpetual (until cancelled)
    cost_json      TEXT,                   -- {"cost":{"unity":10},"upkeep":{"unity":10}} (before multipliers)
    potential_json TEXT,
    allow_json     TEXT,
    unlocked_by_json TEXT,                 -- ["tr_…","ap_…"]: traditions/perks that unlock it
    tooltip        TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0
);
-- Modifiers: owner_key = key (while enacted) and "<key>#finish" (finish_modifier, a static
-- modifier lasting finish_days after launch) in tradition_modifier.
CREATE TABLE council_agenda (
    key            TEXT PRIMARY KEY,       -- e.g. "agenda_superior_colonies(_nomad)"
    name           TEXT,
    description    TEXT,
    cost           REAL,                   -- agenda progress needed to launch
    finish_days    INTEGER,                -- finish_modifier duration
    potential_json TEXT,                   -- shown at all (empire-type variants split here)
    allow_json     TEXT,                   -- selectable
    unlocked_by_json TEXT,                 -- ["tr_…","ap_…"]: traditions/perks that unlock it
    effect_tooltip TEXT,                   -- launch effect text
    sort_order     INTEGER NOT NULL DEFAULT 0
);
-- Modifiers of traditions, ascension perks, swaps, edicts and agendas (owner_key = their
-- key, a tradition_swap.id, or "<agenda>#finish").
-- potential_json is set for triggered_modifier blocks.
CREATE TABLE tradition_modifier (
    owner_key      TEXT NOT NULL,
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    potential_json TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_key, sort_order)
);
-- Federation types (event-only ones skipped), the perks each level grants, and the laws.
-- unlocked_by_json / excluded_by_json: tradition/swap/perk/ethic/civic keys the type's `allow`
-- asks for outside / inside a NOT (scripted triggers expanded: is_militarist → both ethics).
DROP TABLE IF EXISTS federation_type_perk;
DROP TABLE IF EXISTS federation_perk_modifier;
DROP TABLE IF EXISTS federation_perk;
DROP TABLE IF EXISTS federation_type;
DROP TABLE IF EXISTS federation_law;
DROP TABLE IF EXISTS federation_law_category;
CREATE TABLE federation_type (
    key              TEXT PRIMARY KEY,     -- e.g. "hegemony_federation"
    name             TEXT,
    description      TEXT,
    base_intel       INTEGER,
    potential_json   TEXT,
    allow_json       TEXT,                 -- who may form it
    unlocked_by_json TEXT,                 -- ["tr_…","ethic_…","civic_…"]
    excluded_by_json TEXT,                 -- ["civic_barbaric_despoilers"]
    sort_order       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE federation_perk (
    key            TEXT PRIMARY KEY,       -- e.g. "federation_fleet_size_1"
    name           TEXT,
    description    TEXT,                   -- flavour (<key>_desc)
    effect         TEXT,                   -- custom_tooltip + modifier-block description texts
    sort_order     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE federation_type_perk (
    type_key       TEXT NOT NULL,
    level          INTEGER NOT NULL,       -- 1..5
    experience     REAL,                   -- XP to the next level; NULL at the top level
    perk_key       TEXT NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (type_key, sort_order),
    FOREIGN KEY (type_key) REFERENCES federation_type(key),
    FOREIGN KEY (perk_key) REFERENCES federation_perk(key)
);
CREATE TABLE federation_perk_modifier (
    perk_key       TEXT NOT NULL,
    scope          TEXT NOT NULL,          -- members / federation / leader (president) / envoy
    modifier_key   TEXT NOT NULL,
    modifier_value REAL NOT NULL,
    potential_json TEXT,                   -- *_triggered_modifier blocks
    sort_order     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (perk_key, sort_order),
    FOREIGN KEY (perk_key) REFERENCES federation_perk(key)
);
CREATE TABLE federation_law_category (
    key            TEXT PRIMARY KEY,       -- e.g. "law_category_centralization"
    name           TEXT,
    description    TEXT,
    potential_json TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE federation_law (
    key            TEXT PRIMARY KEY,       -- e.g. "centralization_low"
    category_key   TEXT NOT NULL,
    name           TEXT,
    description    TEXT,
    potential_json TEXT,
    allow_json     TEXT,
    types_json     TEXT,                   -- ["trade_federation"]: only for these types
    required_centralization INTEGER,
    cohesion_growth REAL,                  -- per month while enacted
    sort_order     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (category_key) REFERENCES federation_law_category(key)
);
"""
