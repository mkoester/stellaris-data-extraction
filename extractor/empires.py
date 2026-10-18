"""Extraction: prescripted empires, system initializers, rooms, flag assets."""

from pathlib import Path
import re
import sys
from .localisation import _clean_loc
from .values import _to_list
from .writer import Writer


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

        # Design-only templates (`playable = empire_design_never`) are duplicated by a
        # real `<key>_1` entry — exclude them so the picker has no duplicates.
        selectable = 0 if scalar(edef.get('playable')) == 'empire_design_never' else 1

        civics_raw = edef.get('civics')
        civics = _to_list(civics_raw) if not isinstance(civics_raw, dict) else []

        w.execute(
            """INSERT OR REPLACE INTO prescripted_empire
                   (key, name, adjective, ship_prefix, species_class, species_portrait,
                    species_name, species_plural, species_adjective,
                    secondary_class, secondary_portrait, secondary_name,
                    secondary_plural, secondary_adjective, ruler_name,
                    planet_name, system_name, authority, government,
                    origin, planet_class, initializer, graphical_culture, selectable, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
             scalar(edef.get('initializer')), scalar(edef.get('graphical_culture')),
             selectable, order),
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


_FLAG_SWATCH = re.compile(
    r'^\s*(\w+)\s*=\s*\{[^#\n]*?\bmap\s*=\s*rgb\s*\{\s*(\d+)\s+(\d+)\s+(\d+)\s*\}', re.M)


def flag_colors(text: str) -> list[tuple[str, int, int, int]]:
    """flags/colors.txt → (swatch, r, g, b) of each swatch's `map` colour, which the galaxy map
    uses for an empire's territory and borders (an empire's is its flag's first colour). The
    Clausewitz parser drops `rgb { … }` values, so this reads the one-line swatches directly."""
    return [(m[1], int(m[2]), int(m[3]), int(m[4])) for m in _FLAG_SWATCH.finditer(text)]


def extract_flag_colors(install_dir: Path, w: Writer, verbose: bool) -> None:
    path = install_dir / 'flags' / 'colors.txt'
    if not path.is_file():
        print(f"  WARNING: {path} not found", file=sys.stderr)
        return
    rows = flag_colors(path.read_text(encoding='utf-8-sig'))
    for row in rows:
        w.execute("INSERT OR REPLACE INTO flag_color (key, r, g, b) VALUES (?, ?, ?, ?)", row)
    w.commit()
    if verbose:
        print(f"  flag colours: {len(rows)}")


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
