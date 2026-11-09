#!/usr/bin/env python3
"""Unit tests for the mod overlay: which definition wins, and the merged folder."""
import tempfile
import unittest
from pathlib import Path

from extractor.clausewitz import parse_file
from extractor.localisation import load_localisation
from extractor.mods import _localisation_target, cut, losing_spans, overlay, top_level_spans


class TestTopLevelSpans(unittest.TestCase):
    def test_keys_and_spans(self):
        text = 'a = { x = "}" # }\n }\nb = 3\n@v = 2\nc = rgb { 1 2 3 }\nd > 1'
        spans = top_level_spans(text)
        self.assertEqual([k for k, _, _ in spans], ['a', 'b', '@v', 'c', 'd'])
        a = spans[0]
        self.assertEqual(text[a[1]:a[2]], 'a = { x = "}" # }\n }')
        self.assertEqual(text[spans[3][1]:spans[3][2]], 'c = rgb { 1 2 3 }')

    def test_braces_in_strings_and_comments(self):
        text = 'a = { s = "}" # }\n b = { } }\nc = 1\n'
        self.assertEqual([k for k, _, _ in top_level_spans(text)], ['a', 'c'])

    def test_quoted_key_and_block_without_equals(self):
        spans = top_level_spans('"q" = 1\nnamespace = x\n')
        self.assertEqual([k for k, _, _ in spans], ['q', 'namespace'])


class TestLosingSpans(unittest.TestCase):
    def test_later_file_wins(self):
        texts = [('00_vanilla.txt', 'a = { v = 1 }\nb = { v = 1 }'), ('zzz_mod.txt', 'a = { v = 2 }')]
        lost = losing_spans('buildings', texts)
        self.assertEqual(list(lost), ['00_vanilla.txt'])
        self.assertEqual(cut(texts[0][1], lost['00_vanilla.txt']).strip(), 'b = { v = 1 }')

    def test_scripted_variables_first_wins(self):
        texts = [('!!mod.txt', '@x = 2'), ('00_vanilla.txt', '@x = 1\n@y = 1')]
        lost = losing_spans('scripted_variables', texts)
        self.assertEqual(list(lost), ['00_vanilla.txt'])
        self.assertEqual(cut(texts[1][1], lost['00_vanilla.txt']).strip(), '@y = 1')

    def test_file_local_variables_and_wrappers_kept(self):
        texts = [('a.txt', '@c = 1\ndefined_text = { name = x }\ndefined_text = { name = y }'),
                 ('b.txt', '@c = 2\ndefined_text = { name = z }')]
        self.assertEqual(losing_spans('scripted_loc', texts), {})


class TestLocalisationTarget(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(_localisation_target('localisation/english/x_l_english.yml'),
                         'localisation/english/x_l_english.yml')
        self.assertEqual(_localisation_target('localisation/x_l_english.yml'),
                         'localisation/english/x_l_english.yml')
        self.assertEqual(_localisation_target('localisation/replace/x_l_english.yml'),
                         'localisation/english/replace/x_l_english.yml')
        self.assertIsNone(_localisation_target('localisation/german/x_l_german.yml'))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class TestOverlay(unittest.TestCase):
    def test_merged_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game, mod1, mod2, out = root / 'game', root / 'm1', root / 'm2', root / 'out'
            write(game / 'common/colony_types/00_colony_types.txt', 'col_a = { v = 1 }\ncol_b = { v = 1 }')
            write(game / 'common/species_archetypes/00_archetypes.txt', 'BIOLOGICAL = { max_traits = 5 }')
            write(game / 'common/on_actions/00_on_actions.txt', 'on_game_start = { events = { a.1 } }')
            write(game / 'common/zones/00_zones.txt', 'zone_x = { }')
            write(game / 'localisation/english/a_l_english.yml', 'l_english:\n k:0 "Vanilla"\n')
            write(game / 'gfx/x.dds', 'binary')
            write(mod1 / 'descriptor.mod', 'name="One"\nversion="1.0"\nreplace_path="common/zones"')
            write(mod1 / 'common/colony_types/zzz_mod.txt', 'col_a = { v = 2 }\ncol_new = { v = 3 }')
            write(mod1 / 'common/on_actions/mod_on_actions.txt', 'on_game_start = { events = { m.1 } }')
            write(mod1 / 'common/zones/mod_zones.txt', 'zone_y = { }')
            write(mod1 / 'localisation/replace/b_l_english.yml', 'l_english:\n k:0 "Modded"\n')
            write(mod1 / 'localisation/german/b_l_german.yml', 'l_german:\n k:0 "Deutsch"\n')
            write(mod2 / 'common/species_archetypes/00_archetypes.txt', 'BIOLOGICAL = { max_traits = 12 }')
            out.mkdir()

            overrides = overlay(game, [mod1, mod2], out)

            colony = {}
            for f in sorted((out / 'common/colony_types').glob('*.txt')):
                colony.update(parse_file(f))
            self.assertEqual(colony, {'col_a': {'v': '2'}, 'col_b': {'v': '1'}, 'col_new': {'v': '3'}})
            # the overridden definition is gone from the earlier file (readers that keep the first
            # definition, `setdefault`, see the mod's too)
            self.assertEqual(parse_file(out / 'common/colony_types/00_colony_types.txt'),
                             {'col_b': {'v': '1'}})
            self.assertEqual(parse_file(out / 'common/species_archetypes/00_archetypes.txt'),
                             {'BIOLOGICAL': {'max_traits': '12'}})  # same path: replaced
            self.assertEqual(sorted(p.name for p in (out / 'common/on_actions').iterdir()),
                             ['00_on_actions.txt', 'mod_on_actions.txt'])  # merged, not cut
            self.assertEqual([p.name for p in (out / 'common/zones').iterdir()], ['mod_zones.txt'])
            self.assertEqual(load_localisation(out / 'localisation/english')['k'], 'Modded')
            self.assertFalse((out / 'localisation/german').exists())
            self.assertTrue((out / 'gfx/x.dds').exists())
            self.assertEqual(overrides, {str(mod1): 1, str(mod2): 0})
            # the game's own folder is untouched
            self.assertIn('col_a = { v = 1 }',
                          (game / 'common/colony_types/00_colony_types.txt').read_text())


if __name__ == '__main__':
    unittest.main()
