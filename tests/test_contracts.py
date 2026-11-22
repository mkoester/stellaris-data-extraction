#!/usr/bin/env python3
"""Unit tests for Nomads contracts: categories, top-level inline scripts and their times."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.contracts import (contract_categories, days, desc_key, desc_scopes, failed_opinion, offer_techs, operator_techs, reward,
                                 steps, with_inline)
from extractor.situations import branches_on_arkship, requirements


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


# Shaped like common/missions/mission_categories/00_mission_categories.txt (4.5.x).
CATEGORIES = """
mission = { is_contract = no map_icon = GFX_nomad_contract_icon }
resource_contract = { is_contract = yes show_in_issue_list = yes }
science_contract_internal = { is_contract = yes }
"""

# Shaped like contracts/HarvestResource (common/inline_scripts/contracts).
HARVEST = """
category = resource_contract
event_chain = contract_harvest_$RESOURCE$_chain
time_to_accept = @contract_time_base
inline_script = { script = nested/thing }
"""


class TestContracts(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(contract_categories(parse(CATEGORIES)),
                         {'resource_contract', 'science_contract_internal'})

    def test_inline_merged_and_substituted(self):
        block = parse("""c = { inline_script = { script = contracts/HarvestResource RESOURCE = minerals }
                               possible_operator = { always = yes } }""")['c']
        m = with_inline(block, lambda name: HARVEST if name == 'contracts/HarvestResource' else None)
        self.assertEqual(m['category'], 'resource_contract')
        self.assertEqual(m['event_chain'], 'contract_harvest_minerals_chain')
        self.assertIn('possible_operator', m)

    def test_own_keys_win(self):
        block = parse("c = { inline_script = { script = x } category = science_contract_internal }")['c']
        self.assertEqual(with_inline(block, lambda _: HARVEST)['category'], 'science_contract_internal')

    def test_bare_script_name_and_missing_script(self):
        self.assertEqual(with_inline(parse("c = { inline_script = x }")['c'], lambda _: HARVEST)['category'],
                         'resource_contract')
        self.assertEqual(with_inline(parse("c = { inline_script = gone }")['c'], lambda _: None), {})

    def test_days(self):
        v = {'@contract_time_base': 1800.0}
        self.assertEqual(days('@contract_time_base', v), 1800)
        self.assertEqual(days('720', v), 720)
        self.assertIsNone(days(None, v))
        self.assertIsNone(days('@unknown', v))


# Shaped like contract_espionage_support_2 / contract_colony_startup (02_contracts.txt, 4.5.x).
VARS = {'@opinion_contract_small': 50.0, '@opinion_contract_regular': 100.0,
        '@opinion_failed_contract_regular': -125.0}
ESPIONAGE = """
c = {
    counter = { contract_progress_counter = { max = 2 } }
    possible_operator = { custom_tooltip = { fail_text = x NOT = { has_event_chain = y } } }
    on_success = {
        if = { limit = { exists = root }
            espionage_support_effect = { INTEL_AMOUNT = 20 }
            custom_tooltip = trust10
            custom_tooltip = opinion100
            contract_scaling_reward_effect = { TRADE = 1750 RESOURCE = influence RESOURCE_AMOUNT = 35 }
            add_resource = { influence = @opinion_contract_regular }
        }
    }
    on_cancel = { if = { limit = { exists = root }
        prev = { add_opinion_modifier = { modifier = opinion_failed_contract_regular who = root } } } }
}
"""
# Forever Cruise's passengers: an inline script carries the reward.
CRUISE = "on_success = { inline_script = { script = cruise PAY = 2500 } }"
CRUISE_SCRIPT = "cruise_contract_scaling_reward_effect = { TRADE = $PAY$ }"
PROJECT = """
p = {
    requirements = { carries_colony = 1 }
    on_success = { if = { limit = { is_science_arkship = yes } } }
}
"""


class TestContractDetails(unittest.TestCase):
    def test_reward_scaled_fixed_trust_opinion(self):
        c = parse(ESPIONAGE)['c']
        self.assertEqual(reward(c['on_success'], VARS, lambda _: None), {
            'scaled': [{'trade': 1750.0, 'resource': 'influence', 'amount': 35.0}],
            'cruise': None, 'fixed': {'influence': 100.0}, 'trust': 10, 'opinion': 100})

    def test_reward_through_an_inline_script(self):
        r = reward(parse(CRUISE)['on_success'], VARS, lambda n: CRUISE_SCRIPT if n == 'cruise' else None)
        self.assertEqual(r['cruise'], 2500.0)

    def test_no_reward(self):
        self.assertIsNone(reward(None, VARS, lambda _: None))
        # a multiplied add_resource (Heirs hunters) is left out
        self.assertIsNone(reward(parse("s = { add_resource = { trade = 500 mult = x } }")['s'], VARS,
                                 lambda _: None))

    def test_failed_opinion_steps_techs(self):
        c = parse(ESPIONAGE)['c']
        self.assertEqual(failed_opinion(c['on_cancel'], VARS, lambda _: None), -125.0)
        self.assertEqual(steps(c['counter']), 2)
        self.assertEqual(operator_techs(c['possible_operator']), [])
        self.assertEqual(operator_techs(parse("p = { has_technology = tech_mine_volatile_motes }")['p']),
                         ['tech_mine_volatile_motes'])

    def test_offer_techs(self):
        # Clear Blockers, Deep Scan (4.5.x 02_contracts.txt): one tech; any one of several
        self.assertEqual(offer_techs(parse("""p = { NOT = { fromfrom = { is_at_war_with = root } }
            has_technology = tech_planetary_engineering }""")['p']), [['tech_planetary_engineering']])
        self.assertEqual(offer_techs(parse("""p = { OR = { has_technology = tech_a
            has_technology = tech_b } }""")['p']), [['tech_a', 'tech_b']])
        # an OR with anything else in it is not a tech group
        self.assertEqual(offer_techs(parse("p = { OR = { has_technology = tech_a is_ai = yes } }")['p']), [])
        self.assertEqual(offer_techs(None), [])

    def test_desc_key(self):
        self.assertEqual(desc_key({}, 'contract_x'), 'contract_x_desc')
        self.assertEqual(desc_key({'desc': '"contract_y_desc"'}, 'contract_x'), 'contract_y_desc')
        self.assertEqual(desc_key(parse("c = { desc_operator = { trigger = { a = b } text = T1 } }")['c'],
                                  'contract_x'), 'T1')

    def test_desc_scopes(self):
        self.assertEqual(
            desc_scopes('Transport delegates to §Y[from.capital_scope.GetName]§! for §Y[From.GetName]§!'),
            "Transport delegates to §Y{issuer}'s capital§! for §Y{issuer}§!")
        self.assertEqual(desc_scopes('[marauder_country_1.GetName]'), '[marauder_country_1.GetName]')

    def test_project_requirements_and_arkship_branch(self):
        p = parse(PROJECT)['p']
        self.assertEqual(requirements(p['requirements']), {'carries_colony': 1})
        self.assertTrue(branches_on_arkship(p['on_success']))
        self.assertFalse(branches_on_arkship(None))
        self.assertEqual(requirements(parse("r = { shipclass_science_ship = 1 leader = scientist }")['r']),
                         {'shipclass_science_ship': 1, 'leader': 'scientist'})


if __name__ == '__main__':
    unittest.main()
