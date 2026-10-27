#!/usr/bin/env python3
"""Unit tests for the game-start capital setup scripts."""
import unittest

from extractor.game_start import expand_inline_scripts, parse_ordered, setup_effects


def run(text: str, scripts: dict[str, str] | None = None, triggers: dict | None = None) -> list:
    return setup_effects(f"generate_start_buildings_and_districts = {{ {text} }}",
                         (scripts or {}).get, triggers or {})['generate_start_buildings_and_districts']


class TestParseOrdered(unittest.TestCase):
    def test_keeps_order_of_repeated_keys(self):
        self.assertEqual(parse_ordered('a = 1 b = { c = 2 } a = 3'),
                         [('a', '=', '1'), ('b', '=', [('c', '=', '2')]), ('a', '=', '3')])

    def test_limit_parses_as_trigger_dict(self):
        (_, _, body), = parse_ordered('if = { limit = { x = yes y = no } z = 1 }')
        self.assertEqual(body[0], ('limit', '=', {'x': 'yes', 'y': 'no'}))

    def test_inline_scripts_expand_in_place(self):
        scripts = {'s/a': 'x = 1 inline_script = "s/b"', 's/b': 'y = $P$'}.get
        got = expand_inline_scripts(parse_ordered('w = 0 inline_script = s/a '
                                                  'inline_script = { script = s/b P = 7 }'), scripts)
        self.assertEqual([k for k, _, _ in got], ['w', 'x', 'y', 'y'])
        self.assertEqual(got[-1][2], '7')


class TestSetupEffects(unittest.TestCase):
    def test_zone_before_its_building(self):
        got = run('add_building = building_capital '
                  'add_zone = { district = district_city zone = zone_industrial zone_slot = 2 } '
                  'add_building = { building = building_factory_1 zone = zone_industrial }')
        self.assertEqual(got, [
            {'e': 'add_building', 'building': 'building_capital'},
            {'e': 'add_zone', 'district': 'district_city', 'zone': 'zone_industrial', 'slot': 2},
            {'e': 'add_building', 'building': 'building_factory_1', 'zone': 'zone_industrial'}])

    def test_if_chain_with_scoped_and_scripted_limits(self):
        triggers = {'is_materialist': {'OR': {'has_ethic': ['ethic_materialist',
                                                            'ethic_fanatic_materialist']}}}
        got = run('if = { limit = { owner? = { is_materialist = yes } } add_building = a } '
                  'else_if = { limit = { is_planet_class = pc_ocean } add_building = b } '
                  'else = { add_building = c }', triggers=triggers)
        branches = got[0]['branches']
        self.assertEqual(branches[0]['limit'], {'op': 'or', 'args': [
            {'leaf': 'has_ethic', 'value': 'ethic_materialist'},
            {'leaf': 'has_ethic', 'value': 'ethic_fanatic_materialist'}]})
        self.assertEqual(branches[1]['limit'], {'leaf': 'is_planet_class', 'value': 'pc_ocean'})
        self.assertIsNone(branches[2]['limit'])
        self.assertEqual([b['body'][0]['building'] for b in branches], ['a', 'b', 'c'])

    def test_variables_and_while(self):
        got = run('set_variable = { which = farming_districts value = 3 } '
                  'while = { count = 2 change_variable = { which = farming_districts value = -1 } } '
                  'add_district_and_planet_size_if_needed_effect_amount = '
                  '{ amount = farming_districts district = district_farming }')
        self.assertEqual(got, [
            {'e': 'set_variable', 'which': 'farming_districts', 'value': 3},
            {'e': 'while', 'count': 2, 'limit': None,
             'body': [{'e': 'change_variable', 'which': 'farming_districts', 'value': -1}]},
            {'e': 'add_district', 'district': 'district_farming', 'amount': 'farming_districts'}])

    def test_check_variable_and_has_zone_are_leaves(self):
        got = run('if = { limit = { check_variable = { which = v value >= 1 } '
                  'has_zone = { zone = zone_x } } add_district = d }')
        self.assertEqual(got[0]['branches'][0]['limit'], {'op': 'and', 'args': [
            {'leaf': 'check_variable', 'which': 'v', 'cmp': '>=', 'value': '1'},
            {'leaf': 'has_zone', 'value': 'zone_x'}]})

    def test_drops_blocks_without_capital_effects_keeps_unknown(self):
        got = run('if = { limit = { a = yes } create_pop_group = { size = 1 } } mystery_effect = yes')
        self.assertEqual(got, [{'e': 'unknown', 'key': 'mystery_effect'}])


if __name__ == '__main__':
    unittest.main()
