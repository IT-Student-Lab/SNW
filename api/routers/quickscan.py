# -*- coding: utf-8 -*-
"""Quickscan router — AI-powered location analysis + PPTX export."""

from __future__ import annotations

import io
import json
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
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
    """Build a professionally styled PPTX from quickscan sections."""
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.dml.color import RGBColor

    # Brand colours
    CLR_PEACH = RGBColor(0xEC, 0xC9, 0xA0)
    CLR_GREEN = RGBColor(0x6B, 0x9B, 0x37)
    CLR_BLUE = RGBColor(0xA8, 0xC8, 0xDE)
    CLR_DARK = RGBColor(0x33, 0x33, 0x33)
    CLR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    CLR_LIGHT_BG = RGBColor(0xF7, 0xF3, 0xED)

    SLIDE_W = Inches(13.333)
    SLIDE_H = Inches(7.5)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    def _add_color_bar(slide, left, top, width, height, color):
        """Add a solid color rectangle."""
        from pptx.util import Emu as _Emu
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE.RECTANGLE
            left, top, width, height,
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = color
        shape.line.fill.background()

    def _add_bg(slide, color):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    def _set_font(run, size=12, bold=False, color=CLR_DARK, name="Calibri"):
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = name

    # ─── Title slide ───────────────────────────────────────────────
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_bg(slide, CLR_WHITE)

    # Three colour bars at top (like the logo)
    bar_w = Inches(2.5)
    bar_h = Inches(0.35)
    bar_top = Inches(1.5)
    for i, clr in enumerate([CLR_PEACH, CLR_GREEN, CLR_BLUE]):
        _add_color_bar(slide, Inches(2.7 + i * 2.7), bar_top, bar_w, bar_h, clr)

    # Title
    txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.3), Inches(1.5))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = "AI Quickscan Analyse"
    _set_font(run, size=40, bold=True, color=CLR_DARK)

    # Subtitle
    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.CENTER
    run2 = p2.add_run()
    run2.text = "Studio Nico Wissing"
    _set_font(run2, size=20, color=CLR_GREEN)

    # Thin green line under title
    _add_color_bar(slide, Inches(4), Inches(4.2), Inches(5.3), Inches(0.04), CLR_GREEN)

    for section in sections:
        title = section.get("title", "")

        # ─── Location info slide ──────────────────────────────────
        if title == "_location_info":
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            _add_bg(slide, CLR_WHITE)

            # Green accent bar left
            _add_color_bar(slide, Inches(0), Inches(0), Inches(0.25), SLIDE_H, CLR_GREEN)

            # Section title
            txBox = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12), Inches(0.8))
            tf = txBox.text_frame
            run = tf.paragraphs[0].add_run()
            run.text = "Locatie-informatie"
            _set_font(run, size=28, bold=True, color=CLR_DARK)

            # Thin peach line
            _add_color_bar(slide, Inches(0.6), Inches(1.15), Inches(5), Inches(0.03), CLR_PEACH)

            # Info table with alternating row backgrounds
            info_items = [
                ("Adres", section.get("display_name", "—")),
                ("Gemeente", section.get("gemeente", "—")),
                ("Provincie", section.get("provincie", "—")),
                ("Woonplaats", section.get("woonplaats", "—")),
                ("Waterschap", section.get("waterschap", "—")),
                ("Buurt", section.get("buurt", "—")),
                ("Straal", f"{section.get('radius', '—')} m"),
            ]

            tbl = slide.shapes.add_table(
                len(info_items), 2, Inches(0.6), Inches(1.5),
                Inches(7), Inches(0.45 * len(info_items))
            ).table
            tbl.columns[0].width = Inches(2.2)
            tbl.columns[1].width = Inches(4.8)

            for row_idx, (label, value) in enumerate(info_items):
                row = tbl.rows[row_idx]
                row.height = Inches(0.45)

                cell_label = row.cells[0]
                cell_value = row.cells[1]

                cell_label.text = ""
                cell_value.text = ""

                run_l = cell_label.text_frame.paragraphs[0].add_run()
                run_l.text = label
                _set_font(run_l, size=13, bold=True, color=CLR_DARK)
                cell_label.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT

                run_v = cell_value.text_frame.paragraphs[0].add_run()
                run_v.text = str(value)
                _set_font(run_v, size=13, color=CLR_DARK)

                # Alternating row color
                bg_clr = CLR_LIGHT_BG if row_idx % 2 == 0 else CLR_WHITE
                for cell in [cell_label, cell_value]:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = bg_clr
                    cell.vertical_anchor = MSO_ANCHOR.MIDDLE

            continue

        # ─── Normal analysis section slide ────────────────────────
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _add_bg(slide, CLR_WHITE)

        # Blue accent bar left
        _add_color_bar(slide, Inches(0), Inches(0), Inches(0.25), SLIDE_H, CLR_BLUE)

        # Section title
        txBox = slide.shapes.add_textbox(Inches(0.6), Inches(0.3), Inches(12), Inches(0.7))
        tf = txBox.text_frame
        run = tf.paragraphs[0].add_run()
        run.text = title
        _set_font(run, size=26, bold=True, color=CLR_DARK)

        # Peach underline
        _add_color_bar(slide, Inches(0.6), Inches(0.95), Inches(4), Inches(0.03), CLR_PEACH)

        # Images
        images = section.get("images", [])
        img_count = 0
        img_top = Inches(1.2)
        for img_info in images[:3]:
            img_path = out_dir / img_info.get("filename", "")
            if img_path.exists():
                left = Inches(0.6 + img_count * 4.1)
                try:
                    pic = slide.shapes.add_picture(
                        str(img_path), left, img_top, width=Inches(3.8)
                    )
                    # Limit height so it doesn't overflow
                    if pic.height > Inches(3.2):
                        ratio = Inches(3.2) / pic.height
                        pic.height = Inches(3.2)
                        pic.width = int(pic.width * ratio)

                    # Caption below image
                    cap = img_info.get("caption", "")
                    if cap:
                        cap_box = slide.shapes.add_textbox(
                            left, img_top + pic.height + Inches(0.05),
                            pic.width, Inches(0.3)
                        )
                        cap_tf = cap_box.text_frame
                        cap_tf.word_wrap = True
                        cap_run = cap_tf.paragraphs[0].add_run()
                        cap_run.text = cap
                        _set_font(cap_run, size=9, color=RGBColor(0x66, 0x66, 0x66))
                        cap_tf.paragraphs[0].alignment = PP_ALIGN.CENTER

                    img_count += 1
                except Exception:
                    pass

        # Analysis text
        analysis = section.get("analysis", "")
        if analysis:
            text_top = img_top + Inches(3.8) if img_count > 0 else Inches(1.2)
            text_height = SLIDE_H - text_top - Inches(0.4)
            txBox2 = slide.shapes.add_textbox(
                Inches(0.6), text_top, Inches(12), text_height
            )
            tf2 = txBox2.text_frame
            tf2.word_wrap = True

            # Split analysis into paragraphs for readability
            for i, paragraph_text in enumerate(analysis.split("\n")):
                para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                if paragraph_text.strip():
                    run = para.add_run()
                    run.text = paragraph_text
                    _set_font(run, size=11, color=CLR_DARK)
                    para.space_after = Pt(4)

        # ─── Omgevingsvisie sub-slide ─────────────────────────────
        omgevingsvisie = section.get("omgevingsvisie")
        if omgevingsvisie:
            slide2 = prs.slides.add_slide(prs.slide_layouts[6])
            _add_bg(slide2, CLR_WHITE)
            _add_color_bar(slide2, Inches(0), Inches(0), Inches(0.25), SLIDE_H, CLR_GREEN)

            txBox = slide2.shapes.add_textbox(Inches(0.6), Inches(0.3), Inches(12), Inches(0.7))
            tf = txBox.text_frame
            run = tf.paragraphs[0].add_run()
            run.text = f"{title} — Omgevingsvisie"
            _set_font(run, size=24, bold=True, color=CLR_DARK)

            _add_color_bar(slide2, Inches(0.6), Inches(0.95), Inches(4), Inches(0.03), CLR_PEACH)

            txBox2 = slide2.shapes.add_textbox(Inches(0.6), Inches(1.2), Inches(12), Inches(5.8))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            for i, line in enumerate(omgevingsvisie.split("\n")):
                para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                if line.strip():
                    run = para.add_run()
                    run.text = line
                    _set_font(run, size=11, color=CLR_DARK)
                    para.space_after = Pt(4)

        # ─── History sub-slide ────────────────────────────────────
        history_web = section.get("history_web")
        if history_web:
            slide2 = prs.slides.add_slide(prs.slide_layouts[6])
            _add_bg(slide2, CLR_WHITE)
            _add_color_bar(slide2, Inches(0), Inches(0), Inches(0.25), SLIDE_H, CLR_PEACH)

            txBox = slide2.shapes.add_textbox(Inches(0.6), Inches(0.3), Inches(12), Inches(0.7))
            tf = txBox.text_frame
            run = tf.paragraphs[0].add_run()
            run.text = f"{title} — Historische analyse"
            _set_font(run, size=24, bold=True, color=CLR_DARK)

            _add_color_bar(slide2, Inches(0.6), Inches(0.95), Inches(4), Inches(0.03), CLR_PEACH)

            txBox2 = slide2.shapes.add_textbox(Inches(0.6), Inches(1.2), Inches(12), Inches(5.8))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            for i, line in enumerate(history_web.split("\n")):
                para = tf2.paragraphs[0] if i == 0 else tf2.add_paragraph()
                if line.strip():
                    run = para.add_run()
                    run.text = line
                    _set_font(run, size=11, color=CLR_DARK)
                    para.space_after = Pt(4)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


@router.get("/quickscan/{job_id}/cached")
async def quickscan_cached(
    job_id: str,
    _user: str = Depends(get_current_user),
):
    """Return cached quickscan results if available."""
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    cache_file = Path(settings.output_dir) / job_id / "quickscan.json"
    if not cache_file.exists():
        return {"sections": None}
    sections = json.loads(cache_file.read_text(encoding="utf-8"))
    return {"sections": sections}


@router.post("/quickscan/{job_id}/export")
async def quickscan_export_pptx(
    job_id: str,
    body: dict | None = Body(None),
    address: str | None = Query(None),
    x: float | None = Query(None),
    y: float | None = Query(None),
    radius: float = Query(250),
    _user: str = Depends(get_current_user),
):
    """Export quickscan results as a PowerPoint (.pptx) file.

    If sections are included in the request body, use those directly
    (fast path — no AI re-run). Otherwise fall back to running the
    full quickscan.
    """
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "Ongeldige job id")
    out_dir = Path(settings.output_dir) / job_id
    if not out_dir.is_dir():
        raise HTTPException(404, "Job niet gevonden")

    if body and "sections" in body and body["sections"]:
        sections = body["sections"]
    else:
        # Try loading cached results from disk first
        cache_file = out_dir / "quickscan.json"
        if cache_file.exists():
            sections = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
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
