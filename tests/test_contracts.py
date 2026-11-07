#!/usr/bin/env python3
"""Unit tests for Nomads contracts: categories, top-level inline scripts and their times."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.contracts import contract_categories, days, with_inline


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


# Shaped like common/missions/mission_categories/00_mission_categories.txt (4.5.x).
CATEGORIES = """
mission = { is_contract = no map_icon = GFX_nomad_contract_icon }
resource_contract = { is_contract = yes show_in_issue_list = yes }
science_contract_internal = { is_contract = yes }
"""

# Shaped like contracts/HarvestResource (common/inline_scripts/contracts).
HARVEST = """
category = resource_contract
event_chain = contract_harvest_$RESOURCE$_chain
time_to_accept = @contract_time_base
inline_script = { script = nested/thing }
"""


class TestContracts(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(contract_categories(parse(CATEGORIES)),
                         {'resource_contract', 'science_contract_internal'})

    def test_inline_merged_and_substituted(self):
        block = parse("""c = { inline_script = { script = contracts/HarvestResource RESOURCE = minerals }
                               possible_operator = { always = yes } }""")['c']
        m = with_inline(block, lambda name: HARVEST if name == 'contracts/HarvestResource' else None)
        self.assertEqual(m['category'], 'resource_contract')
        self.assertEqual(m['event_chain'], 'contract_harvest_minerals_chain')
        self.assertIn('possible_operator', m)

    def test_own_keys_win(self):
        block = parse("c = { inline_script = { script = x } category = science_contract_internal }")['c']
        self.assertEqual(with_inline(block, lambda _: HARVEST)['category'], 'science_contract_internal')

    def test_bare_script_name_and_missing_script(self):
        self.assertEqual(with_inline(parse("c = { inline_script = x }")['c'], lambda _: HARVEST)['category'],
                         'resource_contract')
        self.assertEqual(with_inline(parse("c = { inline_script = gone }")['c'], lambda _: None), {})

    def test_days(self):
        v = {'@contract_time_base': 1800.0}
        self.assertEqual(days('@contract_time_base', v), 1800)
        self.assertEqual(days('720', v), 720)
        self.assertIsNone(days(None, v))
        self.assertIsNone(days('@unknown', v))


if __name__ == '__main__':
    unittest.main()
