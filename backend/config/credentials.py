from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class ResolvedCredential:
    username: str = ""
    password: str = ""
    source: str = "missing"
    target: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)


def _decode_credential_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.rstrip("\x00")
    if not isinstance(value, (bytes, bytearray)):
        return str(value).rstrip("\x00")

    raw = bytes(value)
    # pywin32 commonly returns UTF-16LE bytes, but older credential entries can
    # be UTF-8. Prefer the encoding that produces a sensible non-NUL value.
    for encoding in ("utf-16-le", "utf-8"):
        try:
            decoded = raw.decode(encoding).rstrip("\x00")
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return ""


def read_windows_credential(target: str) -> ResolvedCredential:
    target_name = str(target or "").strip()
    if not target_name or os.name != "nt":
        return ResolvedCredential(target=target_name)
    try:
        import win32cred

        payload = win32cred.CredRead(target_name, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        return ResolvedCredential(target=target_name)
    return ResolvedCredential(
        username=str(payload.get("UserName") or "").strip(),
        password=_decode_credential_blob(payload.get("CredentialBlob")),
        source="windows-credential-manager",
        target=target_name,
    )


def resolve_credential(
    *,
    user_env: str,
    password_env: str,
    target_env: str,
    default_target: str,
    default_user: str = "",
) -> ResolvedCredential:
    """Resolve a login without ever placing a password in source code.

    Environment variables take precedence only when both halves are present.
    A username from one source is never paired with a password from another.
    """

    env_user = str(os.getenv(user_env, "") or "").strip()
    env_password = str(os.getenv(password_env, "") or "")
    target = str(os.getenv(target_env, default_target) or "").strip()
    stored = read_windows_credential(target)

    if env_user and env_password:
        username = env_user
        password = env_password
        source = "environment"
    elif stored.configured:
        username = stored.username
        password = stored.password
        source = stored.source
    else:
        username = str(default_user or "").strip()
        password = ""
        source = "invalid-partial-environment" if env_user or env_password else "missing"
    return ResolvedCredential(
        username=username,
        password=password,
        source=source,
        target=target,
    )
