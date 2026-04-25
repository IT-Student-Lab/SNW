# -*- coding: utf-8 -*-
"""Download functions for all WMS/raster layers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from app.core.bodem import bodem_label_at_pixel
from app.core.constants import (
    KADAS_OGC,
    TOPOTIJDREIS_BASE,
    WCS_AHN,
    WMS_AHN,
    WMS_BODEM,
    WMS_GMK,
    WMS_KAD,
    WMS_LUCHTFOTO,
    WMS_NATURA2000,
    WMS_PLU,
    WMS_TOPO,
    WMS_WDM,
)
from app.core.legend import (
    build_ahn_dynamic_legend,
    build_pretty_legend,
    extract_dominant_colors,
    extract_rows_from_vertical_legend,
    find_representative_pixel,
)
from app.core.log_config import get_logger
from app.core.raster import place_legend_on_image
from app.core.types import BBox, MapRequest
from app.core.utils import bbox_str
from app.core.wms import (
    build_ahn_sld,
    wcs_get_elevation_stats,
    wms_base_params,
    wms_get_image,
    wms_getlegendgraphic,
    wms_legend_from_capabilities,
)

logger = get_logger(__name__)


# --------------- Plangebied overlay helper ---------------

def _draw_plangebied_rect(
    img: Image.Image,
    bbox_img: BBox,
    bbox_plan: BBox,
    color: str = "red",
    width: int = 5,
    label: str = "Plangebied",
) -> Image.Image:
    """Draw a rectangle on img showing where bbox_plan falls within bbox_img.

    Returns the modified image with a red rectangle and label.
    """
    draw = ImageDraw.Draw(img)
    W, H = img.size
    xmin_i, ymin_i, xmax_i, ymax_i = bbox_img
    xmin_p, ymin_p, xmax_p, ymax_p = bbox_plan

    # Convert plan bbox coords to pixel coords
    span_x = xmax_i - xmin_i
    span_y = ymax_i - ymin_i
    if span_x <= 0 or span_y <= 0:
        return img

    px_left = (xmin_p - xmin_i) / span_x * W
    px_right = (xmax_p - xmin_i) / span_x * W
    px_top = (ymax_i - ymax_p) / span_y * H
    px_bottom = (ymax_i - ymin_p) / span_y * H

    # Draw rectangle
    draw.rectangle(
        [px_left, px_top, px_right, px_bottom],
        outline=color, width=width,
    )

    # Add label above the rectangle
    try:
        font = ImageFont.truetype("arial.ttf", max(16, int(H * 0.018)))
    except OSError:
        font = ImageFont.load_default(size=max(16, int(H * 0.018)))

    label_bbox = draw.textbbox((0, 0), label, font=font)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]
    label_x = px_left + (px_right - px_left - label_w) / 2
    label_y = px_top - label_h - 6
    if label_y < 2:
        label_y = px_bottom + 4

    # Draw text background
    draw.rectangle(
        [label_x - 3, label_y - 2, label_x + label_w + 3, label_y + label_h + 2],
        fill="white",
    )
    draw.text((label_x, label_y), label, fill=color, font=font)

    return img


def _draw_address_dot(
    img: Image.Image,
    bbox_img: BBox,
    center: tuple[float, float],
    radius_px: int = 12,
    color: str = "red",
    outline_color: str = "white",
) -> Image.Image:
    """Draw a small filled dot with white outline at the address point."""
    draw = ImageDraw.Draw(img)
    W, H = img.size
    xmin, ymin, xmax, ymax = bbox_img
    span_x = xmax - xmin
    span_y = ymax - ymin
    if span_x <= 0 or span_y <= 0:
        return img

    cx, cy = center
    px_x = (cx - xmin) / span_x * W
    px_y = (ymax - cy) / span_y * H

    # White outline circle
    draw.ellipse(
        [px_x - radius_px - 2, px_y - radius_px - 2,
         px_x + radius_px + 2, px_y + radius_px + 2],
        fill=outline_color,
    )
    # Filled color dot
    draw.ellipse(
        [px_x - radius_px, px_y - radius_px,
         px_x + radius_px, px_y + radius_px],
        fill=color,
    )

    return img


# --------------- Kadaster BRK data ---------------

def fetch_kadaster_brk_data(
    bbox: BBox,
    center: Optional[tuple] = None,
    session: Optional[requests.Session] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch the most relevant cadastral parcel (closest to center) from BRK OGC API."""
    from app.core.ogc import ogc_get_all_features

    try:
        features = ogc_get_all_features(
            KADAS_OGC,
            "perceel",
            bbox,
            limit=200,
            session=session,
        )
    except Exception as e:
        logger.warning("Kadaster BRK ophalen mislukt: %s", e)
        return None

    if not features:
        return None

    # Pick the parcel closest to center
    def _centroid(feat: Dict) -> Optional[tuple]:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            return None
        # Flatten nested coordinate lists to get average
        flat = []
        def _flatten(c):
            if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
                flat.append((float(c[0]), float(c[1])))
            elif isinstance(c, (list, tuple)):
                for item in c:
                    _flatten(item)
        _flatten(coords)
        if not flat:
            return None
        avg_x = sum(p[0] for p in flat) / len(flat)
        avg_y = sum(p[1] for p in flat) / len(flat)
        return (avg_x, avg_y)

    if center is None:
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)

    best_feat = features[0]
    best_dist = float("inf")
    for feat in features:
        c = _centroid(feat)
        if c:
            d = (c[0] - center[0]) ** 2 + (c[1] - center[1]) ** 2
            if d < best_dist:
                best_dist = d
                best_feat = feat

    props = best_feat.get("properties") or {}
    return {
        "gemeente": props.get("kadastrale_gemeente_waarde", ""),
        "sectie": props.get("sectie", ""),
        "perceelnummer": props.get("perceelnummer", ""),
        "grootte_m2": props.get("kadastrale_grootte_waarde", ""),
        "soort_grootte": props.get("soort_grootte_waarde", ""),
        "begin_geldigheid": props.get("begin_geldigheid", ""),
    }


# --------------- GMK helpers ---------------

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
        "BBOX": bbox_str(bbox),
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

    try:
        r = s.get(WMS_GMK, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_GMK] GetFeatureInfo failed: %s", e)
        raise

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
    data = gmk_getfeatureinfo(
        bbox, width, height, x, y, layer=layer, session=session
    )
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


# --------------- PLU legend ---------------

def get_plu_legend_image(
    session: Optional[requests.Session] = None,
) -> Image.Image:
    s = session or requests.Session()
    legend_url = (
        "https://service.pdok.nl/kadaster/plu/wms/v1_0/"
        "legend/enkelbestemming/enkelbestemming.png"
    )

    try:
        r = s.get(legend_url, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_PLU] Legend download failed: %s", e)
        raise

    return Image.open(BytesIO(r.content)).convert("RGBA")


# --------------- Individual download functions ---------------

def download_luchtfoto(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    img = wms_get_image(
        WMS_LUCHTFOTO,
        {**wms_base_params(req), "LAYERS": "Actueel_orthoHR", "STYLES": ""},
        session=session,
    )
    img.save(out_path)


def download_plu_enkel(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(
        WMS_PLU,
        {
            **wms_base_params(req),
            "LAYERS": "enkelbestemming",
            "STYLES": "enkelbestemming",
        },
        session=session,
    )


def download_plu_dubbel(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(
        WMS_PLU,
        {
            **wms_base_params(req),
            "LAYERS": "dubbelbestemming",
            "STYLES": "dubbelbestemming",
        },
        session=session,
    )


def download_kadastrale_kaart(
    bbox: BBox,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)
    return wms_get_image(
        WMS_KAD,
        {**wms_base_params(req), "LAYERS": "kadastralekaart", "STYLES": ""},
        session=session,
    )


def download_topo_image(
    bbox: BBox,
    *,
    px: int = 4000,
    session: Optional[requests.Session] = None,
) -> Image.Image:
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
        {
            **wms_base_params(req),
            "LAYERS": "geomorphological_area",
            "STYLES": "",
        },
        session=session,
    )

    dominant = extract_dominant_colors(base, n=10, sample=6)
    rows: List[Dict[str, Any]] = []
    for rgb, frac in dominant[:top_k]:
        pt = find_representative_pixel(base, rgb, tol=12)
        label = None
        if pt is not None:
            x, y = pt
            label = gmk_label_at_pixel(
                bbox, px, px, x, y, session=session
            )
        if label and "—" in label:
            label = label.split("—", 1)[1].strip()
        rows.append({"rgb": rgb, "pct": frac * 100.0, "label": label})

    legend = build_pretty_legend(
        rows,
        title="Geomorfologie",
        subtitle="Dominante klassen binnen het geselecteerde gebied",
        width=1400,
        show_percent=True,
    )

    out = place_legend_on_image(
        base,
        legend,
        position="bottom-right",
        legend_scale=1.5,
        legend_max_width_ratio=0.58,
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
    dynamic: bool = True,
    session: Optional[requests.Session] = None,
) -> None:
    product = product.lower().strip()
    layer_map = {"dtm": "dtm_05m", "dsm": "dsm_05m"}
    if product not in layer_map:
        raise ValueError(
            f"Onbekend product {product!r}, kies uit {list(layer_map)}"
        )

    layer = layer_map[product]
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=True)

    # --- dynamic visualisation: colour ramp scaled to local elevation range ---
    vmin = vmax = None
    if dynamic:
        try:
            vmin, vmax = wcs_get_elevation_stats(
                WCS_AHN, layer, bbox, session=session
            )
            logger.info(
                "AHN %s dynamisch bereik: %.2f m – %.2f m", product.upper(), vmin, vmax
            )
            sld = build_ahn_sld(layer, vmin, vmax)
            img = wms_get_image(
                WMS_AHN,
                {
                    **wms_base_params(req),
                    "LAYERS": layer,
                    "STYLES": "",
                    "SLD_BODY": sld,
                },
                session=session,
            )
        except Exception as e:
            logger.warning(
                "Dynamische AHN visualisatie mislukt, terugval op default (%s): %s",
                layer, e,
            )
            dynamic = False  # fall through to default below

    if not dynamic:
        img = wms_get_image(
            WMS_AHN,
            {**wms_base_params(req), "LAYERS": layer, "STYLES": "default"},
            session=session,
        )

    if add_legend:
        try:
            if vmin is not None and vmax is not None:
                legend = build_ahn_dynamic_legend(
                    f"AHN {product.upper()} (dynamisch)",
                    vmin,
                    vmax,
                )
            else:
                try:
                    raw_legend = wms_getlegendgraphic(
                        WMS_AHN, layer, style="default", session=session
                    )
                except Exception:
                    raw_legend = wms_legend_from_capabilities(
                        WMS_AHN, layer, session=session
                    )
                legend = extract_rows_from_vertical_legend(
                    raw_legend,
                    title=f"AHN {product.upper()}",
                    max_width=1500,
                    max_height=1200,
                    scale=2.4,
                )
            img = place_legend_on_image(
                img,
                legend,
                position="bottom-right",
                legend_scale=1.2,
                legend_max_width_ratio=0.62,
                add_white_box=False,
            )
        except Exception as e:
            logger.warning("AHN legenda ophalen mislukt (%s): %s", layer, e)

    img.save(out_path)


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

    for rgb, frac in dominant:
        pt = find_representative_pixel(base, rgb, tol=12)
        label = None
        if pt is not None:
            x, y = pt
            label = bodem_label_at_pixel(
                bbox, px, px, x, y, layer="soilarea", session=session
            )

        if label and "—" in label:
            label = label.split("—", 1)[1].strip()

        label = label or "Onbekende bodemklasse"
        bucket = grouped.setdefault(
            label, {"rgb": rgb, "pct": 0.0, "label": label}
        )
        bucket["pct"] += frac * 100.0

    rows = sorted(
        grouped.values(), key=lambda r: float(r["pct"]), reverse=True
    )
    rows = [r for r in rows if float(r["pct"]) >= 0.5][:top_k]

    legend = build_pretty_legend(
        rows,
        title="Bodemvlakken",
        subtitle="Dominante klassen binnen het geselecteerde gebied",
        width=1400,
        show_percent=True,
    )

    out = place_legend_on_image(
        base,
        legend,
        position="bottom-right",
        legend_scale=1.5,
        legend_max_width_ratio=0.58,
        add_white_box=False,
    )
    out.save(out_path)


def wdm_legend_image(
    layer: str, session: Optional[requests.Session] = None
) -> Image.Image:
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
    img = wms_get_image(
        WMS_WDM,
        {**wms_base_params(req), "LAYERS": layer, "STYLES": ""},
        session=session,
    )

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
                max_width=1500,
                max_height=1200,
                scale=2.4,
            )
            img = place_legend_on_image(
                img,
                legend,
                position="bottom-right",
                legend_scale=1.2,
                legend_max_width_ratio=0.62,
                add_white_box=False,
            )
        except Exception as e:
            logger.warning("WDM legenda ophalen mislukt (%s): %s", layer, e)

    img.save(out_path)


# --------------- PLU composite ---------------

def build_plu_outputs(
    bbox: BBox,
    out_bestemming_percelen: Path,
    out_bestemming_dubbel: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    legend_img = get_plu_legend_image(session=session)

    enkel = download_plu_enkel(
        bbox, out_bestemming_percelen, px=px, session=session
    )
    dubbel = download_plu_dubbel(
        bbox, out_bestemming_dubbel, px=px, session=session
    )
    kad = download_kadastrale_kaart(bbox, px=px, session=session)

    plu_plus_percelen = Image.alpha_composite(enkel, kad)
    bestemming_kadaster = place_legend_on_image(
        base=plu_plus_percelen,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.8,
        legend_max_width_ratio=0.25,
    )
    bestemming_kadaster.save(out_bestemming_percelen)

    enkel_plus_dubbel = Image.alpha_composite(enkel, dubbel)
    bestemmingdubbel = place_legend_on_image(
        base=enkel_plus_dubbel,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.8,
        legend_max_width_ratio=0.25,
    )
    bestemmingdubbel.save(out_bestemming_dubbel)


# --------------- Natura 2000 ---------------

def download_natura2000(
    bbox: BBox,
    out_path: Path,
    *,
    center: Optional[tuple] = None,
    breed_radius: float = 10_000.0,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    """Download Natura 2000 map with topo background, zoomed out."""
    from app.core.locatie import bbox_around_point

    # Compute a wide bbox so nearby Natura 2000 areas are visible
    if center is None:
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    bbox_wide = bbox_around_point(center[0], center[1], breed_radius)

    req = MapRequest(bbox=bbox_wide, width=px, height=px, transparent=True)

    # Background: topo map (more readable when zoomed out)
    bg = wms_get_image(
        WMS_TOPO,
        {**wms_base_params(req), "LAYERS": "top25raster", "STYLES": ""},
        session=session,
    )
    bg = bg.convert("RGBA")

    # Natura2000 overlay
    overlay = wms_get_image(
        WMS_NATURA2000,
        {**wms_base_params(req), "LAYERS": "natura2000", "STYLES": ""},
        session=session,
    )
    overlay = overlay.convert("RGBA")

    result = Image.alpha_composite(bg, overlay)

    # Draw a visible plangebied rectangle and address dot
    _draw_plangebied_rect(result, bbox_wide, bbox, color="red", width=5, label="Plangebied")
    _draw_address_dot(result, bbox_wide, center, radius_px=10)

    result.save(out_path)


# --------------- Zoomed-out ligging ---------------

def download_ligging_breed(
    bbox: BBox,
    out_path_topo: Path,
    out_path_lucht: Path,
    *,
    center: tuple[float, float],
    breed_radius: float = 2000.0,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    """Download a wider-area topo + luchtfoto for the Ligging section."""
    from app.core.locatie import bbox_around_point

    bbox_breed = bbox_around_point(center[0], center[1], breed_radius)
    req = MapRequest(bbox=bbox_breed, width=px, height=px, transparent=False)

    topo = wms_get_image(
        WMS_TOPO,
        {**wms_base_params(req), "LAYERS": "top25raster", "STYLES": ""},
        session=session,
    )
    topo = topo.convert("RGBA")
    _draw_plangebied_rect(topo, bbox_breed, bbox, color="red", width=5, label="Plangebied")
    _draw_address_dot(topo, bbox_breed, center, radius_px=10)
    topo.save(out_path_topo)

    lucht = wms_get_image(
        WMS_LUCHTFOTO,
        {**wms_base_params(req), "LAYERS": "Actueel_orthoHR", "STYLES": ""},
        session=session,
    )
    lucht = lucht.convert("RGBA")
    _draw_plangebied_rect(lucht, bbox_breed, bbox, color="red", width=5, label="Plangebied")
    _draw_address_dot(lucht, bbox_breed, center, radius_px=10)
    lucht.save(out_path_lucht)


# --------------- Historic luchtfotos ---------------

def download_historic_luchtfotos(
    bbox: BBox,
    out_dir: Path,
    *,
    px: int = 2000,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """Download oldest and mid-range historical luchtfotos. Returns list of filenames."""
    historic_layers = [
        ("2016_ortho25", "luchtfoto_2016.png"),
        ("2020_ortho25", "luchtfoto_2020.png"),
    ]
    downloaded = []
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    for layer, fname in historic_layers:
        try:
            img = wms_get_image(
                WMS_LUCHTFOTO,
                {**wms_base_params(req), "LAYERS": layer, "STYLES": ""},
                session=session,
            )
            img.save(out_dir / fname)
            downloaded.append(fname)
        except Exception as e:
            logger.warning("Historische luchtfoto %s mislukt: %s", layer, e)
    return downloaded


# --------------- Topotijdreis (historical topo maps) ---------------

# Tiling scheme constants (shared across all Historische_tijdreis services)
_TOPO_TILE_ORIGIN_X = -30515500.0
_TOPO_TILE_ORIGIN_Y = 31112399.999999993
_TOPO_TILE_SIZE = 256
_TOPO_LODS = {
    0: 3251.206502413005,
    1: 1625.6032512065026,
    2: 812.8016256032513,
    3: 406.40081280162565,
    4: 203.20040640081282,
    5: 101.60020320040641,
    6: 50.800101600203206,
    7: 25.400050800101603,
    8: 12.700025400050801,
    9: 6.350012700025401,
    10: 3.1750063500127004,
    11: 1.5875031750063502,
}


def _fetch_topotijdreis_image(
    year: int,
    bbox: BBox,
    *,
    target_res: float = 1.6,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    """Fetch and stitch topotijdreis tiles for a given year and bbox."""
    import math

    s = session or requests.Session()
    ox, oy = _TOPO_TILE_ORIGIN_X, _TOPO_TILE_ORIGIN_Y
    ts = _TOPO_TILE_SIZE

    # Find best LOD
    best_level = min(_TOPO_LODS, key=lambda l: abs(_TOPO_LODS[l] - target_res))
    res = _TOPO_LODS[best_level]

    xmin, ymin, xmax, ymax = bbox

    # Compute tile range
    col_min = int(math.floor((xmin - ox) / (ts * res)))
    col_max = int(math.floor((xmax - ox) / (ts * res)))
    row_min = int(math.floor((oy - ymax) / (ts * res)))
    row_max = int(math.floor((oy - ymin) / (ts * res)))

    n_cols = col_max - col_min + 1
    n_rows = row_max - row_min + 1

    stitched = Image.new("RGB", (n_cols * ts, n_rows * ts), (255, 255, 255))

    for r_idx, row in enumerate(range(row_min, row_max + 1)):
        for c_idx, col in enumerate(range(col_min, col_max + 1)):
            tile_url = (
                f"{TOPOTIJDREIS_BASE}/Historische_tijdreis_{year}"
                f"/MapServer/tile/{best_level}/{row}/{col}"
            )
            try:
                resp = s.get(tile_url, timeout=15)
                if resp.status_code == 200 and len(resp.content) > 100:
                    tile_img = Image.open(BytesIO(resp.content)).convert("RGB")
                    stitched.paste(tile_img, (c_idx * ts, r_idx * ts))
            except Exception:
                pass  # blank tile stays white

    # Crop to exact bbox
    px_left = (xmin - (ox + col_min * ts * res)) / res
    px_top = ((oy - row_min * ts * res) - ymax) / res
    px_right = px_left + (xmax - xmin) / res
    px_bottom = px_top + (ymax - ymin) / res

    return stitched.crop((int(px_left), int(px_top), int(px_right), int(px_bottom)))


def download_topotijdreis(
    bbox: BBox,
    out_dir: Path,
    *,
    years: Optional[List[int]] = None,
    center: Optional[tuple] = None,
    breed_radius: float = 2000.0,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """Download historical topo maps from topotijdreis for given years.

    Uses a wider bbox (breed_radius around center) so the maps are
    zoomed out enough to show historical context of the surroundings.
    Returns list of saved filenames.
    """
    from app.core.locatie import bbox_around_point

    if years is None:
        years = [1900, 1950, 2000]

    # Compute a wider bbox for more context
    if center is None:
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    bbox_wide = bbox_around_point(center[0], center[1], breed_radius)

    # Use a coarser resolution (~6.4 m/px) so the wider area fits nicely
    target_res = 6.4

    downloaded = []
    for year in years:
        fname = f"topotijdreis_{year}.png"
        try:
            img = _fetch_topotijdreis_image(
                year, bbox_wide, target_res=target_res, session=session,
            )
            img.save(out_dir / fname)
            downloaded.append(fname)
            logger.info("Topotijdreis %d opgeslagen: %s", year, fname)
        except Exception as e:
            logger.warning("Topotijdreis %d mislukt: %s", year, e)
    return downloaded
