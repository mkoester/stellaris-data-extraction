"""Extraction: species classes, archetypes, portrait sets."""

from .values import _to_list
from .writer import Writer


# ---------------------------------------------------------------------------
# Extraction: species_classes
# ---------------------------------------------------------------------------

# Keys handled explicitly and excluded from the generic attribute table
EXTRACTED_KEYS = {
    'archetype', 'trait', 'graphical_culture', 'move_pop_sound_effect',
    'portrait_modding', 'possible', 'playable',
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
        # The class's own auto-included base trait (e.g. INF/Thermophile → trait_infernal,
        # LITHOID → trait_lithoid) — class-specific, so it overrides the archetype default.
        base_trait        = class_data.get('trait')
        base_trait        = str(base_trait).strip('"') if base_trait else None
        # Player-selectable at empire creation unless `playable = { always = no }` (NPC-only:
        # Swarm, AI, Paragon, Enclaves, all PRE_* pre-sapients). DLC/flag gates stay playable.
        playable_raw      = class_data.get('playable')
        playable          = 0 if (isinstance(playable_raw, dict)
                                  and str(playable_raw.get('always', '')).lower() == 'no') else 1

        w.execute("""
            INSERT OR REPLACE INTO species_class
                (key, label, archetype, base_trait, playable, graphical_culture,
                 move_pop_sound_effect, portrait_modding, possible_block, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (class_key, label, archetype, base_trait, playable, graphical_culture,
              move_pop_sound, portrait_modding, possible_raw, source_file))

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
                         required_dlc, selectable: int, source_file: str):
    w.execute("""
        INSERT OR IGNORE INTO portrait_set
            (set_key, species_class_key, portrait_id, required_dlc, selectable, source_file)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (set_key, species_class_key, portrait_id, required_dlc, selectable, source_file))


def _dlc_from_condition(cond_block: dict) -> str | None:
    """Extract host_has_dlc from a randomizable/playable sub-block."""
    for sub_key in ('randomizable', 'playable'):
        sub = cond_block.get(sub_key)
        if isinstance(sub, dict):
            dlc = sub.get('host_has_dlc')
            if dlc:
                return dlc
    return None


def _block_selectable(block: dict) -> bool:
    """A conditional_portraits block is player-selectable unless its `playable` is always=no
    (those are randomizable/NPC-only variants)."""
    pl = block.get('playable')
    return not (isinstance(pl, dict) and str(pl.get('always', '')).lower() == 'no')


def extract_portrait_sets(data: dict, source_file: str, w: Writer, verbose: bool):
    for set_key, set_data in data.items():
        if not isinstance(set_data, dict):
            continue

        species_class_key = set_data.get('species_class') or None

        # Base portraits (no DLC required, always player-selectable)
        base_portraits = _to_list(set_data.get('portraits'))
        for pid in base_portraits:
            _portrait_set_insert(w, set_key, species_class_key, pid, None, 1, source_file)

        # conditional_portraits is ONE dict when a set has a single block, or a list of dicts
        # when it has several (duplicate keys). Normalise to a flat list of dict blocks —
        # `_to_list` mishandles the single-dict case (returns its keys), which silently
        # dropped every DLC-gated set (Aquatic/Toxoid/Lithoid/Infernal/cybernetic/psionic).
        cond_raw = set_data.get('conditional_portraits')
        cond_blocks: list[dict] = []
        for b in (cond_raw if isinstance(cond_raw, list) else [cond_raw]):
            if isinstance(b, list):
                cond_blocks.extend(x for x in b if isinstance(x, dict))
            elif isinstance(b, dict):
                cond_blocks.append(b)

        n_cond = 0
        for block in cond_blocks:
            required_dlc = _dlc_from_condition(block)
            selectable = 1 if _block_selectable(block) else 0
            for pid in _to_list(block.get('portraits')):
                _portrait_set_insert(w, set_key, species_class_key, pid, required_dlc, selectable, source_file)
                n_cond += 1

        if verbose:
            print(f"  Set: {set_key} | class: {species_class_key} | {len(base_portraits)} base + {n_cond} conditional portrait(s)")

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
