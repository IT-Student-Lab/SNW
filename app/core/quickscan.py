# -*- coding: utf-8 -*-
"""Quickscan analysis logic — framework-agnostic.

Extracted from app/main.py so both the (deprecated) Streamlit UI
and the new FastAPI backend can reuse the same analysis functions.
"""

from __future__ import annotations

import base64
from pathlib import Path

import requests
from openai import OpenAI

from app.config import settings

# --------------- Slide section definitions ---------------

SLIDE_SECTIONS: list[dict] = [
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


# --------------- AI helpers ---------------

def image_to_data_url(path: Path) -> str:
    """Convert image file to base64 data URL for OpenAI vision."""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:image/png;base64,{b64}"


def analyse_images(
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
                "image_url": {"url": image_to_data_url(p), "detail": "low"},
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


def analyse_text_only(
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


def web_search_history(location_context: str) -> str:
    """Use OpenAI Responses API with web search to find historical info."""
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


# --------------- Wikimedia ---------------

_WIKIMEDIA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def search_wikimedia_images(location_context: str, limit: int = 6) -> list[dict]:
    """Search Wikimedia Commons for images related to a location.

    Returns list of dicts with 'title', 'thumb_url', and 'description_url'.
    """
    search_term = location_context.split(",")[0].strip()
    parts = location_context.split(",")
    for part in parts[1:]:
        cleaned = part.strip()
        if cleaned.lower().startswith("provincie "):
            search_term += " " + cleaned[len("provincie "):]
            break
        elif cleaned.lower().startswith("gemeente "):
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


# --------------- Full quickscan runner (headless) ---------------

def build_location_context(loc_info: dict | None, adres: str) -> tuple[str, str]:
    """Build location context string and gemeente from loc_info.

    Returns (location_ctx, gemeente).
    """
    display_name = adres
    if loc_info:
        display_name = loc_info.get("weergavenaam", adres)

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

    return location_ctx, gemeente


def run_quickscan(
    out_dir: Path,
    loc_info: dict | None,
    adres: str,
    radius: float,
    bbox: tuple | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """Run the full quickscan analysis (headless, no Streamlit).

    Returns a list of section result dicts:
        [
            {
                "title": str,
                "images": [{"filename": str, "caption": str}, ...],
                "analysis": str,
                "kadaster_parcel": dict | None,       # only for Kadastrale gegevens
                "omgevingsvisie": str | None,          # only for Bestemming
                "history_web": str | None,             # only for Historie
                "wikimedia_images": list[dict] | None, # only for Historie
            },
            ...
        ]
    """
    from app.core.downloads import fetch_kadaster_brk_data

    location_ctx, gemeente = build_location_context(loc_info, adres)

    # Fetch kadaster parcel data
    kadaster_parcel = None
    if bbox and session:
        try:
            center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
            kadaster_parcel = fetch_kadaster_brk_data(bbox, center=center, session=session)
        except Exception:
            pass

    results: list[dict] = []

    # Location info header
    results.append({
        "title": "_location_info",
        "display_name": loc_info.get("weergavenaam", adres) if loc_info else adres,
        "gemeente": loc_info.get("gemeentenaam", "—") if loc_info else "—",
        "provincie": loc_info.get("provincienaam", "—") if loc_info else "—",
        "waterschap": loc_info.get("waterschapsnaam", "—") if loc_info else "—",
        "woonplaats": loc_info.get("woonplaatsnaam", "—") if loc_info else "—",
        "buurt": loc_info.get("buurtnaam", "—") if loc_info else "—",
        "radius": radius,
    })

    for section in SLIDE_SECTIONS:
        title = section["title"]
        existing = [f for f in section["files"] if (out_dir / f).exists()]

        entry: dict = {
            "title": title,
            "images": [],
            "analysis": "",
            "kadaster_parcel": None,
            "omgevingsvisie": None,
            "history_web": None,
            "wikimedia_images": None,
        }

        # Add existing images
        for fname in existing:
            idx = section["files"].index(fname)
            entry["images"].append({
                "filename": fname,
                "caption": section["captions"][idx],
            })

        if title == "Historie":
            # AI analysis of topotijdreis maps
            if existing:
                image_paths = [out_dir / f for f in existing]
                entry["analysis"] = analyse_images(
                    image_paths, section["prompt"], location_ctx
                )

            # Web search for historical info
            entry["history_web"] = web_search_history(location_ctx)

            # Wikimedia Commons photos
            entry["wikimedia_images"] = search_wikimedia_images(location_ctx)

            results.append(entry)
            continue

        if not existing:
            continue

        # Kadaster section extra
        if title == "Kadastrale gegevens" and kadaster_parcel:
            entry["kadaster_parcel"] = kadaster_parcel

        # Bestemming section: enhanced prompt
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
        entry["analysis"] = analyse_images(image_paths, prompt, location_ctx)

        # Bestemming: extra omgevingsvisie lookup
        if title == "Bestemming" and gemeente:
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
            entry["omgevingsvisie"] = analyse_text_only(ov_prompt, location_ctx)

        results.append(entry)

    return results
