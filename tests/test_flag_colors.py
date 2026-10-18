#!/usr/bin/env python3
"""Unit tests for flag_color rows (flags/colors.txt swatches → their map colour)."""
import unittest

from extractor.empires import flag_colors

# Shaped like 4.5.2 flags/colors.txt: one swatch per line, a trailing display-name comment.
TEXT = """
# Color swatch definitions.
#   map   - Color used on the galaxy map for territory borders and fill.
colors = {
	red					= { flag = rgb { 158 22 22 }	map = rgb { 151 14 18 }		ship = rgb { 255 57 36 }	}	# "Commonwealth Red"
	blue				= { flag = rgb { 46 63 152 }	map = rgb { 46 63 153 }		ship = rgb { 90 202 255 }	}	# "UNE Blue"
}
"""


class TestFlagColors(unittest.TestCase):
    def test_takes_the_map_colour_not_the_flag_colour(self):
        self.assertEqual(flag_colors(TEXT), [('red', 151, 14, 18), ('blue', 46, 63, 153)])


if __name__ == '__main__':
    unittest.main()
