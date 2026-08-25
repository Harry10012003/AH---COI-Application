from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import threading
import uuid

from backend.engine.coi_postgres import (
    CoiPostgresConflict,
    CoiPostgresError,
    diff_rows,
    issue_current_sheet,
    load_current_issue,
    normalize_sheet_rows,
    sync_current_sheet,
)
from backend.engine.sql_live_engine import build_live_coi_sheet


_PREVIEW_TTL_SEC = max(60, int(os.getenv("COI_REFRESH_PREVIEW_TTL_SEC", "600") or "600"))
_PREVIEW_LOCK = threading.Lock()
_PREVIEWS: dict[str, dict] = {}
_MANUAL_FIELDS = {"AH Allocate Q'ty (yds)", "User Remark"}


def _error(message: str, **extra) -> dict:
    return {"ok": False, "error": message, **extra}


def _actor(value: object) -> str:
    return str(value or "").strip() or "UNKNOWN"


def _go(value: object) -> str:
    return str(value or "").strip().upper()


def _build_issue_sheet(go_no: str) -> dict:
    current = load_current_issue(go_no)
    if current is not None:
        return current
    result = build_live_coi_sheet(
        go_no,
        prefer_mes_cache=True,
        allow_live_mes=False,
        use_snapshot=True,
        persist_snapshot=True,
        allow_inline_build=False,
        snapshot_built_from="issue-precheck",
    )
    if result.get("pending") and int(result.get("row_count") or 0) <= 0:
        result = build_live_coi_sheet(
            go_no,
            prefer_mes_cache=True,
            allow_live_mes=False,
            use_snapshot=False,
            persist_snapshot=True,
            snapshot_built_from="issue-live",
        )
    return result


def issue_coi_to_postgres(go: object, *, actor: object) -> dict:
    go_no = _go(go)
    if not go_no:
        return _error("GO number required")
    try:
        sheet = _build_issue_sheet(go_no)
        if not sheet.get("ok"):
            return _error(
                "Cannot load COI sheet for ISSUE",
                go=go_no,
                detail=str(sheet.get("error") or sheet.get("detail") or ""),
            )
        result = issue_current_sheet({**sheet, "go": go_no}, actor=_actor(actor))
        result["message"] = (
            f"COI {go_no} stored in PostgreSQL (revision {result['issue_revision']})"
            if result.get("changed")
            else f"COI {go_no} already matches PostgreSQL revision {result['issue_revision']}"
        )
        return result
    except CoiPostgresError as exc:
        return _error(str(exc), go=go_no, storage_source="postgres")


def sync_issued_sheet_after_edit(go: object, sheet_payload: dict, *, actor: object) -> dict:
    go_no = _go(go)
    if not go_no:
        return _error("GO number required")
    try:
        current = load_current_issue(go_no)
        if current is None:
            return {"ok": True, "go": go_no, "skipped": True, "reason": "GO has not been issued yet"}
        return sync_current_sheet(
            {**sheet_payload, "go": go_no},
            actor=_actor(actor),
            action="AUTO_SYNC_EDIT",
        )
    except CoiPostgresError as exc:
        return _error(str(exc), go=go_no, storage_source="postgres")


def _overlay_manual_fields(current: dict, candidate: dict) -> dict:
    current_rows = {
        str(row.get("_row_key") or ""): row
        for row in current.get("rows") or []
        if isinstance(row, dict)
    }
    normalized = normalize_sheet_rows(candidate)
    candidate_rows = [row for row in candidate.get("rows") or [] if isinstance(row, dict)]
    for public_row, normalized_row in zip(candidate_rows, normalized, strict=False):
        previous = current_rows.get(str(normalized_row["internal_row_id"]))
        if previous is None:
            continue
        for field in _MANUAL_FIELDS:
            public_row[field] = previous.get(field)
    return candidate


def _purge_previews(now: datetime) -> None:
    expired = [key for key, value in _PREVIEWS.items() if value["expires_at"] <= now]
    for key in expired:
        _PREVIEWS.pop(key, None)


def create_refresh_preview(go: object, candidate_payload: dict, *, actor: object) -> dict:
    go_no = _go(go)
    try:
        current = load_current_issue(go_no)
        if current is None:
            return {"ok": True, "go": go_no, "preview_required": False, "sheet": candidate_payload}
        candidate = _overlay_manual_fields(current, deepcopy({**candidate_payload, "go": go_no}))
        diff = diff_rows(normalize_sheet_rows(current), normalize_sheet_rows(candidate))
        now = datetime.now(timezone.utc)
        preview_id = uuid.uuid4().hex
        expires_at = now + timedelta(seconds=_PREVIEW_TTL_SEC)
        preview = {
            "go": go_no,
            "actor": _actor(actor),
            "base_updated_at": str(current.get("issue", {}).get("updated_at") or ""),
            "candidate": candidate,
            "diff": diff,
            "expires_at": expires_at,
        }
        with _PREVIEW_LOCK:
            _purge_previews(now)
            _PREVIEWS[preview_id] = preview
        return {
            "ok": True,
            "go": go_no,
            "preview_required": True,
            "preview_id": preview_id,
            "expires_at": expires_at.isoformat(),
            "diff": {
                "added_row_count": diff["added_row_count"],
                "removed_row_count": diff["removed_row_count"],
                "changed_row_count": diff["changed_row_count"],
                "change_count": len(diff["changes"]),
                "changes": diff["changes"][:200],
                "truncated": len(diff["changes"]) > 200,
            },
        }
    except CoiPostgresError as exc:
        return _error(str(exc), go=go_no)


def apply_refresh_preview(go: object, preview_id: object, *, actor: object) -> dict:
    go_no = _go(go)
    token = str(preview_id or "").strip()
    now = datetime.now(timezone.utc)
    with _PREVIEW_LOCK:
        _purge_previews(now)
        preview = deepcopy(_PREVIEWS.get(token))
    if preview is None or preview.get("go") != go_no:
        return _error("Refresh preview expired or was not found", go=go_no, conflict=True)
    if preview.get("actor") != _actor(actor):
        return _error("Refresh preview belongs to another user", go=go_no, conflict=True)
    try:
        result = sync_current_sheet(
            preview["candidate"],
            actor=_actor(actor),
            action="SOURCE_REFRESH_APPLIED",
            expected_updated_at=preview["base_updated_at"],
        )
    except CoiPostgresConflict as exc:
        return _error(str(exc), go=go_no, conflict=True)
    except CoiPostgresError as exc:
        return _error(str(exc), go=go_no)
    with _PREVIEW_LOCK:
        _PREVIEWS.pop(token, None)
    result["message"] = "PPO source refresh applied to PostgreSQL"
    return result


def clear_refresh_previews_for_tests() -> None:
    with _PREVIEW_LOCK:
        _PREVIEWS.clear()
