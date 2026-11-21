"""Extraction: the Grand Archive's specimens (common/specimens) — category, rarity, whether they
can be traded or sold, and what they give while exhibited: country modifiers (each block's
`potential` as a condition, as for relic passives) and resources produced. For the empire
dashboard, which reads a country's exhibits from saves by specimen key."""

from pathlib import Path
import sys
from .clausewitz import parse_file
from .modifiers import _write_modifier_label
from .precursors import _relic_condition, _relic_passive_rows
from .traditions import _clean_loc
from .values import _as_blocks
from .writer import Writer

RARITY_SCRIPT = 'grand_archive/set_specimen_rarity'


# ── Pure helpers ───────────────────────────────────────────────────────────────

def rarity(data: dict) -> str | None:
    """The RARITY a specimen passes to `set_specimen_rarity` (common, rare, epic, exceptional)."""
    for block in _as_blocks(data.get('inline_script')):
        if block.get('script') == RARITY_SCRIPT and isinstance(block.get('RARITY'), str):
            return block['RARITY']
    return None


def flag(data: dict, key: str) -> int:
    """A yes/no field that defaults to yes (`is_tradable`, `is_sellable`) → 1/0."""
    return 0 if data.get(key) == 'no' else 1


def produced(data: dict) -> list[tuple[str, float]]:
    """[(resource, amount)] from `resources = { produces = { … } }`."""
    out: list[tuple[str, float]] = []
    for res in _as_blocks(data.get('resources')):
        for prod in _as_blocks(res.get('produces')):
            for k, v in prod.items():
                try:
                    out.append((k, float(str(v))))
                except ValueError:
                    continue
    return out


def modifier_rows(data: dict) -> list[tuple[str, float, str | None]]:
    """(modifier_key, value, condition) per triggered_country_modifier entry; the `mult`
    (the category's value:*_specimens_modifiers_mult) is the dashboard's to apply."""
    return [r for r in _relic_passive_rows(data) if r[0] != 'mult']


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_specimens(install_dir: Path, loc: dict[str, str], w: Writer,
                      progress=sys.stderr) -> None:
    folder = install_dir / 'common' / 'specimens'
    specimens: dict = {}
    for f in sorted(folder.glob('*.txt')) if folder.is_dir() else []:
        specimens.update({k: v for k, v in parse_file(f).items() if isinstance(v, dict)})
    n_mods = 0
    for order, (key, data) in enumerate(specimens.items()):
        raw = loc.get(key)
        w.execute(
            'INSERT INTO specimen (key, name, category, rarity, tradable, sellable, icon, '
            'sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (key, _clean_loc(raw, loc) if raw else None,
             data.get('type') if isinstance(data.get('type'), str) else None, rarity(data),
             flag(data, 'is_tradable'), flag(data, 'is_sellable'),
             data.get('icon') if isinstance(data.get('icon'), str) else None, order))
        for m_order, (mod_key, value, condition) in enumerate(modifier_rows(data)):
            w.execute(
                'INSERT INTO specimen_modifier (specimen_key, modifier_key, modifier_value, '
                'condition, sort_order) VALUES (?, ?, ?, ?, ?)',
                (key, mod_key, value, condition, m_order))
            _write_modifier_label(w, loc, mod_key)
            n_mods += 1
        for r_order, (resource, amount) in enumerate(produced(data)):
            w.execute(
                'INSERT INTO specimen_resource (specimen_key, resource, amount, sort_order) '
                'VALUES (?, ?, ?, ?)',
                (key, resource, amount, r_order))
    print(f"  {len(specimens)} specimens, {n_mods} modifiers", file=progress)
