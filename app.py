import io
import zipfile
from pathlib import Path
import tempfile
import traceback

import streamlit as st
import requests

import pdok_cad_onderlegger as gen


st.set_page_config(page_title="CAD Onderlegger", layout="wide")
st.title("CAD Onderlegger generator")

# =========================
# Vaste defaults (niet aanpasbaar)
# =========================
PX_DEFAULT = 2000
TOPO_PX_DEFAULT = 4000
TOPO_MIN_SPAN_M = 3000.0

INCLUDE_PERCELEN = True
INCLUDE_BGT = True
BGT_LIMIT_PER_COLLECTION = 2000

DXF_DEFAULT_NAME = "onderlegger.dxf"


with st.sidebar:
    st.header("Input")
    mode = st.radio("Input type", ["Adres", "RD (x,y)"], index=0)

    adres = ""
    x = y = None

    if mode == "Adres":
        adres = st.text_input("Adres", value="")
    else:
        x = st.number_input("RD X (EPSG:28992)", value=188584.761, format="%.3f")
        y = st.number_input("RD Y (EPSG:28992)", value=426360.756, format="%.3f")

    radius = st.number_input("Radius (m) voor project bbox", value=250, min_value=10, step=10)

    st.header("Output")
    dxf_name = st.text_input("DXF bestandsnaam", value=DXF_DEFAULT_NAME)

    preview_btn = st.button("Toon bbox preview")
    run_btn = st.button("Genereer onderlegger", type="primary")


def get_bbox_from_input(mode, adres, x, y, radius, session):
    if mode == "Adres":
        if not adres.strip():
            raise ValueError("Vul een adres in.")
        cx, cy = gen.address_to_rd(adres, session=session)
    else:
        cx, cy = float(x), float(y)

    bbox = gen.bbox_around_point(cx, cy, float(radius))
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
    # Sorteer: eerst dxf, dan png, dan rest
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

if preview_btn:
    try:
        session = requests.Session()
        cx, cy, bbox = get_bbox_from_input(mode, adres, x, y, radius, session)

        st.subheader("BBOX preview")

        with tempfile.TemporaryDirectory(prefix="pdok_preview_") as tmp:
            preview_dir = Path(tmp)

            # Alleen lichte preview maken, niet alles genereren
            rasters, _ = gen.build_all_outputs(
                bbox=bbox,
                out_dir=preview_dir,
                px=800,          # klein houden voor snelle preview
                topo_px=1200,    # klein houden
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

    except Exception:
        st.error("Er ging iets mis bij het maken van de preview.")
        st.code("".join(traceback.format_exc()))

if run_btn:
    with tempfile.TemporaryDirectory(prefix="pdok_onderlegger_") as tmp:
        out_dir = Path(tmp) / "output_onderlegger"
        out_dir.mkdir(parents=True, exist_ok=True)

        session = requests.Session()

        log_box = st.empty()
        logs: list[str] = []

        def slog(msg: str):
            logs.append(msg)
            log_box.code("\n".join(logs), language="text")

        try:
            # 1) centrum bepalen
            if mode == "Adres":
                if not adres.strip():
                    st.error("Vul een adres in.")
                    st.stop()
                slog(f"[LOC] Adres -> RD: {adres}")
                cx, cy = gen.address_to_rd(adres, session=session)
                slog(f"[LOC] RD: x={cx:.3f}, y={cy:.3f}")
            else:
                cx, cy = float(x), float(y)
                slog(f"[LOC] RD (input): x={cx:.3f}, y={cy:.3f}")

            # 2) bbox
            bbox = gen.bbox_around_point(cx, cy, float(radius))
            slog(f"[BBOX] {bbox}")

            # 3) rasters genereren
            slog("[RUN] Download rasters…")
            rasters, _ = gen.build_all_outputs(
                bbox=bbox,
                out_dir=out_dir,
                px=PX_DEFAULT,
                topo_px=TOPO_PX_DEFAULT,
                topo_min_span_m=TOPO_MIN_SPAN_M,
                session=session,
            )

            # 4) DXF export
            out_dxf = out_dir / dxf_name
            slog(f"[DXF] Export -> {out_dxf.name}")

            gen.export_dxf(
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

            st.subheader("Download")

            # ✅ 1 bestand download (zip)
            zip_bytes = zip_folder(out_dir)
            st.download_button(
                "Download alles (zip)",
                data=zip_bytes,
                file_name="output_onderlegger.zip",
                mime="application/zip",
            )

            # ✅ Alternatief: losse downloads (geen zip)
            st.caption("losse bestanden (DXF + PNG’s).")

            files = list_output_files(out_dir)
            for p in files:
                rel_name = p.name
                data = p.read_bytes()

                # mime
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


        except Exception:
            st.error("Er ging iets mis tijdens genereren.")
            st.code("".join(traceback.format_exc()))