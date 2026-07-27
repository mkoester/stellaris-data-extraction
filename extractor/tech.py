"""Extraction: repeatable and fallen-empire technologies."""

from .localisation import _clean_loc
from .modifiers import _write_modifier_label
from .writer import Writer


_AREA_LABELS = {'physics': 'Physics', 'society': 'Society', 'engineering': 'Engineering'}

_AREA_SORT = {'physics': 0, 'society': 1, 'engineering': 2}

_CATEGORY_SORT = {
    # Physics
    'field_manipulation': 0, 'particles': 1, 'computing': 2, 'psionics': 3,
    # Society
    'biology': 0, 'military_theory': 1, 'new_worlds': 2, 'statecraft': 3,
    # Engineering
    'industry': 0, 'materials': 1, 'propulsion': 2, 'voidcraft': 3,
}


def _tech_category(data: dict) -> str | None:
    """The tech's field key. `category` is a block list, e.g. category = { industry }."""
    raw = data.get('category')
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip('"')
    if isinstance(raw, str):
        return raw.strip('"')
    return None


def _tech_common(key: str, data: dict, loc: dict[str, str]) -> tuple | None:
    """Shared per-tech fields; None when the entry has no usable area."""
    area = data.get('area')
    if not isinstance(area, str):
        return None
    area = area.strip('"').lower()

    category = _tech_category(data)

    label_raw = loc.get(key)
    label = _clean_loc(label_raw, loc) if label_raw else None
    desc_raw = loc.get(key + '_desc')
    description = _clean_loc(desc_raw, loc) if desc_raw else None

    area_label = _AREA_LABELS.get(area, area.capitalize())
    cat_label_raw = loc.get(category) if category else None
    category_label = _clean_loc(cat_label_raw, loc) if cat_label_raw else None

    return area, category, key, label, description, area_label, category_label


def _sort_key(entry: tuple) -> tuple:
    """Stable order: area → category → key."""
    return (_AREA_SORT.get(entry[0], 99), _CATEGORY_SORT.get(entry[1] or '', 99), entry[2])


def _is_repeatable(data: dict) -> bool:
    """Repeatable techs are the ones whose weight_groups contain "repeatable"."""
    wg = data.get('weight_groups')
    return isinstance(wg, (list, dict)) and 'repeatable' in wg


def extract_repeatable_techs(
    technologies: dict,
    loc: dict[str, str],
    scripted_vars: dict[str, float],
    w: Writer,
    verbose: bool,
) -> None:
    entries: list[tuple] = []
    for key, data in technologies.items():
        if not isinstance(data, dict) or not _is_repeatable(data):
            continue
        common = _tech_common(key, data, loc)
        if common is None:
            continue

        levels_raw = data.get('levels')
        try:
            levels = int(str(levels_raw).strip())
        except (ValueError, TypeError):
            levels = None

        entries.append(common + (levels, data))

    entries.sort(key=_sort_key)

    for sort_order, entry in enumerate(entries):
        (area, category, key, label, description,
         area_label, category_label, levels, data) = entry
        w.execute(
            """INSERT OR REPLACE INTO tech_repeatable
               (key, label, description, area, area_label, category, category_label, levels, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, label, description, area, area_label, category, category_label, levels, sort_order),
        )

        modifier_block = data.get('modifier')
        if isinstance(modifier_block, dict):
            for mod_order, (mod_key, mod_val) in enumerate(modifier_block.items()):
                if isinstance(mod_val, list):
                    mod_val = mod_val[-1]
                s = str(mod_val).strip()
                value = scripted_vars.get(s) if s.startswith('@') else None
                if value is None:
                    try:
                        value = float(s)
                    except (ValueError, TypeError):
                        continue

                w.execute(
                    """INSERT OR REPLACE INTO tech_repeatable_modifier
                       (tech_key, modifier_key, modifier_value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mod_key, value, mod_order),
                )
                _write_modifier_label(w, loc, mod_key)

                if verbose:
                    print(f"  {key} | {mod_key} = {value}")

        if verbose:
            print(f"  {key} ({area}/{category}) levels={levels}")

    w.commit()


def extract_fallen_empire_techs(
    technologies: dict,
    fe_tech_keys: set[str],
    fe_building_refs: dict[str, str],
    loc: dict[str, str],
    w: Writer,
    verbose: bool,
) -> None:
    """Extract the techs from 00_fallen_empire_tech.txt (identified by fe_tech_keys).

    fe_building_refs maps tech_key -> building_key for entries whose raw loc label
    was a sole $building_xxx$ reference (computed before loc resolution in cli.main).
    """
    entries: list[tuple] = []
    for key in fe_tech_keys:
        data = technologies.get(key)
        if not isinstance(data, dict):
            continue
        common = _tech_common(key, data, loc)
        if common is None:
            continue
        # building_key was pre-computed from the raw (pre-resolution) loc in cli.main.
        entries.append(common + (fe_building_refs.get(key),))

    entries.sort(key=_sort_key)

    for sort_order, entry in enumerate(entries):
        (area, category, key, label, description,
         area_label, category_label, building_key) = entry
        w.execute(
            """INSERT OR REPLACE INTO tech_fallen_empire
               (key, label, description, area, area_label, category, category_label, building_key, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, label, description, area, area_label, category, category_label, building_key, sort_order),
        )
        if verbose:
            print(f"  {key} ({area}/{category}) building={building_key}")

    w.commit()
