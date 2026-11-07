import unittest

from extractor.tech_unlocks import objects_of, tech_refs, unlock_rows

LOC = {
    'building_field_dynamics': 'Field Dynamics Center',
    'SHIELD_2': 'Improved Deflectors',
    'corvette': 'Corvette',
    'allow_dyson_swarm': '§H$UNLOCKS_MEGASTRUCTURE$:§! Dyson Swarm',
    'UNLOCKS_MEGASTRUCTURE': 'Unlocks Megastructure',
    'allow_dyson_swarm_desc': 'Harvest a star.',
    'feature_unlocks_auto_research': 'Auto-Research Technologies',
    'AUTO_EXPLORE_ORDER': 'Science Ship Automation',
    'science': 'Science Ship',
    'TECH_UNLOCK_SCIENCE': '§H$UNLOCKS_SHIP_TYPE$:§! Science Ship',
    'UNLOCKS_SHIP_TYPE': 'Unlocks Ship Type',
}


class TechUnlocksTest(unittest.TestCase):
    def test_tech_refs_reads_plain_and_grouped_prerequisites(self):
        self.assertEqual(tech_refs(['"tech_a"', {'OR': ['tech_b', 'tech_c']}, 'x']),
                         ['tech_a', 'tech_b', 'tech_c'])
        self.assertEqual(tech_refs(None), [])

    def test_component_templates_are_named_by_their_own_key(self):
        parsed = {'utility_component_template': [{'key': '"SMALL_SHIELD_2"'},
                                                 {'key': 'MEDIUM_SHIELD_2'}],
                  '@cost': 5}
        self.assertEqual([k for k, _ in objects_of(parsed)], ['SMALL_SHIELD_2', 'MEDIUM_SHIELD_2'])

    def test_rows_put_own_lines_first_fold_component_sizes_and_honour_hidden_kinds(self):
        techs = {
            'tech_physics_1': {'area': 'physics'},
            'tech_shields_2': {'area': 'physics'},
            'tech_dyson': {'area': 'engineering', 'hide_prereq_for_desc': 'ship',
                           'prereqfor_desc': {'custom': {'title': 'allow_dyson_swarm',
                                                         'desc': 'allow_dyson_swarm_desc'}}},
        }
        sources = {
            'building': [{'building_field_dynamics': {'prerequisites': ['"tech_physics_1"']}}],
            'component': [{'utility_component_template': [
                {'key': 'SMALL_SHIELD_2', 'component_set': 'SHIELD_2', 'prerequisites': ['tech_shields_2']},
                {'key': 'LARGE_SHIELD_2', 'component_set': 'SHIELD_2', 'prerequisites': ['tech_shields_2']},
            ]}],
            'ship': [{'corvette': {'prerequisites': ['tech_dyson', 'tech_unknown']}}],
        }
        self.assertEqual(unlock_rows(techs, sources, LOC), [
            ('tech_dyson', 'line', 'allow_dyson_swarm', 'Unlocks Megastructure: Dyson Swarm',
             'Harvest a star.', 0),
            ('tech_physics_1', 'building', 'building_field_dynamics', 'Field Dynamics Center', None, 0),
            ('tech_shields_2', 'component', 'SHIELD_2', 'Improved Deflectors', None, 0),
        ])

    def test_features_named_and_a_derived_object_named_by_an_own_line_left_out(self):
        techs = {'tech_space_exploration': {
            'area': 'physics',
            'feature_flags': ['tech_automated_exploration', 'unlocks_auto_research', 'no_loc'],
            'prereqfor_desc': {'ship': {'title': 'TECH_UNLOCK_SCIENCE'}}}}
        sources = {'ship': [{'science': {'prerequisites': ['tech_space_exploration']}}]}
        rows = unlock_rows(techs, sources, LOC)
        self.assertEqual([(r[1], r[3]) for r in rows], [
            ('line', 'Unlocks Ship Type: Science Ship'),
            ('feature', 'Science Ship Automation'),
            ('feature', 'Auto-Research Technologies'),
        ])


if __name__ == '__main__':
    unittest.main()
