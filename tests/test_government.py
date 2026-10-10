#!/usr/bin/env python3
"""Unit tests for civic/origin eligibility rules."""
import unittest

from extractor.government import _civic_requirements, _granted_traits
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


if __name__ == '__main__':
    unittest.main()
