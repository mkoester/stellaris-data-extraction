# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `ethic.sort_order`: the order the ethics are defined in, which is the index of a save's
  per-ethic lists (`country.ethics_distribution`, …), for the empire dashboard's ethics tab.

## [0.1.3] - 2026-10-27

### Added

- `game_start_script`: the game-start scripts that build a new empire's capital
  (`generate_start_buildings_and_districts`, `void_dweller_home_planet_setup`) as ordered effect
  lists, for the empire planner's starting capital.
- `ethic.category_value` and `category_opposite`: each ethic's place on its axis, and the
  federation-cohesion defines for ethics (`FEDERATION_BASE_ETHICS`, `FEDERATION_ETHICS_PENALTY`,
  `FEDERATION_OPPOSING_ETHICS_PENALTY`) in `game_define`, for the empire dashboard's diplomacy tab.

## [0.1.2] - 2026-10-26

### Added

- Species rights carry the game's tooltip, which citizenships take a slavery or purge type, and
  the modifiers each right grants its pops (`species_right_modifier`), for the empire dashboard.
- `action_timer`: the timed modifiers and timed flags each decision, artifact action, astral
  action, relic, diplomatic action, federation law and enclave deal sets or waits on, for the
  empire dashboard's cooldowns — those set by the events an action fires included, and a timer
  only an effect's `limit` waits on as role `bonus` (the action stays possible: Insult with
  Satisfying Insults); `astral_action_cooldown`, each astral action's own cooldown.

### Fixed

- A tooltip line opening with an icon is no longer folded into the line above
  ("Adds the Tankbound trait., Farmers, …"): only `$t$`'s four spaces indent a sub-item.
- Stratum icons are dropped like other decoration icons ("Pop Cat Ruler Elites" → "Elites").

## [0.1.1] - 2026-10-24

### Added

- What it takes to propose each Galactic Community resolution — the tier below in force, DLCs,
  traditions — which resolutions are repeals, and how the AI weighs each when voting.
- Build cost and upkeep of districts and ship sizes (stations, outposts, ships' hulls), the jobs
  each district adds, and build times.
- What each technology needs: its prerequisites, who can get it at all, and whether it is rare or
  dangerous.
- Starbase modules and buildings: names, cost, upkeep, output and build time.
- Strategic resources' market lots and AI weights, AI personalities' trade willingness, which AI
  attitudes trade, and opinion modifiers.
- More game defines: outpost and claim costs, the market fee, pop growth, and the AI's trade
  constants.
- Which budget source each job's output is booked under (drone jobs share the organic jobs'),
  and the colony designations with their descriptions and modifiers.

### Fixed

- Building build costs were missing for every building that declares them through the game's
  cost script (most of them since 4.x).

## [0.1.0] - 2026-10-20

First public release.

### Added

- Parses an installed copy of Stellaris (4.3–4.5) into a normalised SQLite database and an SQL
  dump: empires (origins, ethics, authorities, civics), species and traits, jobs, buildings and
  districts, technology, traditions and ascension perks, edicts, policies, council agendas,
  situations, the Shroud, Galactic Community resolutions, subject agreements, DLCs, localised
  names and descriptions, and the game version.
- Python 3.10+, standard library only; deterministic output.
