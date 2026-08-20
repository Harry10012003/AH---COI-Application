from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.engine.sql_live_engine import (  # noqa: E402
    _CACHE_READY_STATE,
    _is_cache_refresh_due,
    _load_all_live_go_rows,
    _load_go_cache_profile,
    _load_local_go_feed_rows,
    _record_go_feed_rows,
    _sync_recent_go_feed_rows,
    build_live_coi_sheet,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm and refresh GO sheet cache")
    parser.add_argument("--go", action="append", default=[], help="Explicit GO number, can repeat")
    parser.add_argument("--limit", type=int, default=0, help="Max GO to process; 0 = all")
    parser.add_argument("--due-only", action="store_true", help="Only refresh GO that are missing or due for retry")
    parser.add_argument("--states", default="", help="Comma-separated cache states to process")
    parser.add_argument("--summary-json", default="", help="Optional path to write summary JSON")
    return parser.parse_args()


def _normalize_go_list(values: list[str]) -> list[str]:
    return [str(item or "").strip().upper() for item in values if str(item or "").strip()]


def _wanted_states(raw: str) -> set[str]:
    return {str(item or "").strip().upper() for item in str(raw or "").split(",") if str(item or "").strip()}


def _select_go_rows(args: argparse.Namespace) -> list[dict]:
    explicit_go = _normalize_go_list(args.go)
    rows = _load_local_go_feed_rows()
    if not rows:
        rows = _load_all_live_go_rows()
        _record_go_feed_rows(rows)
    else:
        _sync_recent_go_feed_rows(force=True)
        rows = _load_local_go_feed_rows()
    if explicit_go:
        wanted = set(explicit_go)
        return [row for row in rows if str(row.get("go_no") or "").strip().upper() in wanted]

    selected: list[dict] = []
    wanted_states = _wanted_states(args.states)
    for row in rows:
        go_no = str(row.get("go_no") or "").strip().upper()
        if not go_no:
            continue
        profile = _load_go_cache_profile(go_no)
        state = str(profile.get("state") or "").strip().upper()
        if wanted_states and state not in wanted_states:
            continue
        if args.due_only:
            if not state:
                selected.append(row)
                continue
            if state != _CACHE_READY_STATE and _is_cache_refresh_due(profile):
                selected.append(row)
                continue
            stamp = str(row.get("modify_date") or row.get("create_date") or "")
            cached_at = str(profile.get("snapshot_updated_at") or "")
            if stamp and cached_at and stamp > cached_at:
                selected.append(row)
            continue
        selected.append(row)
    return selected


def main() -> int:
    args = _parse_args()
    rows = _select_go_rows(args)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    total = len(rows)
    print(f"[preload] selected {total} GO")
    if not rows:
        return 0

    counters: Counter[str] = Counter()
    failures: list[dict] = []
    for index, row in enumerate(rows, start=1):
        go_no = str(row.get("go_no") or "").strip().upper()
        try:
            payload = build_live_coi_sheet(
                go_no,
                prefer_mes_cache=True,
                allow_live_mes=False,
                use_snapshot=False,
                persist_snapshot=True,
                allow_inline_build=False,
                snapshot_built_from="seed-script",
                prefer_source_cache=True,
                allow_live_sample_status=False,
                allow_live_size_breakdown=False,
            )
            if not payload.get("ok"):
                error_text = str(payload.get("error") or "Unknown build error")
                failures.append({"go_no": go_no, "error": error_text})
                counters["ERROR"] += 1
                print(f"[{index}/{total}] {go_no} -> ERROR | {error_text}")
                continue
            profile = dict(payload.get("cache_profile") or _load_go_cache_profile(go_no) or {})
            state = str(profile.get("state") or _CACHE_READY_STATE).strip().upper() or _CACHE_READY_STATE
            counters[state] += 1
            print(f"[{index}/{total}] {go_no} -> {state} | rows={payload.get('row_count', 0)}")
        except Exception as exc:  # pragma: no cover - operational script
            failures.append({"go_no": go_no, "error": str(exc)})
            counters["ERROR"] += 1
            print(f"[{index}/{total}] {go_no} -> ERROR | {exc}")

    summary = {
        "selected": total,
        "states": dict(sorted(counters.items())),
        "failures": failures[:200],
        "failure_count": len(failures),
    }
    print("[preload] summary", json.dumps(summary, ensure_ascii=False))
    if args.summary_json:
        out_path = Path(args.summary_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
