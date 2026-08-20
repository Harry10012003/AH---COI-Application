from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.engine.sql_live_engine import (  # noqa: E402
    _SNAPSHOT_DB,
    _connect,
    _load_go_fabric_rows,
    _load_go_head,
    _load_go_jo_color_qty,
    _load_go_lots,
    _load_go_ppo_mapping,
)
from backend.scraper.gw_client import _fetch_go_report_detail, fetch_ppo_browse  # noqa: E402


SNAPSHOT_DB = _SNAPSHOT_DB


def _to_float(value: object) -> float:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_int(value: object) -> int:
    return int(round(_to_float(value)))


def _normalize_text(value: object) -> str:
    text = str(value or "").upper().strip()
    return " ".join(text.replace("\xa0", " ").replace("@", " ").split())


def _normalize_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _color_key(code: object, desc: object) -> str:
    code_text = str(code or "").strip().upper()
    if code_text:
        return code_text
    return _normalize_text(desc)


def _combo_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        _prefix, suffix = text.split("@", 1)
        suffix = suffix.strip()
        if suffix:
            return _normalize_text(suffix)
    return _normalize_text(text)


def _snapshot_go_list(limit: int = 0, since: str = "", explicit_go: list[str] | None = None) -> list[str]:
    manual = [str(item or "").strip().upper() for item in (explicit_go or []) if str(item or "").strip()]
    if manual:
        return manual

    conn = sqlite3.connect(str(SNAPSHOT_DB))
    try:
        cur = conn.cursor()
        sql = """
            SELECT go_no
            FROM go_feed
            WHERE factory_code IN ('EGV', 'EAV')
        """
        params: list[object] = []
        since_text = _normalize_date(since)
        if since_text:
            sql += " AND substr(COALESCE(modify_date, create_date), 1, 10) >= ?"
            params.append(since_text)
        sql += " ORDER BY COALESCE(modify_date, create_date) DESC, go_no DESC"
        if limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        cur.execute(sql, params)
        return [str(row[0] or "").strip().upper() for row in cur.fetchall() if str(row[0] or "").strip()]
    finally:
        conn.close()


def _load_processed_go(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    processed: set[str] = set()
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            go = str(row.get("go_no") or "").strip().upper()
            if go:
                processed.add(go)
    return processed


def _sql_mapping_pairs(rows: list[dict]) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for row in rows or []:
        ppo = str(row.get("ppo_no") or "").strip().upper()
        lot = _to_int(row.get("lot_no"))
        if ppo and lot > 0:
            pairs.add((ppo, lot))
    return pairs


def _go_mapping_pairs(report: dict) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for row in report.get("ppo_mapping") or []:
        ppo = str(row.get("ppo") or "").strip().upper()
        lot = _to_int(row.get("lot"))
        if ppo and lot > 0:
            pairs.add((ppo, lot))
    return pairs


def _lot_jo_map_from_sql(rows: list[dict]) -> dict[tuple[int, str], dict]:
    result: dict[tuple[int, str], dict] = {}
    for row in rows or []:
        lot = _to_int(row.get("lot_no"))
        jo = str(row.get("jo_no") or "").strip().upper()
        if lot <= 0 or not jo:
            continue
        result[(lot, jo)] = {
            "qty": round(_to_float(row.get("qty")), 3),
            "date": _normalize_date(row.get("buyer_po_del_date")),
        }
    return result


def _lot_jo_map_from_go(report: dict) -> dict[tuple[int, str], dict]:
    result: dict[tuple[int, str], dict] = {}
    for row in report.get("lot_rows") or []:
        lot = _to_int(row.get("lot"))
        jo = str(row.get("job_order_no") or "").strip().upper()
        if lot <= 0 or not jo:
            continue
        result[(lot, jo)] = {
            "qty": round(_to_float(row.get("qty")), 3),
            "date": _normalize_date(row.get("buyer_po_del_date")),
        }
    return result


def _lot_totals(rows: dict[tuple[int, str], dict]) -> dict[int, float]:
    totals: dict[int, float] = defaultdict(float)
    for (lot, _jo), item in rows.items():
        totals[lot] += _to_float(item.get("qty"))
    return {key: round(value, 3) for key, value in totals.items()}


def _lot_dates(rows: dict[tuple[int, str], dict]) -> dict[int, set[str]]:
    dates: dict[int, set[str]] = defaultdict(set)
    for (lot, _jo), item in rows.items():
        date = str(item.get("date") or "").strip()
        if date:
            dates[lot].add(date)
    return dates


def _lot_color_qty_from_sql(rows: list[dict]) -> dict[tuple[int, str], float]:
    result: dict[tuple[int, str], float] = defaultdict(float)
    for row in rows or []:
        lot = _to_int(row.get("lot_no"))
        color = _color_key(row.get("color_code"), row.get("color_desc"))
        if lot <= 0 or not color:
            continue
        result[(lot, color)] += _to_float(row.get("qty"))
    return {key: round(value, 3) for key, value in result.items()}


def _lot_color_qty_from_go(report: dict) -> dict[tuple[int, str], float]:
    result: dict[tuple[int, str], float] = defaultdict(float)
    for row in report.get("color_breakdown_rows") or []:
        lot = _to_int(row.get("lot"))
        color = _color_key(
            row.get("color_code") or row.get("cust_color_code"),
            row.get("color_desc") or row.get("cust_color_desc"),
        )
        if lot <= 0 or not color:
            continue
        result[(lot, color)] += _to_float(row.get("qty"))
    return {key: round(value, 3) for key, value in result.items()}


def _sql_combo_keys(rows: list[dict]) -> set[str]:
    keys: set[str] = set()
    for row in rows or []:
        fabric_type = str(row.get("fabric_type") or "").strip().upper()
        combo = _combo_key(row.get("combo_name"))
        color = _color_key(row.get("color_code"), "")
        token = combo or color
        if fabric_type and token:
            keys.add(f"{fabric_type}|{token}")
    return keys


def _go_combo_keys(report: dict) -> set[str]:
    keys: set[str] = set()
    for row in report.get("knit_bom_rows") or []:
        fabric_type = str(row.get("fabric_type_hint") or "").strip().upper()
        combo = _combo_key(row.get("combo_name"))
        color = _color_key(row.get("color_code"), row.get("color_desc"))
        token = combo or color
        if fabric_type and token:
            keys.add(f"{fabric_type}|{token}")
    return keys


def _browse_payload(ppos: list[str], go: str) -> dict:
    mapping_pairs: set[tuple[str, int]] = set()
    lot_totals: dict[int, float] = defaultdict(float)
    lot_dates: dict[int, set[str]] = defaultdict(set)
    combo_keys: set[str] = set()
    errors: list[str] = []

    for ppo in ppos:
        payload = fetch_ppo_browse(ppo, go=go)
        if not payload.get("ok"):
            errors.append(f"{ppo}:{payload.get('error', 'browse failed')}")
            continue
        for row in payload.get("rows") or []:
            lot = _to_int(row.get("lot"))
            if lot <= 0:
                continue
            mapping_pairs.add((ppo, lot))
            lot_totals[lot] += _to_float(row.get("qty"))
            date = _normalize_date(row.get("del_date") or row.get("bpo_date"))
            if date:
                lot_dates[lot].add(date)
            combo = _combo_key(row.get("combo"))
            if combo:
                combo_keys.add(combo)

    return {
        "mapping_pairs": mapping_pairs,
        "lot_totals": {key: round(value, 3) for key, value in lot_totals.items()},
        "lot_dates": lot_dates,
        "combo_keys": combo_keys,
        "errors": errors,
    }


def _diff_set(left: set, right: set, label_left: str, label_right: str, limit: int = 12) -> list[str]:
    issues: list[str] = []
    missing_left = sorted(left - right)[:limit]
    missing_right = sorted(right - left)[:limit]
    if missing_left:
        issues.append(f"{label_right}_missing={missing_left}")
    if missing_right:
        issues.append(f"{label_left}_missing={missing_right}")
    return issues


def _diff_number_map(left: dict, right: dict, label: str, limit: int = 20) -> list[str]:
    issues: list[str] = []
    keys = sorted(set(left) | set(right), key=lambda item: str(item))
    for key in keys:
        left_value = round(_to_float(left.get(key)), 3)
        right_value = round(_to_float(right.get(key)), 3)
        if abs(left_value - right_value) > 0.001:
            issues.append(f"{label}:{key}:{left_value}->{right_value}")
            if len(issues) >= limit:
                break
    return issues


def _diff_date_map(left: dict[int, set[str]], right: dict[int, set[str]], label: str, limit: int = 20) -> list[str]:
    issues: list[str] = []
    keys = sorted(set(left) | set(right))
    for key in keys:
        left_value = sorted(left.get(key) or [])
        right_value = sorted(right.get(key) or [])
        if left_value != right_value:
            issues.append(f"{label}:{key}:{left_value}->{right_value}")
            if len(issues) >= limit:
                break
    return issues


def audit_go(go: str) -> dict:
    go_key = str(go or "").strip().upper()
    with _connect() as conn:
        cursor = conn.cursor()
        head = _load_go_head(cursor, go_key)
        if not head:
            return {
                "go_no": go_key,
                "status": "ERROR",
                "issues": ["sql_head_missing"],
                "detail": ["GO not found in SQL"],
            }
        sql_lots = _load_go_lots(cursor, go_key)
        sql_mapping = _load_go_ppo_mapping(cursor, go_key)
        sql_color_qty = _load_go_jo_color_qty(cursor, go_key)
        sql_fabric = _load_go_fabric_rows(cursor, go_key)

    report = _fetch_go_report_detail(go_key)
    issues: list[str] = []
    detail: list[str] = []

    sql_mapping_pairs = _sql_mapping_pairs(sql_mapping)
    sql_lot_jo = _lot_jo_map_from_sql(sql_lots)
    sql_lot_totals = _lot_totals(sql_lot_jo)
    sql_lot_dates = _lot_dates(sql_lot_jo)
    sql_color_map = _lot_color_qty_from_sql(sql_color_qty)
    sql_combo_set = _sql_combo_keys(sql_fabric)

    if not report.get("ok"):
        issues.append("go_report_error")
        detail.append(str(report.get("error") or "Cannot fetch GO report"))
        go_mapping_pairs: set[tuple[str, int]] = set()
        go_lot_jo: dict[tuple[int, str], dict] = {}
        go_lot_totals: dict[int, float] = {}
        go_lot_dates: dict[int, set[str]] = {}
        go_color_map: dict[tuple[int, str], float] = {}
        go_combo_set: set[str] = set()
    else:
        go_mapping_pairs = _go_mapping_pairs(report)
        go_lot_jo = _lot_jo_map_from_go(report)
        go_lot_totals = _lot_totals(go_lot_jo)
        go_lot_dates = _lot_dates(go_lot_jo)
        go_color_map = _lot_color_qty_from_go(report)
        go_combo_set = _go_combo_keys(report)

        if sql_mapping_pairs != go_mapping_pairs:
            issues.append("mapping_go_sql")
            detail.extend(_diff_set(sql_mapping_pairs, go_mapping_pairs, "sql", "go"))

        if set(sql_lot_jo) != set(go_lot_jo):
            issues.append("lot_jo_go_sql")
            detail.extend(_diff_set(set(sql_lot_jo), set(go_lot_jo), "sql", "go"))

        lot_qty_diff = _diff_number_map(sql_lot_totals, go_lot_totals, "lot_qty_go_sql")
        if lot_qty_diff:
            issues.append("lot_qty_go_sql")
            detail.extend(lot_qty_diff)

        lot_date_diff = _diff_date_map(sql_lot_dates, go_lot_dates, "lot_date_go_sql")
        if lot_date_diff:
            issues.append("lot_date_go_sql")
            detail.extend(lot_date_diff)

        color_qty_diff = _diff_number_map(sql_color_map, go_color_map, "lot_color_qty_go_sql")
        if color_qty_diff:
            issues.append("color_qty_go_sql")
            detail.extend(color_qty_diff)

        if sql_combo_set != go_combo_set:
            issues.append("combo_go_sql")
            detail.extend(_diff_set(sql_combo_set, go_combo_set, "sql", "go"))

    ppo_list = sorted(
        {
            ppo
            for ppo, _lot in sql_mapping_pairs | go_mapping_pairs
            if ppo
        }
    )
    browse = _browse_payload(ppo_list, go_key)
    browse_pairs = browse.get("mapping_pairs") or set()
    browse_lot_totals = browse.get("lot_totals") or {}
    browse_lot_dates = browse.get("lot_dates") or {}

    if ppo_list and browse.get("errors"):
        issues.append("ppo_browse_error")
        detail.extend(list(browse.get("errors") or [])[:20])

    if ppo_list:
        if sql_mapping_pairs != browse_pairs:
            issues.append("mapping_sql_ppo")
            detail.extend(_diff_set(sql_mapping_pairs, browse_pairs, "sql", "ppo"))

        if go_mapping_pairs and go_mapping_pairs != browse_pairs:
            issues.append("mapping_go_ppo")
            detail.extend(_diff_set(go_mapping_pairs, browse_pairs, "go", "ppo"))

        browse_sql_qty_diff = _diff_number_map(sql_lot_totals, browse_lot_totals, "lot_qty_sql_ppo")
        if browse_sql_qty_diff:
            issues.append("lot_qty_sql_ppo")
            detail.extend(browse_sql_qty_diff)

        browse_sql_date_diff = _diff_date_map(sql_lot_dates, browse_lot_dates, "lot_date_sql_ppo")
        if browse_sql_date_diff:
            issues.append("lot_date_sql_ppo")
            detail.extend(browse_sql_date_diff)

    if ppo_list and go_lot_totals:
        browse_go_qty_diff = _diff_number_map(go_lot_totals, browse_lot_totals, "lot_qty_go_ppo")
        if browse_go_qty_diff:
            issues.append("lot_qty_go_ppo")
            detail.extend(browse_go_qty_diff)

    if ppo_list and go_lot_dates:
        browse_go_date_diff = _diff_date_map(go_lot_dates, browse_lot_dates, "lot_date_go_ppo")
        if browse_go_date_diff:
            issues.append("lot_date_go_ppo")
            detail.extend(browse_go_date_diff)

    result_status = "OK" if not issues else "MISMATCH"
    return {
        "go_no": go_key,
        "status": result_status,
        "issues": sorted(set(issues)),
        "issue_count": len(sorted(set(issues))),
        "detail": detail[:120],
        "go_report_ok": bool(report.get("ok")),
        "ppo_count": len(ppo_list),
        "ppo_browse_error_count": len(browse.get("errors") or []),
    }


def _summary_row(index: int, item: dict) -> dict[str, object]:
    detail = " | ".join(str(part) for part in (item.get("detail") or [])[:12])
    return {
        "index": index,
        "go_no": item.get("go_no", ""),
        "status": item.get("status", ""),
        "issue_count": item.get("issue_count", 0),
        "issues": ",".join(item.get("issues") or []),
        "go_report_ok": item.get("go_report_ok", False),
        "ppo_count": item.get("ppo_count", 0),
        "ppo_browse_error_count": item.get("ppo_browse_error_count", 0),
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GO workflow consistency across GO report, SQL, and PPO browse")
    parser.add_argument("--go", action="append", help="Specific GO number. Can be provided multiple times.")
    parser.add_argument("--limit", type=int, default=0, help="Limit GO count when --go is omitted")
    parser.add_argument("--since", default="", help="Only include GO on or after this date (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=Path("data/cache/audit_workflow_consistency.csv"))
    parser.add_argument("--jsonl", type=Path, default=Path("data/cache/audit_workflow_consistency.jsonl"))
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing output files and reprocess all GO")
    args = parser.parse_args()

    go_list = _snapshot_go_list(limit=args.limit, since=args.since, explicit_go=args.go)
    processed = set() if args.no_resume else _load_processed_go(args.out)
    pending = [go for go in go_list if go not in processed]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    csv_exists = args.out.exists() and not args.no_resume
    jsonl_mode = "a" if args.jsonl.exists() and not args.no_resume else "w"
    csv_mode = "a" if csv_exists else "w"

    started_at = time.time()
    processed_count = len(processed)
    ok_count = 0
    mismatch_count = 0
    error_count = 0

    with args.out.open(csv_mode, newline="", encoding="utf-8") as csv_handle, args.jsonl.open(
        jsonl_mode, encoding="utf-8"
    ) as jsonl_handle:
        fieldnames = [
            "index",
            "go_no",
            "status",
            "issue_count",
            "issues",
            "go_report_ok",
            "ppo_count",
            "ppo_browse_error_count",
            "detail",
        ]
        writer = csv.DictWriter(csv_handle, fieldnames=fieldnames)
        if csv_mode == "w":
            writer.writeheader()

        total = len(pending)
        for offset, go in enumerate(pending, start=1):
            try:
                result = audit_go(go)
            except Exception as exc:
                result = {
                    "go_no": go,
                    "status": "ERROR",
                    "issue_count": 1,
                    "issues": ["runtime_error"],
                    "detail": [str(exc)],
                    "go_report_ok": False,
                    "ppo_count": 0,
                    "ppo_browse_error_count": 0,
                }

            status = str(result.get("status") or "").upper()
            if status == "OK":
                ok_count += 1
            elif status == "MISMATCH":
                mismatch_count += 1
            else:
                error_count += 1

            writer.writerow(_summary_row(processed_count + offset, result))
            csv_handle.flush()
            jsonl_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            jsonl_handle.flush()

            elapsed = round(time.time() - started_at, 1)
            print(
                f"{processed_count + offset}/{processed_count + total} {go} {status} "
                f"issues={result.get('issue_count', 0)} elapsed={elapsed}s"
            )

    print(
        "SUMMARY",
        f"OK={ok_count}",
        f"MISMATCH={mismatch_count}",
        f"ERROR={error_count}",
        f"TOTAL_NEW={len(pending)}",
        f"CSV={args.out}",
        f"JSONL={args.jsonl}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
