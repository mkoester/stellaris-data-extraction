#!/usr/bin/env python3
"""Unit tests for choosing which conditional produces/upkeep blocks a job or deposit shows."""
import unittest

from extractor.jobs import _drop_scaled_duplicates, _fallback_block
from extractor.precursors import _default_overclocks, _primary_resource_block


def signals_from(table):
    """A `signals` stub: trigger name → (positive, negative) token sets."""
    return lambda trigger: table[trigger]


class TestDropScaledDuplicates(unittest.TestCase):
    def test_scaled_bonus_on_top_of_base_is_dropped(self):
        base = {'unity': '1'}
        bonus = {'unity': '0.05', 'mult': 'value:mentor_scale_value'}
        self.assertEqual(_drop_scaled_duplicates([base, bonus]), [base])

    def test_scaled_block_that_is_the_only_source_is_kept(self):
        knights = [{'unity': '1', 'mult': 'value:knights_unity_output_modifier'}]
        self.assertEqual(_drop_scaled_duplicates(knights), knights)

    def test_gated_scaled_block_is_kept(self):
        blocks = [{'unity': '1'}, {'unity': '1', 'mult': 'value:x', 'trigger': 't'}]
        self.assertEqual(_drop_scaled_duplicates(blocks), blocks)


class TestFallbackBlock(unittest.TestCase):
    def test_single_gated_block_is_an_optional_extra(self):
        civilian = [{'consumer_goods': '0.25', 'trigger': 'pleasure'}]
        sig = signals_from({'pleasure': ({'has_valid_civic=civic_pleasure_seekers'}, set())})
        self.assertIsNone(_fallback_block(civilian, sig))

    def test_independent_civic_bonuses_give_none(self):
        enforcer = [{'unity': '1', 'trigger': 'maverick'}, {'unity': '2.5', 'trigger': 'educ'}]
        sig = signals_from({'maverick': ({'has_country_flag=maverick_citizens_flag'}, set()),
                            'educ': ({'has_valid_civic=civic_civil_education'}, set())})
        self.assertIsNone(_fallback_block(enforcer, sig))

    def test_species_type_alternatives_give_the_first(self):
        organic = {'food': '5', 'trigger': 'organic'}
        lithoid = {'minerals': '5', 'trigger': 'lithoid'}
        sig = signals_from({'organic': ({'has_trait=trait_organic'}, set()),
                            'lithoid': ({'is_archetype=LITHOID'}, set())})
        self.assertIs(_fallback_block([organic, lithoid], sig), organic)

    def test_type_alternatives_ignore_an_extra_gated_block(self):
        machine = {'energy': '2', 'trigger': 'machine'}
        hive = {'minerals': '2', 'trigger': 'hive'}
        extra = {'energy': '1', 'trigger': 'subterranean'}
        sig = signals_from({'machine': ({'has_authority=auth_machine_intelligence'}, set()),
                            'hive': ({'has_authority=auth_hive_mind'}, set()),
                            'subterranean': ({'has_authority=auth_machine_intelligence',
                                              'has_deposit=d_underground_contact_zone'}, set())})
        self.assertIs(_fallback_block([extra, machine, hive], sig), machine)

    def test_explicit_complement_gives_the_first(self):
        normal = {'consumer_goods': '1.5', 'trigger': 'not_servitor'}
        servitor = {'consumer_goods': '1', 'trigger': 'servitor'}
        sig = signals_from({'not_servitor': (set(), {'has_valid_civic=civic_machine_servitor'}),
                            'servitor': ({'has_valid_civic=civic_machine_servitor'}, set())})
        self.assertIs(_fallback_block([normal, servitor], sig), normal)

    def test_any_unconditional_block_gives_none(self):
        self.assertIsNone(_fallback_block([{'food': '1'}, {'food': '2', 'trigger': 't'}],
                                          signals_from({})))


class TestDefaultOverclocks(unittest.TestCase):
    MEGA = {'arc_furnace_3': {'overclock_types': ['oc_minerals', 'oc_balanced', 'oc_alloys']}}

    def test_highest_ai_weight_wins(self):
        types = {'oc_balanced': {'ai_weight': '@balanced_weight'}}
        self.assertEqual(_default_overclocks(self.MEGA, types, {'@balanced_weight': 200.0}),
                         {'oc_balanced'})

    def test_block_base_counts_and_missing_weight_is_100(self):
        types = {'oc_alloys': {'ai_weight': {'base': '150', 'modifier': {'factor': '2'}}}}
        self.assertEqual(_default_overclocks(self.MEGA, types, {}), {'oc_alloys'})

    def test_equal_weights_pick_the_first_listed(self):
        self.assertEqual(_default_overclocks(self.MEGA, {}, {}), {'oc_minerals'})

    def test_megastructure_without_modes_is_skipped(self):
        self.assertEqual(_default_overclocks({'gateway': {'build_time': '10'}}, {}, {}), set())


class TestPrimaryResourceBlock(unittest.TestCase):
    def test_lone_block_is_kept_even_if_gated(self):
        block = {'minerals': '2', 'trigger': 't'}
        self.assertIs(_primary_resource_block([block], signals_from({}), set()), block)

    def test_unconditional_blocks_are_merged(self):
        blocks = [{'unity': '10'}, {'physics_research': '10'}, {'alloys': '5', 'trigger': 't'}]
        self.assertEqual(_primary_resource_block(blocks, signals_from({}), set()),
                         {'unity': '10', 'physics_research': '10'})

    def test_default_overclock_block_is_chosen(self):
        minerals = {'minerals': '4', 'trigger': 'm'}
        balanced = {'minerals': '2', 'alloys': '1', 'trigger': 'b'}
        sig = signals_from({'m': ({'has_overclock=oc_minerals'}, set()),
                            'b': ({'has_overclock=oc_balanced'}, set())})
        self.assertIs(_primary_resource_block([minerals, balanced], sig, {'oc_balanced'}),
                      balanced)

    def test_otherwise_gated_blocks_give_none(self):
        blocks = [{'nanites': '1.8', 'trigger': 'a'}, {'nanites': '1', 'trigger': 'b'}]
        sig = signals_from({'a': ({'has_country_flag=x'}, set()),
                            'b': ({'has_country_flag=y'}, set())})
        self.assertIsNone(_primary_resource_block(blocks, sig, {'oc_balanced'}))


if __name__ == '__main__':
    unittest.main()
