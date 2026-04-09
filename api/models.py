# -*- coding: utf-8 -*-
"""Pydantic models for API request / response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


# --------------- Auth ---------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    username: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


# --------------- Generate ---------------

class GenerateRequest(BaseModel):
    mode: str = Field(..., pattern=r"^(address|coords|coordinates)$")
    address: str | None = None
    x: float | None = None
    y: float | None = None
    radius: float = Field(250, ge=10, le=5000)
    dxf_name: str = Field("onderlegger.dxf", pattern=r"^[\w\-. ]+\.dxf$")


class GenerateResponse(BaseModel):
    job_id: str
    message: str = "Generatie voltooid"


# --------------- Preview ---------------

class PreviewRequest(BaseModel):
    mode: str = Field(..., pattern=r"^(address|coords|coordinates)$")
    address: str | None = None
    x: float | None = None
    y: float | None = None
    radius: float = Field(250, ge=10, le=5000)


# --------------- Files ---------------

class FileInfo(BaseModel):
    filename: str
    size_bytes: int
    extension: str


class FileListResponse(BaseModel):
    job_id: str
    files: list[FileInfo]


# --------------- Geocoding ---------------

class GeocodeSuggestion(BaseModel):
    id: str
    display_name: str
    type: str
    score: float | None = None


class GeocodeSuggestResponse(BaseModel):
    suggestions: list[GeocodeSuggestion]


class GeocodeLookupResponse(BaseModel):
    display_name: str
    x: float
    y: float
    gemeente: str | None = None
    provincie: str | None = None
    woonplaats: str | None = None
    waterschap: str | None = None
    buurt: str | None = None
