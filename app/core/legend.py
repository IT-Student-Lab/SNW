# -*- coding: utf-8 -*-
"""Pretty legend builders and colour extraction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.core.wms import _AHN_COLOUR_RAMP


# --------------- text helpers ---------------

def wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int
) -> List[str]:
    words = (text or "").split()
    if not words:
        return [""]

    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


# --------------- pretty legend card ---------------

def build_pretty_legend(
    rows: List[Dict[str, Any]],
    *,
    title: str,
    subtitle: Optional[str] = None,
    width: int = 900,
    show_percent: bool = True,
) -> Image.Image:
    try:
        font_title = ImageFont.truetype("arial.ttf", 42)
        font_sub = ImageFont.truetype("arial.ttf", 26)
        font_row = ImageFont.truetype("arial.ttf", 28)
        font_pct = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_row = ImageFont.load_default()
        font_pct = ImageFont.load_default()

    pad = 28
    top_pad = 22
    swatch = 32
    row_gap = 14
    line_gap = 5
    pct_w = 100 if show_percent else 0
    label_max_w = width - 2 * pad - swatch - 16 - pct_w - 10

    tmp = Image.new("RGBA", (width, 2000), (255, 255, 255, 0))
    dtmp = ImageDraw.Draw(tmp)

    title_bbox = dtmp.textbbox((0, 0), title, font=font_title)
    title_h = title_bbox[3] - title_bbox[1]

    subtitle_h = 0
    subtitle_lines: List[str] = []
    if subtitle:
        subtitle_lines = wrap_text(dtmp, subtitle, font_sub, width - 2 * pad)
        for line in subtitle_lines:
            bb = dtmp.textbbox((0, 0), line, font=font_sub)
            subtitle_h += (bb[3] - bb[1]) + line_gap
        subtitle_h += 4

    row_layouts: List[Dict[str, Any]] = []
    total_rows_h = 0
    for r in rows:
        label = str(r.get("label") or "(onbekend)")
        lines = wrap_text(dtmp, label, font_row, label_max_w)

        text_h = 0
        for line in lines:
            bb = dtmp.textbbox((0, 0), line, font=font_row)
            text_h += (bb[3] - bb[1]) + line_gap
        text_h = max(text_h, swatch)

        row_h = text_h + row_gap
        total_rows_h += row_h
        row_layouts.append({**r, "lines": lines, "row_h": row_h})

    height = top_pad + pad + title_h + 8 + subtitle_h + 12 + total_rows_h + pad

    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle(
        [6, 6, width - 1, height - 1], radius=18, fill=(0, 0, 0, 40)
    )
    d.rounded_rectangle(
        [0, 0, width - 8, height - 8],
        radius=18,
        fill=(255, 255, 255, 238),
        outline=(180, 180, 180, 180),
        width=1,
    )

    x0 = pad
    y = top_pad + pad

    d.text((x0, y), title, font=font_title, fill=(20, 20, 20, 255))
    y += title_h + 8

    if subtitle_lines:
        for line in subtitle_lines:
            d.text((x0, y), line, font=font_sub, fill=(70, 70, 70, 255))
            bb = d.textbbox((0, 0), line, font=font_sub)
            y += (bb[3] - bb[1]) + line_gap
        y += 8

    d.line((x0, y, width - pad - 8, y), fill=(210, 210, 210, 255), width=1)
    y += 14

    for row in row_layouts:
        rr, gg, bb = row["rgb"]
        lines = row["lines"]
        row_h = row["row_h"]

        sy = y + 2
        d.rounded_rectangle(
            [x0, sy, x0 + swatch, sy + swatch],
            radius=5,
            fill=(rr, gg, bb, 255),
            outline=(60, 60, 60, 120),
            width=1,
        )

        tx = x0 + swatch + 14
        ty = y
        for line in lines:
            d.text((tx, ty), line, font=font_row, fill=(20, 20, 20, 255))
            bb2 = d.textbbox((0, 0), line, font=font_row)
            ty += (bb2[3] - bb2[1]) + line_gap

        if show_percent and row.get("pct") is not None:
            pct_txt = f'{float(row["pct"]):.1f}%'
            pct_bbox = d.textbbox((0, 0), pct_txt, font=font_pct)
            pct_h = pct_bbox[3] - pct_bbox[1]
            d.text(
                (width - pad - pct_w, y + max(0, (swatch - pct_h) // 2)),
                pct_txt,
                font=font_pct,
                fill=(90, 90, 90, 255),
            )

        y += row_h

    return img


def extract_rows_from_vertical_legend(
    legend_img: Image.Image,
    *,
    title: str,
    max_width: int = 420,
    max_height: int = 520,
    scale: float = 1.15,
) -> Image.Image:
    legend = legend_img.convert("RGBA")

    legend = legend.resize(
        (
            max(1, int(legend.size[0] * scale)),
            max(1, int(legend.size[1] * scale)),
        ),
        Image.Resampling.LANCZOS,
    )

    ratio = min(max_width / legend.size[0], max_height / legend.size[1], 1.0)
    if ratio < 1.0:
        legend = legend.resize(
            (
                max(1, int(legend.size[0] * ratio)),
                max(1, int(legend.size[1] * ratio)),
            ),
            Image.Resampling.LANCZOS,
        )

    pad = 18
    header_h = 52
    width = legend.size[0] + 2 * pad
    height = legend.size[1] + 2 * pad + header_h

    card = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    d = ImageDraw.Draw(card)

    try:
        font_title = ImageFont.truetype("arial.ttf", 32)
    except Exception:
        font_title = ImageFont.load_default()

    d.rounded_rectangle(
        [5, 5, width - 1, height - 1], radius=16, fill=(0, 0, 0, 35)
    )
    d.rounded_rectangle(
        [0, 0, width - 6, height - 6],
        radius=16,
        fill=(255, 255, 255, 238),
        outline=(185, 185, 185, 180),
        width=1,
    )

    d.text((pad, 14), title, font=font_title, fill=(20, 20, 20, 255))
    d.line(
        (pad, header_h, width - pad - 6, header_h),
        fill=(210, 210, 210, 255),
        width=1,
    )

    x = (width - legend.size[0]) // 2
    y = header_h + pad
    card.paste(legend, (x, y), legend)
    return card


# --------------- colour extraction ---------------

def extract_dominant_colors(
    img: Image.Image, n: int = 10, sample: int = 6
) -> List[Tuple[Tuple[int, int, int], float]]:
    im = img.convert("RGBA")
    if sample > 1:
        im = im.resize(
            (max(1, im.size[0] // sample), max(1, im.size[1] // sample)),
            Image.Resampling.NEAREST,
        )

    px = list(im.getdata())
    filtered: List[Tuple[int, int, int]] = []
    for r, g, b, a in px:
        if a < 10:
            continue
        if r > 245 and g > 245 and b > 245:
            continue
        filtered.append((r, g, b))

    if not filtered:
        return []

    tmp = Image.new("RGB", im.size)
    total_px = im.size[0] * im.size[1]
    tmp.putdata(
        filtered + [(255, 255, 255)] * (total_px - len(filtered))
    )

    q = tmp.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    counts = q.getcolors() or []
    palette = q.getpalette() or []
    total = sum(c for c, _ in counts) or 1
    counts.sort(reverse=True, key=lambda x: x[0])

    out: List[Tuple[Tuple[int, int, int], float]] = []
    for c, idx in counts[:n]:
        rr = palette[idx * 3 + 0]
        gg = palette[idx * 3 + 1]
        bb = palette[idx * 3 + 2]
        out.append(((rr, gg, bb), c / total))
    return out


def find_representative_pixel(
    img: Image.Image,
    rgb: Tuple[int, int, int],
    max_samples: int = 200_000,
    tol: int = 10,
) -> Optional[Tuple[int, int]]:
    im = img.convert("RGBA")
    w, h = im.size
    px = im.load()

    stride = max(1, int((w * h / max_samples) ** 0.5))
    r0, g0, b0 = rgb

    for y in range(0, h, stride):
        for x in range(0, w, stride):
            r, g, b, a = px[x, y]
            if a < 10:
                continue
            if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol:
                return x, y
    return None


# --------------- dynamic AHN gradient legend ---------------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def build_ahn_dynamic_legend(
    title: str,
    vmin: float,
    vmax: float,
    *,
    width: int = 1200,
    bar_height: int = 36,
) -> Image.Image:
    """Build a gradient-bar legend card for AHN showing the local value range."""
    try:
        font_title = ImageFont.truetype("arial.ttf", 32)
        font_label = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()

    pad = 22
    header_h = 52
    label_h = 30
    num_ticks = 6
    height = header_h + pad + bar_height + label_h + pad + 10

    card = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    d = ImageDraw.Draw(card)

    # card background
    d.rounded_rectangle(
        [5, 5, width - 1, height - 1], radius=16, fill=(0, 0, 0, 35)
    )
    d.rounded_rectangle(
        [0, 0, width - 6, height - 6],
        radius=16,
        fill=(255, 255, 255, 238),
        outline=(185, 185, 185, 180),
        width=1,
    )

    # title
    d.text((pad, 14), title, font=font_title, fill=(20, 20, 20, 255))
    d.line(
        (pad, header_h, width - pad - 6, header_h),
        fill=(210, 210, 210, 255),
        width=1,
    )

    # gradient bar
    bar_x0 = pad
    bar_x1 = width - pad - 8
    bar_y0 = header_h + pad
    bar_w = bar_x1 - bar_x0

    colours = [(f, _hex_to_rgb(c)) for f, c in _AHN_COLOUR_RAMP]

    for px_x in range(bar_w):
        frac = px_x / max(bar_w - 1, 1)
        # interpolate between the two surrounding stops
        r, g, b = colours[-1][1]
        for i in range(len(colours) - 1):
            f0, c0 = colours[i]
            f1, c1 = colours[i + 1]
            if f0 <= frac <= f1:
                t = (frac - f0) / max(f1 - f0, 1e-9)
                r = int(c0[0] + t * (c1[0] - c0[0]))
                g = int(c0[1] + t * (c1[1] - c0[1]))
                b = int(c0[2] + t * (c1[2] - c0[2]))
                break
        x = bar_x0 + px_x
        d.line([(x, bar_y0), (x, bar_y0 + bar_height)], fill=(r, g, b, 255))

    # border around bar
    d.rectangle(
        [bar_x0, bar_y0, bar_x1, bar_y0 + bar_height],
        outline=(100, 100, 100, 200),
    )

    # tick labels
    tick_y = bar_y0 + bar_height + 4
    span = vmax - vmin
    for i in range(num_ticks):
        frac = i / (num_ticks - 1)
        val = vmin + frac * span
        label = f"{val:.1f} m"
        bbox = d.textbbox((0, 0), label, font=font_label)
        tw = bbox[2] - bbox[0]
        tx = bar_x0 + int(frac * bar_w) - tw // 2
        tx = max(bar_x0, min(tx, bar_x1 - tw))
        d.text((tx, tick_y), label, font=font_label, fill=(50, 50, 50, 255))

    return card
