# -*- coding: utf-8 -*-
"""
PDOK CAD Onderlegger (DXF) generator

Wat dit script doet
- Adres -> RD (EPSG:28992) via PDOK Locatieserver
- Download rasterlagen (WMS) als PNG (luchtfoto, topo, PLU, kadastraal, GMK, AHN, bodem, grondwater)
- Plakt legenda's in de kaarten waar gewenst
- Exporteert een DXF met:
  - georefererende IMAGE entities voor de rasterlagen
  - vectorlagen voor Kadaster percelen + (optioneel) alle BGT collections
  - AutoCAD .scr scripts om alle BGT layers in één keer aan/uit te zetten

Waarom dit een .py is (geen notebook)
- Alles zit in functies en een main() met argparse
- Je kunt het draaien als: python pdok_cad_onderlegger.py --adres "..." --radius 250

Belangrijke CAD-notes
- AutoCAD is vaak lastig met echte PNG alpha (RGBA). Daarom bieden we een "palette transparency" writer
  (mode='P' + transparency-index). Dit is meestal het meest CAD-compatibel.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    """Welke outputbestanden we schrijven, met bijbehorende bbox en DXF-layernaam."""
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
    return ",".join(map(lambda x: f"{x:.6f}", b))


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
    """
    Zet een adres om naar RD coördinaten (x, y) via PDOK Locatieserver.
    """
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
        "service": "WMS",
        "request": "GetMap",
        "version": req.version,
        "crs": req.crs,
        "bbox": _bbox_str(req.bbox),
        "width": str(req.width),
        "height": str(req.height),
        "format": req.fmt,
        "transparent": "true" if req.transparent else "false",
    }


def wms_get_image(wms_url: str, params: Dict[str, str], session: Optional[requests.Session] = None) -> Image.Image:
    """
    Download WMS GetMap en retourneer PIL.Image (RGBA).
    Print debug als server een ServiceException terugstuurt.
    """
    s = session or requests.Session()
    r = s.get(wms_url, params=params, timeout=60)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype:
        # meestal ServiceException (XML/HTML)
        snippet = r.text[:800]
        raise ValueError(
            "WMS response is not an image.\n"
            f"URL: {r.url}\n"
            f"Content-Type: {ctype}\n"
            f"First chars:\n{snippet}"
        )
    return Image.open(BytesIO(r.content)).convert("RGBA")


def wms_getlegendgraphic(wms_url: str, layer: str, style: str = "", version: str = "1.3.0",
                         session: Optional[requests.Session] = None) -> Image.Image:
    s = session or requests.Session()
    params: Dict[str, str] = {
        "service": "WMS",
        "request": "GetLegendGraphic",
        "version": version,
        "format": "image/png",
        "layer": layer,
    }
    if style:
        params["style"] = style

    r = s.get(wms_url, params=params, timeout=60)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def wms_legend_from_capabilities(wms_url: str, layer: str, session: Optional[requests.Session] = None) -> Image.Image:
    """
    Sommige services hebben geen (werkende) GetLegendGraphic, maar wel LegendURL in GetCapabilities.
    Deze helper pakt die URL.
    """
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
    """
    Crop een WMS image (die bbox_render dekt) naar bbox_target.
    We gebruiken floor/ceil om afrondingsfouten te minimaliseren.
    """
    minx_r, miny_r, maxx_r, maxy_r = bbox_render
    minx_t, miny_t, maxx_t, maxy_t = bbox_target
    w, h = img.size

    x0 = math.floor((minx_t - minx_r) / (maxx_r - minx_r) * w)
    x1 = math.ceil((maxx_t - minx_r) / (maxx_r - minx_r) * w)

    # y-as omgekeerd
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

    # 1) grof schalen
    legend = legend.resize(
        (int(legend.size[0] * legend_scale), int(legend.size[1] * legend_scale)),
        Image.Resampling.LANCZOS,
    )

    # 2) max breedte cap
    max_w = int(base.size[0] * legend_max_width_ratio)
    if legend.size[0] > max_w:
        s = max_w / legend.size[0]
        legend = legend.resize((max_w, int(legend.size[1] * s)), Image.Resampling.LANCZOS)

    # 3) wit vlak
    if add_white_box:
        box_w = legend.size[0] + 2 * box_padding
        box_h = legend.size[1] + 2 * box_padding
        box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 220))
        box.paste(legend, (box_padding, box_padding), legend)
        legend = box

    # 4) position
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
    """
    CAD-robuste save: converteer RGBA alpha -> paletted PNG ('P') met transparency-index 0.

    Dit is bewust 'strict': alpha==0 => index 0, zodat AutoCAD niet op zwart/magenta valt.
    """
    img = img_rgba.convert("RGBA")
    w, h = img.size

    # RGB met alpha-mask (alles buiten alpha wordt zwart)
    rgb = Image.new("RGB", (w, h), (0, 0, 0))
    rgb.paste(img, mask=img.getchannel("A"))

    pal = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)

    # reserveer index 0 (kleur maakt niet uit)
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
# GMK legend builder (dominante klassen)
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


def find_representative_pixel(img: Image.Image, rgb: Tuple[int, int, int], max_samples: int = 200_000, tol: int = 10) -> Optional[Tuple[int, int]]:
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


def gmk_getfeatureinfo(bbox: BBox, width: int, height: int, i: int, j: int,
                       layer: str = "geomorphological_area",
                       session: Optional[requests.Session] = None) -> Dict[str, Any]:
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


def gmk_label_at_pixel(bbox: BBox, width: int, height: int, x: int, y: int,
                       layer: str = "geomorphological_area",
                       session: Optional[requests.Session] = None) -> Optional[str]:
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


def build_color_meaning_legend(rows: List[Dict[str, Any]], title: str = "Geomorfologie (dominante klassen)") -> Image.Image:
    try:
        font = ImageFont.truetype("arial.ttf", 22)
        font_b = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
        font_b = ImageFont.load_default()

    pad = 16
    sw = 28
    line_h = 34
    W = 980
    H = pad * 2 + 50 + len(rows) * line_h + 10

    img = Image.new("RGBA", (W, H), (255, 255, 255, 235))
    d = ImageDraw.Draw(img)
    d.text((pad, pad), title, font=font_b, fill=(0, 0, 0, 255))

    y = pad + 45
    for r in rows:
        rr, gg, bb = r["rgb"]
        d.rectangle([pad, y + 3, pad + sw, y + 3 + sw], fill=(rr, gg, bb, 255), outline=(0, 0, 0, 90))
        d.text((pad + sw + 12, y + 6), f'{r["pct"]:.1f}%', font=font, fill=(0, 0, 0, 255))

        label = r.get("label") or "(onbekend)"
        # liever codes weg? kan via CLI flag later
        if len(label) > 120:
            label = label[:117] + "…"
        d.text((pad + sw + 110, y + 6), label, font=font, fill=(0, 0, 0, 255))
        y += line_h

    return img



# =========================
# OGC API Features helpers (Kadaster/BGT)
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
    """
    Haal alle features op (pagination via rel=next). Probeert output CRS te vragen via 'crs'.
    """
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
            # fallback: sommige servers accepteren 'crs' niet
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
        params = None  # next_url bevat doorgaans al query

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
    gtype = geom.geom_type

    # ✅ sanity check: RD-coördinaten liggen grofweg binnen deze range
    minx, miny, maxx, maxy = geom.bounds
    if not (0 <= minx <= 300000 and 0 <= maxx <= 300000 and 300000 <= miny <= 650000 and 300000 <= maxy <= 650000):
        return
    
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
        # Holes laten we default weg (kan later aan)
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

    # ✅ Alleen bestandsnaam, AutoCAD zoekt dan in dezelfde map als de DXF
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
    """
    Maak 2 scripts: BGT layers aan/uit.
    """
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
    img = wms_get_image(WMS_LUCHTFOTO, {**wms_base_params(req), "layers": "Actueel_orthoHR", "styles": ""}, session=session)
    img.save(out_path)


def download_plu_enkel(bbox: BBox, out_path: Path, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(WMS_PLU, {**wms_base_params(req), "layers": "enkelbestemming", "styles": "enkelbestemming"}, session=session)


def download_plu_dubbel(bbox: BBox, out_path: Path, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(WMS_PLU, {**wms_base_params(req), "layers": "dubbelbestemming", "styles": "dubbelbestemming"}, session=session)


def download_kadastrale_kaart(bbox: BBox, *, px: int = 2000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(WMS_KAD, {**wms_base_params(req), "layers": "kadastralekaart", "styles": ""}, session=session)

def getmap(layer, bbox, px=1024, styles="", session: Optional[requests.Session] = None):
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": "1.3.0",
        "CRS": "EPSG:28992",
        "BBOX": _bbox_str(bbox),   # <-- belangrijk
        "WIDTH": str(px),
        "HEIGHT": str(px),
        "LAYERS": layer,
        "STYLES": styles,
        "FORMAT": "image/png",
        "TRANSPARENT": "FALSE",
    }
    s = session or requests
    r = s.get(WMS_TOPO, params=params, timeout=60)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype:
        raise RuntimeError(f"Geen image terug. Content-Type={ctype}\n{r.text[:800]}")

    img = Image.open(BytesIO(r.content)).convert("RGB")
    return img, r.url


def download_topo_image(bbox: BBox, *, px: int = 4000, session: Optional[requests.Session] = None) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    img = wms_get_image(
        WMS_TOPO,
        {**wms_base_params(req), "layers": "top25raster", "styles": ""},
        session=session,
    )
    return img.convert("RGBA")


def download_gmk_with_dominant_legend(bbox: BBox, out_path: Path, *, px: int = 2000, top_k: int = 6,
                                     session: Optional[requests.Session] = None) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    base = wms_get_image(WMS_GMK, {**wms_base_params(req), "layers": "geomorphological_area", "styles": ""}, session=session)

    dominant = extract_dominant_colors(base, n=10, sample=6)
    rows: List[Dict[str, Any]] = []
    for (rgb, frac) in dominant[:top_k]:
        pt = find_representative_pixel(base, rgb, tol=12)
        label = None
        if pt is not None:
            x, y = pt
            label = gmk_label_at_pixel(bbox, px, px, x, y, session=session)
        # optie: code weghalen
        if label and "—" in label:
            label = label.split("—", 1)[1].strip()
        rows.append({"rgb": rgb, "pct": frac * 100.0, "label": label})

    legend = build_color_meaning_legend(rows, title="Geomorfologie (dominante klassen)")
    out = place_legend_on_image(base, legend, position="bottom-right", legend_scale=1.0, legend_max_width_ratio=0.55, add_white_box=False)
    out.save(out_path)


def download_ahn(bbox: BBox, out_path: Path, *, px: int = 2000, product: str = "dtm",
                 add_legend: bool = True, session: Optional[requests.Session] = None) -> None:
    product = product.lower().strip()
    layer_map = {"dtm": "dtm_05m", "dsm": "dsm_05m"}
    if product not in layer_map:
        raise ValueError(f"Onbekend product {product!r}, kies uit {list(layer_map)}")

    layer = layer_map[product]
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(WMS_AHN, {**wms_base_params(req), "layers": layer, "styles": "default"}, session=session)

    if add_legend:
        try:
            # PDOK legend endpoint voor AHN kan verschillen; dit werkt vaak:
            legend = wms_getlegendgraphic(WMS_AHN, layer, style="default", session=session)
            img = place_legend_on_image(img, legend, position="bottom-right")
        except Exception as e:
            _log(f"[WARN] AHN legenda ophalen mislukt: {e}")

    img.save(out_path)


def download_bodem_layer(bbox: BBox, out_path: Path, *, layer: str, px: int = 2000,
                         legend_via: str = "getlegendgraphic",
                         session: Optional[requests.Session] = None) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(WMS_BODEM, {**wms_base_params(req), "layers": layer, "styles": ""}, session=session)

    try:
        if legend_via == "capabilities":
            legend = wms_legend_from_capabilities(WMS_BODEM, layer, session=session)
        else:
            legend = wms_getlegendgraphic(WMS_BODEM, layer, style="", session=session)
        img = place_legend_on_image(img, legend, position="bottom-right")
    except Exception as e:
        _log(f"[WARN] Bodem legenda ophalen mislukt ({layer}): {e}")

    img.save(out_path)


def wdm_legend_image(layer: str, session: Optional[requests.Session] = None) -> Image.Image:
    return wms_legend_from_capabilities(WMS_WDM, layer, session=session)


def download_wdm(bbox: BBox, out_path: Path, *, layer: str, px: int = 2000, add_legend: bool = True,
                 session: Optional[requests.Session] = None) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(WMS_WDM, {**wms_base_params(req), "layers": layer, "styles": ""}, session=session)

    if add_legend:
        try:
            legend = wdm_legend_image(layer, session=session)
            img = place_legend_on_image(img, legend, position="bottom-right")
        except Exception as e:
            _log(f"[WARN] WDM legenda ophalen mislukt ({layer}): {e}")

    img.save(out_path)


# =========================
# PLU composite helpers
# =========================

def get_plu_legend_image(session: Optional[requests.Session] = None) -> Image.Image:
    """
    In jouw notebook gebruikte je een vaste URL; dat kan prima.
    """
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
    """
    Maakt:
    - Bestemming_percelen.png: enkelbestemming + kadastralekaart + legenda
    - Bestemming_dubbel.png: dubbelbestemming + legenda, maar we saven CAD-robust via palette transparency
    """
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

    bestemmingdubbel = place_legend_on_image(
        base=dubbel,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.0,
        legend_max_width_ratio=0.2,
    )

    # AutoCAD-friendly save (palette transparency)
    save_png_palette_transparency(bestemmingdubbel, out_bestemming_dubbel)


# =========================
# BGT export
# =========================

def bgt_list_collections(session: Optional[requests.Session] = None) -> List[str]:
    s = session or requests.Session()
    url = f"{BGT_OGC.rstrip('/')}/collections"
    r = s.get(url, params={"f": "json"}, timeout=30)
    r.raise_for_status()
    cols = r.json().get("collections", []) or []
    return [c["id"] for c in cols if "id" in c]


def add_all_bgt_to_dxf(doc: ezdxf.EzDxf, msp: ezdxf.layouts.Modelspace, bbox: BBox,
                       limit_per_collection: int = 2000,
                       session: Optional[requests.Session] = None) -> None:
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
    response_crs=RD_CRS_URI,   # ✅ forceer RD output
    limit=limit_per_collection,
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
    # Add raster image refs
    for rp in rasters:
        ensure_layer_onoff(doc, rp.dxf_layer, default_on=rp.default_on, color=7)
        add_georef_image_to_doc(doc, raster_dir / rp.filename, rp.bbox, layer=rp.dxf_layer)

    # Vector overlays

    if include_bgt:
        add_all_bgt_to_dxf(doc, msp, bbox, limit_per_collection=bgt_limit_per_collection, session=session)

    doc.saveas(out_dxf)

    # Scripts for toggling BGT layers
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
    """
    Download/schrijf alle raster outputs en return:
    - list ExportPlan entries (voor DXF)
    - bbox_topo (hier gelijk aan bbox, want we croppen)
    """
    _ensure_dir(out_dir)

    # Filenames
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
    fn_bodem_belang = "bodemkundig_belang.png"

    # 1) Luchtfoto
    _log("[DL] Luchtfoto")
    download_luchtfoto(bbox, out_dir / fn_luchtfoto, px=px, session=session)

    # 2) Kadastrale overlay (WMS) + compositing luchtfoto_kadaster
    _log("[DL] Kadastrale kaart (WMS)")
    kad = download_kadastrale_kaart(bbox, px=px, session=session)
    lucht = Image.open(out_dir / fn_luchtfoto).convert("RGBA")
    lucht_plus = Image.alpha_composite(lucht, kad)
    lucht_plus.save(out_dir / fn_luchtfoto_kad)

    # 3) PLU outputs
    _log("[DL] PLU (bestemmingsplan)")
    build_plu_outputs(bbox, out_dir / fn_best_enkel, out_dir / fn_best_dubbel, px=px, session=session)

     # 4) Topo: download groot (vaste radius 3000m), crop terug naar bbox
    _log("[DL] TOPraster (download radius 3000m, crop naar project-bbox)")

    cx, cy = bbox_center(bbox)
    bbox_topo_big = bbox_around_point(cx, cy, 3000.0)

    topo_big = download_topo_image(bbox_topo_big, px=topo_px, session=session)
    topo_cropped = crop_image_to_bbox(topo_big, bbox_render=bbox_topo_big, bbox_target=bbox)

    # transparantie uit (zoals je eerdere topo), en save
    topo_cropped.convert("RGB").save(out_dir / fn_topo)

    bbox_topo_for_dxf = bbox  # belangrijk: DXF plaatst topo nu op dezelfde extent als de rest

    # 5) GMK (met dominante klassen legenda)
    _log("[DL] GMK")
    download_gmk_with_dominant_legend(bbox, out_dir / fn_gmk, px=px, session=session)

    # 6) AHN
    _log("[DL] AHN DSM/DTM")
    download_ahn(bbox, out_dir / fn_ahn_dsm, px=px, product="dsm", add_legend=True, session=session)
    download_ahn(bbox, out_dir / fn_ahn_dtm, px=px, product="dtm", add_legend=True, session=session)

    # 7) WDM (GHG/GLG/GT)
    _log("[DL] WDM (grondwater)")
    download_wdm(bbox, out_dir / fn_wdm_ghg, layer="bro-grondwaterspiegeldieptemetingen-GHG", px=px, session=session)
    download_wdm(bbox, out_dir / fn_wdm_glg, layer="bro-grondwaterspiegeldieptemetingen-GLG", px=px, session=session)
    download_wdm(bbox, out_dir / fn_wdm_gt, layer="bro-grondwaterspiegeldieptemetingen-GT", px=px, session=session)

    # 8) Bodem
    _log("[DL] Bodem")
    download_bodem_layer(bbox, out_dir / fn_bodemvlakken, layer="soilarea", px=px, session=session)
    download_bodem_layer(bbox, out_dir / fn_bodem_belang, layer="areaofpedologicalinterest", px=px, session=session)

    # DXF layer plan (naamgeving behouden uit notebook)
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
        ExportPlan(fn_bodem_belang, bbox, "$$_00-00-00_onderlegger_BodemkundigBelang", default_on=False),
    ]

    return rasters, bbox_topo_for_dxf


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genereer PDOK CAD onderlegger (DXF) met raster + vector overlays.")
    p.add_argument("--adres", type=str, default="", help="Adres voor centrum (bv. 'Rust en Vreugdlaan 2, 2243 AS Wassenaar').")
    p.add_argument("--x", type=float, default=None, help="RD X (als je geen adres wilt).")
    p.add_argument("--y", type=float, default=None, help="RD Y (als je geen adres wilt).")
    p.add_argument("--radius", type=float, default=250.0, help="Radius in meters rond centrum voor bbox (default 250).")
    p.add_argument("--outdir", type=str, default="output_onderlegger", help="Output map voor PNGs + DXF.")
    p.add_argument("--dxf", type=str, default="onderlegger.dxf", help="DXF bestandsnaam (in outdir).")

    p.add_argument("--px", type=int, default=2000, help="Raster resolutie (breedte=hoogte) voor meeste lagen.")
    p.add_argument("--topo-px", type=int, default=4000, help="Raster resolutie voor topo download.")
    p.add_argument("--topo-min-span", type=float, default=3000.0, help="Minimum bbox-span voor topo (meters).")

    p.add_argument("--no-bgt", action="store_true", help="Voeg geen BGT vectorlagen toe.")
    p.add_argument("--no-percelen", action="store_true", help="Voeg geen Kadaster percelen vectorlaag toe.")
    p.add_argument("--bgt-limit", type=int, default=2000, help="Max features per BGT collection (veiligheid).")

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

    _log("[DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
