from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import re
import zipfile

from flask import Blueprint, g, jsonify, request, send_file

from backend.sources import CACHE_DIR

from .excel_exporter import COLLAR_SHEET_NAME, COI_SHEET_NAME, MASTER_FILE_NAME, read_records_from_workbook, write_workbook
from .jobs import PreCoiJobStore
from .models import ExportRecord
from .services import GetYYService


_jobs = PreCoiJobStore(CACHE_DIR / "precoi_jobs")
_service_factory = GetYYService
bp = Blueprint("precoi", __name__)
_PRECOI_ALLOWED_USERNAME = "ah"
_EDITABLE_DRAFT_FIELDS = {"ppo_no", "yy_req_no"}
_MAX_DRAFT_EDITS = 5000
_MAX_EDIT_VALUE_LENGTH = 300


def _current_owner() -> str:
    return str((getattr(g, "auth_user", {}) or {}).get("username") or "").strip()


def _require_precoi_user():
    user = getattr(g, "auth_user", {}) or {}
    username = str(user.get("username") or "").strip().casefold()
    if username != _PRECOI_ALLOWED_USERNAME:
        return jsonify({"ok": False, "error": "Pre-COI access is restricted"}), 403
    return None


def _required_form_value(name: str, *, strip: bool = True) -> str:
    raw_value = str(request.form.get(name, "") or "")
    value = raw_value.strip() if strip else raw_value
    if not value:
        raise ValueError(f"{name.replace('_', ' ').title()} is required")
    return value


def _start(action: str, runner):
    job = _jobs.start(owner=_current_owner(), action=action, runner=runner)
    return jsonify({"ok": True, "job_id": job.job_id, "state": job.state}), 202


def _uploaded_workbook() -> tuple[bytes, str]:
    upload = request.files.get("workbook")
    if upload is None or not upload.filename:
        raise ValueError("Workbook file is required")
    if not upload.filename.lower().endswith(".xlsx"):
        raise ValueError("Workbook file must be .xlsx")
    payload = upload.read()
    if not payload or not zipfile.is_zipfile(BytesIO(payload)):
        raise ValueError("Workbook file is not a valid .xlsx archive")
    return payload, "input.xlsx"


def _download_name_for_records(records: list[ExportRecord]) -> str:
    ordered_gos: list[str] = []
    seen: set[str] = set()
    for record in records:
        go = re.sub(r"[^A-Za-z0-9-]", "", str(record.go or "").upper())
        if not go or go in seen:
            continue
        seen.add(go)
        ordered_gos.append(go)
    stem = "-".join(ordered_gos) or "Output"
    if len(stem) > 180:
        shown = "-".join(ordered_gos[:3])
        stem = f"{shown}-and-{max(len(ordered_gos) - 3, 0)}-more"
    return f"Pre-COI {stem}.xlsx"


def _draft_row_id(record: ExportRecord) -> str:
    return f"{record.sheet_kind}:{record.row_index}"


def _record_to_draft_row(record: ExportRecord) -> dict:
    return {
        "row_id": _draft_row_id(record),
        "go": record.go,
        "yy_req_no": record.yy_req_no,
        "marker_yy": record.marker_yy,
        "ppo_yy": record.ppo_yy,
        "gmt_color": record.gmt_color,
        "fabric_part": record.fabric_part,
        "color_code": record.color_code,
        "color_desc": record.color_desc,
        "jo": record.jo,
        "size": record.size,
        "qty": record.qty,
        "ppo_no": record.ppo_no,
        "ppo_qty": record.ppo_qty,
        "flow": record.flow,
    }


def _draft_payload(job_id: str, records: list[ExportRecord], revision: int) -> dict:
    main_rows = [_record_to_draft_row(record) for record in records if record.sheet_kind != COLLAR_SHEET_NAME]
    collar_rows = [_record_to_draft_row(record) for record in records if record.sheet_kind == COLLAR_SHEET_NAME]
    return {
        "ok": True,
        "job_id": job_id,
        "revision": revision,
        "download_name": _download_name_for_records(records),
        "sheets": [
            {"key": COI_SHEET_NAME, "label": COI_SHEET_NAME, "rows": main_rows},
            {"key": COLLAR_SHEET_NAME, "label": "COI Collar/Cuff", "rows": collar_rows},
        ],
    }


def _draft_records_for(job_id: str) -> tuple[list[ExportRecord], int] | None:
    owner = _current_owner()
    cached = _jobs.draft_records_for(job_id, owner)
    if cached is not None:
        records, revision = cached
        return list(records), revision

    artifact = _jobs.artifact_for(job_id, owner)
    if artifact is None:
        return None
    records = read_records_from_workbook(artifact)
    revision = _jobs.set_draft_records(job_id, owner, records)
    if revision is None:
        return None
    return records, revision


def _draft_edit_value(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if len(value) > _MAX_EDIT_VALUE_LENGTH or any(ord(character) < 32 for character in value):
        raise ValueError("Draft cell value is invalid")
    return value


def _apply_draft_edits(records: list[ExportRecord], edits: list[object]) -> list[ExportRecord]:
    by_row_id = {_draft_row_id(record): index for index, record in enumerate(records)}
    updated = list(records)
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("Draft edits are invalid")
        row_id = str(edit.get("row_id") or "")
        field = str(edit.get("field") or "")
        if row_id not in by_row_id or field not in _EDITABLE_DRAFT_FIELDS:
            raise ValueError("Draft edit is not allowed")
        index = by_row_id[row_id]
        updated[index] = replace(updated[index], **{field: _draft_edit_value(edit.get("value"))})
    return updated


@bp.route("/api/precoi/jobs/create", methods=["POST"])
def api_precoi_create_job():
    denied = _require_precoi_user()
    if denied:
        return denied
    try:
        go_text = _required_form_value("go_text")
        username = _required_form_value("ypd_username")
        password = _required_form_value("ypd_password", strip=False)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    def runner(job_dir: Path, log):
        return _service_factory().create_output(
            go_input=go_text,
            username=username,
            password=password,
            output_dir=job_dir,
            log=log,
        )

    return _start("create", runner)


@bp.route("/api/precoi/jobs/cm", methods=["POST"])
def api_precoi_cm_job():
    denied = _require_precoi_user()
    if denied:
        return denied
    try:
        go_text = _required_form_value("go_text")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return _start("cm", lambda job_dir, log: _service_factory().update_cm_from_go(go_text, job_dir, log))


@bp.route("/api/precoi/jobs/update-ppo", methods=["POST"])
def api_precoi_ppo_job():
    denied = _require_precoi_user()
    if denied:
        return denied
    try:
        payload, filename = _uploaded_workbook()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    def runner(job_dir: Path, log):
        workbook = job_dir / filename
        workbook.write_bytes(payload)
        return _service_factory().update_ppo_qty_from_workbook(workbook, log)

    return _start("update-ppo", runner)


@bp.route("/api/precoi/jobs/update-yy", methods=["POST"])
def api_precoi_yy_job():
    denied = _require_precoi_user()
    if denied:
        return denied
    try:
        payload, filename = _uploaded_workbook()
        username = _required_form_value("ypd_username")
        password = _required_form_value("ypd_password", strip=False)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    def runner(job_dir: Path, log):
        workbook = job_dir / filename
        workbook.write_bytes(payload)
        return _service_factory().update_yy_req_no_from_workbook(workbook, username, password, log)

    return _start("update-yy", runner)


@bp.route("/api/precoi/jobs/<job_id>")
def api_precoi_job_status(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    snapshot = _jobs.snapshot(job_id, _current_owner())
    if snapshot is None:
        return jsonify({"ok": False, "error": "Pre-COI job not found"}), 404
    return jsonify({"ok": True, **snapshot})


@bp.route("/api/precoi/jobs/<job_id>/draft")
def api_precoi_job_draft(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    try:
        draft = _draft_records_for(job_id)
    except Exception:
        return jsonify({"ok": False, "error": "Cannot prepare Pre-COI draft."}), 400
    if draft is None:
        return jsonify({"ok": False, "error": "Pre-COI draft is not ready"}), 404
    records, revision = draft
    return jsonify(_draft_payload(job_id, records, revision))


@bp.route("/api/precoi/jobs/<job_id>/draft", methods=["POST"])
def api_precoi_save_draft(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    revision = payload.get("revision")
    edits = payload.get("edits")
    if not isinstance(revision, int) or not isinstance(edits, list) or len(edits) > _MAX_DRAFT_EDITS:
        return jsonify({"ok": False, "error": "Draft save payload is invalid"}), 400
    draft = _draft_records_for(job_id)
    if draft is None:
        return jsonify({"ok": False, "error": "Pre-COI draft is not ready"}), 404
    records, current_revision = draft
    if revision != current_revision:
        return jsonify({"ok": False, "error": "Draft changed. Reload the draft before saving."}), 409
    try:
        updated_records = _apply_draft_edits(records, edits)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    next_revision = _jobs.replace_draft_records(job_id, _current_owner(), updated_records, expected_revision=revision)
    if next_revision is None:
        return jsonify({"ok": False, "error": "Draft changed. Reload the draft before saving."}), 409
    return jsonify(_draft_payload(job_id, updated_records, next_revision))


def _start_draft_update(job_id: str, action: str, *, username: str = "", password: str = ""):
    draft = _draft_records_for(job_id)
    if draft is None:
        return jsonify({"ok": False, "error": "Save or create a Pre-COI draft first"}), 404
    records, _revision = draft

    def runner(job_dir: Path, log):
        workbook = job_dir / MASTER_FILE_NAME
        write_workbook(workbook, records)
        if action == "update-ppo":
            return _service_factory().update_ppo_qty_from_workbook(workbook, log)
        return _service_factory().update_yy_req_no_from_workbook(workbook, username, password, log)

    return _start(action, runner)


@bp.route("/api/precoi/jobs/<job_id>/update-ppo", methods=["POST"])
def api_precoi_draft_ppo_job(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    return _start_draft_update(job_id, "update-ppo")


@bp.route("/api/precoi/jobs/<job_id>/update-yy", methods=["POST"])
def api_precoi_draft_yy_job(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    try:
        username = _draft_edit_value(payload.get("ypd_username"))
        password = _draft_edit_value(payload.get("ypd_password"))
        if not username or not password:
            raise ValueError("ESCM account and password are required")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return _start_draft_update(job_id, "update-yy", username=username, password=password)


@bp.route("/api/precoi/jobs/<job_id>/download")
def api_precoi_job_download(job_id: str):
    denied = _require_precoi_user()
    if denied:
        return denied
    artifact = _jobs.artifact_for(job_id, _current_owner())
    if artifact is None:
        return jsonify({"ok": False, "error": "Pre-COI workbook is not ready"}), 404
    try:
        draft = _draft_records_for(job_id)
        download_name = _download_name_for_records(draft[0]) if draft is not None else artifact.name
    except Exception:
        download_name = artifact.name
    return send_file(artifact, as_attachment=True, download_name=download_name, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
