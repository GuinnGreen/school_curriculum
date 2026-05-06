#!/usr/bin/env python3
"""Generate PWA icons. Run after editing the design here."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
OUT = ROOT / "icons"
OUT.mkdir(exist_ok=True)

BG = (181, 196, 177)   # --accent sage green
INK = (92, 84, 72)     # --ink warm brown
ACCENT = (212, 165, 165)  # --accent-2 dusty rose, for the highlighted cell


def draw_icon(size, maskable=False):
    """Minimal line-art schedule card: rounded-rect outline + 3 row dividers,
    with one row segment filled to add a touch of color."""
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    # Maskable spec: keep critical content inside a centered 80% circle.
    # Use a slightly larger margin for maskable so the design is fully safe.
    margin_ratio = 0.22 if maskable else 0.18
    margin = int(size * margin_ratio)

    left, top = margin, margin
    right, bottom = size - margin, size - margin
    inner_w = right - left
    inner_h = bottom - top

    stroke = max(2, round(size * 0.045))
    corner = max(2, int(inner_w * 0.10))

    # Outer card outline.
    d.rounded_rectangle((left, top, right, bottom),
                        radius=corner, outline=INK, width=stroke)

    # Vertical divider (1/3 from left), like a "time column".
    col_x = left + inner_w // 3
    d.line((col_x, top, col_x, bottom), fill=INK, width=stroke)

    # Two horizontal dividers, splitting into 3 rows.
    row1 = top + inner_h // 3
    row2 = top + (inner_h * 2) // 3
    d.line((left, row1, right, row1), fill=INK, width=stroke)
    d.line((left, row2, right, row2), fill=INK, width=stroke)

    # Highlight one schedule cell (middle row, right column) with accent color.
    pad = stroke
    cell = (col_x + pad, row1 + pad, right - pad, row2 - pad)
    d.rectangle(cell, fill=ACCENT)

    return img


for size in (180, 192, 512):
    draw_icon(size).save(OUT / f"icon-{size}.png", "PNG", optimize=True)
draw_icon(512, maskable=True).save(OUT / "icon-512-maskable.png", "PNG", optimize=True)
print("Icons written to", OUT)
