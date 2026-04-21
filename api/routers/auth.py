# -*- coding: utf-8 -*-
"""Auth router — JWT login / me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import create_access_token, create_download_token, get_current_user, verify_credentials
from api.models import ChangePasswordRequest, LoginRequest, TokenResponse, UserInfo
from api.users import change_password

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


@router.post("/download-token")
async def download_token(username: str = Depends(get_current_user)):
    """Issue a short-lived single-purpose token for browser-native downloads."""
    token = create_download_token(username)
    return {"token": token}


@router.get("/me", response_model=UserInfo)
async def me(username: str = Depends(get_current_user)):
    return UserInfo(username=username)


@router.post("/change-password")
async def change_pw(
    body: ChangePasswordRequest,
    username: str = Depends(get_current_user),
):
    if not verify_credentials(username, body.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Huidig wachtwoord is onjuist",
        )
    change_password(username, body.new_password)
    return {"message": "Wachtwoord succesvol gewijzigd"}
