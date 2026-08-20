from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re
import threading
import time
from typing import Any

import requests


SAMPLE_TRACKING_URL = "http://192.168.152.2/MES/SampleReqTracking.asp"
SAMPLE_TRACKING_TIMEOUT_SEC = 12
SAMPLE_TRACKING_CACHE_TTL_SEC = 10 * 60

_cache_lock = threading.RLock()
_sample_cache: dict[tuple[str, str], dict[str, Any]] = {}


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._table_depth = 0
        self._in_row = False
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        if tag_name == "table":
            self._table_depth += 1
            return
        if self._table_depth <= 0:
            return
        if tag_name == "tr":
            self._in_row = True
            self._row = []
            return
        if tag_name in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"td", "th"} and self._in_cell:
            self._row.append(_clean_cell("".join(self._cell_parts)))
            self._cell_parts = []
            self._in_cell = False
            return
        if tag_name == "tr" and self._in_row:
            if any(cell.strip() for cell in self._row):
                self.rows.append(list(self._row))
            self._row = []
            self._in_row = False
            self._in_cell = False
            return
        if tag_name == "table" and self._table_depth > 0:
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def _clean_cell(value: object) -> str:
    text = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_go(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _normalize_sample_type(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def _sample_color_keys(value: object) -> set[str]:
    raw = _clean_cell(value).upper()
    keys = {
        re.sub(r"[^A-Z0-9]+", "", raw),
    }
    stripped = re.sub(r"^\s*0*\d+\s*[/@\\-]?\s*", "", raw).strip()
    stripped = re.sub(r"^\s*0*\d+\s+", "", stripped).strip()
    if stripped:
        keys.add(re.sub(r"[^A-Z0-9]+", "", stripped))
    return {key for key in keys if key}


def _parse_html_rows(html_text: str) -> list[dict[str, str]]:
    parser = _HtmlTableParser()
    parser.feed(html_text or "")
    header_index = -1
    headers: list[str] = []
    for index, raw_row in enumerate(parser.rows):
        normalized = [_normalize_key(cell) for cell in raw_row]
        if "gono" in normalized and "sampletype" in normalized and "pidate" in normalized:
            header_index = index
            headers = [_clean_cell(cell) for cell in raw_row]
            break
    if header_index < 0 or not headers:
        return []

    output: list[dict[str, str]] = []
    for raw_row in parser.rows[header_index + 1 :]:
        if not raw_row:
            continue
        if _normalize_key(raw_row[0]) == "total":
            break
        if len(raw_row) < len(headers):
            continue
        item = {}
        for offset, header in enumerate(headers):
            item[header] = _clean_cell(raw_row[offset])
        if _normalize_go(item.get("GONo")):
            output.append(item)
    return output


def _parse_mes_datetime(value: object) -> datetime | None:
    text = _clean_cell(value)
    if not text:
        return None
    for fmt in (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_mes_date(value: datetime) -> str:
    return f"{value.month}/{value.day}/{value.year}"


def _sample_row_status(row: dict, sample_type: str) -> dict:
    type_key = _normalize_sample_type(sample_type or row.get("Sample Type") or "PPS")
    pi_date = _parse_mes_datetime(row.get("PI Date"))
    cut_date = _parse_mes_datetime(row.get("Cut Date"))
    if pi_date:
        value = f"{type_key} PI {_format_mes_date(pi_date)}"
    elif cut_date:
        value = f"{type_key} CUTTED {_format_mes_date(cut_date)}"
    else:
        value = f"{type_key} TBA"
    return {
        "value": value,
        "sample_type": type_key,
        "pi_date": _format_mes_date(pi_date) if pi_date else "",
        "cut_date": _format_mes_date(cut_date) if cut_date else "",
        "color": _clean_cell(row.get("Color")),
        "raw": row,
    }


def fetch_sample_tracking(go_no: str, sample_type: str = "PPS", *, timeout_sec: int = SAMPLE_TRACKING_TIMEOUT_SEC) -> dict:
    go_key = _normalize_go(go_no)
    type_key = _normalize_sample_type(sample_type)
    if not go_key:
        return {"ok": False, "go": "", "sample_type": type_key, "rows": [], "error": "GO is required"}

    cache_key = (go_key, type_key)
    now_ts = time.time()
    with _cache_lock:
        cached = _sample_cache.get(cache_key)
        if cached and now_ts - float(cached.get("ts") or 0.0) <= SAMPLE_TRACKING_CACHE_TTL_SEC:
            data = cached.get("data")
            return dict(data) if isinstance(data, dict) else {"ok": False, "go": go_key, "sample_type": type_key, "rows": []}

    params = {"txtGoNo": go_key}
    if type_key:
        params["txtSampleType"] = type_key

    try:
        response = requests.get(SAMPLE_TRACKING_URL, params=params, timeout=timeout_sec)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"
        rows = _parse_html_rows(response.text)
        if type_key:
            rows = [row for row in rows if _normalize_sample_type(row.get("Sample Type")) == type_key]
        result = {
            "ok": True,
            "go": go_key,
            "sample_type": type_key,
            "rows": rows,
            "row_count": len(rows),
            "source_url": SAMPLE_TRACKING_URL,
            "error": "",
        }
    except Exception as exc:
        result = {
            "ok": False,
            "go": go_key,
            "sample_type": type_key,
            "rows": [],
            "row_count": 0,
            "source_url": SAMPLE_TRACKING_URL,
            "error": str(exc),
        }

    with _cache_lock:
        _sample_cache[cache_key] = {"ts": now_ts, "data": result}
    return dict(result)


def sample_status_for_go(go_no: str, sample_type: str = "PPS") -> dict:
    payload = fetch_sample_tracking(go_no, sample_type)
    if not payload.get("ok"):
        return {
            "ok": False,
            "go": payload.get("go") or _normalize_go(go_no),
            "sample_type": payload.get("sample_type") or _normalize_sample_type(sample_type),
            "sample_value": "",
            "pi_date": "",
            "row_count": 0,
            "error": payload.get("error") or "Cannot query MES sample tracking",
        }

    pi_dates: list[datetime] = []
    for row in payload.get("rows") or []:
        parsed = _parse_mes_datetime(row.get("PI Date"))
        if parsed:
            pi_dates.append(parsed)

    if not pi_dates:
        return {
            "ok": True,
            "go": payload.get("go") or _normalize_go(go_no),
            "sample_type": payload.get("sample_type") or _normalize_sample_type(sample_type),
            "sample_value": "TBA",
            "pi_date": "",
            "row_count": int(payload.get("row_count") or 0),
            "error": "",
        }

    latest = max(pi_dates)
    pi_date = _format_mes_date(latest)
    return {
        "ok": True,
        "go": payload.get("go") or _normalize_go(go_no),
        "sample_type": payload.get("sample_type") or _normalize_sample_type(sample_type),
        "sample_value": pi_date,
        "pi_date": pi_date,
        "row_count": int(payload.get("row_count") or 0),
        "error": "",
    }


def sample_status_lookup_for_go(go_no: str, sample_type: str = "PPS") -> dict:
    payload = fetch_sample_tracking(go_no, sample_type)
    type_key = _normalize_sample_type(sample_type)
    if not payload.get("ok"):
        return {
            "ok": False,
            "go": payload.get("go") or _normalize_go(go_no),
            "sample_type": payload.get("sample_type") or type_key,
            "by_color": {},
            "default": {"value": "", "sample_type": type_key, "pi_date": "", "cut_date": "", "color": "", "raw": {}},
            "error": payload.get("error") or "Cannot query MES sample tracking",
        }

    by_color: dict[str, dict] = {}
    statuses: list[dict] = []
    for row in payload.get("rows") or []:
        status = _sample_row_status(row, type_key)
        statuses.append(status)
        for key in _sample_color_keys(row.get("Color")):
            existing = by_color.get(key)
            existing_pi = _parse_mes_datetime((existing or {}).get("pi_date"))
            next_pi = _parse_mes_datetime(status.get("pi_date"))
            existing_cut = _parse_mes_datetime((existing or {}).get("cut_date"))
            next_cut = _parse_mes_datetime(status.get("cut_date"))
            if not existing:
                by_color[key] = status
            elif next_pi and (not existing_pi or next_pi > existing_pi):
                by_color[key] = status
            elif not existing_pi and next_cut and (not existing_cut or next_cut > existing_cut):
                by_color[key] = status

    default_status = {"value": f"{type_key} TBA", "sample_type": type_key, "pi_date": "", "cut_date": "", "color": "", "raw": {}}
    pi_statuses = [item for item in statuses if item.get("pi_date")]
    cut_statuses = [item for item in statuses if item.get("cut_date")]
    if pi_statuses:
        default_status = max(pi_statuses, key=lambda item: _parse_mes_datetime(item.get("pi_date")) or datetime.min)
    elif cut_statuses:
        default_status = max(cut_statuses, key=lambda item: _parse_mes_datetime(item.get("cut_date")) or datetime.min)

    return {
        "ok": True,
        "go": payload.get("go") or _normalize_go(go_no),
        "sample_type": payload.get("sample_type") or type_key,
        "by_color": by_color,
        "default": default_status,
        "row_count": int(payload.get("row_count") or 0),
        "error": "",
    }
