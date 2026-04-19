# -*- coding: utf-8 -*-
"""Generate router — orchestrates the full DXF generation pipeline with SSE progress."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.deps import get_current_user
from api.models import GenerateRequest
from app.config import settings
from app.core import (
    address_to_rd_full,
    bbox_around_point,
    build_all_outputs,
    export_dxf,
)
from app.core.quickscan import run_quickscan
from app.usage import track_generation

router = APIRouter(prefix="/api", tags=["generate"])

# Same defaults as the Streamlit app
_PX = 2000
_TOPO_PX = 4000
_TOPO_MIN_SPAN_M = 3000.0
_INCLUDE_PERCELEN = True
_INCLUDE_BGT = True
_BGT_LIMIT = 2000

_ALLOWED_TEMPLATE_EXT = {".dxf"}

_RD_X_MIN, _RD_X_MAX = 0.0, 300_000.0
_RD_Y_MIN, _RD_Y_MAX = 300_000.0, 625_000.0
_DXF_NAME_RE = re.compile(r"^[\w\-. ]+\.dxf$", re.IGNORECASE)


def _resolve_location(req: GenerateRequest, session: http_requests.Session):
    """Return (cx, cy, loc_info_or_None)."""
    if req.mode == "address":
        if not req.address or len(req.address.strip()) < 3:
            raise HTTPException(400, "Vul een geldig adres in (minimaal 3 tekens).")
        loc_info = address_to_rd_full(req.address.strip(), session=session)
        return loc_info["x"], loc_info["y"], loc_info
    else:
        if req.x is None or req.y is None:
            raise HTTPException(400, "x en y coördinaten zijn verplicht.")
        if not (_RD_X_MIN <= req.x <= _RD_X_MAX):
            raise HTTPException(400, f"RD X moet tussen {_RD_X_MIN:.0f} en {_RD_X_MAX:.0f} liggen.")
        if not (_RD_Y_MIN <= req.y <= _RD_Y_MAX):
            raise HTTPException(400, f"RD Y moet tussen {_RD_Y_MIN:.0f} en {_RD_Y_MAX:.0f} liggen.")
        return req.x, req.y, None


def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/generate")
async def generate(
    req: GenerateRequest,
    username: str = Depends(get_current_user),
):
    """Run the full generation pipeline, streaming progress via SSE."""
    if not _DXF_NAME_RE.match(req.dxf_name):
        raise HTTPException(400, "Ongeldige DXF bestandsnaam.")

    # Validate location before starting the stream
    session = http_requests.Session()
    cx, cy, loc_info = _resolve_location(req, session)

    job_id = uuid.uuid4().hex[:12]
    out_dir = Path(settings.output_dir) / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    bbox = bbox_around_point(cx, cy, req.radius)
    display_address = req.address or (
        f"RD ({req.x:.0f}, {req.y:.0f})" if req.x and req.y else "onbekend"
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        success = False
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def send_progress(msg: str):
            """Thread-safe: put an SSE progress event into the queue."""
            asyncio.run_coroutine_threadsafe(
                queue.put(_sse_event("progress", {"message": msg})),
                loop,
            )

        async def _run_pipeline():
            nonlocal success

            await queue.put(_sse_event("progress", {
                "message": f"Locatie vastgesteld: {display_address}",
            }))

            rasters, _ = await loop.run_in_executor(
                None,
                lambda: build_all_outputs(
                    bbox=bbox,
                    out_dir=out_dir,
                    px=_PX,
                    topo_px=_TOPO_PX,
                    topo_min_span_m=_TOPO_MIN_SPAN_M,
                    session=session,
                    on_progress=send_progress,
                ),
            )

            await queue.put(_sse_event("progress", {
                "message": "DXF bestand samenstellen met kadaster- en BGT-data… (dit kan even duren)",
            }))

            # Resolve custom template if available
            template_path = None
            tpl_dir = Path(settings.template_dir)
            if tpl_dir.is_dir():
                for ext in _ALLOWED_TEMPLATE_EXT:
                    candidate = tpl_dir / f"custom_template{ext}"
                    if candidate.is_file():
                        template_path = candidate
                        break

            await loop.run_in_executor(
                None,
                lambda: export_dxf(
                    out_dir / req.dxf_name,
                    bbox=bbox,
                    raster_dir=out_dir,
                    rasters=rasters,
                    include_percelen=_INCLUDE_PERCELEN,
                    include_bgt=_INCLUDE_BGT,
                    bgt_limit_per_collection=_BGT_LIMIT,
                    template_path=template_path,
                    session=session,
                ),
            )

            success = True

            # Persist job metadata for history
            job_meta = {
                "job_id": job_id,
                "address": req.address or "",
                "x": cx,
                "y": cy,
                "radius": req.radius,
                "dxf_name": req.dxf_name,
                "user": username,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            (out_dir / "job.json").write_text(
                json.dumps(job_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            await queue.put(_sse_event("progress", {
                "message": "DXF export gereed. AI-analyse starten… (dit kan even duren)",
            }))

            # Auto-run quickscan
            try:
                qs_sections = await loop.run_in_executor(
                    None,
                    lambda: run_quickscan(
                        out_dir=out_dir,
                        loc_info=loc_info,
                        adres=display_address,
                        radius=req.radius,
                        bbox=tuple(bbox),
                        session=session,
                    ),
                )

                # Cache to disk so PPTX export can reuse without AI re-run
                qs_cache = out_dir / "quickscan.json"
                qs_cache.write_text(
                    json.dumps(qs_sections, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                await queue.put(_sse_event("quickscan", {
                    "sections": qs_sections,
                }))
            except Exception:
                await queue.put(_sse_event("quickscan_error", {
                    "message": "AI quickscan kon niet worden uitgevoerd.",
                }))

            await queue.put(_sse_event("complete", {
                "job_id": job_id,
                "message": "Alles gereed!",
            }))

        async def _run_and_finalize():
            try:
                await _run_pipeline()
            except Exception as e:
                await queue.put(_sse_event("error", {
                    "message": f"Generatie mislukt: {e}",
                }))
            finally:
                track_generation(
                    job_id=job_id,
                    user=username,
                    address=req.address or "",
                    x=cx,
                    y=cy,
                    radius=req.radius,
                    success=success,
                )
                await queue.put(None)  # sentinel

        # Launch pipeline as a background task
        asyncio.ensure_future(_run_and_finalize())

        # Yield events as they arrive
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
