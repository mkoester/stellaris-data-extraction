#!/usr/bin/env python3
"""Unit tests for the policy and faction-demand helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.politics import attraction_rules, attraction_structured, expand_inline, owner_scope
from extractor.triggers import normalize_trigger

# Shaped like common/inline_scripts/pop_faction_types/demand_faction_pleased.txt.
PLEASED = """
demand = {
	title = "FACTION_PLEASED_GC"
	fulfilled_effect = -20
	trigger = { owner = { has_country_flag = $FLAG$ } }
}
"""


class TestPolitics(unittest.TestCase):
    def test_inline_script_params_are_filled_in(self):
        d = expand_inline(PLEASED, {'FLAG': 'galcom_resolution_passed_pro_imperialist'})['demand']
        self.assertEqual(d['trigger']['owner']['has_country_flag'],
                         'galcom_resolution_passed_pro_imperialist')

    def test_owner_scope_unwraps_the_owner_block(self):
        block = Parser(tokenize(
            'exists = owner owner = { has_policy_flag = unrestricted_wars }')).parse()
        self.assertEqual(owner_scope(block), {'has_policy_flag': 'unrestricted_wars'})

    def test_owner_scope_drops_a_lone_exists(self):
        self.assertIsNone(owner_scope({'exists': 'owner'}))

    def test_owner_scope_keeps_other_triggers(self):
        block = {'owner': {'is_at_war': 'yes'}, 'num_pops': '5'}
        self.assertIs(owner_scope(block), block)


class TestAttractionRules(unittest.TestCase):
    # shaped like ethic_materialist (common/ethics/00_ethics.txt, 4.5.2)
    ETHIC = """
    pop_attraction_tag = { desc = POP_ATTRACTION_ROBOT_RIGHTS_POS trigger = { has_policy_flag = ai_full_rights } }
    country_attraction = {
        base = 1
        modifier = { factor = 2 has_ethic = ethic_materialist }
        modifier = { factor = 1.5 has_federation = yes desc = FROM_FEDERATION }
        modifier = { exists = overlord mult = value:ethics_from_overlord|ETHIC|materialist| desc = FROM_OVERLORD }
    }
    """

    def test_tags_then_factors_with_their_conditions(self):
        rules = attraction_rules(Parser(tokenize(self.ETHIC)).parse())
        self.assertEqual([(r['kind'], r['desc'], r['factor']) for r in rules],
                         [('tag', 'POP_ATTRACTION_ROBOT_RIGHTS_POS', None), ('country', None, 2.0),
                          ('country', 'FROM_FEDERATION', 1.5), ('country', 'FROM_OVERLORD', None)])
        self.assertEqual(rules[1]['trigger'], {'has_ethic': 'ethic_materialist'})
        self.assertEqual(rules[3]['trigger'], {'exists': 'overlord'})
        self.assertTrue(rules[3]['factor_text'].startswith('value:ethics_from_overlord'))


class TestAttractionStructured(unittest.TestCase):
    # the triggers of ethic_egalitarian's and ethic_materialist's pop_attraction_tags (4.5.2)
    def norm(self, text):
        block = Parser(tokenize(text)).parse()
        return normalize_trigger(block, {}, {}, attraction_structured({}, {}))

    def test_council_and_governor(self):
        self.assertEqual(self.norm("""owner = { any_owned_leader = { OR = { is_ruler = yes is_councilor = yes }
                                    has_ethic = ethic_egalitarian } }"""),
                         {'leaf': 'council_leader_ethic', 'value': 'ethic_egalitarian'})
        self.assertEqual(self.norm("any_owned_planet = { exists = leader leader = { has_ethic = ethic_egalitarian } }"),
                         {'leaf': 'governor_ethic', 'value': 'ethic_egalitarian'})

    def test_jobs_and_species(self):
        self.assertEqual(self.norm("num_assigned_jobs = { job = bureaucrat value > 0 }"),
                         {'leaf': 'assigned_jobs', 'value': 'bureaucrat'})
        self.assertEqual(self.norm("""any_owned_species = { OR = {
                                    has_living_standard = { type = living_standard_good country = root }
                                    has_trait = trait_decadent } }"""),
                         {'op': 'or', 'args': [
                             {'leaf': 'species_has_living_standard', 'value': 'living_standard_good'},
                             {'leaf': 'species_has_trait', 'value': 'trait_decadent'}]})

    def test_anything_else_stays_opaque(self):
        self.assertEqual(self.norm("any_owned_species = { is_robotic = no NOT = { is_same_species = root } }"),
                         {'leaf': 'any_owned_species', 'value': 'scope'})


if __name__ == '__main__':
    unittest.main()
