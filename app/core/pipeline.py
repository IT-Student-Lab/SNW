# -*- coding: utf-8 -*-
"""Main pipeline: build all outputs, export DXF, preview."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
import ezdxf
import requests
from PIL import Image, ImageDraw, ImageFont

from app.core.bgt import add_all_bgt_to_dxf, add_kadaster_percelen_to_dxf
from app.core.downloads import (
    build_plu_outputs,
    download_ahn,
    download_bodemvlakken_with_dominant_legend,
    download_gmk_with_dominant_legend,
    download_kadastrale_kaart,
    download_ligging_breed,
    download_luchtfoto,
    download_natura2000,
    download_topo_image,
    download_topotijdreis,
    download_wdm,
)
from app.core.dxf import (
    add_georef_image_to_doc,
    create_doc,
    create_layer_filter_groups,
    ensure_layer_onoff,
    export_dwg_copy,
    write_image_reload_script,
    write_layer_toggle_scripts,
)
from app.core.locatie import address_to_rd, bbox_around_point
from app.core.log_config import get_logger
from app.core.raster import crop_image_to_bbox
from app.core.types import BBox, ExportPlan, MapRequest
from app.core.utils import bbox_center, ensure_dir
from app.core.wms import wms_base_params, wms_get_image
from app.core.constants import WMS_LUCHTFOTO, WMS_TOPO

logger = get_logger(__name__)


# --------------- Helpers: empty‑image check & placeholder ---------------

_THEMATIC_RADIUS = 5000.0  # metres – wider bbox for scale‑dependent WMS layers


def _is_image_empty(path: Path, threshold: float = 0.02) -> bool:
    """Return *True* when <threshold of pixels carry actual map data.

    Ignores fully‑transparent pixels AND near‑white pixels (background).
    """
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    non_trivial = (alpha > 10) & ~np.all(rgb > 245, axis=2)
    return float(non_trivial.sum()) / (arr.shape[0] * arr.shape[1]) < threshold


def _save_no_data_placeholder(
    path: Path, title: str, px: int = 2000
) -> None:
    """Overwrite *path* with a neutral 'geen data beschikbaar' image."""
    img = Image.new("RGBA", (px, px), (245, 245, 245, 255))
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("arial.ttf", 48)
        font_sub = ImageFont.truetype("arial.ttf", 32)
    except OSError:
        font_title = ImageFont.load_default(size=48)
        font_sub = ImageFont.load_default(size=32)
    draw.text(
        (px // 2, px // 2 - 40), title,
        fill=(100, 100, 100), font=font_title, anchor="mm",
    )
    draw.text(
        (px // 2, px // 2 + 30),
        "Geen data beschikbaar voor deze locatie",
        fill=(150, 150, 150), font=font_sub, anchor="mm",
    )
    img.save(path)


# --------------- Preview ---------------

def preview_image(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 1000,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    img = wms_get_image(
        WMS_TOPO,
        {**wms_base_params(req), "LAYERS": "top25raster", "STYLES": ""},
        session=session,
    )
    img.save(out_path)


def preview_luchtfoto(
    bbox: BBox,
    out_path: Path,
    *,
    px: int = 1000,
    session: Optional[requests.Session] = None,
) -> None:
    req = MapRequest(bbox=bbox, width=px, height=px, transparent=False)
    img = wms_get_image(
        WMS_LUCHTFOTO,
        {**wms_base_params(req), "LAYERS": "Actueel_orthoHR", "STYLES": ""},
        session=session,
    )
    img.save(out_path)


# --------------- Build all raster outputs ---------------

def build_all_outputs(
    bbox: BBox,
    out_dir: Path,
    *,
    px: int = 2000,
    topo_px: int = 4000,
    topo_min_span_m: float = 3000.0,
    session: Optional[requests.Session] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Tuple[List[ExportPlan], Optional[BBox]]:
    ensure_dir(out_dir)

    def _progress(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    fn_luchtfoto = "Luchtfoto.png"
    fn_luchtfoto_kad = "luchtfoto_kadaster.png"
    fn_topo = "topo_kaart.png"
    fn_best_enkel = "Bestemming_percelen.png"
    fn_best_dubbel = "Bestemming_dubbel.png"
    fn_gmk = "Geomorfologische_kaart.png"
    fn_ahn_dsm = "ahn_dsm.png"
    fn_ahn_dtm = "ahn_dtm.png"
    fn_wdm_ghg = "wdm_ghg.png"
    fn_wdm_glg = "wdm_glg.png"
    fn_wdm_gt = "wdm_gt.png"
    fn_bodemvlakken = "bodemvlakken.png"
    fn_natura2000 = "natura2000.png"
    fn_ligging_topo = "ligging_topo_breed.png"
    fn_ligging_lucht = "ligging_luchtfoto_breed.png"

    _progress("Luchtfoto ophalen…")
    download_luchtfoto(bbox, out_dir / fn_luchtfoto, px=px, session=session)

    _progress("Kadastrale kaart samenvoegen met luchtfoto…")
    kad = download_kadastrale_kaart(bbox, px=px, session=session)
    lucht = Image.open(out_dir / fn_luchtfoto).convert("RGBA")
    lucht_plus = Image.alpha_composite(lucht, kad)
    lucht_plus.save(out_dir / fn_luchtfoto_kad)

    _progress("Bestemmingsplan kaarten ophalen…")
    build_plu_outputs(
        bbox,
        out_dir / fn_best_enkel,
        out_dir / fn_best_dubbel,
        px=px,
        session=session,
    )

    _progress("Topokaart voorbereiden…")
    cx, cy = bbox_center(bbox)
    topo_radius = max(3000.0, topo_min_span_m / 2.0)
    bbox_topo_big = bbox_around_point(cx, cy, topo_radius)
    bbox_thematic = bbox_around_point(cx, cy, _THEMATIC_RADIUS)

    topo_big = download_topo_image(bbox_topo_big, px=topo_px, session=session)
    topo_cropped = crop_image_to_bbox(
        topo_big, bbox_render=bbox_topo_big, bbox_target=bbox
    )
    topo_cropped.convert("RGB").save(out_dir / fn_topo)

    bbox_topo_for_dxf = bbox

    _progress("Geomorfologische kaart ophalen…")
    download_gmk_with_dominant_legend(
        bbox_thematic, out_dir / fn_gmk, px=px, session=session
    )
    if _is_image_empty(out_dir / fn_gmk):
        _save_no_data_placeholder(
            out_dir / fn_gmk, "Geomorfologische kaart", px=px,
        )

    _progress("Hoogtekaarten (AHN) ophalen…")
    download_ahn(
        bbox,
        out_dir / fn_ahn_dsm,
        px=px,
        product="dsm",
        add_legend=True,
        session=session,
    )
    download_ahn(
        bbox,
        out_dir / fn_ahn_dtm,
        px=px,
        product="dtm",
        add_legend=True,
        session=session,
    )

    _progress("Grondwaterstanden ophalen…")
    download_wdm(
        bbox_thematic,
        out_dir / fn_wdm_ghg,
        layer="bro-grondwaterspiegeldieptemetingen-GHG",
        px=px,
        session=session,
    )
    if _is_image_empty(out_dir / fn_wdm_ghg):
        _save_no_data_placeholder(
            out_dir / fn_wdm_ghg, "Grondwaterstand (GHG)", px=px,
        )
    download_wdm(
        bbox_thematic,
        out_dir / fn_wdm_glg,
        layer="bro-grondwaterspiegeldieptemetingen-GLG",
        px=px,
        session=session,
    )
    if _is_image_empty(out_dir / fn_wdm_glg):
        _save_no_data_placeholder(
            out_dir / fn_wdm_glg, "Grondwaterstand (GLG)", px=px,
        )
    download_wdm(
        bbox_thematic,
        out_dir / fn_wdm_gt,
        layer="bro-grondwaterspiegeldieptemetingen-GT",
        px=px,
        session=session,
    )
    if _is_image_empty(out_dir / fn_wdm_gt):
        _save_no_data_placeholder(
            out_dir / fn_wdm_gt, "Grondwatertrap (GT)", px=px,
        )

    _progress("Bodemkaart ophalen…")
    download_bodemvlakken_with_dominant_legend(
        bbox_thematic, out_dir / fn_bodemvlakken, px=px, session=session
    )
    if _is_image_empty(out_dir / fn_bodemvlakken):
        _save_no_data_placeholder(
            out_dir / fn_bodemvlakken, "Bodemvlakken", px=px,
        )

    _progress("Natura 2000 gegevens ophalen…")
    n2k_radius = 10_000.0
    bbox_natura2000 = bbox_around_point(cx, cy, n2k_radius)
    try:
        download_natura2000(
            bbox, out_dir / fn_natura2000,
            center=(cx, cy),
            breed_radius=n2k_radius,
            px=px,
            session=session,
        )
    except Exception as e:
        logger.warning("Natura 2000 download mislukt: %s", e)

    _progress("Overzichtskaarten maken…")
    try:
        download_ligging_breed(
            bbox,
            out_dir / fn_ligging_topo,
            out_dir / fn_ligging_lucht,
            center=(cx, cy),
            breed_radius=3000.0,
            px=px,
            session=session,
        )
    except Exception as e:
        logger.warning("Ligging breed download mislukt: %s", e)

    _progress("Historische kaarten ophalen…")
    try:
        download_topotijdreis(
            bbox, out_dir,
            years=[1900, 1950, 2000],
            center=(cx, cy),
            breed_radius=1000.0,
            session=session,
        )
    except Exception as e:
        logger.warning("Topotijdreis download mislukt: %s", e)

    rasters = [
        ExportPlan(fn_topo, bbox_topo_for_dxf, "01-00-00-TOPOKAART", default_on=False),
        ExportPlan(fn_luchtfoto_kad, bbox, "01-00-00-LUCHTFOTO_KADASTER", default_on=False),
        ExportPlan(fn_luchtfoto, bbox, "01-00-00-LUCHTFOTO_PDOK", default_on=True),
        ExportPlan(fn_wdm_ghg, bbox_thematic, "01-00-00-GRONDWATERSTAND_GHG", default_on=False),
        ExportPlan(fn_wdm_glg, bbox_thematic, "01-00-00-GRONDWATERSTAND_GLG", default_on=False),
        ExportPlan(fn_wdm_gt, bbox_thematic, "01-00-00-GRONDWATERTRAP_GT", default_on=False),
        ExportPlan(fn_gmk, bbox_thematic, "01-00-00-GEOMORFOLOGISCHE_KAART", default_on=False),
        ExportPlan(fn_best_enkel, bbox, "01-00-00-BESTEMMINGSPLAN_ENKEL", default_on=False),
        ExportPlan(fn_best_dubbel, bbox, "01-00-00-BESTEMMINGSPLAN_DUBBEL", default_on=False),
        ExportPlan(fn_ahn_dsm, bbox, "01-00-00-HOOGTEKAART_AHN_DSM", default_on=False),
        ExportPlan(fn_ahn_dtm, bbox, "01-00-00-HOOGTEKAART_AHN_DTM", default_on=False),
        ExportPlan(fn_bodemvlakken, bbox_thematic, "01-00-00-BODEMVLAKKEN", default_on=False),
        ExportPlan(fn_natura2000, bbox_natura2000, "01-00-00-NATURA2000", default_on=False),
    ]

    return rasters, bbox_topo_for_dxf


# --------------- DXF export ---------------

def export_dxf(
    out_dxf: Path,
    *,
    bbox: BBox,
    raster_dir: Path,
    rasters: List[ExportPlan],
    include_percelen: bool = True,
    include_bgt: bool = True,
    bgt_limit_per_collection: int = 2000,
    template_path: Optional[Path] = None,
    session: Optional[requests.Session] = None,
) -> Path:
    doc = create_doc(template_path)
    msp = doc.modelspace()

    for rp in rasters:
        minx, miny, maxx, maxy = rp.bbox
        logger.info(
            "[DXF IMG] %s layer=%s bbox=%s span=(%.1fm, %.1fm)",
            rp.filename,
            rp.dxf_layer,
            rp.bbox,
            maxx - minx,
            maxy - miny,
        )
        ensure_layer_onoff(doc, rp.dxf_layer, default_on=rp.default_on, color=7)
        add_georef_image_to_doc(
            doc, raster_dir / rp.filename, rp.bbox, layer=rp.dxf_layer
        )

    if include_percelen:
        add_kadaster_percelen_to_dxf(doc, msp, bbox, session=session)

    if include_bgt:
        add_all_bgt_to_dxf(
            doc,
            msp,
            bbox,
            limit_per_collection=bgt_limit_per_collection,
            session=session,
        )

    create_layer_filter_groups(doc)
    doc.saveas(out_dxf)

    # Also export a .dwg copy if ODA File Converter is available
    export_dwg_copy(out_dxf)

    try:
        reload_scr = write_image_reload_script(out_dxf)
        logger.info("AutoCAD reload script geschreven: %s", reload_scr)
    except Exception as e:
        logger.warning("Kon reload_images.scr niet schrijven: %s", e)

    if include_bgt:
        scr_on = out_dxf.parent / "toggle_BGT_AAN.scr"
        scr_off = out_dxf.parent / "toggle_BGT_UIT.scr"

        if not scr_on.exists() or not scr_off.exists():
            try:
                scr_on, scr_off = write_layer_toggle_scripts(
                    doc, out_dxf, prefix="BGT-"
                )
                logger.info(
                    "AutoCAD scripts geschreven: %s, %s", scr_on, scr_off
                )
            except Exception as e:
                logger.warning(
                    "Kon BGT toggle scripts niet schrijven: %s", e
                )

    return out_dxf


# --------------- CLI ---------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Genereer PDOK CAD onderlegger (DXF) met raster + vector overlays."
    )
    p.add_argument("--adres", type=str, default="", help="Adres voor centrum.")
    p.add_argument("--x", type=float, default=None, help="RD X.")
    p.add_argument("--y", type=float, default=None, help="RD Y.")
    p.add_argument(
        "--radius", type=float, default=250.0, help="Radius in meters."
    )
    p.add_argument(
        "--outdir",
        type=str,
        default="output_onderlegger",
        help="Output map.",
    )
    p.add_argument(
        "--dxf", type=str, default="onderlegger.dxf", help="DXF bestandsnaam."
    )
    p.add_argument("--px", type=int, default=2000)
    p.add_argument("--topo-px", type=int, default=4000)
    p.add_argument("--topo-min-span", type=float, default=3000.0)
    p.add_argument("--no-bgt", action="store_true")
    p.add_argument("--no-percelen", action="store_true")
    p.add_argument("--bgt-limit", type=int, default=2000)
    return p.parse_args(argv)


def cli_main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.outdir).resolve()
    ensure_dir(out_dir)

    session = requests.Session()

    if args.x is not None and args.y is not None:
        x, y = float(args.x), float(args.y)
    else:
        if not args.adres.strip():
            raise SystemExit("Geef --adres of (--x en --y).")
        logger.info("[LOC] Adres -> RD: %s", args.adres)
        x, y = address_to_rd(args.adres, session=session)
        logger.info("[LOC] RD: x=%.3f, y=%.3f", x, y)

    bbox = bbox_around_point(x, y, float(args.radius))
    logger.info("[BBOX] %s", bbox)

    preview_image(bbox, out_dir / "preview_topo.png", px=1000, session=session)
    rasters, _bbox_topo = build_all_outputs(
        bbox=bbox,
        out_dir=out_dir,
        px=int(args.px),
        topo_px=int(args.topo_px),
        topo_min_span_m=float(args.topo_min_span),
        session=session,
    )

    out_dxf = out_dir / args.dxf
    logger.info("[DXF] Export -> %s", out_dxf)

    export_dxf(
        out_dxf,
        bbox=bbox,
        raster_dir=out_dir,
        rasters=rasters,
        include_percelen=not args.no_percelen,
        include_bgt=not args.no_bgt,
        bgt_limit_per_collection=int(args.bgt_limit),
        session=session,
    )

    logger.info("[DONE!]")
    return 0
