from __future__ import annotations

from pathlib import Path
from io import BytesIO
import os
import sys
import threading
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, g, jsonify, request, send_file, send_from_directory

from backend.auth import (
    ROLE_EDITOR,
    ROLE_VIEWER,
    authenticate,
    bearer_token,
    create_session,
    resolve_session,
    revoke_session,
)

from backend.engine.coi_engine import build_coi_preview
from backend.engine.coi_export_engine import build_coi_ui_export_workbook
from backend.engine.coi_issue_archive import (
    backfill_local_issued_coi,
    get_latest_issued_coi_feed,
    issued_coi_archive_status,
    rebuild_combined_issued_coi,
)
from backend.engine.coi_issue_engine import (
    get_issue_job_status,
    issue_coi_to_sharepoint,
    queue_issue_coi_to_sharepoint,
    sync_published_issue_after_sheet_change,
)
from backend.engine.color_audit_engine import color_audit_status, ensure_color_audit_worker
from backend.engine.excel_workspace import (
    apply_workbook_edits,
    audit_workbook,
    get_workbook_overview,
    recalculate_workbook,
    read_sheet_window,
)
from backend.engine.fabric_engine import (
    build_fabric_rows,
    get_fabric_stock_meta,
    load_fabric_stock,
    preload_default_fabric,
    save_uploaded_fabric,
)
from backend.engine.sql_live_engine import (
    build_live_coi,
    build_live_coi_sheet,
    ensure_sql_snapshot_status_logger,
    ensure_sql_snapshot_worker,
    load_ppo_order_detail,
    list_live_go,
    save_live_sheet_edits,
    sql_live_status,
    sql_source_cache_status,
    sql_snapshot_status,
    wait_sqlite_startup_ready,
)
from backend.scraper.gw_client import (
    fetch_go_color_summary,
    fetch_go_ppo_mapping_only,
    fetch_ppo_browse,
    fetch_tendam_ppo_status,
    query_gw_by_go_list,
)
from backend.scraper.mes_client import (
    clear_cutting_forecast_cache,
    get_cutting_cache_status,
    query_mes_cutting,
)
from backend.sources import CACHE_DIR, FRONTEND_DIR, PROJECT_DIR, get_source_map

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = max(
    1,
    int(os.getenv("APP_MAX_UPLOAD_MB", "25") or "25"),
) * 1024 * 1024
_DIAGNOSTICS_DETAIL = str(os.getenv("APP_DIAGNOSTICS_DETAIL", "false") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ALLOWED_SHAREPOINT_HOSTS = {
    item.strip().lower()
    for item in str(
        os.getenv(
            "APP_SHAREPOINT_HOSTS",
            "esquel.sharepoint.com,esquel-my.sharepoint.com",
        )
        or ""
    ).split(",")
    if item.strip()
}
_CUTTING_COI_API_ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in str(os.getenv("COI_API_ALLOWED_ORIGINS", "") or "").split(",")
    if item.strip()
}
_APP_ALLOWED_ORIGINS = {
    item.strip().rstrip("/").lower()
    for item in str(
        os.getenv(
            "APP_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        or ""
    ).split(",")
    if item.strip()
}
_ALLOWED_LOCAL_ROOTS = [
    PROJECT_ROOT.resolve(),
    (Path.home() / "Desktop").resolve(),
    (Path.home() / "OneDrive - Esquel Group").resolve(),
]
for _configured_root in str(os.getenv("APP_ALLOWED_FILE_ROOTS", "") or "").split(os.pathsep):
    if _configured_root.strip():
        _ALLOWED_LOCAL_ROOTS.append(Path(_configured_root).expanduser().resolve())
_background_services_lock = threading.Lock()
_background_services_started = False


def start_background_services(*, wait_for_startup: bool = False) -> None:
    global _background_services_started
    with _background_services_lock:
        if _background_services_started:
            return
        ensure_sql_snapshot_worker()
        ensure_sql_snapshot_status_logger()
        ensure_color_audit_worker()
        _background_services_started = True
    if wait_for_startup:
        wait_sqlite_startup_ready()


def _json_payload():
    return request.get_json(silent=True) or {}


def _parse_go_list(payload: dict) -> list[str]:
    values = []
    raw_list = payload.get("go_list")
    if isinstance(raw_list, str):
        values.extend(raw_list.replace(",", " ").replace(";", " ").split())
    elif isinstance(raw_list, list):
        values.extend(str(item or "") for item in raw_list)
    raw_text = str(payload.get("go_text", "") or "").strip()
    if raw_text:
        values.extend(raw_text.replace(",", " ").replace(";", " ").split())
    result = []
    seen = set()
    for raw in values:
        go = str(raw or "").strip().upper()
        if not go or go in seen:
            continue
        seen.add(go)
        result.append(go)
    return result


def _build_ppo_detail_payload(ppo: str) -> dict:
    return load_ppo_order_detail(ppo)


def _allowed_https_url(raw_url: object, allowed_hosts: set[str]) -> bool:
    text = str(raw_url or "").strip()
    if not text:
        return True
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() in allowed_hosts


def _allowed_local_path(raw_path: object) -> bool:
    text = str(raw_path or "").strip()
    if not text:
        return True
    if "://" in text:
        return False
    try:
        candidate = Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return any(candidate == root or candidate.is_relative_to(root) for root in _ALLOWED_LOCAL_ROOTS)


def _request_origin_allowed(raw_origin: str) -> bool:
    """Allow same-origin requests and explicitly configured local dev origins."""
    try:
        parsed = urlsplit(str(raw_origin or "").strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return False
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    request_origin = f"{request.scheme.lower()}://{str(request.host or '').lower()}"
    return normalized == request_origin or normalized in _APP_ALLOWED_ORIGINS


def validate_bind_security(host: object) -> None:
    """Keep startup compatible while this app is intentionally LAN-only.

    SQL access is restricted by the company network and the application no
    longer requires an API token for either loopback or private-LAN binding.
    The host argument remains part of the public startup helper for callers
    that already invoke it.
    """
    _ = host


@app.errorhandler(400)
def _bad_request(error):
    return jsonify({"ok": False, "error": "Bad request", "detail": str(error)}), 400


@app.errorhandler(404)
def _not_found(error):
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.errorhandler(405)
def _method_not_allowed(error):
    return jsonify({"ok": False, "error": "Method not allowed"}), 405


@app.errorhandler(413)
def _payload_too_large(error):
    return jsonify({"ok": False, "error": "Upload too large"}), 413


@app.errorhandler(500)
def _internal_error(error):
    return jsonify({"ok": False, "error": "Internal server error"}), 500


@app.after_request
def _add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
    if request.path.startswith("/api/cutting/coi/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def _add_cors_headers():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Max-Age"] = "3600"
        return resp


@app.route("/api/health")
def health_check():
    return jsonify({"ok": True, "status": "healthy", "version": "1.0.0"})


def _public_snapshot_status(status: dict | None = None) -> dict:
    payload = status if isinstance(status, dict) else sql_snapshot_status()
    if _DIAGNOSTICS_DETAIL:
        return payload
    public_keys = (
        "warmup_complete",
        "cached_go_count",
        "preload_feed_count",
        "preload_uncached_count",
        "preload_outdated_count",
        "preload_staged_missing_count",
        "preload_staged_outdated_count",
        "source_active_go_count",
        "source_current_go_count",
        "source_uncurrent_go_count",
        "source_missing_staged_head_count",
        "source_outdated_topology_count",
        "source_missing_ppo_count",
        "source_scope_evaluated",
        "thread_alive",
        "source_refresh_thread_alive",
        "lease_monitor_thread_alive",
        "worker_standby",
        "sql_connectivity_state",
        "sql_failure_count",
        "sql_retry_delay_sec",
        "sql_retry_at",
        "startup_ready",
        "startup_wait_timeout_sec",
        "snapshot_payload_version",
        "stale_backlog",
        "last_batch_size",
        "interactive_queue_size",
        "inline_building_count",
        "query_metrics",
    )
    return {key: payload.get(key) for key in public_keys if key in payload}


def _public_source_cache_status(status: dict | None = None) -> dict:
    payload = status if isinstance(status, dict) else sql_source_cache_status()
    if _DIAGNOSTICS_DETAIL:
        return payload
    recent_sources = payload.get("recent_sources")
    if not isinstance(recent_sources, list):
        recent_sources = []
    return {
        "latest_synced_at": str(payload.get("latest_synced_at") or ""),
        "latest_checked_at": str(payload.get("latest_checked_at") or ""),
        "recent_source_count": len(recent_sources),
        "recent_error_count": sum(
            1
            for item in recent_sources
            if isinstance(item, dict)
            and str(item.get("source_status") or "").strip().upper() not in {"", "OK"}
        ),
    }


def _pick_public_fields(payload: dict, fields: tuple[str, ...]) -> dict:
    return {key: payload.get(key) for key in fields if key in payload}


def _public_fabric_stock_status(status: dict) -> dict:
    if _DIAGNOSTICS_DETAIL:
        return status
    result = _pick_public_fields(status, ("loaded_at", "total_groups"))
    result["loaded"] = bool(status.get("loaded_at") or status.get("total_groups"))
    return result


def _public_mes_cache_status(status: dict) -> dict:
    if _DIAGNOSTICS_DETAIL:
        return status
    return _pick_public_fields(status, ("cached_go_count", "exists"))


def _public_color_audit_status(status: dict) -> dict:
    if _DIAGNOSTICS_DETAIL:
        return status
    result = _pick_public_fields(
        status,
        (
            "running",
            "thread_alive",
            "started_at",
            "last_started_at",
            "last_finished_at",
            "last_success_at",
        ),
    )
    summary = status.get("last_summary")
    if not isinstance(summary, dict):
        summary = {}
    counters = summary.get("counters")
    if not isinstance(counters, dict):
        counters = {}
    result["summary"] = {
        **_pick_public_fields(
            summary,
            ("scanned_go", "conflict_row_count", "finding_row_count"),
        ),
        **_pick_public_fields(
            counters,
            ("go_with_divergent_codes", "go_with_sql_risk"),
        ),
    }
    result["has_error"] = bool(status.get("last_error"))
    return result


@app.before_request
def enforce_api_request_boundary():
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS" and request.path.startswith("/api/cutting/coi/"):
        # CORS preflight carries no data. The subsequent GET still follows the
        # normal LAN/token boundary below.
        return ("", 204)
    if request.method == "GET" and request.path in {"/api/excel/workbook", "/api/excel/audit"}:
        workbook_path = str(request.args.get("path") or "").strip()
        if workbook_path and (
            workbook_path.lower().startswith(("http://", "https://"))
            or not _allowed_local_path(workbook_path)
        ):
            return jsonify({"ok": False, "error": "Local path is not allowed for path"}), 400
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        fetch_site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if fetch_site == "cross-site":
            return jsonify({"ok": False, "error": "Cross-site API request rejected"}), 403
        origin = str(request.headers.get("Origin") or "").strip()
        if origin:
            if not _request_origin_allowed(origin):
                return jsonify({"ok": False, "error": "Request origin rejected"}), 403
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            for field_name in ("target_url", "workbook_link", "weekly_report_link"):
                value = str(payload.get(field_name) or "").strip()
                if value and not _allowed_https_url(value, _ALLOWED_SHAREPOINT_HOSTS):
                    return jsonify({"ok": False, "error": f"Remote host is not allowed for {field_name}"}), 400
            workbook_path = str(payload.get("workbook_path") or "").strip()
            if workbook_path.lower().startswith(("http://", "https://")) and not _allowed_https_url(
                workbook_path,
                _ALLOWED_SHAREPOINT_HOSTS,
            ):
                return jsonify({"ok": False, "error": "Remote workbook host is not allowed"}), 400
            for field_name in ("workbook_path", "upload_dir"):
                value = str(payload.get(field_name) or "").strip()
                if value and not value.lower().startswith(("http://", "https://")) and not _allowed_local_path(value):
                    return jsonify({"ok": False, "error": f"Local path is not allowed for {field_name}"}), 400
    if not request.path.startswith("/api/auth/"):
        start_background_services(wait_for_startup=False)
    return None


_PUBLIC_API_RULES = {
    "/api/health",
    "/api/auth/login",
    "/api/cutting/coi/latest",
}


def _viewer_can_access() -> bool:
    if request.path == "/api/auth/me" and request.method == "GET":
        return True
    if request.path == "/api/auth/logout" and request.method == "POST":
        return True
    if request.path == "/api/sql/go/list" and request.method == "GET":
        return True
    if request.method != "GET":
        return False
    endpoint = str(request.endpoint or "")
    return endpoint in {"api_sql_go_coi", "api_sql_go_sheet"}


@app.before_request
def enforce_api_authentication():
    if not request.path.startswith("/api/") or request.method == "OPTIONS":
        return None
    if request.url_rule is None or request.path in _PUBLIC_API_RULES:
        return None

    token = bearer_token(request.headers.get("Authorization"))
    user = resolve_session(token)
    if user is None:
        return jsonify({"ok": False, "error": "Authentication required"}), 401

    g.auth_token = token
    g.auth_user = user
    if user.get("role") == ROLE_VIEWER and not _viewer_can_access():
        return jsonify({"ok": False, "error": "Viewer access is read-only"}), 403
    return None


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    payload = _json_payload()
    user = authenticate(payload.get("username"), payload.get("password"))
    if user is None:
        return jsonify({"ok": False, "error": "Incorrect username or password"}), 401
    token, expires_at = create_session(user)
    return jsonify(
        {
            "ok": True,
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at.isoformat(),
            "user": user,
        }
    )


@app.route("/api/auth/me")
def api_auth_me():
    return jsonify({"ok": True, "user": dict(g.auth_user)})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    revoke_session(g.auth_token)
    return jsonify({"ok": True})


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'self'; object-src 'none'; img-src 'self' data: blob:; "
        "script-src 'self'; style-src 'self'; connect-src 'self'",
    )
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.path.startswith("/api/cutting/coi/"):
        origin = str(request.headers.get("Origin") or "").strip().rstrip("/")
        allow_any_origin = "*" in _CUTTING_COI_API_ALLOWED_ORIGINS
        if origin and (allow_any_origin or origin in _CUTTING_COI_API_ALLOWED_ORIGINS):
            response.headers["Access-Control-Allow-Origin"] = "*" if allow_any_origin else origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Token, Authorization"
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"ok": False, "error": "Upload exceeds configured size limit"}), 413


@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIR / "dist"), "index.html")


@app.route("/assets/<path:filename>")
def frontend_asset(filename: str):
    return send_from_directory(str(FRONTEND_DIR / "dist" / "assets"), filename)


@app.route("/favicon.svg")
@app.route("/icons.svg")
def frontend_public_asset():
    return send_from_directory(str(FRONTEND_DIR / "dist"), request.path.lstrip("/"))


@app.route("/<path:path>")
def spa_fallback(path: str):
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if dist_index.exists():
        return send_from_directory(str(FRONTEND_DIR / "dist"), "index.html")
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.route("/api/status")
def api_status():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fabric_status = get_fabric_stock_meta()
    mes_status = get_cutting_cache_status()
    snapshot_status = sql_snapshot_status()
    source_cache_status = sql_source_cache_status()
    audit_status = color_audit_status()
    payload = {
        "ok": True,
        "fabric_stock": _public_fabric_stock_status(fabric_status),
        "mes_cache": _public_mes_cache_status(mes_status),
        "sheet_snapshot": _public_snapshot_status(snapshot_status),
        "sql_source_cache": _public_source_cache_status(source_cache_status),
        "color_audit": _public_color_audit_status(audit_status),
    }
    if _DIAGNOSTICS_DETAIL:
        payload.update(
            {
                "project_dir": str(PROJECT_DIR),
                "frontend_dir": str(FRONTEND_DIR),
                "cache_dir": str(CACHE_DIR),
            }
        )
    return jsonify(payload)


@app.route("/api/sql/status")
def api_sql_status():
    result = sql_live_status()
    if result.get("ok") and not _DIAGNOSTICS_DETAIL:
        connection = result.get("connection")
        if not isinstance(connection, dict):
            connection = {}
        result.pop("sql_version", None)
        result.pop("server_time", None)
        result.pop("user", None)
        result["connection"] = {
            "database": result.get("database", ""),
            "encrypted": bool(connection.get("encrypted")),
            "transport_security": str(connection.get("transport_security") or ""),
            "encryption_required": bool(connection.get("encryption_required")),
        }
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/preload/status")
def api_sql_preload_status():
    return jsonify({"ok": True, **_public_snapshot_status()})


@app.route("/api/sql/readiness")
def api_sql_readiness():
    timeout = request.args.get("timeout", 0)
    startup = wait_sqlite_startup_ready(timeout)
    preload = sql_snapshot_status()
    public_startup = _public_snapshot_status(startup)
    return jsonify(
        {
            "ok": True,
            **public_startup,
            "warmup_complete": bool(preload.get("warmup_complete")),
            "preload": _public_snapshot_status(preload),
        }
    )


@app.route("/api/sql/source-cache/status")
def api_sql_source_cache_status():
    return jsonify({"ok": True, **_public_source_cache_status()})


@app.route("/api/sql/audit/status")
def api_sql_audit_status():
    return jsonify({"ok": True, **_public_color_audit_status(color_audit_status())})


@app.route("/api/sql/go/list")
def api_sql_go_list():
    result = list_live_go(
        limit=request.args.get("limit", 220),
        search=str(request.args.get("search", "") or ""),
        since=str(request.args.get("since", "") or ""),
        factories=request.args.get("factories", "EGV,EAV"),
        coi_ready=str(request.args.get("coi_ready", "all") or "all"),
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/coi")
def api_sql_go_coi(go: str):
    result = build_live_coi(go)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/sheet")
def api_sql_go_sheet(go: str):
    viewer_mode = str((getattr(g, "auth_user", {}) or {}).get("role") or "") == ROLE_VIEWER
    source_max_age_raw = str(request.args.get("source_max_age_sec", "") or "").strip()
    source_max_age_sec = None
    if source_max_age_raw:
        try:
            source_max_age_sec = float(source_max_age_raw)
        except ValueError:
            source_max_age_sec = None
    result = build_live_coi_sheet(
        go,
        prefer_mes_cache=True if viewer_mode else str(request.args.get("prefer_mes_cache", "true") or "").strip().lower() != "false",
        allow_live_mes=False if viewer_mode else str(request.args.get("allow_live_mes", "false") or "").strip().lower() == "true",
        use_snapshot=True if viewer_mode else str(request.args.get("use_snapshot", "true") or "").strip().lower() != "false",
        persist_snapshot=False if viewer_mode else str(request.args.get("persist_snapshot", "true") or "").strip().lower() != "false",
        allow_inline_build=False if viewer_mode else str(request.args.get("allow_inline_build", "false") or "").strip().lower() == "true",
        allow_slow_sql_enrichment=not viewer_mode,
        sample_type=str(request.args.get("sample_type", "PPS") or "PPS").strip(),
        # Render from the versioned SQLite sheet first. The source worker keeps
        # active PPO quantities fresh in the background; callers that need a
        # blocking source verification can still opt in with
        # require_current_source=true. This avoids an empty UI while a slow
        # received/FOC SQL view is being refreshed.
        require_current_source=False if viewer_mode else str(request.args.get("require_current_source", "false") or "").strip().lower() == "true",
        source_max_age_sec=None if viewer_mode else source_max_age_sec,
        manual_allocation_mode=str(request.args.get("manual_allocation_mode", "") or "").strip() or None,
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/refresh-ppo", methods=["POST"])
def api_sql_go_refresh_ppo(go: str):
    result = build_live_coi_sheet(
        str(go or "").strip().upper(),
        prefer_mes_cache=True,
        allow_live_mes=False,
        use_snapshot=False,
        persist_snapshot=True,
        allow_inline_build=True,
        allow_slow_sql_enrichment=True,
        prefer_source_cache=False,
        require_current_source=True,
        snapshot_built_from="manual-ppo-refresh",
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/sheet/export", methods=["POST"])
def api_sql_go_sheet_export(go: str):
    payload = _json_payload()
    go_key = str(payload.get("go") or go or "").strip().upper()
    if not go_key:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    payload["go"] = go_key
    if not isinstance(payload.get("columns"), list) or not payload.get("columns"):
        result = build_live_coi_sheet(
            go_key,
            use_snapshot=True,
            persist_snapshot=True,
            allow_inline_build=True,
            allow_slow_sql_enrichment=True,
            sample_type=str(payload.get("sample_type") or "PPS").strip(),
            require_current_source=True,
        )
        if not result.get("ok"):
            return jsonify(result), 502
        payload = result
    file_bytes, filename, mimetype = build_coi_ui_export_workbook(payload)
    return send_file(
        BytesIO(file_bytes),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@app.route("/api/sql/go/<go>/sheet/edits", methods=["POST"])
def api_sql_go_sheet_edits(go: str):
    payload = _json_payload()
    edits = payload.get("edits") if isinstance(payload.get("edits"), list) else []
    result = save_live_sheet_edits(
        go,
        edits,
        manual_allocation_mode=str(payload.get("manual_allocation_mode", "") or "").strip() or None,
    )
    ppo_changed = any(
        isinstance(edit, dict) and str(edit.get("field") or "").strip() == "PPO"
        for edit in edits
    )
    refresh_after_ppo_edit = payload.get("refresh_after_ppo_edit", True)
    if isinstance(refresh_after_ppo_edit, str):
        refresh_after_ppo_edit = refresh_after_ppo_edit.strip().lower() not in {"0", "false", "no", "off"}
    if not result.get("ok") or not ppo_changed or not refresh_after_ppo_edit:
        return jsonify(result), (200 if result.get("ok") else 400)

    # A PPO edit changes the fabric identity. Reuse the already staged GO
    # topology, then query SQL Server for the overridden PPO only. Querying the
    # full historical GO bundle again can take a minute on the received/FOC
    # view and is unnecessary for this operator action.
    go_key = str(go or "").strip().upper()
    refreshed_sheet = build_live_coi_sheet(
        go_key,
        prefer_mes_cache=True,
        allow_live_mes=False,
        use_snapshot=False,
        persist_snapshot=True,
        allow_inline_build=True,
        allow_slow_sql_enrichment=True,
        prefer_source_cache=True,
        require_current_source=False,
        snapshot_built_from="ppo-edit-live-sql",
    )
    if not refreshed_sheet.get("ok"):
        result["refresh_error"] = refreshed_sheet.get("error") or refreshed_sheet.get("detail") or "SQL refresh failed"
        result["sheet"] = refreshed_sheet
        return jsonify(result), 200

    result["sheet"] = refreshed_sheet
    result["recalculated_from_sql"] = True
    issue_sync = sync_published_issue_after_sheet_change(go_key, refreshed_sheet)
    result["issued_coi_sync"] = issue_sync
    if not issue_sync.get("ok"):
        result["issued_coi_sync_error"] = issue_sync.get("error") or "Issued COI synchronization failed"
    return jsonify(result), 200


@app.route("/api/sql/go/<go>/sheet/query-remark", methods=["POST"])
def api_sql_go_sheet_query_remark(go: str):
    payload = _json_payload()
    result = query_coi_remarks_from_weekly(
        go,
        weekly_report_path=str(payload.get("weekly_report_path") or payload.get("weekly_report_link") or "").strip(),
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/issue", methods=["POST"])
def api_sql_go_issue(go: str):
    payload = _json_payload()
    go_key = str(payload.get("go") or go or "").strip().upper()
    async_raw = payload.get("async", True)
    async_mode = bool(async_raw) if isinstance(async_raw, bool) else str(async_raw or "true").strip().lower() != "false"
    if async_mode:
        result = queue_issue_coi_to_sharepoint(go_key, target_url=payload.get("target_url"))
        return jsonify(result), 202
    result = issue_coi_to_sharepoint(go_key, target_url=payload.get("target_url"))
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/go/<go>/issue/status")
def api_sql_go_issue_status(go: str):
    go_key = str(go or "").strip().upper()
    result = get_issue_job_status(go_key)
    return jsonify({"ok": True, **result})


@app.route("/api/sql/issued-coi/combined/status")
def api_issued_coi_combined_status():
    return jsonify(issued_coi_archive_status())


@app.route("/api/sql/issued-coi/combined/rebuild", methods=["POST"])
def api_issued_coi_combined_rebuild():
    payload = _json_payload()
    backfill_raw = payload.get("backfill", True)
    backfill_enabled = bool(backfill_raw) if isinstance(backfill_raw, bool) else str(backfill_raw or "true").strip().lower() != "false"
    backfill_result = backfill_local_issued_coi() if backfill_enabled else {"ok": True, "skipped": True}
    result = rebuild_combined_issued_coi(sync_to_onedrive=True)
    result["backfill"] = backfill_result
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/sql/issued-coi/combined/download")
def api_issued_coi_combined_download():
    status = issued_coi_archive_status()
    file_path = Path(str(status.get("local_file_path") or ""))
    if not file_path.exists():
        rebuilt = rebuild_combined_issued_coi(sync_to_onedrive=True)
        if not rebuilt.get("ok"):
            return jsonify(rebuilt), 404
        file_path = Path(str(rebuilt.get("file_path") or ""))
    if not file_path.exists():
        return jsonify({"ok": False, "error": "Combined ISSUE COI workbook was not created"}), 404
    return send_file(
        file_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=file_path.name,
        max_age=0,
    )


@app.route("/api/cutting/coi/latest", methods=["GET", "OPTIONS"])
def api_cutting_coi_latest():
    if request.method == "OPTIONS":
        return ("", 204)
    result = get_latest_issued_coi_feed(
        go=request.args.get("go"),
        ppo=request.args.get("ppo"),
        jo=request.args.get("jo"),
        color_code=request.args.get("color_code"),
        limit=request.args.get("limit", 5000),
        offset=request.args.get("offset", 0),
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/excel/workbook")
def api_excel_workbook():
    result = get_workbook_overview(request.args.get("path"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/excel/audit")
def api_excel_audit():
    result = audit_workbook(request.args.get("path"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/excel/sheet", methods=["POST"])
def api_excel_sheet():
    payload = _json_payload()
    result = read_sheet_window(
        workbook_path=payload.get("workbook_path"),
        sheet_name=str(payload.get("sheet") or ""),
        start_row=payload.get("start_row", 1),
        row_limit=payload.get("row_limit", 120),
        start_col=payload.get("start_col", 1),
        col_limit=payload.get("col_limit", 26),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/excel/apply-edits", methods=["POST"])
def api_excel_apply_edits():
    payload = _json_payload()
    result = apply_workbook_edits(
        edits=payload.get("edits") if isinstance(payload.get("edits"), list) else [],
        workbook_path=payload.get("workbook_path"),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/excel/recalculate", methods=["POST"])
def api_excel_recalculate():
    payload = _json_payload()
    result = recalculate_workbook(payload.get("workbook_path"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/excel/sync-sql-coi", methods=["POST"])
def api_excel_sync_sql_coi():
    payload = _json_payload()
    go = str(payload.get("go", "") or "").strip().upper()
    if not go:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    # Merge1 is a legacy workbook layout: it has no SIZE column and derives
    # on-hand from receipt quantity. It cannot represent verified O/F stock
    # or the current production/sample-issue deduction safely. Keep callers
    # from generating a plausible but incorrect workbook; the COI workspace
    # export already uses the canonical, size-aware sheet payload.
    return jsonify(
        {
            "ok": False,
            "go": go,
            "error": "Legacy COI workbook sync is disabled because it cannot preserve size-specific stock allocation.",
            "replacement": f"/api/sql/go/{go}/sheet/export",
        }
    ), 409


@app.route("/api/sources")
def api_sources():
    sources = get_source_map()
    if _DIAGNOSTICS_DETAIL:
        return jsonify({"ok": True, **sources})
    return jsonify(
        {
            "ok": True,
            "sql_live": {
                "configured": bool(((sources.get("sql_live") or {}).get("credentials") or {}).get("configured")),
                "encrypted": bool((sources.get("sql_live") or {}).get("encrypted")),
            },
            "shipment_on_way_sql": {
                "configured": bool(
                    ((sources.get("shipment_on_way_sql") or {}).get("credentials") or {}).get("configured")
                ),
                "encrypted": bool((sources.get("shipment_on_way_sql") or {}).get("encrypted")),
            },
        }
    )


@app.route("/api/go/summary", methods=["POST"])
def api_go_summary():
    payload = _json_payload()
    go = str(payload.get("go", "") or "").strip().upper()
    if not go:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    result = fetch_go_color_summary(go)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/go/ppo-mapping", methods=["POST"])
def api_go_ppo_mapping():
    payload = _json_payload()
    go = str(payload.get("go", "") or "").strip().upper()
    if not go:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    result = fetch_go_ppo_mapping_only(go)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/coi/preview", methods=["POST"])
def api_coi_preview():
    payload = _json_payload()
    go = str(payload.get("go", "") or "").strip().upper()
    if not go:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    result = build_coi_preview(go, prefer_mes_cache=bool(payload.get("prefer_mes_cache", True)))
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/fabric-left/upload", methods=["POST"])
def api_fabric_left_upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    result = save_uploaded_fabric(file.read(), file.filename or "uploaded.xlsx")
    return jsonify(result)


@app.route("/api/fabric-left/data")
def api_fabric_left_data():
    rows = load_fabric_stock()
    meta = get_fabric_stock_meta()
    return jsonify({"ok": True, "rows": rows, **meta})


@app.route("/api/fabric-left/reload-default", methods=["POST"])
def api_fabric_left_reload_default():
    ok = preload_default_fabric(force_parse=True)
    if not ok:
        return jsonify({"ok": False, "error": "Default fabric Excel not found"}), 404
    return jsonify({"ok": True, "rows": load_fabric_stock(), **get_fabric_stock_meta()})


@app.route("/api/fabric-left/go", methods=["POST"])
@app.route("/api/fabric-left/map-go-colors", methods=["POST"])
def api_fabric_left_go():
    payload = _json_payload()
    go = str(payload.get("go", "") or "").strip().upper()
    if not go:
        return jsonify({"ok": False, "error": "GO number required"}), 400
    result = build_fabric_rows(go)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/gw/query", methods=["POST"])
def api_gw_query():
    payload = _json_payload()
    go_list = _parse_go_list(payload)
    if not go_list:
        return jsonify({"ok": False, "error": "Please provide GO list"}), 400
    flags = payload.get("factory_flags")
    result = query_gw_by_go_list(
        go_list,
        factory_flags=flags if isinstance(flags, list) else None,
        backend=str(payload.get("backend", "auto") or "auto"),
    )
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/ppo/browse", methods=["POST"])
@app.route("/api/thesis/ppo-browse", methods=["POST"])
def api_ppo_browse():
    payload = _json_payload()
    ppo = str(payload.get("ppo", "") or "").strip().upper()
    go = str(payload.get("go", "") or "").strip().upper()
    if not ppo:
        return jsonify({"ok": False, "error": "PPO number required"}), 400
    result = fetch_ppo_browse(ppo, go=go)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/ppo/detail", methods=["POST"])
def api_ppo_detail():
    payload = _json_payload()
    ppo = str(payload.get("ppo", "") or "").strip().upper()
    result = _build_ppo_detail_payload(ppo)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/ppo/status", methods=["POST"])
@app.route("/api/tendam/ppo-status", methods=["POST"])
def api_ppo_status():
    payload = _json_payload()
    ppo = str(payload.get("ppo", "") or "").strip().upper()
    if not ppo:
        return jsonify({"ok": False, "error": "PPO number required"}), 400
    result = fetch_tendam_ppo_status(ppo)
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/api/mes/cutting", methods=["POST"])
def api_mes_cutting():
    payload = _json_payload()
    go_list = _parse_go_list(payload)
    if not go_list:
        return jsonify({"ok": False, "error": "Please provide GO list"}), 400
    result = query_mes_cutting(go_list, prefer_cache=bool(payload.get("prefer_cache", True)))
    return jsonify({"ok": True, **result})


@app.route("/api/mes/cutting/cache/status")
def api_mes_cutting_cache_status():
    return jsonify({"ok": True, **_public_mes_cache_status(get_cutting_cache_status())})


@app.route("/api/mes/cutting/cache/clear", methods=["POST"])
def api_mes_cutting_cache_clear():
    return jsonify({"ok": True, **_public_mes_cache_status(clear_cutting_forecast_cache())})


if __name__ == "__main__":
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("APP_HOST", "127.0.0.1")
    validate_bind_security(host)
    start_background_services(wait_for_startup=True)
    try:
        port = int(os.environ.get("APP_PORT", "5070"))
    except ValueError:
        port = 5070
    app.run(host=host, port=port, debug=False)
