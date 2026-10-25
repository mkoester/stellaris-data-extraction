#!/usr/bin/env python3
"""Unit tests for the timers of player actions."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.cooldowns import action_timers, gates, timers

# Shaped like artifact_arcane_deciphering (common/artifact_actions/artifact_actions.txt, 4.5.2).
ARTIFACT = """
allow = {
	custom_tooltip = {
		fail_text = "requires_arcane_deciphering_no_cooldown"
		NOT = { has_modifier = arcane_deciphering_cooldown }
	}
}
effect = {
	hidden_effect = {
		if = { limit = { NOT = { has_country_flag = arcana_achievement } } set_country_flag = arcana_achievement }
		add_modifier = { modifier = "arcane_deciphering_cooldown" days = 720 }
		set_timed_country_flag = { flag = deciphered_recently years = 2 }
		add_modifier = { modifier = permanent_one }
	}
}
"""


def parse(text):
    return Parser(tokenize(text)).parse()


class TestTimers(unittest.TestCase):
    def test_gates_are_negated_checks_only(self):
        self.assertEqual(gates(parse('allow = { has_modifier = a NOT = { has_planet_flag = b '
                                     'NOT = { has_modifier = c } } }')['allow']),
                         {('flag', 'b')})

    def test_timers_with_their_days(self):
        self.assertEqual(timers(parse(ARTIFACT)['effect']),
                         {('modifier', 'arcane_deciphering_cooldown'): 720,
                          ('flag', 'deciphered_recently'): 720})

    def test_cooldown_and_effect_roles(self):
        d = parse(ARTIFACT)
        self.assertEqual(action_timers([d['allow']], [d['effect']]), [
            ('flag', 'deciphered_recently', 'effect', 720),
            ('modifier', 'arcane_deciphering_cooldown', 'cooldown', 720)])


if __name__ == '__main__':
    unittest.main()
