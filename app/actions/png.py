"""A minimal PNG writer and reader for screenshots, standard library only (`zlib`).

`encode(width, height, rows)` writes an 8-bit RGB PNG from rows of bytes (three per pixel,
top row first). `dimensions(data)` reads a PNG's width and height from its `IHDR` chunk,
or None when the bytes are not a PNG - the screenshot postcondition's check.
"""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def encode(width: int, height: int, rows) -> bytes:
    """An 8-bit RGB PNG; each row is `width * 3` bytes."""
    raw = bytearray()
    for row in rows:
        if len(row) != width * 3:
            raise ValueError("a row has the wrong length")
        raw += b"\x00" + bytes(row)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")


def blank(width: int, height: int) -> bytes:
    """A black image of this size."""
    row = bytes(width * 3)
    return encode(width, height, (row for _ in range(height)))


def dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])
