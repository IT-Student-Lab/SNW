# -*- coding: utf-8 -*-
"""
PDOK CAD Onderlegger (DXF) generator
"""

from __future__ import annotations

import argparse
import html as ihtml
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ezdxf
import requests
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape


# =========================
# Constants / Endpoints
# =========================

SUGGEST = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
LOOKUP = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/lookup"

KADAS_OGC = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1"
BGT_OGC = "https://api.pdok.nl/lv/bgt/ogc/v1"
RD_CRS_URI = "http://www.opengis.net/def/crs/EPSG/0/28992"

WMS_LUCHTFOTO = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
WMS_PLU = "https://service.pdok.nl/kadaster/plu/wms/v1_0"
WMS_KAD = "https://service.pdok.nl/kadaster/kadastralekaart/wms/v5_0?"
WMS_GMK = "https://service.pdok.nl/bzk/bro-geomorfologischekaart/wms/v2_0?"
WMS_BODEM = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
WMS_TOPO = "https://service.pdok.nl/brt/topraster/wms/v1_0"
WMS_AHN = "https://service.pdok.nl/rws/ahn/wms/v1_0"
WMS_WDM = "https://service.pdok.nl/bzk/bro-grondwaterspiegeldiepte/wms/v2_0"

BODEM_GPKG_PATH = Path("BRO-SGM-DownloadBodemkaart-V2024-01_1.gpkg")


# =========================
# Types
# =========================

BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class MapRequest:
    bbox: BBox
    width: int = 2000
    height: int = 2000
    crs: str = "EPSG:28992"
    version: str = "1.3.0"
    fmt: str = "image/png"
    transparent: bool = True


@dataclass
class ExportPlan:
    filename: str
    bbox: BBox
    dxf_layer: str
    default_on: bool = False


# =========================
# Small utilities
# =========================

def bbox_center(b: BBox) -> Tuple[float, float]:
    minx, miny, maxx, maxy = b
    return (0.5 * (minx + maxx), 0.5 * (miny + maxy))


def _bbox_str(b: BBox) -> str:
    return ",".join(f"{x:.6f}" for x in b)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _log(msg: str) -> None:
    print(msg, flush=True)


# =========================
# Locatieserver: adres -> RD
# =========================

def address_to_rd(address: str, session: Optional[requests.Session] = None) -> Tuple[float, float]:
    s = session or requests.Session()

    r = s.get(SUGGEST, params={"q": address}, timeout=30)
    r.raise_for_status()
    docs = r.json().get("response", {}).get("docs", [])
    if not docs:
        raise ValueError(f"Geen resultaat voor adres: {address!r}")
    loc_id = docs[0]["id"]

    r = s.get(LOOKUP, params={"id": loc_id}, timeout=30)
    r.raise_for_status()
    doc = r.json()["response"]["docs"][0]

    rd = doc["centroide_rd"]
    cleaned = re.sub(r"[^0-9. ]", "", rd).strip()
    x, y = map(float, cleaned.split())
    return x, y


def bbox_around_point(x: float, y: float, radius_m: float) -> BBox:
    return (x - radius_m, y - radius_m, x + radius_m, y + radius_m)


# =========================
# WMS helpers
# =========================

def wms_base_params(req: MapRequest) -> Dict[str, str]:
    return {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": req.version,
        "CRS": req.crs,
        "BBOX": _bbox_str(req.bbox),
        "WIDTH": str(req.width),
        "HEIGHT": str(req.height),
        "FORMAT": req.fmt,
        "TRANSPARENT": "TRUE" if req.transparent else "FALSE",
    }


def wms_get_image(wms_url: str, params: Dict[str, str], session: Optional[requests.Session] = None) -> Image.Image:
    s = session or requests.Session()
    r = s.get(wms_url, params=params, timeout=60)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype:
        snippet = r.text[:800]
        raise ValueError(
            "WMS response is not an image.\n"
            f"URL: {r.url}\n"
            f"Content-Type: {ctype}\n"
            f"First chars:\n{snippet}"
        )
    return Image.open(BytesIO(r.content)).convert("RGBA")


def wms_getlegendgraphic(
    wms_url: str,
    layer: str,
    style: str = "",
    version: str = "1.3.0",
    session: Optional[requests.Session] = None,
) -> Image.Image:
    s = session or requests.Session()
    params: Dict[str, str] = {
        "SERVICE": "WMS",
        "REQUEST": "GetLegendGraphic",
        "VERSION": version,
        "FORMAT": "image/png",
        "LAYER": layer,
    }
    if style:
        params["STYLE"] = style

    r = s.get(wms_url, params=params, timeout=60)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype:
        raise ValueError(
            f"Legend response is not an image for layer={layer!r}. "
            f"Content-Type={ctype}. First chars={r.text[:300]!r}"
        )

    try:
        return Image.open(BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        raise ValueError(
            f"Legend image kon niet worden gelezen voor layer={layer!r}. "
            f"Content-Type={ctype}"
        ) from e


def wms_legend_from_capabilities(wms_url: str, layer: str, session: Optional[requests.Session] = None) -> Image.Image:
    s = session or requests.Session()
    cap = s.get(wms_url, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"}, timeout=60)
    cap.raise_for_status()
    root = ET.fromstring(cap.text)

    for lyr in root.iter():
        if not lyr.tag.endswith("Layer"):
            continue
        name_el = lyr.find("./{*}Name")
        if name_el is None or (name_el.text or "").strip() != layer:
            continue

        online = lyr.find(".//{*}Style/{*}LegendURL/{*}OnlineResource")
        if online is None:
            raise ValueError(f"Geen LegendURL gevonden voor layer={layer!r}")

        href = None
        for k, v in online.attrib.items():
            if k.endswith("href"):
                href = v
                break
        if not href:
            raise ValueError("LegendURL OnlineResource heeft geen href attribuut")

        r = s.get(href, timeout=60)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGBA")

    raise ValueError(f"Layer {layer!r} niet gevonden in GetCapabilities")


# =========================
# Raster post-processing
# =========================

def crop_image_to_bbox(img: Image.Image, bbox_render: BBox, bbox_target: BBox) -> Image.Image:
    minx_r, miny_r, maxx_r, maxy_r = bbox_render
    minx_t, miny_t, maxx_t, maxy_t = bbox_target
    w, h = img.size

    x0 = math.floor((minx_t - minx_r) / (maxx_r - minx_r) * w)
    x1 = math.ceil((maxx_t - minx_r) / (maxx_r - minx_r) * w)

    y0 = math.floor((maxy_r - maxy_t) / (maxy_r - miny_r) * h)
    y1 = math.ceil((maxy_r - miny_t) / (maxy_r - miny_r) * h)

    x0 = _clamp(x0, 0, w)
    x1 = _clamp(x1, 0, w)
    y0 = _clamp(y0, 0, h)
    y1 = _clamp(y1, 0, h)

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
        legend = legend.resize((max_w, int(legend.size[1] * s)), Image.Resampling.LANCZOS)

    if add_white_box:
        box_w = legend.size[0] + 2 * box_padding
        box_h = legend.size[1] + 2 * box_padding
        box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 220))
        box.paste(legend, (box_padding, box_padding), legend)
        legend = box

    W, H = base.size
    w, h = legend.size

    if position == "bottom-right":
        x, y = W - w - margin, H - h - margin
    elif position == "bottom-left":
        x, y = margin, H - h - margin
    elif position == "top-right":
        x, y = W - w - margin, margin
    elif position == "top-left":
        x, y = margin, margin
    else:
        raise ValueError("position must be one of: bottom-right, bottom-left, top-right, top-left")

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


# =========================
# Pretty legend helpers
# =========================

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
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


def build_pretty_legend(
    rows: List[Dict[str, Any]],
    *,
    title: str,
    subtitle: Optional[str] = None,
    width: int = 900,
    show_percent: bool = True,
) -> Image.Image:
    try:
        font_title = ImageFont.truetype("arial.ttf", 28)
        font_sub = ImageFont.truetype("arial.ttf", 18)
        font_row = ImageFont.truetype("arial.ttf", 20)
        font_pct = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_row = ImageFont.load_default()
        font_pct = ImageFont.load_default()

    pad = 22
    top_pad = 18
    swatch = 26
    row_gap = 10
    line_gap = 4
    pct_w = 88 if show_percent else 0
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

    d.rounded_rectangle([6, 6, width - 1, height - 1], radius=18, fill=(0, 0, 0, 40))
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
    """
    Zet een standaard verticale legenda in een nette compacte kaart.
    Vooral bedoeld voor hele lange WMS-legenda's zoals bodemvlakken.
    """
    legend = legend_img.convert("RGBA")

    # eerst iets vergroten voor leesbaarheid
    legend = legend.resize(
        (max(1, int(legend.size[0] * scale)), max(1, int(legend.size[1] * scale))),
        Image.Resampling.LANCZOS,
    )

    # hard cap op breedte/hoogte
    ratio = min(max_width / legend.size[0], max_height / legend.size[1], 1.0)
    if ratio < 1.0:
        legend = legend.resize(
            (max(1, int(legend.size[0] * ratio)), max(1, int(legend.size[1] * ratio))),
            Image.Resampling.LANCZOS,
        )

    pad = 18
    header_h = 52
    width = legend.size[0] + 2 * pad
    height = legend.size[1] + 2 * pad + header_h

    card = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    d = ImageDraw.Draw(card)

    try:
        font_title = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font_title = ImageFont.load_default()

    # schaduw
    d.rounded_rectangle(
        [5, 5, width - 1, height - 1],
        radius=16,
        fill=(0, 0, 0, 35),
    )

    # witte kaart
    d.rounded_rectangle(
        [0, 0, width - 6, height - 6],
        radius=16,
        fill=(255, 255, 255, 238),
        outline=(185, 185, 185, 180),
        width=1,
    )

    d.text((pad, 14), title, font=font_title, fill=(20, 20, 20, 255))
    d.line((pad, header_h, width - pad - 6, header_h), fill=(210, 210, 210, 255), width=1)

    x = (width - legend.size[0]) // 2
    y = header_h + pad
    card.paste(legend, (x, y), legend)
    return card


# =========================
# GMK legend builder
# =========================

def extract_dominant_colors(img: Image.Image, n: int = 10, sample: int = 6) -> List[Tuple[Tuple[int, int, int], float]]:
    im = img.convert("RGBA")
    if sample > 1:
        im = im.resize((max(1, im.size[0] // sample), max(1, im.size[1] // sample)), Image.Resampling.NEAREST)

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
    tmp.putdata(filtered + [(255, 255, 255)] * (total_px - len(filtered)))

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


def gmk_getfeatureinfo(
    bbox: BBox,
    width: int,
    height: int,
    i: int,
    j: int,
    layer: str = "geomorphological_area",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    s = session or requests.Session()
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:28992",
        "BBOX": _bbox_str(bbox),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "STYLES": "",
        "FORMAT": "image/png",
        "INFO_FORMAT": "application/json",
        "I": str(int(i)),
        "J": str(int(j)),
        "FEATURE_COUNT": "1",
    }
    r = s.get(WMS_GMK, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def gmk_label_at_pixel(
    bbox: BBox,
    width: int,
    height: int,
    x: int,
    y: int,
    layer: str = "geomorphological_area",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    data = gmk_getfeatureinfo(bbox, width, height, x, y, layer=layer, session=session)
    feats = data.get("features", []) or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    code = props.get("landform_subgroup_code")
    desc = props.get("landform_subgroup_description")
    if code and desc:
        return f"{code} — {desc}"
    if code:
        return str(code)
    return None


# =========================
# OGC API Features helpers
# =========================

def ogc_get_all_features(
    base_url: str,
    collection: str,
    bbox: BBox,
    bbox_crs: str = RD_CRS_URI,
    limit: int = 1000,
    timeout: int = 30,
    response_crs: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    s = session or requests.Session()
    url = f"{base_url.rstrip('/')}/collections/{collection}/items"

    params: Optional[Dict[str, str]] = {
        "bbox": _bbox_str(bbox),
        "bbox-crs": bbox_crs,
        "limit": str(limit),
        "f": "json",
    }
    if response_crs:
        params["crs"] = response_crs

    features: List[Dict[str, Any]] = []
    while True:
        try:
            r = s.get(url, params=params, timeout=timeout)
            r.raise_for_status()
        except requests.HTTPError as e:
            if params and "crs" in params:
                params = dict(params)
                params.pop("crs", None)
                r = s.get(url, params=params, timeout=timeout)
                r.raise_for_status()
            else:
                raise e

        data = r.json()
        features.extend(data.get("features", []) or [])

        next_url = None
        for link in data.get("links", []) or []:
            if link.get("rel") == "next" and link.get("href"):
                next_url = link["href"]
                break
        if not next_url:
            break

        url = next_url
        params = None

    return features


# =========================
# DXF helpers
# =========================

def ensure_layer(doc: ezdxf.EzDxf, name: str, *, color: int = 7) -> None:
    if name not in doc.layers:
        doc.layers.new(name=name, dxfattribs={"color": color})


def ensure_layer_onoff(doc: ezdxf.EzDxf, name: str, *, default_on: bool, color: int = 7) -> None:
    ensure_layer(doc, name, color=color)
    lyr = doc.layers.get(name)
    try:
        lyr.thaw()
        if default_on:
            lyr.on()
        else:
            lyr.off()
    except Exception:
        pass


def safe_layer_name(s: str, prefix: str = "BGT-") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
    return f"{prefix}{s}"[:255]


def add_any_geom_to_dxf(msp: ezdxf.layouts.Modelspace, geom, layer: str) -> None:
    if geom is None or geom.is_empty:
        return

    minx, miny, maxx, maxy = geom.bounds
    if not (0 <= minx <= 300000 and 0 <= maxx <= 300000 and 300000 <= miny <= 650000 and 300000 <= maxy <= 650000):
        return

    gtype = geom.geom_type

    if gtype == "Point":
        msp.add_point((float(geom.x), float(geom.y)), dxfattribs={"layer": layer})
        return
    if gtype == "MultiPoint":
        for p in geom.geoms:
            msp.add_point((float(p.x), float(p.y)), dxfattribs={"layer": layer})
        return

    if gtype == "LineString":
        coords = [(float(x), float(y)) for x, y in geom.coords]
        if len(coords) >= 2:
            msp.add_lwpolyline(coords, dxfattribs={"layer": layer})
        return
    if gtype == "MultiLineString":
        for ls in geom.geoms:
            add_any_geom_to_dxf(msp, ls, layer)
        return

    if gtype == "Polygon":
        ring = [(float(x), float(y)) for x, y in geom.exterior.coords]
        if len(ring) >= 3:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
        return
    if gtype == "MultiPolygon":
        for poly in geom.geoms:
            add_any_geom_to_dxf(msp, poly, layer)
        return

    if gtype == "GeometryCollection":
        for gg in geom.geoms:
            add_any_geom_to_dxf(msp, gg, layer)
        return


def add_georef_image_to_doc(
    doc: ezdxf.EzDxf,
    image_path: Path,
    bbox_rd: BBox,
    layer: str,
    fade: int = 0,
    contrast: int = 50,
    brightness: int = 50,
) -> None:
    minx, miny, maxx, maxy = bbox_rd
    width_units = float(maxx - minx)
    height_units = float(maxy - miny)

    rel = image_path.name

    with Image.open(image_path) as im:
        w_px, h_px = im.size

    img_def = doc.add_image_def(filename=rel, size_in_pixel=(w_px, h_px))

    image_entity = doc.modelspace().add_image(
        img_def,
        insert=(minx, miny),
        size_in_units=(width_units, height_units),
        rotation=0,
        dxfattribs={"layer": layer},
    )

    image_entity.transparency = 0.0

    try:
        image_entity.dxf.fade = int(fade)
        image_entity.dxf.contrast = int(contrast)
        image_entity.dxf.brightness = int(brightness)
    except Exception:
        pass

    try:
        doc.objects.add_image_def_reactor(img_def.dxf.handle, image_entity.dxf.handle)
    except Exception:
        pass


def write_layer_toggle_scripts(doc: ezdxf.EzDxf, dxf_out: Path, prefix: str = "BGT-") -> Tuple[Path, Path]:
    layers = [lyr.dxf.name for lyr in doc.layers if lyr.dxf.name.startswith(prefix)]
    if not layers:
        raise ValueError(f"Geen layers gevonden met prefix {prefix!r}")

    folder = dxf_out.parent
    scr_on = folder / "toggle_BGT_AAN.scr"
    scr_off = folder / "toggle_BGT_UIT.scr"

    def make_lines(turn: str) -> List[str]:
        lines: List[str] = []
        for ln in layers:
            lines += ["_.-LAYER", f"_{turn}", ln, ""]
        lines += ["_REGEN", ""]
        return lines

    scr_on.write_text("\n".join(make_lines("ON")), encoding="utf-8")
    scr_off.write_text("\n".join(make_lines("OFF")), encoding="utf-8")
    return scr_on, scr_off


# =========================
# Downloads: specific layers
# =========================

def download_luchtfoto(bbox: BBox, out_path: Path, *, px: int = 2000, session: Optional[requests.Session] = None) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(WMS_LUCHTFOTO, {**wms_base_params(req), "LAYERS": "Actueel_orthoHR", "STYLES": ""}, session=session)
    img.save(out_path)


def download_plu_enkel(bbox: BBox, out_path: Path, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(
        WMS_PLU,
        {**wms_base_params(req), "LAYERS": "enkelbestemming", "STYLES": "enkelbestemming"},
        session=session,
    )


def download_plu_dubbel(bbox: BBox, out_path: Path, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(
        WMS_PLU,
        {**wms_base_params(req), "LAYERS": "dubbelbestemming", "STYLES": "dubbelbestemming"},
        session=session,
    )


def download_kadastrale_kaart(bbox: BBox, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(WMS_KAD, {**wms_base_params(req), "LAYERS": "kadastralekaart", "STYLES": ""}, session=session)


def download_topo_image(bbox: BBox, *, px: int = 4000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    img = wms_get_image(
        WMS_TOPO,
        {**wms_base_params(req), "LAYERS": "top25raster", "STYLES": ""},
        session=session,
    )
    return img.convert("RGBA")


def download_gmk_with_dominant_legend(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    top_k: int = 6,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    base = wms_get_image(
        WMS_GMK,
        {**wms_base_params(req), "LAYERS": "geomorphological_area", "STYLES": ""},
        session=session,
    )

    dominant = extract_dominant_colors(base, n=10, sample=6)
    rows: List[Dict[str, Any]] = []
    for (rgb, frac) in dominant[:top_k]:
        pt = find_representative_pixel(base, rgb, tol=12)
        label = None
        if pt is not None:
            x, y = pt
            label = gmk_label_at_pixel(bbox, px, px, x, y, session=session)
        if label and "—" in label:
            label = label.split("—", 1)[1].strip()
        rows.append({"rgb": rgb, "pct": frac * 100.0, "label": label})

    legend = build_pretty_legend(
        rows,
        title="Geomorfologie",
        subtitle="Dominante klassen binnen het geselecteerde gebied",
        width=920,
        show_percent=True,
    )

    out = place_legend_on_image(
        base,
        legend,
        position="bottom-right",
        legend_scale=1.0,
        legend_max_width_ratio=0.48,
        add_white_box=False,
    )
    out.save(out_path)


def download_ahn(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    product: str = "dtm",
    add_legend: bool = True,
    session: Optional[requests.Session] = None,
) -> None:
    product = product.lower().strip()
    layer_map = {"dtm": "dtm_05m", "dsm": "dsm_05m"}
    if product not in layer_map:
        raise ValueError(f"Onbekend product {product!r}, kies uit {list(layer_map)}")

    layer = layer_map[product]
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(
        WMS_AHN,
        {**wms_base_params(req), "LAYERS": layer, "STYLES": "default"},
        session=session,
    )

    if add_legend:
        try:
            try:
                raw_legend = wms_getlegendgraphic(WMS_AHN, layer, style="default", session=session)
            except Exception:
                raw_legend = wms_legend_from_capabilities(WMS_AHN, layer, session=session)

            legend = extract_rows_from_vertical_legend(
                raw_legend,
                title=f"AHN {product.upper()}",
                max_width=1100,
                max_height=900,
                scale=1.65,
            )
            img = place_legend_on_image(
                img,
                legend,
                position="bottom-right",
                legend_scale=1.0,
                legend_max_width_ratio=0.58,
                add_white_box=False,
            )
        except Exception as e:
            _log(f"[WARN] AHN legenda ophalen mislukt ({layer}): {e}")

    img.save(out_path)


def bodem_getfeatureinfo(
    bbox: BBox,
    width: int,
    height: int,
    i: int,
    j: int,
    layer: str = "soilarea",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    s = session or requests.Session()
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:28992",
        "BBOX": _bbox_str(bbox),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "STYLES": "",
        "FORMAT": "image/png",
        "INFO_FORMAT": "application/json",
        "I": str(int(i)),
        "J": str(int(j)),
        "FEATURE_COUNT": "1",
    }
    r = s.get(WMS_BODEM, params=params, timeout=30)
    r.raise_for_status()
    return r.json()



def normalize_bodem_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("\u00a0", " ").strip()
    m = re.search(r"\b([A-Za-z]{1,4}[A-Za-z]?[0-9]{0,4}[A-Za-z0-9-]*)\b", s)
    if not m:
        return None
    return m.group(1).upper()


def _find_candidate_tables(con: sqlite3.Connection) -> List[str]:
    q = (
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'gpkg_%' "
        "AND name NOT LIKE 'rtree_%' "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [r[0] for r in con.execute(q).fetchall()]


def _table_columns(con: sqlite3.Connection, table_name: str) -> List[str]:
    q = f"PRAGMA table_info('{table_name}')"
    return [r[1] for r in con.execute(q).fetchall()]


def load_all_bodem_code_map(gpkg_path: Path) -> Dict[str, str]:
    if not gpkg_path.exists():
        _log(f"[WARN] Bodem GPKG niet gevonden: {gpkg_path}")
        return {}

    con = sqlite3.connect(str(gpkg_path))
    con.row_factory = sqlite3.Row
    mapping: Dict[str, str] = {}

    try:
        tables = _find_candidate_tables(con)
        interesting_tables: List[str] = []

        for t in tables:
            cols = [c.lower() for c in _table_columns(con, t)]
            joined = " ".join(cols)
            if (
                "soilunit" in joined
                or "soilcode" in joined
                or "legend" in joined
                or ("code" in joined and "description" in joined)
            ):
                interesting_tables.append(t)

        for table in interesting_tables:
            cols = _table_columns(con, table)
            cols_l = {c.lower(): c for c in cols}

            code_candidates = [
                "soilunit_code",
                "soilcode",
                "code",
                "first_soilcode",
                "maplegend_code",
                "legend_code",
            ]
            desc_candidates = [
                "soilunit_code_description",
                "soilunit_description",
                "soilunitname",
                "description",
                "naam",
                "legend_text",
                "maplegend_text",
            ]

            code_col = next((cols_l[c] for c in code_candidates if c in cols_l), None)
            desc_col = next((cols_l[c] for c in desc_candidates if c in cols_l), None)

            if not code_col or not desc_col:
                continue

            q = (
                f'SELECT DISTINCT "{code_col}" AS code, "{desc_col}" AS descr '
                f'FROM "{table}" '
                f'WHERE "{code_col}" IS NOT NULL AND "{desc_col}" IS NOT NULL'
            )

            for row in con.execute(q):
                code = normalize_bodem_code(row["code"])
                descr = str(row["descr"]).strip() if row["descr"] is not None else ""
                if code and descr and len(descr) > 3:
                    mapping[code] = descr

        _log(f"[BODEM] {len(mapping)} codes geladen uit {gpkg_path.name}")
        return dict(sorted(mapping.items()))
    finally:
        con.close()


BODEM_CODE_MAP: Dict[str, str] = load_all_bodem_code_map(BODEM_GPKG_PATH)


def bodem_code_to_label(value: Any) -> Optional[str]:
    code = normalize_bodem_code(value)
    if not code:
        return None
    return BODEM_CODE_MAP.get(code)


def looks_like_only_bodem_code(value: Any) -> bool:
    code = normalize_bodem_code(value)
    if not code:
        return False
    return str(value).strip().upper() == code


def bodem_label_from_properties(
    props: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    descriptive_keys = [
        "soilunit_code_description",
        "soilunit_description",
        "soilunitname",
        "soil_name",
        "bodemeenheid",
        "naam",
        "description",
        "legend_text",
        "maplegend",
        "maplegend_text",
    ]

    for k in descriptive_keys:
        v = props.get(k)
        if not v:
            continue
        v_str = str(v).strip()
        if not v_str:
            continue

        mapped = bodem_code_to_label(v_str)
        if mapped:
            return mapped

        if len(v_str) > 8 and not looks_like_only_bodem_code(v_str):
            return v_str

    code_keys = [
        "soilunit_code",
        "code",
        "legend_code",
        "maplegend_code",
    ]
    for k in code_keys:
        v = props.get(k)
        mapped = bodem_code_to_label(v)
        if mapped:
            return mapped

    for _, v in props.items():
        mapped = bodem_code_to_label(v)
        if mapped:
            return mapped

    for _, v in props.items():
        if isinstance(v, str):
            s = v.strip()
            if s and len(s) > 8 and not looks_like_only_bodem_code(s):
                return s

    for k in code_keys:
        v = props.get(k)
        if v:
            return str(v).strip()

    return "Onbekende bodemklasse"

def bodem_label_at_pixel(
    bbox: BBox,
    width: int,
    height: int,
    x: int,
    y: int,
    layer: str = "soilarea",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    data = bodem_getfeatureinfo(bbox, width, height, x, y, layer=layer, session=session)
    feats = data.get("features", []) or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    return bodem_label_from_properties(props, session=session)



def download_bodemvlakken_with_dominant_legend(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    top_k: int = 6,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    base = wms_get_image(
        WMS_BODEM,
        {**wms_base_params(req), "LAYERS": "soilarea", "STYLES": ""},
        session=session,
    )

    dominant = extract_dominant_colors(base, n=18, sample=6)
    grouped: Dict[str, Dict[str, Any]] = {}

    for (rgb, frac) in dominant:
        pt = find_representative_pixel(base, rgb, tol=12)
        label = None
        if pt is not None:
            x, y = pt
            label = bodem_label_at_pixel(bbox, px, px, x, y, layer="soilarea", session=session)

        if label and "—" in label:
            label = label.split("—", 1)[1].strip()

        label = label or "Onbekende bodemklasse"
        bucket = grouped.setdefault(label, {"rgb": rgb, "pct": 0.0, "label": label})
        bucket["pct"] += frac * 100.0

    rows = sorted(grouped.values(), key=lambda r: float(r["pct"]), reverse=True)
    rows = [r for r in rows if float(r["pct"]) >= 0.5][:top_k]

    legend = build_pretty_legend(
        rows,
        title="Bodemvlakken",
        subtitle="Dominante klassen binnen het geselecteerde gebied",
        width=920,
        show_percent=True,
    )

    out = place_legend_on_image(
        base,
        legend,
        position="bottom-right",
        legend_scale=1.0,
        legend_max_width_ratio=0.48,
        add_white_box=False,
    )
    out.save(out_path)



def wdm_legend_image(layer: str, session: Optional[requests.Session] = None) -> Image.Image:
    return wms_legend_from_capabilities(WMS_WDM, layer, session=session)


def download_wdm(
    bbox: BBox,
    out_path: Path,
    *,
    layer: str,
    px: int = 2000,
    add_legend: bool = True,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(WMS_WDM, {**wms_base_params(req), "LAYERS": layer, "STYLES": ""}, session=session)

    if add_legend:
        try:
            raw_legend = wdm_legend_image(layer, session=session)
            nice_title = {
                "bro-grondwaterspiegeldieptemetingen-GHG": "Grondwaterstand (GHG)",
                "bro-grondwaterspiegeldieptemetingen-GLG": "Grondwaterstand (GLG)",
                "bro-grondwaterspiegeldieptemetingen-GT": "Grondwatertrap (GT)",
            }.get(layer, "Grondwater")
            legend = extract_rows_from_vertical_legend(
                raw_legend,
                title=nice_title,
                max_width=1100,
                max_height=900,
                scale=1.65,
            )
            img = place_legend_on_image(
                img,
                legend,
                position="bottom-right",
                legend_scale=1.0,
                legend_max_width_ratio=0.58,
                add_white_box=False,
            )
        except Exception as e:
            _log(f"[WARN] WDM legenda ophalen mislukt ({layer}): {e}")

    img.save(out_path)


# =========================
# PLU composite helpers
# =========================

def get_plu_legend_image(session: Optional[requests.Session] = None) -> Image.Image:
    s = session or requests.Session()
    legend_url = "https://service.pdok.nl/kadaster/plu/wms/v1_0/legend/enkelbestemming/enkelbestemming.png"
    r = s.get(legend_url, timeout=60)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def build_plu_outputs(
    bbox: BBox,
    out_bestemming_percelen: Path,
    out_bestemming_dubbel: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    legend_img = get_plu_legend_image(session=session)

    enkel = download_plu_enkel(bbox, out_bestemming_percelen, px=px, session=session)
    dubbel = download_plu_dubbel(bbox, out_bestemming_dubbel, px=px, session=session)
    kad = download_kadastrale_kaart(bbox, px=px, session=session)

    plu_plus_percelen = Image.alpha_composite(enkel, kad)
    bestemming_kadaster = place_legend_on_image(
        base=plu_plus_percelen,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.0,
        legend_max_width_ratio=0.2,
    )
    bestemming_kadaster.save(out_bestemming_percelen)

    # dubbelbestemming laag op enkelbestemming zetten
    enkel_plus_dubbel = Image.alpha_composite(enkel, dubbel)

    bestemmingdubbel = place_legend_on_image(
        base=enkel_plus_dubbel,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.0,
        legend_max_width_ratio=0.2,
    )

    bestemmingdubbel.save(out_bestemming_dubbel)    


# =========================
# BGT / Kadaster export
# =========================

def bgt_list_collections(session: Optional[requests.Session] = None) -> List[str]:
    s = session or requests.Session()
    url = f"{BGT_OGC.rstrip('/')}/collections"
    r = s.get(url, params={"f": "json"}, timeout=30)
    r.raise_for_status()
    cols = r.json().get("collections", []) or []
    return [c["id"] for c in cols if "id" in c]


def add_all_bgt_to_dxf(
    doc: ezdxf.EzDxf,
    msp: ezdxf.layouts.Modelspace,
    bbox: BBox,
    limit_per_collection: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    col_ids = bgt_list_collections(session=session)
    _log(f"[BGT] collections: {len(col_ids)}")

    colors = [1, 2, 3, 4, 5, 6, 7]
    for idx, cid in enumerate(col_ids):
        layer = safe_layer_name(cid, prefix="BGT-")
        ensure_layer_onoff(doc, layer, default_on=False, color=colors[idx % len(colors)])

        feats = ogc_get_all_features(
            BGT_OGC,
            cid,
            bbox,
            bbox_crs=RD_CRS_URI,
            response_crs=RD_CRS_URI,
            limit=limit_per_collection,
            session=session,
        )
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            add_any_geom_to_dxf(msp, geom, layer=layer)


def add_kadaster_percelen_to_dxf(
    doc: ezdxf.EzDxf,
    msp: ezdxf.layouts.Modelspace,
    bbox: BBox,
    limit: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    layer = "01_KAD_PERCELEN"
    ensure_layer(doc, layer, color=3)

    feats = ogc_get_all_features(
        KADAS_OGC,
        "perceel",
        bbox,
        bbox_crs=RD_CRS_URI,
        response_crs=RD_CRS_URI,
        limit=limit,
        session=session,
    )
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        geom = shape(g)
        add_any_geom_to_dxf(msp, geom, layer=layer)


# =========================
# DXF export orchestration
# =========================

def export_dxf(
    out_dxf: Path,
    *,
    bbox: BBox,
    raster_dir: Path,
    rasters: List[ExportPlan],
    include_percelen: bool = True,
    include_bgt: bool = True,
    bgt_limit_per_collection: int = 2000,
    session: Optional[requests.Session] = None,
) -> Path:
    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()

    for rp in rasters:
        minx, miny, maxx, maxy = rp.bbox
        _log(f"[DXF IMG] {rp.filename} layer={rp.dxf_layer} bbox={rp.bbox} span=({maxx-minx:.1f}m, {maxy-miny:.1f}m)")
        ensure_layer_onoff(doc, rp.dxf_layer, default_on=rp.default_on, color=7)
        add_georef_image_to_doc(doc, raster_dir / rp.filename, rp.bbox, layer=rp.dxf_layer)

    if include_percelen:
        add_kadaster_percelen_to_dxf(doc, msp, bbox, session=session)

    if include_bgt:
        add_all_bgt_to_dxf(doc, msp, bbox, limit_per_collection=bgt_limit_per_collection, session=session)

    doc.saveas(out_dxf)

    if include_bgt:
        scr_on = out_dxf.parent / "toggle_BGT_AAN.scr"
        scr_off = out_dxf.parent / "toggle_BGT_UIT.scr"

        if not scr_on.exists() or not scr_off.exists():
            try:
                scr_on, scr_off = write_layer_toggle_scripts(doc, out_dxf, prefix="BGT-")
                _log(f"AutoCAD scripts geschreven:\n - {scr_on}\n - {scr_off}")
            except Exception as e:
                _log(f"[WARN] Kon BGT toggle scripts niet schrijven: {e}")

    return out_dxf


# =========================
# Main pipeline
# =========================

def build_all_outputs(
    bbox: BBox,
    out_dir: Path,
    *,
    px: int = 2000,
    topo_px: int = 4000,
    topo_min_span_m: float = 3000.0,
    session: Optional[requests.Session] = None,
) -> Tuple[List[ExportPlan], Optional[BBox]]:
    _ensure_dir(out_dir)

    fn_luchtfoto = "Luchtfoto.png"
    fn_luchtfoto_kad = "luchtfoto_kadaster.png"
    fn_topo = "topo_kaart.png"
    fn_best_enkel = "Bestemming_percelen.png"
    fn_best_dubbel = "Bestemming_dubbel.png"
    fn_gmk = "Geomorfologische_kaart.png"
    fn_ahn_dsm = "ahn_dsm.png"
    fn_ahn_dtm = "ahn_dtm.png"
    fn_wdm_ghg = "wdm_ghg.png"
    fn_wdm_glg = "wdm_glg.png"
    fn_wdm_gt = "wdm_gt.png"
    fn_bodemvlakken = "bodemvlakken.png"

    _log("[DL] Luchtfoto")
    download_luchtfoto(bbox, out_dir / fn_luchtfoto, px=px, session=session)

    _log("[DL] Kadastrale kaart (WMS)")
    kad = download_kadastrale_kaart(bbox, px=px, session=session)
    lucht = Image.open(out_dir / fn_luchtfoto).convert("RGBA")
    lucht_plus = Image.alpha_composite(lucht, kad)
    lucht_plus.save(out_dir / fn_luchtfoto_kad)

    _log("[DL] PLU (bestemmingsplan)")
    build_plu_outputs(bbox, out_dir / fn_best_enkel, out_dir / fn_best_dubbel, px=px, session=session)

    _log("[DL] TOPraster (download radius 3000m, crop naar project-bbox)")
    cx, cy = bbox_center(bbox)
    topo_radius = max(3000.0, topo_min_span_m / 2.0)
    bbox_topo_big = bbox_around_point(cx, cy, topo_radius)

    topo_big = download_topo_image(bbox_topo_big, px=topo_px, session=session)
    topo_cropped = crop_image_to_bbox(topo_big, bbox_render=bbox_topo_big, bbox_target=bbox)
    topo_cropped.convert("RGB").save(out_dir / fn_topo)

    bbox_topo_for_dxf = bbox

    _log("[DL] GMK")
    download_gmk_with_dominant_legend(bbox, out_dir / fn_gmk, px=px, session=session)

    _log("[DL] AHN DSM/DTM")
    download_ahn(bbox, out_dir / fn_ahn_dsm, px=px, product="dsm", add_legend=True, session=session)
    download_ahn(bbox, out_dir / fn_ahn_dtm, px=px, product="dtm", add_legend=True, session=session)

    _log("[DL] WDM (grondwater)")
    download_wdm(bbox, out_dir / fn_wdm_ghg, layer="bro-grondwaterspiegeldieptemetingen-GHG", px=px, session=session)
    download_wdm(bbox, out_dir / fn_wdm_glg, layer="bro-grondwaterspiegeldieptemetingen-GLG", px=px, session=session)
    download_wdm(bbox, out_dir / fn_wdm_gt, layer="bro-grondwaterspiegeldieptemetingen-GT", px=px, session=session)

    _log("[DL] Bodem")
    download_bodemvlakken_with_dominant_legend(bbox, out_dir / fn_bodemvlakken, px=px, session=session)

    rasters = [
        ExportPlan(fn_topo, bbox_topo_for_dxf, "$$_00-00-00_onderlegger_Topokaart", default_on=False),
        ExportPlan(fn_luchtfoto_kad, bbox, "$$_00-00-00_onderlegger_Luchtfoto met kadastrale kaart V5", default_on=False),
        ExportPlan(fn_luchtfoto, bbox, "$$_00-00-00_onderlegger_Luchtfoto (actueel)", default_on=True),
        ExportPlan(fn_wdm_ghg, bbox, "$$_00-00-00_onderlegger_Grondwaterstand (GHG)", default_on=False),
        ExportPlan(fn_wdm_glg, bbox, "$$_00-00-00_onderlegger_Grondwaterstand (GLG)", default_on=False),
        ExportPlan(fn_wdm_gt, bbox, "$$_00-00-00_onderlegger_Grondwaterstand (GT)", default_on=False),
        ExportPlan(fn_gmk, bbox, "$$_00-00-00_onderlegger_Geomorfologische kaart", default_on=False),
        ExportPlan(fn_best_enkel, bbox, "$$_00-00-00_onderlegger_Bestemmingsplankaart (Enkelbestemming)", default_on=False),
        ExportPlan(fn_best_dubbel, bbox, "$$_00-00-00_onderlegger_Bestemmingsplankaart (Dubbelbestemming)", default_on=False),
        ExportPlan(fn_ahn_dsm, bbox, "$$_00_00_00_onderlegger_Hoogtekaart (AHN 4 - DSM)", default_on=False),
        ExportPlan(fn_ahn_dtm, bbox, "$$_00_00_00_onderlegger_Hoogtekaart (AHN 4 - DTM)", default_on=False),
        ExportPlan(fn_bodemvlakken, bbox, "$$_00-00-00_onderlegger_Bodemvlakken", default_on=False),
    ]

    return rasters, bbox_topo_for_dxf


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genereer PDOK CAD onderlegger (DXF) met raster + vector overlays.")
    p.add_argument("--adres", type=str, default="", help="Adres voor centrum.")
    p.add_argument("--x", type=float, default=None, help="RD X (als je geen adres wilt).")
    p.add_argument("--y", type=float, default=None, help="RD Y (als je geen adres wilt).")
    p.add_argument("--radius", type=float, default=250.0, help="Radius in meters rond centrum voor bbox.")
    p.add_argument("--outdir", type=str, default="output_onderlegger", help="Output map voor PNGs + DXF.")
    p.add_argument("--dxf", type=str, default="onderlegger.dxf", help="DXF bestandsnaam (in outdir).")

    p.add_argument("--px", type=int, default=2000, help="Raster resolutie voor meeste lagen.")
    p.add_argument("--topo-px", type=int, default=4000, help="Raster resolutie voor topo download.")
    p.add_argument("--topo-min-span", type=float, default=3000.0, help="Minimum bbox-span voor topo.")

    p.add_argument("--no-bgt", action="store_true", help="Voeg geen BGT vectorlagen toe.")
    p.add_argument("--no-percelen", action="store_true", help="Voeg geen Kadaster percelen vectorlaag toe.")
    p.add_argument("--bgt-limit", type=int, default=2000, help="Max features per BGT collection.")

    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.outdir).resolve()
    _ensure_dir(out_dir)

    session = requests.Session()

    if args.x is not None and args.y is not None:
        x, y = float(args.x), float(args.y)
    else:
        if not args.adres.strip():
            raise SystemExit("Geef --adres of (--x en --y).")
        _log(f"[LOC] Adres -> RD: {args.adres}")
        x, y = address_to_rd(args.adres, session=session)
        _log(f"[LOC] RD: x={x:.3f}, y={y:.3f}")

    bbox = bbox_around_point(x, y, float(args.radius))
    _log(f"[BBOX] {bbox}")

    rasters, _bbox_topo = build_all_outputs(
        bbox=bbox,
        out_dir=out_dir,
        px=int(args.px),
        topo_px=int(args.topo_px),
        topo_min_span_m=float(args.topo_min_span),
        session=session,
    )

    out_dxf = out_dir / args.dxf
    _log(f"[DXF] Export -> {out_dxf}")

    export_dxf(
        out_dxf,
        bbox=bbox,
        raster_dir=out_dir,
        rasters=rasters,
        include_percelen=not args.no_percelen,
        include_bgt=not args.no_bgt,
        bgt_limit_per_collection=int(args.bgt_limit),
        session=session,
    )

    _log("[DONE!]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())