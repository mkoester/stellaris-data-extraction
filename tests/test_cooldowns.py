#!/usr/bin/env python3
"""Unit tests for the timers of player actions."""
import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.cooldowns import action_timers, astral_cooldowns, expand_inline, fired, gates, timers

# Shaped like artifact_arcane_deciphering (common/artifact_actions/artifact_actions.txt, 4.5.2).
ARTIFACT = """
allow = {
	custom_tooltip = {
		fail_text = "requires_arcane_deciphering_no_cooldown"
		NOT = { has_modifier = arcane_deciphering_cooldown }
	}
}
effect = {
	hidden_effect = {
		if = { limit = { NOT = { has_country_flag = arcana_achievement } } set_country_flag = arcana_achievement }
		add_modifier = { modifier = "arcane_deciphering_cooldown" days = 720 }
		set_timed_country_flag = { flag = deciphered_recently years = 2 }
		add_modifier = { modifier = permanent_one }
	}
}
"""


def parse(text):
    return Parser(tokenize(text)).parse()


class TestTimers(unittest.TestCase):
    def test_gates_are_negated_checks_only(self):
        self.assertEqual(gates(parse('allow = { has_modifier = a NOT = { has_planet_flag = b '
                                     'NOT = { has_modifier = c } } }')['allow']),
                         {('flag', 'b')})

    def test_timers_with_their_days(self):
        self.assertEqual(timers(parse(ARTIFACT)['effect']),
                         {('modifier', 'arcane_deciphering_cooldown'): 720,
                          ('flag', 'deciphered_recently'): 720})

    def test_cooldown_and_effect_roles(self):
        d = parse(ARTIFACT)
        self.assertEqual(action_timers([d['allow']], [d['effect']]), [
            ('flag', 'deciphered_recently', 'effect', 720),
            ('modifier', 'arcane_deciphering_cooldown', 'cooldown', 720)])


# Shaped like action_insult's on_accept (common/diplomatic_actions/00_actions.txt, 4.5.2): the
# cooldown is checked in the effect's own `limit`.
INSULT = """
on_accept = {
	if = {
		limit = { has_technology = tech_satisfying_insults NOT = { has_modifier = satisfying_insult_recently } }
		add_modifier = { modifier = satisfying_insult_recently years = 5 }
	}
}
"""


class TestMoreSources(unittest.TestCase):
    def test_gate_in_the_effects_limit(self):
        d = parse(INSULT)
        self.assertEqual(action_timers([], [d['on_accept']]),
                         [('modifier', 'satisfying_insult_recently', 'bonus', 1800)])

    def test_inline_script_with_parameters(self):
        script = 'NOT = { has_modifier = relic_activation_cooldown } last_activated_relic = $RELIC$'
        load = {'relics/activation_checks': script}.get
        d = parse('possible = { inline_script = { script = relics/activation_checks RELIC = r_x } }')
        [body] = expand_inline(d['possible'], load)
        self.assertEqual(body['last_activated_relic'], 'r_x')
        self.assertEqual(gates(body), {('modifier', 'relic_activation_cooldown')})

    def test_days_from_a_scripted_variable(self):
        d = parse('e = { add_modifier = { modifier = relic_activation_cooldown days = @triumph_duration } }')
        self.assertEqual(timers(d['e'], {'@triumph_duration': 3600.0}),
                         {('modifier', 'relic_activation_cooldown'): 3600})

    def test_astral_action_cooldowns(self):
        d = parse('@ten = 3600\naction_lock = { cooldown = @ten }\naction_other = { unlock_threshold = 3 }')
        self.assertEqual(astral_cooldowns(d, {}), {'action_lock': 3600})


class TestFiredEvents(unittest.TestCase):
    # Find Military Applications (4.5.2): its effect fires ancrel.10010, whose option adds the
    # Combat Drugs modifier
    def test_timers_of_the_events_it_fires(self):
        effect = parse('effect = { add_modifier = { modifier = artifact_find_applications_cooldown days = 900 } '
                       'owner = { country_event = { id = ancrel.10010 } } }')['effect']
        event = parse('e = { id = ancrel.10010 option = { add_modifier = { '
                      'modifier = artifact_find_military_application_army years = 5 } } }')['e']
        events = {'ancrel.10010': event}
        self.assertEqual(fired([effect], events), [event])
        self.assertIn(('modifier', 'artifact_find_military_application_army', 'effect', 1800),
                      action_timers([], [effect], events=events))

    def test_a_loop_of_events_ends(self):
        events = {'a.1': parse('e = { id = a.1 immediate = { country_event = { id = a.2 } } }')['e'],
                  'a.2': parse('e = { id = a.2 immediate = { country_event = { id = a.1 } } }')['e']}
        start = parse('x = { country_event = { id = a.1 } }')['x']
        self.assertEqual(len(fired([start], events, depth=5)), 2)


if __name__ == '__main__':
    unittest.main()
