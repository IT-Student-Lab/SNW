# -*- coding: utf-8 -*-
"""Preview router — quick bbox preview image."""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import get_current_user
from api.models import PreviewRequest
from app.core import address_to_rd_full, bbox_around_point, build_all_outputs

router = APIRouter(prefix="/api", tags=["preview"])

_PREVIEW_PX = 800
_PREVIEW_TOPO_PX = 1200
_TOPO_MIN_SPAN_M = 3000.0


@router.post("/preview")
async def preview(
    req: PreviewRequest,
    _user: str = Depends(get_current_user),
):
    """Generate a quick preview image and return it as PNG."""
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

        # Use a temp dir that stays alive until response is sent
        tmp = tempfile.mkdtemp(prefix="pdok_preview_")
        preview_dir = Path(tmp)

        build_all_outputs(
            bbox=bbox,
            out_dir=preview_dir,
            px=_PREVIEW_PX,
            topo_px=_PREVIEW_TOPO_PX,
            topo_min_span_m=_TOPO_MIN_SPAN_M,
            session=session,
        )

        # Prefer luchtfoto, fallback to topo
        luchtfoto = preview_dir / "Luchtfoto.png"
        topo = preview_dir / "topo_kaart.png"
        img = luchtfoto if luchtfoto.exists() else topo

        if not img.exists():
            raise HTTPException(500, "Preview kon niet worden gegenereerd.")

        return FileResponse(img, media_type="image/png")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except http_requests.RequestException as e:
        raise HTTPException(502, f"Externe API-fout: {e}")
    except Exception as e:
        raise HTTPException(500, f"Preview mislukt: {e}")
