#!/usr/bin/env python3
"""Unit tests for the uncompressed-DDS pixel reader."""
import struct
import unittest

from extractor.dds import dds_pixel, dds_size, hex_color

# B8G8R8A8, the layout of the game's grid-box icons
MASKS = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)


def dds(width: int, height: int, pixels: list[int], flags: int = 0x41, fourcc: bytes = b'\0' * 4) -> bytes:
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<II', header, 12, height, width)
    struct.pack_into('<I4s5I', header, 80, flags, fourcc, 32, *MASKS)
    return bytes(header) + struct.pack(f'<{len(pixels)}I', *pixels)


class TestDds(unittest.TestCase):
    # 2×2: red, green / blue, half-transparent white
    IMAGE = dds(2, 2, [0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0x80FFFFFF])

    def test_size(self):
        self.assertEqual(dds_size(self.IMAGE), (2, 2))

    def test_pixels_follow_the_masks_row_by_row(self):
        self.assertEqual(dds_pixel(self.IMAGE, 0, 0), (255, 0, 0, 255))
        self.assertEqual(dds_pixel(self.IMAGE, 1, 0), (0, 255, 0, 255))
        self.assertEqual(dds_pixel(self.IMAGE, 0, 1), (0, 0, 255, 255))
        self.assertEqual(dds_pixel(self.IMAGE, 1, 1), (255, 255, 255, 128))

    def test_hex(self):
        self.assertEqual(hex_color((65, 170, 36, 255)), '#41aa24')

    def test_compressed_is_refused(self):
        with self.assertRaises(ValueError):
            dds_pixel(dds(1, 1, [0], flags=0x4, fourcc=b'DXT5'), 0, 0)

    def test_outside_is_refused(self):
        with self.assertRaises(ValueError):
            dds_pixel(self.IMAGE, 2, 0)

    def test_not_dds(self):
        with self.assertRaises(ValueError):
            dds_size(b'PNG ' + bytes(124))


if __name__ == '__main__':
    unittest.main()
