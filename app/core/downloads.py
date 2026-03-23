# -*- coding: utf-8 -*-
"""Download functions for all WMS/raster layers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image

from app.core.bodem import bodem_label_at_pixel
from app.core.constants import (
    WMS_AHN,
    WMS_BODEM,
    WMS_GMK,
    WMS_KAD,
    WMS_LUCHTFOTO,
    WMS_PLU,
    WMS_TOPO,
    WMS_WDM,
)
from app.core.legend import (
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
    wms_base_params,
    wms_get_image,
    wms_getlegendgraphic,
    wms_legend_from_capabilities,
)

logger = get_logger(__name__)


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
        raise ValueError(
            f"Onbekend product {product!r}, kies uit {list(layer_map)}"
        )

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
        legend_scale=2.0,
        legend_max_width_ratio=0.2,
    )
    bestemming_kadaster.save(out_bestemming_percelen)

    enkel_plus_dubbel = Image.alpha_composite(enkel, dubbel)
    bestemmingdubbel = place_legend_on_image(
        base=enkel_plus_dubbel,
        legend=legend_img,
        position="bottom-right",
        legend_scale=2.0,
        legend_max_width_ratio=0.2,
    )
    bestemmingdubbel.save(out_bestemming_dubbel)
