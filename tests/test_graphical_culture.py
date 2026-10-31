import unittest

from extractor.clausewitz import Parser, tokenize
from extractor.misc import graphical_cultures


class TestGraphicalCultures(unittest.TestCase):
    def test_ship_kinds_per_shipset(self):
        # Shaped like 4.5.2 common/graphical_culture/00_graphical_culture.txt
        data = Parser(tokenize('''
            mammalian_01 = { ship_kinds = { default_ship space_amoeba } }
            biogenesis_02 = { selectable = { has_biogenesis_dlc = yes } ship_kinds = { bio_ship tiyanki } }
            pirate_01 = { }
        ''')).parse()
        self.assertEqual(graphical_cultures(data), [
            ('biogenesis_02', '["bio_ship", "tiyanki"]'),
            ('mammalian_01', '["default_ship", "space_amoeba"]'),
            ('pirate_01', '[]'),
        ])


if __name__ == '__main__':
    unittest.main()
