#!/usr/bin/env python3
"""Unit tests for the events a fleet's first entry into a system fires."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.system_events import (_desc_key, expand_dotted, from_star_flags, on_action_events,
                                     once_flag)


def parse(text: str) -> dict:
    return Parser(tokenize(text)).parse_block()


# Shaped like leviathans.3202's trigger (events/leviathans_events_2.txt, 4.5.3).
HIVE = """
trigger = {
	NOT = { has_global_flag = hivers_appeared }
	owner = { is_ai = no }
	FROM = {
		has_star_flag = guardians_hive_system
		NOT = { has_star_flag = hivers_defeated }
	}
}
"""


class SystemEntryEvents(unittest.TestCase):
    def test_on_action_lists_its_events_once_in_order(self):
        data = parse('on_entering_system_first_time = { events = { a.1 b.2 a.1 } }'
                     ' on_entering_system_first_time = { events = { c.3 } }'
                     ' on_other = { events = { d.4 } }')
        self.assertEqual(on_action_events(data), ['a.1', 'b.2', 'c.3'])

    def test_flags_asked_of_the_system_entered(self):
        trigger = parse(HIVE)['trigger']
        self.assertEqual(from_star_flags(trigger),
                         (['guardians_hive_system'], [], ['hivers_defeated']))
        # fircon.5005: both flags (Helitio's Solarpunk borders, once revealed)
        trigger = parse('trigger = { from = { has_star_flag = solarpunk_system_01'
                        ' has_star_flag = solarpunk_system_revealed } }')['trigger']
        self.assertEqual(from_star_flags(trigger),
                         (['solarpunk_system_01', 'solarpunk_system_revealed'], [], []))

    def test_alternatives_and_flags_outside_from(self):
        # enclave.100: either tunnel flag; a flag on another scope does not count
        trigger = parse('trigger = { FROM = { OR = { has_star_flag = shroud_tunnel_nexus'
                        ' has_star_flag = shroud_tunnel_node } }'
                        ' owner = { capital_scope = { solar_system = { has_star_flag = home } } }'
                        ' FROM = { any_system_planet = { has_star_flag = nope } } }')['trigger']
        self.assertEqual(from_star_flags(trigger),
                         ([], ['shroud_tunnel_nexus', 'shroud_tunnel_node'], []))
        # NOR lists what the system must not have
        trigger = parse('trigger = { FROM = { NOR = { has_star_flag = a has_star_flag = b } } }')
        self.assertEqual(from_star_flags(trigger['trigger']), ([], [], ['a', 'b']))
        self.assertEqual(from_star_flags(None), ([], [], []))

    def test_first_plain_description(self):
        self.assertEqual(_desc_key('x.desc'), 'x.desc')
        desc = parse('desc = { trigger = { a = b } text = x.desc.one } desc = { text = x.desc.two }')
        self.assertEqual(_desc_key(desc['desc']), 'x.desc.one')
        self.assertIsNone(_desc_key(None))

    def test_dotted_loc_refs(self):
        loc = {'leviathans.3202.base': 'The $x.y$ belt.', 'x.y': 'dense'}
        self.assertEqual(expand_dotted('$leviathans.3202.base$\n\nHowever', loc),
                         'The dense belt.\n\nHowever')
        self.assertEqual(expand_dotted('$no.such$ $PLAIN$', loc), '$no.such$ $PLAIN$')

    def test_once_per_empire(self):
        # apoc.1 (events/apocalypse_events.txt, 4.5.3): only the first gateway found
        apoc = parse('''e = {
            trigger = { owner = { NOT = { has_country_flag = encountered_first_gateway } }
                        FROM = { has_star_flag = abandoned_gateway } }
            immediate = { owner = { set_country_flag = encountered_first_gateway } }
        }''')['e']
        self.assertEqual(once_flag(apoc), 'encountered_first_gateway')
        # a flag tested but set elsewhere, or set but not tested, is no guard of its own
        other = parse('''e = {
            trigger = { owner = { NOT = { has_country_flag = a } } }
            option = { owner = { set_timed_country_flag = { flag = a days = 3 } set_country_flag = b } }
        }''')['e']
        self.assertIsNone(once_flag(other))
        # the timed form counts when it is the guard's flag
        timed = parse('''e = { trigger = { NOT = { owner = { has_country_flag = c } } }
            immediate = { owner = { set_country_flag = { flag = c } } } }''')['e']
        self.assertEqual(once_flag(timed), 'c')


if __name__ == '__main__':
    unittest.main()
