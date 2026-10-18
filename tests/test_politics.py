#!/usr/bin/env python3
"""Unit tests for the policy and faction-demand helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.politics import expand_inline, owner_scope

# Shaped like common/inline_scripts/pop_faction_types/demand_faction_pleased.txt.
PLEASED = """
demand = {
	title = "FACTION_PLEASED_GC"
	fulfilled_effect = -20
	trigger = { owner = { has_country_flag = $FLAG$ } }
}
"""


class TestPolitics(unittest.TestCase):
    def test_inline_script_params_are_filled_in(self):
        d = expand_inline(PLEASED, {'FLAG': 'galcom_resolution_passed_pro_imperialist'})['demand']
        self.assertEqual(d['trigger']['owner']['has_country_flag'],
                         'galcom_resolution_passed_pro_imperialist')

    def test_owner_scope_unwraps_the_owner_block(self):
        block = Parser(tokenize(
            'exists = owner owner = { has_policy_flag = unrestricted_wars }')).parse()
        self.assertEqual(owner_scope(block), {'has_policy_flag': 'unrestricted_wars'})

    def test_owner_scope_drops_a_lone_exists(self):
        self.assertIsNone(owner_scope({'exists': 'owner'}))

    def test_owner_scope_keeps_other_triggers(self):
        block = {'owner': {'is_at_war': 'yes'}, 'num_pops': '5'}
        self.assertIs(owner_scope(block), block)


if __name__ == '__main__':
    unittest.main()
