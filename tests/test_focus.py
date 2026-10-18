#!/usr/bin/env python3
"""Unit tests for the empire focus helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.focus import requirement_keys, research_options, with_base
from extractor.localisation import inline_refs

# Shaped like development_rewards_3 (common/country_focus/focus_rewards/00_development_focus_rewards.txt).
REWARD = """
on_success = {
	if = {
		limit = { is_diplomatic = no }
		add_research_option = tech_colonial_centralization
	}
	else = {
		add_research_option = tech_federation_code
	}
	create_focus_reward_message = { CATEGORY = DEVELOPMENT }
}
"""


class TestFocus(unittest.TestCase):
    def test_research_options_from_every_branch(self):
        block = Parser(tokenize(REWARD)).parse()['on_success']
        self.assertEqual(research_options(block),
                         ['tech_colonial_centralization', 'tech_federation_code'])

    def test_requirement_keys(self):
        block = Parser(tokenize(
            'condition_for_achievement = { custom_tooltip = { fail_text = "a.fail" '
            'success_text = "a.success" has_country_flag = x } }')).parse()
        self.assertEqual(requirement_keys(block), ['a.success'])

    def test_cards_inherit_from_their_base(self):
        cards = with_base({'c_100': {'category': 'conquest', 'tier': '1'},
                           'c_105': {'base': 'c_100', 'name': 'x'}})
        self.assertEqual(cards['c_105']['category'], 'conquest')

    def test_inline_refs(self):
        self.assertEqual(inline_refs('ok $c.req$', {'c.req': 'Has hired a Commander'}),
                         'ok Has hired a Commander')


if __name__ == '__main__':
    unittest.main()
