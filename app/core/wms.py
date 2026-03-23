# -*- coding: utf-8 -*-
"""WMS client helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Any, Dict, Optional

import requests
from PIL import Image

from app.core.log_config import get_logger
from app.core.types import MapRequest
from app.core.utils import bbox_str

logger = get_logger(__name__)


def wms_base_params(req: MapRequest) -> Dict[str, str]:
    return {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": req.version,
        "CRS": req.crs,
        "BBOX": bbox_str(req.bbox),
        "WIDTH": str(req.width),
        "HEIGHT": str(req.height),
        "FORMAT": req.fmt,
        "TRANSPARENT": "TRUE" if req.transparent else "FALSE",
    }


def wms_get_image(
    wms_url: str,
    params: Dict[str, str],
    session: Optional[requests.Session] = None,
) -> Image.Image:
    s = session or requests.Session()

    try:
        r = s.get(wms_url, params=params, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_WMS] WMS GetMap failed for %s: %s", wms_url, e)
        raise

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

    try:
        r = s.get(wms_url, params=params, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_WMS] GetLegendGraphic failed for %s layer=%s: %s", wms_url, layer, e)
        raise

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


def wms_legend_from_capabilities(
    wms_url: str,
    layer: str,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    s = session or requests.Session()

    try:
        cap = s.get(wms_url, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"}, timeout=60)
        cap.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_WMS] GetCapabilities failed for %s: %s", wms_url, e)
        raise

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

        try:
            r = s.get(href, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error("[ERROR_API_WMS] Legend download failed from %s: %s", href, e)
            raise

        return Image.open(BytesIO(r.content)).convert("RGBA")

    raise ValueError(f"Layer {layer!r} niet gevonden in GetCapabilities")
