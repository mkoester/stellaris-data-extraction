#!/usr/bin/env python3
"""Unit tests for the pure ambition helpers."""
import unittest

from extractor.ambitions import allow_tooltips, crisis_paths_of_perks, reward_steps
from extractor.clausewitz import Parser, tokenize
from extractor.game_start import parse_ordered


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


def reward(text: str):
    return next(v for k, _, v in parse_ordered(text) if k == 'reward')


class TestRewardSteps(unittest.TestCase):
    def test_steps_keep_file_order(self):
        r = reward('reward = { base = 700 complex_trigger_modifier = { trigger = count_playable_country '
                   'mode = subtract mult = 25 } round_to = 50 min = 100 }')
        self.assertEqual(reward_steps(r), [
            {'op': 'base', 'value': 700.0},
            {'op': 'subtract', 'by': 'count_playable_country', 'mult': 25.0},
            {'op': 'round_to', 'value': 50.0},
            {'op': 'min', 'value': 100.0}])

    def test_trigger_division_galaxy_setup_and_round(self):
        r = reward('reward = { base = 3000 divide = trigger:num_galaxy_systems '
                   'complex_trigger_modifier = { trigger = check_galaxy_setup_value '
                   'parameters = { setting = habitable_worlds_scale } mode = divide } round = yes }')
        self.assertEqual(reward_steps(r), [
            {'op': 'base', 'value': 3000.0},
            {'op': 'divide', 'by': 'num_galaxy_systems'},
            {'op': 'divide', 'by': 'galaxy_setup:habitable_worlds_scale'},
            {'op': 'round'}])

    def test_variables_resolve_and_game_values_stay_named(self):
        self.assertEqual(reward_steps(reward('reward = { base = @x }'), {'@x': 10.0}),
                         [{'op': 'base', 'value': 10.0}])
        self.assertEqual(reward_steps(reward('reward = { base = 15 mult = root.v }')),
                         [{'op': 'base', 'value': 15.0}, {'op': 'multiply', 'var': 'root.v'}])

    def test_a_bare_reward(self):
        self.assertEqual(reward_steps('5'), [{'op': 'base', 'value': 5.0}])


class TestLevelsAndPaths(unittest.TestCase):
    def test_allow_tooltips_skip_hidden_triggers(self):
        allow = parse('a = { custom_tooltip = { success_text = t1 fail_text = f1 has_country_flag = x } '
                      'hidden_trigger = { custom_tooltip = { success_text = hidden } } '
                      'custom_tooltip = { fail_text = f2 is_nomadic = no } }')['a']
        self.assertEqual(allow_tooltips(allow), ['t1', 'f2'])
        self.assertEqual(allow_tooltips(None), [])

    def test_paths_found_anywhere_in_a_perk(self):
        perks = parse('ap_a = { on_enabled = { hidden_effect = { x = y } activate_crisis_progression = p1 } } '
                      'ap_b = { modifier = { m = 1 } }')
        self.assertEqual(crisis_paths_of_perks(perks), [('ap_a', 'p1')])


if __name__ == '__main__':
    unittest.main()
