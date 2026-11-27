#!/usr/bin/env python3
"""Unit tests for the pure ideology helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.ideologies import (adopted_level, concept_links, council_bypasses, custom_tooltips, drop_unresolved,
                                  ethic_of, humanize_ref, inline_expanded, strength_source,
                                  tier_blocks)


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


class TestInlineExpanded(unittest.TestCase):
    SCRIPTS = {
        'tiers': 'tier = { triggered_country_modifier = { x_add = $V$ } } '
                 'tier = { triggered_country_modifier = { x_add = 2 } }',
        'extra': 'triggered_country_modifier = { y_add = 1 }',
    }

    def load(self, name):
        return self.SCRIPTS.get(name)

    def test_params_filled_and_blocks_appended(self):
        t = inline_expanded(parse('t = { first_tier = 1 inline_script = { script = tiers V = 1 } }')['t'],
                            self.load)
        self.assertNotIn('inline_script', t)
        self.assertEqual([b['triggered_country_modifier']['x_add'] for b in t['tier']], ['1', '2'])

    def test_nested_and_bare_name(self):
        t = inline_expanded(parse('t = { tier = { triggered_country_modifier = { z_add = 3 } '
                                  'inline_script = extra } }')['t'], self.load)
        mods = t['tier']['triggered_country_modifier']
        self.assertEqual([m for m in mods], [{'z_add': '3'}, {'y_add': '1'}])

    def test_missing_script_is_skipped(self):
        self.assertEqual(inline_expanded({'a': '1', 'inline_script': 'nope'}, self.load), {'a': '1'})


class TestTiers(unittest.TestCase):
    def test_numbered_from_first_tier(self):
        t = parse('t = { first_tier = 2 tier = { a = 1 } tier = { a = 2 } }')['t']
        self.assertEqual([n for n, _ in tier_blocks(t)], [2, 3])

    def test_default_first_tier_and_single_tier(self):
        self.assertEqual([n for n, _ in tier_blocks(parse('t = { tier = { a = 1 } }')['t'])], [1])


class TestEthicAndLevel(unittest.TestCase):
    def test_ethic_of(self):
        self.assertEqual(ethic_of('ethic_fanatic_authoritarian'), 'ethic_authoritarian')
        self.assertEqual(ethic_of('ethic_gestalt_consciousness'), 'ethic_gestalt_consciousness')
        self.assertIsNone(ethic_of(None))

    def test_adopted_level_at_any_depth(self):
        self.assertEqual(adopted_level(parse('p = { adopted_ideology_level = 2 }')['p']), 2)
        self.assertEqual(adopted_level(parse('p = { owner? = { ideology? = { x = y } '
                                             'adopted_ideology_level = 3 } }')['p']), 3)
        self.assertIsNone(adopted_level(parse('p = { is_gestalt = no }')['p']))


class TestTooltipsAndLinks(unittest.TestCase):
    def test_custom_tooltips_skip_hidden_effects(self):
        e = parse('e = { custom_tooltip = a if = { limit = { x = y } custom_tooltip = b } '
                  'hidden_effect = { custom_tooltip = c } custom_tooltip = a }')['e']
        self.assertEqual(custom_tooltips(e), ['a', 'b'])

    def test_concept_links_through_refs(self):
        loc = {'tt': "Grants $x$ and ['policy:trade_policy:trade_policy_knowledge_exchange']",
               'x': "['edict:broadcasting_pacifist', Broadcasting]"}
        self.assertEqual(concept_links(loc['tt'], loc),
                         [('edict', 'broadcasting_pacifist'),
                          ('policy', 'trade_policy_knowledge_exchange')])
        self.assertEqual(concept_links("['concept_ideology']", {}), [])
        self.assertEqual(concept_links(None, {}), [])


class TestStrengthSource(unittest.TestCase):
    def test_number_variable_and_ref(self):
        self.assertEqual(strength_source({'add': '0.25', 'has_edict': 'grand_fleet', 'desc': 'X'}, {}),
                         ('add', 0.25, None, {'has_edict': 'grand_fleet'}))
        self.assertEqual(strength_source({'set': '@v'}, {'@v': -10.0})[:3], ('set', -10.0, None))
        self.assertEqual(strength_source({'subtract': 'value:ideology_level_malus'}, {})[:3],
                         ('subtract', None, 'value:ideology_level_malus'))

    def test_humanize_ref(self):
        self.assertEqual(humanize_ref('value:clashing_ideologies_bonus'), 'Clashing ideologies bonus')
        self.assertIsNone(humanize_ref(None))


class TestDropUnresolved(unittest.TestCase):
    def test_unfilled_numbers_and_double_signs(self):
        self.assertEqual(drop_unresolved('Per Clash: +0.25, If Rival Ideology: +%\nNext: +1'),
                         'Per Clash: +0.25\nNext: +1')
        self.assertEqual(drop_unresolved('Opinion: ++0.1 (Max. +)'), 'Opinion: +0.1')
        self.assertEqual(drop_unresolved('Martyr ()'), 'Martyr')
        self.assertEqual(drop_unresolved('Opinion: +0.1 (Max. +50)'), 'Opinion: +0.1 (Max. +50)')


class TestCouncilBypasses(unittest.TestCase):
    def test_one_row_per_and_naming_a_tenet(self):
        rule = parse('r = { OR = { '
                     'AND = { leader_class = official has_ethic = ethic_spiritualist '
                     'owner? = { has_active_tenet = tenet_sacred_counsel } } '
                     'AND = { leader_class = scientist has_ethic = ethic_materialist '
                     'owner? = { has_active_tenet = tenet_rationality } } '
                     'AND = { leader_class = envoy } } }')['r']
        self.assertEqual(council_bypasses(rule), [
            ('tenet_sacred_counsel', 'official', 'ethic_spiritualist'),
            ('tenet_rationality', 'scientist', 'ethic_materialist')])

    def test_single_and_and_missing_rule(self):
        rule = parse('r = { AND = { leader_class = commander '
                     'owner = { has_active_tenet = tenet_x } } }')['r']
        self.assertEqual(council_bypasses(rule), [('tenet_x', 'commander', None)])
        self.assertEqual(council_bypasses(None), [])


if __name__ == '__main__':
    unittest.main()
