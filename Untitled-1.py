# %%
from __future__ import annotations

import os
import re
import requests
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Dict, List, Tuple, Optional, Any

import ezdxf
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape
from dotenv import load_dotenv
from openai import OpenAI
import base64
from collections import Counter
import math

# %%
"""
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
"""

# %%
adres = "Rust en Vreugdlaan 2, 2243 AS Wassenaar"

grootte = 250

# %%
SUGGEST = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
LOOKUP  = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/lookup"
KADAS = "https://api.pdok.nl/kadaster/brk-kadastrale-kaart/ogc/v1"
BGT_BASE = "https://api.pdok.nl/lv/bgt/ogc/v1"
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
BODEM_WMS = "https://service.pdok.nl/bzk/bro-bodemkaart/wms/v1_0"
TOPO_WMS = "https://service.pdok.nl/brt/topraster/wms/v1_0"

# %%
def wms_to_image(wms_url, params):
    r = requests.get(wms_url, params=params, timeout=60)
    r.raise_for_status()

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image" not in ctype:
        # dit is bijna altijd een ServiceException XML/HTML
        print("---- WMS returned non-image ----")
        print("URL:", r.url)
        print("Content-Type:", ctype)
        print("First 600 chars:\n", r.text[:600])
        raise ValueError("WMS response is not an image (see debug output above).")

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
def gmk_getfeatureinfo(GMK_WMS, bbox, width, height, i, j, layer="geomorphological_area"):
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:28992",
        "BBOX": ",".join(map(str, bbox)),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "STYLES": "",
        "FORMAT": "image/png",
        "INFO_FORMAT": "application/json",
        "I": str(int(i)),
        "J": str(int(j)),
        "FEATURE_COUNT": "1",
    }
    r = requests.get(GMK_WMS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _pick_gmk_code_and_label(props: dict) -> tuple[str | None, str | None]:
    code = props.get("landform_subgroup_code")
    label = props.get("landform_subgroup_description")
    if code and label:
        return str(code), str(label)
    if code:
        return str(code), str(code)
    return None, None

def collect_gmk_items_in_bbox(
    GMK_WMS,
    bbox,
    grid=15,
    probe_size=800,
    layer="geomorphological_area",
):
    width = height = probe_size
    xs = [int((k + 0.5) * width / grid) for k in range(grid)]
    ys = [int((k + 0.5) * height / grid) for k in range(grid)]

    counts = Counter()          # code -> hits
    labels = {}                 # code -> description

    for i in xs:
        for j in ys:
            data = gmk_getfeatureinfo(GMK_WMS, bbox, width, height, i, j, layer=layer)
            feats = data.get("features", []) or []
            if not feats:
                continue
            props = feats[0].get("properties") or {}

            code, label = _pick_gmk_code_and_label(props)
            if not code:
                continue

            counts[code] += 1
            # eerste label bewaren
            labels.setdefault(code, label or code)

    return counts, labels

def build_compact_gmk_legend(
    items: list[tuple[str, str, int]],   # (code, label, hits)
    title="Geomorfologie (aanwezig in bbox)",
    max_items=12
) -> Image.Image:
    try:
        font = ImageFont.truetype("arial.ttf", 22)
        font_b = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
        font_b = ImageFont.load_default()

    items = items[:max_items]

    pad = 18
    W = 950
    line_h = 30

    # simpele hoogteberekening
    H = pad*2 + 55 + len(items)*line_h + 30
    img = Image.new("RGBA", (W, H), (255, 255, 255, 235))
    d = ImageDraw.Draw(img)

    d.text((pad, pad), title, font=font_b, fill=(0, 0, 0, 255))

    y = pad + 45
    for code, label, n in items:
        # afkappen zodat het niet uit beeld loopt
        text = f"{code} — {label}"
        if len(text) > 80:
            text = text[:77] + "…"
        d.text((pad, y), text, font=font, fill=(0, 0, 0, 255))
        d.text((W - pad - 120, y), f"~{n}", font=font, fill=(80, 80, 80, 255))
        y += line_h

    d.text((pad, H - pad - 20), "Alleen aanwezige eenheden getoond (sampling).", font=font, fill=(60, 60, 60, 255))
    return img


# %%
def get_topo_layer_and_bbox(
    bbox,
    target_px=2000,
    pixel_size_m=0.00028,  # OGC default pixel size
):
    """
    Kiest TOPraster layer en dwingt bbox af zodat scale binnen de layer-range valt.
    - Als je te ver ingezoomd bent: bbox wordt vergroot.
    - Als je te ver uitgezoomd bent: bbox wordt verkleind.
    Retourneert: (layer_name, bbox_rd)
    """

    # Min/Max scale denominators uit GetCapabilities (zoals jij ze zag)
    # top25:  4000 ..  50000
    # top50: 12000 .. 100000
    # top100:24000 .. 200000
    # top250:60000 .. 500000
    LAYERS = [
        ("top25raster",  4000,   50000),
        ("top50raster",  12000, 100000),
        ("top100raster", 24000, 200000),
        ("top250raster", 60000, 500000),
    ]

    minx, miny, maxx, maxy = bbox
    cx = (minx + maxx) / 2
    cy = (miny + maxy) / 2

    span = max(maxx - minx, maxy - miny)  # meters
    m_per_px = span / float(target_px)
    scale = m_per_px / float(pixel_size_m)

    # 1) Kies eerst een layer puur op basis van je huidige schaal
    #    (als je ver ingezoomd bent, is top25 nog steeds "de juiste", maar bbox moet omhoog)
    if scale <= 50000:
        layer, smin, smax = LAYERS[0]
    elif scale <= 100000:
        layer, smin, smax = LAYERS[1]
    elif scale <= 200000:
        layer, smin, smax = LAYERS[2]
    else:
        layer, smin, smax = LAYERS[3]

    # 2) Dwing schaal binnen [smin, smax] door span aan te passen
    #    scale = (span/target_px) / pixel_size_m  => span = scale * pixel_size_m * target_px
    min_span = smin * pixel_size_m * target_px
    max_span = smax * pixel_size_m * target_px

    # afdwingen: clamp span naar [min_span, max_span]
    forced_span = max(min_span, min(max_span, span))
    half = forced_span / 2

    bbox_forced = (cx - half, cy - half, cx + half, cy + half)
    return layer, bbox_forced


# %%
params = wms_parameters(bbox)
bbox_groot = x-3000, y-3000, x+3000, y+3000
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

# 3)
Dubbelbestemming = wms_to_image(PLU_WMS, {
    **params,
    "layers": "dubbelbestemming",
    "styles": "dubbelbestemming",
})

# 4) perceelgrenzen
percelen = wms_to_image(KAD_WMS, {
    **params,
    "layers": "kadastralekaart",
    "styles": "",
})

geo = wms_to_image(GMK_WMS, {
    **params,
    "layers": "geomorphological_area",
    "styles": "",           # of "default" als je liever expliciet bent
})
# 4) luchtfoto gebied
luchtfoto_groot = wms_to_image(WMS_URL, {
    **wms_parameters(bbox_groot),
    "layers": "Actueel_orthoHR",
    "styles": "",
})

topo_kaart = wms_to_image(TOPO_WMS, {
    **params,
    "layers": "top25raster",
    "styles": "",
    "transparent": "false"
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

bestemming_kadaster = place_legend_on_image(
    base=plu_plus_percelen,
    legend=legend_img,
    position="bottom-right",
    legend_scale=2.0,           
    legend_max_width_ratio=0.2
)
bestemmingdubbel = place_legend_on_image(
    base=Dubbelbestemming,
    legend=legend_img,
    position="bottom-right",
    legend_scale=2.0,           
    legend_max_width_ratio=0.2
)
layer, bbox_topo = get_topo_layer_and_bbox(bbox)


topo_kaart = wms_to_image(TOPO_WMS, {
    **wms_parameters(bbox_topo, width=2000, height=2000),
    "layers": layer,
    "styles": "",
    "transparent": "false"
})
topo_kaart.save("topo_kaart.png")
bestemmingdubbel.save("Bestemming_dubbel.png")
luchtfoto.save("Luchtfoto.png")
bestemming_kadaster.save("Bestemming_percelen.png")

luchtfoto_plus_percelen = Image.alpha_composite(luchtfoto, percelen)
luchtfoto_plus_percelen.save("luchtfoto_kadaster.png")
# GMK kaart ophalen
geo = wms_to_image(GMK_WMS, {
    **wms_parameters(bbox, width=2000, height=2000),
    "layers": "geomorphological_area",
    "styles": "",
})


counts, labels = collect_gmk_items_in_bbox(
    GMK_WMS,
    bbox=bbox,
    grid=15,        # 15 = snel; 25 = netter
    probe_size=800,
    layer="geomorphological_area",
)


geo = place_legend_on_image(
    base=geo,
    legend=compact_legend,
    position="bottom-right",
    legend_scale=1.0,
    legend_max_width_ratio=0.45,
    add_white_box=False,  # we hebben al wit vlak
)


geo.save("Geomorfologische_kaart.png")

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
def download_bodemkundig_belang_png(
    bbox_rd,
    out_path="bodemkundig_belang.png",
    width=2000,
    height=2000,
    legend_position="bottom-right",
):
    params = wms_parameters(bbox_rd, width=width, height=height)

    img = wms_to_image(
        BODEM_WMS,
        {
            **params,
            "layers": "areaofpedologicalinterest",
            "styles": "",
            "format": "image/png",
            "transparent": "true",
        },
    )

    try:
        legend = wms_legend_to_image(BODEM_WMS, "areaofpedologicalinterest")
        img = place_legend_on_image(img, legend, position=legend_position)
    except Exception as e:
        print(f"[WARN] Legenda bodemkundig belang ophalen mislukt: {e}")

    img.save(out_path)
    return os.path.abspath(out_path)



# %%
def download_bodemvlakken_png(
    bbox_rd,
    out_path="bodemvlakken.png",
    width=2000,
    height=2000,
    legend_position="bottom-right",
):
    params = wms_parameters(bbox_rd, width=width, height=height)

    img = wms_to_image(
        BODEM_WMS,
        {
            **params,
            "layers": "soilarea",
            "styles": "",
            "format": "image/png",
            "transparent": "true",
        },
    )

    # legenda ophalen + in kaart plakken (zelfde als bij je andere lagen)
    try:
        legend = wms_legend_to_image(BODEM_WMS, "soilarea")
        img = place_legend_on_image(img, legend, position=legend_position)
    except Exception as e:
        print(f"[WARN] Legenda bodemvlakken (soilarea) ophalen mislukt: {e}")

    img.save(out_path)
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

def _ensure_layer_off(doc, name: str, color: int = 7) -> None:
    _ensure_layer(doc, name, color=color)
    try:
        doc.layers.get(name).off()
        doc.layers.get(name).thaw()
    except Exception:
        pass

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
    
def _add_any_geom_to_dxf(msp, geom, layer: str) -> None:
    """
    Voeg shapely-geometry toe aan DXF:
    - Point / MultiPoint -> POINT
    - LineString / MultiLineString -> LWPOLYLINE
    - Polygon / MultiPolygon -> LWPOLYLINE (closed)
    - GeometryCollection -> recursief
    """
    if geom is None or geom.is_empty:
        return

    gtype = geom.geom_type

    # --- Points ---
    if gtype == "Point":
        msp.add_point((float(geom.x), float(geom.y)), dxfattribs={"layer": layer})
        return
    if gtype == "MultiPoint":
        for p in geom.geoms:
            msp.add_point((float(p.x), float(p.y)), dxfattribs={"layer": layer})
        return

    # --- Lines ---
    if gtype == "LineString":
        coords = [(float(x), float(y)) for x, y in geom.coords]
        if len(coords) >= 2:
            msp.add_lwpolyline(coords, dxfattribs={"layer": layer})
        return
    if gtype == "MultiLineString":
        for ls in geom.geoms:
            _add_any_geom_to_dxf(msp, ls, layer)
        return

    # --- Polygons ---
    if gtype == "Polygon":
        ring = [(float(x), float(y)) for x, y in geom.exterior.coords]
        if len(ring) >= 3:
            msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
        # (optioneel) holes tekenen:
        # for interior in geom.interiors:
        #     hole = [(float(x), float(y)) for x, y in interior.coords]
        #     if len(hole) >= 3:
        #         msp.add_lwpolyline(hole, close=True, dxfattribs={"layer": layer})
        return
    if gtype == "MultiPolygon":
        for poly in geom.geoms:
            _add_any_geom_to_dxf(msp, poly, layer)
        return

    # --- Collections ---
    if gtype == "GeometryCollection":
        for gg in geom.geoms:
            _add_any_geom_to_dxf(msp, gg, layer)
        return

    # fallback: negeer onbekende types
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
def _safe_layer_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")
    return f"BGT-{s}"[:255]

def _bgt_list_collections() -> List[str]:
    url = f"{BGT_BASE.rstrip('/')}/collections"
    r = requests.get(url, params={"f": "json"}, timeout=30)
    r.raise_for_status()
    cols = r.json().get("collections", [])
    return [c["id"] for c in cols if "id" in c]

def add_all_bgt_to_dxf(doc, msp, bbox_rd: Tuple[float, float, float, float], limit_per_collection: int = 2000):
    """
    Voeg alle BGT collections toe als vectorlayers binnen bbox.
    Let op: kan groot worden. limit_per_collection begrenst per laag.
    """
    col_ids = _bgt_list_collections()
    print(f"[BGT] collections: {len(col_ids)}")

    colors = [1, 2, 3, 4, 5, 6, 7]
    for idx, cid in enumerate(col_ids):
        layer = _safe_layer_name(cid)
        _ensure_layer_off(doc, layer, color=colors[idx % len(colors)])

        feats = _ogc_get_all_features(
            base_url=BGT_BASE,
            collection=cid,
            bbox=bbox_rd,
            bbox_crs=RD_CRS,
            limit=limit_per_collection,
        )

        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            _add_any_geom_to_dxf(msp, geom, layer=layer)

# %%
def write_bgt_toggle_scripts(doc, dxf_out_path: str, prefix: str = "BGT-") -> tuple[str, str]:
    """
    Schrijft 2 AutoCAD .scr scripts naast de DXF:
    - toggle_BGT_AAN.scr: zet alle layers met prefix aan
    - toggle_BGT_UIT.scr: zet alle layers met prefix uit

    Gebruik in AutoCAD: command 'SCRIPT' en kies het .scr bestand.
    """
    bgt_layers = [layer.dxf.name for layer in doc.layers if layer.dxf.name.startswith(prefix)]
    if not bgt_layers:
        raise ValueError(f"Geen layers gevonden met prefix '{prefix}'")

    folder = os.path.dirname(dxf_out_path)
    scr_on  = os.path.join(folder, "toggle_BGT_AAN.scr")
    scr_off = os.path.join(folder, "toggle_BGT_UIT.scr")

    # AutoCAD -LAYER accepteert vaak een kommagescheiden lijst.
    # Om limieten te vermijden, zetten we per laag aan/uit (langzamer maar extreem robuust).
    def make_lines(turn: str) -> list[str]:
        lines = []
        for ln in bgt_layers:
            lines += [
                "_.-LAYER",
                f"_{turn}",   # _ON of _OFF (underscore = werkt ook in NL UI)
                ln,
                "",
            ]
        lines += ["_REGEN", ""]
        return lines

    with open(scr_on, "w", encoding="utf-8") as f:
        f.write("\n".join(make_lines("ON")))

    with open(scr_off, "w", encoding="utf-8") as f:
        f.write("\n".join(make_lines("OFF")))

    return scr_on, scr_off

# %%
def export_cad_onderlegger_dxf(
    out_path: str,
    bbox_rd: Tuple[float, float, float, float],
    bbox_topo_rd: Tuple[float, float, float, float] | None = None,
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
    bbox_for_topo = bbox_topo_rd or bbox_rd

    L_TOPO = "$$_00-00-00_onderlegger_Topokaart"
    _ensure_raster_layer(L_TOPO, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="topo_kaart.png",
        bbox_rd=bbox_for_topo,
        layer=L_TOPO,
    )

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
    L_BEST = "$$_00-00-00_onderlegger_Bestemmingsplankaart (Enkelbestemming)"
    _ensure_raster_layer(L_BEST, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="Bestemming_percelen.png",  #
        bbox_rd=bbox_rd,
        layer=L_BEST,
    )
    L_BEST_DUBBEL = "$$_00-00-00_onderlegger_Bestemmingsplankaart (Dubbelbestemming)"
    _ensure_raster_layer(L_BEST_DUBBEL, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="Bestemming_dubbel.png",  #
        bbox_rd=bbox_rd,
        layer=L_BEST_DUBBEL,
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
    # --- Bodemvlakken (BRO Bodemkaart) ---
    L_BODEM = "$$_00-00-00_onderlegger_Bodemvlakken"
    _ensure_raster_layer(L_BODEM, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="bodemvlakken.png",
        bbox_rd=bbox_rd,
        layer=L_BODEM,
    )

# --- Bodemkundig belang ---
    L_BODEM_BELANG = "$$_00-00-00_onderlegger_BodemkundigBelang"
    _ensure_raster_layer(L_BODEM_BELANG, default_on=False)
    add_georef_image_to_doc(
        doc=doc,
        image_path="bodemkundig_belang.png",
        bbox_rd=bbox_rd,
        layer=L_BODEM_BELANG,
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
    _ensure_layer(doc, "BGT_PAND", color=6)
    panden = _ogc_get_all_features(
    base_url=BGT_BASE,
    collection="pand",
    bbox=bbox_rd,
    bbox_crs=RD_CRS,
    limit=500,
)
    
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
    
    add_all_bgt_to_dxf(doc, msp, bbox_rd, limit_per_collection=2000)

    
    # Geen IMAGEDEF-relpath rewrite meer: nieuwe add_georef_image_to_doc schrijft direct bruikbare filenames.
    doc.saveas(out_path)
    
    try:
        scr_on, scr_off = write_bgt_toggle_scripts(doc, out_path, prefix="BGT-")
        print("AutoCAD scripts geschreven:")
        print(" -", scr_on)
        print(" -", scr_off)
    except Exception as e:
        print("[WARN] Kon BGT toggle scripts niet schrijven:", e)
    
    return out_path

# %%

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
bodemvlakken_png = download_bodemvlakken_png(
    bbox,
    out_path="bodemvlakken.png",
)

bodemkundig_png = download_bodemkundig_belang_png(
    bbox,
    out_path="bodemkundig_belang.png",
)





dxf_path = export_cad_onderlegger_dxf(
    out_path="onderlegger_snw_met_rasters.dxf",
    bbox_rd=bbox,
    bbox_topo_rd=bbox_topo,
    include_percelen=True,
    include_bebouwing=True
)

# %% [markdown]
# Tekst


