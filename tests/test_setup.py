#!/usr/bin/env python3
"""Unit tests for the galaxy setup scenarios' defaults."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.setup import scenario_defaults


class TestScenarioDefaults(unittest.TestCase):
    def test_maps_scenario_keys_to_the_saves(self):
        # map/setup_scenarios/medium.txt (4.5.4), abridged
        d = Parser(tokenize('s = { name = "medium" num_empire_default = 9 nomad_empire_default = 2 '
                            'colonizable_planet_odds = 1.0 num_hyperlanes_default = 1 '
                            'num_empires = { min = 0 max = 18 } }')).parse()['s']
        self.assertEqual(scenario_defaults(d), {'num_empires': 9.0, 'num_nomad_empires': 2.0,
                                                'habitability': 1.0, 'num_hyperlanes': 1.0})


if __name__ == '__main__':
    unittest.main()
