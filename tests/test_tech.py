import unittest

from extractor.tech import prerequisite_blocks, tech_base_cost, tech_requirement


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
                         ('["tech_sensors_4"]', '[]', None, 0, 0))
        req = tech_requirement({'prerequisites': ['"tech_b"', '"tech_a"'], 'is_rare': 'yes',
                                'potential': {'is_gestalt': 'yes'}}, None, {})
        self.assertEqual(req[0], '["tech_a", "tech_b"]')
        self.assertIn('is_gestalt', req[2])
        self.assertEqual(req[3:], (1, 0))

    def test_none(self):
        self.assertEqual(tech_requirement({}, None, {}), ('[]', '[]', None, 0, 0))

    def test_or_groups_beside_plain_prerequisites(self):
        # 4.5.2 tech_mega_engineering: the dict parser kept none of these
        text = '''tech_mega_engineering = {
            area = engineering
            prerequisites = {
                OR = { tech_starbase_5 tech_arkship_tier_3 }
                tech_zero_point_power
                OR = { tech_stingers tech_battleships }
            }
        }
        tech_lasers_2 = { prerequisites = { "tech_lasers_1" } }'''
        blocks = prerequisite_blocks(text)
        self.assertEqual(tech_requirement({}, None, {}, blocks['tech_mega_engineering'])[:2],
                         ('["tech_zero_point_power"]',
                          '[["tech_arkship_tier_3", "tech_starbase_5"], ["tech_battleships", "tech_stingers"]]'))
        self.assertEqual(tech_requirement({}, None, {}, blocks['tech_lasers_2'])[:2],
                         ('["tech_lasers_1"]', '[]'))


if __name__ == '__main__':
    unittest.main()
