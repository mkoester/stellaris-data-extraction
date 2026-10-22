#!/usr/bin/env python3
"""Unit tests for which civics/origins a building requires."""
import unittest

from extractor.buildings import _building_resources, _write_build_time, building_unlocks

TRIGGERS = {'is_toxic_baths_empire': {'OR': {'has_valid_civic': ['civic_toxic_baths',
                                                                 'civic_hive_toxic_baths']}}}


class TestBuildingUnlocks(unittest.TestCase):
    def test_plain_civic_gate(self):
        b = {'potential': {'owner': {'has_valid_civic': 'civic_environmentalist'}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), ['civic_environmentalist'])

    def test_or_of_civic_variants_via_scripted_trigger(self):
        b = {'potential': {'owner': {'is_toxic_baths_empire': 'yes'}}}
        self.assertEqual(building_unlocks(b, TRIGGERS),
                         ['civic_hive_toxic_baths', 'civic_toxic_baths'])

    def test_optional_path_is_not_an_unlock(self):
        # Shaped like building_factory_1: Fear of the Dark is one alternative.
        b = {'potential': {'owner': {'OR': {'country_uses_consumer_goods': 'yes',
                                            'has_origin': 'origin_fear_of_the_dark'}}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), [])

    def test_negated_civic_is_not_an_unlock(self):
        b = {'potential': {'owner': {'NOT': {'has_valid_civic': 'civic_x'}}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), [])


# 4.5.2 common/inline_scripts/buildings/nomadic_cost_switcher.txt
SWITCHER = """cost = {
	trigger = { from? = { is_nomadic = no } }
	$REGULAR_RESOURCE$ = $COST$
}
cost = {
	trigger = { from? = { is_nomadic = yes } }
	$NOMADIC_RESOURCE$ = $COST$
	mult = $NOMADIC_COST_MULT$
}"""


class TestBuildingResources(unittest.TestCase):
    def test_cost_from_the_inline_script_the_regular_empires(self):
        # Shaped like building_research_lab_1.
        b = {'resources': {'category': 'planet_buildings',
                           'inline_script': {'script': 'buildings/nomadic_cost_switcher',
                                             'REGULAR_RESOURCE': 'minerals',
                                             'NOMADIC_RESOURCE': 'sr_zro', 'COST': '@b1_minerals',
                                             'NOMADIC_COST_MULT': '0.1'},
                           'upkeep': {'energy': '@b1_upkeep'}}}
        load = {'buildings/nomadic_cost_switcher': SWITCHER}.get
        self.assertEqual(_building_resources(b, {'@b1_minerals': 400.0, '@b1_upkeep': 2.0}, load),
                         [('cost', 'minerals', 400.0), ('upkeep', 'energy', 2.0)])

    def test_gated_costs_take_the_first(self):
        # Shaped like mining_station: minerals unless bio ships, then food.
        s = {'resources': {'cost': [
            {'trigger': {'from': {'country_uses_bio_ships': 'no'}}, 'minerals': '@civilian_station_cost'},
            {'trigger': {'from': {'country_uses_bio_ships': 'yes'}}, 'food': '@civilian_station_cost'}],
            'upkeep': {'energy': '1'}}}
        self.assertEqual(_building_resources(s, {'@civilian_station_cost': 100.0}),
                         [('cost', 'minerals', 100.0), ('upkeep', 'energy', 1.0)])



class _Writes:
    def __init__(self):
        self.rows = []

    def execute(self, _sql, params):
        self.rows.append(params)


class TestBuildTime(unittest.TestCase):
    def test_resolves_the_variable_and_skips_a_definition_without_one(self):
        w = _Writes()
        _write_build_time(w, 'building_research_lab_1', {'base_buildtime': '@b1_time'}, {'@b1_time': 360.0})
        _write_build_time(w, 'mining_station', {'class': 'shipclass_mining_station'}, {})
        self.assertEqual(w.rows, [('building_research_lab_1', 360.0)])


if __name__ == '__main__':
    unittest.main()
