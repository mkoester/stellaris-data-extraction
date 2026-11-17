#!/usr/bin/env python3
"""Unit tests for megastructure cost blocks: inline scripts, bio-ship gates, `mult`."""
import unittest

from extractor.megastructures import megastructure_resources

# 4.5.3 inline_scripts/megastructures/arkship.txt, its resources block
ARKSHIP = """
resources = {
	category = megastructures_arkships
	cost = { influence = @arkship_cost_influence_500 }
	cost = { trigger = { country_uses_bio_ships = no } alloys = @arkship_cost_alloys_10000 }
	cost = { trigger = { country_uses_bio_ships = yes } alloys = @arkship_cost_alloys_10000 mult = 0.5 }
	cost = { trigger = { country_uses_bio_ships = yes } food = @arkship_cost_alloys_10000 mult = @halved_alloy_to_food_cost_ratio }
}
build_time = 1800
"""
VARS = {'@arkship_cost_influence_500': 500.0, '@arkship_cost_alloys_10000': 10000.0,
        '@halved_alloy_to_food_cost_ratio': 1.75}


class TestMegastructureResources(unittest.TestCase):
    def test_arkship_cost_comes_from_its_inline_script_per_variant(self):
        mdef = {'inline_script': {'script': 'megastructures/arkship', 'SHIP_SIZE': 'civilian_arkship'}}
        load = lambda name: ARKSHIP if name == 'megastructures/arkship' else None
        rows, skipped = megastructure_resources(mdef, VARS, load)
        self.assertEqual(rows, [('cost', 'alloys', 'bio', 5000.0), ('cost', 'alloys', 'regular', 10000.0),
                                ('cost', 'food', 'bio', 17500.0), ('cost', 'influence', 'any', 500.0)])
        self.assertEqual(skipped, 0)

    def test_a_flag_discount_reads_as_absent_and_other_gates_are_skipped(self):
        mdef = {'resources': {'cost': [
            {'trigger': {'NOT': {'has_country_flag': 'flash_forge'}}, 'energy': '100'},
            {'trigger': {'is_subject': 'yes'}, 'energy': '-50'}],
            'upkeep': {'energy': '5'}}}
        rows, skipped = megastructure_resources(mdef, {})
        self.assertEqual(rows, [('cost', 'energy', 'any', 100.0), ('upkeep', 'energy', 'any', 5.0)])
        self.assertEqual(skipped, 1)


if __name__ == '__main__':
    unittest.main()
