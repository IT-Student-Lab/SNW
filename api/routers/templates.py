# -*- coding: utf-8 -*-
"""Template upload / management router."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse

import ezdxf
from ezdxf.addons import odafc

from api.deps import get_current_user
from app.config import settings
from app.core.dxf import _odafc_read

router = APIRouter(prefix="/api/template", tags=["template"])

_ALLOWED_EXT = {".dxf", ".dwt", ".dwg"}
_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _template_dir() -> Path:
    d = Path(settings.template_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_custom_template() -> Path | None:
    """Return the path of the active custom template, or None."""
    d = _template_dir()
    for ext in _ALLOWED_EXT:
        p = d / f"custom_template{ext}"
        if p.is_file():
            return p
    return None


@router.get("")
async def get_template_info(_user: str = Depends(get_current_user)):
    """Return info about the current template."""
    t = _find_custom_template()
    if t:
        stat = t.stat()
        return {
            "type": "custom",
            "filename": t.name,
            "size": stat.st_size,
            "uploaded_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    return {"type": "default", "filename": None, "size": None, "uploaded_at": None}


@router.post("")
async def upload_template(file: UploadFile, _user: str = Depends(get_current_user)):
    """Upload a .dwt or .dwg file as the active template."""
    if not file.filename:
        raise HTTPException(400, "Geen bestand geselecteerd")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            400,
            f"Ongeldig bestandstype '{ext}'. Gebruik .dxf, .dwt of .dwg.",
        )

    data = await file.read()
    if len(data) > _MAX_SIZE_BYTES:
        raise HTTPException(400, "Bestand is groter dan 50 MB")

    # Remove any existing custom template
    for old_ext in _ALLOWED_EXT:
        old = _template_dir() / f"custom_template{old_ext}"
        if old.is_file():
            os.remove(old)

    dest = _template_dir() / f"custom_template{ext}"
    dest.write_bytes(data)

    # Validate that the file can actually be read
    try:
        if ext in (".dwt", ".dwg"):
            if not odafc.is_installed():
                raise HTTPException(
                    400,
                    "ODA File Converter is niet geïnstalleerd op de server. "
                    "Upload een .dxf bestand, of installeer ODA File Converter "
                    "voor .dwt/.dwg ondersteuning.",
                )
            _odafc_read(dest)
        else:
            ezdxf.readfile(str(dest))
    except HTTPException:
        os.remove(dest)
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        os.remove(dest)
        raise HTTPException(
            400,
            f"Het bestand kon niet worden gelezen als geldig {ext} bestand.",
        )

    return JSONResponse(
        {"message": "Sjabloon geüpload", "filename": dest.name, "size": len(data)},
        status_code=201,
    )


@router.delete("")
async def delete_template(_user: str = Depends(get_current_user)):
    """Remove the custom template, falling back to the default."""
    removed = False
    for ext in _ALLOWED_EXT:
        p = _template_dir() / f"custom_template{ext}"
        if p.is_file():
            os.remove(p)
            removed = True
    if not removed:
        raise HTTPException(404, "Geen custom sjabloon gevonden")
    return {"message": "Custom sjabloon verwijderd, standaard wordt weer gebruikt"}
