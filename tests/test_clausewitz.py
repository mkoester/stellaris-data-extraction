#!/usr/bin/env python3
"""Unit tests for the Clausewitz tokenizer/parser."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from extractor.clausewitz import Cmp, Parser, parse_file, prune_cache, tokenize, use_cache


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


class TestParseCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        use_cache(self.cache)

    def tearDown(self):
        use_cache(None)
        self.tmp.cleanup()

    def entries(self):
        return sorted(self.cache.glob('*/*.pickle'))

    def test_same_result_as_uncached_and_a_hit_the_second_time(self):
        f = self.root / 'a.txt'
        f.write_text('e = { if = { limit = { x > 2 } a = 1 }\nelse = { b = 2 } }\n')
        first = parse_file(f)
        self.assertEqual(len(self.entries()), 1)
        hit = parse_file(f)
        use_cache(None)
        plain = parse_file(f)
        self.assertEqual(first, plain)
        self.assertEqual(hit, plain)
        self.assertEqual(hit['e']['if']['limit']['x'], Cmp('>', '2'))
        self.assertEqual(hit['e']['if'].chain, plain['e']['if'].chain)  # the else chain survives the cache
        self.assertTrue(hit['e']['if'].chain)

    def test_changed_content_is_parsed_again(self):
        f = self.root / 'a.txt'
        f.write_text('a = 1')
        self.assertEqual(parse_file(f), {'a': '1'})
        f.write_text('a = 2')
        self.assertEqual(parse_file(f), {'a': '2'})
        self.assertEqual(len(self.entries()), 2)

    def test_unreadable_entry_is_a_miss(self):
        f = self.root / 'a.txt'
        f.write_text('a = 1')
        parse_file(f)
        self.entries()[0].write_bytes(b'not a pickle')
        self.assertEqual(parse_file(f), {'a': '1'})

    def test_prune_drops_unused_entries_only(self):
        old, new = self.root / 'old.txt', self.root / 'new.txt'
        old.write_text('a = 1')
        new.write_text('b = 1')
        parse_file(old)
        stale = self.entries()[0]
        past = time.time() - 61 * 86400
        os.utime(stale, (past, past))
        parse_file(new)
        self.assertEqual(prune_cache(), 1)
        self.assertEqual(len(self.entries()), 1)
        self.assertFalse(stale.exists())


if __name__ == '__main__':
    unittest.main()
