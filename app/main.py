# -*- coding: utf-8 -*-
"""Streamlit web interface for the CAD Onderlegger generator."""

from __future__ import annotations

import base64
import io
import re
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

# Ensure project root is on sys.path for package imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import streamlit as st
from openai import OpenAI

from app.auth import check_auth
from app.cleanup import start_cleanup_scheduler
from app.config import settings
from app.core import (
    address_to_rd,
    address_to_rd_full,
    bbox_around_point,
    build_all_outputs,
    export_dxf,
)
from app.core.downloads import fetch_kadaster_brk_data
from app.core.log_config import setup_logging
from app.usage import track_generation

# --------------- Initialisation ---------------

setup_logging(settings.log_level)

st.set_page_config(page_title="CAD Onderlegger", layout="wide")

# Start cleanup scheduler once per process
if "cleanup_started" not in st.session_state:
    start_cleanup_scheduler()
    st.session_state["cleanup_started"] = True

# --------------- Auth gate ---------------

if not check_auth():
    st.stop()

# --------------- Defaults ---------------

PX_DEFAULT = 2000
TOPO_PX_DEFAULT = 4000
TOPO_MIN_SPAN_M = 3000.0
INCLUDE_PERCELEN = True
INCLUDE_BGT = True
BGT_LIMIT_PER_COLLECTION = 2000
DXF_DEFAULT_NAME = "onderlegger.dxf"

# --------------- Input validation helpers ---------------

RD_X_MIN, RD_X_MAX = 0.0, 300_000.0
RD_Y_MIN, RD_Y_MAX = 300_000.0, 625_000.0
RADIUS_MIN, RADIUS_MAX = 10.0, 5000.0
DXF_NAME_PATTERN = re.compile(r"^[\w\-. ]+\.dxf$", re.IGNORECASE)


def validate_address(address: str) -> str | None:
    """Return an error message, or None if valid."""
    address = address.strip()
    if not address:
        return "Vul een adres in."
    if len(address) < 3:
        return "Adres is te kort (minimaal 3 tekens)."
    if len(address) > 300:
        return "Adres is te lang (maximaal 300 tekens)."
    return None


def validate_coordinates(x: float, y: float) -> str | None:
    if not (RD_X_MIN <= x <= RD_X_MAX):
        return f"RD X moet tussen {RD_X_MIN:.0f} en {RD_X_MAX:.0f} liggen."
    if not (RD_Y_MIN <= y <= RD_Y_MAX):
        return f"RD Y moet tussen {RD_Y_MIN:.0f} en {RD_Y_MAX:.0f} liggen."
    return None


def validate_radius(radius: float) -> str | None:
    if not (RADIUS_MIN <= radius <= RADIUS_MAX):
        return f"Radius moet tussen {RADIUS_MIN:.0f} en {RADIUS_MAX:.0f} meter liggen."
    return None


def validate_dxf_name(name: str) -> str | None:
    if not DXF_NAME_PATTERN.match(name):
        return "Ongeldige DXF bestandsnaam. Gebruik alleen letters, cijfers, streepjes en eindig met .dxf."
    return None


# --------------- Sidebar ---------------

st.title("CAD Onderlegger generator")

with st.sidebar:
    st.header("Input")
    mode = st.radio("Input type", ["Adres", "RD (x,y)"], index=0)

    adres = ""
    x_val = y_val = None

    if mode == "Adres":
        adres = st.text_input("Adres", value="")
    else:
        x_val = st.number_input("RD X (EPSG:28992)", value=188584.761, format="%.3f")
        y_val = st.number_input("RD Y (EPSG:28992)", value=426360.756, format="%.3f")

    radius = st.number_input(
        "Radius (m) voor project bbox", value=250, min_value=10, max_value=5000, step=10
    )

    st.header("Output")
    dxf_name = st.text_input("DXF bestandsnaam", value=DXF_DEFAULT_NAME)

    preview_btn = st.button("Toon bbox preview")
    run_btn = st.button("Genereer onderlegger", type="primary")

    # Logout
    st.divider()
    if st.button("Uitloggen"):
        st.session_state["authenticated"] = False
        st.session_state.pop("username", None)
        st.rerun()


# --------------- Helpers ---------------

def _validate_all_inputs() -> str | None:
    """Run all validations. Return first error or None."""
    err = validate_radius(float(radius))
    if err:
        return err

    err = validate_dxf_name(dxf_name)
    if err:
        return err

    if mode == "Adres":
        err = validate_address(adres)
        if err:
            return err
    else:
        err = validate_coordinates(float(x_val), float(y_val))
        if err:
            return err

    return None


def get_bbox_from_input(mode_, adres_, x_, y_, radius_, session_):
    if mode_ == "Adres":
        cx, cy = address_to_rd(adres_, session=session_)
    else:
        cx, cy = float(x_), float(y_)
    bbox = bbox_around_point(cx, cy, float(radius_))
    return cx, cy, bbox


def zip_folder(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob("*"):
            if p.is_file():
                z.write(p, arcname=p.relative_to(folder))
    buf.seek(0)
    return buf.read()


def list_output_files(folder: Path) -> list[Path]:
    files = [p for p in folder.rglob("*") if p.is_file()]

    def key(p: Path):
        ext = p.suffix.lower()
        pri = 9
        if ext == ".dxf":
            pri = 0
        elif ext == ".png":
            pri = 1
        elif ext == ".scr":
            pri = 2
        return (pri, ext, str(p).lower())

    return sorted(files, key=key)


# --------------- Quickscan Presentatie ---------------

_SLIDE_SECTIONS = [
    {
        "title": "Ligging",
        "files": ["ligging_topo_breed.png", "ligging_luchtfoto_breed.png"],
        "captions": ["Topokaart (uitgezoomd)", "Luchtfoto (uitgezoomd)"],
        "prompt": (
            "Je ziet een uitgezoomde topokaart en/of luchtfoto van een projectlocatie in Nederland. "
            "Beschrijf de ligging: in welk soort omgeving ligt het (stedelijk, dorps, "
            "landelijk, bos, polder, kust)? Welke opvallende elementen zie je in de "
            "wijdere omgeving (wegen, water, bebouwing, groen, industriegebieden)? "
            "Noem relevante infrastructuur en landschappelijke kenmerken. "
            "Vermeld de bron: PDOK TOPraster / Luchtfoto (actueel jaar). "
            "Geef een korte analyse van 3-5 zinnen."
        ),
    },
    {
        "title": "Kadastrale gegevens",
        "files": ["luchtfoto_kadaster.png"],
        "captions": ["Luchtfoto met kadastrale kaart"],
        "prompt": (
            "Je ziet een luchtfoto met daarop de kadastrale percelen. "
            "Beschrijf wat je ziet: hoe groot zijn de percelen globaal, "
            "hoe is de verkaveling, zijn er opvallende perceelvormen? "
            "Bron: Kadaster BRK / PDOK (actueel). "
            "Geef een korte analyse van 2-4 zinnen."
        ),
    },
    {
        "title": "Bestemming",
        "files": ["Bestemming_percelen.png", "Bestemming_dubbel.png"],
        "captions": ["Enkelbestemming", "Dubbelbestemming"],
        "prompt": (
            "Je ziet bestemmingsplankaarten (enkelbestemming en/of dubbelbestemming). "
            "Analyseer welke bestemmingen je herkent aan de kleuren en patronen "
            "(wonen, agrarisch, groen, natuur, verkeer, water, etc.). "
            "Welke bestemming is dominant? Zijn er dubbelbestemmingen die beperkingen "
            "opleveren voor planontwikkeling? "
            "Vermeld dat de bron het bestemmingsplan is via Ruimtelijkeplannen.nl / PDOK PLU (actueel). "
            "Geef een analyse van 3-5 zinnen."
        ),
    },
    {
        "title": "Natuurnetwerk & Natura 2000",
        "files": ["natura2000.png"],
        "captions": ["Natura 2000-gebieden"],
        "prompt": (
            "Je ziet een kaart met Natura 2000-gebieden over een luchtfoto. "
            "Gekleurde/gearceerde vlakken duiden op beschermde Natura 2000-gebieden. "
            "Beoordeel of de projectlocatie binnen of nabij een beschermd gebied valt. "
            "Beschrijf de mogelijke gevolgen: noodzaak voor een voortoets of passende beoordeling, "
            "beperkingen voor stikstofdepositie, verstoring van soorten, of bufferzone-eisen. "
            "Als er geen gekleurd vlak zichtbaar is, meld dan dat het gebied niet binnen "
            "Natura 2000 valt maar benoem wel de afstand tot het dichtstbijzijnde gebied indien zichtbaar. "
            "Bron: Rijksdienst voor Ondernemend Nederland (RVO), Natura 2000-register (actueel). "
            "Geef een analyse van 3-5 zinnen."
        ),
    },
    {
        "title": "Geomorfologie",
        "files": ["Geomorfologische_kaart.png"],
        "captions": ["Geomorfologische kaart"],
        "prompt": (
            "Je ziet een geomorfologische kaart met legenda. "
            "Beschrijf welke landvormen aanwezig zijn (strandwal, strandvlakte, "
            "dekzandrug, rivierterras, kustduin, etc.). "
            "Leg per landvorm kort uit wat het is en wat de betekenis is "
            "voor bouwplannen of grondwerk. "
            "Bron: BRO Geomorfologische kaart (2024). "
            "Geef een analyse van 3-5 zinnen."
        ),
    },
    {
        "title": "Bodem",
        "files": ["bodemvlakken.png"],
        "captions": ["Bodemkaart"],
        "prompt": (
            "Je ziet een bodemkaart met legenda. "
            "Beschrijf welke bodemtypen aanwezig zijn op de locatie. "
            "Geef per type een korte uitleg (bijv. draagkracht, waterdoorlatendheid, "
            "geschiktheid voor bebouwing/beplanting). "
            "Bron: BRO Bodemkaart (2024). "
            "Analyse in 3-5 zinnen."
        ),
    },
    {
        "title": "Grondwater",
        "files": ["wdm_ghg.png", "wdm_glg.png", "wdm_gt.png"],
        "captions": [
            "Gemiddeld Hoogste Grondwaterstand (GHG)",
            "Gemiddeld Laagste Grondwaterstand (GLG)",
            "Grondwatertrap (GT)",
        ],
        "prompt": (
            "Je ziet kaarten van de grondwaterstand: GHG (gemiddeld hoogste), "
            "GLG (gemiddeld laagste) en eventueel grondwatertrap. "
            "Beschrijf op welke diepte het grondwater zich bevindt, "
            "wat de grondwatertrap betekent, en welke gevolgen dit heeft "
            "voor bouw, drainage en tuinaanleg. "
            "Bron: BRO Grondwaterspiegeldiepte (2024). "
            "Analyse in 3-5 zinnen."
        ),
    },
    {
        "title": "Hoogtekaart (AHN)",
        "files": ["ahn_dtm.png", "ahn_dsm.png"],
        "captions": [
            "Digitaal Terreinmodel (DTM) — maaiveld",
            "Digitaal Oppervlaktemodel (DSM) — incl. objecten",
        ],
        "prompt": (
            "Je ziet AHN hoogtekaarten: een DTM (maaiveld) en/of DSM "
            "(inclusief gebouwen/bomen). De kleurschaal is dynamisch. "
            "Beschrijf de hoogteverschillen die je ziet, wat het globale "
            "hoogtebereik is (lees af uit de legenda), of het terrein vlak "
            "of heuvelachtig is, en welke objecten (gebouwen, bomen) opvallen. "
            "Bron: AHN4, Rijkswaterstaat (2020-2022). "
            "Geef een analyse van 3-5 zinnen."
        ),
    },
    {
        "title": "Historie",
        "files": [
            "topotijdreis_1900.png",
            "topotijdreis_1950.png",
            "topotijdreis_2000.png",
        ],
        "captions": [
            "Topokaart 1900 (Topotijdreis)",
            "Topokaart 1950 (Topotijdreis)",
            "Topokaart 2000 (Topotijdreis)",
        ],
        "prompt": (
            "Je ziet historische topokaarten (Topotijdreis, Kadaster) uit 1900, 1950 en 2000. "
            "Beschrijf hoe het gebied zich in de afgelopen 100+ jaar heeft ontwikkeld: "
            "wat was er rond 1900 (agrarisch, onbebouwd, dorp, stad)? "
            "Welke veranderingen zijn zichtbaar rond 1950 (naoorlogse uitbreiding, ruilverkaveling)? "
            "En hoe is het gebied sindsdien getransformeerd tot de huidige situatie? "
            "Bron: Topotijdreis.nl / Kadaster (historisch kaartmateriaal). "
            "Geef een uitgebreide analyse van 5-8 zinnen."
        ),
    },
]


def _image_to_data_url(path: Path) -> str:
    """Convert image file to base64 data URL for OpenAI vision."""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:image/png;base64,{b64}"


def _analyse_images(
    image_paths: list[Path],
    section_prompt: str,
    location_context: str,
) -> str:
    """Send images to GPT-4o for analysis. Returns markdown text."""
    api_key = settings.openai_api_key
    if not api_key:
        return "*Geen OPENAI_API_KEY geconfigureerd — analyse overgeslagen.*"

    client = OpenAI(api_key=api_key)

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Locatiecontext: {location_context}\n\n"
                f"{section_prompt}\n\n"
                "Antwoord in het Nederlands. Wees bondig en feitelijk. "
                "Gebruik geen opsommingstekens, schrijf in lopende tekst."
            ),
        }
    ]
    for p in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(p), "detail": "low"},
            }
        )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=400,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"*AI-analyse mislukt: {e}*"


def _analyse_text_only(
    prompt: str,
    location_context: str,
) -> str:
    """Send a text-only prompt to GPT-4o. Returns markdown text."""
    api_key = settings.openai_api_key
    if not api_key:
        return "*Geen OPENAI_API_KEY geconfigureerd — analyse overgeslagen.*"

    client = OpenAI(api_key=api_key)

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Locatiecontext: {location_context}\n\n"
                        f"{prompt}\n\n"
                        "Antwoord in het Nederlands. Wees bondig en feitelijk. "
                        "Gebruik geen opsommingstekens, schrijf in lopende tekst."
                    ),
                }
            ],
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"*AI-analyse mislukt: {e}*"


def _web_search_history(location_context: str) -> str:
    """Use OpenAI Responses API with web search to find historical info.

    Returns markdown text with historical information.
    """
    api_key = settings.openai_api_key
    if not api_key:
        return "*Geen OPENAI_API_KEY geconfigureerd.*"

    client = OpenAI(api_key=api_key)

    prompt = (
        f"Zoek op het internet naar historische informatie over {location_context}.\n\n"
        "Doe het volgende:\n"
        "1. Beschrijf de geschiedenis van deze locatie/dit dorp/deze buurt: "
        "oorsprong, belangrijke historische gebeurtenissen, hoe het gebied zich heeft ontwikkeld.\n"
        "2. Benoem historische gebouwen, monumenten, landgoederen of andere erfgoedobjecten "
        "die in of nabij het gebied liggen.\n"
        "3. Geef relevante jaartallen en historische context.\n\n"
        "Vermeld altijd de bronnen met jaartallen waar mogelijk. "
        "Antwoord in het Nederlands."
    )

    try:
        resp = client.responses.create(
            model="gpt-4o",
            input=prompt,
            tools=[{"type": "web_search_preview"}],
            max_output_tokens=800,
        )
        return resp.output_text or ""
    except Exception as e:
        return f"*Historisch onderzoek mislukt: {e}*"


_WIKIMEDIA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _search_wikimedia_images(location_context: str, limit: int = 6) -> list[dict]:
    """Search Wikimedia Commons for images related to a location.

    Returns list of dicts with 'title', 'thumb_url', and 'description_url'.
    """
    # Extract a short search term (place name + province name)
    search_term = location_context.split(",")[0].strip()
    parts = location_context.split(",")
    # Try to add province name (strip "provincie " prefix)
    for part in parts[1:]:
        cleaned = part.strip()
        if cleaned.lower().startswith("provincie "):
            search_term += " " + cleaned[len("provincie "):]
            break
        elif cleaned.lower().startswith("gemeente "):
            # Skip gemeente, prefer provincie
            continue
        else:
            search_term += " " + cleaned
            break

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": search_term,
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "800",
        "format": "json",
    }

    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params=params,
            timeout=15,
            headers={"User-Agent": _WIKIMEDIA_UA},
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    results = []
    pages = data.get("query", {}).get("pages", {})
    for _pid, page in pages.items():
        ii = page.get("imageinfo", [{}])[0]
        thumb = ii.get("thumburl", "")
        if thumb:
            title = page.get("title", "").replace("File:", "")
            results.append({
                "title": title,
                "thumb_url": thumb,
                "description_url": ii.get("descriptionurl", ""),
            })
    return results


def show_quickscan(
    out_dir: Path,
    loc_info: dict | None,
    adres: str,
    radius: float,
    bbox: tuple | None = None,
    session: object | None = None,
) -> None:
    """Render a Quickscan-style presentation overview in Streamlit."""
    st.divider()
    st.header("Quickscan Presentatie")

    # --- Locatie informatie header ---
    display_name = adres
    if loc_info:
        display_name = loc_info.get("weergavenaam", adres)

    st.subheader(f"Projectlocatie: {display_name}")

    if loc_info:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Gemeente", loc_info.get("gemeentenaam", "—"))
        with col2:
            st.metric("Provincie", loc_info.get("provincienaam", "—"))
        with col3:
            st.metric("Waterschap", loc_info.get("waterschapsnaam", "—"))

        col4, col5, col6 = st.columns(3)
        with col4:
            st.metric("Woonplaats", loc_info.get("woonplaatsnaam", "—"))
        with col5:
            st.metric("Buurt", loc_info.get("buurtnaam", "—"))
        with col6:
            st.metric("Onderzoeksgebied", f"{radius:.0f} m radius")

    # Build location context string for AI prompts
    location_ctx = display_name
    gemeente = ""
    if loc_info:
        parts = [display_name]
        gemeente = loc_info.get("gemeentenaam", "")
        pv = loc_info.get("provincienaam", "")
        if gemeente:
            parts.append(f"gemeente {gemeente}")
        if pv:
            parts.append(f"provincie {pv}")
        location_ctx = ", ".join(parts)

    st.divider()

    # --- Fetch the most relevant Kadaster BRK parcel ---
    kadaster_parcel = None
    if bbox and session:
        try:
            center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
            kadaster_parcel = fetch_kadaster_brk_data(bbox, center=center, session=session)
        except Exception:
            pass

    # --- Thematic slides ---
    for section in _SLIDE_SECTIONS:
        title = section["title"]

        existing = [f for f in section["files"] if (out_dir / f).exists()]

        # --- Historie section: special handling with web search ---
        if title == "Historie":
            st.subheader(title)

            # Show topotijdreis maps if available
            if existing:
                topo_cols = st.columns(min(len(existing), 3))
                for i, fname in enumerate(existing):
                    idx = section["files"].index(fname)
                    with topo_cols[i % len(topo_cols)]:
                        st.image(
                            str(out_dir / fname),
                            caption=section["captions"][idx],
                            use_container_width=True,
                        )

            # AI analysis of topotijdreis maps
            if existing:
                with st.spinner("AI analyse topokaarten…"):
                    image_paths = [out_dir / f for f in existing]
                    topo_analysis = _analyse_images(
                        image_paths, section["prompt"], location_ctx
                    )
                st.markdown(topo_analysis)

            # Web search for historical info
            with st.spinner("Historisch onderzoek (internet)…"):
                history_text = _web_search_history(location_ctx)

            st.markdown("---")
            st.markdown("**Historische informatie (bronnen: internet)**")
            st.markdown(history_text)

            # Wikimedia Commons photos
            with st.spinner("Foto's zoeken (Wikimedia Commons)…"):
                wiki_images = _search_wikimedia_images(location_ctx)

            if wiki_images:
                st.markdown("**Historische foto's** *(bron: Wikimedia Commons)*")
                photo_cols = st.columns(min(len(wiki_images), 3))
                for i, img_info in enumerate(wiki_images):
                    try:
                        r = requests.get(
                            img_info["thumb_url"],
                            timeout=15,
                            headers={"User-Agent": _WIKIMEDIA_UA},
                        )
                        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                            with photo_cols[i % len(photo_cols)]:
                                st.image(
                                    r.content,
                                    caption=img_info["title"][:80],
                                    use_container_width=True,
                                )
                    except Exception:
                        pass

            st.divider()
            continue
        if not existing:
            continue

        st.subheader(title)

        # Layout: images on left (40%), AI analysis text on right (60%)
        col_img, col_txt = st.columns([2, 3])

        with col_img:
            if len(existing) == 1:
                st.image(
                    str(out_dir / existing[0]),
                    caption=section["captions"][section["files"].index(existing[0])],
                    use_container_width=True,
                )
            elif len(existing) == 2:
                subcols = st.columns(2)
                for i, fname in enumerate(existing):
                    idx = section["files"].index(fname)
                    with subcols[i]:
                        st.image(
                            str(out_dir / fname),
                            caption=section["captions"][idx],
                            use_container_width=True,
                        )
            else:
                # 3+ images: show first row of 2, then rest
                subcols = st.columns(2)
                for i, fname in enumerate(existing[:2]):
                    idx = section["files"].index(fname)
                    with subcols[i]:
                        st.image(
                            str(out_dir / fname),
                            caption=section["captions"][idx],
                            use_container_width=True,
                        )
                if len(existing) > 2:
                    subcols2 = st.columns(min(len(existing) - 2, 2))
                    for i, fname in enumerate(existing[2:]):
                        idx = section["files"].index(fname)
                        with subcols2[i % len(subcols2)]:
                            st.image(
                                str(out_dir / fname),
                                caption=section["captions"][idx],
                                use_container_width=True,
                            )

        with col_txt:
            # --- Extra: show the most relevant Kadaster BRK parcel ---
            if title == "Kadastrale gegevens" and kadaster_parcel:
                p = kadaster_parcel
                grootte = p.get("grootte_m2", "")
                grootte_str = f"{grootte} m²" if grootte else "onbekend"
                geldigheid = p.get("begin_geldigheid", "")[:10] if p.get("begin_geldigheid") else ""
                st.markdown("**Kadastraal perceel** *(bron: Kadaster BRK, PDOK)*")
                st.markdown(
                    f"**{p.get('gemeente', '?')} {p.get('sectie', '?')} "
                    f"{p.get('perceelnummer', '?')}**"
                )
                st.markdown(f"Oppervlakte: **{grootte_str}** ({p.get('soort_grootte', '')})")
                if geldigheid:
                    st.markdown(f"Geldig vanaf: {geldigheid}")
                st.markdown("---")

            # --- Extra: bestemming section gets enhanced prompt with municipality context ---
            prompt = section["prompt"]
            if title == "Bestemming" and gemeente:
                prompt += (
                    f"\n\nAanvullende context: de locatie valt onder gemeente {gemeente}. "
                    "Benoem indien mogelijk relevante gemeentelijke beleidsplannen, "
                    "structuurvisies of omgevingsvisies van deze gemeente die van toepassing "
                    "kunnen zijn op de projectlocatie. Vermeld altijd de naam van het plan en "
                    "het jaartal. Als je niet zeker bent, geef dit eerlijk aan."
                )

            image_paths = [out_dir / f for f in existing]
            with st.spinner("AI analyse…"):
                analysis = _analyse_images(
                    image_paths, prompt, location_ctx
                )
            st.markdown(analysis)

            # --- Extra: dedicated omgevingsvisie lookup for Bestemming ---
            if title == "Bestemming" and gemeente:
                with st.spinner("Omgevingsvisie opzoeken…"):
                    ov_prompt = (
                        f"Zoek informatie over de omgevingsvisie van gemeente {gemeente}. "
                        "Beantwoord de volgende vragen zo concreet mogelijk:\n"
                        "1. Hoe heet de omgevingsvisie of structuurvisie van deze gemeente? "
                        "Wanneer is deze vastgesteld?\n"
                        "2. Wat zijn de belangrijkste ambities en beleidslijnen uit deze visie "
                        "die relevant zijn voor ruimtelijke ontwikkeling?\n"
                        "3. Zijn er specifieke gebiedsvisies, bestemmingsplannen of "
                        "programma's die van toepassing kunnen zijn op de beschreven locatie?\n"
                        "4. Welke kansen of beperkingen volgen hieruit voor een nieuw project "
                        "op deze locatie?\n\n"
                        "Vermeld altijd de naam van het document en het jaartal. "
                        "Vermeld ook de URL van de gemeente als je die kent. "
                        "Als je niet zeker bent over specifieke details, geef dit eerlijk aan "
                        "en verwijs naar de gemeentelijke website voor de meest actuele informatie."
                    )
                    ov_analysis = _analyse_text_only(ov_prompt, location_ctx)
                st.markdown("---")
                st.markdown("**Omgevingsvisie**")
                st.markdown(ov_analysis)

        st.divider()


# --------------- Preview ---------------

if preview_btn:
    err = _validate_all_inputs()
    if err:
        st.error(err)
    else:
        try:
            session = requests.Session()
            cx, cy, bbox = get_bbox_from_input(
                mode, adres, x_val, y_val, radius, session
            )
            st.subheader("BBOX preview")

            with tempfile.TemporaryDirectory(prefix="pdok_preview_") as tmp:
                preview_dir = Path(tmp)
                rasters, _ = build_all_outputs(
                    bbox=bbox,
                    out_dir=preview_dir,
                    px=800,
                    topo_px=1200,
                    topo_min_span_m=TOPO_MIN_SPAN_M,
                    session=session,
                )

                cols = st.columns(2)
                luchtfoto = preview_dir / "Luchtfoto.png"
                topo = preview_dir / "topo_kaart.png"

                if luchtfoto.exists():
                    with cols[0]:
                        st.image(str(luchtfoto), caption="Preview luchtfoto", width=350)
                if topo.exists():
                    with cols[1]:
                        st.image(str(topo), caption="Preview topo", width=350)

        except ValueError as ve:
            st.error(f"Invoerfout: {ve}")
        except requests.RequestException as re_:
            st.error(f"API-fout: kon gegevens niet ophalen. Probeer het later opnieuw. ({re_})")
        except Exception:
            st.error("Er ging iets mis bij het maken van de preview.")
            st.code("".join(traceback.format_exc()))

# --------------- Generate ---------------

if run_btn:
    err = _validate_all_inputs()
    if err:
        st.error(err)
    else:
        with tempfile.TemporaryDirectory(prefix="pdok_onderlegger_") as tmp:
            out_dir = Path(tmp) / "output_onderlegger"
            out_dir.mkdir(parents=True, exist_ok=True)

            session = requests.Session()
            log_box = st.empty()
            logs: list[str] = []

            def slog(msg: str):
                logs.append(msg)
                log_box.code("\n".join(logs), language="text")

            success = False
            cx_log = cy_log = 0.0
            loc_info = None

            try:
                if mode == "Adres":
                    slog(f"[LOC] Adres -> RD: {adres}")
                    loc_info = address_to_rd_full(adres, session=session)
                    cx_log, cy_log = loc_info["x"], loc_info["y"]
                    slog(f"[LOC] RD: x={cx_log:.3f}, y={cy_log:.3f}")
                else:
                    cx_log, cy_log = float(x_val), float(y_val)
                    slog(f"[LOC] RD (input): x={cx_log:.3f}, y={cy_log:.3f}")

                bbox = bbox_around_point(cx_log, cy_log, float(radius))
                slog(f"[BBOX] {bbox}")

                slog("[RUN] Download rasters…")
                rasters, _ = build_all_outputs(
                    bbox=bbox,
                    out_dir=out_dir,
                    px=PX_DEFAULT,
                    topo_px=TOPO_PX_DEFAULT,
                    topo_min_span_m=TOPO_MIN_SPAN_M,
                    session=session,
                )

                out_dxf = out_dir / dxf_name
                slog(f"[DXF] Export -> {out_dxf.name}")

                export_dxf(
                    out_dxf,
                    bbox=bbox,
                    raster_dir=out_dir,
                    rasters=rasters,
                    include_percelen=INCLUDE_PERCELEN,
                    include_bgt=INCLUDE_BGT,
                    bgt_limit_per_collection=BGT_LIMIT_PER_COLLECTION,
                    session=session,
                )

                slog("[DONE] Klaar")
                success = True

                # --- Downloads ---
                st.subheader("Download")

                zip_bytes = zip_folder(out_dir)
                st.download_button(
                    "Download alles (zip)",
                    data=zip_bytes,
                    file_name="output_onderlegger.zip",
                    mime="application/zip",
                )

                # --- Quickscan Presentatie ---
                show_quickscan(
                    out_dir=out_dir,
                    loc_info=loc_info,
                    adres=adres if mode == "Adres" else f"RD ({cx_log:.0f}, {cy_log:.0f})",
                    radius=float(radius),
                    bbox=bbox,
                    session=session,
                )

            except ValueError as ve:
                st.error(f"Invoerfout: {ve}")
            except requests.RequestException as re_:
                st.error(f"API-fout: kon gegevens niet ophalen. Probeer het later opnieuw. ({re_})")
            except Exception:
                st.error("Er ging iets mis tijdens genereren.")
                st.code("".join(traceback.format_exc()))
            finally:
                # Track usage regardless of outcome
                track_generation(
                    user=st.session_state.get("username", "unknown"),
                    address=adres if mode == "Adres" else "",
                    x=cx_log,
                    y=cy_log,
                    radius=float(radius),
                    success=success,
                )
