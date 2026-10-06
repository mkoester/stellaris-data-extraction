"""Command-line entry point: argument parsing and the extraction pipeline."""

from pathlib import Path
import argparse
import json
import sqlite3
import sys
from . import __version__
from .buildings import extract_buildings_ref
from .clausewitz import parse_file
from .empires import extract_flag_assets, extract_initializers, extract_prescripted_empires, extract_rooms
from .government import _custom_empire_initializers, compute_ascension_restrictions, extract_authorities, extract_civics, extract_ethics, extract_origins, extract_policy_restrictions
from .jobs import extract_job_swaps, extract_jobs
from .localisation import _LOC_ENTRY, _LOC_REF, load_localisation
from .misc import extract_dlc, extract_named_colors, extract_planet_classes
from .modifiers import _load_scaling_values, _load_scripted_modifier_meta, _load_scripted_variables
from .precursors import _default_overclocks, extract_deposits, extract_precursor_systems, extract_precursors
from .species import _ROBOT_BASE_TRAIT, extract_portrait_sets, extract_species_archetypes, extract_species_classes
from .tech import extract_fallen_empire_techs, extract_repeatable_techs
from .traits import TRAIT_CATEGORIES, extract_trait_categories, extract_trait_modifiers, extract_traits, is_species_trait_file
from .triggers import _scripted_trigger_index, _signals
from .values import _as_blocks, _resolve_modifier_float
from .writer import Writer


def _iter_parsed(files, progress, announce: bool = False):
    """Yield (path, parsed dict) per file; parse failures become warnings."""
    for f in files:
        if announce:
            print(f"  Reading: {f.name}", file=progress)
        try:
            yield f, parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)


def _merge_dir(d: Path, progress) -> dict:
    """Merge every *.txt in d into one dict (later files win); warn if d is missing."""
    if not d.exists():
        print(f"  WARNING: {d} not found", file=progress)
        return {}
    merged: dict = {}
    for _, data in _iter_parsed(sorted(d.glob('*.txt')), progress):
        merged.update(data)
    return merged


def main():
    ap = argparse.ArgumentParser(
        description=f'Stellaris Data Extractor v{__version__} -> SQL / SQLite',
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
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
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
    # Authoritative good/bad modifier flags (before any _write_modifier_label runs).
    _load_scripted_modifier_meta(install_dir)
    archetype_base_traits: dict[str, str] = {'ROBOT': _ROBOT_BASE_TRAIT}
    for f, data in _iter_parsed(sc_files, progress, announce=True):
        # Collect archetype → base_trait before writing to DB
        for class_data in data.values():
            if isinstance(class_data, dict):
                arch  = class_data.get('archetype')
                trait = class_data.get('trait')
                if arch and trait and arch not in archetype_base_traits:
                    archetype_base_traits[arch] = str(trait).strip('"')
        extract_species_classes(data, f.name, loc, w, args.verbose)

    # --- species_archetypes ---
    sa_files = sorted(archetypes_dir.glob('*.txt')) if archetypes_dir.exists() else []
    print(f"\n[2/5] species_archetypes: {len(sa_files)} file(s)", file=progress)
    for f, data in _iter_parsed(sa_files, progress, announce=True):
        extract_species_archetypes(data, f.name, archetype_base_traits, w, args.verbose)

    # --- portrait_sets ---
    ps_files = sorted(portrait_dir.glob('*.txt')) if portrait_dir.exists() else []
    print(f"\n[3/5] portrait_sets: {len(ps_files)} file(s)", file=progress)
    for f, data in _iter_parsed(ps_files, progress, announce=True):
        extract_portrait_sets(data, f.name, w, args.verbose)

    # --- traits ---
    tr_files = sorted(
        f for f in traits_dir.glob('*.txt') if is_species_trait_file(f)
    ) if traits_dir.exists() else []
    print(f"\n[4/6] traits: {len(tr_files)} file(s)", file=progress)
    # Trait modifiers resolve @vars from common/scripted_variables and gate
    # triggered_species/pop_group_modifier blocks through the scripted_triggers index.
    trait_vars = _load_scripted_variables(install_dir)
    trait_idx = _scripted_trigger_index(install_dir)
    scaling_values = _load_scaling_values(install_dir)
    for f, data in _iter_parsed(tr_files, progress, announce=True):
        extract_traits(data, f.name, loc, w, args.verbose)
        extract_trait_modifiers(data, f.name, loc, trait_vars, trait_idx,
                                inline_scripts_dir, scaling_values, w, args.verbose)

    # --- named colors + trait categories ---
    co_files = sorted(colors_dir.glob('*.txt')) if colors_dir.exists() else []
    print(f"\n[5/6] named_colors: {len(co_files)} file(s)", file=progress)
    for f, data in _iter_parsed(co_files, progress, announce=True):
        extract_named_colors(data, f.name, w, args.verbose)
    # trait_category references named_color (FK), so it runs after colors are loaded
    print(f"  Writing {len(TRAIT_CATEGORIES)} trait categories", file=progress)
    extract_trait_categories(w, args.verbose)

    # --- ethics ---
    et_files = sorted(ethics_dir.glob('*.txt')) if ethics_dir.exists() else []
    print(f"\n[6/7] ethics: {len(et_files)} file(s)", file=progress)
    for _, data in _iter_parsed(et_files, progress, announce=True):
        extract_ethics(data, loc, w, args.verbose)

    # --- policy restrictions ---
    po_files = sorted(policies_dir.glob('*.txt')) if policies_dir.exists() else []
    print(f"\n[7/7] policies: {len(po_files)} file(s)", file=progress)
    for f, data in _iter_parsed(po_files, progress, announce=True):
        extract_policy_restrictions(data, f.name, loc, w, args.verbose)

    # --- precursors ---
    print(f"\n[8/8] precursors", file=progress)
    # Merge the precursor_civilizations files (base + DLC) into one dict.
    precursor_civ = _merge_dir(precursor_dir, progress)
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
    static_mods = _merge_dir(static_mod_dir, progress)
    # Building definitions — planet (common/buildings) and orbital starbase
    # (common/starbase_buildings) — precursor buildings' effects/restrictions.
    buildings: dict = {}
    for bdir in (buildings_dir, starbase_buildings_dir):
        buildings.update(_merge_dir(bdir, progress))
    # Technology definitions (the precursor "Secrets of …" techs live here).
    # Merged like _merge_dir, but per-file so the fallen-empire tech keys can be
    # picked out of their own file (they're not otherwise distinguishable).
    technologies: dict = {}
    fe_tech_keys: set[str] = set()
    if technology_dir.exists():
        for f, parsed in _iter_parsed(sorted(technology_dir.glob('*.txt')), progress):
            technologies.update(parsed)
            if f.name == '00_fallen_empire_tech.txt':
                fe_tech_keys = {k for k, v in parsed.items()
                                if isinstance(v, dict) and not k.startswith('@')}
    else:
        print(f"  WARNING: {technology_dir} not found", file=progress)
    # Global scripted variables (e.g. @activation_cost, @triumph_duration) used to
    # resolve relic triumph costs/cooldowns.
    scripted_vars: dict[str, float] = {}
    scripted_var_dir = install_dir / 'common' / 'scripted_variables'
    if scripted_var_dir.exists():
        for _, data in _iter_parsed(sorted(scripted_var_dir.glob('*.txt')), progress):
            for vk, vv in data.items():
                if vk.startswith('@'):
                    fv = _resolve_modifier_float(vv)
                    if fv is not None:
                        scripted_vars[vk] = fv
    # Event index (id → event) for following relic actives that fire a country_event
    # (e.g. the Reality Perforator → precursor.105 → its random combat buffs).
    events_index: dict = {}
    events_dir = install_dir / 'events'
    relic_event_files = ('precursor_events.txt', 'precursor_events_cosmic_storms.txt',
                         'ancient_relics_arcsite_events_1.txt', 'ancient_relics_arcsite_events_2.txt')
    relic_event_paths = [events_dir / fn for fn in relic_event_files
                         if (events_dir / fn).exists()]
    for _, data in _iter_parsed(relic_event_paths, progress):
        # Index every event type: relic actives fire country_events, but the
        # home-world discovery rewards live in ship_event/planet_event blocks.
        for ev_type in ('country_event', 'ship_event', 'planet_event', 'fleet_event'):
            for ev in _as_blocks(data.get(ev_type)):
                if isinstance(ev.get('id'), str):
                    events_index.setdefault(ev['id'], ev)
    print(f"  {len(precursor_civ)} civ entries, {len(relics)} relics, "
          f"{len(static_mods)} static modifiers, {len(scripted_vars)} scripted vars, "
          f"{len(events_index)} events loaded", file=progress)
    extract_precursors(precursor_civ, relics, static_mods, events_index, buildings,
                       technologies, inline_scripts_dir, scripted_vars, loc, w, args.verbose)

    # --- deposits (dictionary for home-system planet deposits) ---
    dep_files = sorted(deposits_dir.glob('*.txt')) if deposits_dir.exists() else []
    print(f"\n[9/10] deposits: {len(dep_files)} file(s)", file=progress)
    # Arc Furnace output is split per smelting (overclock) mode; show the starting one.
    default_overclocks = _default_overclocks(
        _merge_dir(install_dir / 'common' / 'megastructures', progress),
        _merge_dir(install_dir / 'common' / 'megastructure_overclock_types', progress),
        trait_vars)
    for f, data in _iter_parsed(dep_files, progress):
        extract_deposits(data, f.name, loc, trait_vars, lambda t: _signals(t, trait_idx),
                         default_overclocks, w, args.verbose)

    # --- precursor home systems (planets, deposits, megastructures) ---
    print(f"\n[10/10] precursor home systems", file=progress)
    initializers = _merge_dir(sysinit_dir, progress)
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
        for _, data in _iter_parsed(sorted(authorities_dir.glob('*.txt')), progress):
            extract_authorities(data, loc, scripted_vars, w, args.verbose)
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
    planet_classes = _merge_dir(planet_class_dir, progress)
    if planet_classes:
        extract_planet_classes(planet_classes, loc, w, args.verbose)

    # --- prescripted (predefined) empires ---
    print(f"\n[empires] {prescripted_dir}", file=progress)
    if prescripted_dir.exists():
        empire_order = 0
        for _, data in _iter_parsed(sorted(prescripted_dir.glob('*.txt')), progress):
            empire_order = extract_prescripted_empires(data, loc, w,
                                                       empire_order, args.verbose)
    else:
        print(f"  WARNING: {prescripted_dir} not found", file=progress)

    # --- home-system initializers (custom_empire) + flag assets ---
    print(f"\n[initializers] {sysinit_dir}", file=progress)
    if sysinit_dir.exists():
        init_order = 0
        for _, data in _iter_parsed(sorted(sysinit_dir.glob('*.txt')), progress):
            init_order = extract_initializers(data, loc, w, init_order)
    print(f"\n[flags] {install_dir / 'flags'}", file=progress)
    extract_flag_assets(install_dir, w, args.verbose)
    print(f"\n[rooms] {prescripted_dir}", file=progress)
    extract_rooms(install_dir, w, args.verbose)
    print(f"\n[jobs] {install_dir / 'common' / 'pop_jobs'}", file=progress)
    extract_jobs(install_dir, loc, scripted_vars, inline_scripts_dir, w, args.verbose)
    print(f"\n[buildings-ref] {install_dir / 'common' / 'buildings'}", file=progress)
    extract_buildings_ref(install_dir, loc, scripted_vars, inline_scripts_dir, w, args.verbose)
    print(f"\n[job-swaps] {install_dir / 'common' / 'pop_jobs'}", file=progress)
    extract_job_swaps(install_dir, loc, scripted_vars, inline_scripts_dir, w, args.verbose)

    # --- repeatable technologies ---
    print(f"\n[repeatable-techs] {technology_dir}", file=progress)
    extract_repeatable_techs(technologies, loc, scripted_vars, w, args.verbose)

    # --- fallen empire technologies ---
    # Build a raw-loc mapping (before $KEY$ resolution) for the fallen empire loc file so
    # we can detect which tech keys reference a building key directly ($building_xxx$).
    fe_building_refs: dict[str, str] = {}
    fe_loc_file = install_dir / 'localisation' / 'english' / 'fallen_empire_l_english.yml'
    if fe_loc_file.exists():
        raw_text = fe_loc_file.read_text(encoding='utf-8-sig', errors='replace')
        for m in _LOC_ENTRY.finditer(raw_text):
            _key, _lbl = m.group(1), m.group(3)
            ref = _LOC_REF.match(_lbl)
            if ref and ref.group(1).startswith('building_'):
                fe_building_refs[_key] = ref.group(1)
    print(f"\n[fallen-empire-techs] {technology_dir / '00_fallen_empire_tech.txt'}", file=progress)
    extract_fallen_empire_techs(technologies, fe_tech_keys, fe_building_refs, loc, w, args.verbose)

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
        n_rep_techs     = tmp.execute("SELECT COUNT(*) FROM tech_repeatable").fetchone()[0]
        n_rep_tech_mods = tmp.execute("SELECT COUNT(*) FROM tech_repeatable_modifier").fetchone()[0]
        n_fe_techs      = tmp.execute("SELECT COUNT(*) FROM tech_fallen_empire").fetchone()[0]
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
|  Repeatable techs   : {n_rep_techs:<20}|
|  Repeatable tech mods: {n_rep_tech_mods:<19}|
|  Fallen empire techs: {n_fe_techs:<20}|
+------------------------------------------+""", file=progress)
    elif sql_path:
        sql_size = Path(sql_path).stat().st_size
        print(f"\nDone. SQL file: {sql_path} ({sql_size:,} bytes)", file=progress)
    else:
        print("\nDone.", file=progress)
