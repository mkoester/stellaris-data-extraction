"""Read single pixels from uncompressed DDS textures (pure, no I/O).

The game's district grid-box icons (`gfx/interface/icons/districts/grid_box/*.dds`) are flat
colour swatches stored as uncompressed 32-bit RGBA; that colour is the district's colour in game.
Compressed formats (DXT/BC) are not supported and raise ValueError.
"""
import struct

_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40


def _shift(mask: int) -> int:
    return (mask & -mask).bit_length() - 1 if mask else 0


def dds_size(data: bytes) -> tuple[int, int]:
    """(width, height) of a DDS texture."""
    if data[:4] != b'DDS ':
        raise ValueError('not a DDS file')
    height, width = struct.unpack_from('<II', data, 12)
    return width, height


def dds_pixel(data: bytes, x: int, y: int) -> tuple[int, int, int, int]:
    """(r, g, b, a) at (x, y) of an uncompressed 32-bit DDS, decoded through its channel masks."""
    width, height = dds_size(data)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f'pixel ({x}, {y}) outside {width}x{height}')
    flags, _fourcc, bits, rmask, gmask, bmask, amask = struct.unpack_from('<I4s5I', data, 80)
    if flags & _DDPF_FOURCC or not flags & _DDPF_RGB or bits != 32:
        raise ValueError('only uncompressed 32-bit RGB(A) DDS is supported')
    (value,) = struct.unpack_from('<I', data, 128 + (y * width + x) * 4)
    channel = lambda mask: (value & mask) >> _shift(mask)
    alpha = channel(amask) if flags & _DDPF_ALPHAPIXELS and amask else 255
    return channel(rmask), channel(gmask), channel(bmask), alpha


def hex_color(rgba: tuple[int, int, int, int]) -> str:
    """'#rrggbb' (alpha dropped)."""
    return '#%02x%02x%02x' % rgba[:3]
