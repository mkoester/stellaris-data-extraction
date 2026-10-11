#!/usr/bin/env python3
"""Unit tests for which civics/origins a building requires."""
import unittest

from extractor.buildings import building_unlocks

TRIGGERS = {'is_toxic_baths_empire': {'OR': {'has_valid_civic': ['civic_toxic_baths',
                                                                 'civic_hive_toxic_baths']}}}


class TestBuildingUnlocks(unittest.TestCase):
    def test_plain_civic_gate(self):
        b = {'potential': {'owner': {'has_valid_civic': 'civic_environmentalist'}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), ['civic_environmentalist'])

    def test_or_of_civic_variants_via_scripted_trigger(self):
        b = {'potential': {'owner': {'is_toxic_baths_empire': 'yes'}}}
        self.assertEqual(building_unlocks(b, TRIGGERS),
                         ['civic_hive_toxic_baths', 'civic_toxic_baths'])

    def test_optional_path_is_not_an_unlock(self):
        # Shaped like building_factory_1: Fear of the Dark is one alternative.
        b = {'potential': {'owner': {'OR': {'country_uses_consumer_goods': 'yes',
                                            'has_origin': 'origin_fear_of_the_dark'}}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), [])

    def test_negated_civic_is_not_an_unlock(self):
        b = {'potential': {'owner': {'NOT': {'has_valid_civic': 'civic_x'}}}}
        self.assertEqual(building_unlocks(b, TRIGGERS), [])


if __name__ == '__main__':
    unittest.main()
