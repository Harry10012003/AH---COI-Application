from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import threading


ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
SESSION_TTL_HOURS = 12
_PBKDF2_ITERATIONS = 200_000


@dataclass(frozen=True)
class _Account:
    username: str
    role: str
    salt: bytes
    password_hash: str


@dataclass(frozen=True)
class _Session:
    username: str
    role: str
    expires_at: datetime


_ACCOUNTS = {
    "ah": _Account(
        username="AH",
        role=ROLE_EDITOR,
        salt=b"coi-ah-v1",
        password_hash="1bb6b72249c61f37d0cf7f0d82d8287927d6b81f275e093157e9cd4b3b154591",
    ),
    "viewer": _Account(
        username="Viewer",
        role=ROLE_VIEWER,
        salt=b"coi-viewer-v1",
        password_hash="399149f449a4cf973f83682203d17771a3ce44f1ac5005c29502202f41bf0781",
    ),
}
_SESSIONS: dict[str, _Session] = {}
_SESSION_LOCK = threading.Lock()


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    ).hex()


def authenticate(username: object, password: object) -> dict | None:
    account = _ACCOUNTS.get(str(username or "").strip().casefold())
    if account is None:
        # Keep the work factor similar for unknown users.
        _password_hash(str(password or ""), b"coi-unknown-v1")
        return None
    candidate = _password_hash(str(password or ""), account.salt)
    if not hmac.compare_digest(candidate, account.password_hash):
        return None
    return {"username": account.username, "role": account.role}


def create_session(user: dict) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    session = _Session(
        username=str(user.get("username") or ""),
        role=str(user.get("role") or ""),
        expires_at=expires_at,
    )
    with _SESSION_LOCK:
        _purge_expired_locked()
        _SESSIONS[token] = session
    return token, expires_at


def resolve_session(token: object) -> dict | None:
    token_text = str(token or "").strip()
    if not token_text:
        return None
    now = datetime.now(timezone.utc)
    with _SESSION_LOCK:
        session = _SESSIONS.get(token_text)
        if session is None:
            return None
        if session.expires_at <= now:
            _SESSIONS.pop(token_text, None)
            return None
    return {
        "username": session.username,
        "role": session.role,
        "expires_at": session.expires_at.isoformat(),
    }


def revoke_session(token: object) -> None:
    token_text = str(token or "").strip()
    if not token_text:
        return
    with _SESSION_LOCK:
        _SESSIONS.pop(token_text, None)


def bearer_token(authorization: object) -> str:
    value = str(authorization or "").strip()
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return ""
    return token.strip()


def _purge_expired_locked() -> None:
    now = datetime.now(timezone.utc)
    expired = [token for token, session in _SESSIONS.items() if session.expires_at <= now]
    for token in expired:
        _SESSIONS.pop(token, None)


def clear_sessions_for_tests() -> None:
    with _SESSION_LOCK:
        _SESSIONS.clear()
