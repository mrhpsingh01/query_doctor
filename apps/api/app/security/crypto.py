"""Symmetric encryption for stored secrets (target-database passwords) using Fernet (AES-128-CBC + HMAC)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class DecryptionError(ValueError):
    pass


def _fernet(key: str | None) -> Fernet:
    key = key or get_settings().secret_key
    if not key:  # development fallback only; production must set QD_SECRET_KEY
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"querydoctor-dev-only").digest()))
    return Fernet(key.encode())


def encrypt(plaintext: str, *, key: str | None = None) -> str:
    return _fernet(key).encrypt(plaintext.encode()).decode()


def decrypt(token: str, *, key: str | None = None) -> str:
    try:
        return _fernet(key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("stored secret could not be decrypted (wrong key or tampered data)") from exc
