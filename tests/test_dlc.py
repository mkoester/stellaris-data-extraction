#!/usr/bin/env python3
"""Unit tests for the dlc table rows (dlc_info.txt merged with dlc/*/*.dlc descriptors)."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.misc import _dlc_rows, host_checks

# Shaped like dlc_metadata/dlc_info.txt (note the missing `=` the real file has once).
INFO = """
dlcs = {
	dlc001 = { name = "Symbols of Domination" steam_id = "447680" affects_gameplay = no }
	dlc029 = { name = "First Contact Story Pack" steam_id = "2277860" paradoxplaza_store_url "" affects_gameplay = yes }
}
"""
# Shaped like dlc/dlc029_firstcontact/dlc029.dlc.
DESCRIPTOR = """
name = "First Contact Story Pack"
steam_id = 2277860
category = "story_pack"
"""


def parse(text):
    return Parser(tokenize(text)).parse()


class TestDlcRows(unittest.TestCase):
    def test_merges_category_and_date(self):
        rows = {r[0]: r for r in _dlc_rows(parse(INFO), [parse(DESCRIPTOR)])}
        self.assertEqual(rows['First Contact Story Pack'],
                         ('First Contact Story Pack', 'dlc029', '2277860', 1, 'story_pack', '2023-03-14', 0))

    def test_unlisted_dlc_has_no_category_or_date(self):
        rows = {r[0]: r for r in _dlc_rows(parse(INFO), [parse(DESCRIPTOR)])}
        self.assertEqual(rows['Symbols of Domination'][3:], (0, None, None, 0))

    def test_host_checks_counts_each_dlc(self):
        texts = ['potential = { host_has_dlc = "Megacorp" }', 'host_has_dlc="Megacorp" host_has_dlc = "Utopia2"']
        self.assertEqual(host_checks(texts, ['Megacorp', 'Utopia']), {'Megacorp': 2, 'Utopia': 0})


if __name__ == '__main__':
    unittest.main()
