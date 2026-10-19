#!/usr/bin/env python3
"""Unit tests for the pure resolution helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.diplomacy import category_members, influence_cost


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


class TestCategoryMembers(unittest.TestCase):
    def test_each_resolution_in_the_first_category_listing_it(self):
        cats = parse('a = { group = g resolution_types = { "r1" "r2" } } '
                     'b = { resolution_types = { "r2" "r3" } }')
        self.assertEqual(category_members(cats), {'r1': 'a', 'r2': 'a', 'r3': 'b'})

    def test_a_single_entry_is_not_split_into_letters(self):
        self.assertEqual(category_members(parse('a = { resolution_types = { "r1" } }')), {'r1': 'a'})


class TestInfluenceCost(unittest.TestCase):
    def test_resolves_the_cost_variable(self):
        r = parse('x = { resources = { category = resolutions cost = { influence = @t1 } } }')['x']
        self.assertEqual(influence_cost(r, {'@t1': 100.0}), 100.0)

    def test_none_without_a_cost(self):
        self.assertIsNone(influence_cost(parse('x = { target = no }')['x'], {}))


if __name__ == '__main__':
    unittest.main()
