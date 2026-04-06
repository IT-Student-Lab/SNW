# -*- coding: utf-8 -*-
"""WMS/WCS client helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import numpy as np
import requests
from PIL import Image

from app.core.log_config import get_logger
from app.core.types import BBox, MapRequest
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


# --------------- WCS helpers for AHN dynamic visualisation ---------------

def wcs_get_elevation_stats(
    wcs_url: str,
    coverage_id: str,
    bbox: BBox,
    *,
    sample_size: int = 200,
    session: Optional[requests.Session] = None,
) -> Tuple[float, float]:
    """Fetch a small elevation sample via WCS and return (min, max).

    Uses 2nd / 98th percentiles to ignore nodata outliers.
    """
    s = session or requests.Session()
    minx, miny, maxx, maxy = bbox

    params = [
        ("SERVICE", "WCS"),
        ("REQUEST", "GetCoverage"),
        ("VERSION", "2.0.1"),
        ("CoverageId", coverage_id),
        ("FORMAT", "image/tiff"),
        ("SUBSET", f"x({minx},{maxx})"),
        ("SUBSET", f"y({miny},{maxy})"),
        ("SCALESIZE", f"x({sample_size}),y({sample_size})"),
    ]

    try:
        r = s.get(wcs_url, params=params, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_WCS] GetCoverage failed for %s: %s", wcs_url, e)
        raise

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "tiff" not in ctype and "image" not in ctype:
        raise ValueError(
            f"WCS response is not a TIFF. Content-Type: {ctype}\n"
            f"First chars: {r.text[:400]}"
        )

    arr = np.array(Image.open(BytesIO(r.content)), dtype=np.float32)
    valid = arr[(arr > -1000) & (arr < 1000)]
    if valid.size == 0:
        raise ValueError("Geen geldige hoogtepixels gevonden in WCS response")

    lo = float(np.percentile(valid, 2))
    hi = float(np.percentile(valid, 98))
    return lo, hi


# --------------- SLD generation for dynamic AHN colour ramp ---------------

_AHN_COLOUR_RAMP = [
    # (fraction 0..1, hex colour)
    (0.00, "#1a3399"),
    (0.10, "#2c7bb6"),
    (0.22, "#abd9e9"),
    (0.35, "#66c2a5"),
    (0.48, "#d9ef8b"),
    (0.58, "#ffffbf"),
    (0.70, "#fee08b"),
    (0.80, "#fdae61"),
    (0.90, "#f46d43"),
    (1.00, "#d73027"),
]


def build_ahn_sld(
    layer: str, vmin: float, vmax: float
) -> str:
    """Build an SLD document with a colour ramp mapped to [vmin, vmax]."""
    span = vmax - vmin
    if span < 0.01:
        span = 1.0

    entries = []
    for frac, colour in _AHN_COLOUR_RAMP:
        q = round(vmin + frac * span, 3)
        entries.append(
            f'              <ColorMapEntry color="{colour}" quantity="{q}" '
            f'label="{q:.1f} m"/>'
        )
    entries_xml = "\n".join(entries)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<StyledLayerDescriptor version="1.0.0"\n'
        '  xmlns="http://www.opengis.net/sld"\n'
        '  xmlns:ogc="http://www.opengis.net/ogc"\n'
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "  <NamedLayer>\n"
        f"    <Name>{layer}</Name>\n"
        "    <UserStyle>\n"
        "      <Name>dynamic</Name>\n"
        "      <FeatureTypeStyle>\n"
        "        <Rule>\n"
        "          <RasterSymbolizer>\n"
        "            <ColorMap>\n"
        f"{entries_xml}\n"
        "            </ColorMap>\n"
        "          </RasterSymbolizer>\n"
        "        </Rule>\n"
        "      </FeatureTypeStyle>\n"
        "    </UserStyle>\n"
        "  </NamedLayer>\n"
        "</StyledLayerDescriptor>"
    )
