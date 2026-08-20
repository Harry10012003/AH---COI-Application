from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.engine.sql_live_engine import _connect, _load_go_jo_color_qty  # noqa: E402

GO_REPORT_CACHE_DIR = ROOT / "data" / "cache" / "go_report_detail"


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").upper().replace(" ", " ").split())


def _load_go_report_cache(go: str) -> dict:
    path = GO_REPORT_CACHE_DIR / f"{str(go or '').strip().upper()}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _iter_cached_go() -> list[str]:
    if not GO_REPORT_CACHE_DIR.exists():
        return []
    return sorted(path.stem.upper() for path in GO_REPORT_CACHE_DIR.glob("*.json"))


def _extract_conflicts(go: str, payload: dict) -> list[dict]:
    rows = payload.get("color_breakdown_rows") or []
    conflicts = []
    for row in rows:
        gmt_code = str(row.get("gmt_color_code") or row.get("color_code") or "").strip().upper()
        cust_code = str(row.get("cust_color_code") or "").strip().upper()
        gmt_desc = str(row.get("gmt_color_desc") or row.get("color_desc") or "").strip()
        cust_desc = str(row.get("cust_color_desc") or "").strip()
        if not gmt_code or not cust_code or gmt_code == cust_code:
            continue
        conflicts.append(
            {
                "go_no": go,
                "lot": str(row.get("lot") or "").strip(),
                "qty": row.get("qty") or 0,
                "gmt_color_code": gmt_code,
                "gmt_color_desc": gmt_desc,
                "cust_color_code": cust_code,
                "cust_color_desc": cust_desc,
                "desc_same": int(_normalize_text(gmt_desc) == _normalize_text(cust_desc)),
            }
        )
    return conflicts


def _load_sql_color_rows(go: str) -> list[dict]:
    with _connect() as conn:
        cursor = conn.cursor()
        return _load_go_jo_color_qty(cursor, go)


def _index_conflicts(conflicts: list[dict]) -> tuple[dict[tuple[int, str], list[dict]], dict[tuple[int, str], list[dict]]]:
    by_desc: dict[tuple[int, str], list[dict]] = defaultdict(list)
    by_cust: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for item in conflicts:
        lot_no = int(float(item.get("lot") or 0) or 0)
        desc_key = _normalize_text(item.get("gmt_color_desc"))
        cust_key = str(item.get("cust_color_code") or "").strip().upper()
        if lot_no > 0 and desc_key:
            by_desc[(lot_no, desc_key)].append(item)
        if lot_no > 0 and cust_key:
            by_cust[(lot_no, cust_key)].append(item)
    return by_desc, by_cust


def _analyze_go(go: str) -> tuple[list[dict], list[dict]]:
    payload = _load_go_report_cache(go)
    if not payload.get("ok"):
        return [], []
    conflicts = _extract_conflicts(go, payload)
    if not conflicts:
        return [], []
    by_desc, by_cust = _index_conflicts(conflicts)
    sql_rows = _load_sql_color_rows(go)
    findings: list[dict] = []
    for row in sql_rows:
        lot_no = int(round(float(row.get("lot_no") or 0)))
        sql_code = str(row.get("color_code") or "").strip().upper()
        sql_desc = str(row.get("color_desc") or "").strip()
        if lot_no <= 0:
            continue
        desc_matches = by_desc.get((lot_no, _normalize_text(sql_desc))) or []
        for item in desc_matches:
            if sql_code == str(item.get("cust_color_code") or "").strip().upper() and sql_code != str(item.get("gmt_color_code") or "").strip().upper():
                findings.append(
                    {
                        "go_no": go,
                        "lot": lot_no,
                        "jo_no": str(row.get("jo_no") or "").strip(),
                        "sql_color_code": sql_code,
                        "sql_color_desc": sql_desc,
                        "gmt_color_code": item.get("gmt_color_code"),
                        "gmt_color_desc": item.get("gmt_color_desc"),
                        "cust_color_code": item.get("cust_color_code"),
                        "cust_color_desc": item.get("cust_color_desc"),
                        "qty": row.get("qty") or 0,
                        "issue": "sql_code_matches_cust_not_gmt",
                    }
                )
        cust_matches = by_cust.get((lot_no, sql_code)) or []
        for item in cust_matches:
            if _normalize_text(sql_desc) != _normalize_text(item.get("gmt_color_desc")):
                findings.append(
                    {
                        "go_no": go,
                        "lot": lot_no,
                        "jo_no": str(row.get("jo_no") or "").strip(),
                        "sql_color_code": sql_code,
                        "sql_color_desc": sql_desc,
                        "gmt_color_code": item.get("gmt_color_code"),
                        "gmt_color_desc": item.get("gmt_color_desc"),
                        "cust_color_code": item.get("cust_color_code"),
                        "cust_color_desc": item.get("cust_color_desc"),
                        "qty": row.get("qty") or 0,
                        "issue": "cust_code_ambiguous_vs_gmt_desc",
                    }
                )
    dedup = []
    seen = set()
    for item in findings:
        key = tuple(item.get(field) for field in ["go_no", "lot", "jo_no", "sql_color_code", "sql_color_desc", "gmt_color_code", "cust_color_code", "issue"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return conflicts, dedup


def _write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in headers})


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit cached GO reports for GMT/Cust color-code conflicts")
    parser.add_argument("--limit", type=int, default=0, help="Max cached GO to scan; 0 = all cached GO")
    parser.add_argument("--go", action="append", default=[], help="Explicit GO to scan")
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "cache"), help="Output directory")
    args = parser.parse_args()

    go_list = [str(item or "").strip().upper() for item in args.go if str(item or "").strip()] or _iter_cached_go()
    if args.limit and args.limit > 0:
        go_list = go_list[: args.limit]

    conflict_rows: list[dict] = []
    finding_rows: list[dict] = []
    counters: Counter[str] = Counter()
    for index, go in enumerate(go_list, start=1):
        conflicts, findings = _analyze_go(go)
        if conflicts:
            counters["go_with_divergent_codes"] += 1
            conflict_rows.extend(conflicts)
        if findings:
            counters["go_with_sql_risk"] += 1
            finding_rows.extend(findings)
        if index % 200 == 0:
            print(f"[scan] {index}/{len(go_list)}")

    out_dir = Path(args.out_dir)
    conflict_csv = out_dir / "audit_color_code_conflicts.csv"
    findings_csv = out_dir / "audit_color_code_findings.csv"
    summary_json = out_dir / "audit_color_code_summary.json"

    _write_csv(
        conflict_csv,
        conflict_rows,
        ["go_no", "lot", "qty", "gmt_color_code", "gmt_color_desc", "cust_color_code", "cust_color_desc", "desc_same"],
    )
    _write_csv(
        findings_csv,
        finding_rows,
        ["go_no", "lot", "jo_no", "sql_color_code", "sql_color_desc", "gmt_color_code", "gmt_color_desc", "cust_color_code", "cust_color_desc", "qty", "issue"],
    )
    summary = {
        "scanned_go": len(go_list),
        "conflict_row_count": len(conflict_rows),
        "finding_row_count": len(finding_rows),
        "counters": dict(sorted(counters.items())),
        "conflict_csv": str(conflict_csv),
        "findings_csv": str(findings_csv),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
