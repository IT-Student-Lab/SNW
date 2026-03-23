# -*- coding: utf-8 -*-
"""OGC API Features client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from app.core.log_config import get_logger
from app.core.types import BBox
from app.core.utils import bbox_str

logger = get_logger(__name__)


def ogc_get_all_features(
    base_url: str,
    collection: str,
    bbox: BBox,
    bbox_crs: str = "http://www.opengis.net/def/crs/EPSG/0/28992",
    limit: int = 1000,
    timeout: int = 30,
    response_crs: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    s = session or requests.Session()
    url = f"{base_url.rstrip('/')}/collections/{collection}/items"

    params: Optional[Dict[str, str]] = {
        "bbox": bbox_str(bbox),
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
        except requests.HTTPError:
            if params and "crs" in params:
                params = dict(params)
                params.pop("crs", None)
                try:
                    r = s.get(url, params=params, timeout=timeout)
                    r.raise_for_status()
                except requests.RequestException as e2:
                    logger.error("[ERROR_API_OGC] %s/%s failed: %s", base_url, collection, e2)
                    raise
            else:
                logger.error("[ERROR_API_OGC] %s/%s failed", base_url, collection)
                raise
        except requests.RequestException as e:
            logger.error("[ERROR_API_OGC] %s/%s failed: %s", base_url, collection, e)
            raise

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
