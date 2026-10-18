#!/usr/bin/env python3
"""Unit tests for species rights' triggers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.rights import unwrap_from
from extractor.triggers import normalize_trigger


class TestRights(unittest.TestCase):
    def test_from_is_folded_in(self):
        block = Parser(tokenize(
            'exists = from from = { is_gestalt = no } NOT = { has_trait = trait_hive_mind }')).parse()
        self.assertEqual(normalize_trigger(unwrap_from(block)), {'op': 'and', 'args': [
            {'leaf': 'is_gestalt', 'value': 'no'},
            {'op': 'not', 'args': [{'leaf': 'has_trait', 'value': 'trait_hive_mind'}]}]})


if __name__ == '__main__':
    unittest.main()
