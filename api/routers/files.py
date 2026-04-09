# -*- coding: utf-8 -*-
"""Files router — list and download generated output files."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.deps import get_current_user
from api.models import FileInfo, FileListResponse
from app.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])


def _job_dir(job_id: str) -> Path:
    """Resolve and validate the job output directory."""
    # Prevent path traversal
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    d = Path(settings.output_dir) / job_id
    if not d.is_dir():
        raise HTTPException(404, "Job niet gevonden")
    return d


@router.get("/{job_id}", response_model=FileListResponse)
async def list_files(
    job_id: str,
    _user: str = Depends(get_current_user),
):
    """List all generated files for a job."""
    d = _job_dir(job_id)
    files = []
    for p in sorted(d.rglob("*")):
        if p.is_file():
            files.append(
                FileInfo(
                    filename=p.name,
                    size_bytes=p.stat().st_size,
                    extension=p.suffix.lower(),
                )
            )
    return FileListResponse(job_id=job_id, files=files)


@router.get("/{job_id}/download/{filename}")
async def download_file(
    job_id: str,
    filename: str,
    _user: str = Depends(get_current_user),
):
    """Download a single file from a job."""
    d = _job_dir(job_id)
    # Prevent path traversal in filename
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Ongeldige bestandsnaam")
    fpath = d / filename
    if not fpath.is_file():
        raise HTTPException(404, "Bestand niet gevonden")

    media = "application/octet-stream"
    ext = fpath.suffix.lower()
    if ext == ".png":
        media = "image/png"
    elif ext == ".dxf":
        media = "application/dxf"
    elif ext == ".scr":
        media = "text/plain"

    return FileResponse(fpath, media_type=media, filename=filename)


@router.get("/{job_id}/zip")
async def download_zip(
    job_id: str,
    _user: str = Depends(get_current_user),
):
    """Download all files for a job as a single zip archive."""
    d = _job_dir(job_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in d.rglob("*"):
            if p.is_file():
                z.write(p, arcname=p.relative_to(d))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={job_id}.zip"},
    )
