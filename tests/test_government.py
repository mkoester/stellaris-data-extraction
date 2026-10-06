#!/usr/bin/env python3
"""Unit tests for civic/origin eligibility rules."""
import unittest

from extractor.government import _civic_requirements


class TestNomadicGate(unittest.TestCase):
    def test_nomads_only(self):
        self.assertIn(('gameplay', 'require', 'nomadic', 0),
                      _civic_requirements({'possible': {'is_nomadic': 'yes'}}))

    def test_settled_only(self):
        self.assertIn(('gameplay', 'forbid', 'nomadic', 0),
                      _civic_requirements({'potential': {'is_nomadic': 'no'}}))

    def test_no_gate_means_both(self):
        rows = _civic_requirements({'possible': {'ethics': {'value': 'ethic_xenophile'}}})
        self.assertFalse([r for r in rows if r[0] == 'gameplay'])

    def test_gate_in_both_blocks_is_one_row(self):
        rows = _civic_requirements({'potential': {'is_nomadic': 'no'},
                                    'possible': {'is_nomadic': 'no'}})
        self.assertEqual([r for r in rows if r[0] == 'gameplay'],
                         [('gameplay', 'forbid', 'nomadic', 0)])


if __name__ == '__main__':
    unittest.main()
