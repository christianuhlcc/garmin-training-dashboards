"""Home Screen icon (site/static/icon-180.png): stdlib only, no image libraries.

    python3 tools/make_icon.py

A light tile in the site's ink-on-surface palette with a rising, anti-aliased line, like a training-load
curve. iOS rounds the corners itself, so the tile is a full square.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 180
BG = (0xFC, 0xFC, 0xFB)      # --surface
INK = (0x0B, 0x0B, 0x0B)     # --ink
LINE_W = 13.0
# A load curve from lower left to upper right with one dip, in 0..1 tile coordinates.
POINTS = [(0.16, 0.70), (0.36, 0.52), (0.50, 0.62), (0.66, 0.36), (0.84, 0.28)]
DOT_R = 15.0                 # end point marker


def seg_dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay, bx, by = *a, *b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def coverage(x: int, y: int, pts: list[tuple[float, float]]) -> float:
    """Fraction of the pixel covered by the line or the end dot (4x4 supersampling)."""
    hit = 0
    for sy in range(4):
        for sx in range(4):
            px, py = x + (sx + 0.5) / 4, y + (sy + 0.5) / 4
            d = min(seg_dist(px, py, pts[i], pts[i + 1]) for i in range(len(pts) - 1))
            if d <= LINE_W / 2 or math.hypot(px - pts[-1][0], py - pts[-1][1]) <= DOT_R:
                hit += 1
    return hit / 16


def png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    pts = [(x * SIZE, y * SIZE) for x, y in POINTS]
    rows = []
    for y in range(SIZE):
        row = bytearray()
        for x in range(SIZE):
            c = coverage(x, y, pts)
            row += bytes(round(BG[i] + (INK[i] - BG[i]) * c) for i in range(3))
        rows.append(bytes(row))
    out = Path(__file__).resolve().parents[1] / "site" / "static" / "icon-180.png"
    out.write_bytes(png(SIZE, SIZE, rows))
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
