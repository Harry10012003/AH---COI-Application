from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.engine.sql_live_engine import _connect, _load_go_jo_color_qty, build_live_coi_sheet, list_live_go
from backend.scraper.gw_client import _fetch_go_report_detail


def _norm_code(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text.zfill(2)
    return text.upper()


def _norm_desc(value: object) -> str:
    return str(value or "").strip().upper()


def _pick_color_key(code: object, desc: object) -> str:
    code_key = _norm_code(code)
    if code_key and code_key not in {"COLOR TOTAL :", "TOTAL"}:
        return code_key
    return _norm_desc(desc)


def _sheet_b_by_color(go: str) -> dict[str, float]:
    payload = build_live_coi_sheet(
        go,
        prefer_mes_cache=True,
        allow_live_mes=False,
        use_snapshot=False,
        persist_snapshot=False,
    )
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "sheet build failed"))
    totals: dict[str, float] = defaultdict(float)
    for row in payload.get("rows") or []:
        if str(row.get("Type") or "").strip().upper() != "B":
            continue
        color_key = _pick_color_key(row.get("COLOR_CODE"), row.get("COLOR_DESC"))
        if color_key:
            totals[color_key] += float(row.get("Qty (pcs)") or 0.0)
    return dict(totals)


def _sheet_b_by_lot_color(go: str) -> dict[tuple[str, str], float]:
    payload = build_live_coi_sheet(
        go,
        prefer_mes_cache=True,
        allow_live_mes=False,
        use_snapshot=False,
        persist_snapshot=False,
    )
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "sheet build failed"))
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for row in payload.get("rows") or []:
        if str(row.get("Type") or "").strip().upper() != "B":
            continue
        jo_no = str(row.get("JOB ORDER NO") or "").strip().upper()
        color_key = _pick_color_key(row.get("COLOR_CODE"), row.get("COLOR_DESC"))
        if jo_no and color_key:
            totals[(jo_no, color_key)] += float(row.get("Qty (pcs)") or 0.0)
    return dict(totals)


def _expected_by_color(go: str) -> tuple[dict[str, float], str]:
    report = _fetch_go_report_detail(go)

    report_colors: dict[str, float] = defaultdict(float)
    for row in report.get("colors") or []:
        color_key = _pick_color_key(row.get("color_code"), row.get("color_desc"))
        if color_key:
            report_colors[color_key] += float(row.get("qty") or row.get("total") or 0.0)
    if report_colors:
        return dict(report_colors), "go_summary"

    with _connect() as conn:
        rows = _load_go_jo_color_qty(conn.cursor(), go)
    sql_totals: dict[str, float] = defaultdict(float)
    for row in rows:
        color_key = _pick_color_key(row.get("color_code"), row.get("color_desc"))
        if color_key:
            sql_totals[color_key] += float(row.get("qty") or 0.0)
    return dict(sql_totals), "sql_jo_color"


def _expected_by_lot_color(go: str) -> tuple[dict[tuple[str, str], float], str]:
    report = _fetch_go_report_detail(go)
    report_pairs: dict[tuple[str, str], float] = defaultdict(float)
    for row in report.get("color_breakdown_rows") or []:
        lot_no = str(row.get("lot") or "").strip()
        color_key = _pick_color_key(
            row.get("color_code") or row.get("cust_color_code"),
            row.get("color_desc") or row.get("cust_color_desc"),
        )
        if lot_no and color_key:
            report_pairs[(lot_no, color_key)] += float(row.get("qty") or 0.0)
    if report_pairs:
        return dict(report_pairs), "go_breakdown"

    with _connect() as conn:
        rows = _load_go_jo_color_qty(conn.cursor(), go)
    sql_pairs: dict[tuple[str, str], float] = defaultdict(float)
    for row in rows:
        jo_no = str(row.get("jo_no") or "").strip().upper()
        color_key = _pick_color_key(row.get("color_code"), row.get("color_desc"))
        if jo_no and color_key:
            sql_pairs[(jo_no, color_key)] += float(row.get("qty") or 0.0)
    return dict(sql_pairs), "sql_jo_color"


def _compare_maps(expected: dict, actual: dict) -> list[str]:
    mismatches: list[str] = []
    keys = sorted(set(expected) | set(actual), key=lambda item: str(item))
    for key in keys:
        expected_qty = round(float(expected.get(key, 0.0)), 3)
        actual_qty = round(float(actual.get(key, 0.0)), 3)
        if abs(expected_qty - actual_qty) > 0.001:
            mismatches.append(f"{key}:{expected_qty}->{actual_qty}")
    return mismatches


def _go_list_from_args(args: argparse.Namespace) -> list[str]:
    explicit = [str(item or "").strip().upper() for item in (args.go or []) if str(item or "").strip()]
    if explicit:
        return explicit
    feed = list_live_go(limit=max(args.limit, 1))
    return [
        str(row.get("go_no") or "").strip().upper()
        for row in (feed.get("rows") or [])[: max(args.limit, 1)]
        if str(row.get("go_no") or "").strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit COI Qty consistency against GO report / SQL")
    parser.add_argument("--go", action="append", help="GO number to audit. Can be provided multiple times.")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent GO rows to audit when --go is omitted")
    parser.add_argument("--out", type=Path, default=Path("data/cache/audit_qty_consistency.csv"))
    args = parser.parse_args()

    go_list = _go_list_from_args(args)
    results: list[dict[str, object]] = []

    for index, go in enumerate(go_list, start=1):
        try:
            expected_color, color_source = _expected_by_color(go)
            actual_color = _sheet_b_by_color(go)
            color_mismatches = _compare_maps(expected_color, actual_color)

            expected_lot_color, lot_source = _expected_by_lot_color(go)
            actual_lot_color = _sheet_b_by_lot_color(go)
            lot_mismatches = _compare_maps(expected_lot_color, actual_lot_color)

            status = "OK" if not color_mismatches and not lot_mismatches else "MISMATCH"
            detail_parts = []
            if color_mismatches:
                detail_parts.append("COLOR=" + " | ".join(color_mismatches[:20]))
            if lot_mismatches:
                detail_parts.append("LOT_COLOR=" + " | ".join(lot_mismatches[:30]))
            detail = " || ".join(detail_parts)
        except Exception as exc:
            status = "ERROR"
            color_source = ""
            lot_source = ""
            detail = str(exc)

        results.append(
            {
                "index": index,
                "go_no": go,
                "status": status,
                "color_source": color_source,
                "lot_color_source": lot_source,
                "detail": detail,
            }
        )
        print(index, go, status, color_source, lot_source, detail)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["index", "go_no", "status", "color_source", "lot_color_source", "detail"],
        )
        writer.writeheader()
        writer.writerows(results)

    ok_count = sum(1 for row in results if row["status"] == "OK")
    mismatch_count = sum(1 for row in results if row["status"] == "MISMATCH")
    error_count = sum(1 for row in results if row["status"] == "ERROR")
    print(f"SUMMARY OK={ok_count} MISMATCH={mismatch_count} ERROR={error_count}")
    print(f"CSV: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
