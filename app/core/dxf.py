# -*- coding: utf-8 -*-
"""DXF helpers: layers, geometry, georeferenced images, toggle scripts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import ezdxf
from PIL import Image
from shapely.geometry import shape


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


def add_layer_filter_groups(doc) -> None:
    """Create layer filter groups: 'Onderlegger BGT' and 'Onderlegger kaarten'.

    Uses LAYER_FILTER DXF objects stored in the LAYER table's extension
    dictionary under 'ACAD_LAYERFILTERS'.
    """
    bgt_handles = []
    kaarten_handles = []

    for layer in doc.layers:
        name = layer.dxf.name
        handle = layer.dxf.handle
        if name.startswith("BGT-"):
            bgt_handles.append(handle)
        elif name.startswith("$$_") or name == "01_KAD_PERCELEN":
            kaarten_handles.append(handle)

    if not bgt_handles and not kaarten_handles:
        return

    lt_head = doc.tables.layers.head
    if not lt_head.has_extension_dict:
        ext_dict = lt_head.new_extension_dict()
    else:
        ext_dict = lt_head.get_extension_dict()

    filter_dict = ext_dict.add_dictionary("ACAD_LAYERFILTERS")

    if bgt_handles:
        bgt_filter = doc.objects.new_entity(
            "LAYER_FILTER", {"owner": filter_dict.dxf.handle}
        )
        bgt_filter.handles = bgt_handles
        filter_dict["Onderlegger BGT"] = bgt_filter

    if kaarten_handles:
        kaarten_filter = doc.objects.new_entity(
            "LAYER_FILTER", {"owner": filter_dict.dxf.handle}
        )
        kaarten_filter.handles = kaarten_handles
        filter_dict["Onderlegger kaarten"] = kaarten_filter


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
