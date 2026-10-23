# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- What it takes to propose each Galactic Community resolution — the tier below in force, DLCs,
  traditions — and which resolutions are repeals.
- Build cost and upkeep of districts and ship sizes (stations, outposts, ships' hulls), the jobs
  each district adds, and build times.
- What each technology needs: its prerequisites, who can get it at all, and whether it is rare or
  dangerous.
- Starbase modules and buildings: names, cost, upkeep, output and build time.
- Strategic resources' market lots and AI weights, AI personalities' trade willingness, which AI
  attitudes trade, and opinion modifiers.
- More game defines: outpost and claim costs, the market fee, pop growth, and the AI's trade
  constants.

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
