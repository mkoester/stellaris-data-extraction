#!/usr/bin/env python3
"""Unit tests for the outcome trees of anomalies, special projects and dig sites."""
import unittest

from extractor.game_start import parse_ordered
from extractor.outcomes import Outcomes, _Ctx, definition_index, event_index

# Shaped like anomaly.2010 and anomaly.2015/.2020 (events/anomaly_events_4.txt, 4.5).
EVENTS = """
@tier1researchreward = 12
ship_event = {
    id = flags.1
    title = "flags.1.name"
    immediate = { set_country_flag = not_ours }
    option = {
        name = OK
        owner = { set_country_flag = found_thing set_country_flag = $NAME$ set_country_flag = found_thing }
        owner = { set_country_flag = { flag = timed_thing days = 360 } }
        from = { set_country_flag = their_flag }
    }
}
ship_event = {
    id = anomaly.2010
    title = "anomaly.2010.name"
    immediate = { from = { clear_deposits = yes } }
    option = {
        name = anomaly.2010.a
        enable_special_project = { name = "BOTANICAL_GARDEN_PROJECT" location = FROM owner = root }
    }
    option = {
        name = anomaly.2010.b
        owner = { add_monthly_resource_mult = { resource = society_research value = @tier1researchreward min = 250 max = 100000 } }
    }
}
ship_event = {
    id = anomaly.2015
    title = "PROJECT_COMPLETE"
    option = { name = OK leader = { add_experience = 200 } }
}
ship_event = {
    id = anomaly.2020
    hide_window = yes
    immediate = { owner = { add_resource = { minor_artifacts = 50 } } }
}
ship_event = {
    id = loop.1
    title = loop.1.name
    option = { name = OK hidden_effect = { ship_event = { id = loop.1 days = 400 } } }
}
ship_event = {
    id = orb.1
    title = orb.1.name
    option = { name = orb.1.a }
    option = {
        name = orb.1.b
        trigger = { owner = { has_civic = civic_hive_devouring_swarm } leader = { has_trait = x } }
        owner = { add_resource = { food = 100 } }
    }
}
country_event = {
    id = gate.1
    title = gate.1.name
    trigger = { is_gestalt = no }
    option = { name = OK if = { limit = { has_ethic = ethic_xenophile } add_resource = { unity = 10 } } else = { add_resource = { energy = 10 } } }
}
country_event = {
    id = story.5
    title = story.5.name
    option = { name = OK add_resource = { energy = 1 } }
}
country_event = {
    id = later.1
    hide_window = yes
    immediate = { country_event = { id = later.2 days = 36000 random = 180 } }
}
country_event = {
    id = later.2
    hide_window = yes
    immediate = { add_resource = { physics_research = 500 } }
}
"""
# Shaped like small_artifact_reward and add_expertise_trait_to_leader_or_research_node_effect.
EFFECTS = """
small_artifact_reward = {
    if = {
        limit = { has_ancrel = yes }
        locked_random_list = {
            33 = { owner = { add_resource = { minor_artifacts = 50 } } }
            33 = { owner = { add_resource = { minor_artifacts = 150 } } }
        }
    }
}
give_trait = { leader = { add_trait = { trait = $TRAIT$ } } }
"""
LOC = {'anomaly.2010.name': 'Botanical Paradise', 'anomaly.2010.a': 'Explore it.',
       'anomaly.2010.b': 'Abandon it.', 'PROJECT_COMPLETE': 'Special Project Completed',
       'OK': 'OK', 'BOTANICAL_GARDEN_PROJECT': 'Explore The Botanical Paradise',
       'leader_trait_carefree': 'Carefree', 'story.5.name': 'Contact Report'}


def make(shared=frozenset()):
    idx = event_index(EVENTS)
    variables = {k: float(v) for k, v in idx.items() if k.startswith('@')}
    events = {k: v for k, v in idx.items() if not k.startswith('@')}
    return Outcomes(events, definition_index(EFFECTS), LOC, variables, shared=shared)


def block(text):
    return parse_ordered(text)[0][2]


class TestConditions(unittest.TestCase):
    def test_ship_option_keeps_only_the_owner_part(self):
        (ev,) = make().root('orb.1', 'anomaly')
        self.assertNotIn('c', ev['o'][0])
        self.assertEqual(ev['o'][1]['c'], {'op': 'and', 'args': [
            {'leaf': 'has_civic', 'value': 'civic_hive_devouring_swarm'},
            {'leaf': 'complex', 'value': 'yes'}]})

    def test_country_event_trigger_and_if_limits(self):
        (ev,) = make().root('gate.1')
        self.assertEqual(ev['c'], {'leaf': 'is_gestalt', 'value': 'no'})
        (node,) = ev['o'][0]['e']
        self.assertEqual(node['c'], [{'leaf': 'has_ethic', 'value': 'ethic_xenophile'}, None])

    def test_unknown_scope_writes_no_condition(self):
        tree = make().root(block('x = { if = { limit = { has_ethic = e } add_resource = { a = 1 } } }'))
        self.assertNotIn('c', tree[0])
        tree = make().root(block('x = { if = { limit = { has_ethic = e } add_resource = { a = 1 } } }'),
                           scope='country')
        self.assertEqual(tree[0]['c'], [{'leaf': 'has_ethic', 'value': 'e'}])

    def test_every_limit_is_kept_for_display(self):
        # Living Metal Marvel's shape (strange_worlds.2050): a planet's limit asks nothing of us
        tree = make().root(block('x = { if = { limit = { has_deposit_for = shipclass_research_station } '
                                 'clear_deposits = yes } }'))
        self.assertNotIn('c', tree[0])
        self.assertEqual(tree[0]['d'], [{'leaf': 'has_deposit_for',
                                         'value': 'shipclass_research_station'}])
        tree = make().root(block('x = { if = { limit = { has_ethic = e } add_resource = { a = 1 } } '
                                 'else = { add_resource = { a = 2 } } }'))
        self.assertEqual(tree[0]['d'], [{'leaf': 'has_ethic', 'value': 'e'}, None])

    def test_a_ship_condition_on_the_ship_alone_is_dropped(self):
        on_success = block('on_success = { 0 = { modifier = { add = 1 leader = { x = y } } '
                           'anomaly_event = anomaly.2015 } 1 = anomaly.2020 }')
        (r,) = make().root(on_success, 'anomaly')
        self.assertNotIn('c', r['b'][0])


class TestOutcomes(unittest.TestCase):
    def test_event_with_options(self):
        tree = make().root('anomaly.2010', 'anomaly')
        self.assertEqual(tree, [{
            't': 'event', 'id': 'anomaly.2010', 'title': 'Botanical Paradise',
            'e': [{'t': 'clear_deposits'}],
            'o': [{'name': 'Explore it.', 'cond': False,
                   'e': [{'t': 'project', 'k': 'BOTANICAL_GARDEN_PROJECT',
                          'name': 'Explore The Botanical Paradise'}]},
                  {'name': 'Abandon it.', 'cond': False,
                   'e': [{'t': 'months', 'r': 'society_research', 'n': 12, 'min': 250,
                          'max': 100000}]}]}])

    def test_project_random_list_and_hidden_event(self):
        # BOTANICAL_GARDEN_PROJECT's on_success; a hidden event's effects are spliced in
        on_success = block('on_success = { random_list = { 50 = { ship_event = { id = anomaly.2015 } } '
                           '50 = { ship_event = { id = anomaly.2020 } } } }')
        (r,) = make().root(on_success)
        self.assertEqual(r['t'], 'random')
        self.assertEqual([b['w'] for b in r['b']], [50, 50])
        self.assertEqual(r['b'][0]['e'][0]['title'], 'Special Project Completed')
        self.assertEqual(r['b'][1]['e'], [{'t': 'res', 'r': 'minor_artifacts', 'n': 50}])

    def test_weighted_anomaly_outcomes(self):
        # AST_CLUSTER_CAT: 0 plus 1 when the modifier holds, beside a plain 1
        on_success = block('on_success = { 0 = { modifier = { add = 1 leader = { x = y } } '
                           'anomaly_event = anomaly.2015 } 1 = anomaly.2020 }')
        (r,) = make().root(on_success, 'anomaly')
        self.assertEqual([(b['w'], b['cond'], b.get('w_if')) for b in r['b']],
                         [(0, True, 1), (1, False, None)])

    def test_scripted_effects_with_parameters_and_locked_lists(self):
        tree = make().root(block('x = { small_artifact_reward = yes give_trait = { TRAIT = leader_trait_carefree } }'))
        self.assertEqual(tree[0]['t'], 'if')
        self.assertEqual(tree[0]['b'][0][0]['t'], 'random')
        self.assertEqual(tree[1], {'t': 'trait', 'k': 'leader_trait_carefree', 'name': 'Carefree'})

    def test_cycle_becomes_ref(self):
        (ev,) = make().root('loop.1')
        self.assertEqual(ev['o'][0]['e'], [{'t': 'ref', 'id': 'loop.1', 'title': None}])

    def test_shared_side_event_is_a_ref(self):
        # story.5 (the first-contact catalogue) fired from 175 places in 4.5
        tree = make(shared=frozenset({'story.5'})).root('anomaly.2010')
        self.assertEqual(tree[0]['id'], 'anomaly.2010')   # a root is never a side event
        nested = make(shared=frozenset({'story.5'})).walk(
            block('x = { country_event = { id = story.5 } }'), 1, _Ctx())
        self.assertEqual(nested, [{'t': 'ref', 'id': 'story.5', 'title': 'Contact Report',
                                   'shared': True}])

    def test_later(self):
        self.assertEqual(make().root('later.1'),
                         [{'t': 'later', 'days': 36090,
                           'e': [{'t': 'res', 'r': 'physics_research', 'n': 500}]}])

    def test_title_from_title_key(self):
        self.assertEqual(make().title('anomaly.2015'), 'Special Project Completed')



class TestFlags(unittest.TestCase):
    def test_country_flags_in_our_scope_once(self):
        oc = make()
        on_success = block('x = { ship_event = { id = flags.1 } }')
        tree, flags = oc.collect(lambda: oc.root(on_success, 'anomaly'))
        self.assertEqual(tree[0]['id'], 'flags.1')
        # the owner's flags, once; not the ship's, a $param$, or another country's
        self.assertEqual(flags, [{'k': 'found_thing'}, {'k': 'timed_thing'}])
        self.assertIsNone(oc.flags)

    def test_chapter_and_nothing_outside_collect(self):
        oc = make()
        _, flags = oc.collect(lambda: oc.root('flags.1', 'anomaly'), 2)
        self.assertEqual(flags[0], {'k': 'found_thing', 'ch': 2})
        oc.root('flags.1', 'anomaly')   # no collector: nothing to record into
        self.assertIsNone(oc.flags)


if __name__ == '__main__':
    unittest.main()
