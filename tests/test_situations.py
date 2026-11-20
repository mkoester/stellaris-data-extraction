#!/usr/bin/env python3
"""Unit tests for situation stages and the outcome events of anomalies and special projects."""
import json
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.situations import (chain_counters, event_ids, is_permanent, outcomes_json, project_number, stage_keys,
                                  stage_modifiers)

# Shaped like crashed_ship_asteroid_category's on_success (common/anomalies/00_anomaly_categories.txt).
ANOMALY = """
on_success = {
	1 = anomaly.35
	1 = {
		modifier = { factor = 0 from = { has_any_strategic_resource = yes } }
		anomaly_event = anomaly.65
	}
	1 = anomaly.35
}
"""


class TestEventChains(unittest.TestCase):
    def test_counters(self):
        # cybrex_chain (common/event_chains/00_event_chains.txt); vultaum_homeworld_chain has none
        data = Parser(tokenize("""counter = { cybrex_artifacts = { max = 6 } }
            situation_log_category = precursors""")).parse()
        self.assertEqual(chain_counters(data), [{'key': 'cybrex_artifacts', 'max': 6}])
        self.assertEqual(chain_counters(Parser(tokenize('situation_log_category = precursors')).parse()), [])


class TestStages(unittest.TestCase):
    def test_permanent(self):
        # situation_nomad_economy: `permanent = yes`; most situations say nothing
        self.assertTrue(is_permanent(Parser(tokenize('permanent = yes stages = {}')).parse()))
        self.assertFalse(is_permanent(Parser(tokenize('stages = {}')).parse()))
        self.assertFalse(is_permanent(Parser(tokenize('permanent = no')).parse()))

    # shaped like situation_nomad_economy (common/situations/14_nomads_situations.txt)
    def test_file_order(self):
        data = Parser(tokenize("""stages = {
            situation_nomad_economy_stage_critical = { end = 1000 }
            situation_nomad_economy_stage_1_struggle = { end = 5000 }
        }""")).parse()
        self.assertEqual(stage_keys(data), ['situation_nomad_economy_stage_critical',
                                            'situation_nomad_economy_stage_1_struggle'])

    def test_none(self):
        self.assertEqual(stage_keys({}), [])

    # situation_nomad_economy_stage_3_thriving (14_nomads_situations.txt, 4.5.x)
    def test_stage_modifiers(self):
        stage = Parser(tokenize("""s = {
            section_weight = 25
            modifier = { planet_stability_add = 10 all_technology_research_speed = @speed }
        }""")).parse()['s']
        self.assertEqual(stage_modifiers(stage, {'@speed': 0.1}),
                         [('planet_stability_add', 10.0), ('all_technology_research_speed', 0.1)])
        self.assertEqual(stage_modifiers({'end': '1000'}, {}), [])


class TestOutcomes(unittest.TestCase):
    def test_event_ids_in_order_once_each(self):
        block = Parser(tokenize(ANOMALY)).parse()['on_success']
        self.assertEqual(event_ids(block), ['anomaly.35', 'anomaly.65'])

    def test_project_event_and_titles(self):
        block = Parser(tokenize('on_success = { ship_event = { id = anomaly.2501 } }')).parse()
        out = json.loads(outcomes_json(block['on_success'], {'anomaly.2501.name': 'Space Whale'}))
        self.assertEqual(out, [{'event': 'anomaly.2501', 'name': 'Space Whale'}])

    def test_title_from_the_event_itself(self):
        # extreme_frontiers.445's title key is `.title`, not `.name`: named by the event's own key
        block = Parser(tokenize('on_success = { ship_event = { id = extreme_frontiers.445 } }')).parse()
        out = json.loads(outcomes_json(block['on_success'], {}, lambda i: 'Orbital Slingshot Advancements'))
        self.assertEqual(out, [{'event': 'extreme_frontiers.445', 'name': 'Orbital Slingshot Advancements'}])

    def test_no_events_is_none(self):
        self.assertIsNone(outcomes_json({'add_resource': {'energy': '100'}}, {}))


if __name__ == '__main__':
    unittest.main()


class TestProjectNumber(unittest.TestCase):
    def test_forms(self):
        v = {'@tier2cost1': 1500.0}
        self.assertEqual(project_number('600', v), 600.0)
        self.assertEqual(project_number('@tier2cost1', v), 1500.0)
        self.assertEqual(project_number({'base': '2000', 'modifier': {'factor': '2'}}, v), 2000.0)
        self.assertIsNone(project_number(None, v))
        self.assertIsNone(project_number('@unknown', v))
