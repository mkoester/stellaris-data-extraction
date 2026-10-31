"""Extraction: leader traits (the common/traits files the species extraction skips)."""

from pathlib import Path
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc, inline_refs
from .modifiers import _block_modifier_values, _write_modifier_label
from .traits import is_species_trait_file
from .values import _as_blocks, _to_list
from .writer import Writer

# The untriggered modifier blocks of a leader trait and where they apply (common/traits/
# 000_documentation_leader_traits.txt): `modifier` "to scope leader is assigned to", stored as
# `modifier`; galcom / federation while the delegate there.
SCOPES = ('modifier', 'self_modifier', 'councilor_modifier', 'fleet_modifier', 'army_modifier',
          'planet_modifier', 'sector_modifier', 'galcom_modifier', 'federation_modifier')


def trait_modifiers(data: dict, variables: dict[str, float]) -> list[tuple[str, str, float]]:
    """[(scope, modifier_key, value)] of a leader trait's untriggered modifier blocks. Pure."""
    out = []
    for scope in SCOPES:
        for block in _as_blocks(data.get(scope)):
            out += [(scope.removesuffix('_modifier') or 'country', k, v)
                    for k, v in _block_modifier_values(block, variables)]
    return out


def extract_leader_traits(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                          w: Writer, progress=sys.stderr) -> None:
    tdir = install_dir / 'common' / 'traits'
    files = [f for f in sorted(tdir.glob('*.txt'))
             if not is_species_trait_file(f) and 'documentation' not in f.stem]
    n = 0
    for f in files:
        data = parse_file(f)
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                try:
                    variables[k] = float(str(v))
                except ValueError:
                    pass
        for key, t in data.items():
            if key.startswith('@') or not isinstance(t, dict):
                continue
            name, desc = loc.get(key), loc.get(f'{key}_desc')
            classes = [c for c in _to_list(t.get('leader_class')) if isinstance(c, str)]
            w.execute(
                """INSERT OR REPLACE INTO leader_trait (key, name, description, leader_classes, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, _clean_loc(inline_refs(name, loc), loc) if name else None,
                 _clean_loc(inline_refs(desc, loc), loc, keep_newlines=True) if desc else None,
                 ','.join(classes) or None, n))
            for order, (scope, mk, mv) in enumerate(trait_modifiers(t, variables)):
                w.execute(
                    """INSERT OR IGNORE INTO leader_trait_modifier
                       (trait_key, scope, modifier_key, modifier_value, sort_order) VALUES (?, ?, ?, ?, ?)""",
                    (key, scope, mk, mv, order))
                _write_modifier_label(w, loc, mk)
            n += 1
    print(f"  {n} leader traits from {len(files)} files", file=progress)
    w.commit()
