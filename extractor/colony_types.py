"""Extraction: colony designations (common/colony_types) — name, description, base modifiers."""

from pathlib import Path
import sys
from .buildings import script_loader
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .politics import expand_inline
from .values import _as_blocks
from .writer import Writer


def colony_type_modifiers(cdef: dict, load_script, variables: dict[str, float]) -> list[tuple[str, float]]:
    """A designation's unconditional `planet_modifier` entries, its own and those of the
    `colony_types/colony_type_planet_modifier*` inline scripts it fills with `MODIFIER`
    (`load_script(name)` → the script's text, or None). The `triggered_planet_modifier`
    extras (Adaptability finished, GalCom resolutions) are left out."""
    sources = [cdef] + [
        expand_inline(text, {k: str(v).strip('"') for k, v in ref.items() if k != 'script'})
        for ref in _as_blocks(cdef.get('inline_script'))
        if isinstance(ref.get('script'), str)
        for text in [load_script(ref['script'].strip('"'))] if text]
    out: dict[str, float] = {}
    for src in sources:
        for block in _as_blocks(src.get('planet_modifier')):
            for k, v in _block_modifier_values(block, variables):
                out[k] = out.get(k, 0) + v
    return list(out.items())


def extract_colony_types(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                         inline_scripts_dir: Path, w: Writer, verbose: bool) -> None:
    """common/colony_types → colony_type (+ colony_type_modifier)."""
    folder = install_dir / 'common' / 'colony_types'
    if not folder.is_dir():
        print(f"  WARNING: {folder} not found", file=sys.stderr)
        return
    load_script = script_loader(inline_scripts_dir)
    order = 0
    for f in sorted(folder.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        for key, cdef in data.items():
            if key.startswith('@') or not isinstance(cdef, dict):
                continue
            raw_name, raw_desc = loc.get(key), loc.get(key + '_desc')
            w.execute(
                "INSERT OR REPLACE INTO colony_type (key, name, description, sort_order) VALUES (?, ?, ?, ?)",
                (key, _clean_loc(raw_name, loc) if raw_name else None,
                 _clean_loc(raw_desc, loc, keep_newlines=True) if raw_desc else None, order))
            order += 1
            for m_order, (mk, mv) in enumerate(colony_type_modifiers(cdef, load_script, scripted_vars)):
                w.execute(
                    """INSERT OR REPLACE INTO colony_type_modifier (colony_type_key, modifier, value, sort_order)
                       VALUES (?, ?, ?, ?)""", (key, mk, mv, m_order))
                _write_modifier_label(w, loc, mk)
    w.commit()
    if verbose:
        print(f"  colony types: {order}")
