#!/usr/bin/env python3
"""Unit tests for trait modifiers that scale with a script value."""
import tempfile
import unittest
from pathlib import Path

from extractor.modifiers import _load_scaling_values
from extractor.traits import _trait_modifier_pairs, _trait_scaling_effects

# Shaped like common/script_values/06_script_values_biogenesis.txt.
SCRIPT_VALUES = """
hollow_bones_mult = {
	base = 2
	mult = planet.trigger:planet_size
	divide = 3
}
"""
# Shaped like trait_hollow_bones in common/traits/15_biogenesis_species_traits.txt.
HOLLOW_BONES = {
    'triggered_pop_group_modifier': {
        'potential': {'exists': 'planet'},
        'soldier_jobs_bonus_workforce_mult': '-0.01',
        'mult': 'value:hollow_bones_mult',
    },
}


class TestPlanetSizeScaling(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            sv = Path(d) / 'common' / 'script_values'
            sv.mkdir(parents=True)
            (sv / '00_test.txt').write_text(SCRIPT_VALUES)
            self.scaling = _load_scaling_values(Path(d))

    def test_rate_per_planet_size_uses_base_and_divide(self):
        # -1% × 2 × size / 3 → -0.67% per planet size, uncapped (the game's own tooltip).
        self.assertEqual(_trait_scaling_effects(HOLLOW_BONES, {}, self.scaling),
                         [('soldier_jobs_bonus_workforce_mult', -0.006667, None, 'planet size')])

    def test_not_also_counted_as_a_flat_modifier(self):
        self.assertEqual(_trait_modifier_pairs(HOLLOW_BONES, {}, {}, lambda _: {}, self.scaling), {})


if __name__ == '__main__':
    unittest.main()
