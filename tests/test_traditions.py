#!/usr/bin/env python3
"""Unit tests for the pure tradition/ascension-perk helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.traditions import pick_defines, prereqs, required_dlc, tooltip_keys
from extractor.triggers import normalize_trigger


def block(text: str):
    return Parser(tokenize(f"x = {{ {text} }}")).parse()['x']


class TestPickDefines(unittest.TestCase):
    def test_nested_and_block_values(self):
        d = block('NGameplay = { TRADITION_COST_TRADITION_EXP = 1.800 OTHER = 3 } '
                  'NEconomy = { TRADITION_COST_AMOUNTS = { 300 } }')
        self.assertEqual(pick_defines(d), {'TRADITION_COST_TRADITION_EXP': 1.8,
                                           'TRADITION_COST_AMOUNTS': 300.0})


class TestRequiredDlc(unittest.TestCase):
    def test_top_level_only(self):
        self.assertEqual(required_dlc(normalize_trigger(block('host_has_dlc = Nemesis is_ai = no'))),
                         'Nemesis')
        # Either of two DLCs is not one required DLC.
        self.assertIsNone(required_dlc(normalize_trigger(block(
            'OR = { host_has_dlc = A host_has_dlc = B }'))))
        self.assertIsNone(required_dlc(None))


class TestPrereqs(unittest.TestCase):
    def test_siblings_only_and_negations_skipped(self):
        t = normalize_trigger(block('has_tradition = tr_a has_tradition = tr_other_tree '
                                    'NOT = { has_tradition = tr_b }'))
        self.assertEqual(prereqs(t, {'tr_a', 'tr_b'}), ['tr_a'])


class TestTooltipKeys(unittest.TestCase):
    def test_collects_strings_not_blocks(self):
        d = block('custom_tooltip = a_tt custom_tooltip_with_modifiers = b_tt '
                  'on_enabled = { custom_tooltip = c_tt custom_tooltip = { fail_text = x } }')
        self.assertEqual(tooltip_keys(d), ['a_tt', 'b_tt', 'c_tt'])


if __name__ == '__main__':
    unittest.main()
