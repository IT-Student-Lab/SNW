# -*- coding: utf-8 -*-
"""Preview router — quick bbox preview image."""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw

from api.deps import get_current_user
from api.models import PreviewRequest
from app.core import address_to_rd_full, bbox_around_point, preview_image

router = APIRouter(prefix="/api", tags=["preview"])


@router.post("/preview")
async def preview(
    req: PreviewRequest,
    _user: str = Depends(get_current_user),
):
    """Generate a quick topo preview image and return it as PNG."""
    session = http_requests.Session()

    try:
        if req.mode == "address":
            if not req.address or len(req.address.strip()) < 3:
                raise HTTPException(400, "Vul een geldig adres in.")
            loc_info = address_to_rd_full(req.address.strip(), session=session)
            cx, cy = loc_info["x"], loc_info["y"]
        else:
            if req.x is None or req.y is None:
                raise HTTPException(400, "x en y coördinaten zijn verplicht.")
            cx, cy = req.x, req.y

        # Use a wider bbox so top25raster actually renders at small radii
        preview_radius = max(req.radius, 3000.0)
        bbox = bbox_around_point(cx, cy, preview_radius)

        tmp = tempfile.mkdtemp(prefix="pdok_preview_")
        out = Path(tmp) / "preview.png"
        px = 800
        preview_image(bbox, out, px=px, session=session)

        if not out.exists():
            raise HTTPException(500, "Preview kon niet worden gegenereerd.")

        # Draw a red rectangle showing the actual selected area
        if preview_radius > req.radius:
            img = Image.open(out).convert("RGBA")
            draw = ImageDraw.Draw(img)
            frac = req.radius / preview_radius
            half = (px * frac) / 2
            cx_px, cy_px = px / 2, px / 2
            rect = (
                cx_px - half, cy_px - half,
                cx_px + half, cy_px + half,
            )
            draw.rectangle(rect, outline="red", width=3)
            img.save(out)

        return FileResponse(out, media_type="image/png")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except http_requests.RequestException as e:
        raise HTTPException(502, f"Externe API-fout: {e}")
    except Exception as e:
        raise HTTPException(500, f"Preview mislukt: {e}")
