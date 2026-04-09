# -*- coding: utf-8 -*-
"""DXF helpers: layers, geometry, georeferenced images, toggle scripts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import ezdxf
from PIL import Image
from shapely.geometry import shape

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


def create_doc(template_path: Optional[Path] = None):
    """Create a DXF document from a template or with default SNW layers."""
    if template_path and template_path.is_file():
        doc = ezdxf.readfile(str(template_path))
    else:
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
    """Add AutoCAD-compatible layer filter groups for BGT and kaarten layers."""
    bgt_layers = [
        lyr.dxf.name for lyr in doc.layers if lyr.dxf.name.startswith("BGT-")
    ]
    kaart_layers = [
        lyr.dxf.name for lyr in doc.layers if lyr.dxf.name.startswith("$$_")
    ]

    if not bgt_layers and not kaart_layers:
        return

    # Build AutoCAD LAYER_FILTER_MANAGER via .scr script approach is unreliable.
    # Use ezdxf group filter dictionaries instead.
    try:
        root_dict = doc.rootdict
        if "ACAD_LAYERFILTERS" not in root_dict:
            lf_dict = doc.objects.new_entity("DICTIONARY", {})
            root_dict["ACAD_LAYERFILTERS"] = lf_dict
        else:
            lf_dict = root_dict["ACAD_LAYERFILTERS"]

        # Create group filter entries for BGT
        if bgt_layers:
            _add_group_filter(doc, lf_dict, "Onderlegger BGT", bgt_layers)
        if kaart_layers:
            _add_group_filter(doc, lf_dict, "Onderlegger kaarten", kaart_layers)
    except Exception:
        # Filter groups are cosmetic — don't fail the export
        pass


def _add_group_filter(doc, lf_dict, name: str, layers: List[str]) -> None:
    """Add a single group filter to the ACAD_LAYERFILTERS dictionary."""
    xrec = doc.objects.new_entity(
        "XRECORD",
        dxfattribs={"cloning": 1},
    )
    # Group filter XRECORD format: pairs of (8, layer_name) tags
    tags = ezdxf.tags.Tags(
        [ezdxf.tags.DXFTag(8, ln) for ln in layers]
    )
    xrec.tags = ezdxf.tags.Tags(list(xrec.tags) + list(tags))
    lf_dict[name] = xrec
