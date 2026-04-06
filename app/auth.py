"""
Auth utilities: password hashing (scrypt, stdlib only) and session helpers.
"""

import hashlib
import os
import secrets

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.models import AppSettings


# ---------------------------------------------------------------------------
# Password hashing — uses Python stdlib scrypt, no extra dependencies
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, key_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App settings helpers
# ---------------------------------------------------------------------------

def get_app_settings(db: Session) -> AppSettings | None:
    return db.query(AppSettings).first()


def is_password_set(db: Session) -> bool:
    s = get_app_settings(db)
    return s is not None and bool(s.password_hash)


def set_password(db: Session, password: str) -> None:
    s = db.query(AppSettings).first()
    if s:
        s.password_hash = hash_password(password)
    else:
        db.add(AppSettings(id=1, password_hash=hash_password(password)))
    db.commit()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True


def login_session(request: Request) -> None:
    request.session["logged_in"] = True


def logout_session(request: Request) -> None:
    request.session.clear()
