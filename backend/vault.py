"""Symmetric encryption for stored secrets (device/agent passwords, agent keys)."""
import base64
import hashlib
import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    digest = hashlib.sha256(os.environ["JWT_SECRET"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        return ""
