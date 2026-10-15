import unittest

from extractor.tech import tech_base_cost


class TestTechBaseCost(unittest.TestCase):
    VARS = {'@tier1cost3': 1500.0}

    def test_variable_number_and_factor_block(self):
        self.assertEqual(tech_base_cost('@tier1cost3', self.VARS), 1500.0)
        self.assertEqual(tech_base_cost('0', self.VARS), 0.0)
        block = {'factor': '@tier1cost3', 'inline_script': {'script': 'x'}}
        self.assertEqual(tech_base_cost(block, self.VARS), 1500.0)

    def test_unresolvable_is_none(self):
        self.assertIsNone(tech_base_cost('@unknown', self.VARS))
        self.assertIsNone(tech_base_cost(None, self.VARS))
        self.assertIsNone(tech_base_cost({'inline_script': {}}, self.VARS))


if __name__ == '__main__':
    unittest.main()
