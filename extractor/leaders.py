"""Extraction: leader traits (the common/traits files the species extraction skips)."""

from pathlib import Path
import sys
from .buildings import _expand_inline_refs, script_loader
from .clausewitz import parse_file
from .localisation import _clean_loc, inline_refs
from .modifiers import _block_modifier_values, _write_modifier_label
from .traditions import dumps
from .traits import is_species_trait_file
from .triggers import normalize_trigger
from .values import _as_blocks, _to_list
from .writer import Writer

# The modifier blocks of a leader trait and where they apply (common/traits/
# 000_documentation_leader_traits.txt): `modifier` "to scope leader is assigned to"; galcom /
# federation while the delegate there; background_planet "to the leader's homeworld/background
# planet". Each also `triggered_<block>` with a `potential` (THIS the leader, FROM the scope it
# applies to): 145 traits have some (4.5.3) — Righteous's crime only so, by gestalt or not.
SCOPES = ('modifier', 'self_modifier', 'councilor_modifier', 'fleet_modifier', 'army_modifier',
          'planet_modifier', 'sector_modifier', 'galcom_modifier', 'federation_modifier',
          'background_planet_modifier')


def owner_conditions(k, v):
    """The `structured` hook of `normalize_trigger` for a trait's `potential` (THIS the leader):
    `owner = { … }` is the leader's empire, its conditions read as the empire's — Expertise's
    `owner = { has_valid_civic = civic_technocracy }`. Other scopes stay opaque."""
    if k == 'owner' and isinstance(v, dict):
        return normalize_trigger(v, None, None, owner_conditions)
    return None


def _scaled(block: dict, variables: dict[str, float]) -> list[tuple[str, float]]:
    """A block's modifier values, × its `mult` where it has a number (an Expertise trait's tier)."""
    try:
        mult = float(str(block.get('mult', 1)))
    except ValueError:
        mult = 1.0
    return [(k, round(v * mult, 10)) for k, v in _block_modifier_values(block, variables)]


def trait_modifiers(data: dict, variables: dict[str, float], inlined: tuple[dict, ...] = ()
                    ) -> list[tuple[str, str, float, dict | None]]:
    """[(scope, modifier_key, value, potential)] of a leader trait's modifier blocks — its own,
    then those of the `inline_script`s it names at its top (`inlined`, expanded: 36 Expertise
    traits' Technocracy research, `traits/technocracy_expertise_effects`) —, each scope's plain ones
    then its triggered ones (their `potential`, else None). Pure."""
    out = []
    for src in (data, *inlined):
        for scope in SCOPES:
            name = scope.removesuffix('_modifier')
            for block in _as_blocks(src.get(scope)):
                out += [(name, k, v, None) for k, v in _scaled(block, variables)]
            for block in _as_blocks(src.get(f'triggered_{scope}')):
                body = {k: v for k, v in block.items() if k != 'potential'}
                out += [(name, k, v, block.get('potential')) for k, v in _scaled(body, variables)]
    return out


def extract_leader_traits(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                          w: Writer, progress=sys.stderr) -> None:
    tdir = install_dir / 'common' / 'traits'
    load = script_loader(install_dir / 'common' / 'inline_scripts')
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
            inlined = tuple(_expand_inline_refs(t, load))
            for order, (scope, mk, mv, potential) in enumerate(trait_modifiers(t, variables, inlined)):
                w.execute(
                    """INSERT INTO leader_trait_modifier
                       (trait_key, scope, modifier_key, modifier_value, potential_json, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, scope, mk, mv, dumps(normalize_trigger(potential, None, None, owner_conditions)),
                     order))
                _write_modifier_label(w, loc, mk)
            n += 1
    print(f"  {n} leader traits from {len(files)} files", file=progress)
    w.commit()
