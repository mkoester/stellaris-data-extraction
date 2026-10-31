#!/usr/bin/env python3
"""Unit tests for trigger-tree normalisation."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.triggers import COMPLEX, dlc_trigger_map, normalize_trigger


def block(text: str):
    return Parser(tokenize(f"x = {{ {text} }}")).parse()['x']


def L(k, v, cmp=None):
    d = {'leaf': k, 'value': v}
    if cmp:
        d['cmp'] = cmp
    return d


class TestNormalizeTrigger(unittest.TestCase):
    def test_plain_conjunction_and_single_collapse(self):
        self.assertEqual(normalize_trigger(block('is_gestalt = no')), L('is_gestalt', 'no'))
        self.assertEqual(normalize_trigger(block('a = yes b = no')),
                         {'op': 'and', 'args': [L('a', 'yes'), L('b', 'no')]})

    def test_safe_scope_is_the_scope(self):
        # 4.5.3's advanced_traits_have_reduced_cost: `owner? = { … }`
        self.assertEqual(normalize_trigger(block('owner? = { has_tradition = tr_x }')),
                         L('owner', 'scope'))
        unwrap = lambda k, v: normalize_trigger(v) if k == 'owner' else None
        self.assertEqual(normalize_trigger(block('owner? = { has_tradition = tr_x }'), structured=unwrap),
                         L('has_tradition', 'tr_x'))

    def test_empty_block_is_none(self):
        self.assertIsNone(normalize_trigger(block('')))
        self.assertIsNone(normalize_trigger(None))

    def test_nor_becomes_not_or(self):
        self.assertEqual(normalize_trigger(block('NOR = { a = yes b = yes }')),
                         {'op': 'not', 'args': [{'op': 'or', 'args': [L('a', 'yes'), L('b', 'yes')]}]})

    def test_or_of_single_and_is_not_flattened(self):
        self.assertEqual(normalize_trigger(block('OR = { AND = { p = yes q = yes } }')),
                         {'op': 'and', 'args': [L('p', 'yes'), L('q', 'yes')]})

    def test_duplicate_scalar_key_in_or(self):
        self.assertEqual(normalize_trigger(block('OR = { has_country_flag = a has_country_flag = b }')),
                         {'op': 'or', 'args': [L('has_country_flag', 'a'), L('has_country_flag', 'b')]})

    def test_custom_tooltip_unwrapped_and_text_dropped(self):
        t = block('custom_tooltip = { fail_text = "x" num_ascension_perks > 2 } custom_tooltip = foo_tt')
        self.assertEqual(normalize_trigger(t), L('num_ascension_perks', '2', '>'))

    def test_custom_tooltip_is_one_alternative_inside_or(self):
        t = block('OR = { custom_tooltip = { text = t a = yes b = yes } c = yes }')
        self.assertEqual(normalize_trigger(t), {'op': 'or', 'args': [
            {'op': 'and', 'args': [L('a', 'yes'), L('b', 'yes')]}, L('c', 'yes')]})

    def test_variable_resolved_in_comparison(self):
        t = block('num_tradition_categories < @max')
        self.assertEqual(normalize_trigger(t, variables={'@max': 7.0}),
                         L('num_tradition_categories', '7', '<'))

    def test_dlc_trigger_inlined(self):
        dlc = {'has_machine_age_dlc': 'The Machine Age'}
        self.assertEqual(normalize_trigger(block('has_machine_age_dlc = yes'), dlc),
                         L('host_has_dlc', 'The Machine Age'))
        self.assertEqual(normalize_trigger(block('has_machine_age_dlc = no'), dlc),
                         {'op': 'not', 'args': [L('host_has_dlc', 'The Machine Age')]})

    def test_lone_if_is_implication(self):
        t = block('if = { limit = { a = yes } b = yes }')
        self.assertEqual(normalize_trigger(t), {'op': 'or', 'args': [
            {'op': 'and', 'args': [L('a', 'yes'), L('b', 'yes')]},
            {'op': 'not', 'args': [L('a', 'yes')]}]})

    def test_if_else_chain_negates_earlier_limits(self):
        t = block('if = { limit = { a = yes } b = yes } else = { c = yes }')
        self.assertEqual(normalize_trigger(t), {'op': 'or', 'args': [
            {'op': 'and', 'args': [L('a', 'yes'), L('b', 'yes')]},
            {'op': 'and', 'args': [{'op': 'not', 'args': [L('a', 'yes')]}, L('c', 'yes')]}]})

    def test_two_chains_keep_their_own_else(self):
        t = block('if = { limit = { a = yes } b = yes } else_if = { c = yes } '
                  'if = { limit = { d = yes } e = yes }')
        self.assertEqual(normalize_trigger(t), {'op': 'and', 'args': [
            {'op': 'or', 'args': [
                {'op': 'and', 'args': [L('a', 'yes'), L('b', 'yes')]},
                {'op': 'and', 'args': [{'op': 'not', 'args': [L('a', 'yes')]}, L('c', 'yes')]}]},
            {'op': 'or', 'args': [
                {'op': 'and', 'args': [L('d', 'yes'), L('e', 'yes')]},
                {'op': 'not', 'args': [L('d', 'yes')]}]}]})

    def test_orphan_else_is_complex(self):
        self.assertEqual(normalize_trigger({'else': {'c': 'yes'}}), COMPLEX)

    def test_scope_kept_opaque(self):
        self.assertEqual(normalize_trigger(block('owner = { is_ai = no }')), L('owner', 'scope'))


class TestDlcTriggerMap(unittest.TestCase):
    def test_only_pure_dlc_triggers(self):
        idx = {'has_x_dlc': {'host_has_dlc': 'X'}, 'other': {'host_has_dlc': 'Y', 'is_ai': 'no'}}
        self.assertEqual(dlc_trigger_map(idx), {'has_x_dlc': 'X'})



class TestParameters(unittest.TestCase):
    def test_has_parameters(self):
        from extractor.triggers import has_parameters
        self.assertTrue(has_parameters({'has_country_flag': '$FLAG$'}))
        self.assertFalse(has_parameters({'OR': {'has_valid_civic': 'civic_dark_consortium'}}))


if __name__ == '__main__':
    unittest.main()
