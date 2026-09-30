"""The RUDRA mark (ADR 0057): its files match its source, are well formed, and stay legible.

`windows/icon/rudra_mark.py` is the mark's source; the SVG, the .ico and the PNGs are
generated from it and committed. These tests regenerate them and compare byte for byte,
so the artwork and its source cannot drift apart.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zlib

import pytest

from windows.icon import rudra_mark as mark


@pytest.fixture(scope="module")
def generated() -> dict:
    return mark.outputs()


def test_every_committed_file_is_exactly_what_the_source_generates(generated):
    assert len(generated) == 2 + len(mark.PNG_SIZES)
    for path, data in generated.items():
        assert path.is_file(), f"{path.name} is missing: run windows/icon/rudra_mark.py"
        assert path.read_bytes() == data, f"{path.name} differs from its source: run windows/icon/rudra_mark.py"


def test_the_icon_holds_every_size_windows_asks_for(generated):
    data = generated[mark.ASSETS / "rudra.ico"]
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(mark.ICO_SIZES))
    sizes = []
    for index in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * index:22 + 16 * index])
        size = width or 256
        sizes.append(size)
        assert height == width and planes == 1 and bits == 32 and offset + length <= len(data)
        image = data[offset:offset + length]
        if size == 256:
            assert image.startswith(b"\x89PNG\r\n\x1a\n")  # PNG-compressed, as Windows reads at 256 px
        else:
            header, dib_width, dib_height = struct.unpack("<Iii", image[:12])
            assert (header, dib_width, dib_height) == (40, size, 2 * size)  # image and mask
    assert tuple(sizes) == mark.ICO_SIZES


def _png_rgba(data: bytes) -> tuple[int, int, bytes]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    assert (depth, color) == (8, 6)
    position, compressed = 8, b""
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        if data[position + 4:position + 8] == b"IDAT":
            compressed += data[position + 8:position + 8 + length]
        position += 12 + length
    raw = zlib.decompress(compressed)
    stride = width * 4 + 1
    return width, height, b"".join(raw[row * stride + 1:(row + 1) * stride] for row in range(height))


def test_the_marks_are_rgba_pngs_of_their_named_size(generated):
    for size in mark.PNG_SIZES:
        width, height, pixels = _png_rgba(generated[mark.ASSETS / f"rudra-{size}.png"])
        assert (width, height, len(pixels)) == (size, size, size * size * 4)


def test_the_svg_is_the_detailed_drawing(generated):
    tree = ET.fromstring(generated[mark.SVG_PATH])
    assert tree.get("viewBox") == "0 0 256 256"
    ids = [element.get("id") for element in tree.iter("{http://www.w3.org/2000/svg}path")]
    assert ids == ["badge-shape", "ring-shape", "trace-shape", "letter-shape"]


def _luminance(pixels: bytes, size: int, x: int, y: int) -> float:
    red, green, blue, _ = pixels[(y * size + x) * 4:(y * size + x) * 4 + 4]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def test_at_16_pixels_the_letter_keeps_its_counter_and_its_stem():
    size = 16
    pixels = mark.render(size)
    unit = size / mark.DESIGN
    d = mark.SMALL
    counter = _luminance(pixels, size, int((d.left + d.weight + (d.right - d.weight)) / 2 * unit),
                         int((d.top + d.weight + d.mid - d.weight) / 2 * unit))
    stem = _luminance(pixels, size, int((d.left + d.weight / 2) * unit), int((d.mid + d.bottom) / 2 * unit))
    assert stem - counter > 120, (stem, counter)


def test_small_sizes_use_the_simplified_drawing():
    assert [layer.name for layer in mark.layers(32)] == ["badge", "letter"]
    assert [layer.name for layer in mark.layers(48)] == ["badge", "ring", "trace", "letter"]


def test_rendering_is_deterministic():
    assert mark.render(24) == mark.render(24)
