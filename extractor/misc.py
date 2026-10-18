"""Extraction: DLC metadata, named colors, planet classes."""

from .localisation import _clean_loc
from .values import _to_list
from .writer import Writer


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
        ideal = 1 if str(pdef.get('ideal')).strip() == 'yes' else 0
        raw_name = loc.get(key)
        name = _clean_loc(raw_name, loc) if raw_name else None
        w.execute(
            """INSERT OR REPLACE INTO planet_class
                   (key, name, climate, colonizable, is_standard, ideal, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, name, climate, colonizable, is_standard, ideal, order),
        )
        if verbose:
            print(f"  planet_class {key} | {name} | climate={climate} colonizable={colonizable}")
        order += 1
    w.commit()
