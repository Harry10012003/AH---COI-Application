from __future__ import annotations

import re


class ReadOnlySqlViolation(RuntimeError):
    """Raised before an upstream statement can mutate source data."""


_LEADING_COMMENTS = re.compile(
    r"\A(?:\s+|--[^\r\n]*(?:\r?\n|\Z)|/\*.*?\*/)*",
    re.DOTALL,
)
_READ_ONLY_PREFIXES = {"SELECT", "WITH"}
_FORBIDDEN_TOKENS = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE|EXEC|EXECUTE|INTO)\b",
    re.IGNORECASE,
)


def assert_read_only_sql(operation: object) -> None:
    """Reject non-query SQL before it reaches an upstream SQL cursor."""

    if not isinstance(operation, str):
        raise ReadOnlySqlViolation("Upstream SQL must be a text SELECT statement")
    sql = _LEADING_COMMENTS.sub("", operation).strip()
    first_token = (sql.split(None, 1)[0] if sql else "").upper()
    if first_token not in _READ_ONLY_PREFIXES:
        raise ReadOnlySqlViolation(
            f"Upstream SQL is read-only; {first_token or 'empty statement'} is not allowed"
        )
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statements) != 1:
        raise ReadOnlySqlViolation("Multiple upstream SQL statements are not allowed")
    scrubbed = re.sub(r"'(?:''|[^'])*'", "''", sql)
    # SQL Server identifiers such as [Create Date] may contain words that are
    # mutation verbs; they are names, not executable tokens.
    scrubbed = re.sub(r"\[[^\]]+\]", "[]", scrubbed)
    if _FORBIDDEN_TOKENS.search(scrubbed):
        raise ReadOnlySqlViolation("Upstream SQL contains a mutation token")
