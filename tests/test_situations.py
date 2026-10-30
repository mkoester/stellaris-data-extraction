#!/usr/bin/env python3
"""Unit tests for situation stages and the outcome events of anomalies and special projects."""
import json
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.situations import event_ids, outcomes_json, stage_keys

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


class TestStages(unittest.TestCase):
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


class TestOutcomes(unittest.TestCase):
    def test_event_ids_in_order_once_each(self):
        block = Parser(tokenize(ANOMALY)).parse()['on_success']
        self.assertEqual(event_ids(block), ['anomaly.35', 'anomaly.65'])

    def test_project_event_and_titles(self):
        block = Parser(tokenize('on_success = { ship_event = { id = anomaly.2501 } }')).parse()
        out = json.loads(outcomes_json(block['on_success'], {'anomaly.2501.name': 'Space Whale'}))
        self.assertEqual(out, [{'event': 'anomaly.2501', 'name': 'Space Whale'}])

    def test_no_events_is_none(self):
        self.assertIsNone(outcomes_json({'add_resource': {'energy': '100'}}, {}))


if __name__ == '__main__':
    unittest.main()
