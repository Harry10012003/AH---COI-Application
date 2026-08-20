from __future__ import annotations

import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.sources import CACHE_DIR, PROJECT_DIR

_AUDIT_INTERVAL_SEC = 6 * 60 * 60
_AUDIT_TIMEOUT_SEC = 2 * 60 * 60
_audit_lock = threading.Lock()
_audit_state = {
    "running": False,
    "started_at": "",
    "last_started_at": "",
    "last_finished_at": "",
    "last_success_at": "",
    "last_error": "",
    "last_summary": {},
    "thread": None,
}


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _summary_json_path() -> Path:
    return CACHE_DIR / "audit_color_code_summary.json"


def _findings_csv_path() -> Path:
    return CACHE_DIR / "audit_color_code_findings.csv"


def _load_summary() -> dict:
    path = _summary_json_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _run_once() -> None:
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "scripts" / "audit_color_code_semantics.py"),
        "--out-dir",
        str(CACHE_DIR),
    ]
    with _audit_lock:
        _audit_state["last_started_at"] = _now_text()
        _audit_state["last_error"] = ""
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT := PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=_AUDIT_TIMEOUT_SEC,
            check=False,
        )
        summary = _load_summary()
        error_bits = []
        if completed.returncode != 0:
            error_bits.append(f"exit={completed.returncode}")
        stderr_text = str(completed.stderr or "").strip()
        if stderr_text:
            error_bits.append(stderr_text.splitlines()[-1])
        with _audit_lock:
            _audit_state["last_finished_at"] = _now_text()
            _audit_state["last_summary"] = summary
            if error_bits:
                _audit_state["last_error"] = " | ".join(error_bits)
            else:
                _audit_state["last_success_at"] = _audit_state["last_finished_at"]
                _audit_state["last_error"] = ""
    except Exception as exc:
        with _audit_lock:
            _audit_state["last_finished_at"] = _now_text()
            _audit_state["last_error"] = str(exc)


def _audit_worker_loop() -> None:
    while True:
        with _audit_lock:
            _audit_state["running"] = True
            if not _audit_state["started_at"]:
                _audit_state["started_at"] = _now_text()
        _run_once()
        time.sleep(_AUDIT_INTERVAL_SEC)


def ensure_color_audit_worker() -> None:
    with _audit_lock:
        thread = _audit_state.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
        worker = threading.Thread(target=_audit_worker_loop, name="color-code-audit", daemon=True)
        _audit_state["thread"] = worker
        worker.start()


def color_audit_priority_go_nos(limit: int = 200) -> list[str]:
    max_items = max(0, int(limit or 0))
    if max_items <= 0:
        return []
    path = _findings_csv_path()
    if not path.exists():
        return []
    result: list[str] = []
    seen: set[str] = set()
    try:
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                go_key = str((row or {}).get("go_no") or "").strip().upper()
                if not go_key or go_key in seen:
                    continue
                seen.add(go_key)
                result.append(go_key)
                if len(result) >= max_items:
                    break
    except Exception:
        return []
    return result


def color_audit_status() -> dict:
    with _audit_lock:
        state = dict(_audit_state)
    thread = state.pop("thread", None)
    state["thread_alive"] = bool(isinstance(thread, threading.Thread) and thread.is_alive())
    if not state.get("last_summary"):
        state["last_summary"] = _load_summary()
    state["summary_json"] = str(_summary_json_path())
    state["findings_csv"] = str(_findings_csv_path())
    return state
