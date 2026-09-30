"""The RUDRA mark: an engineered letter R, defined as geometry (ADR 0057).

This file is the mark's source artwork. Running it regenerates every derived file:

    .venv\\Scripts\\python.exe windows\\icon\\rudra_mark.py

    windows/icon/rudra-mark.svg        vector artwork, for viewing, editing, large renders
    app/ui/gui/assets/rudra.ico        16-256 px: the executables', window and taskbar icon
    app/ui/gui/assets/rudra-<n>.png    the interface's header marks

The R is built, not typed: a stem with a chamfered head, a chamfered bowl and a straight
diagonal leg, all in one stroke weight, with a signal trace and node leaving the bowl -
an engineered symbol rather than a font glyph. Sizes of 32 px and below use a bolder,
simpler drawing (no ring, trace or node) so the letter stays legible on the taskbar.

Standard library only: a scanline rasterizer (non-zero winding, exact horizontal
coverage, 4x vertical supersampling), a PNG writer and an ICO writer.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG_PATH = Path(__file__).resolve().parent / "rudra-mark.svg"
ASSETS = ROOT / "app" / "ui" / "gui" / "assets"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_SIZES = (32, 48, 64, 96, 128, 256)
SMALL_MAX = 32  # sizes up to this use the bold, simplified drawing
DESIGN = 256.0  # the design grid

Point = tuple[float, float]
Color = tuple[int, int, int, int]

# Palette: deep instrument-panel navy, an ice-to-cyan letter, one cyan accent.
BADGE_TOP: Color = (22, 34, 58, 255)
BADGE_BOTTOM: Color = (7, 11, 21, 255)
RING: Color = (56, 189, 248, 64)
LETTER_TOP: Color = (224, 242, 254, 255)
LETTER_BOTTOM: Color = (56, 189, 248, 255)
TRACE: Color = (103, 232, 249, 255)


@dataclass(frozen=True)
class Layer:
    """Filled rings (holes run the other way round) with a flat or vertical-gradient paint."""

    name: str
    rings: tuple[tuple[Point, ...], ...]
    top: Color
    bottom: Color | None = None  # None: flat `top`
    y0: float = 0.0
    y1: float = DESIGN

    def color_at(self, y: float) -> Color:
        if self.bottom is None:
            return self.top
        t = min(1.0, max(0.0, (y - self.y0) / (self.y1 - self.y0)))
        return tuple(round(a + (b - a) * t) for a, b in zip(self.top, self.bottom))  # type: ignore[return-value]


# ------------------------------------------------------------------ geometry


def _area(ring: tuple[Point, ...]) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / 2


def _outer(ring: list[Point]) -> tuple[Point, ...]:
    """Clockwise on screen (y down), the orientation every filled outline uses."""
    points = tuple(ring)
    return points if _area(points) > 0 else points[::-1]


def _hole(ring: list[Point]) -> tuple[Point, ...]:
    points = tuple(ring)
    return points if _area(points) < 0 else points[::-1]


def _rounded_rect(x0: float, y0: float, x1: float, y1: float, radius: float, steps: int = 14) -> list[Point]:
    points: list[Point] = []
    corners = ((x1 - radius, y0 + radius, -90), (x1 - radius, y1 - radius, 0),
               (x0 + radius, y1 - radius, 90), (x0 + radius, y0 + radius, 180))
    for cx, cy, start in corners:
        for step in range(steps + 1):
            angle = math.radians(start + 90 * step / steps)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _circle(cx: float, cy: float, radius: float, steps: int = 40) -> list[Point]:
    return [(cx + radius * math.cos(2 * math.pi * i / steps), cy + radius * math.sin(2 * math.pi * i / steps))
            for i in range(steps)]


@dataclass(frozen=True)
class Letter:
    """The R's construction, in design units."""

    weight: float  # the one stroke weight
    left: float
    top: float
    bottom: float
    right: float  # the bowl's flat right side
    mid: float  # the bowl's lower edge
    foot: float  # the leg's lower right corner
    head: float  # the chamfer on the stem's head
    corner: float  # the chamfers on the bowl
    cut: float  # the stencil cut under the bowl; 0 for none
    leg_at: float  # where the leg leaves the bowl, as a fraction of the counter's width


# Drawn on the 16-pixel grid (16 units = 1 px at 16 px) so small icons stay crisp.
SMALL = Letter(weight=32, left=64, top=32, bottom=224, right=192, mid=144, foot=208, head=16,
               corner=24, cut=0, leg_at=0.25)
DETAILED = Letter(weight=26, left=68, top=54, bottom=202, right=174, mid=138, foot=184, head=14,
                  corner=30, cut=8, leg_at=0.40)


def letter(small: bool) -> list[tuple[Point, ...]]:
    """The R: a stem with a chamfered head, a chamfered bowl around its counter, a leg.

    The detailed drawing cuts the letter once, just under the bowl, like a stencil: the
    upper stem and bowl sit apart from the lower stem and the leg.
    """
    d = SMALL if small else DETAILED
    w = d.weight
    inner = max(d.corner - w * math.tan(math.radians(22.5)), 3.0)
    low = d.mid + d.cut  # where the lower strokes begin
    shapes = [
        _outer([(d.left, d.top + d.head), (d.left + d.head, d.top), (d.left + w, d.top),
                (d.left + w, d.mid), (d.left, d.mid)]),
        _outer([(d.left, low), (d.left + w, low), (d.left + w, d.bottom), (d.left, d.bottom)]),
        _outer([(d.left + w / 2, d.top), (d.right - d.corner, d.top), (d.right, d.top + d.corner),
                (d.right, d.mid - d.corner), (d.right - d.corner, d.mid), (d.left + w / 2, d.mid)]),
        _hole([(d.left + w, d.top + w), (d.right - w - inner, d.top + w), (d.right - w, d.top + w + inner),
               (d.right - w, d.mid - w - inner), (d.right - w - inner, d.mid - w), (d.left + w, d.mid - w)]),
    ]
    start = d.left + w + (d.right - d.left - 2 * w) * d.leg_at
    leg_top = low if d.cut else d.mid - w
    shapes.append(_outer([(start, leg_top), (start + w * 1.1, leg_top), (d.foot, d.bottom),
                          (d.foot - w * 1.2, d.bottom)]))
    return shapes


def layers(size: int) -> list[Layer]:
    """What is drawn at `size` pixels, bottom layer first."""
    small = size <= SMALL_MAX
    inset, radius = (0.0, 46.0) if small else (8.0, 54.0)
    badge = _outer(_rounded_rect(inset, inset, DESIGN - inset, DESIGN - inset, radius))
    drawn = [Layer("badge", (badge,), BADGE_TOP, BADGE_BOTTOM)]
    if not small:
        outer = _rounded_rect(18, 18, DESIGN - 18, DESIGN - 18, 44)
        inner = _rounded_rect(21, 21, DESIGN - 21, DESIGN - 21, 41)
        drawn.append(Layer("ring", (_outer(outer), _hole(inner)), RING))
        # The signal trace leaves the bowl's flat right side and ends in a node.
        d = DETAILED
        y = (d.top + d.mid) / 2
        trace = [(d.right - 2, y - 2.5), (d.right + 26, y - 2.5), (d.right + 26, y + 2.5), (d.right - 2, y + 2.5)]
        drawn.append(Layer("trace", (_outer(trace), _outer(_circle(d.right + 33, y, 7.5))), TRACE))
    d = SMALL if small else DETAILED
    drawn.append(Layer("letter", tuple(letter(small)), LETTER_TOP, LETTER_BOTTOM, d.top, d.bottom))
    return drawn


# ------------------------------------------------------------------ rasterizing


def _coverage(rings: tuple[tuple[Point, ...], ...], size: int, supersample: int = 4) -> list[float]:
    """Per-pixel coverage (0..1) of the rings under the non-zero winding rule."""
    scale = size / DESIGN
    edges = []
    for ring in rings:
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            if y0 == y1:
                continue
            direction = 1 if y1 > y0 else -1
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((y0 * scale, y1 * scale, x0 * scale, (x1 - x0) / (y1 - y0), direction))
    cover = [0.0] * (size * size)
    weight = 1.0 / supersample
    for sub in range(size * supersample):
        y = (sub + 0.5) / supersample
        crossings = sorted((x0 + (y - y0) * slope, direction)
                           for y0, y1, x0, slope, direction in edges if y0 <= y < y1)
        row = int(y) * size
        winding, span_start = 0, 0.0
        for x, direction in crossings:
            before = winding
            winding += direction
            if before == 0 and winding != 0:
                span_start = x
            elif before != 0 and winding == 0:
                _add_span(cover, row, size, span_start, x, weight)
    return cover


def _add_span(cover: list[float], row: int, size: int, start: float, end: float, weight: float) -> None:
    start, end = max(0.0, start), min(float(size), end)
    if end <= start:
        return
    first, last = int(start), int(end)
    if first == last:
        cover[row + first] += (end - start) * weight
        return
    cover[row + first] += (first + 1 - start) * weight
    for column in range(first + 1, last):
        cover[row + column] += weight
    if last < size:
        cover[row + last] += (end - last) * weight


def render(size: int) -> bytes:
    """The mark at `size` x `size` pixels, as straight (non-premultiplied) RGBA bytes."""
    pixels = [(0.0, 0.0, 0.0, 0.0)] * (size * size)
    scale = size / DESIGN
    for layer in layers(size):
        cover = _coverage(layer.rings, size)
        for index, amount in enumerate(cover):
            if amount <= 0.0:
                continue
            red, green, blue, alpha = layer.color_at(((index // size) + 0.5) / scale)
            a = min(amount, 1.0) * alpha / 255
            r0, g0, b0, a0 = pixels[index]
            out = a + a0 * (1 - a)
            pixels[index] = (
                (red * a + r0 * a0 * (1 - a)) / out,
                (green * a + g0 * a0 * (1 - a)) / out,
                (blue * a + b0 * a0 * (1 - a)) / out,
                out,
            )
    return bytes(
        value
        for red, green, blue, alpha in pixels
        for value in (round(red), round(green), round(blue), round(alpha * 255))
    )


# ------------------------------------------------------------------ file formats


def png(size: int, rgba: bytes) -> bytes:
    """A PNG file: 8-bit RGBA, no filtering, zlib level 9."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    stride = size * 4
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _dib(size: int, rgba: bytes) -> bytes:
    """An icon image in the classic form: 32-bit BGRA rows bottom-up, then an empty AND mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    stride = size * 4
    rows = []
    for row in range(size - 1, -1, -1):
        line = rgba[row * stride:(row + 1) * stride]
        rows.append(b"".join(line[i + 2:i + 3] + line[i + 1:i + 2] + line[i:i + 1] + line[i + 3:i + 4]
                             for i in range(0, stride, 4)))
    mask_stride = ((size + 31) // 32) * 4
    return header + b"".join(rows) + b"\x00" * (mask_stride * size)


def ico(images: dict[int, bytes]) -> bytes:
    """An ICO file: classic images up to 128 px, PNG-compressed at 256 px (Windows Vista+)."""
    sizes = sorted(images)
    blobs = [png(size, images[size]) if size >= 256 else _dib(size, images[size]) for size in sizes]
    offset = 6 + 16 * len(sizes)
    directory = struct.pack("<HHH", 0, 1, len(sizes))
    for size, blob in zip(sizes, blobs):
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return directory + b"".join(blobs)


def svg() -> str:
    """The detailed drawing as SVG: the same rings, gradients and colours."""
    def hex_rgb(color: Color) -> str:
        return "#{:02x}{:02x}{:02x}".format(*color[:3])

    def path(rings: tuple[tuple[Point, ...], ...]) -> str:
        return " ".join("M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in ring) + " Z" for ring in rings)

    defs, shapes = [], []
    for layer in layers(int(DESIGN)):
        fill = hex_rgb(layer.top)
        if layer.bottom is not None:
            defs.append(
                f'<linearGradient id="{layer.name}" x1="0" y1="{layer.y0:g}" x2="0" y2="{layer.y1:g}" '
                f'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{hex_rgb(layer.top)}"/>'
                f'<stop offset="1" stop-color="{hex_rgb(layer.bottom)}"/></linearGradient>')
            fill = f"url(#{layer.name})"
        opacity = "" if layer.top[3] == 255 else f' fill-opacity="{layer.top[3] / 255:.3f}"'
        shapes.append(f'<path id="{layer.name}-shape" d="{path(layer.rings)}" fill="{fill}"'
                      f'{opacity} fill-rule="nonzero"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- The RUDRA mark. Generated by windows/icon/rudra_mark.py, its source; edit there. -->\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {DESIGN:g} {DESIGN:g}" '
        f'width="{DESIGN:g}" height="{DESIGN:g}">\n'
        f"<defs>{''.join(defs)}</defs>\n" + "\n".join(shapes) + "\n</svg>\n"
    )


def outputs() -> dict[Path, bytes]:
    """Every derived file and its exact bytes."""
    images = {size: render(size) for size in sorted(set(ICO_SIZES) | set(PNG_SIZES))}
    files = {SVG_PATH: svg().encode("utf-8"), ASSETS / "rudra.ico": ico({s: images[s] for s in ICO_SIZES})}
    for size in PNG_SIZES:
        files[ASSETS / f"rudra-{size}.png"] = png(size, images[size])
    return files


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for path, data in outputs().items():
        path.write_bytes(data)
        print(f"{path.relative_to(ROOT)}  ({len(data)} bytes)")


if __name__ == "__main__":
    main()
