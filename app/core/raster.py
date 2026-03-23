# -*- coding: utf-8 -*-
"""Raster post-processing: crop, legend placement, palette save."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from app.core.types import BBox
from app.core.utils import clamp


def crop_image_to_bbox(
    img: Image.Image, bbox_render: BBox, bbox_target: BBox
) -> Image.Image:
    minx_r, miny_r, maxx_r, maxy_r = bbox_render
    minx_t, miny_t, maxx_t, maxy_t = bbox_target
    w, h = img.size

    x0 = math.floor((minx_t - minx_r) / (maxx_r - minx_r) * w)
    x1 = math.ceil((maxx_t - minx_r) / (maxx_r - minx_r) * w)
    y0 = math.floor((maxy_r - maxy_t) / (maxy_r - miny_r) * h)
    y1 = math.ceil((maxy_r - miny_t) / (maxy_r - miny_r) * h)

    x0 = clamp(x0, 0, w)
    x1 = clamp(x1, 0, w)
    y0 = clamp(y0, 0, h)
    y1 = clamp(y1, 0, h)

    if x1 <= x0 or y1 <= y0:
        raise ValueError("bbox_target ligt niet binnen bbox_render (crop faalt).")
    return img.crop((x0, y0, x1, y1))


def place_legend_on_image(
    base: Image.Image,
    legend: Image.Image,
    position: str = "bottom-right",
    margin: int = 30,
    legend_scale: float = 1.5,
    legend_max_width_ratio: float = 0.25,
    add_white_box: bool = True,
    box_padding: int = 14,
) -> Image.Image:
    base = base.convert("RGBA")
    legend = legend.convert("RGBA")

    legend = legend.resize(
        (int(legend.size[0] * legend_scale), int(legend.size[1] * legend_scale)),
        Image.Resampling.LANCZOS,
    )

    max_w = int(base.size[0] * legend_max_width_ratio)
    if legend.size[0] > max_w:
        s = max_w / legend.size[0]
        legend = legend.resize(
            (max_w, int(legend.size[1] * s)), Image.Resampling.LANCZOS
        )

    if add_white_box:
        box_w = legend.size[0] + 2 * box_padding
        box_h = legend.size[1] + 2 * box_padding
        box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 220))
        box.paste(legend, (box_padding, box_padding), legend)
        legend = box

    W, H = base.size
    w, h = legend.size

    positions = {
        "bottom-right": (W - w - margin, H - h - margin),
        "bottom-left": (margin, H - h - margin),
        "top-right": (W - w - margin, margin),
        "top-left": (margin, margin),
    }
    if position not in positions:
        raise ValueError(
            f"position must be one of: {', '.join(positions)}"
        )
    x, y = positions[position]

    out = base.copy()
    out.paste(legend, (x, y), legend)
    return out


def save_png_palette_transparency(img_rgba: Image.Image, out_path: Path) -> None:
    img = img_rgba.convert("RGBA")
    w, h = img.size

    rgb = Image.new("RGB", (w, h), (0, 0, 0))
    rgb.paste(img, mask=img.getchannel("A"))

    pal = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)

    palette = pal.getpalette()
    palette[0:3] = [0, 0, 0]
    pal.putpalette(palette)

    alpha = img.getchannel("A")
    p_px = pal.load()
    a_px = alpha.load()

    for yy in range(h):
        for xx in range(w):
            if a_px[xx, yy] == 0:
                p_px[xx, yy] = 0

    pal.save(out_path, transparency=0)
