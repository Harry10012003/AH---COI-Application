from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from backend.scraper.gw_client import _fetch_go_report_detail, fetch_ppo_fabric_combos
from backend.scraper.mes_client import get_cutting_forecast
from backend.utils import infer_brand as _infer_brand
from backend.utils import normalize_ppo as _normalize_ppo
from backend.utils import normalize_text as _normalize_text
from backend.utils import safe_float as _safe_float


def _safe_round(value: float) -> float:
    return round(float(value or 0), 4)


def _sort_date_key(value: object) -> tuple:
    text = str(value or "").strip()
    if not text:
        return (9999, 12, 31, text)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return (parsed.year, parsed.month, parsed.day, text)
        except ValueError:
            continue
    return (9999, 12, 31, text)


def _build_summary_lookup(summary_rows: list[dict], color_rows: list[dict]) -> dict[str, dict]:
    lookup = {}
    for row in summary_rows or []:
        for key in (
            _normalize_text(row.get("Color")),
            _normalize_text(row.get("Color_Desc")),
        ):
            if key:
                lookup[key] = row
    for row in color_rows or []:
        for key in (
            _normalize_text(row.get("color_code")),
            _normalize_text(row.get("color_desc")),
        ):
            if key and key not in lookup:
                lookup[key] = row
    return lookup


def _build_lot_context(go_summary: dict) -> tuple[dict[str, dict], dict[str, list[str]]]:
    lots_by_job = {}
    jobs_by_lot = {}
    for item in go_summary.get("lot_rows") or []:
        lot_no = str(item.get("lot") or "").strip()
        job = str(item.get("job_order_no") or "").strip()
        if job:
            lots_by_job[job] = dict(item)
        if lot_no and job:
            jobs_by_lot[lot_no] = job

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

    for job, item in lots_by_job.items():
        item["mapped_ppos"] = list(ppos_by_job.get(job, []))
    return lots_by_job, {job: list(ppos) for job, ppos in ppos_by_job.items()}


def _collect_active_ppos(go_summary: dict, ppos_by_job: dict[str, list[str]], jo_details: list[dict]) -> list[str]:
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


def _build_ppo_context(ppo_list: list[str]) -> tuple[dict[str, dict], dict[tuple[str, str], list[dict]], list[dict]]:
    ppo_infos = {}
    ppo_index = defaultdict(list)
    warnings = []

    for ppo in ppo_list or []:
        ppo_key = _normalize_ppo(ppo)
        if not ppo_key:
            continue
        info = fetch_ppo_fabric_combos(ppo_key, backend="auto")
        ppo_infos[ppo_key] = info
        if not info.get("ok"):
            warnings.append({"ppo": ppo_key, "error": info.get("error", "Cannot fetch PPO report")})
            continue
        for line in info.get("fabric_lines") or []:
            color_code = str(line.get("color_code") or "").strip()
            if not color_code:
                continue
            enriched = {**line, "ppo": ppo_key}
            ppo_index[(ppo_key, color_code)].append(enriched)
    return ppo_infos, ppo_index, warnings


def _build_candidate_rows(candidate_ppos: list[str], color_code: str, ppo_index: dict[tuple[str, str], list[dict]]) -> list[dict]:
    rows = []
    seen = set()
    for ppo in candidate_ppos:
        for line in ppo_index.get((ppo, color_code), []):
            key = (
                ppo,
                str(line.get("fabric_type") or "").strip().upper(),
                str(line.get("fabric_combo") or "").strip().upper(),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(line))
    return rows


def build_coi_preview(go: str, prefer_mes_cache: bool = True) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"ok": False, "error": "GO number required", "go": ""}

    go_summary = _fetch_go_report_detail(go_key)
    if not go_summary.get("ok"):
        return {
            "ok": False,
            "go": go_key,
            "error": go_summary.get("error", "Cannot fetch GO summary"),
            "source_url": go_summary.get("source_url", ""),
        }

    cutting = get_cutting_forecast(go_key, prefer_cache=prefer_mes_cache)
    cutting_summary = cutting.get("summary") or []
    jo_details = cutting.get("jo_details") or []

    lots_by_job, ppos_by_job = _build_lot_context(go_summary)
    active_ppos = _collect_active_ppos(go_summary, ppos_by_job, jo_details)
    ppo_infos, ppo_index, ppo_warnings = _build_ppo_context(active_ppos)
    brand = _infer_brand(go_summary, ppo_infos)
    summary_lookup = _build_summary_lookup(cutting_summary, go_summary.get("colors") or [])

    rows = []
    for detail in jo_details:
        job_order_no = str(detail.get("JO") or "").strip()
        color_code = str(detail.get("Color") or "").strip()
        color_desc = str(detail.get("Color_Desc") or "").strip()
        lot_info = lots_by_job.get(job_order_no, {})
        summary_row = (
            summary_lookup.get(_normalize_text(color_code))
            or summary_lookup.get(_normalize_text(color_desc))
            or {}
        )

        candidate_ppos = [ppo for ppo in ppos_by_job.get(job_order_no, []) if ppo in ppo_infos]
        fallback_ppo = _normalize_ppo(detail.get("PPO_No"))
        if not candidate_ppos and fallback_ppo:
            candidate_ppos = [fallback_ppo]
        if not candidate_ppos:
            candidate_ppos = list(ppo_infos.keys())[:1]

        candidate_rows = _build_candidate_rows(candidate_ppos, color_code, ppo_index)
        if not candidate_rows:
            candidate_rows = [
                {
                    "ppo": candidate_ppos[0] if candidate_ppos else fallback_ppo,
                    "fabric_type": "",
                    "fabric_combo": color_desc,
                    "order_qty": 0,
                }
            ]

        qty = _safe_float(detail.get("Order_QTY"))
        marker_yy = _safe_float(detail.get("Marker_YY"))
        ppo_yy = _safe_float(detail.get("PPO_YY"))
        actual_yy = _safe_float(summary_row.get("Net_YY")) or ppo_yy or marker_yy
        minus_pct = lot_info.get("minus_pct", "")
        plus_pct = lot_info.get("plus_pct", "")
        buyer_po_del_date = lot_info.get("buyer_po_del_date", "")
        remark = str(lot_info.get("remarks") or "").strip()

        for line in candidate_rows:
            ppo_value = str(line.get("ppo") or fallback_ppo or "").strip()
            fabric_type = str(line.get("fabric_type") or "").strip().upper()
            # Keep the preview route aligned with the live COI sheet: PPC
            # requirement is PPO YY × garment quantity. Marker/Net YY is a
            # display value only, except for collar/cuff O/F rows whose
            # neutral marker remains the sanctioned legacy fallback.
            required_yy = ppo_yy if ppo_yy > 0 else (marker_yy if fabric_type in {"O", "F"} else 0.0)
            required_qty = _safe_round(qty * required_yy) if qty and required_yy else 0
            default_allocate = _safe_round(required_qty * 1.02) if required_qty else 0
            ppo_rcv_qty = _safe_float(line.get("order_qty"))
            allocate_pct = _safe_round(default_allocate / required_qty) if required_qty else 0
            rows.append(
                {
                    "BRAND": brand,
                    "GO#": go_key,
                    "PPO": ppo_value,
                    "Type": fabric_type,
                    "COLOR_CODE": color_code,
                    "COLOR_DESC": color_desc,
                    "FABRIC COLOR (For piecing only)": str(line.get("fabric_combo") or color_desc).strip(),
                    "JOB ORDER NO": job_order_no,
                    "- %": minus_pct,
                    "+%": plus_pct,
                    "Qty (pcs)": qty,
                    "BUYER_PO_DEL_DATE": buyer_po_del_date,
                    "Marker YY": marker_yy,
                    "PPO YY": ppo_yy,
                    "Actual YY#": actual_yy,
                    "Required Q'ty (Yds)": required_qty,
                    "Rcv Q'ty (PPO)": ppo_rcv_qty,
                    "Allocate Q'ty (Yds)": default_allocate,
                    "AH Allocate Q'ty (yds)": "",
                    "Allocate %": allocate_pct,
                    "Remark": remark,
                }
            )

    rows.sort(
        key=lambda row: (
            str(row.get("Type") or ""),
            str(row.get("COLOR_CODE") or ""),
            _sort_date_key(row.get("BUYER_PO_DEL_DATE")),
            str(row.get("JOB ORDER NO") or ""),
            str(row.get("PPO") or ""),
        )
    )

    return {
        "ok": True,
        "go": go_key,
        "brand": brand,
        "style_no": go_summary.get("style_no", ""),
        "style_desc": go_summary.get("style_desc", ""),
        "season": go_summary.get("season", ""),
        "customer_style": go_summary.get("customer_style", ""),
        "rows": rows,
        "row_count": len(rows),
        "ppo_list": go_summary.get("ppo_list", []),
        "lot_rows": go_summary.get("lot_rows", []),
        "go_source_url": go_summary.get("source_url", ""),
        "mes_site": cutting.get("site", ""),
        "mes_summary_count": len(cutting_summary),
        "mes_error": cutting.get("error", ""),
        "warnings": ppo_warnings,
    }
