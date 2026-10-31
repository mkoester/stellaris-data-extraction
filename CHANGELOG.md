# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Reads Stellaris 4.5.3 (open beta, build 7793) as well as 4.5.2, which wrote some of it in a new
form; the extraction ran without an error on 4.5.3 but lost these silently.

### Added

- `ethic_attraction.op`: how a `country_attraction` rule applies — `factor` (× it, 4.5.2) or
  `add` (+ it, 4.5.3, onto a base of 1), in `sort_order`. 4.5.3's `add` rules had no number.
- The define `TECH_COST_DIFFICULTY_SCALE_MULT` (0.025): how much the difficulty raises tech costs,
  for the empire dashboard's research due dates.
- The define `LEADER_POOL_REFRESH_TIME` (1800 days): when the recruitable leaders are replaced,
  for the empire dashboard's Upcoming list.
- `ethic_attraction.per_json` / `per_value`: "From Council" read from its script value
  (`count_councilors_with_ethic`: + 0.1 per ruler or councilor with the ethic), so a save can
  answer it — the rule's number is `factor` + `per_value` for each leader the leaf counts.

### Fixed

- `ethic_attraction.factor_text` is NULL, not the text `None`, for a rule without a value.
- `building_modifier`: the Fallen Empire buildings' job automation, which 4.5.3 moved into the
  inline script `jobs/fe_automation` (the regular and the gestalt job, as before), including
  4.5.3's new Logistic Drone automation of the Virtual Bliss Plaza and Omni-Bliss Nexus.
- Triggers: a safe scope (`owner? = { … }`) is the scope (`owner`), no longer a test of its own.

## [0.1.4] - 2026-10-31

### Added

- `ethic.sort_order`: the order the ethics are defined in, which is the index of a save's
  per-ethic lists (`country.ethics_distribution`, …), for the empire dashboard's ethics tab.
- `situation_stage`: each situation's stages with their names, in the order a save's `stage`
  counts them (the nomads' Operational Reserves: Critical, Low, Medium, High).
- `ethic_attraction`: each ethic's own attraction rules — the empire-wide `country_attraction`
  factors and the `pop_attraction_tag` reasons — with their conditions, normalised; the scopes
  the reasons look into (council and governor ethics, assigned jobs, our species' living standards
  and traits; another organic species, all on full citizenship, one enslaved) as leaves a save
  can answer.
- Defines for a colony's amenities, happiness and stability (`MIN|MAX_PLANET_AMENITIES`,
  `LOW|HIGH_HAPPINESS_STABILITY_EFFECT`, `BASE|MIN|MAX|LOW_PLANET_STABILITY`, `BASE_HAPPINESS`,
  crime).
- `authority.election_type` (democratic / oligarchic / none), which `has_election_type` tests.
- `prescripted_name` also holds the name lists' ship names that differ from their key
  (`HUM1_SHIP_PeshnabtheFaithful` = "Peshnab the Faithful"), as a save names ships by key.

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
