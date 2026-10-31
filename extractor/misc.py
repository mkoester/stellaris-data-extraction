"""Extraction: DLC metadata, named colors, planet classes."""

import re
from .dlc_dates import DLC_RELEASE_DATES
from .localisation import _clean_loc
from .values import _to_list
from .writer import Writer


# ---------------------------------------------------------------------------
# Extraction: DLC metadata
# ---------------------------------------------------------------------------

def host_checks(texts, names) -> dict[str, int]:
    """How often each DLC name is checked with `host_has_dlc` in the script texts."""
    pattern = re.compile(r'host_has_dlc\s*=\s*"([^"]+)"')
    counts = dict.fromkeys(names, 0)
    for text in texts:
        for m in pattern.finditer(text):
            if m.group(1) in counts:
                counts[m.group(1)] += 1
    return counts


def _dlc_rows(info: dict, descriptors: list[dict], checks: dict[str, int] | None = None) -> list[tuple]:
    """Rows for `dlc`: every entry of dlc_info.txt, its category from the matching
    dlc/*/*.dlc descriptor (by name; only installed DLCs have one), its Steam release date and how
    often the game's scripts check for it."""
    dlcs = info.get('dlcs', {})
    if not isinstance(dlcs, dict):
        return []
    category = {d.get('name'): d.get('category') for d in descriptors if isinstance(d, dict)}
    rows = []
    for key, dlc_data in dlcs.items():
        if not isinstance(dlc_data, dict) or not dlc_data.get('name'):
            continue
        name     = dlc_data['name']
        steam_id = dlc_data.get('steam_id') or None
        rows.append((
            name, key, steam_id,
            1 if dlc_data.get('affects_gameplay', 'no') == 'yes' else 0,
            category.get(name),
            DLC_RELEASE_DATES.get(steam_id),
            (checks or {}).get(name, 0),
        ))
    return rows


def extract_dlc(info: dict, descriptors: list[dict], checks: dict[str, int], w: Writer,
                verbose: bool):
    for row in _dlc_rows(info, descriptors, checks):
        w.execute("""
            INSERT OR IGNORE INTO dlc (name, dlc_key, steam_id, affects_gameplay, category, release_date,
                                       host_checks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)
        if verbose:
            print(f"  DLC: {row[0]} | {row[1]} | steam_id: {row[2]} | category: {row[4]} | released: {row[5]}")
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


# ---------------------------------------------------------------------------
# Extraction: prescripted names
# ---------------------------------------------------------------------------

_SHIP_NAME = re.compile(r'^[A-Za-z0-9]+_(?:SHIP|FLEET|ARMY)_(.+)$')


def _ship_name(key: str, text: str) -> bool:
    """A name list's ship, fleet or army name whose text is more than its key's tail
    (`HUM1_SHIP_PeshnabtheFaithful` = "Peshnab the Faithful", `NECROID1_FLEET_MaelstromFleet` =
    "Maelstrom Fleet"): a save names them, a nomad's arkship colony among them, by key."""
    m = _SHIP_NAME.match(key)
    return bool(m) and m.group(1) != text


def prescripted_names(loc: dict[str, str], name_keys: set[str] = frozenset()) -> list[tuple[str, str]]:
    """The loc of every PRESCRIPTED_* / EMPIRE_DESIGN_* key, of the `name_keys` (random-name
    entries that are localisation keys) and of ship, fleet and army names that differ from their
    key, sorted (determinism)."""
    return sorted((k, _clean_loc(v, loc)) for k, v in loc.items()
                  if (k.startswith(('PRESCRIPTED_', 'EMPIRE_DESIGN_')) or k in name_keys
                      or _ship_name(k, v))
                  and v and _clean_loc(v, loc))


def random_name_keys(text: str) -> set[str]:
    """The quoted entries of a common/random_names file. Some are localisation keys the game
    resolves (`voidworm_star_names`: "VOIDWORM_SYSTEM_12" = "Papqaqtet"; "Abaddons_Demesne" =
    "Abaddon's Demesne"), and a save names its systems by them."""
    return set(re.findall(r'"([A-Za-z][A-Za-z0-9_]*)"', text))


def extract_prescripted_names(loc: dict[str, str], name_keys: set[str], w: Writer) -> None:
    for key, text in prescripted_names(loc, name_keys):
        w.execute("INSERT INTO prescripted_name (key, text) VALUES (?, ?)", (key, text))
    w.commit()
