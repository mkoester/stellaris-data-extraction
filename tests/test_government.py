#!/usr/bin/env python3
"""Unit tests for civic/origin eligibility rules."""
import unittest

from extractor.clausewitz import IfBlock
from extractor.government import _civic_requirements, _granted_traits, ascension_restrictions
from extractor.traits import _trait_requirements


class TestNomadicGate(unittest.TestCase):
    def test_nomads_only(self):
        self.assertIn(('gameplay', 'require', 'nomadic', 0),
                      _civic_requirements({'possible': {'is_nomadic': 'yes'}}))

    def test_settled_only(self):
        self.assertIn(('gameplay', 'forbid', 'nomadic', 0),
                      _civic_requirements({'potential': {'is_nomadic': 'no'}}))

    def test_no_gate_means_both(self):
        rows = _civic_requirements({'possible': {'ethics': {'value': 'ethic_xenophile'}}})
        self.assertFalse([r for r in rows if r[0] == 'gameplay'])

    def test_gate_in_both_blocks_is_one_row(self):
        rows = _civic_requirements({'potential': {'is_nomadic': 'no'},
                                    'possible': {'is_nomadic': 'no'}})
        self.assertEqual([r for r in rows if r[0] == 'gameplay'],
                         [('gameplay', 'forbid', 'nomadic', 0)])


# Shaped like civic_tankbound in common/governments/civics/00_civics.txt.
TANKBOUND = {
    'possible': {'species_class': {'OR': {'text': 'tankbound_species_class_requirement',
                                          'value': ['NECROID', 'TOX', 'AQUATIC', 'INF']}}},
    'traits': {'trait': 'trait_tankbound'},
}


class TestSpeciesGates(unittest.TestCase):
    def test_species_class_or_set(self):
        rows = _civic_requirements(TANKBOUND)
        self.assertEqual(sorted(r[2] for r in rows if r[0] == 'species_class'),
                         ['AQUATIC', 'INF', 'NECROID', 'TOX'])
        self.assertTrue(all(r[1] == 'require_any' for r in rows))

    def test_tooltip_text_is_not_a_value(self):
        rows = _civic_requirements(TANKBOUND)
        self.assertFalse([r for r in rows if 'tankbound' in r[2]])

    def test_archetype_forbid(self):
        rows = _civic_requirements(
            {'possible': {'species_archetype': {'NOT': {'value': 'MACHINE'}}}})
        self.assertEqual(rows, [('archetype', 'forbid', 'MACHINE', 0)])

    def test_repeated_gate_block_is_read(self):
        # Two species_archetype blocks in one possible → promoted to a list (Reanimated Armies).
        rows = _civic_requirements({'possible': {'species_archetype': [
            {'NOT': {'text': 'x', 'value': 'ROBOT'}}, {'NOT': {'value': 'MACHINE'}}]}})
        self.assertEqual(rows, [('archetype', 'forbid', 'ROBOT', 0),
                                ('archetype', 'forbid', 'MACHINE', 0)])

    def test_homeworld_and_trait_gates(self):
        rows = _civic_requirements({'possible': {
            'preferred_planet_class': {'NOT': {'value': 'pc_volcanic'}},
            'traits': {'NOT': {'value': 'trait_aquatic'}}}})
        self.assertIn(('planet_class', 'forbid', 'pc_volcanic', 0), rows)
        self.assertIn(('trait', 'forbid', 'trait_aquatic', 0), rows)


class TestGrantedTraits(unittest.TestCase):
    def test_single(self):
        self.assertEqual(_granted_traits(TANKBOUND), ['trait_tankbound'])

    def test_several(self):
        self.assertEqual(_granted_traits({'traits': {'trait': ['a', 'b']}}), ['a', 'b'])

    def test_none_or_empty(self):
        self.assertEqual(_granted_traits({}), [])
        self.assertEqual(_granted_traits({'traits': {}}), [])


class TestTraitGates(unittest.TestCase):
    def test_forbidden_origins(self):
        rows = _trait_requirements({'forbidden_origins': ['origin_default_nomads', 'origin_x']})
        self.assertEqual(rows, [('origin', 'forbid', 'origin_default_nomads', 0),
                                ('origin', 'forbid', 'origin_x', 0)])

    def test_allowed_lists_are_separate_or_sets(self):
        rows = _trait_requirements({'allowed_origins': 'origin_unplugged',
                                    'allowed_planet_classes': ['pc_ark', 'pc_habitat']})
        self.assertEqual(rows, [('origin', 'require_any', 'origin_unplugged', 1),
                                ('planet_class', 'require_any', 'pc_ark', 2),
                                ('planet_class', 'require_any', 'pc_habitat', 2)])

    def test_unrelated_fields_ignored(self):
        self.assertEqual(_trait_requirements({'allowed_archetypes': ['BIOLOGICAL']}), [])


# Shaped like common/ascension_perks/00_ascension_paths.txt: every path perk shares the
# same gates unless a test overrides one.
TRIGGERS = {
    'is_natural_design_empire': {'OR': {'has_valid_civic': ['civic_natural_design',
                                                            'civic_hive_natural_design']}},
    'is_chosen_empire': {'OR': {'has_valid_civic': 'civic_chosen'}},
}
PATHS = ('ap_engineered_evolution', 'ap_the_flesh_is_weak', 'ap_synthetic_evolution',
         'ap_mind_over_matter')


def perks(**overrides):
    base = {'potential': {'NOR': {'has_origin': 'origin_banned'},
                          'is_natural_design_empire': 'no'}}
    return {ap: overrides.get(ap, base) for ap in PATHS}


class TestAscensionRestrictions(unittest.TestCase):
    def test_nor_listed_origin_blocked_everywhere_without_agenda_gets_nothing(self):
        # Auto-ascension origins need the agenda file to name their path.
        self.assertNotIn('origin_banned', ascension_restrictions(perks(), TRIGGERS, {}))

    def test_origin_resolved_through_agenda(self):
        agendas = {'agenda_psionic_x': {'if': IfBlock({'limit': {'has_origin': 'origin_banned'}})}}
        self.assertEqual(ascension_restrictions(perks(), TRIGGERS, agendas)['origin_banned'],
                         {'lock': 'Psionic', 'blocks': []})

    def test_scripted_trigger_no_blocks_every_path_for_a_civic(self):
        r = ascension_restrictions(perks(), TRIGGERS, {})
        for civic in ('civic_natural_design', 'civic_hive_natural_design'):
            self.assertEqual(r[civic]['blocks'], ['Genetic', 'Cybernetic', 'Synthetic', 'Psionic'])

    def test_positive_mention_in_or_allows_that_path(self):
        psionic = {'potential': {'is_chosen_empire': 'no'},
                   'possible': {'OR': {'is_chosen_empire': 'yes', 'has_country_flag': 'x'}}}
        gated = {'potential': {'is_chosen_empire': 'no'}}
        r = ascension_restrictions(perks(ap_engineered_evolution=gated, ap_the_flesh_is_weak=gated,
                                         ap_synthetic_evolution=gated, ap_mind_over_matter=psionic),
                                   TRIGGERS, {})
        self.assertEqual(r['civic_chosen'], {'lock': 'Psionic', 'blocks': []})

    def test_if_limit_is_not_a_gate(self):
        cond = {'possible': {'if': IfBlock({'limit': {'NOT': {'has_origin': 'origin_clone'}}})}}
        self.assertNotIn('origin_clone',
                         ascension_restrictions(perks(ap_engineered_evolution=cond), TRIGGERS, {}))

    def test_negation_inside_an_or_branch_is_not_a_ban(self):
        alt = {'potential': {'OR': {'has_shroud_dlc': 'yes',
                                    'AND': {'NOT': {'has_valid_civic': 'civic_maybe'}}}}}
        self.assertNotIn('civic_maybe',
                         ascension_restrictions(perks(ap_mind_over_matter=alt), TRIGGERS, {}))

    def test_one_blocked_path_lists_it(self):
        one = {'potential': {'NOT': {'has_valid_civic': 'civic_galvanic'}}}
        r = ascension_restrictions(perks(ap_synthetic_evolution=one), TRIGGERS, {})
        self.assertEqual(r['civic_galvanic'], {'lock': None, 'blocks': ['Synthetic']})


if __name__ == '__main__':
    unittest.main()
