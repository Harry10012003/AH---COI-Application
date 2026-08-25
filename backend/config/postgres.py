from __future__ import annotations

from dataclasses import dataclass
import os
import re


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresConfigError(ValueError):
    pass


def _positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise PostgresConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise PostgresConfigError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    database: str
    schema: str
    user: str
    password: str
    sslmode: str
    connect_timeout_sec: int
    pool_min_size: int
    pool_max_size: int
    pool_timeout_sec: int

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        config = cls(
            host=str(os.getenv("COI_PG_HOST", "") or "").strip(),
            port=_positive_int("COI_PG_PORT", 5432),
            database=str(os.getenv("COI_PG_DATABASE", "") or "").strip(),
            schema=str(os.getenv("COI_PG_SCHEMA", "ah_app") or "ah_app").strip(),
            user=str(os.getenv("COI_PG_USER", "") or "").strip(),
            password=str(os.getenv("COI_PG_PASSWORD", "") or ""),
            sslmode=str(os.getenv("COI_PG_SSLMODE", "prefer") or "prefer").strip().lower(),
            connect_timeout_sec=_positive_int("COI_PG_CONNECT_TIMEOUT_SEC", 10),
            pool_min_size=_positive_int("COI_PG_POOL_MIN_SIZE", 1, minimum=0),
            pool_max_size=_positive_int("COI_PG_POOL_MAX_SIZE", 8),
            pool_timeout_sec=_positive_int("COI_PG_POOL_TIMEOUT_SEC", 10),
        )
        config.validate()
        return config

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("COI_PG_HOST", self.host),
                ("COI_PG_DATABASE", self.database),
                ("COI_PG_USER", self.user),
                ("COI_PG_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise PostgresConfigError("Missing PostgreSQL configuration: " + ", ".join(missing))
        if not _IDENTIFIER_RE.fullmatch(self.schema):
            raise PostgresConfigError("COI_PG_SCHEMA must be a valid PostgreSQL identifier")
        if self.sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise PostgresConfigError("COI_PG_SSLMODE is unsupported")
        if self.pool_min_size > self.pool_max_size:
            raise PostgresConfigError("COI_PG_POOL_MIN_SIZE cannot exceed COI_PG_POOL_MAX_SIZE")

    def safe_summary(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "schema": self.schema,
            "user_configured": bool(self.user),
            "password_configured": bool(self.password),
            "sslmode": self.sslmode,
            "connect_timeout_sec": self.connect_timeout_sec,
            "pool_min_size": self.pool_min_size,
            "pool_max_size": self.pool_max_size,
            "pool_timeout_sec": self.pool_timeout_sec,
        }
