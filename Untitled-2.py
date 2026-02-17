# %%
from __future__ import annotations

import os
import re
import requests
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Dict, List, Tuple, Optional, Any

import ezdxf
from PIL import Image, UnidentifiedImageError
from shapely.geometry import shape
from dotenv import load_dotenv
from openai import OpenAI

# %%
'''
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
'''

# %%
adres = "van haapsstraat 40, nijmegen"
grootte = 100

# %%
SUGGEST = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
LOOKUP  = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/lookup"
KADAS = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1"

RD_CRS = "http://www.opengis.net/def/crs/EPSG/0/28992"

# %% [markdown]
# Gebruikt API van de locatieserver om adres om te zetten naar RD coördinaten.

# %%
def adres_naar_rd(adres):
    # 1) Suggest: zoek beste match + id
    r = requests.get(SUGGEST, params={"q": adres}, timeout=30)
    r.raise_for_status()
    docs = r.json()["response"]["docs"]
    if not docs:
        raise ValueError("Geen resultaat voor dit adres.")
    loc_id = docs[0]["id"]

    # 2) Lookup: haal details van die id
    r = requests.get(LOOKUP, params={"id": loc_id}, timeout=30)
    r.raise_for_status()
    doc = r.json()["response"]["docs"][0]

    # 3) Haal RD-coördinaten op
    rd = doc["centroide_rd"]
    cleaned_rd = re.sub(r"[^0-9. ]", "", rd)
    x, y = map(float, cleaned_rd.split())
    

    return x, y

# %%
x, y = adres_naar_rd(adres)
meters = grootte #Grootte van de bbox in meters
bbox = (x-meters, y-meters, x+meters, y+meters)
bbox_klein = (x-0.1, y-0.1, x+0.1, y+0.1)

# %%
def perceel_informatie(bbox_klein):  
    bbox_klein_str = ",".join(map(str, bbox_klein))

    r = requests.get(f"{KADAS}/collections/perceel/items",params={"bbox": bbox_klein_str, "bbox-crs": RD_CRS},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    
    percelen = []
    for feature in data["features"]:
        props = feature["properties"]

        percelen.append({
            "perceelnummer": props.get("perceelnummer"),
            "sectie": props.get("sectie"),
            "oppervlakte_m2": props.get("kadastrale_grootte_waarde"),
            "definitief?": props.get("soort_grootte_waarde"),
            "status": props.get("status_historie_waarde")
        })
    return percelen

# %%
print(perceel_informatie(bbox_klein))

# %%
def bebouwing_informatie(bbox_klein):  
    bbox_klein_str = ",".join(map(str, bbox_klein))

    r = requests.get(f"{KADAS}/collections/bebouwing/items",params={"bbox": bbox_klein_str, "bbox-crs": RD_CRS},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    
    bebouwing = []
    for feature in data["features"]:
        props = feature["properties"]

        bebouwing.append({
            "relatieve hoogteligging": props.get("relatieve_hoogteligging")
        })
    return bebouwing

# %%
print(bebouwing_informatie(bbox_klein))

# %%
WMS_URL = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
PLU_WMS = "https://service.pdok.nl/kadaster/plu/wms/v1_0"
KAD_WMS = "https://service.pdok.nl/kadaster/kadastralekaart/wms/v5_0?"
GMK_WMS= "https://service.pdok.nl/bzk/bro-geomorfologischekaart/wms/v2_0?"

# %%
def wms_to_image(wms_url, params):
    r = requests.get(wms_url, params=params, timeout=60)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")



# %%
def wms_parameters(bbox, width=2000, height=2000):
    return {
        "service": "WMS",
        "request": "GetMap",
        "version": "1.3.0",
        "crs": "EPSG:28992",
        "bbox": ",".join(map(str, bbox)),
        "width": str(width),
        "height": str(height),
        "format": "image/png",
        "transparent": "true",
    }

# %%
params = wms_parameters(bbox)

# 1) luchtfoto
luchtfoto = wms_to_image(WMS_URL, {
    **params,
    "layers": "Actueel_orthoHR",
    "styles": "",
})

# 2) bestemmingsplan
bestemming = wms_to_image(PLU_WMS, {
    **params,
    "layers": "enkelbestemming",
    "styles": "enkelbestemming",
})

# 3) perceelgrenzen
percelen = wms_to_image(KAD_WMS, {
    **params,
    "layers": "kadastralekaart",
    "styles": "",
})

geo = wms_to_image(GMK_WMS, {
    **wms_parameters(bbox, width=2000, height=2000),
    "layers": "geomorphological_area",
    "styles": "",           # of "default" als je liever expliciet bent
})



# %%
def wms_legend_from_capabilities(wms_url: str, layer: str) -> Image.Image:
    cap = requests.get(
        wms_url,
        params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"},
        timeout=60
    )
    cap.raise_for_status()
    root = ET.fromstring(cap.text)

    for lyr in root.iter():
        if not lyr.tag.endswith("Layer"):
            continue

        name_el = lyr.find("./{*}Name")
        if name_el is None or (name_el.text or "").strip() != layer:
            continue

        online = lyr.find(".//{*}Style/{*}LegendURL/{*}OnlineResource")
        if online is None:
            raise ValueError(f"Geen LegendURL gevonden voor layer='{layer}'")

        href = None
        for k, v in online.attrib.items():
            if k.endswith("href"):  # xlink:href
                href = v
                break
        if not href:
            raise ValueError("LegendURL OnlineResource heeft geen href attribuut")

        r = requests.get(href, timeout=60)
        r.raise_for_status()
        return Image.open(BytesIO(r.content))

    raise ValueError(f"Layer '{layer}' niet gevonden in WMS GetCapabilities")

# %%
def place_legend_on_image(
    base: Image.Image,
    legend: Image.Image,
    position: str = "bottom-right",
    margin: int = 30,
    legend_scale: float = 1.5,          # maak legenda eerst groter
    legend_max_width_ratio: float = 0.25,  # max 25% van kaartbreedte
    add_white_box: bool = True,
    box_padding: int = 14,
):
    base = base.convert("RGBA")
    legend = legend.convert("RGBA")

    # 1) eerst "grof" schalen zodat hij niet mini is
    legend = legend.resize(
        (int(legend.size[0] * legend_scale), int(legend.size[1] * legend_scale)),
        Image.Resampling.LANCZOS
    )

    # 2) daarna cap op max breedte t.o.v. kaart
    max_w = int(base.size[0] * legend_max_width_ratio)
    if legend.size[0] > max_w:
        scale = max_w / legend.size[0]
        legend = legend.resize(
            (max_w, int(legend.size[1] * scale)),
            Image.Resampling.LANCZOS
        )

    # 3) optioneel wit vlak erachter
    if add_white_box:
        box_w = legend.size[0] + 2 * box_padding
        box_h = legend.size[1] + 2 * box_padding
        box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 220))
        box.paste(legend, (box_padding, box_padding), legend)
        legend = box

    # 4) positie bepalen
    W, H = base.size
    w, h = legend.size
    if position == "bottom-right":
        x, y = W - w - margin, H - h - margin
    elif position == "bottom-left":
        x, y = margin, H - h - margin
    elif position == "top-right":
        x, y = W - w - margin, margin
    elif position == "top-left":
        x, y = margin, margin
    else:
        raise ValueError("position must be one of: bottom-right, bottom-left, top-right, top-left")

    out = base.copy()
    out.paste(legend, (x, y), legend)
    return out




# %%
def wms_legend_to_image(wms_url: str, layer: str, style: str = "", version: str = "1.3.0") -> Image.Image:
    params = {
        "service": "WMS",
        "request": "GetLegendGraphic",
        "version": version,
        "format": "image/png",
        "layer": layer,
    }
    if style:
        params["style"] = style

    r = requests.get(wms_url, params=params, timeout=60)
    r.raise_for_status()
    return Image.open(BytesIO(r.content))

# %%
legend_url = "https://service.pdok.nl/kadaster/plu/wms/v1_0/legend/enkelbestemming/enkelbestemming.png"
legend_img = Image.open(BytesIO(requests.get(legend_url, timeout=30).content)).convert("RGBA")

# %%
plu_plus_percelen = Image.alpha_composite(bestemming, percelen)

# legenda erop
bestemming_kadaster = place_legend_on_image(
    base=plu_plus_percelen,
    legend=legend_img,
    position="bottom-right",
    legend_scale=2.0,           
    legend_max_width_ratio=0.2
)

# alleen eindproduct opslaan
luchtfoto.save("Luchtfoto.png")
bestemming_kadaster.save("Bestemming_percelen.png")
luchtfoto_plus_percelen = Image.alpha_composite(luchtfoto, percelen)
luchtfoto_plus_percelen.save("luchtfoto_kadaster.png")
geo = wms_to_image(GMK_WMS, {
    **wms_parameters(bbox, width=2000, height=2000),
    "layers": "geomorphological_area",
    "styles": "",
})

try:
    legend = wms_legend_from_capabilities(GMK_WMS, "geomorphological_area")
    geo = place_legend_on_image(geo, legend, position="bottom-right")
except Exception as e:
    print(f"[WARN] Geomorfologische legenda ophalen mislukt: {e}")

geo.save("Geomorfologische_kaart.png")

# %%


# %% [markdown]
# CAD ONDERLEGGER

# %%
AHN_WMS = "https://service.pdok.nl/rws/ahn/wms/v1_0"

# %%
def download_ahn_png(
    bbox_rd,
    out_path="ahn_kaart.png",
    width=2000,
    height=2000,
    add_legend=True,
    product="dtm",          # "dtm" of "dsm"
    style="default",        # meestal alleen "default"
):
    """
    Download AHN WMS kaart als PNG voor DTM of DSM.

    product:
      - "dtm" -> layer dtm_05m
      - "dsm" -> layer dsm_05m
    """

    params = wms_parameters(bbox_rd, width=width, height=height)

    product = product.lower().strip()
    layer_map = {
        "dtm": "dtm_05m",
        "dsm": "dsm_05m",
    }

    if product not in layer_map:
        raise ValueError(f"Onbekend product '{product}'. Kies uit: {list(layer_map.keys())}")

    layer = layer_map[product]

    # --- WMS image ophalen ---
    ahn_img = wms_to_image(AHN_WMS, {
        **params,
        "layers": layer,
        "styles": style,
    })

    # --- Optioneel: legenda ophalen en plakken ---
    if add_legend:
        try:
            legend_url = (
                "https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wms/v1_0"
                "?language=dut&version=1.3.0&service=WMS&request=GetLegendGraphic"
                f"&sld_version=1.1.0&layer={layer}&format=image/png&STYLE={style}"
            )

            r = requests.get(legend_url, timeout=60)
            r.raise_for_status()
            legend = Image.open(BytesIO(r.content))

            ahn_img = place_legend_on_image(ahn_img, legend, position="bottom-right")

        except Exception as e:
            print(f"[WARN] AHN legenda ophalen mislukt ({product.upper()}): {e}")

    ahn_img.save(out_path)
    return os.path.abspath(out_path)

# %%
WDM_WMS = "https://service.pdok.nl/bzk/bro-grondwaterspiegeldiepte/wms/v2_0"

# %%
def wdm_legend_image(layer: str) -> Image.Image:
    """
    Haalt de legenda PNG op via LegendURL uit WDM GetCapabilities.
    Returnt een PIL.Image zodat jouw place_legend_on_image() direct werkt.
    """
    cap = requests.get(
        WDM_WMS,
        params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"},
        timeout=60
    )
    cap.raise_for_status()
    root = ET.fromstring(cap.text)

    # zoek Layer <Name> == layer
    for lyr in root.iter():
        if not lyr.tag.endswith("Layer"):
            continue
        name_el = lyr.find("./{*}Name")
        if name_el is None or (name_el.text or "").strip() != layer:
            continue

        online = lyr.find(".//{*}Style/{*}LegendURL/{*}OnlineResource")
        if online is None:
            raise ValueError(f"Geen LegendURL gevonden voor layer='{layer}'")

        href = None
        for k, v in online.attrib.items():
            if k.endswith("href"):   # xlink:href
                href = v
                break
        if not href:
            raise ValueError("LegendURL OnlineResource heeft geen href attribuut")

        r = requests.get(href, timeout=60)
        r.raise_for_status()
        return Image.open(BytesIO(r.content))

    raise ValueError(f"Layer '{layer}' niet gevonden in WDM GetCapabilities")


# %%
def download_wdm_png(
    bbox_rd,
    layer=None,
    *,
    layer_name=None,
    out_path="wdm_kaart.png",
    width=2000,
    height=2000,
    add_legend=True
):
    # accepteer zowel layer als layer_name
    if layer is None:
        layer = layer_name
    if not layer:
        raise ValueError("Geef 'layer' of 'layer_name' mee (bijv. bro-grondwaterspiegeldieptemetingen-GHG)")

    params = wms_parameters(bbox_rd, width=width, height=height)
    style = ""

    wdm_img = wms_to_image(WDM_WMS, {
        **params,
        "layers": layer,
        "styles": style,
    })

    if add_legend:
        try:
            legend = wdm_legend_image(layer)  # via LegendURL (GetCapabilities)
            wdm_img = place_legend_on_image(wdm_img, legend, position="bottom-right")
        except Exception as e:
            print(f"[WARN] WDM legenda ophalen mislukt: {e}")

    wdm_img.save(out_path)
    return os.path.abspath(out_path)


# %%
PDOK_3D_BASE = "https://api.pdok.nl/kadaster/3d-basisvoorziening/ogc/v1"
EPSG_RD = "EPSG:28992"

# %%
RD_CRS = "http://www.opengis.net/def/crs/EPSG/0/28992"

def _bbox_to_str(bbox: Tuple[float, float, float, float]) -> str:
    return ",".join(map(str, bbox))

def _ogc_get_all_features(
    base_url: str,
    collection: str,
    bbox: Tuple[float, float, float, float],
    bbox_crs: str = RD_CRS,
    limit: int = 1000,
    extra_params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    response_crs: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Haalt ALLE features op voor een collection binnen bbox, incl. pagination (rel=next).
    Werkt met OGC API Features /collections/{collection}/items.

    Belangrijk: veel PDOK OGC API's geven geometrie standaard terug in EPSG:4326.
    Voor DXF wil je RD-coördinaten (EPSG:28992). Daarom proberen we standaard
    `crs=<bbox_crs>` mee te geven (of `response_crs`), en vallen we terug als de server
    die parameter niet accepteert.
    """
    url = f"{base_url.rstrip('/')}/collections/{collection}/items"

    crs_out = response_crs or bbox_crs
    params = {
        "bbox": _bbox_to_str(bbox),
        "bbox-crs": bbox_crs,
        "limit": str(limit),
        "f": "json",
    }

    # Vraag output CRS aan (als ondersteund)
    if crs_out:
        params["crs"] = crs_out

    if extra_params:
        params.update(extra_params)

    features: List[Dict[str, Any]] = []

    while True:
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
        except requests.HTTPError as e:
            # Fallback: sommige endpoints accepteren `crs` niet.
            if params and "crs" in params:
                params = dict(params)
                params.pop("crs", None)
                r = requests.get(url, params=params, timeout=timeout)
                r.raise_for_status()
            else:
                raise e

        data = r.json()
        feats = data.get("features", [])
        features.extend(feats)

        # Pagination: zoek rel="next"
        next_url = None
        for link in data.get("links", []):
            if link.get("rel") == "next" and link.get("href"):
                next_url = link["href"]
                break

        if not next_url:
            break

        # Volgende page: OGC geeft meestal een volledige URL terug.
        url = next_url
        params = None  # next-link bevat vaak al alle params

    return features

def _ensure_layer(doc, name: str, color: int = 7) -> None:
    if name not in doc.layers:
        doc.layers.new(name=name, dxfattribs={"color": color})

def _add_polygon_like_to_dxf(
    msp,
    geom,
    layer: str,
) -> None:
    """
    Voegt Polygon/MultiPolygon toe als LWPolyline(s) op layer.
    """
    if geom.is_empty:
        return

    gtype = geom.geom_type

    def add_polygon(poly):
        # exterior
        ext = list(poly.exterior.coords)
        if len(ext) >= 3:
            msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": layer})
        # interiors (holes)
        for ring in poly.interiors:
            coords = list(ring.coords)
            if len(coords) >= 3:
                msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": layer})

    if gtype == "Polygon":
        add_polygon(geom)
    elif gtype == "MultiPolygon":
        for poly in geom.geoms:
            add_polygon(poly)
    else:
        # Je kunt dit uitbreiden voor LineString/Point indien nodig
        return

# %%
def add_georef_image_to_doc(
    doc: ezdxf.EzDxf,
    image_path: str,
    bbox_rd,
    layer: str,
    fade: int = 0,
    contrast: int = 50,
    brightness: int = 50,
):
    """
    Compatibele versie voor oudere ezdxf:
    IMAGEDEF + IMAGE + IMAGEDEF_REACTOR zodat AutoCAD het als 'Referenced' ziet.
    """
    minx, miny, maxx, maxy = bbox_rd
    width_units = float(maxx - minx)
    height_units = float(maxy - miny)

    # Zorg dat pad stabiel is: bij voorkeur relatief naast DXF
    # (laat hem zoals jij hem aanroept; vaak gewoon "wdm_ghg.png")
    image_path = image_path.replace("\\", "/")

    # Pixel size bepalen (AutoCAD gebruikt dit in IMAGEDEF)
    with Image.open(image_path) as im:
        w_px, h_px = im.size

    # 1) IMAGEDEF aanmaken
    img_def = doc.add_image_def(
        filename=image_path,
        size_in_pixel=(w_px, h_px),
    )

    # 2) IMAGE entity plaatsen
    image_entity = doc.modelspace().add_image(
        img_def,
        insert=(minx, miny),
        size_in_units=(width_units, height_units),
        rotation=0,
        dxfattribs={"layer": layer},
    )

    # 3) Display properties (optioneel; AutoCAD-spec)
    try:
        image_entity.dxf.fade = int(fade)
        image_entity.dxf.contrast = int(contrast)
        image_entity.dxf.brightness = int(brightness)
    except Exception:
        pass

    # 4) Cruciaal: IMAGEDEF_REACTOR toevoegen zodat AutoCAD de ref als 'referenced' ziet
    try:
        doc.objects.add_image_def_reactor(img_def.dxf.handle, image_entity.dxf.handle)
    except Exception:
        # Sommige ezdxf versies doen dit automatisch; dan is het oké.
        pass

    return image_entity

# %%
def export_cad_onderlegger_dxf(
    out_path: str,
    bbox_rd: Tuple[float, float, float, float],
    kadas_base_url: str = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1",
    include_percelen: bool = True,
    include_bebouwing: bool = True,
) -> str:
    """
    Maakt een DXF-onderlegger (RD / EPSG:28992)
    met standaard rasters ONDER de vectorlagen.

    Deze versie gebruikt de NIEUWE add_georef_image_to_doc(doc, image_path, bbox_rd, layer)
    die intern msp.add_image(filename=...) gebruikt (stabiel in AutoCAD).
    -> Daarom: GEEN IMAGEDEF pad-rewrite meer nodig.
    """
    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()

    def _ensure_raster_layer(name: str, default_on: bool) -> None:
        """Helper: layer maken + standaard status instellen (zonder lock, zodat ON/OFF werkt)."""
        if name not in doc.layers:
            doc.layers.new(name=name)
        lyr = doc.layers.get(name)
        lyr.dxf.plot = 1
        try:
            lyr.thaw()
            if default_on:
                lyr.on()
            else:
                lyr.off()
            # Niet locken: dat geeft gedoe met ON/OFF en is onnodig voor onderleggers.
            # lyr.lock()
        except Exception:
            pass

    # ===============================
    # RASTERS (onderaan)
    # ===============================

    # --- LUCHTFOTO Kadastrale Kaart (standaard UIT) ---
    L_LF_KAD = "$$_00-00-00_onderlegger_Luchtfoto met kadastrale kaart V5"
    _ensure_raster_layer(L_LF_KAD, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="luchtfoto_kadaster.png",
        bbox_rd=bbox_rd,
        layer=L_LF_KAD,
    )

    # --- LUCHTFOTO (actueel) (standaard AAN) ---
    # Let op: hier moet je natuurlijk het juiste bestand gebruiken (niet opnieuw luchtfoto_kadaster.png)
    L_LF_ACT = "$$_00-00-00_onderlegger_Luchtfoto (actueel)"
    _ensure_raster_layer(L_LF_ACT, default_on=True)
    add_georef_image_to_doc(
        doc=doc,
        image_path="Luchtfoto.png",  # <- pas aan naar jouw echte bestandsnaam (bij jou staat 'Luchtfoto.png')
        bbox_rd=bbox_rd,
        layer=L_LF_ACT,
    )

    # --- Grondwater GHG (standaard UIT) ---
    L_WDM_GHG = "$$_00-00-00_onderlegger_Grondwaterstand (GHG)"
    _ensure_raster_layer(L_WDM_GHG, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="wdm_ghg.png",
        bbox_rd=bbox_rd,
        layer=L_WDM_GHG,
    )

    # --- Grondwater GLG (standaard UIT) ---
    L_WDM_GLG = "$$_00-00-00_onderlegger_Grondwaterstand (GLG)"
    _ensure_raster_layer(L_WDM_GLG, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="wdm_glg.png",
        bbox_rd=bbox_rd,
        layer=L_WDM_GLG,
    )

    # --- Grondwater GT (standaard UIT) ---
    L_WDM_GT = "$$_00-00-00_onderlegger_Grondwaterstand (GT)"
    _ensure_raster_layer(L_WDM_GT, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="wdm_gt.png",
        bbox_rd=bbox_rd,
        layer=L_WDM_GT,
    )

    # --- Geomorfologische kaart (standaard UIT) ---
    L_GEO = "$$_00-00-00_onderlegger_Geomorfologische kaart"
    _ensure_raster_layer(L_GEO, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="Geomorfologische_kaart.png",
        bbox_rd=bbox_rd,
        layer=L_GEO,
    )

    # --- Bestemmingsplankaart (standaard UIT) ---
    L_BEST = "00_BESTEMMING"
    _ensure_raster_layer(L_BEST, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="Bestemming_percelen.png",  # <- bij jou file heet 'Bestemming_percelen.png'
        bbox_rd=bbox_rd,
        layer=L_BEST,
    )

    # --- AHN DSM (standaard UIT) ---
    L_AHN_DSM = "$$_00_00_00_onderlegger_Hoogtekaart (AHN 4 - DSM)"
    _ensure_raster_layer(L_AHN_DSM, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="ahn_dsm.png",
        bbox_rd=bbox_rd,
        layer=L_AHN_DSM,
    )

    # --- AHN DTM (standaard UIT) ---
    L_AHN_DTM = "$$_00_00_00_onderlegger_Hoogtekaart (AHN 4 - DTM)"
    _ensure_raster_layer(L_AHN_DTM, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="ahn_dtm.png",
        bbox_rd=bbox_rd,
        layer=L_AHN_DTM,
    )

    # ===============================
    # VECTOR-LAYERS
    # ===============================

    if include_percelen:
        _ensure_layer(doc, "01_KAD_PERCELEN", color=3)
    if include_bebouwing:
        _ensure_layer(doc, "02_KAD_BEBouwing", color=1)

    # ===============================
    # VECTOR DATA (komt bovenop rasters)
    # ===============================

    if include_percelen:
        feats = _ogc_get_all_features(
            base_url=kadas_base_url,
            collection="perceel",
            bbox=bbox_rd,
            bbox_crs=RD_CRS,
            limit=1000,
        )
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            _add_polygon_like_to_dxf(msp, geom, layer="01_KAD_PERCELEN")

    if include_bebouwing:
        feats = _ogc_get_all_features(
            base_url=kadas_base_url,
            collection="bebouwing",
            bbox=bbox_rd,
            bbox_crs=RD_CRS,
            limit=1000,
        )
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            _add_polygon_like_to_dxf(msp, geom, layer="02_KAD_BEBouwing")

    # Geen IMAGEDEF-relpath rewrite meer: nieuwe add_georef_image_to_doc schrijft direct bruikbare filenames.
    doc.saveas(out_path)
    return out_path

# %%
# 1) AHN PNG opslaan (zorg dat hij naast de DXF komt te staan)
ahn_png_dsm = download_ahn_png(bbox, out_path="ahn_dsm.png", product="dsm")
ahn_png_dtm = download_ahn_png(bbox, out_path="ahn_dtm.png", product="dtm")
wdm_png_GHG = download_wdm_png(
    bbox,
    layer_name="bro-grondwaterspiegeldieptemetingen-GHG",
    out_path="wdm_ghg.png",
    width=2000,
    height=2000
)
wdm_png_GLG = download_wdm_png(
    bbox,
    layer_name="bro-grondwaterspiegeldieptemetingen-GLG",
    out_path="wdm_glg.png",
    width=2000,
    height=2000
)
wdm_png_gt = download_wdm_png(
    bbox,
    layer_name="bro-grondwaterspiegeldieptemetingen-GT",
    out_path="wdm_gt.png",
    width=2000,
    height=2000
)





dxf_path = export_cad_onderlegger_dxf(
    out_path="onderlegger_snw_met_rasters.dxf",
    bbox_rd=bbox,
    include_percelen=True,
    include_bebouwing=True
)

# %% [markdown]
# Tekst

# %%
'''
response = client.responses.create(
    model="gpt-4.1-mini",
    input="Hallo!"
)
'''
#print(response.output_text)


