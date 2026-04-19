# -*- coding: utf-8 -*-
"""Preview router — quick bbox preview image."""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.deps import get_current_user
from api.models import PreviewRequest
from app.core import address_to_rd_full, bbox_around_point, preview_image, preview_luchtfoto
from app.core.raster import crop_image_to_bbox

router = APIRouter(prefix="/api", tags=["preview"])


@router.post("/preview")
async def preview(
    req: PreviewRequest,
    layer: str = Query("luchtfoto", regex="^(topo|luchtfoto)$"),
    _user: str = Depends(get_current_user),
):
    """Generate a quick preview image and return it as PNG.

    ?layer=topo      — topokaart (top25raster)
    ?layer=luchtfoto — luchtfoto (default)
    """
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

        bbox = bbox_around_point(cx, cy, req.radius)
        tmp = tempfile.mkdtemp(prefix="pdok_preview_")
        out = Path(tmp) / "preview.png"
        px = 800

        if layer == "topo":
            # top25raster has scale limits — fetch wider, then crop
            preview_radius = max(req.radius, 1500.0)
            bbox_wide = bbox_around_point(cx, cy, preview_radius)
            preview_image(bbox_wide, out, px=px, session=session)
            if preview_radius > req.radius and out.exists():
                from PIL import Image
                wide_img = Image.open(out)
                cropped = crop_image_to_bbox(wide_img, bbox_render=bbox_wide, bbox_target=bbox)
                cropped.save(out)
        else:
            preview_luchtfoto(bbox, out, px=px, session=session)

        if not out.exists():
            raise HTTPException(500, "Preview kon niet worden gegenereerd.")

        return FileResponse(out, media_type="image/png")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except http_requests.RequestException as e:
        raise HTTPException(502, f"Externe API-fout: {e}")
    except Exception as e:
        raise HTTPException(500, f"Preview mislukt: {e}")
