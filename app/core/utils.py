# -*- coding: utf-8 -*-
"""Small utility functions."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from app.core.types import BBox


def bbox_center(b: BBox) -> Tuple[float, float]:
    minx, miny, maxx, maxy = b
    return (0.5 * (minx + maxx), 0.5 * (miny + maxy))


def bbox_str(b: BBox) -> str:
    return ",".join(f"{x:.6f}" for x in b)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))
