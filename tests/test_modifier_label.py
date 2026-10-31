import unittest

from extractor.modifiers import _write_modifier_label
from extractor.writer import Writer


class ModifierLabelTest(unittest.TestCase):
    def test_lower_is_better_for_costs_crime_and_devastation(self):
        w = Writer(None, ':memory:')
        loc = {'mod_planet_storm_devastation_mult': 'Planetary Cosmic Storm Devastation Reduction',
               'mod_planet_crime_add': 'Crime', 'mod_planet_stability_add': 'Stability'}
        for key in ('planet_storm_devastation_mult', 'planet_crime_add', 'planet_stability_add'):
            _write_modifier_label(w, loc, key)
        self.assertEqual(
            dict(w._conn.execute('SELECT modifier_key, positive_is_good FROM modifier_label').fetchall()),
            {'planet_storm_devastation_mult': 0, 'planet_crime_add': 0, 'planet_stability_add': 1})


if __name__ == '__main__':
    unittest.main()
