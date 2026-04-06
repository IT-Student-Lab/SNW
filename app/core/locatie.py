# -*- coding: utf-8 -*-
"""Address geocoding via PDOK Locatieserver."""

from __future__ import annotations

import re
from typing import Optional, Tuple

import requests

from app.core.constants import LOOKUP, SUGGEST
from app.core.log_config import get_logger
from app.core.types import BBox

logger = get_logger(__name__)


def address_to_rd(
    address: str, session: Optional[requests.Session] = None
) -> Tuple[float, float]:
    s = session or requests.Session()

    try:
        r = s.get(SUGGEST, params={"q": address}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_SUGGEST] Locatieserver suggest failed: %s", e)
        raise

    docs = r.json().get("response", {}).get("docs", [])
    if not docs:
        raise ValueError(f"Geen resultaat voor adres: {address!r}")
    loc_id = docs[0]["id"]

    try:
        r = s.get(LOOKUP, params={"id": loc_id}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_LOOKUP] Locatieserver lookup failed: %s", e)
        raise

    doc = r.json()["response"]["docs"][0]

    rd = doc["centroide_rd"]
    cleaned = re.sub(r"[^0-9. ]", "", rd).strip()
    x, y = map(float, cleaned.split())
    logger.info("Address '%s' resolved to RD (%.3f, %.3f)", address, x, y)
    return x, y


def address_to_rd_full(
    address: str, session: Optional[requests.Session] = None
) -> dict:
    """Resolve an address and return all location metadata."""
    s = session or requests.Session()

    try:
        r = s.get(SUGGEST, params={"q": address}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_SUGGEST] Locatieserver suggest failed: %s", e)
        raise

    docs = r.json().get("response", {}).get("docs", [])
    if not docs:
        raise ValueError(f"Geen resultaat voor adres: {address!r}")
    loc_id = docs[0]["id"]

    try:
        r = s.get(LOOKUP, params={"id": loc_id}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_LOOKUP] Locatieserver lookup failed: %s", e)
        raise

    doc = r.json()["response"]["docs"][0]

    rd = doc.get("centroide_rd", "")
    cleaned = re.sub(r"[^0-9. ]", "", rd).strip()
    x, y = map(float, cleaned.split())

    return {
        "x": x,
        "y": y,
        "weergavenaam": doc.get("weergavenaam", address),
        "gemeentenaam": doc.get("gemeentenaam", ""),
        "provincienaam": doc.get("provincienaam", ""),
        "waterschapsnaam": doc.get("waterschapsnaam", ""),
        "woonplaatsnaam": doc.get("woonplaatsnaam", ""),
        "buurtnaam": doc.get("buurtnaam", ""),
        "wijknaam": doc.get("wijknaam", ""),
        "postcode": doc.get("postcode", ""),
        "straatnaam": doc.get("straatnaam", ""),
        "huisnummer": str(doc.get("huis_nlt", doc.get("huisnummer", ""))),
    }


def bbox_around_point(x: float, y: float, radius_m: float) -> BBox:
    return (x - radius_m, y - radius_m, x + radius_m, y + radius_m)
