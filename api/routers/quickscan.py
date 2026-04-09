# -*- coding: utf-8 -*-
"""Quickscan router — AI-powered location analysis + PPTX export."""

from __future__ import annotations

import io
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.deps import get_current_user
from app.config import settings
from app.core import address_to_rd_full, bbox_around_point
from app.core.quickscan import run_quickscan

router = APIRouter(prefix="/api", tags=["quickscan"])


@router.post("/quickscan/{job_id}")
async def quickscan(
    job_id: str,
    address: str | None = Query(None),
    x: float | None = Query(None),
    y: float | None = Query(None),
    radius: float = Query(250),
    _user: str = Depends(get_current_user),
):
    """Run AI quickscan analysis on the generated output of a job.

    Pass either `address` or `x`+`y` so we can build location context.
    """
    # Prevent path traversal
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    out_dir = Path(settings.output_dir) / job_id
    if not out_dir.is_dir():
        raise HTTPException(404, "Job niet gevonden")

    session = http_requests.Session()
    loc_info = None
    bbox = None

    try:
        if address and len(address.strip()) >= 3:
            loc_info = address_to_rd_full(address.strip(), session=session)
            cx, cy = loc_info["x"], loc_info["y"]
        elif x is not None and y is not None:
            cx, cy = x, y
        else:
            raise HTTPException(400, "Geef een adres of x+y coördinaten op.")

        bbox = tuple(bbox_around_point(cx, cy, radius))
    except HTTPException:
        raise
    except Exception:
        pass  # quickscan can still run without bbox

    display_address = address or (f"RD ({x:.0f}, {y:.0f})" if x and y else "onbekend")

    try:
        sections = run_quickscan(
            out_dir=out_dir,
            loc_info=loc_info,
            adres=display_address,
            radius=radius,
            bbox=bbox,
            session=session,
        )
        return {"job_id": job_id, "sections": sections}
    except Exception as e:
        raise HTTPException(500, f"Quickscan mislukt: {e}")


def _build_quickscan_data(job_id: str, address: str | None, x: float | None,
                          y: float | None, radius: float):
    """Shared helper: resolve location + run quickscan. Returns (sections, out_dir)."""
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    out_dir = Path(settings.output_dir) / job_id
    if not out_dir.is_dir():
        raise HTTPException(404, "Job niet gevonden")

    session = http_requests.Session()
    loc_info = None
    bbox = None

    try:
        if address and len(address.strip()) >= 3:
            loc_info = address_to_rd_full(address.strip(), session=session)
            cx, cy = loc_info["x"], loc_info["y"]
        elif x is not None and y is not None:
            cx, cy = x, y
        else:
            raise HTTPException(400, "Geef een adres of x+y coördinaten op.")
        bbox = tuple(bbox_around_point(cx, cy, radius))
    except HTTPException:
        raise
    except Exception:
        pass

    display_address = address or (f"RD ({x:.0f}, {y:.0f})" if x and y else "onbekend")

    sections = run_quickscan(
        out_dir=out_dir,
        loc_info=loc_info,
        adres=display_address,
        radius=radius,
        bbox=bbox,
        session=session,
    )
    return sections, out_dir


def _sections_to_pptx(sections: list[dict], out_dir: Path) -> io.BytesIO:
    """Build a PPTX file from quickscan sections. Returns BytesIO buffer."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Title slide
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11), Inches(2))
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    p.text = "AI Quickscan Analyse"
    p.font.size = Pt(36)
    p.font.bold = True
    p.alignment = PP_ALIGN.CENTER

    for section in sections:
        title = section.get("title", "")

        # Skip internal location info — render it as a special slide
        if title == "_location_info":
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = "Locatie-informatie"
            p.font.size = Pt(28)
            p.font.bold = True

            info_lines = [
                f"Adres: {section.get('display_name', '—')}",
                f"Gemeente: {section.get('gemeente', '—')}",
                f"Provincie: {section.get('provincie', '—')}",
                f"Woonplaats: {section.get('woonplaats', '—')}",
                f"Waterschap: {section.get('waterschap', '—')}",
                f"Buurt: {section.get('buurt', '—')}",
                f"Straal: {section.get('radius', '—')} m",
            ]
            txBox2 = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), Inches(12), Inches(5))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            for i, line in enumerate(info_lines):
                para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                para.text = line
                para.font.size = Pt(16)
            continue

        # Normal analysis section
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        # Title
        txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.font.size = Pt(28)
        p.font.bold = True

        # Images
        images = section.get("images", [])
        img_top = Inches(1.2)
        for j, img_info in enumerate(images[:3]):
            img_path = out_dir / img_info.get("filename", "")
            if img_path.exists():
                left = Inches(0.5 + j * 4.2)
                try:
                    slide.shapes.add_picture(
                        str(img_path), left, img_top, width=Inches(4)
                    )
                except Exception:
                    pass

        # Analysis text
        analysis = section.get("analysis", "")
        if analysis:
            text_top = img_top + Inches(3.5) if images else Inches(1.2)
            txBox2 = slide.shapes.add_textbox(
                Inches(0.5), text_top, Inches(12), Inches(3)
            )
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            p2 = tf2.paragraphs[0]
            p2.text = analysis
            p2.font.size = Pt(12)

        # Omgevingsvisie on separate slide
        omgevingsvisie = section.get("omgevingsvisie")
        if omgevingsvisie:
            slide2 = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide2.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = f"{title} — Omgevingsvisie"
            p.font.size = Pt(24)
            p.font.bold = True

            txBox2 = slide2.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            p2 = tf2.paragraphs[0]
            p2.text = omgevingsvisie
            p2.font.size = Pt(12)

        # History web on separate slide
        history_web = section.get("history_web")
        if history_web:
            slide2 = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide2.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = f"{title} — Historische analyse"
            p.font.size = Pt(24)
            p.font.bold = True

            txBox2 = slide2.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            p2 = tf2.paragraphs[0]
            p2.text = history_web
            p2.font.size = Pt(12)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


@router.post("/quickscan/{job_id}/export")
async def quickscan_export_pptx(
    job_id: str,
    address: str | None = Query(None),
    x: float | None = Query(None),
    y: float | None = Query(None),
    radius: float = Query(250),
    _user: str = Depends(get_current_user),
):
    """Export quickscan results as a PowerPoint (.pptx) file."""
    try:
        sections, out_dir = _build_quickscan_data(job_id, address, x, y, radius)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Quickscan mislukt: {e}")

    buf = _sections_to_pptx(sections, out_dir)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f"attachment; filename=quickscan_{job_id}.pptx"
        },
    )
