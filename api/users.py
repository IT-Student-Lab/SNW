# -*- coding: utf-8 -*-
"""Simple file-backed user store with hashed passwords."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from threading import Lock

from app.config import settings

_USERS_FILE = Path("users.json")
_lock = Lock()


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with PBKDF2-SHA256. Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return h.hex(), salt


def _load_users() -> dict[str, dict]:
    if _USERS_FILE.exists():
        return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_users(users: dict[str, dict]) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_FILE.write_text(
        json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _ensure_seeded() -> None:
    """If no users file exists yet, seed it from APP_USERNAME / APP_PASSWORD."""
    with _lock:
        users = _load_users()
        if users:
            return
        pw_hash, salt = _hash_password(settings.app_password)
        users[settings.app_username] = {"hash": pw_hash, "salt": salt}
        _save_users(users)


# Ensure the file is seeded on import
_ensure_seeded()


def verify_user(username: str, password: str) -> bool:
    """Verify credentials against the user store."""
    with _lock:
        users = _load_users()
    entry = users.get(username)
    if entry is None:
        # Constant-time: still hash to prevent timing attacks
        _hash_password(password, salt="dummy-salt-constant")
        return False
    h, _ = _hash_password(password, salt=entry["salt"])
    return secrets.compare_digest(h, entry["hash"])


def change_password(username: str, new_password: str) -> None:
    """Update a user's password."""
    with _lock:
        users = _load_users()
        if username not in users:
            raise KeyError(f"Gebruiker '{username}' niet gevonden")
        pw_hash, salt = _hash_password(new_password)
        users[username]["hash"] = pw_hash
        users[username]["salt"] = salt
        _save_users(users)
