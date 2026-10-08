#!/usr/bin/env python3
"""Unit tests for the pure tradition/ascension-perk helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.traditions import cost_amounts, pick_defines, prereqs, required_dlc, tooltip_keys, unlock_keys
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


class TestUnlockKeys(unittest.TestCase):
    def test_positive_leaves_and_expanded_scripted_triggers(self):
        idx = {'has_psionic_ascension': block('OR = { has_ascension_perk = ap_mom has_origin = o }')}
        b = block('has_active_tradition = tr_a has_psionic_ascension = yes '
                  'NOT = { has_tradition = tr_b } OR = { has_tradition = tr_c is_ai = no }')
        self.assertEqual(unlock_keys(b, idx), ['tr_a', 'ap_mom', 'tr_c'])

    def test_scripted_trigger_with_no_is_skipped(self):
        idx = {'has_psionic_ascension': block('has_ascension_perk = ap_mom')}
        self.assertEqual(unlock_keys(block('has_psionic_ascension = no'), idx), [])


class TestCostAmounts(unittest.TestCase):
    def test_multiplier_ignored(self):
        self.assertEqual(cost_amounts(block('unity = 10 multiplier = value:edict_size_effect')),
                         {'unity': 10.0})

    def test_variables_resolved(self):
        self.assertEqual(cost_amounts(block('unity = @Edict1Cost'), {'@Edict1Cost': 20.0}),
                         {'unity': 20.0})


if __name__ == '__main__':
    unittest.main()
