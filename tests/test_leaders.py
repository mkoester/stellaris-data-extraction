#!/usr/bin/env python3
"""Unit tests for leader trait modifiers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.leaders import trait_modifiers

# Shaped like leader_trait_spark_of_genius (common/traits/00_starting_ruler_traits.txt).
SPARK = """
leader_trait_spark_of_genius = {
	councilor_modifier = { all_technology_research_speed = 0.03 }
	self_modifier = { species_leader_exp_gain = @x }
	triggered_councilor_modifier = { potential = { always = yes } physics_research_speed = 0.1 }
}
"""


class TestLeaderTraits(unittest.TestCase):
    def test_untriggered_modifiers_by_scope(self):
        t = Parser(tokenize(SPARK)).parse()['leader_trait_spark_of_genius']
        self.assertEqual(trait_modifiers(t, {'@x': 0.25}),
                         [('self', 'species_leader_exp_gain', 0.25),
                          ('councilor', 'all_technology_research_speed', 0.03)])

    def test_delegate_scopes(self):
        t = Parser(tokenize('t = { galcom_modifier = { diplo_weight_mult = 0.1 } '
                            'federation_modifier = { federation_cohesion_add = 1 } }')).parse()['t']
        self.assertEqual(trait_modifiers(t, {}), [('galcom', 'diplo_weight_mult', 0.1),
                                                  ('federation', 'federation_cohesion_add', 1.0)])


if __name__ == '__main__':
    unittest.main()
