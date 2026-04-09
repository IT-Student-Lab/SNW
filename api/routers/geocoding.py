# -*- coding: utf-8 -*-
"""Geocoding router — PDOK Locatieserver suggest / lookup."""

from __future__ import annotations

import requests as http_requests
from fastapi import APIRouter, Depends, Query

from api.deps import get_current_user
from api.models import (
    GeocodeLookupResponse,
    GeocodeSuggestion,
    GeocodeSuggestResponse,
)

router = APIRouter(prefix="/api/geocode", tags=["geocoding"])

_SUGGEST_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
_LOOKUP_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/lookup"


@router.get("/suggest", response_model=GeocodeSuggestResponse)
async def suggest(
    q: str = Query(..., min_length=2, max_length=300),
    _user: str = Depends(get_current_user),
):
    """Return address suggestions from PDOK Locatieserver."""
    resp = http_requests.get(
        _SUGGEST_URL,
        params={"q": q, "rows": 7},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    suggestions = []
    for doc in data.get("response", {}).get("docs", []):
        suggestions.append(
            GeocodeSuggestion(
                id=doc.get("id", ""),
                display_name=doc.get("weergavenaam", ""),
                type=doc.get("type", ""),
                score=doc.get("score"),
            )
        )
    return GeocodeSuggestResponse(suggestions=suggestions)


@router.get("/lookup", response_model=GeocodeLookupResponse)
async def lookup(
    id: str = Query(..., min_length=1),
    _user: str = Depends(get_current_user),
):
    """Lookup full details for a PDOK suggestion id."""
    import re
    from app.core.constants import LOOKUP

    resp = http_requests.get(LOOKUP, params={"id": id}, timeout=10)
    resp.raise_for_status()
    docs = resp.json().get("response", {}).get("docs", [])
    if not docs:
        from fastapi import HTTPException
        raise HTTPException(404, "ID niet gevonden")
    doc = docs[0]

    rd = doc.get("centroide_rd", "")
    cleaned = re.sub(r"[^0-9. ]", "", rd).strip()
    x, y = map(float, cleaned.split())

    return GeocodeLookupResponse(
        display_name=doc.get("weergavenaam", id),
        x=x,
        y=y,
        gemeente=doc.get("gemeentenaam"),
        provincie=doc.get("provincienaam"),
        woonplaats=doc.get("woonplaatsnaam"),
        waterschap=doc.get("waterschapsnaam"),
        buurt=doc.get("buurtnaam"),
    )
