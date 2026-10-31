#!/usr/bin/env python3
"""Unit tests for leader trait modifiers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.leaders import owner_conditions, trait_modifiers
from extractor.triggers import normalize_trigger
from extractor.politics import expand_inline

# Shaped like leader_trait_spark_of_genius (common/traits/00_starting_ruler_traits.txt).
SPARK = """
leader_trait_spark_of_genius = {
	councilor_modifier = { all_technology_research_speed = 0.03 }
	self_modifier = { species_leader_exp_gain = @x }
	triggered_councilor_modifier = { potential = { always = yes } physics_research_speed = 0.1 }
}
"""


class TestLeaderTraits(unittest.TestCase):
    def test_modifiers_by_scope_triggered_ones_with_their_condition(self):
        t = Parser(tokenize(SPARK)).parse()['leader_trait_spark_of_genius']
        self.assertEqual(trait_modifiers(t, {'@x': 0.25}),
                         [('self', 'species_leader_exp_gain', 0.25, None),
                          ('councilor', 'all_technology_research_speed', 0.03, None),
                          ('councilor', 'physics_research_speed', 0.1, {'always': 'yes'})])

    def test_righteous_by_empire_type(self):
        # 4.5.3 common/traits/00_governor_traits.txt: crime only in triggered blocks
        t = Parser(tokenize('''t = {
            triggered_planet_modifier = { potential = { trait_is_gestalt_check = no } planet_crime_add = -25 }
            triggered_planet_modifier = { potential = { trait_is_gestalt_check = yes } planet_crime_no_happiness_add = -25 }
            background_planet_modifier = { planet_stability_add = 1 }
        }''')).parse()['t']
        self.assertEqual(trait_modifiers(t, {}),
                         [('planet', 'planet_crime_add', -25.0, {'trait_is_gestalt_check': 'no'}),
                          ('planet', 'planet_crime_no_happiness_add', -25.0, {'trait_is_gestalt_check': 'yes'}),
                          ('background_planet', 'planet_stability_add', 1.0, None)])

    def test_inline_script_scaled_by_its_tier(self):
        # 4.5.3 inline_scripts/traits/technocracy_expertise_effects.txt, an Expertise trait of tier 2
        script = '''triggered_planet_modifier = {
            potential = { exists = owner owner = { has_valid_civic = civic_technocracy } }
            planet_jobs_$FIELD$_research_produces_mult = 0.10
            mult = $TIER$
        }'''
        inlined = (expand_inline(script, {'FIELD': 'physics', 'TIER': '2'}),)
        [(scope, key, value, potential)] = trait_modifiers({}, {}, inlined)
        self.assertEqual((scope, key, round(value, 6)), ('planet', 'planet_jobs_physics_research_produces_mult', 0.2))
        self.assertEqual(potential['owner'], {'has_valid_civic': 'civic_technocracy'})

    def test_owner_is_the_leaders_empire(self):
        block = Parser(tokenize('x = { exists = owner owner = { has_valid_civic = civic_technocracy } }')).parse()['x']
        self.assertEqual(normalize_trigger(block, None, None, owner_conditions),
                         {'op': 'and', 'args': [{'leaf': 'exists', 'value': 'owner'},
                                                {'leaf': 'has_valid_civic', 'value': 'civic_technocracy'}]})

    def test_delegate_scopes(self):
        t = Parser(tokenize('t = { galcom_modifier = { diplo_weight_mult = 0.1 } '
                            'federation_modifier = { federation_cohesion_add = 1 } }')).parse()['t']
        self.assertEqual(trait_modifiers(t, {}), [('galcom', 'diplo_weight_mult', 0.1, None),
                                                  ('federation', 'federation_cohesion_add', 1.0, None)])


if __name__ == '__main__':
    unittest.main()
