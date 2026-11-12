"""Extraction: the galaxy setup's defaults that depend on the galaxy size — each
`map/setup_scenarios/<size>.txt`'s empire counts, wormholes, gateways, hyperlanes, planet odds and
crisis strength, keyed by its `name` (the save's `galaxy.template`). The size-independent defaults
are defines (`game_define`, `*_DEFAULT`). For the empire dashboard's galaxy tab, which marks the
settings a game changed."""

import json
from pathlib import Path
import sys
from .clausewitz import parse_file
from .writer import Writer

# scenario key → the save's `galaxy` key it is the default of
SCENARIO_DEFAULTS = {
    'num_empire_default': 'num_empires',
    'fallen_empire_default': 'num_fallen_empires',
    'marauder_empire_default': 'num_marauder_empires',
    'nomad_empire_default': 'num_nomad_empires',
    'advanced_empire_default': 'num_advanced_empires',
    'colonizable_planet_odds': 'habitability',
    'primitive_odds': 'primitive',
    'crisis_strength': 'crises',
    'num_wormhole_pairs_default': 'num_wormhole_pairs',
    'num_gateways_default': 'num_gateways',
    'num_hyperlanes_default': 'num_hyperlanes',
}


def scenario_defaults(data: dict) -> dict[str, float]:
    """A `setup_scenario = { … }` block → {save key: default}, numbers only."""
    out: dict[str, float] = {}
    for k, save_key in SCENARIO_DEFAULTS.items():
        try:
            out[save_key] = float(str(data.get(k)))
        except (TypeError, ValueError):
            pass
    return out


def extract_setup_scenarios(install_dir: Path, w: Writer, progress=sys.stderr) -> None:
    folder = install_dir / 'map' / 'setup_scenarios'
    n = 0
    for f in sorted(folder.glob('*.txt')) if folder.is_dir() else []:
        sc = parse_file(f).get('setup_scenario')
        for data in sc if isinstance(sc, list) else [sc]:
            if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                continue
            defaults = scenario_defaults(data)
            w.execute("INSERT OR REPLACE INTO setup_scenario (key, num_stars, defaults_json) VALUES (?, ?, ?)",
                      (data['name'], float(str(data.get('num_stars', 0) or 0)), json.dumps(defaults)))
            n += 1
    print(f"  {n} setup scenarios", file=progress)
    w.commit()
