"""Extraction: species rights (common/species_rights/<kind>/*.txt), for the empire dashboard."""

from pathlib import Path
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .traditions import dumps
from .triggers import _scripted_trigger_index, dlc_trigger_map, normalize_trigger
from .values import _as_blocks
from .writer import Writer

# The save's field per kind of right (country.modules.standard_species_rights_module.primary),
# in the game's order on the species rights panel.
KINDS = (
    ('citizenship_types', 'citizenship'),
    ('subspecies_integration_types', 'subspecies_integration'),
    ('living_standards', 'living_standard'),
    ('military_service_types', 'military_service'),
    ('colonization_controls', 'colonization_control'),
    ('population_controls', 'population_control'),
    ('migration_controls', 'migration_control'),
    ('slavery_types', 'slavery'),
    ('purge_types', 'purge'),
)


def unwrap_from(block):
    """A right's trigger is in the species' scope with the country as `from`: `from = { … }` is
    folded in (an AND), `exists = from` dropped (always true), so the country's conditions are read
    like the species' own. Recursive into logic blocks."""
    if isinstance(block, list):
        return [unwrap_from(b) for b in block]
    if not isinstance(block, dict):
        return block
    out: dict = {}
    for k, v in block.items():
        if k == 'exists' and v == 'from':
            continue
        if k == 'from' and isinstance(v, (dict, list)):
            extra = unwrap_from(v)
            prev = out.get('AND')
            out['AND'] = [*(prev if isinstance(prev, list) else [prev] if prev else []),
                          *(extra if isinstance(extra, list) else [extra])]
            continue
        out[k] = unwrap_from(v) if k in ('AND', 'OR', 'NOT', 'NOR', 'NAND', 'if', 'else_if', 'else') else v
    return out


def right_modifiers(d: dict, variables: dict[str, float]) -> list[tuple[str, float]]:
    """A right's unconditional `pop_group_modifier` entries, summed over its blocks. The
    `triggered_pop_group_modifier` ones (Social Welfare's happiness unless consumer goods run
    short) are left out: the right's tooltip text names them with their condition."""
    out: dict[str, float] = {}
    for block in _as_blocks(d.get('pop_group_modifier')):
        for k, v in _block_modifier_values(block, variables):
            out[k] = out.get(k, 0) + v
    return list(out.items())


def extract_species_rights(install_dir: Path, loc: dict[str, str], variables: dict[str, float],
                           w: Writer, progress=sys.stderr) -> None:
    base = install_dir / 'common' / 'species_rights'
    dlc = dlc_trigger_map(_scripted_trigger_index(install_dir))
    text = lambda k, **kw: _clean_loc(loc[k], loc, **kw) if loc.get(k) else None
    n = 0
    for kind_order, (folder, field) in enumerate(KINDS):
        for f in sorted((base / folder).glob('*.txt')) if (base / folder).is_dir() else []:
            for key, d in parse_file(f).items():
                if key.startswith('@') or not isinstance(d, dict):
                    continue
                w.execute(
                    """INSERT OR REPLACE INTO species_right (key, kind, kind_order, name, description,
                       is_slavery, is_purge, potential_json, allow_json, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, field, kind_order, text(key),
                     text(f'{key}_tooltip', keep_newlines=True),
                     int(d.get('is_slavery') == 'yes'), int(d.get('is_purge') == 'yes'),
                     dumps(normalize_trigger(unwrap_from(d.get('potential')), dlc)),
                     dumps(normalize_trigger(unwrap_from(d.get('allow')), dlc)), n))
                n += 1
                for m_order, (mk, mv) in enumerate(right_modifiers(d, variables)):
                    w.execute(
                        """INSERT OR REPLACE INTO species_right_modifier (right_key, modifier, value,
                           sort_order) VALUES (?, ?, ?, ?)""", (key, mk, mv, m_order))
                    _write_modifier_label(w, loc, mk)
    print(f"  {n} species rights", file=progress)
    w.commit()
