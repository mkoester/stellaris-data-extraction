#!/usr/bin/env python3
"""Unit tests for a colony designation's base modifiers and a job's budget source."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.colony_types import colony_type_modifiers
from extractor.jobs import _job_budget_source

RURAL = '''planet_modifier = { $MODIFIER$ = 0.15 }
triggered_planet_modifier = { potential = { always = yes } modifier = { $MODIFIER$ = 0.05 } }'''


def parse(text):
    return Parser(tokenize(text)).parse()


class TestColonyTypeModifiers(unittest.TestCase):
    def test_inline_script_is_filled_and_triggered_extras_left_out(self):
        cdef = parse('col_mining = { inline_script = { script = colony_types/rural '
                     'MODIFIER = miner_jobs_bonus_workforce_mult } }')['col_mining']
        load = {'colony_types/rural': RURAL}.get
        self.assertEqual(colony_type_modifiers(cdef, load, {}),
                         [('miner_jobs_bonus_workforce_mult', 0.15)])

    def test_own_planet_modifier_counts(self):
        cdef = parse('c = { planet_modifier = { pop_housing_usage_mult = -0.1 } }')['c']
        self.assertEqual(colony_type_modifiers(cdef, lambda _: None, {}),
                         [('pop_housing_usage_mult', -0.1)])


class TestJobBudgetSource(unittest.TestCase):
    def test_drone_job_books_under_the_organic_category(self):
        jdef = parse('technician_drone = { category = simple_drone resources = { '
                     'category = planet_technician produces = { energy = 4 } } '
                     'overlord_resources = { category = planet_requisitioned_energy } }')
        self.assertEqual(_job_budget_source(jdef['technician_drone'], lambda _: {}),
                         'planet_technician')

    def test_category_from_a_job_level_inline_script(self):
        scripts = {'jobs/x': parse('resources = { category = planet_telepaths }')}
        jdef = parse('telepath = { inline_script = "jobs/x" }')['telepath']
        self.assertEqual(_job_budget_source(jdef, lambda n: scripts.get(n, {})), 'planet_telepaths')

    def test_no_resources_gives_none(self):
        self.assertIsNone(_job_budget_source({'category': 'worker'}, lambda _: {}))


if __name__ == '__main__':
    unittest.main()
