#!/usr/bin/env python3
"""Unit tests for the pure specimen helpers."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.specimens import flag, modifier_rows, produced, rarity


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse()


SPORES = parse('''s = {
    icon = "GFX_specimen_acidic_spores"
    type = xeno_geology
    inline_script = { script = "grand_archive/set_specimen_rarity" RARITY = common }
    triggered_country_modifier = {
        mult = value:xeno_geology_specimens_modifiers_mult
        potential = { is_lithoid = no is_infernal = no }
        planet_jobs_food_produces_mult = 0.02
    }
    triggered_country_modifier = {
        mult = value:xeno_geology_specimens_modifiers_mult
        planet_stability_add = 1
    }
    resources = { produces = { unity = 5 } }
}''')['s']


class TestSpecimenHelpers(unittest.TestCase):
    def test_rarity_from_the_inline_script(self):
        self.assertEqual(rarity(SPORES), 'common')
        self.assertIsNone(rarity({'inline_script': {'script': 'other', 'RARITY': 'epic'}}))

    def test_flags_default_to_yes(self):
        self.assertEqual(flag(SPORES, 'is_tradable'), 1)
        self.assertEqual(flag({'is_tradable': 'no'}, 'is_tradable'), 0)

    def test_modifiers_with_their_potential_and_without_the_mult(self):
        self.assertEqual(modifier_rows(SPORES), [
            ('planet_jobs_food_produces_mult', 0.02, 'is_lithoid = no, is_infernal = no'),
            ('planet_stability_add', 1.0, None),
        ])

    def test_resources_produced(self):
        self.assertEqual(produced(SPORES), [('unity', 5.0)])
        self.assertEqual(produced({}), [])


if __name__ == '__main__':
    unittest.main()
