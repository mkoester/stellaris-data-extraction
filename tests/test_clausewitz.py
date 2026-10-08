#!/usr/bin/env python3
"""Unit tests for the Clausewitz tokenizer/parser."""
import tempfile
import unittest
from pathlib import Path

from extractor.clausewitz import Cmp, Parser, parse_file, tokenize


class TestTokenize(unittest.TestCase):
    def test_strips_comments(self):
        self.assertEqual(tokenize("a = 1 # comment\nb = 2"),
                         ['a', '=', '1', 'b', '=', '2'])

    def test_quoted_strings_kept_whole(self):
        self.assertEqual(tokenize('name = "Prikkiki-Ti"'),
                         ['name', '=', '"Prikkiki-Ti"'])

    def test_scripted_variable_is_one_token(self):
        self.assertEqual(tokenize('cost = @base_cost'),
                         ['cost', '=', '@base_cost'])


class TestParser(unittest.TestCase):
    def parse(self, text: str):
        return Parser(tokenize(text)).parse()

    def test_nested_blocks(self):
        data = self.parse("outer = { inner = { key = value } }")
        self.assertEqual(data, {'outer': {'inner': {'key': 'value'}}})

    def test_duplicate_keys_promote_to_list(self):
        data = self.parse("a = { swap = { x = 1 } swap = { x = 2 } }")
        self.assertEqual(data['a']['swap'], [{'x': '1'}, {'x': '2'}])

    def test_bare_list_block(self):
        data = self.parse('set = { humanoid_01 humanoid_02 }')
        self.assertEqual(data['set'], ['humanoid_01', 'humanoid_02'])

    def test_block_without_equals(self):
        # Clausewitz allows `key { }` at top level (e.g. named_colors)
        data = self.parse('colors { red = { 1 0 0 } }')
        self.assertEqual(data['colors'], {'red': ['1', '0', '0']})

    def test_comparison_kept_beside_assignments(self):
        # Previously the comparison turned the block into a token list, dropping fail_text.
        data = self.parse('t = { fail_text = "x" num_ascension_perks > 2 years_passed >= 5 }')
        self.assertEqual(data['t'], {'fail_text': 'x',
                                     'num_ascension_perks': Cmp('>', '2'),
                                     'years_passed': Cmp('>=', '5')})

    def test_quotes_stripped_from_values(self):
        data = self.parse('name = "Great Khan"')
        self.assertEqual(data, {'name': 'Great Khan'})


class TestParseFile(unittest.TestCase):
    def test_reads_utf8_sig(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'f.txt'
            p.write_bytes('﻿key = value\n'.encode('utf-8'))
            self.assertEqual(parse_file(p), {'key': 'value'})


if __name__ == '__main__':
    unittest.main()
