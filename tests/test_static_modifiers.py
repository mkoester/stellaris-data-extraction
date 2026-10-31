import unittest

from extractor.precursors import extract_static_modifiers
from extractor.writer import Writer


class ExtractStaticModifiersTest(unittest.TestCase):
    def run_extract(self, mods: dict, loc: dict, scripted_vars: dict | None = None) -> Writer:
        w = Writer(None, ':memory:')
        extract_static_modifiers(mods, loc, w, verbose=False, scripted_vars=scripted_vars)
        return w

    def test_scripted_variables_resolved(self):
        # 4.5.3 skill_official_planet_governor: its job output from common/scripted_variables
        w = self.run_extract(
            {'skill_official_planet_governor': {'custom_tooltip': 'tt', 'planet_jobs_energy_produces_mult': '@planet_job_mult',
                                                'species_empire_size_mult': '@unknown_var'}},
            {}, {'@planet_job_mult': 0.02})
        self.assertEqual(w._conn.execute('SELECT modifier_key, modifier_value FROM static_modifier_modifier').fetchall(),
                         [('planet_jobs_energy_produces_mult', 0.02)])

    def test_name_description_and_effects(self):
        w = self.run_extract(
            {'galactic_market_founder': {'icon': 'gfx/x.dds', 'icon_frame': '1', 'country_trade_fee': '-0.1'}},
            {'galactic_market_founder': 'Market Nexus', 'galactic_market_founder_desc': 'Hosts the market.'},
        )
        self.assertEqual(w._conn.execute('SELECT * FROM static_modifier').fetchall(),
                         [('galactic_market_founder', 'Market Nexus', 'Hosts the market.', None)])
        self.assertEqual(w._conn.execute('SELECT static_key, modifier_key, modifier_value FROM static_modifier_modifier').fetchall(),
                         [('galactic_market_founder', 'country_trade_fee', -0.1)])

    def test_tooltip_only_keeps_no_effects(self):
        w = self.run_extract(
            {'@v': '3', 'hidden': {'show_only_custom_tooltip': 'yes', 'custom_tooltip': 'hidden_tt', 'pop_happiness': '0.1'}},
            {'hidden_tt': 'Something happens.'},
        )
        self.assertEqual(w._conn.execute('SELECT key, name, tooltip FROM static_modifier').fetchall(),
                         [('hidden', None, 'Something happens.')])
        self.assertEqual(w._conn.execute('SELECT COUNT(*) FROM static_modifier_modifier').fetchone(), (0,))


if __name__ == '__main__':
    unittest.main()
