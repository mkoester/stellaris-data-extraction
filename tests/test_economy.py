#!/usr/bin/env python3
"""Unit tests for the pure economy helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.economy import attitude_trades, base_value, opinion_row, resource_row, slot_count


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


class TestEconomyHelpers(unittest.TestCase):
    def test_resource_leaves_conditional_factors_out(self):
        r = parse('''consumer_goods = { tradable = yes category = advanced market_amount = 50
            market_price = 100 max = 15000
            ai_weight = { weight = 5 modifier = { factor = 0.1 country_uses_consumer_goods = no } }
            ai_wants = { base = 500 modifier = { factor = 0.1 } } }''')['consumer_goods']
        self.assertEqual(resource_row(r, {}), ('advanced', 1, 50.0, 100.0, 15000.0, 5.0, 500.0))

    def test_resource_off_the_market(self):
        r = parse('unity = { category = abstract max = 50000 }')['unity']
        self.assertEqual(resource_row(r, {}), ('abstract', 0, None, None, 50000.0, None, None))

    def test_base_value_with_variable_and_flat(self):
        self.assertEqual(base_value(parse('x = { base = @v }')['x'], {'@v': 3.0}), 3.0)
        self.assertEqual(base_value('2', {}), 2.0)

    def test_attitude_trades(self):
        self.assertEqual(attitude_trades(parse('a = { behaviour = { trade = yes coexist = yes } }')['a']), 1)
        self.assertEqual(attitude_trades(parse('a = { behaviour = { weaken = yes } }')['a']), 0)

    def test_slot_count(self):
        s = parse('l = { module_slots = { 1 2 } building_slots = { 1 } }')['l']
        self.assertEqual((slot_count(s.get('module_slots')), slot_count(s.get('building_slots'))), (2, 1))
        self.assertEqual(slot_count(None), 0)

    def test_opinion_modifier(self):
        o = parse('o = { opinion = { base = 1 } accumulative = yes max = 100 decay = { base = 2 } }')['o']
        self.assertEqual(opinion_row(o, {}), (1.0, 1, None, 100.0, 2.0))


if __name__ == '__main__':
    unittest.main()
