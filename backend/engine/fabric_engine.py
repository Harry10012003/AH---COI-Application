from __future__ import annotations

import io
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

from backend.scraper.gw_client import _fetch_go_report_detail, fetch_ppo_fabric_combos
from backend.scraper.mes_client import get_cutting_forecast
from backend.sources import FABRIC_LEFT_DEFAULT_XLSX, FABRIC_UPLOAD_CACHE_JSON
from backend.utils import infer_brand as _infer_brand
from backend.utils import normalize_ppo as _normalize_ppo
from backend.utils import normalize_text as _normalize_text
from backend.utils import safe_float as _safe_float

_fabric_stock_cache = {
    "rows": [],
    "filename": "",
    "loaded_at": "",
    "source_path": "",
}


def _write_cache() -> None:
    FABRIC_UPLOAD_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    FABRIC_UPLOAD_CACHE_JSON.write_text(
        json.dumps(_fabric_stock_cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_cache() -> dict | None:
    if not FABRIC_UPLOAD_CACHE_JSON.exists():
        return None
    try:
        return json.loads(FABRIC_UPLOAD_CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_fabric_upload(file_bytes: bytes) -> list[dict]:
    workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    worksheet = workbook.active
    grouped = defaultdict(
        lambda: {
            "warehouse": "",
            "ppo_no": "",
            "combo": "",
            "shade": "",
            "fabric_type": "",
            "qty": 0.0,
            "uom": "",
            "lots": [],
            "row_count": 0,
        }
    )

    for row in worksheet.iter_rows(min_row=2, values_only=True):
        if len(row) < 23:
            continue
        warehouse = row[0]
        lot_no = row[5]
        po_no = row[6]
        shade = row[10]
        combo = row[11]
        fabric_type = row[12]
        qty = row[21]
        uom = row[22]

        if combo is None and shade is None:
            continue
        combo_text = str(combo or shade or "").strip()
        if not combo_text:
            continue

        ppo_no = str(po_no or "").strip()
        key = (
            str(warehouse or "").strip(),
            ppo_no,
            combo_text,
            str(fabric_type or "").strip(),
        )
        item = grouped[key]
        item["warehouse"] = str(warehouse or "").strip()
        item["ppo_no"] = ppo_no
        item["combo"] = combo_text
        item["shade"] = str(shade or "").strip()
        item["fabric_type"] = str(fabric_type or "").strip()
        item["uom"] = str(uom or "").strip()
        item["row_count"] += 1
        item["qty"] += _safe_float(qty)

        lot_text = str(lot_no or "").strip()
        if lot_text and lot_text not in item["lots"]:
            item["lots"].append(lot_text)

    workbook.close()
    rows = []
    for _, item in sorted(grouped.items()):
        rows.append(
            {
                "warehouse": item["warehouse"],
                "ppo_no": item["ppo_no"],
                "combo": item["combo"],
                "shade": item["shade"],
                "fabric_type": item["fabric_type"],
                "qty": round(item["qty"], 2),
                "uom": item["uom"],
                "lots": sorted(item["lots"]),
                "pos": [item["ppo_no"]] if item["ppo_no"] else [],
                "row_count": item["row_count"],
            }
        )
    return rows


def load_fabric_stock() -> list[dict]:
    cached = _read_cache()
    if cached and cached.get("rows"):
        _fabric_stock_cache.update(cached)
        return list(_fabric_stock_cache["rows"])
    preload_default_fabric(force_parse=False)
    return list(_fabric_stock_cache["rows"])


def save_uploaded_fabric(file_bytes: bytes, filename: str) -> dict:
    rows = parse_fabric_upload(file_bytes)
    _fabric_stock_cache.update(
        {
            "rows": rows,
            "filename": filename,
            "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_path": "",
        }
    )
    _write_cache()
    return {
        "ok": True,
        "rows": rows,
        "total_groups": len(rows),
        "filename": filename,
        "loaded_at": _fabric_stock_cache["loaded_at"],
    }


def get_fabric_stock_meta() -> dict:
    if not _fabric_stock_cache["rows"]:
        load_fabric_stock()
    return {
        "filename": _fabric_stock_cache["filename"],
        "loaded_at": _fabric_stock_cache["loaded_at"],
        "source_path": _fabric_stock_cache["source_path"],
        "total_groups": len(_fabric_stock_cache["rows"]),
    }


def preload_default_fabric(force_parse: bool = False) -> bool:
    source = Path(FABRIC_LEFT_DEFAULT_XLSX)
    if not source.exists():
        if not force_parse:
            cached = _read_cache()
            if cached and cached.get("rows"):
                _fabric_stock_cache.update(cached)
                return True
        return False

    if not force_parse:
        cached = _read_cache()
        if cached and cached.get("source_path") == str(source) and cached.get("rows"):
            _fabric_stock_cache.update(cached)
            return True

    rows = parse_fabric_upload(source.read_bytes())
    _fabric_stock_cache.update(
        {
            "rows": rows,
            "filename": source.name,
            "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_path": str(source),
        }
    )
    _write_cache()
    return True


def _build_summary_lookup(summary_rows: list[dict]) -> dict[str, dict]:
    lookup = {}
    for row in summary_rows or []:
        for key in (
            _normalize_text(row.get("Color")),
            _normalize_text(row.get("Color_Desc")),
        ):
            if key:
                lookup[key] = row
    return lookup


def _build_lots_by_ppo(go_summary: dict) -> dict[str, set[str]]:
    lots_by_no = {
        str(item.get("lot") or ""): str(item.get("job_order_no") or "").strip()
        for item in (go_summary.get("lot_rows") or [])
        if item.get("lot")
    }
    result = defaultdict(set)
    for item in go_summary.get("ppo_mapping") or []:
        ppo = _normalize_ppo(item.get("ppo"))
        lot_no = str(item.get("lot") or "").strip()
        if not ppo or not lot_no:
            continue
        job = str(item.get("job_order_no") or "").strip() or lots_by_no.get(lot_no, "")
        if job:
            result[ppo].add(job)
    return result


def _collect_active_ppos(go_summary: dict, jo_details: list[dict]) -> list[str]:
    jobs_by_lot = {
        str(item.get("lot") or ""): str(item.get("job_order_no") or "").strip()
        for item in (go_summary.get("lot_rows") or [])
        if item.get("lot")
    }
    ppos_by_job = defaultdict(list)
    for item in go_summary.get("ppo_mapping") or []:
        ppo = _normalize_ppo(item.get("ppo"))
        if not ppo:
            continue
        job = str(item.get("job_order_no") or "").strip()
        lot_no = str(item.get("lot") or "").strip()
        if not job and lot_no:
            job = jobs_by_lot.get(lot_no, "")
        if not job:
            continue
        if ppo not in ppos_by_job[job]:
            ppos_by_job[job].append(ppo)

    job_set = {
        str(item.get("JO") or "").strip()
        for item in (jo_details or [])
        if str(item.get("JO") or "").strip()
    }
    active = []
    seen = set()
    for job in sorted(job_set):
        for ppo in ppos_by_job.get(job, []):
            if ppo and ppo not in seen:
                seen.add(ppo)
                active.append(ppo)
    if active:
        return active
    for ppo in go_summary.get("ppo_list") or []:
        ppo_key = _normalize_ppo(ppo)
        if ppo_key and ppo_key not in seen:
            seen.add(ppo_key)
            active.append(ppo_key)
    return active


def _match_stock_rows(stock_rows: list[dict], ppo: str, keys: set[str]) -> list[dict]:
    normalized_targets = {key for key in keys if key}
    matches = []
    for row in stock_rows:
        row_ppo = _normalize_ppo(row.get("ppo_no") or (row.get("pos") or [""])[0])
        if ppo and row_ppo != ppo:
            continue
        row_keys = {
            _normalize_text(row.get("combo")),
            _normalize_text(row.get("shade")),
            _normalize_text(row.get("fabric_type")),
        }
        row_keys.discard("")
        if not row_keys:
            continue
        matched = False
        for target in normalized_targets:
            for candidate in row_keys:
                if target == candidate:
                    matched = True
                    break
                if len(target) >= 3 and len(candidate) >= 3 and (target in candidate or candidate in target):
                    matched = True
                    break
            if matched:
                break
        if matched:
            matches.append(row)
    return matches


def _build_expected_color_rows(go_summary: dict, cutting_summary: list[dict], active_ppos: list[str]) -> tuple[list[dict], list[dict], dict[str, dict]]:
    summary_lookup = _build_summary_lookup(cutting_summary)
    ppo_infos = {}
    warnings = []
    expected = {}

    for ppo in active_ppos or []:
        ppo_key = _normalize_ppo(ppo)
        info = fetch_ppo_fabric_combos(ppo_key, backend="auto")
        ppo_infos[ppo_key] = info
        if not info.get("ok"):
            warnings.append({"ppo": ppo_key, "error": info.get("error", "Cannot fetch PPO report")})
            continue
        for line in info.get("fabric_lines") or []:
            color_code = str(line.get("color_code") or "").strip()
            combo = str(line.get("fabric_combo") or line.get("fabric_color") or "").strip()
            group_key = (ppo_key, color_code or _normalize_text(combo))
            item = expected.setdefault(
                group_key,
                {
                    "ppo": ppo_key,
                    "color_code": color_code,
                    "color_desc": "",
                    "fabric_combos": set(),
                    "fabric_types": set(),
                    "ppo_qty": 0.0,
                    "summary_row": {},
                },
            )
            item["fabric_combos"].add(combo)
            if line.get("fabric_type"):
                item["fabric_types"].add(str(line.get("fabric_type")))
            item["ppo_qty"] += _safe_float(line.get("order_qty"))
            if not item["color_desc"]:
                item["color_desc"] = combo
            if not item["summary_row"]:
                item["summary_row"] = (
                    summary_lookup.get(_normalize_text(color_code))
                    or summary_lookup.get(_normalize_text(item["color_desc"]))
                    or {}
                )

    rows = []
    for item in expected.values():
        rows.append(
            {
                "ppo": item["ppo"],
                "color_code": item["color_code"],
                "color_desc": item["color_desc"],
                "fabric_combos": sorted(combo for combo in item["fabric_combos"] if combo),
                "fabric_types": sorted(ft for ft in item["fabric_types"] if ft),
                "ppo_qty": round(item["ppo_qty"], 2),
                "summary_row": item["summary_row"],
            }
        )
    return rows, warnings, ppo_infos


def match_fabric_stock(go: str, stock_rows: list[dict] | None = None) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"ok": False, "go": "", "error": "GO number required", "matches": []}

    go_summary = _fetch_go_report_detail(go_key)
    if not go_summary.get("ok"):
        return {
            "ok": False,
            "go": go_key,
            "error": go_summary.get("error", "Cannot fetch GO summary"),
            "matches": [],
        }

    cutting = get_cutting_forecast(go_key, prefer_cache=True)
    stock = stock_rows if stock_rows is not None else load_fabric_stock()
    active_ppos = _collect_active_ppos(go_summary, cutting.get("jo_details") or [])
    expected_rows, warnings, ppo_infos = _build_expected_color_rows(go_summary, cutting.get("summary") or [], active_ppos)
    brand = _infer_brand(go_summary, ppo_infos)
    lots_by_ppo = _build_lots_by_ppo(go_summary)

    matches = []
    for item in expected_rows:
        target_keys = {
            _normalize_text(item.get("color_desc")),
            _normalize_text(item.get("color_code")),
            *[_normalize_text(combo) for combo in (item.get("fabric_combos") or [])],
        }
        target_keys.discard("")
        matched_rows = _match_stock_rows(stock, item.get("ppo", ""), target_keys)
        actual_qty = round(sum(_safe_float(row.get("qty")) for row in matched_rows), 2)
        summary_row = item.get("summary_row") or {}
        ppo_qty = round(_safe_float(item.get("ppo_qty")), 2)
        balance = round(actual_qty - ppo_qty, 2)
        lots = sorted(
            {
                *lots_by_ppo.get(item.get("ppo", ""), set()),
                *(str(lot).strip() for row in matched_rows for lot in (row.get("lots") or []) if str(lot).strip()),
            }
        )
        matches.append(
            {
                "Brand": brand,
                "PPO": item.get("ppo", ""),
                "Color": item.get("color_desc") or ", ".join(item.get("fabric_combos") or []),
                "Lots (hidden)": ", ".join(lots),
                "YY PPO": summary_row.get("PPO_YY", ""),
                "Actual YY#": summary_row.get("Net_YY", "") or summary_row.get("Marker_YY", ""),
                "YY# remark": "",
                "PPO q'ty (yds)": ppo_qty,
                "Actual Q'ty (yds)": actual_qty,
                "Balance q'ty": balance,
                "Overage q'ty alert": "Over > 50 yds" if balance > 50 else "",
                "Write Off": "Need W/O" if balance > 50 else "",
                "W/O KPI": "",
                "Color Code": item.get("color_code", ""),
                "Fabric Types": ", ".join(item.get("fabric_types") or []),
                "Matched Stock Rows": len(matched_rows),
            }
        )

    matches.sort(key=lambda row: (str(row.get("PPO") or ""), str(row.get("Color Code") or ""), str(row.get("Color") or "")))
    return {
        "ok": True,
        "go": go_summary["go"],
        "style_no": go_summary.get("style_no", ""),
        "brand": brand,
        "matches": matches,
        "match_count": len(matches),
        "target_keys": sorted({row.get("Color") for row in matches if row.get("Color")}),
        "match_mode": "ppo_color_split",
        "stock_scope": "ppo_strict",
        "warnings": warnings,
        "source_url": go_summary.get("source_url", ""),
    }


def build_fabric_rows(go: str, stock_rows: list[dict] | None = None) -> dict:
    matched = match_fabric_stock(go, stock_rows=stock_rows)
    if not matched.get("ok"):
        return matched
    return {
        "ok": True,
        "go": matched["go"],
        "style_no": matched.get("style_no", ""),
        "brand": matched.get("brand", ""),
        "rows": matched.get("matches", []),
        "match_count": matched.get("match_count", 0),
        "target_keys": matched.get("target_keys", []),
        "match_mode": matched.get("match_mode", ""),
        "stock_scope": matched.get("stock_scope", ""),
        "warnings": matched.get("warnings", []),
        "source_url": matched.get("source_url", ""),
    }
