from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import threading
import uuid
from typing import Any, Iterator

from backend.config.postgres import PostgresConfig, PostgresConfigError

try:
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised through safe status/config paths
    sql = None
    dict_row = None
    Jsonb = None
    ConnectionPool = None


class CoiPostgresError(RuntimeError):
    pass


class CoiPostgresUnavailable(CoiPostgresError):
    pass


class CoiPostgresConflict(CoiPostgresError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    ui_key: str
    db_name: str | None
    numeric: bool = False


FIELD_SPECS = (
    FieldSpec("BRAND", "brand"),
    FieldSpec("GO#", None),
    FieldSpec("PPO", "ppo_no"),
    FieldSpec("Type", "fabric_type"),
    FieldSpec("COLOR_CODE", "color_code"),
    FieldSpec("COLOR_DESC", "color_desc"),
    FieldSpec("FABRIC COLOR (For piecing only)", "fabric_color"),
    FieldSpec("JOB ORDER NO", "job_order_no"),
    FieldSpec("LOT", "lot_no"),
    FieldSpec("SIZE", "size_code"),
    FieldSpec("- %", "minus_pct", True),
    FieldSpec("+%", "plus_pct", True),
    FieldSpec("Qty (pcs)", "qty_pcs", True),
    FieldSpec("BUYER_PO_DEL_DATE", "buyer_po_delivery_date"),
    FieldSpec("Net YY", "net_yy", True),
    FieldSpec("PPO YY", "ppo_yy", True),
    FieldSpec("Marker YY", "marker_yy", True),
    FieldSpec("Required Q'ty (Yds)", "required_qty_yds", True),
    FieldSpec("Rcv Q'ty (PPO)", "received_qty_ppo", True),
    FieldSpec("On The Way Q'ty (Yds)", "on_the_way_qty_yds", True),
    FieldSpec("Allocate Q'ty (Yds)", "allocate_qty_yds", True),
    FieldSpec("Shortage Q'ty (Yds)", "shortage_qty_yds", True),
    FieldSpec("AH Allocate Q'ty (yds)", "ah_allocate_qty_yds", True),
    FieldSpec("Allocate %", "allocate_pct", True),
    FieldSpec("ETD Fabric", "etd_fabric"),
    FieldSpec("User Remark", "user_remark"),
    FieldSpec("PPO Order Total (Yds)", "ppo_order_total_yds", True),
    FieldSpec("SAMPLE STATUS", "sample_status"),
)

EDITABLE_FIELDS = {"PPO", "AH Allocate Q'ty (yds)", "User Remark"}
_ROW_NAMESPACE = uuid.UUID("57e9bed4-6720-4e0d-aa1f-7de3e776cd0a")
_POOL_LOCK = threading.Lock()
_POOL = None
_POOL_CONFIG: PostgresConfig | None = None


def coi_columns() -> list[dict[str, Any]]:
    metadata = {
        "PPO": {"editable": True, "source": "PostgreSQL/UI"},
        "AH Allocate Q'ty (yds)": {"editable": True, "source": "PostgreSQL/UI"},
        "User Remark": {"editable": True, "source": "PostgreSQL/UI"},
    }
    return [
        {
            "key": spec.ui_key,
            "label": "PPO Q'ty" if spec.ui_key == "PPO Order Total (Yds)" else spec.ui_key,
            "editable": bool(metadata.get(spec.ui_key, {}).get("editable")),
            "source": metadata.get(spec.ui_key, {}).get("source", "PostgreSQL"),
        }
        for spec in FIELD_SPECS
    ]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _go(value: object) -> str:
    return str(value or "").strip().upper()


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _text_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _numeric_value(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise CoiPostgresError("Boolean is not a valid COI numeric value")
    raw = str(value).replace(",", "").replace("%", "").strip()
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise CoiPostgresError(f"Invalid numeric COI value: {value}") from exc
    if not number.is_finite():
        raise CoiPostgresError("COI numeric values must be finite")
    return number


def _row_uuid(go_no: str, raw_key: object) -> uuid.UUID:
    text = str(raw_key or "").strip()
    if not text:
        raise CoiPostgresError("Every COI row must have a stable _row_key before ISSUE")
    try:
        return uuid.UUID(text)
    except ValueError:
        return uuid.uuid5(_ROW_NAMESPACE, f"{go_no}|{text}")


def normalize_sheet_rows(payload: dict) -> list[dict[str, Any]]:
    go_no = _go(payload.get("go"))
    if not go_no:
        raise CoiPostgresError("GO number required")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[uuid.UUID] = set()
    source_rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    for row_no, source in enumerate(source_rows, start=1):
        if not isinstance(source, dict):
            continue
        row_id = _row_uuid(go_no, source.get("_row_key"))
        if row_id in seen_ids:
            raise CoiPostgresError(f"Duplicate COI row identity at row {row_no}")
        seen_ids.add(row_id)
        item: dict[str, Any] = {
            "go_no": go_no,
            "internal_row_id": row_id,
            "row_no": row_no,
        }
        for spec in FIELD_SPECS:
            if spec.db_name is None:
                continue
            raw_value = source.get(spec.ui_key)
            item[spec.db_name] = _numeric_value(raw_value) if spec.numeric else _text_value(raw_value)
        normalized.append(item)
    if not normalized:
        raise CoiPostgresError("Cannot ISSUE COI because the sheet has no rows")
    return normalized


def _public_row(db_row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"GO#": _go(db_row.get("go_no"))}
    for spec in FIELD_SPECS:
        if spec.db_name is None:
            continue
        result[spec.ui_key] = _json_value(db_row.get(spec.db_name))
    result["_row_key"] = str(db_row.get("internal_row_id") or "")
    result["_last_modified_at"] = _json_value(db_row.get("last_modified_at"))
    return result


def _comparable(row: dict[str, Any]) -> dict[str, Any]:
    return {
        spec.db_name: _json_value(row.get(spec.db_name))
        for spec in FIELD_SPECS
        if spec.db_name is not None
    }


def diff_rows(existing_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing = {str(row["internal_row_id"]): row for row in existing_rows}
    incoming = {str(row["internal_row_id"]): row for row in incoming_rows}
    added_ids = incoming.keys() - existing.keys()
    removed_ids = existing.keys() - incoming.keys()
    changes: list[dict[str, Any]] = []
    changed_ids: set[str] = set()

    for row_id in sorted(added_ids):
        row = incoming[row_id]
        changes.append({
            "internal_row_id": row_id,
            "row_no": row.get("row_no"),
            "ppo_no": _json_value(row.get("ppo_no")),
            "field": "__row__",
            "old_value": None,
            "new_value": _comparable(row),
        })
    for row_id in sorted(removed_ids):
        row = existing[row_id]
        changes.append({
            "internal_row_id": row_id,
            "row_no": row.get("row_no"),
            "ppo_no": _json_value(row.get("ppo_no")),
            "field": "__row__",
            "old_value": _comparable(row),
            "new_value": None,
        })
    for row_id in sorted(existing.keys() & incoming.keys()):
        old = existing[row_id]
        new = incoming[row_id]
        for spec in FIELD_SPECS:
            if spec.db_name is None:
                continue
            old_value = _json_value(old.get(spec.db_name))
            new_value = _json_value(new.get(spec.db_name))
            if old_value == new_value:
                continue
            changed_ids.add(row_id)
            changes.append({
                "internal_row_id": row_id,
                "row_no": new.get("row_no"),
                "ppo_no": _json_value(new.get("ppo_no") or old.get("ppo_no")),
                "field": spec.db_name,
                "old_value": old_value,
                "new_value": new_value,
            })
    return {
        "added_row_count": len(added_ids),
        "removed_row_count": len(removed_ids),
        "changed_row_count": len(changed_ids),
        "changes": changes,
        "has_changes": bool(added_ids or removed_ids or changed_ids),
    }


def _require_driver() -> None:
    if ConnectionPool is None or sql is None:
        raise CoiPostgresUnavailable(
            "PostgreSQL support is not installed; install requirements.txt in the active virtual environment"
        )


def _pool():
    global _POOL, _POOL_CONFIG
    _require_driver()
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        raise CoiPostgresUnavailable(str(exc)) from exc
    with _POOL_LOCK:
        if _POOL is not None and _POOL_CONFIG == config:
            return _POOL
        if _POOL is not None:
            _POOL.close()
        _POOL = ConnectionPool(
            conninfo="",
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
            timeout=config.pool_timeout_sec,
            kwargs={
                "host": config.host,
                "port": config.port,
                "dbname": config.database,
                "user": config.user,
                "password": config.password,
                "sslmode": config.sslmode,
                "connect_timeout": config.connect_timeout_sec,
                "row_factory": dict_row,
            },
            open=True,
        )
        _POOL_CONFIG = config
        return _POOL


def close_postgres_pool() -> None:
    global _POOL, _POOL_CONFIG
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
        _POOL = None
        _POOL_CONFIG = None


@contextmanager
def _connection() -> Iterator[Any]:
    pool = _pool()
    try:
        with pool.connection() as conn:
            yield conn
    except CoiPostgresError:
        raise
    except Exception as exc:
        if str(getattr(exc, "sqlstate", "") or "") == "42P01":
            raise CoiPostgresUnavailable("PostgreSQL migration has not been applied") from exc
        if str(getattr(exc, "sqlstate", "") or "").startswith("23"):
            raise CoiPostgresError("PostgreSQL rejected invalid COI data") from exc
        raise CoiPostgresUnavailable("PostgreSQL ISSUE store is temporarily unavailable") from exc


class CoiPostgresRepository:
    def __init__(self, schema: str | None = None):
        self._schema_override = schema

    @property
    def schema(self) -> str:
        if self._schema_override:
            return self._schema_override
        try:
            return PostgresConfig.from_env().schema
        except PostgresConfigError as exc:
            raise CoiPostgresUnavailable(str(exc)) from exc

    def _table(self, name: str):
        return sql.Identifier(self.schema, name)

    def status(self) -> dict[str, Any]:
        try:
            config = PostgresConfig.from_env()
        except PostgresConfigError as exc:
            return {"ok": False, "connected": False, "configured": False, "error": str(exc)}
        summary = config.safe_summary()
        try:
            with _connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT current_database() AS database, to_regclass(%s) AS current_issue_table",
                        (f"{config.schema}.current_issue",),
                    )
                    row = cur.fetchone() or {}
            migrated = bool(row.get("current_issue_table"))
            return {
                "ok": migrated,
                "connected": True,
                "configured": True,
                "migration_ready": migrated,
                "connection": summary,
                "error": "" if migrated else "PostgreSQL migration has not been applied",
            }
        except CoiPostgresError as exc:
            return {
                "ok": False,
                "connected": False,
                "configured": True,
                "migration_ready": False,
                "connection": summary,
                "error": str(exc),
            }

    def exists(self, go_no: object) -> bool:
        go_key = _go(go_no)
        if not go_key:
            return False
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT 1 FROM {} WHERE go_no = %s").format(self._table("current_issue")), (go_key,))
            return cur.fetchone() is not None

    def _load_locked(self, cur, go_no: str, *, lock: bool) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        lock_sql = sql.SQL(" FOR UPDATE") if lock else sql.SQL("")
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE go_no = %s").format(self._table("current_issue")) + lock_sql,
            (go_no,),
        )
        header = cur.fetchone()
        if header is None:
            return None, []
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE go_no = %s ORDER BY row_no").format(self._table("current_issue_row")),
            (go_no,),
        )
        return dict(header), [dict(row) for row in cur.fetchall()]

    def load_current(self, go_no: object) -> dict[str, Any] | None:
        go_key = _go(go_no)
        if not go_key:
            return None
        with _connection() as conn, conn.cursor() as cur:
            header, rows = self._load_locked(cur, go_key, lock=False)
        if header is None:
            return None
        public_rows = [_public_row(row) for row in rows]
        metadata = {key: _json_value(value) for key, value in header.items()}
        return {
            "ok": True,
            "go": go_key,
            "columns": coi_columns(),
            "rows": public_rows,
            "row_count": len(public_rows),
            "storage_source": "postgres",
            "issue": metadata,
            "sheet": {"columns": coi_columns(), "rows": public_rows},
        }

    def _advisory_lock(self, cur, go_no: str) -> None:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (go_no,))

    def _insert_audit(
        self,
        cur,
        *,
        go_no: str,
        action: str,
        issue_revision: int,
        sync_revision: int,
        actor: str,
        diff: dict[str, Any],
        happened_at: datetime,
    ) -> None:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (
                    go_no, action, issue_revision, sync_revision, issued_by, issued_at,
                    added_row_count, removed_row_count, changed_row_count, changes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(self._table("issue_audit_log")),
            (
                go_no,
                action,
                issue_revision,
                sync_revision,
                actor,
                happened_at,
                diff["added_row_count"],
                diff["removed_row_count"],
                diff["changed_row_count"],
                Jsonb(diff["changes"]),
            ),
        )

    def _replace_rows(self, cur, go_no: str, incoming: list[dict[str, Any]], existing: list[dict[str, Any]], now: datetime) -> None:
        existing_by_id = {str(row["internal_row_id"]): row for row in existing}
        db_specs = [spec for spec in FIELD_SPECS if spec.db_name is not None]
        value_columns = ["go_no", "internal_row_id", "row_no", *[spec.db_name for spec in db_specs], "last_modified_at"]
        assignments = ["row_no = EXCLUDED.row_no"] + [
            f"{spec.db_name} = EXCLUDED.{spec.db_name}" for spec in db_specs
        ] + ["last_modified_at = EXCLUDED.last_modified_at"]
        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (go_no, internal_row_id) DO UPDATE SET {}"
        ).format(
            self._table("current_issue_row"),
            sql.SQL(", ").join(map(sql.Identifier, value_columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in value_columns),
            sql.SQL(", ").join(sql.SQL(item) for item in assignments),
        )
        # Move existing row numbers out of the target range before UPSERT.
        # Keep the temporary values positive because the schema deliberately
        # enforces row_no >= 1.
        row_no_offset = max(
            [len(incoming), *(int(row.get("row_no") or 0) for row in existing)],
            default=0,
        ) + 1
        cur.execute(
            sql.SQL("UPDATE {} SET row_no = row_no + %s WHERE go_no = %s").format(
                self._table("current_issue_row")
            ),
            (row_no_offset, go_no),
        )
        params = []
        incoming_ids: list[uuid.UUID] = []
        for row in incoming:
            row_id = row["internal_row_id"]
            incoming_ids.append(row_id)
            previous = existing_by_id.get(str(row_id))
            changed = previous is None or _comparable(previous) != _comparable(row)
            modified_at = now if changed else previous.get("last_modified_at") or now
            params.append(tuple(row.get(column) for column in value_columns[:-1]) + (modified_at,))
        cur.executemany(query, params)
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE go_no = %s AND NOT (internal_row_id = ANY(%s::uuid[]))").format(
                self._table("current_issue_row")
            ),
            (go_no, [str(value) for value in incoming_ids]),
        )

    def _write_payload(
        self,
        cur,
        payload: dict,
        *,
        actor: str,
        mode: str,
        action: str,
        expected_updated_at: object = None,
    ) -> dict[str, Any]:
        go_no = _go(payload.get("go"))
        incoming = normalize_sheet_rows({**payload, "go": go_no})
        self._advisory_lock(cur, go_no)
        header, existing = self._load_locked(cur, go_no, lock=True)
        if mode != "ISSUE" and header is None:
            raise CoiPostgresConflict("GO has not been issued yet")
        if expected_updated_at is not None and header is not None:
            current_token = str(_json_value(header.get("updated_at")) or "")
            if current_token != str(expected_updated_at):
                raise CoiPostgresConflict("The issued GO changed after preview; refresh the preview and try again")

        diff = diff_rows(existing, incoming)
        now = _now()
        if header is None:
            issue_revision = 1
            sync_revision = 0
            effective_action = "INITIAL_ISSUE"
            cur.execute(
                sql.SQL(
                    "INSERT INTO {} (go_no, issue_revision, sync_revision, issued_at, issued_by, row_count, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(self._table("current_issue")),
                (go_no, issue_revision, sync_revision, now, actor, len(incoming), now, now),
            )
        elif not diff["has_changes"]:
            issue_revision = int(header["issue_revision"])
            sync_revision = int(header["sync_revision"])
            effective_action = "NO_CHANGE"
        elif mode == "ISSUE":
            issue_revision = int(header["issue_revision"]) + 1
            sync_revision = int(header["sync_revision"])
            effective_action = "REISSUE"
            cur.execute(
                sql.SQL(
                    "UPDATE {} SET issue_revision = %s, issued_at = %s, issued_by = %s, row_count = %s, updated_at = %s WHERE go_no = %s"
                ).format(self._table("current_issue")),
                (issue_revision, now, actor, len(incoming), now, go_no),
            )
        else:
            issue_revision = int(header["issue_revision"])
            sync_revision = int(header["sync_revision"]) + 1
            effective_action = action
            cur.execute(
                sql.SQL(
                    "UPDATE {} SET sync_revision = %s, last_synced_at = %s, last_synced_by = %s, "
                    "row_count = %s, updated_at = %s WHERE go_no = %s"
                ).format(self._table("current_issue")),
                (sync_revision, now, actor, len(incoming), now, go_no),
            )

        if header is None or diff["has_changes"]:
            self._replace_rows(cur, go_no, incoming, existing, now)
        self._insert_audit(
            cur,
            go_no=go_no,
            action=effective_action,
            issue_revision=issue_revision,
            sync_revision=sync_revision,
            actor=actor,
            diff=diff,
            happened_at=now,
        )
        return {
            "ok": True,
            "go": go_no,
            "action": effective_action,
            "issue_revision": issue_revision,
            "sync_revision": sync_revision,
            "row_count": len(incoming),
            "changed": bool(diff["has_changes"]),
            "diff": {key: value for key, value in diff.items() if key != "changes"},
            "updated_at": now.isoformat(),
            "storage_source": "postgres",
        }

    def issue(self, payload: dict, *, actor: str) -> dict[str, Any]:
        with _connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                return self._write_payload(cur, payload, actor=actor, mode="ISSUE", action="REISSUE")

    def sync_payload(
        self,
        payload: dict,
        *,
        actor: str,
        action: str,
        expected_updated_at: object = None,
    ) -> dict[str, Any]:
        if action not in {"AUTO_SYNC_EDIT", "SOURCE_REFRESH_APPLIED"}:
            raise CoiPostgresError("Unsupported PostgreSQL sync action")
        with _connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                return self._write_payload(
                    cur,
                    payload,
                    actor=actor,
                    mode="SYNC",
                    action=action,
                    expected_updated_at=expected_updated_at,
                )

    def apply_edits(self, go_no: object, edits: list[dict], *, actor: str) -> dict[str, Any]:
        go_key = _go(go_no)
        with _connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                self._advisory_lock(cur, go_key)
                header, rows = self._load_locked(cur, go_key, lock=True)
                if header is None:
                    raise CoiPostgresConflict("GO has not been issued yet")
                by_id = {str(row["internal_row_id"]): row for row in rows}
                for edit in edits:
                    if not isinstance(edit, dict):
                        continue
                    field = str(edit.get("field") or "").strip()
                    if field not in EDITABLE_FIELDS:
                        raise CoiPostgresError(f"Field is not editable: {field}")
                    row_id = str(_row_uuid(go_key, edit.get("row_key")))
                    row = by_id.get(row_id)
                    if row is None:
                        raise CoiPostgresConflict("The edited COI row no longer exists")
                    spec = next(item for item in FIELD_SPECS if item.ui_key == field)
                    row[spec.db_name] = _numeric_value(edit.get("value")) if spec.numeric else _text_value(edit.get("value"))
                payload = {
                    "go": go_key,
                    "rows": [_public_row(row) for row in sorted(rows, key=lambda item: item["row_no"])],
                }
                return self._write_payload(cur, payload, actor=actor, mode="SYNC", action="AUTO_SYNC_EDIT")

    def latest_feed(
        self,
        *,
        go: object = "",
        ppo: object = "",
        jo: object = "",
        color_code: object = "",
        limit: object = 5000,
        offset: object = 0,
    ) -> dict[str, Any]:
        try:
            page_limit = max(1, min(10_000, int(limit)))
            page_offset = max(0, int(offset))
        except (TypeError, ValueError):
            raise CoiPostgresError("Invalid feed pagination")
        filters = {"go_no": _go(go), "ppo_no": _go(ppo), "job_order_no": _go(jo), "color_code": _go(color_code)}
        conditions = []
        params: list[Any] = []
        for column, value in filters.items():
            if value:
                conditions.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                params.append(value)
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        view = self._table("v_current_issue_rows")
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COUNT(*) AS total_rows, COUNT(DISTINCT go_no) AS go_count FROM {} ").format(view) + where, params)
            counts = cur.fetchone() or {}
            cur.execute(
                sql.SQL("SELECT * FROM {} ").format(view) + where + sql.SQL(" ORDER BY go_no, row_no LIMIT %s OFFSET %s"),
                [*params, page_limit, page_offset],
            )
            source_rows = [dict(row) for row in cur.fetchall()]
        rows = []
        for source in source_rows:
            output = _public_row(source)
            output.pop("_row_key", None)
            output.pop("_last_modified_at", None)
            output["ISSUE_AT"] = _json_value(source.get("issued_at"))
            output["ISSUE_SYNC_AT"] = _json_value(source.get("last_synced_at") or source.get("issued_at"))
            output["ISSUE_VERSION"] = int(source.get("issue_revision") or 0)
            output["SYNC_VERSION"] = int(source.get("sync_revision") or 0)
            rows.append(output)
        total_rows = int(counts.get("total_rows") or 0)
        return {
            "ok": True,
            "feed": "cutting-coi-latest",
            "mode": "postgres_current_issue_per_go",
            "columns": coi_columns(),
            "rows": rows,
            "filters": {"go": filters["go_no"], "ppo": filters["ppo_no"], "jo": filters["job_order_no"], "color_code": filters["color_code"]},
            "pagination": {
                "limit": page_limit,
                "offset": page_offset,
                "returned": len(rows),
                "total_rows": total_rows,
                "has_more": page_offset + len(rows) < total_rows,
            },
            "go_count": int(counts.get("go_count") or 0),
            "storage_source": "postgres",
        }


repository = CoiPostgresRepository()


def postgres_status() -> dict[str, Any]:
    return repository.status()


def load_current_issue(go_no: object) -> dict[str, Any] | None:
    return repository.load_current(go_no)


def current_issue_exists(go_no: object) -> bool:
    return repository.exists(go_no)


def issue_current_sheet(payload: dict, *, actor: str) -> dict[str, Any]:
    return repository.issue(payload, actor=actor)


def sync_current_sheet(
    payload: dict,
    *,
    actor: str,
    action: str,
    expected_updated_at: object = None,
) -> dict[str, Any]:
    return repository.sync_payload(
        payload,
        actor=actor,
        action=action,
        expected_updated_at=expected_updated_at,
    )


def apply_current_edits(go_no: object, edits: list[dict], *, actor: str) -> dict[str, Any]:
    return repository.apply_edits(go_no, edits, actor=actor)


def get_latest_current_feed(**filters) -> dict[str, Any]:
    return repository.latest_feed(**filters)
