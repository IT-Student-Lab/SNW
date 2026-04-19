# -*- coding: utf-8 -*-
"""DXF helpers: layers, geometry, georeferenced images, toggle scripts."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import ezdxf
from ezdxf.addons import odafc
from PIL import Image
from shapely.geometry import shape

from app.core.log_config import get_logger

logger = get_logger(__name__)

# --------------- Default SNW layer structure ---------------

SNW_DEFAULT_LAYERS: List[str] = [
    "00 HUIDIGE SITUATIE",
    "05 EXTERNE DOCUMENTEN",
    "10 OPRUIMEN AFVOEREN",
    "20 GRONDWERK",
    "30 VERHARDINGEN",
    "40 BEPLANTING",
    "50 BOUWKUNDIG",
    "60 ELECTRA",
    "70 IRRIGATIE",
    "80 ERFSCHEIDING",
    "95 MAATVOERING",
    "96 OVERIG",
    "98 PRESENTATIE",
    "999 DETAILS",
]


def _odafc_read(path: Path):
    """Read a .dwg or .dwt file via ODA File Converter.

    ODA FC does not recognise .dwt as a format, but .dwt is structurally
    identical to .dwg, so we copy to a temp .dwg before converting.
    """
    if path.suffix.lower() == ".dwt":
        tmp_dir = tempfile.mkdtemp(prefix="snw_dwt_")
        tmp_dwg = Path(tmp_dir) / (path.stem + ".dwg")
        shutil.copy2(path, tmp_dwg)
        return odafc.readfile(str(tmp_dwg))
    return odafc.readfile(str(path))


def create_doc(template_path: Optional[Path] = None):
    """Create a DXF document from a template or with default SNW layers.

    Supports .dxf directly via ezdxf, and .dwt/.dwg via ODA File Converter.
    """
    if template_path and template_path.is_file():
        ext = template_path.suffix.lower()
        if ext in (".dwt", ".dwg"):
            if not odafc.is_installed():
                logger.warning(
                    "ODA File Converter niet geïnstalleerd — "
                    "kan %s niet lezen, standaard sjabloon wordt gebruikt.",
                    template_path.name,
                )
                return _new_default_doc()
            doc = _odafc_read(template_path)
        else:
            doc = ezdxf.readfile(str(template_path))
        return doc
    return _new_default_doc()


def _new_default_doc():
    """Create a fresh DXF document with the default SNW layers."""
    doc = ezdxf.new(setup=True)
    for name in SNW_DEFAULT_LAYERS:
        ensure_layer(doc, name, color=7)
    return doc


def ensure_layer(doc, name: str, *, color: int = 7) -> None:
    if name not in doc.layers:
        doc.layers.new(name=name, dxfattribs={"color": color})


def ensure_layer_onoff(doc, name: str, *, default_on: bool, color: int = 7) -> None:
    ensure_layer(doc, name, color=color)
    lyr = doc.layers.get(name)
    try:
        lyr.thaw()
        if default_on:
            lyr.on()
        else:
            lyr.off()
    except Exception:
        pass


def safe_layer_name(s: str, prefix: str = "BGT-") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
    return f"{prefix}{s}"[:255]


def add_any_geom_to_dxf(msp, geom, layer: str) -> None:
    if geom is None or geom.is_empty:
        return

    minx, miny, maxx, maxy = geom.bounds
    if not (
        0 <= minx <= 300000
        and 0 <= maxx <= 300000
        and 300000 <= miny <= 650000
        and 300000 <= maxy <= 650000
    ):
        return

    gtype = geom.geom_type

    if gtype == "Point":
        msp.add_point(
            (float(geom.x), float(geom.y)), dxfattribs={"layer": layer}
        )
        return
    if gtype == "MultiPoint":
        for p in geom.geoms:
            msp.add_point(
                (float(p.x), float(p.y)), dxfattribs={"layer": layer}
            )
        return

    if gtype == "LineString":
        coords = [(float(x), float(y)) for x, y in geom.coords]
        if len(coords) >= 2:
            msp.add_lwpolyline(coords, dxfattribs={"layer": layer})
        return
    if gtype == "MultiLineString":
        for ls in geom.geoms:
            add_any_geom_to_dxf(msp, ls, layer)
        return

    if gtype == "Polygon":
        ring = [(float(x), float(y)) for x, y in geom.exterior.coords]
        if len(ring) >= 3:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
        return
    if gtype == "MultiPolygon":
        for poly in geom.geoms:
            add_any_geom_to_dxf(msp, poly, layer)
        return

    if gtype == "GeometryCollection":
        for gg in geom.geoms:
            add_any_geom_to_dxf(msp, gg, layer)
        return


def add_georef_image_to_doc(
    doc,
    image_path: Path,
    bbox_rd,
    layer: str,
    fade: int = 0,
    contrast: int = 50,
    brightness: int = 50,
) -> None:
    minx, miny, maxx, maxy = bbox_rd
    width_units = float(maxx - minx)
    height_units = float(maxy - miny)

    rel = image_path.name

    with Image.open(image_path) as im:
        w_px, h_px = im.size

    img_def = doc.add_image_def(filename=rel, size_in_pixel=(w_px, h_px))

    image_entity = doc.modelspace().add_image(
        img_def,
        insert=(minx, miny),
        size_in_units=(width_units, height_units),
        rotation=0,
        dxfattribs={"layer": layer},
    )

    image_entity.transparency = 0.0

    try:
        image_entity.dxf.fade = int(fade)
        image_entity.dxf.contrast = int(contrast)
        image_entity.dxf.brightness = int(brightness)
    except Exception:
        pass

    try:
        doc.objects.add_image_def_reactor(
            img_def.dxf.handle, image_entity.dxf.handle
        )
    except Exception:
        pass


def export_dwg_copy(dxf_path: Path) -> Optional[Path]:
    """Convert a .dxf file to .dwg using ODA File Converter. Returns the .dwg path or None."""
    if not odafc.is_installed():
        logger.info("ODA File Converter niet geïnstalleerd — DWG export overgeslagen.")
        return None
    dwg_path = dxf_path.with_suffix(".dwg")
    try:
        odafc.convert(str(dxf_path), str(dwg_path))
        logger.info("DWG export geslaagd: %s", dwg_path)
        return dwg_path
    except Exception as e:
        logger.warning("DWG export mislukt: %s", e)
        return None


def write_image_reload_script(dxf_out: Path) -> Path:
    """Generate an AutoCAD .scr that reloads all image xrefs."""
    scr = dxf_out.parent / "reload_images.scr"
    scr.write_text(
        "-IMAGE\n_Reload\n*\n\n",
        encoding="utf-8",
    )
    return scr


def write_layer_toggle_scripts(
    doc, dxf_out: Path, prefix: str = "BGT-"
) -> Tuple[Path, Path]:
    layers = [
        lyr.dxf.name
        for lyr in doc.layers
        if lyr.dxf.name.startswith(prefix)
    ]
    if not layers:
        raise ValueError(f"Geen layers gevonden met prefix {prefix!r}")

    folder = dxf_out.parent
    scr_on = folder / "toggle_BGT_AAN.scr"
    scr_off = folder / "toggle_BGT_UIT.scr"

    def make_lines(turn: str) -> List[str]:
        lines: List[str] = []
        for ln in layers:
            lines += ["_.-LAYER", f"_{turn}", ln, ""]
        lines += ["_REGEN", ""]
        return lines

    scr_on.write_text("\n".join(make_lines("ON")), encoding="utf-8")
    scr_off.write_text("\n".join(make_lines("OFF")), encoding="utf-8")
    return scr_on, scr_off


def create_layer_filter_groups(doc) -> None:
    """Add AcLyLayerGroup filters to ACLYDICTIONARY (AutoCAD layer filter tree)."""
    bgt_layers = [lyr for lyr in doc.layers if lyr.dxf.name.startswith("BGT-")]
    onderlegger_layers = [lyr for lyr in doc.layers if lyr.dxf.name.startswith("01-")]

    if not bgt_layers and not onderlegger_layers:
        return

    try:
        # Get or create the ACLYDICTIONARY in the LAYER table extension dict
        lt = doc.tables.layers
        if lt.head.extension_dict is None:
            lt.head.new_extension_dict()
        xdict = lt.head.extension_dict

        if "ACLYDICTIONARY" not in xdict:
            acly = doc.objects.new_entity("DICTIONARY", {})
            xdict["ACLYDICTIONARY"] = acly
        else:
            acly = xdict["ACLYDICTIONARY"]

        if onderlegger_layers:
            _add_acly_group_filter(doc, acly, "01 ONDERLEGGER", onderlegger_layers)
        if bgt_layers:
            _add_acly_group_filter(doc, acly, "02 BGT", bgt_layers)
    except Exception as e:
        logger.warning("Layer filter groups konden niet worden aangemaakt: %s", e)


def _add_acly_group_filter(doc, acly_dict, name: str, layers) -> None:
    """Add an AcLyLayerGroup XRECORD to the ACLYDICTIONARY.

    Format matches AutoCAD's native layer group filters:
      code 1:   'AcLyLayerGroup'
      code 90:  1
      code 300: group name
      code 330: handle of each layer table record (repeated)
    """
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag

    xrec = doc.objects.new_entity("XRECORD", dxfattribs={"cloning": 1})

    group_tags = [
        DXFTag(1, "AcLyLayerGroup"),
        DXFTag(90, 1),
        DXFTag(300, name),
    ]
    for lyr in layers:
        group_tags.append(DXFTag(330, lyr.dxf.handle))

    xrec.tags = Tags(list(xrec.tags) + group_tags)
    acly_dict[name] = xrec
