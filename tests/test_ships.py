#!/usr/bin/env python3
"""Unit tests for the pure ship-design and starbase-part helpers."""
import json
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.ships import (component_row, file_variables, modifier_dict, part_modifier_rows,
                             resources_json, section_row, size_row, weapon_csv)


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


class TestShipSize(unittest.TestCase):
    # corvette (common/ship_sizes/00_ship_sizes.txt, 4.5.x), shortened
    def test_corvette(self):
        d = parse('''@corvette_hp = 200
            corvette = { max_speed = 160 max_hitpoints = @corvette_hp
              modifier = { ship_evasion_add = 60 } size_multiplier = 1
              section_slots = { "mid" = { locator = "part1" } } is_space_station = no
              class = shipclass_military
              required_component_set = "power_core" required_component_set = "ftl_components" }''')
        v = file_variables(d, {})
        self.assertEqual(size_row('corvette', d['corvette'], v), (
            'shipclass_military', 200.0, 160.0, 1.0, 0,
            '["mid"]', '["power_core", "ftl_components"]', '{"ship_evasion_add": 60.0}'))


class TestSection(unittest.TestCase):
    def test_weapon_and_utility_slots(self):
        b = parse('''s = { key = "CORVETTE_MID_S2PD1" ship_size = corvette fits_on_slot = mid
            component_slot = { name = "SMALL_GUN_01" template = "point_defence_turret" }
            component_slot = { name = "SMALL_GUN_02" template = "small_turret" }
            small_utility_slots = 3 aux_utility_slots = 1
            resources = { category = ship_sections cost = { alloys = @section_cost } } }''')['s']
        size, slot, weapons, utility, mods = section_row(b, {})
        self.assertEqual((size, slot, mods), ('corvette', 'mid', None))
        self.assertEqual(json.loads(weapons), [
            {'name': 'SMALL_GUN_01', 'template': 'point_defence_turret'},
            {'name': 'SMALL_GUN_02', 'template': 'small_turret'}])
        self.assertEqual(json.loads(utility), {'small': 3, 'aux': 1})
        self.assertEqual(json.loads(resources_json(b, {'@section_cost': 40.0})),
                         {'cost': {'alloys': 40.0}})

    def test_single_slot_is_a_list_too(self):
        b = parse('s = { component_slot = { name = "A" template = "large_turret" } }')['s']
        self.assertEqual(json.loads(section_row(b, {})[2]),
                         [{'name': 'A', 'template': 'large_turret'}])


class TestComponents(unittest.TestCase):
    CSV = ('# comment;;;\n'
           'key;cost;power;min_damage;max_damage;cooldown;range;end\n'
           '# Kinetic;;;;;;;\n'
           'SMALL_MASS_DRIVER_1;10.00;-5.00;5.00;16.00;20.00;50.00;\n')

    def test_weapon_csv(self):
        self.assertEqual(weapon_csv(self.CSV), {'SMALL_MASS_DRIVER_1': {
            'cost': 10.0, 'power': -5.0, 'min_damage': 5.0, 'max_damage': 16.0,
            'cooldown': 20.0, 'range': 50.0}})

    def test_weapon_takes_power_from_the_csv(self):
        b = parse('''w = { key = "SMALL_MASS_DRIVER_1" size = small type = instant
            component_set = "MASS_DRIVER_1" tags = { weapon_type_kinetic s_slot } }''')['w']
        row = component_row('weapon', b, {}, weapon_csv(self.CSV))
        self.assertEqual(row[:5], ('weapon', 'small', 'MASS_DRIVER_1', 'instant', -5.0))
        self.assertEqual(json.loads(row[5]), ['weapon_type_kinetic', 's_slot'])
        self.assertEqual(json.loads(row[7])['max_damage'], 16.0)

    def test_armor_modifier(self):
        b = parse('a = { key = "SMALL_ARMOR_1" size = small power = 0 '
                  'modifier = { ship_armor_add = @armor_S1 } }')['a']
        row = component_row('utility', b, {'@armor_S1': 100.0}, {})
        self.assertEqual(row[4], 0.0)
        self.assertEqual(json.loads(row[6]), {'ship_armor_add': 100.0})
        self.assertIsNone(row[7])

    def test_modifier_blocks_sum(self):
        self.assertEqual(modifier_dict([{'ship_speed_mult': '0.1'}, {'ship_speed_mult': '0.05'}], {}),
                         {'ship_speed_mult': 0.15000000000000002})


class TestStarbasePart(unittest.TestCase):
    # transit_hub (common/starbase_buildings/00_starbase_buildings.txt, 4.5.3), the user's example
    def test_transit_hub_system_modifier(self):
        b = parse('''transit_hub = { category = sm_colony_cat
            system_modifier = { planet_resettlement_unemployed_mult = 2.00 } }''')['transit_hub']
        self.assertEqual(part_modifier_rows(b, {}, lambda t: None),
                         [('system', 'planet_resettlement_unemployed_mult', 2.0, None)])

    def test_triggered_rows_carry_their_potential(self):
        b = parse('''x = {
            station_modifier = { ship_sensor_range_add = 1 }
            triggered_system_modifier = { potential = { owner = { has_ascension_perk = ap_a } }
                station_gatherers_produces_mult = 1.5 }
            triggered_system_modifier = { potential = { always = yes }
                station_gatherers_produces_mult = 1.0 mult = value:foo } }''')['x']
        rows = part_modifier_rows(b, {}, lambda t: {'leaf': 'p'} if t else None)
        self.assertEqual(rows, [
            ('station', 'ship_sensor_range_add', 1.0, None),
            ('system', 'station_gatherers_produces_mult', 1.5, '{"leaf": "p"}'),
            ('system', 'station_gatherers_produces_mult', 1.0, '{"leaf": "p"}')])


if __name__ == '__main__':
    unittest.main()
