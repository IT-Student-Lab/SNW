# -*- coding: utf-8 -*-
"""Jobs router — list, view, and delete historical jobs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_user
from app.config import settings

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_OUTPUT = Path(settings.output_dir)


def _read_job_meta(job_dir: Path) -> dict | None:
    """Read job.json from a job directory, return None on failure."""
    meta_file = job_dir / "job.json"
    if not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta["has_quickscan"] = (job_dir / "quickscan.json").exists()
        return meta
    except Exception:
        return None


@router.get("")
async def list_jobs(_user: str = Depends(get_current_user)):
    """List all jobs with metadata, newest first."""
    if not _OUTPUT.is_dir():
        return {"jobs": []}

    jobs: list[dict] = []
    for item in _OUTPUT.iterdir():
        if not item.is_dir():
            continue
        meta = _read_job_meta(item)
        if meta:
            jobs.append(meta)

    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return {"jobs": jobs}


@router.get("/{job_id}")
async def get_job(job_id: str, _user: str = Depends(get_current_user)):
    """Get metadata for a single job."""
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    job_dir = _OUTPUT / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "Job niet gevonden")
    meta = _read_job_meta(job_dir)
    if not meta:
        raise HTTPException(404, "Job metadata niet gevonden")
    return meta


@router.delete("/{job_id}")
async def delete_job(job_id: str, _user: str = Depends(get_current_user)):
    """Delete a job and all its files."""
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    job_dir = _OUTPUT / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "Job niet gevonden")
    shutil.rmtree(job_dir)
    return {"message": "Job verwijderd"}
