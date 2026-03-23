# -*- coding: utf-8 -*-
"""BGT + Kadaster percelen vector export to DXF."""

from __future__ import annotations

from typing import List, Optional

import requests
from shapely.geometry import shape

from app.core.constants import BGT_OGC, KADAS_OGC, RD_CRS_URI
from app.core.dxf import (
    add_any_geom_to_dxf,
    ensure_layer,
    ensure_layer_onoff,
    safe_layer_name,
)
from app.core.log_config import get_logger
from app.core.ogc import ogc_get_all_features
from app.core.types import BBox

logger = get_logger(__name__)


def bgt_list_collections(
    session: Optional[requests.Session] = None,
) -> List[str]:
    s = session or requests.Session()
    url = f"{BGT_OGC.rstrip('/')}/collections"

    try:
        r = s.get(url, params={"f": "json"}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_BGT] List collections failed: %s", e)
        raise

    cols = r.json().get("collections", []) or []
    return [c["id"] for c in cols if "id" in c]


def add_tree_symbol(msp, x: float, y: float, layer: str, size: float = 1.5) -> None:
    msp.add_circle((x, y), radius=size, dxfattribs={"layer": layer})

    r_small = size * 0.55
    offset = size * 0.45

    msp.add_circle((x - offset, y), radius=r_small, dxfattribs={"layer": layer})
    msp.add_circle((x + offset, y), radius=r_small, dxfattribs={"layer": layer})
    msp.add_circle((x, y + offset), radius=r_small, dxfattribs={"layer": layer})


def add_all_bgt_to_dxf(
    doc,
    msp,
    bbox: BBox,
    limit_per_collection: int = 2000,
    session: Optional[requests.Session] = None,
) -> None:
    col_ids = bgt_list_collections(session=session)
    logger.info("[BGT] collections: %d", len(col_ids))

    colors = [1, 2, 3, 4, 5, 6, 7]

    for idx, cid in enumerate(col_ids):
        layer = safe_layer_name(cid, prefix="BGT-")
        ensure_layer_onoff(
            doc,
            layer,
            default_on=False,
            color=colors[idx % len(colors)],
        )

        feats = ogc_get_all_features(
            BGT_OGC,
            cid,
            bbox,
            bbox_crs=RD_CRS_URI,
            response_crs=RD_CRS_URI,
            limit=limit_per_collection,
            session=session,
        )

        seen_geom_types = set()
        logger.info("[BGT] %s: %d features", cid, len(feats))

        for f in feats:
            g = f.get("geometry")
            if not g:
                continue

            geom = shape(g)
            seen_geom_types.add(geom.geom_type)

            if cid == "vegetatieobject_punt" and geom.geom_type == "Point":
                add_tree_symbol(msp, geom.x, geom.y, layer=layer, size=1.5)
            elif cid == "vegetatieobject_punt" and geom.geom_type == "MultiPoint":
                for pt in geom.geoms:
                    add_tree_symbol(msp, pt.x, pt.y, layer=layer, size=1.5)
            else:
                add_any_geom_to_dxf(msp, geom, layer=layer)

        logger.info("[BGT] %s: geom types = %s", cid, sorted(seen_geom_types))


def add_kadaster_percelen_to_dxf(
    doc,
    msp,
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
