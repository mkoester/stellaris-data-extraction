import unittest

from extractor.tech import tech_base_cost, tech_requirement


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



class TestTechRequirement(unittest.TestCase):
    def test_prerequisites_potential_and_flags(self):
        # Shaped like tech_mine_dark_matter and a gestalt-only tech.
        self.assertEqual(tech_requirement({'prerequisites': '"tech_sensors_4"'}, None, {}),
                         ('["tech_sensors_4"]', None, 0, 0))
        req = tech_requirement({'prerequisites': ['"tech_b"', '"tech_a"'], 'is_rare': 'yes',
                                'potential': {'is_gestalt': 'yes'}}, None, {})
        self.assertEqual(req[0], '["tech_a", "tech_b"]')
        self.assertIn('is_gestalt', req[1])
        self.assertEqual(req[2:], (1, 0))

    def test_none(self):
        self.assertEqual(tech_requirement({}, None, {}), ('[]', None, 0, 0))


if __name__ == '__main__':
    unittest.main()
