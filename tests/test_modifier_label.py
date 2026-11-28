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

    def test_lower_is_better_for_cooldowns_empire_size_amenities_friction_max_price(self):
        w = Writer(None, ':memory:')
        keys = ('council_agenda_cooldown_mult', 'country_border_friction_mult',
                'country_market_max_price_mult', 'pop_amenities_usage_no_happiness_mult',
                'system_empire_size_mult', 'shipsize_titan_fallen_empire_size_damage_factor')
        loc = {f'mod_{k}': k.replace('_', ' ').title() for k in keys}
        for key in keys:
            _write_modifier_label(w, loc, key)
        got = dict(w._conn.execute('SELECT modifier_key, positive_is_good FROM modifier_label').fetchall())
        self.assertEqual(got, {**{k: 0 for k in keys[:-1]}, keys[-1]: 1})


if __name__ == '__main__':
    unittest.main()
