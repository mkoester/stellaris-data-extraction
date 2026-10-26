#!/usr/bin/env python3
"""Unit tests for localisation loading and text cleanup."""
import tempfile
import unittest
from pathlib import Path

from extractor.localisation import (_clean_loc, _dedup_adjacent, _fmt_scripted, join_texts,
                                    load_localisation, scripted_loc_jobs, set_scripted_loc)


class TestFmtScripted(unittest.TestCase):
    def test_percent_zero_decimals(self):
        self.assertEqual(_fmt_scripted(-0.25, '%0'), '-25%')

    def test_decimal_places(self):
        self.assertEqual(_fmt_scripted(1.5, '1'), '1.5')

    def test_forced_sign(self):
        self.assertEqual(_fmt_scripted(2, '+0'), '+2')

    def test_default_two_decimals(self):
        self.assertEqual(_fmt_scripted(1.5, None), '1.50')


class TestDedupAdjacent(unittest.TestCase):
    def test_single_word_repeat(self):
        self.assertEqual(_dedup_adjacent('Official Official Capacity'),
                         'Official Capacity')

    def test_multi_word_repeat(self):
        self.assertEqual(_dedup_adjacent('Consumer Goods Consumer Goods'),
                         'Consumer Goods')

    def test_no_repeat_untouched(self):
        self.assertEqual(_dedup_adjacent('Energy Credits from Jobs'),
                         'Energy Credits from Jobs')


class TestCleanLoc(unittest.TestCase):
    def test_key_expansion(self):
        self.assertEqual(_clean_loc('$greeting$ world', {'greeting': 'Hello'}),
                         'Hello world')

    def test_colour_codes_stripped(self):
        self.assertEqual(_clean_loc('§YShiny§! text', {}), 'Shiny text')

    def test_command_tags_dropped(self):
        self.assertEqual(_clean_loc('[This.GetName] rules', {}), 'rules')

    def test_resource_icon_rendered(self):
        self.assertEqual(_clean_loc('£unity£ Produced per 100', {}),
                         'Unity Produced per 100')

    def test_decoration_icon_dropped(self):
        self.assertEqual(_clean_loc('£trigger_yes£ Allowed', {}), 'Allowed')

    def test_icon_name_deduped_against_label(self):
        self.assertEqual(_clean_loc('£official£ Official Capacity', {}),
                         'Official Capacity')

    def test_concept_tag_resolves_via_loc(self):
        loc = {'tech_lasers': 'Lasers'}
        self.assertEqual(_clean_loc("['technology:tech_lasers'] unlocked", loc),
                         'Lasers unlocked')

    def test_scripted_variable_formatting(self):
        out = _clean_loc('Costs $@cost|%0$', {}, variables={'@cost': -0.25})
        self.assertEqual(out, 'Costs -25%')

    def test_keep_newlines_separates_rules(self):
        out = _clean_loc('- Rule one\\n- Rule two', {}, keep_newlines=True)
        self.assertEqual(out, 'Rule one\nRule two')

    def test_a_tag_opening_a_line_is_no_indent(self):
        # 4.5.2 UNEMPLOYED_PRODUCE_UNITY (Social Welfare's tooltip): the dropped icon's space
        # used to fold the line into the one above, "Happiness., Civilians produce Unity"
        loc = {'job_civilian_plural': 'Civilians'}
        out = _clean_loc('Happiness§!.\\n\\n§Y[civilian.GetIcon]\u00a0[civilian.GetNamePlural]§! '
                         'produce Unity', loc, keep_newlines=True)
        self.assertEqual(out, 'Happiness.\nCivilians produce Unity')

    def test_an_icon_opening_a_line_is_no_indent(self):
        # 4.5.2 DYSTOPIAN_LIVING_STANDARDS_POP_EFFECTS: a stratum's icon (dropped) opens a line
        loc = {'WORKERS': '£pop_cat_worker£\u00a0$pop_cat_worker_plural$',
               'pop_cat_worker_plural': 'Workers'}
        out = _clean_loc('deficit.\\n\\n$WORKERS$ have §R-200%§! Happiness', loc,
                         keep_newlines=True)
        self.assertEqual(out, 'deficit.\nWorkers have -200% Happiness')

    def test_four_spaces_indent_a_sub_item(self):
        loc = {'t': '    '}
        out = _clean_loc('Starting Technologies:\\n$t$Lasers\\n$t$Shields', loc, keep_newlines=True)
        self.assertEqual(out, 'Starting Technologies: Lasers, Shields')


class TestLoadLocalisation(unittest.TestCase):
    def write_and_load(self, content: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'test_l_english.yml').write_text(content, encoding='utf-8')
            return load_localisation(Path(d))

    def test_basic_entries(self):
        labels = self.write_and_load('l_english:\n key1:0 "Label One"\n key2:1 "Label Two"\n')
        self.assertEqual(labels['key1'], 'Label One')
        self.assertEqual(labels['key2'], 'Label Two')

    def test_lowest_version_wins(self):
        labels = self.write_and_load('l_english:\n key:1 "Newer"\n key:0 "Original"\n')
        self.assertEqual(labels['key'], 'Original')

    def test_single_ref_resolved_one_level(self):
        labels = self.write_and_load('l_english:\n alias:0 "$target$"\n target:0 "Real Label"\n')
        self.assertEqual(labels['alias'], 'Real Label')

    def test_unresolvable_ref_dropped(self):
        labels = self.write_and_load('l_english:\n alias:0 "$missing$"\n')
        self.assertNotIn('alias', labels)


class TestScriptedLocJobs(unittest.TestCase):
    def tearDown(self):
        set_scripted_loc({})

    def test_default_then_first_variant_jobs_only(self):
        texts = [
            {'name': 'GetResearcherPlural', 'text': [{'localization_key': 'job_brain_drone_plural'}],
             'default': 'job_researcher_plural'},
            {'name': 'GetMinerPlural', 'text': [{'localization_key': 'job_miner_plural'},
                                                {'localization_key': 'job_mining_drone_plural'}]},
            {'name': 'GetRulerTitle', 'default': 'ruler_title'},
        ]
        self.assertEqual(scripted_loc_jobs(texts), {'GetResearcherPlural': 'job_researcher_plural',
                                                    'GetMinerPlural': 'job_miner_plural'})

    def test_clean_loc_resolves_job_command(self):
        set_scripted_loc({'GetResearcherPluralWithIcon': 'job_researcher_plural_with_icon'})
        loc = {'job_researcher_plural_with_icon': '£job_researcher£ Researchers',
               'mod_x': 'Amenities Produced per 100 $res$', 'res': '[GetResearcherPluralWithIcon]'}
        self.assertEqual(_clean_loc('$mod_x$: +200', loc), 'Amenities Produced per 100 Researchers: +200')
        self.assertEqual(_clean_loc('[GetUnknown] left', loc), 'left')



class TestIconEcho(unittest.TestCase):
    """An icon rendered as a word is dropped when the clause repeats that word."""

    def test_icon_before_an_adjective_and_its_noun(self):
        self.assertEqual(_clean_loc('Assimilating £pop£ §HOrganic Pops§! now generates.', {}),
                         'Assimilating Organic Pops now generates.')

    def test_multi_word_gap_and_other_icons(self):
        self.assertEqual(_clean_loc('Remove £blocker£ Natural Blockers', {}),
                         'Remove Natural Blockers')
        self.assertEqual(_clean_loc('£district£ Mining Districts: +1', {}), 'Mining Districts: +1')

    def test_icon_kept_when_it_is_the_only_mention(self):
        self.assertEqual(_clean_loc('£pop£ is enslaved', {}), 'Pop is enslaved')
        self.assertEqual(_clean_loc('£unity£ Produced per 100', {}), 'Unity Produced per 100')

    def test_echo_does_not_cross_clause_punctuation(self):
        self.assertEqual(_clean_loc('£pop£ gained: 3 pops', {}), 'Pop gained: 3 pops')


class TestJoinTexts(unittest.TestCase):
    def test_a_restated_line_appears_once(self):
        # Shaped like tr_cybernetics_assimilator_modular_cybernetics_tt / _tt_2.
        first = 'Machine Points also apply to Cybernetic species.'
        self.assertEqual(join_texts([first, first + '\nSpecies Modification Cost: -50%']),
                         first + '\nSpecies Modification Cost: -50%')

    def test_empty_inputs(self):
        self.assertIsNone(join_texts([None, '']))


if __name__ == '__main__':
    unittest.main()
