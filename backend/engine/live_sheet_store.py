from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from backend.sources import CACHE_DIR

LIVE_SHEET_STORE_DB = Path(
    os.getenv("LIVE_SHEET_STORE_DB", str(CACHE_DIR / "live_sheet_store_v56.db"))
)


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


@contextmanager
def _connect():
    LIVE_SHEET_STORE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LIVE_SHEET_STORE_DB), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_live_sheet_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_sheets (
                go_no TEXT PRIMARY KEY,
                factory_code TEXT NOT NULL DEFAULT '',
                style_no TEXT NOT NULL DEFAULT '',
                style_desc TEXT NOT NULL DEFAULT '',
                source_modify_date TEXT NOT NULL DEFAULT '',
                row_count INTEGER NOT NULL DEFAULT 0,
                cache_state TEXT NOT NULL DEFAULT '',
                total_required_qty REAL NOT NULL DEFAULT 0,
                total_received_qty REAL NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                built_from TEXT NOT NULL DEFAULT 'ui',
                build_started_ns INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS live_sheet_rows (
                go_no TEXT NOT NULL,
                row_key TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                ppo_no TEXT NOT NULL DEFAULT '',
                lot_no INTEGER NOT NULL DEFAULT 0,
                jo_no TEXT NOT NULL DEFAULT '',
                fabric_type TEXT NOT NULL DEFAULT '',
                color_code TEXT NOT NULL DEFAULT '',
                fabric_combo TEXT NOT NULL DEFAULT '',
                row_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_key)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_live_sheet_rows_go ON live_sheet_rows(go_no, row_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_live_sheets_state ON live_sheets(cache_state, updated_at)")
        columns = {
            str(row["name"] or "").strip().lower()
            for row in conn.execute("PRAGMA table_info(live_sheets)").fetchall()
        }
        if "build_started_ns" not in columns:
            conn.execute(
                "ALTER TABLE live_sheets ADD COLUMN build_started_ns INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()


def _go_key(go: object) -> str:
    return str(go or "").strip().upper()


def _to_float(value: object) -> float:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _payload_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    profile = payload.get("cache_profile") if isinstance(payload.get("cache_profile"), dict) else {}
    head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
    row_count = int(payload.get("row_count") or len(rows) or summary.get("rows") or 0)
    total_required = _to_float(summary.get("total_required_qty"))
    total_received = _to_float(summary.get("total_received_qty"))
    return {
        "factory_code": str(payload.get("factory_code") or head.get("factory_code") or ""),
        "style_no": str(payload.get("style_no") or head.get("style_no") or ""),
        "style_desc": str(payload.get("style_desc") or head.get("style_desc") or ""),
        "source_modify_date": str(head.get("modify_date") or head.get("create_date") or ""),
        "row_count": row_count,
        "cache_state": str(profile.get("state") or "").strip().upper(),
        "total_required_qty": total_required,
        "total_received_qty": total_received,
        "has_received_gap": row_count > 0 and total_required > 0 and total_received <= 0,
    }


def _row_storage(row: dict[str, Any]) -> dict[str, Any]:
    storage = row.get("_storage") if isinstance(row.get("_storage"), dict) else {}
    return {
        "ppo_no": str(storage.get("ppo_no") or row.get("PPO") or "").strip().upper(),
        "lot_no": int(_to_float(storage.get("lot_no") or row.get("LOT") or row.get("Lot"))),
        "jo_no": str(storage.get("jo_no") or row.get("JOB ORDER NO") or row.get("JO") or "").strip().upper(),
        "fabric_type": str(storage.get("fabric_type") or row.get("Type") or "").strip().upper(),
        "color_code": str(storage.get("color_code") or row.get("COLOR_CODE") or "").strip().upper(),
        "fabric_combo": str(storage.get("fabric_combo") or row.get("FABRIC_COMBO") or "").strip(),
    }


def save_live_sheet_payload(go: str, payload: dict[str, Any], built_from: str = "ui-live") -> bool:
    go_no = _go_key(go)
    if not go_no or not isinstance(payload, dict) or not payload.get("ok"):
        return False
    ensure_live_sheet_store()
    metrics = _payload_metrics(payload)
    updated_at = _now()
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    incoming_snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    build_started_ns = int(incoming_snapshot.get("build_started_ns") or time.time_ns())
    with _connect() as conn:
        payload_for_store = dict(payload)
        snapshot = dict(payload_for_store.get("snapshot") or {})
        snapshot.update(
            {
                "served_from_sheet_store": False,
                "sheet_store_updated_at": updated_at,
                "sheet_store_db": str(LIVE_SHEET_STORE_DB),
                "build_started_ns": build_started_ns,
            }
        )
        payload_for_store["snapshot"] = snapshot
        cursor = conn.execute(
            """
            INSERT INTO live_sheets (
                go_no, factory_code, style_no, style_desc, source_modify_date,
                row_count, cache_state, total_required_qty, total_received_qty,
                payload_json, updated_at, built_from, build_started_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                factory_code = excluded.factory_code,
                style_no = excluded.style_no,
                style_desc = excluded.style_desc,
                source_modify_date = excluded.source_modify_date,
                row_count = excluded.row_count,
                cache_state = excluded.cache_state,
                total_required_qty = excluded.total_required_qty,
                total_received_qty = excluded.total_received_qty,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                built_from = excluded.built_from,
                build_started_ns = excluded.build_started_ns
            WHERE excluded.build_started_ns >= live_sheets.build_started_ns
              AND (
                    COALESCE(live_sheets.source_modify_date, '') = ''
                 OR COALESCE(excluded.source_modify_date, '') >= COALESCE(live_sheets.source_modify_date, '')
              )
            """,
            (
                go_no,
                metrics["factory_code"],
                metrics["style_no"],
                metrics["style_desc"],
                metrics["source_modify_date"],
                metrics["row_count"],
                metrics["cache_state"],
                metrics["total_required_qty"],
                metrics["total_received_qty"],
                json.dumps(payload_for_store, ensure_ascii=False),
                updated_at,
                built_from,
                build_started_ns,
            ),
        )
        if int(cursor.rowcount or 0) <= 0:
            return False
        conn.execute("DELETE FROM live_sheet_rows WHERE go_no = ?", (go_no,))
        row_params = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            row_key = str(row.get("_row_key") or "").strip()
            if not row_key:
                row_key = f"{go_no}:{index}"
            storage = _row_storage(row)
            row_params.append(
                (
                    go_no,
                    row_key,
                    index,
                    storage["ppo_no"],
                    storage["lot_no"],
                    storage["jo_no"],
                    storage["fabric_type"],
                    storage["color_code"],
                    storage["fabric_combo"],
                    json.dumps(row, ensure_ascii=False),
                    updated_at,
                )
            )
        if row_params:
            conn.executemany(
                """
                INSERT INTO live_sheet_rows (
                    go_no, row_key, row_index, ppo_no, lot_no, jo_no,
                    fabric_type, color_code, fabric_combo, row_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row_params,
            )
        conn.commit()
    return True


def load_live_sheet_payload(go: str) -> dict[str, Any] | None:
    go_no = _go_key(go)
    if not go_no:
        return None
    ensure_live_sheet_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload_json, updated_at, built_from FROM live_sheets WHERE go_no = ?",
            (go_no,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or ""))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    payload.setdefault("snapshot", {})
    payload["snapshot"].update(
        {
            "served_from_snapshot": True,
            "served_from_sheet_store": True,
            "sheet_store_updated_at": str(row["updated_at"] or ""),
            "built_from": str(row["built_from"] or "sheet-store"),
        }
    )
    payload.setdefault("sources", {})["sheet_store"] = str(LIVE_SHEET_STORE_DB)
    return payload


def patch_live_sheet_payload_edits(go: str, edits: list[dict[str, Any]]) -> bool:
    go_no = _go_key(go)
    if not go_no or not edits:
        return False
    payload = load_live_sheet_payload(go_no)
    if not payload:
        return False
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    rows_by_key = {
        str(row.get("_row_key") or "").strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("_row_key") or "").strip()
    }
    changed = False
    for edit in edits:
        row_key = str(edit.get("row_key") or "").strip()
        field = str(edit.get("field") or "").strip()
        if not row_key or field not in {"ETD Fabric", "User Remark", "Remark"}:
            continue
        row = rows_by_key.get(row_key)
        if row is None:
            continue
        value = str(edit.get("value") or "").strip()
        if field == "Remark":
            field = "User Remark"
        row[field] = value
        changed = True
    if not changed:
        return False
    return save_live_sheet_payload(go_no, payload, built_from="edit-patch")


def delete_live_sheet_payload(go: str) -> None:
    go_no = _go_key(go)
    if not go_no:
        return
    ensure_live_sheet_store()
    with _connect() as conn:
        conn.execute("DELETE FROM live_sheet_rows WHERE go_no = ?", (go_no,))
        conn.execute("DELETE FROM live_sheets WHERE go_no = ?", (go_no,))
        conn.commit()


def live_sheet_store_status() -> dict[str, Any]:
    ensure_live_sheet_store()
    with _connect() as conn:
        sheet_count = int(conn.execute("SELECT COUNT(*) FROM live_sheets").fetchone()[0])
        row_count = int(conn.execute("SELECT COUNT(*) FROM live_sheet_rows").fetchone()[0])
        latest = conn.execute("SELECT MAX(updated_at) FROM live_sheets").fetchone()[0]
        states = {
            str(row["cache_state"] or "UNSET"): int(row["count"] or 0)
            for row in conn.execute(
                """
                SELECT COALESCE(NULLIF(cache_state, ''), 'UNSET') AS cache_state, COUNT(*) AS count
                FROM live_sheets
                GROUP BY COALESCE(NULLIF(cache_state, ''), 'UNSET')
                """
            ).fetchall()
        }
    return {
        "db_file": str(LIVE_SHEET_STORE_DB),
        "cached_go_count": sheet_count,
        "cached_row_count": row_count,
        "latest_updated_at": str(latest or ""),
        "cache_state_counts": dict(sorted(states.items())),
    }
