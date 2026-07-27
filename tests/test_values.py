#!/usr/bin/env python3
"""Unit tests for generic value helpers, trait cost parsing, and SQL rendering."""
import unittest

from extractor.traits import _parse_cost
from extractor.values import (_as_blocks, _find_first, _flatten_text,
                              _resolve_modifier_float, _resolve_var, _to_list)
from extractor.writer import Writer, _sql_str


class TestToList(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(_to_list(None), [])

    def test_string_wraps(self):
        self.assertEqual(_to_list('a'), ['a'])

    def test_nested_list_flattens_one_level(self):
        self.assertEqual(_to_list(['a', ['b', 'c']]), ['a', 'b', 'c'])

    def test_dict_gives_keys(self):
        self.assertEqual(_to_list({'x': 1, 'y': 2}), ['x', 'y'])


class TestAsBlocks(unittest.TestCase):
    def test_single_dict_wrapped(self):
        self.assertEqual(_as_blocks({'a': 1}), [{'a': 1}])

    def test_list_keeps_only_dicts(self):
        self.assertEqual(_as_blocks([{'a': 1}, 'noise']), [{'a': 1}])

    def test_scalar_is_empty(self):
        self.assertEqual(_as_blocks('x'), [])


class TestResolveModifierFloat(unittest.TestCase):
    def test_plain_number_string(self):
        self.assertEqual(_resolve_modifier_float('0.15'), 0.15)

    def test_list_takes_last(self):
        self.assertEqual(_resolve_modifier_float(['1', '2']), 2.0)

    def test_non_numeric_is_none(self):
        self.assertIsNone(_resolve_modifier_float('@var'))


class TestResolveVar(unittest.TestCase):
    def test_variable_lookup(self):
        self.assertEqual(_resolve_var('@cost', {'@cost': 3.0}), 3.0)

    def test_unknown_variable_is_none(self):
        self.assertIsNone(_resolve_var('@missing', {}))

    def test_literal_falls_through(self):
        self.assertEqual(_resolve_var('2.5', {}), 2.5)


class TestFindFirst(unittest.TestCase):
    def test_depth_first(self):
        node = {'a': {'b': {'target': 'found'}}, 'target': 'shadowed?'}
        self.assertEqual(_find_first(node, 'target'), 'shadowed?')

    def test_inside_list(self):
        self.assertEqual(_find_first([{'x': 1}, {'target': 2}], 'target'), 2)

    def test_missing_is_none(self):
        self.assertIsNone(_find_first({'a': 1}, 'target'))


class TestFlattenText(unittest.TestCase):
    def test_nested_dict(self):
        self.assertEqual(_flatten_text({'has_ethic': 'ethic_xenophile'}),
                         'has_ethic = ethic_xenophile')


class TestParseCost(unittest.TestCase):
    def test_plain_int(self):
        self.assertEqual(_parse_cost('2'), 2)

    def test_base_block(self):
        self.assertEqual(_parse_cost({'base': '3'}), 3)

    def test_non_numeric_is_none(self):
        self.assertIsNone(_parse_cost('@overtuned_cost'))


class TestSqlRendering(unittest.TestCase):
    def test_string_escaping(self):
        self.assertEqual(_sql_str("O'Brien"), "'O''Brien'")

    def test_render_placeholders(self):
        w = Writer(None, None, stdout=True)
        rendered = w._render("INSERT INTO t VALUES (?, ?, ?)", (1, "it's", None))
        self.assertEqual(rendered, "INSERT INTO t VALUES (1, 'it''s', NULL)")


if __name__ == '__main__':
    unittest.main()
