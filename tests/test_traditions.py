#!/usr/bin/env python3
"""Unit tests for the pure tradition/ascension-perk helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.traditions import (cost_amounts, decision_costs, decision_effects, owner_scope,
                                  pick_defines, prereqs, required_dlc, tooltip_keys, tree_textures,
                                  unlock_keys)
from extractor.triggers import normalize_trigger


def block(text: str):
    return Parser(tokenize(f"x = {{ {text} }}")).parse()['x']


class TestTreeTextures(unittest.TestCase):
    def test_header_strips_by_tree(self):
        gfx = Parser(tokenize('''spriteTypes = {
            spriteType = { name = "GFX_tradition_hex_bg_blue" textureFile = "a.dds" }
            spriteType = { name = "GFX_tradition_hex_bg_tradition_expansion" textureFile = "b.dds" }
            spriteType = { name = "GFX_tradition_category_bg_tradition_expansion" textureFile = "c.dds" }
        }''')).parse()
        self.assertEqual(tree_textures(gfx), {'blue': 'a.dds', 'tradition_expansion': 'b.dds'})


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


class TestDecisions(unittest.TestCase):
    LUXURIES = '''d = {
        owned_planets_only = yes
        resources = { category = decisions
            cost = { trigger = { pop_amount < 1000 } consumer_goods = 100 }
            cost = { trigger = { pop_amount >= 10000 } consumer_goods = 1000 } }
        potential = { owner = { is_regular_empire = yes } NOT = { is_planet_class = pc_nanotech } }
        allow = { NOT = { has_modifier = planet_luxuries_boost } }
        effect = { add_modifier = { modifier = "planet_luxuries_boost" days = 3600 }
                   remove_modifier = bleak_planet
                   if = { limit = { has_modifier = x } add_modifier = { modifier = y } } }
    }'''

    def setUp(self):
        self.d = Parser(tokenize(self.LUXURIES)).parse()['d']
        self.trig = lambda b: normalize_trigger(b, structured=owner_scope(normalize_trigger))

    def test_cost_tiers_keep_their_trigger(self):
        tiers = decision_costs(self.d['resources'], {}, self.trig)
        self.assertEqual([t['cost'] for t in tiers], [{'consumer_goods': 100.0}, {'consumer_goods': 1000.0}])
        self.assertEqual(tiers[0]['when'], {'leaf': 'pop_amount', 'cmp': '<', 'value': '1000'})

    def test_effects_top_level_only(self):
        self.assertEqual(decision_effects(self.d['effect']),
                         ([{'modifier': 'planet_luxuries_boost', 'days': 3600}], ['bleak_planet']))

    def test_owner_scope_unwrapped(self):
        t = self.trig(self.d['potential'])
        self.assertIn({'leaf': 'is_regular_empire', 'value': 'yes'}, t['args'])
