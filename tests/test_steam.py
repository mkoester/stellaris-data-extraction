#!/usr/bin/env python3
"""Unit tests for the beta guard: the Steam branch, refused outputs, and the release check."""
import unittest
from pathlib import Path

from extractor.dump_check import problems
from extractor.steam import RELEASE_OUTPUTS, branch_from_manifest, release_output_refusal

# Shaped like steamapps/appmanifest_281990.acf (Linux, 2026-10-31).
BETA = '''"AppState"
{
	"buildid"		"26223710"
	"UserConfig"
	{
		"language"		"english"
		"BetaKey"		"stellaris_test"
	}
	"MountedConfig"
	{
		"language"		"english"
		"BetaKey"		"stellaris_test"
	}
}'''
PUBLIC = BETA.replace('\t\t"BetaKey"\t\t"stellaris_test"\n', '')
# Opted into the beta, but Steam has not updated the files yet: still public's.
CHOSEN = BETA.replace('"BetaKey"\t\t"stellaris_test"\n\t}\n}', '}\n}')

SCHEMA = "CREATE TABLE meta (\n    key TEXT PRIMARY KEY,  -- k\n    value TEXT\n);\n"
META = "INSERT OR REPLACE INTO meta (key, value) VALUES ('{}', '{}');\n"


class Branch(unittest.TestCase):
    def test_mounted_beta(self):
        self.assertEqual(branch_from_manifest(BETA), 'stellaris_test')

    def test_no_beta_key_is_public(self):
        self.assertEqual(branch_from_manifest(PUBLIC), 'public')

    def test_the_installed_files_count_not_the_choice(self):
        self.assertIn('stellaris_test', CHOSEN)
        self.assertEqual(branch_from_manifest(CHOSEN), 'public')


class Refusal(unittest.TestCase):
    def test_release_outputs_need_the_public_game(self):
        for out in RELEASE_OUTPUTS:
            self.assertIsNone(release_output_refusal((str(out), None), 'public'))
            self.assertIn('stellaris_test', release_output_refusal((str(out), None), 'stellaris_test'))
            self.assertIn('no Steam manifest', release_output_refusal((None, str(out)), None))

    def test_other_files_take_any_branch(self):
        self.assertIsNone(release_output_refusal(('beta.sql', '/elsewhere/x.db'), 'stellaris_test'))
        self.assertIsNone(release_output_refusal((None, None), None))

    def test_any_spelling_of_the_dump_is_the_dump(self):
        dump = RELEASE_OUTPUTS[0]
        detour = dump.parent / '..' / 'sql' / dump.name
        self.assertIsNotNone(release_output_refusal((str(detour), None), 'stellaris_test'))


class DumpCheck(unittest.TestCase):
    def test_fresh_public_dump_passes(self):
        self.assertEqual(problems(SCHEMA + META.format('steam_branch', 'public'), SCHEMA), [])

    def test_beta_dump_fails(self):
        [p] = problems(SCHEMA + META.format('steam_branch', 'stellaris_test'), SCHEMA)
        self.assertIn("'stellaris_test'", p)

    def test_dump_without_a_branch_fails(self):
        self.assertEqual(len(problems(SCHEMA, SCHEMA)), 1)

    def test_stale_dump_fails_naming_the_tables(self):
        newer = SCHEMA + "CREATE TABLE added (\n    x TEXT\n);\n"
        [p] = problems(SCHEMA + META.format('steam_branch', 'public'), newer)
        self.assertIn('added', p)
        changed = SCHEMA.replace('value TEXT', 'value TEXT, extra TEXT')
        [p] = problems(SCHEMA + META.format('steam_branch', 'public'), changed)
        self.assertIn('meta', p)

    def test_comments_and_layout_do_not_count(self):
        relaid = "CREATE TABLE meta (\n  key   TEXT PRIMARY KEY,\n  value TEXT -- other\n);\n"
        self.assertEqual(problems(relaid + META.format('steam_branch', 'public'), SCHEMA), [])


if __name__ == '__main__':
    unittest.main()
