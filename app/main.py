# -*- coding: utf-8 -*-
"""Streamlit web interface for the CAD Onderlegger generator."""

from __future__ import annotations

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

from app.auth import check_auth
from app.cleanup import start_cleanup_scheduler
from app.config import settings
from app.core import address_to_rd, bbox_around_point, build_all_outputs, export_dxf
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

            try:
                if mode == "Adres":
                    slog(f"[LOC] Adres -> RD: {adres}")
                    cx_log, cy_log = address_to_rd(adres, session=session)
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

                st.caption("Losse bestanden (DXF + PNG's).")
                files = list_output_files(out_dir)
                for p in files:
                    rel_name = p.name
                    data = p.read_bytes()

                    ext = p.suffix.lower()
                    if ext == ".png":
                        mime = "image/png"
                    elif ext == ".dxf":
                        mime = "application/dxf"
                    elif ext == ".scr":
                        mime = "text/plain"
                    else:
                        mime = "application/octet-stream"

                    st.download_button(
                        f"Download {rel_name}",
                        data=data,
                        file_name=rel_name,
                        mime=mime,
                        key=f"dl-{rel_name}",
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
