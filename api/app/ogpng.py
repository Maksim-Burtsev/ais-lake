"""The og:image, drawn by hand: a truecolour PNG out of zlib and struct (F31).

A share card is one polyline on a dark plate. Pillow would draw it prettier, but
it is 3 MB of wheel for ~60 lines of scanlines, so the encoder lives here and the
line is Bresenham'd straight into the pixel buffer.

# ponytail: no anti-aliasing and no text on the card — the ship's name is in the
# og:title beside it. Swap this module for Pillow the day the card needs a label,
# a legend or a curve that does not look like a staircase.
"""

import struct
import zlib

WIDTH = 1200
HEIGHT = 630
PAD = 90
BG = (0x0A, 0x16, 0x20)
INK = (0x8F, 0xB8, 0xCC)
STROKE = 3  # half-width in px of the drawn line, in a square brush


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    )


def png(width: int, height: int, pixels: bytearray) -> bytes:
    """RGB8 bytes -> a PNG file. Filter 0 on every scanline: zlib does the work."""
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(pixels[y * stride : (y + 1) * stride]) for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def _dot(pixels: bytearray, x: int, y: int) -> None:
    for dy in range(-STROKE, STROKE + 1):
        for dx in range(-STROKE, STROKE + 1):
            px, py = x + dx, y + dy
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                pixels[(py * WIDTH + px) * 3 : (py * WIDTH + px) * 3 + 3] = bytes(INK)


def _line(pixels: bytearray, x0: int, y0: int, x1: int, y1: int) -> None:
    """Bresenham, integer-only, both octants."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        _dot(pixels, x0, y0)
        if x0 == x1 and y0 == y1:
            return
        err2 = 2 * err
        if err2 >= dy:
            err += dy
            x0 += sx
        if err2 <= dx:
            err += dx
            y0 += sy


def card(coordinates: list[list[float]]) -> bytes:
    """The share card: the simplified track on the brand plate, or the bare plate
    when the ship has no track to show — never a broken image."""
    pixels = bytearray(bytes(BG) * (WIDTH * HEIGHT))
    points = [(float(c[0]), float(c[1])) for c in coordinates if len(c) >= 2]
    if len(points) >= 2:
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        span_x, span_y = max(lons) - min(lons), max(lats) - min(lats)
        # One scale for both axes, so a 400-nm leg does not come out as a circle.
        scale = min(
            (WIDTH - 2 * PAD) / span_x if span_x else float("inf"),
            (HEIGHT - 2 * PAD) / span_y if span_y else float("inf"),
        )
        if scale == float("inf"):  # every fix in one place: nothing to draw a line with
            scale = 0.0
        cx, cy = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
        screen = [
            (
                round(WIDTH / 2 + (lon - cx) * scale),
                round(HEIGHT / 2 - (lat - cy) * scale),  # north is up
            )
            for lon, lat in points
        ]
        for (x0, y0), (x1, y1) in zip(screen, screen[1:], strict=False):
            _line(pixels, x0, y0, x1, y1)
    return png(WIDTH, HEIGHT, pixels)
