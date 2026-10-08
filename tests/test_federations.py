#!/usr/bin/env python3
"""Unit tests for the pure federation helpers (and unlock_keys' federation modes)."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.federations import (FEDERATION_LEAVES, law_types, level_perks, never_available,
                                   perk_effect_keys)
from extractor.traditions import unlock_keys
from extractor.triggers import normalize_trigger


def block(text: str):
    return Parser(tokenize(f"x = {{ {text} }}")).parse()['x']


IDX = {'is_militarist': block('OR = { has_ethic = ethic_militarist is_fanatic_militarist = yes }'),
       'is_fanatic_militarist': block('has_ethic = ethic_fanatic_militarist')}


class TestUnlockKeysFederation(unittest.TestCase):
    def test_ethic_scripted_trigger_expanded(self):
        b = block('OR = { custom_tooltip = { fail_text = x is_militarist = yes } '
                  'has_active_tradition = tr_unyielding_federations_finish }')
        self.assertEqual(unlock_keys(b, IDX, FEDERATION_LEAVES),
                         ['ethic_militarist', 'ethic_fanatic_militarist',
                          'tr_unyielding_federations_finish'])

    def test_default_leaves_ignore_ethics_and_civics(self):
        b = block('is_militarist = yes has_valid_civic = civic_x has_tradition = tr_a')
        self.assertEqual(unlock_keys(b, IDX), ['tr_a'])

    def test_negated_collects_only_inside_not(self):
        b = block('has_valid_civic = civic_merchant_guilds '
                  'custom_tooltip = { NOT = { has_valid_civic = civic_barbaric_despoilers } } '
                  'NOR = { has_civic = civic_a AND = { has_civic = civic_b } }')
        self.assertEqual(unlock_keys(b, IDX, FEDERATION_LEAVES), ['civic_merchant_guilds'])
        self.assertEqual(unlock_keys(b, IDX, FEDERATION_LEAVES, negated=True),
                         ['civic_barbaric_despoilers', 'civic_a', 'civic_b'])


class TestLevelPerks(unittest.TestCase):
    def test_order_xp_and_variables(self):
        levels = block('level_2 = { perks = { b c } } '
                       'level_1 = { experience = @xp perks = { a } } '
                       'not_a_level = { perks = { z } }')
        self.assertEqual(level_perks(levels, {'@xp': 1200.0}),
                         [(1, 1200.0, ['a']), (2, None, ['b', 'c'])])


class TestLawTypes(unittest.TestCase):
    def test_positive_only(self):
        self.assertEqual(law_types(block('has_federation_type = hegemony_federation'), {}),
                         ['hegemony_federation'])
        self.assertEqual(law_types(block(
            'NOT = { has_federation_type = imperial_loyalists_federation }'), {}), [])
        self.assertEqual(law_types(block('OR = { has_federation_type = a has_federation_type = b }'), {}),
                         ['a', 'b'])


class TestNeverAvailable(unittest.TestCase):
    def test_always_no(self):
        self.assertTrue(never_available(normalize_trigger(block('always = no'))))
        self.assertFalse(never_available(normalize_trigger(block('always = yes'))))
        self.assertFalse(never_available(None))


class TestPerkEffectKeys(unittest.TestCase):
    def test_tooltip_then_block_descriptions_deduplicated(self):
        d = block('custom_tooltip = tip members_modifier = { description = eff x = 1 } '
                  'leader_modifier = { description = eff y = 2 } federation_modifier = { z = 1 }')
        self.assertEqual(perk_effect_keys(d), ['tip', 'eff'])


if __name__ == '__main__':
    unittest.main()
