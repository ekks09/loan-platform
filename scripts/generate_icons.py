"""Generate the M-Biashara PWA icons without external image libraries.

Run from the repository root:

    python3 scripts/generate_icons.py

The mark is the same artwork as ``Backend/Frontend/partials/brand_mark.svg``:
two interlocking upward chevrons in Kenyan gold on an obsidian field, with an
emerald baseline standing in for the M-Pesa rails. Keeping the generator and
the inline SVG on the same geometry means the tab icon, the installed app tile
and the header mark never drift apart.

Rendering notes
---------------
* Geometry is authored in the 64x64 viewBox used by the SVG, then scaled about
  the canvas centre so every export is the same picture at a different size.
* Coverage is rasterised at 4x and box-downsampled, which is the antialiasing
  the previous "M" icons used.
* ``purpose: any`` icons are full-bleed obsidian squares: the OS applies its
  own mask (iOS rounds the corners, Android may circle or squircle them), and
  a bleeding field avoids a second, invisible colour appearing at the corners.
  The in-page install tile is the only place the circular
  ``.icon-pwa-standard`` treatment is used.
* Maskable icons keep the glyph inside the central 80% safe zone so aggressive
  Android masks never clip a chevron.

Output is deterministic; re-running rewrites byte-identical files.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Backend" / "Frontend" / "icons"

OBSIDIAN = (7, 19, 32, 255)
GOLD = (212, 175, 55, 255)
GOLD_DEEP = (140, 110, 31, 255)
EMERALD = (16, 185, 129, 255)

# --- Geometry, in the 64-unit viewBox of partials/brand_mark.svg -----------
REAR_CHEVRON = ((18.0, 30.0), (32.0, 16.0), (46.0, 30.0))
FRONT_CHEVRON = ((18.0, 41.0), (32.0, 27.0), (46.0, 41.0))
STROKE = 7.0
BASELINE = (17.0, 46.4, 30.0, 2.4)  # x, y, width, height

# Fraction of the canvas the 64-unit artwork occupies. The maskable export is
# pulled in so the mark survives a circular crop.
GLYPH_SPAN = 0.66
GLYPH_SPAN_MASKABLE = 0.50


class Canvas:
    """Small RGBA canvas: coverage blending, box downsample, PNG encode."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(width * height * 4)

    def blend(self, x: int, y: int, color: tuple[int, int, int, int], alpha: float) -> None:
        if alpha <= 0 or x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        if alpha > 1:
            alpha = 1.0
        index = (y * self.width + x) * 4
        inverse = 1 - alpha
        for channel in range(3):
            self.pixels[index + channel] = int(
                color[channel] * alpha + self.pixels[index + channel] * inverse + 0.5
            )
        self.pixels[index + 3] = min(
            255, int(color[3] * alpha + self.pixels[index + 3] * inverse + 0.5)
        )

    def fill(self, color: tuple[int, int, int, int]) -> None:
        for y in range(self.height):
            for x in range(self.width):
                self.blend(x, y, color, 1.0)

    def stroke_coverage(
        self, geometry_fn, colour, bbox: tuple[float, float, float, float], half_width: float
    ) -> None:
        """Blend a stroked polyline using per-pixel centre distance tests."""
        x0 = max(0, int(bbox[0]) - 1)
        y0 = max(0, int(bbox[1]) - 1)
        x1 = min(self.width - 1, int(bbox[2]) + 1)
        y1 = min(self.height - 1, int(bbox[3]) + 1)
        for y in range(y0, y1 + 1):
            py = y + 0.5
            for x in range(x0, x1 + 1):
                px = x + 0.5
                if geometry_fn(px, py) <= half_width:
                    self.blend(x, y, colour, 1.0)

    def rect(self, x0: float, y0: float, x1: float, y1: float, r: float, colour) -> None:
        for y in range(max(0, int(y0)), min(self.height, int(math.ceil(y1)))):
            scan = y + 0.5
            if scan < y0 or scan > y1:
                continue
            if scan < y0 + r or scan > y1 - r:
                centre = y0 + r if scan < y0 + r else y1 - r
                dy = abs(scan - centre)
                if dy > r:
                    continue
                offset = math.sqrt(max(0.0, r * r - dy * dy))
                left, right = x0 + r - offset, x1 - r + offset
            else:
                left, right = x0, x1
            for x in range(max(0, int(left)), min(self.width, int(math.ceil(right)))):
                if left <= x + 0.5 <= right:
                    self.blend(x, y, colour, 1.0)

    def downsample(self, factor: int) -> "Canvas":
        out = Canvas(self.width // factor, self.height // factor)
        area = factor * factor
        for y in range(out.height):
            for x in range(out.width):
                r = g = b = a = 0
                for dy in range(factor):
                    base = ((y * factor + dy) * self.width + x * factor) * 4
                    for dx in range(factor):
                        index = base + dx * 4
                        alpha = self.pixels[index + 3]
                        r += self.pixels[index] * alpha
                        g += self.pixels[index + 1] * alpha
                        b += self.pixels[index + 2] * alpha
                        a += alpha
                if a:
                    out_index = (y * out.width + x) * 4
                    out.pixels[out_index] = min(255, int(r / a + 0.5))
                    out.pixels[out_index + 1] = min(255, int(g / a + 0.5))
                    out.pixels[out_index + 2] = min(255, int(b / a + 0.5))
                    out.pixels[out_index + 3] = min(255, int(a / area + 0.5))
        return out

    def png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 4
        for y in range(self.height):
            raw.append(0)
            raw.extend(self.pixels[y * stride : (y + 1) * stride])

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 6, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else ((px - ax) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def draw_icon(size: int, *, span: float) -> bytes:
    factor = 4
    canvas = Canvas(size * factor, size * factor)
    canvas.fill(OBSIDIAN)

    scale = (size * factor / 64.0) * (span / GLYPH_SPAN)
    centre = size * factor / 2.0

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return (centre + (point[0] - 32.0) * scale, centre + (point[1] - 32.0) * scale)

    half_width = STROKE * scale / 2.0

    def bbox(points) -> tuple[float, float, float, float]:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        pad = half_width + 1
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    rear = [project(p) for p in REAR_CHEVRON]
    front = [project(p) for p in FRONT_CHEVRON]

    def rear_fn(px: float, py: float) -> float:
        return min(
            _distance_to_segment(px, py, *rear[0], *rear[1]),
            _distance_to_segment(px, py, *rear[1], *rear[2]),
        )

    def front_fn(px: float, py: float) -> float:
        return min(
            _distance_to_segment(px, py, *front[0], *front[1]),
            _distance_to_segment(px, py, *front[1], *front[2]),
        )

    # Draw order matches the SVG: rear chevron, then the front one on top.
    canvas.stroke_coverage(rear_fn, GOLD_DEEP, bbox(rear), half_width)
    canvas.stroke_coverage(front_fn, GOLD, bbox(front), half_width)

    bx, by, bw, bh = BASELINE
    x0, y0 = project((bx, by))
    x1, y1 = project((bx + bw, by + bh))
    canvas.rect(x0, y0, x1, y1, max(0.5, (y1 - y0) / 2.0), EMERALD)

    return canvas.downsample(factor).png()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "icon-192.png": draw_icon(192, span=GLYPH_SPAN),
        "icon-512.png": draw_icon(512, span=GLYPH_SPAN),
        "icon-maskable-512.png": draw_icon(512, span=GLYPH_SPAN_MASKABLE),
        "apple-touch-icon.png": draw_icon(180, span=GLYPH_SPAN),
        "favicon-32.png": draw_icon(32, span=GLYPH_SPAN),
    }
    for name, payload in files.items():
        (OUT / name).write_bytes(payload)
        print(f"wrote {OUT / name} ({len(payload)} bytes)")


if __name__ == "__main__":
    main()
