# -*- coding: utf-8 -*-
"""Shared types and dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class MapRequest:
    bbox: BBox
    width: int = 2000
    height: int = 2000
    crs: str = "EPSG:28992"
    version: str = "1.3.0"
    fmt: str = "image/png"
    transparent: bool = True


@dataclass
class ExportPlan:
    filename: str
    bbox: BBox
    dxf_layer: str
    default_on: bool = False
