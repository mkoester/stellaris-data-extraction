#!/usr/bin/env python3
"""Unit tests for the pure Shroud helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.shroud import calling_tiers, position, rgb_hex


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


class TestShroudHelpers(unittest.TestCase):
    def test_colour_as_hex(self):
        self.assertEqual(rgb_hex(parse('c = { 0.125 0.608 0.208 1 }')['c']), '#209b35')
        self.assertIsNone(rgb_hex('red'))

    def test_tiers_numbered_from_first_tier_with_variables(self):
        c = parse('x = { first_tier = 2 tier = { attunement = @t2 } tier = { attunement = 300 } }')['x']
        self.assertEqual(calling_tiers(c, {'@t2': 200.0}), [(2, 200.0), (3, 300.0)])

    def test_a_single_tier(self):
        c = parse('x = { tier = { attunement = 100 } }')['x']
        self.assertEqual(calling_tiers(c, {}), [(1, 100.0)])

    def test_position(self):
        self.assertEqual(position(parse('p = { position = { x = 0 y = -1 } }')['p']), (0.0, -1.0))
        self.assertEqual(position({}), (None, None))


if __name__ == '__main__':
    unittest.main()
