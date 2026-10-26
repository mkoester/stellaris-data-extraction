#!/usr/bin/env python3
"""Unit tests for species rights' triggers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.rights import right_modifiers, unwrap_from
from extractor.triggers import normalize_trigger


class TestRights(unittest.TestCase):
    def test_from_is_folded_in(self):
        block = Parser(tokenize(
            'exists = from from = { is_gestalt = no } NOT = { has_trait = trait_hive_mind }')).parse()
        self.assertEqual(normalize_trigger(unwrap_from(block)), {'op': 'and', 'args': [
            {'leaf': 'is_gestalt', 'value': 'no'},
            {'op': 'not', 'args': [{'leaf': 'has_trait', 'value': 'trait_hive_mind'}]}]})

    def test_modifiers_are_the_unconditional_ones(self):
        # 4.5.2 living_standards: Utopian Abundance's pop group modifier, its triggered one left out
        block = Parser(tokenize(
            'pop_group_modifier = { pop_happiness = 0.2 pop_consumer_goods_upkeep_add = @lux } '
            'triggered_pop_group_modifier = { potential = { always = yes } pop_happiness = 0.1 }')).parse()
        self.assertEqual(right_modifiers(block, {'@lux': 0.5}),
                         [('pop_happiness', 0.2), ('pop_consumer_goods_upkeep_add', 0.5)])

    def test_repeated_blocks_are_summed(self):
        block = Parser(tokenize(
            'pop_group_modifier = { pop_happiness = -0.1 } pop_group_modifier = { pop_happiness = -0.2 }')).parse()
        [(key, value)] = right_modifiers(block, {})
        self.assertEqual(key, 'pop_happiness')
        self.assertAlmostEqual(value, -0.3)


if __name__ == '__main__':
    unittest.main()
