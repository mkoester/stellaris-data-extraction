#!/usr/bin/env python3
"""Unit tests for which civics/origins a building requires."""
import unittest

from extractor.buildings import (_building_modifiers, _building_resources, _write_build_time,
                                 building_unlocks, conditional_modifiers, owned_requirements)

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



# Shaped like 4.5.3's common/inline_scripts/jobs/fe_automation.txt (beta 7793).
FE_AUTOMATION = """
triggered_planet_modifier = {
    potential = { owner? = { is_gestalt = no } }
    job_$JOB$_automated_workforce_mult = @fe_automation_$TIER$
}
triggered_planet_modifier = {
    potential = { owner? = { is_gestalt = yes } }
    job_$GESTALT_JOB$_automated_workforce_mult = @fe_automation_$TIER$
}
"""


class TestBuildingModifiers(unittest.TestCase):
    # Shaped like 4.5.3's building_affluence_center: its own modifier, automation inlined.
    B = {'planet_modifier': {'planet_amenities_add': '5'},
         'inline_script': [{'script': 'jobs/fe_automation', 'JOB': 'artisan',
                            'GESTALT_JOB': 'artisan_drone', 'TIER': 'specialist_2'},
                           {'script': 'buildings/on_all_capital_buildings'}]}
    VARS = {'@fe_automation_specialist_2': 0.5}

    def test_fe_automation_from_the_inline_script(self):
        load = {'jobs/fe_automation': FE_AUTOMATION,
                'buildings/on_all_capital_buildings': 'planet_modifier = { planet_housing_add = 9 }'}.get
        self.assertEqual(_building_modifiers(self.B, self.VARS, load),
                         [('planet', 'planet_amenities_add', 5.0, None, None),
                          ('planet', 'job_artisan_automated_workforce_mult', 0.5, None, None),
                          ('planet', 'job_artisan_drone_automated_workforce_mult', 0.5, None, None)])

    def test_without_a_loader_only_its_own(self):
        self.assertEqual(_building_modifiers(self.B, self.VARS),
                         [('planet', 'planet_amenities_add', 5.0, None, None)])


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



class TestConditionalModifiers(unittest.TestCase):
    # shaped like 4.5.2 building_private_mining_consortium
    B = {
        'triggered_planet_modifier': [
            {'potential': {'always': 'yes'}, 'planet_miners_trade_produces_add': '@med'},
            {'potential': {'branch_office_owner': {'has_valid_civic': 'civic_private_prospectors'}},
             'planet_miners_trade_produces_add': '@med'},
            {'potential': {'branch_office_owner': {'is_criminal_syndicate': 'yes'}},
             'planet_crime_floor_add': '10'},
        ],
        'triggered_country_modifier': {
            'potential': {'branch_office_owner': {'has_valid_civic': 'civic_private_prospectors'}},
            'job_capital_trader_add': '200'},
    }

    def test_each_block_with_its_gate(self):
        self.assertEqual(conditional_modifiers(self.B, {'@med': 0.5}), [
            ('planet', 'planet_miners_trade_produces_add', 0.5, None, None),
            ('planet', 'planet_miners_trade_produces_add', 0.5, 'civic_private_prospectors', None),
            ('planet', 'planet_crime_floor_add', 10.0, None,
             'branch_office_owner = is_criminal_syndicate = yes'),
            ('country', 'job_capital_trader_add', 200.0, 'civic_private_prospectors', None),
        ])

    def test_plain_modifier_blocks_are_ungated(self):
        b = {'country_modifier': {'all_technology_research_speed': '0.03'}}
        self.assertEqual(conditional_modifiers(b, {}),
                         [('country', 'all_technology_research_speed', 0.03, None, None)])



class TestOwnedRequirements(unittest.TestCase):
    def test_owner_host_allow_and_techs_apart(self):
        # shaped like 4.5.2 building_corporate_embassy, with building_private_mining_consortium's
        # host conditions
        b = {'prerequisites': ['"tech_xeno_diplomacy"'],
             'potential': {'has_branch_office': 'yes',
                           'branch_office_owner': {'is_legit_or_legal_criminal': 'yes'},
                           'is_artificial': 'no'},
             'allow': {'has_major_upgraded_capital': 'yes'}}
        self.assertEqual(owned_requirements(b, {}, {}), (
            '{"leaf":"is_legit_or_legal_criminal","value":"yes"}',
            '{"leaf":"is_artificial","value":"no"}',
            '{"leaf":"has_major_upgraded_capital","value":"yes"}',
            '["tech_xeno_diplomacy"]'))

    def test_nothing_required(self):
        b = {'potential': {'has_branch_office': 'yes'}, 'allow': {'always': 'yes'},
             'prerequisites': []}
        self.assertEqual(owned_requirements(b, {}, {}), (None, None, None, None))


if __name__ == '__main__':
    unittest.main()
