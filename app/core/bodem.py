# -*- coding: utf-8 -*-
"""Bodem data: GPKG lookup, WMS FeatureInfo, label resolution."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.core.constants import BODEM_GPKG_PATH, WMS_BODEM
from app.core.log_config import get_logger
from app.core.utils import bbox_str
from app.core.types import BBox

logger = get_logger(__name__)


# --------------- GPKG helpers ---------------

def _find_candidate_tables(con: sqlite3.Connection) -> List[str]:
    q = (
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'gpkg_%' "
        "AND name NOT LIKE 'rtree_%' "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [r[0] for r in con.execute(q).fetchall()]


def _table_columns(con: sqlite3.Connection, table_name: str) -> List[str]:
    q = f"PRAGMA table_info('{table_name}')"
    return [r[1] for r in con.execute(q).fetchall()]


def normalize_bodem_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("\u00a0", " ").strip()
    m = re.search(r"\b([A-Za-z]{1,4}[A-Za-z]?[0-9]{0,4}[A-Za-z0-9-]*)\b", s)
    if not m:
        return None
    return m.group(1).upper()


def load_all_bodem_code_map(gpkg_path: Path) -> Dict[str, str]:
    if not gpkg_path.exists():
        logger.warning("Bodem GPKG niet gevonden: %s", gpkg_path)
        return {}

    con = sqlite3.connect(str(gpkg_path))
    con.row_factory = sqlite3.Row
    mapping: Dict[str, str] = {}

    try:
        tables = _find_candidate_tables(con)
        interesting_tables: List[str] = []

        for t in tables:
            cols = [c.lower() for c in _table_columns(con, t)]
            joined = " ".join(cols)
            if (
                "soilunit" in joined
                or "soilcode" in joined
                or "legend" in joined
                or ("code" in joined and "description" in joined)
            ):
                interesting_tables.append(t)

        for table in interesting_tables:
            cols = _table_columns(con, table)
            cols_l = {c.lower(): c for c in cols}

            code_candidates = [
                "soilunit_code",
                "soilcode",
                "code",
                "first_soilcode",
                "maplegend_code",
                "legend_code",
            ]
            desc_candidates = [
                "soilunit_code_description",
                "soilunit_description",
                "soilunitname",
                "description",
                "naam",
                "legend_text",
                "maplegend_text",
            ]

            code_col = next(
                (cols_l[c] for c in code_candidates if c in cols_l), None
            )
            desc_col = next(
                (cols_l[c] for c in desc_candidates if c in cols_l), None
            )

            if not code_col or not desc_col:
                continue

            q = (
                f'SELECT DISTINCT "{code_col}" AS code, "{desc_col}" AS descr '
                f'FROM "{table}" '
                f'WHERE "{code_col}" IS NOT NULL AND "{desc_col}" IS NOT NULL'
            )

            for row in con.execute(q):
                code = normalize_bodem_code(row["code"])
                descr = (
                    str(row["descr"]).strip() if row["descr"] is not None else ""
                )
                if code and descr and len(descr) > 3:
                    mapping[code] = descr

        logger.info(
            "[BODEM] %d codes geladen uit %s", len(mapping), gpkg_path.name
        )
        return dict(sorted(mapping.items()))
    finally:
        con.close()


# Module-level lookup table (loaded once at import)
BODEM_CODE_MAP: Dict[str, str] = load_all_bodem_code_map(BODEM_GPKG_PATH)


def bodem_code_to_label(value: Any) -> Optional[str]:
    code = normalize_bodem_code(value)
    if not code:
        return None
    return BODEM_CODE_MAP.get(code)


def looks_like_only_bodem_code(value: Any) -> bool:
    code = normalize_bodem_code(value)
    if not code:
        return False
    return str(value).strip().upper() == code


# --------------- WMS FeatureInfo ---------------

def bodem_getfeatureinfo(
    bbox: BBox,
    width: int,
    height: int,
    i: int,
    j: int,
    layer: str = "soilarea",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    s = session or requests.Session()
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "CRS": "EPSG:28992",
        "BBOX": bbox_str(bbox),
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

    try:
        r = s.get(WMS_BODEM, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error("[ERROR_API_PDOK_BODEM] GetFeatureInfo failed: %s", e)
        raise

    return r.json()


def bodem_label_from_properties(
    props: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    descriptive_keys = [
        "soilunit_code_description",
        "soilunit_description",
        "soilunitname",
        "soil_name",
        "bodemeenheid",
        "naam",
        "description",
        "legend_text",
        "maplegend",
        "maplegend_text",
    ]

    for k in descriptive_keys:
        v = props.get(k)
        if not v:
            continue
        v_str = str(v).strip()
        if not v_str:
            continue

        mapped = bodem_code_to_label(v_str)
        if mapped:
            return mapped

        if len(v_str) > 8 and not looks_like_only_bodem_code(v_str):
            return v_str

    code_keys = [
        "soilunit_code",
        "code",
        "legend_code",
        "maplegend_code",
    ]
    for k in code_keys:
        v = props.get(k)
        mapped = bodem_code_to_label(v)
        if mapped:
            return mapped

    for _, v in props.items():
        mapped = bodem_code_to_label(v)
        if mapped:
            return mapped

    for _, v in props.items():
        if isinstance(v, str):
            s = v.strip()
            if s and len(s) > 8 and not looks_like_only_bodem_code(s):
                return s

    for k in code_keys:
        v = props.get(k)
        if v:
            return str(v).strip()

    return "Onbekende bodemklasse"


def bodem_label_at_pixel(
    bbox: BBox,
    width: int,
    height: int,
    x: int,
    y: int,
    layer: str = "soilarea",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    data = bodem_getfeatureinfo(
        bbox, width, height, x, y, layer=layer, session=session
    )
    feats = data.get("features", []) or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    return bodem_label_from_properties(props, session=session)
