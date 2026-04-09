# -*- coding: utf-8 -*-
"""Auth router — JWT login / me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import create_access_token, get_current_user, verify_credentials
from api.models import LoginRequest, TokenResponse, UserInfo

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    if not verify_credentials(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldige inloggegevens",
        )
    token = create_access_token(body.username)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserInfo)
async def me(username: str = Depends(get_current_user)):
    return UserInfo(username=username)
