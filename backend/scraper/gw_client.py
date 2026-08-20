from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

import requests
try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency
    BeautifulSoup = None

from backend.sources import (
    CACHE_DIR,
    EDGE_CDP_URL,
    GO_REPORT_BASE,
    GW_DEFAULT_FACTORY_FLAGS,
    GW_FRAMESET_URL,
    GW_HTTP_BASE_URL,
    GW_LOGIN_PASSWORD,
    GW_LOGIN_USER,
    PPO_BROWSE_BASE,
    PPO_REPORT_BASE,
    TENDAM_PPO_STATUS_BASE,
    go_report_url,
    ppo_browse_url,
    ppo_report_url,
    tendam_ppo_status_url,
)
from .go_parser import parse_go_report
from .ppo_parser import parse_ppo_report

GO_TIMEOUT_SEC = 18
PPO_TIMEOUT_SEC = 18
GW_HTTP_TIMEOUT_SEC = 12
GW_QUERY_TIMEOUT_MS = 15000
GW_POST_QUERY_WAIT_MS = 180

GO_CACHE_TTL_SEC = 600
GO_FAIL_CACHE_TTL_SEC = 45
PPO_COMBO_CACHE_TTL_SEC = 600
PPO_COMBO_FAIL_CACHE_TTL_SEC = 120
GW_PPO_ROWS_CACHE_TTL_SEC = 300
GW_PPO_EMPTY_ROWS_CACHE_TTL_SEC = 90
GO_DETAIL_DISK_CACHE_MAX_AGE_SEC = 1800

_cache_lock = threading.Lock()
_go_mapping_cache: dict[str, dict[str, Any]] = {}
_go_cache_only_failure_cache: dict[str, dict[str, Any]] = {}
_ppo_combo_cache: dict[str, dict[str, Any]] = {}
_gw_rows_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
_GO_DETAIL_CACHE_DIR = CACHE_DIR / "go_report_detail"


def _normalize_color_key(text: object) -> str:
    raw = str(text or "").upper().replace("@", " ").replace("\xa0", " ")
    raw = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_ppo_key(text: object) -> str:
    raw = str(text or "").upper().replace("\xa0", " ").strip()
    raw = re.sub(r"\s+", "", raw)
    return re.sub(r"[^A-Z0-9]", "", raw)


def _cache_get(store: dict, key: Any):
    now = time.time()
    with _cache_lock:
        item = store.get(key)
        if not item:
            return None
        if now > float(item.get("expires_at") or 0):
            store.pop(key, None)
            return None
        data = item.get("data")
        if isinstance(data, dict):
            return dict(data)
        if isinstance(data, list):
            return [dict(row) if isinstance(row, dict) else row for row in data]
        return data


def _cache_set(store: dict, key: Any, data: Any, ttl_sec: int) -> None:
    ttl = max(1, int(ttl_sec or 1))
    with _cache_lock:
        store[key] = {"expires_at": time.time() + ttl, "data": data}


def _go_detail_cache_path(go: str):
    return _GO_DETAIL_CACHE_DIR / f"{str(go or '').strip().upper()}.json"


def _load_go_detail_disk_cache(go: str, max_age_sec: int | None = None) -> dict | None:
    path = _go_detail_cache_path(go)
    try:
        if not path.exists():
            return None
        if max_age_sec is not None:
            age_sec = max(time.time() - path.stat().st_mtime, 0.0)
            if age_sec > float(max_age_sec):
                return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, dict) else None
    except Exception:
        return None


def _save_go_detail_disk_cache(go: str, payload: dict) -> None:
    path = _go_detail_cache_path(go)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except Exception:
        return


def _decode_html(content: bytes, response: requests.Response | None = None) -> str:
    encodings = []
    if response is not None and getattr(response, "encoding", None):
        encodings.append(str(response.encoding))
    encodings.extend(["gb2312", "utf-8", "big5"])
    tried = set()
    for encoding in encodings:
        if not encoding or encoding in tried:
            continue
        tried.add(encoding)
        try:
            return content.decode(encoding)
        except Exception:
            continue
    return content.decode("utf-8", errors="replace")


def _fetch_text(url: str, timeout_sec: float) -> tuple[str, requests.Response]:
    response = requests.get(url, timeout=timeout_sec, headers={"User-Agent": "TEST-Dashboard/1.0"})
    response.raise_for_status()
    return _decode_html(response.content, response), response


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


async def _launch_headless_browser(playwright):
    errors = []
    for kwargs in ({"headless": True}, {"headless": True, "channel": "msedge"}):
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Cannot launch hidden browser: %s" % " | ".join(errors))


async def _wait_for_report_text(page, max_wait_ms: int = 10000) -> str:
    waited_ms = 0
    last_text = ""
    while waited_ms <= max_wait_ms:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        text = str(text or "")
        last_text = text
        normalized = text.strip()
        if normalized and "Loading..." not in normalized and (
            "Gmt Color Code @ Fabric Combo Name" in normalized
            or "FabPart" in normalized
            or "PPO#:" in normalized
        ):
            return text
        await page.wait_for_timeout(500)
        waited_ms += 500
    return last_text


async def _fetch_report_text_headless(url: str) -> tuple[str, int]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await _launch_headless_browser(p)
        context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1360, "height": 900})
        page = await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=GW_QUERY_TIMEOUT_MS)
            text = await _wait_for_report_text(page)
            return str(text or ""), int(response.status) if response else 0
        finally:
            await context.close()
            await browser.close()


async def _fetch_report_text_edge(url: str) -> tuple[str, int]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(EDGE_CDP_URL)
        if not browser.contexts:
            raise RuntimeError("Edge debug has no context. Start Edge with --remote-debugging-port=9222.")
        context = browser.contexts[0]
        page = await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=GW_QUERY_TIMEOUT_MS)
            text = await _wait_for_report_text(page)
            return str(text or ""), int(response.status) if response else 0
        finally:
            await page.close()


def _fetch_ppo_report_browser_text(ppo: str, backend: str = "auto") -> tuple[str, str]:
    url = ppo_report_url(ppo)
    backend_key = str(backend or "auto").strip().lower()
    if backend_key == "edge":
        order = ["edge", "headless"]
    elif backend_key == "headless":
        order = ["headless", "edge"]
    else:
        order = ["headless", "edge"]

    errors = []
    for mode in order:
        try:
            if mode == "edge":
                report_text, status = _run_async(_fetch_report_text_edge(url))
            else:
                report_text, status = _run_async(_fetch_report_text_headless(url))
            if status and status >= 400:
                raise RuntimeError("HTTP %s" % status)
            if report_text.strip():
                return report_text, mode
            raise RuntimeError("Empty report text")
        except Exception as exc:
            errors.append("%s: %s" % (mode, exc))
    raise RuntimeError(" | ".join(errors) if errors else "Cannot fetch PPO report by browser")


def _build_go_result(go: str, parsed: dict, source_url: str) -> dict:
    header = parsed.get("header") or {}
    ppo_mapping = parsed.get("ppo_mapping") or []
    ppo_set = {_normalize_ppo_key(ppo) for ppo in parsed.get("ppo_refs") or []}
    for row in ppo_mapping:
        ppo_key = _normalize_ppo_key((row or {}).get("ppo"))
        if ppo_key:
            ppo_set.add(ppo_key)

    return {
        "ok": True,
        "go": str(go or "").strip().upper(),
        "style_no": str(header.get("style_no") or "").strip(),
        "style_desc": str(header.get("style_desc") or "").strip(),
        "customer_style": str(header.get("customer_style") or "").strip(),
        "customer_name_code": str(header.get("customer_name_code") or "").strip(),
        "brand_name_code": str(header.get("brand_name_code") or "").strip(),
        "customer_label": str(header.get("customer_label") or "").strip(),
        "garment_type": str(header.get("garment_type") or "").strip(),
        "season": str(header.get("season") or "").strip(),
        "buyer": str(header.get("buyer") or "").strip(),
        "colors": list(parsed.get("color_summary") or []),
        "ppo_mapping": list(ppo_mapping),
        "ppo_list": sorted(ppo_set),
        "lot_rows": list(parsed.get("lot_rows") or []),
        "knit_bom_rows": list(parsed.get("knit_bom_rows") or []),
        "color_breakdown_rows": list(parsed.get("color_breakdown_rows") or []),
        "table_count": int(parsed.get("table_count") or 0),
        "source_url": source_url,
    }


def _is_valid_go_result_payload(result: dict | None) -> bool:
    payload = result or {}
    if not payload.get("ok"):
        return False
    has_header = any(
        str(payload.get(key) or "").strip()
        for key in ("style_no", "style_desc", "customer_name_code", "brand_name_code")
    )
    has_core_rows = any(
        payload.get(key)
        for key in ("ppo_mapping", "lot_rows", "knit_bom_rows", "color_breakdown_rows")
    )
    return bool(has_header and has_core_rows)


def _fetch_go_report_detail(
    go: str,
    allow_live_fetch: bool = True,
    max_disk_age_sec: int | None = GO_DETAIL_DISK_CACHE_MAX_AGE_SEC,
) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"ok": False, "go": "", "error": "Invalid GO", "source_url": ""}

    cached = _cache_get(_go_mapping_cache, go_key)
    if cached is not None:
        return cached
    if not allow_live_fetch:
        cached_failure = _cache_get(_go_cache_only_failure_cache, go_key)
        if cached_failure is not None:
            return cached_failure

    url = go_report_url(go_key)
    disk_cached = _load_go_detail_disk_cache(go_key, max_age_sec=max_disk_age_sec)
    if not allow_live_fetch:
        if isinstance(disk_cached, dict) and disk_cached.get("ok"):
            _cache_set(_go_mapping_cache, go_key, disk_cached, GO_CACHE_TTL_SEC)
            return disk_cached
        result = {
            "ok": False,
            "go": go_key,
            "style_no": "",
            "style_desc": "",
            "customer_style": "",
            "customer_name_code": "",
            "brand_name_code": "",
            "customer_label": "",
            "garment_type": "",
            "season": "",
            "buyer": "",
            "colors": [],
            "ppo_mapping": [],
            "ppo_list": [],
            "lot_rows": [],
            "knit_bom_rows": [],
            "color_breakdown_rows": [],
            "table_count": 0,
            "source_url": url,
            "error": "GO report cache unavailable",
        }
        # Keep cache-only misses separate. A following allow_live_fetch=True call
        # must still reach the source instead of being blocked by this short-lived
        # negative result.
        _cache_set(_go_cache_only_failure_cache, go_key, result, GO_FAIL_CACHE_TTL_SEC)
        return result

    try:
        html_text, _response = _fetch_text(url, GO_TIMEOUT_SEC)
        result = _build_go_result(go_key, parse_go_report(html_text), url)
        if not _is_valid_go_result_payload(result):
            raise RuntimeError("Incomplete GO report payload")
        _save_go_detail_disk_cache(go_key, result)
        _cache_set(_go_mapping_cache, go_key, result, GO_CACHE_TTL_SEC)
        return result
    except Exception as exc:
        if isinstance(disk_cached, dict) and disk_cached.get("ok"):
            _cache_set(_go_mapping_cache, go_key, disk_cached, GO_CACHE_TTL_SEC)
            return disk_cached
        result = {
            "ok": False,
            "go": go_key,
            "style_no": "",
            "style_desc": "",
            "customer_style": "",
            "customer_name_code": "",
            "brand_name_code": "",
            "customer_label": "",
            "garment_type": "",
            "season": "",
            "buyer": "",
            "colors": [],
            "ppo_mapping": [],
            "ppo_list": [],
            "lot_rows": [],
            "knit_bom_rows": [],
            "color_breakdown_rows": [],
            "table_count": 0,
            "source_url": url,
            "error": str(exc),
        }
        _cache_set(_go_mapping_cache, go_key, result, GO_FAIL_CACHE_TTL_SEC)
        return result


def fetch_ppo_fabric_combos(ppo: str, backend: str = "auto") -> dict:
    ppo_key = _normalize_ppo_key(ppo)
    if not ppo_key:
        return {
            "ok": False,
            "ppo": "",
            "fabric_lines": [],
            "fabric_combos": [],
            "fabric_color_keys": [],
            "fetch_backend": "",
            "source_url": "",
            "error": "Invalid PPO",
        }

    cached = _cache_get(_ppo_combo_cache, ppo_key)
    if cached is not None:
        return cached

    url = ppo_report_url(ppo_key)
    errors = []
    backend_key = str(backend or "auto").strip().lower()

    if backend_key in {"auto", "http"}:
        try:
            html_text, response = _fetch_text(url, PPO_TIMEOUT_SEC)
            parsed = parse_ppo_report(html_text=html_text)
            if parsed.get("fabric_combos") or parsed.get("fabric_lines"):
                result = {
                    "ok": True,
                    "ppo": ppo_key,
                    "source_url": url,
                    "fetch_backend": "http",
                    **parsed,
                }
                _cache_set(_ppo_combo_cache, ppo_key, result, PPO_COMBO_CACHE_TTL_SEC)
                return result
            status_text = getattr(response, "status_code", 0) or 0
            errors.append("http: empty fabric combos (status %s)" % status_text)
        except Exception as exc:
            errors.append("http: %s" % exc)

    try:
        report_text, used_backend = _fetch_ppo_report_browser_text(ppo_key, backend=backend_key)
        parsed = parse_ppo_report(report_text=report_text)
        if parsed.get("fabric_combos") or parsed.get("fabric_lines"):
            result = {
                "ok": True,
                "ppo": ppo_key,
                "source_url": url,
                "fetch_backend": used_backend,
                **parsed,
            }
            _cache_set(_ppo_combo_cache, ppo_key, result, PPO_COMBO_CACHE_TTL_SEC)
            return result
        errors.append("%s: empty fabric combos" % used_backend)
    except Exception as exc:
        errors.append("browser: %s" % exc)

    result = {
        "ok": False,
        "ppo": ppo_key,
        "source_url": url,
        "fabric_lines": [],
        "fabric_combos": [],
        "fabric_color_keys": [],
        "fetch_backend": "",
        "source_mode": "",
        "error": " | ".join(errors) if errors else "Cannot fetch PPO fabric combos",
    }
    _cache_set(_ppo_combo_cache, ppo_key, result, PPO_COMBO_FAIL_CACHE_TTL_SEC)
    return result


def fetch_go_ppo_mapping_only(go: str) -> dict:
    detail = _fetch_go_report_detail(go)
    if not detail.get("ok"):
        return detail
    return {
        "ok": True,
        "go": detail.get("go", ""),
        "style_no": detail.get("style_no", ""),
        "style_desc": detail.get("style_desc", ""),
        "ppo_mapping": detail.get("ppo_mapping", []),
        "ppo_list": detail.get("ppo_list", []),
        "lot_rows": detail.get("lot_rows", []),
        "color_breakdown_rows": detail.get("color_breakdown_rows", []),
        "source_url": detail.get("source_url", ""),
    }


def fetch_go_color_summary(go: str) -> dict:
    result = _fetch_go_report_detail(go)
    if not result.get("ok"):
        return result

    ppo_combos = set()
    ppo_color_keys = set()
    ppo_fetch_errors = []
    for ppo in result.get("ppo_list") or []:
        ppo_info = fetch_ppo_fabric_combos(ppo, backend="auto")
        if ppo_info.get("ok"):
            for combo in ppo_info.get("fabric_combos") or []:
                ppo_combos.add(str(combo))
            for key in ppo_info.get("fabric_color_keys") or []:
                ppo_color_keys.add(str(key))
        else:
            ppo_fetch_errors.append({"ppo": ppo, "error": ppo_info.get("error", "Cannot fetch PPO report")})

    return {
        **result,
        "ppo_fabric_combos": sorted(ppo_combos, key=lambda item: item.upper()),
        "ppo_color_keys": sorted(ppo_color_keys),
        "ppo_fetch_errors": ppo_fetch_errors,
    }


def _gw_clean_cell(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _gw_extract_rows_flexible(table_html: str) -> list[list[str]]:
    rows = []
    parts = re.split(r"<tr[^>]*>", str(table_html or ""), flags=re.IGNORECASE)
    if len(parts) <= 1:
        return rows
    for part in parts[1:]:
        row_html = re.split(r"</tr>|<tr[^>]*>", part, maxsplit=1, flags=re.IGNORECASE)[0]
        cells_raw = re.findall(
            r"<t[dh][^>]*>(.*?)(?=(?:<t[dh][^>]*>)|(?:</tr>)|(?:<tr[^>]*>)|$)",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not cells_raw:
            cells_raw = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if cells_raw:
            rows.append([_gw_clean_cell(cell) for cell in cells_raw])
    return rows


def _gw_parse_contents_table(contents_html: str, ppo_filter: str = "") -> dict:
    ppo_filter_norm = _normalize_ppo_key(ppo_filter)
    table_match = re.search(
        r"<table[^>]*id\s*=\s*[\"']maintable[\"'][^>]*>(.*?)</table>",
        str(contents_html or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return {"ok": False, "error": "GW result table not found", "rows": []}

    rows = _gw_extract_rows_flexible(table_match.group(1))
    if not rows:
        return {"ok": True, "rows": []}

    header_idx = -1
    header_map = {}
    for i, row in enumerate(rows[:5]):
        norm = [re.sub(r"[^A-Z0-9]+", "", str(cell or "").upper()) for cell in row]
        if "FACTORY" in norm and "PPONO" in norm:
            header_idx = i
            for j, header in enumerate(norm):
                if header:
                    header_map[header] = j
            break
    if header_idx < 0:
        return {"ok": False, "error": "GW header row not found", "rows": []}

    idx_factory = header_map.get("FACTORY", -1)
    idx_warehouse = header_map.get("WAREHOUSE", -1)
    idx_gk = header_map.get("GKNO", -1)
    idx_stock = header_map.get("STOCK", -1)
    idx_available = header_map.get("AVAILABLE", -1)
    idx_batch = header_map.get("BATCHNO", -1)
    idx_ppo = header_map.get("PPONO", -1)
    idx_combo = header_map.get("COMBO", -1)
    idx_usage = header_map.get("USAGE", -1)
    idx_color_code = header_map.get("COLORCODE", -1)
    max_idx = max(idx_factory, idx_warehouse, idx_gk, idx_available, idx_batch, idx_ppo, idx_combo, idx_usage)
    if max_idx < 0:
        return {"ok": False, "error": "GW columns not found", "rows": []}

    header_col_count = len(rows[header_idx])
    data_rows = []
    seen = set()
    for row in rows[header_idx + 1 :]:
        if header_col_count and len(row) == (header_col_count - 1):
            first = str(row[0] if row else "").strip().upper()
            if first in {"GEG", "YMG", "GEK", "TDC", "CEG", "CEK", "NBO", "EGM", "EGV", "PTX", "EAV"}:
                row = [""] + list(row)
            elif idx_stock >= 0:
                row = list(row[:idx_stock]) + [""] + list(row[idx_stock:])
        if len(row) <= max_idx:
            continue
        record = {
            "factory": row[idx_factory] if idx_factory >= 0 and idx_factory < len(row) else "",
            "warehouse": row[idx_warehouse] if idx_warehouse >= 0 and idx_warehouse < len(row) else "",
            "gk_no": row[idx_gk] if idx_gk >= 0 and idx_gk < len(row) else "",
            "available": row[idx_available] if idx_available >= 0 and idx_available < len(row) else "",
            "batch_no": row[idx_batch] if idx_batch >= 0 and idx_batch < len(row) else "",
            "ppo_no": row[idx_ppo] if idx_ppo >= 0 and idx_ppo < len(row) else "",
            "combo": row[idx_combo] if idx_combo >= 0 and idx_combo < len(row) else "",
            "usage": row[idx_usage] if idx_usage >= 0 and idx_usage < len(row) else "",
            "color_code": row[idx_color_code] if idx_color_code >= 0 and idx_color_code < len(row) else "",
        }
        ppo_norm = _normalize_ppo_key(record["ppo_no"])
        if ppo_filter_norm and ppo_norm and ppo_norm != ppo_filter_norm:
            continue
        key = tuple(record.values())
        if key in seen:
            continue
        seen.add(key)
        data_rows.append(record)
    return {"ok": True, "rows": data_rows}


def _gw_flags_cache_key(flags: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    values = [str(flag or "").strip() for flag in (flags or []) if str(flag or "").strip()]
    return tuple(sorted(set(values)))


def _gw_get_ppo_rows_cached(ppo: str, flags: list[str] | tuple[str, ...] | None):
    return _cache_get(_gw_rows_cache, (_normalize_ppo_key(ppo), _gw_flags_cache_key(flags)))


def _gw_set_ppo_rows_cached(ppo: str, flags: list[str] | tuple[str, ...] | None, rows: list[dict]) -> None:
    key = (_normalize_ppo_key(ppo), _gw_flags_cache_key(flags))
    ttl = GW_PPO_ROWS_CACHE_TTL_SEC if rows else GW_PPO_EMPTY_ROWS_CACHE_TTL_SEC
    _cache_set(_gw_rows_cache, key, [dict(row) for row in rows], ttl)


def _dedupe_gw_rows(rows: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for row in rows:
        key = (
            row.get("factory", ""),
            row.get("warehouse", ""),
            row.get("gk_no", ""),
            row.get("available", ""),
            row.get("batch_no", ""),
            row.get("ppo_no", ""),
            row.get("combo", ""),
            row.get("usage", ""),
            row.get("color_code", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def _gw_http_new_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def _gw_http_is_login_form(html_text: str) -> bool:
    text = str(html_text or "").lower()
    return ('id="username"' in text and 'name="password"' in text) or ("please logon" in text)


def _gw_http_has_query_form(html_text: str) -> bool:
    text = str(html_text or "").lower()
    return ('name="query_form"' in text) and ('name="txtppono"' in text)


def _gw_http_login(session) -> None:
    if not str(GW_LOGIN_USER or "").strip() or not str(GW_LOGIN_PASSWORD or "").strip():
        raise RuntimeError("GW credentials are missing.")
    try:
        session.get(GW_FRAMESET_URL, timeout=GW_HTTP_TIMEOUT_SEC)
    except Exception:
        pass
    payload = {
        "username": GW_LOGIN_USER,
        "password": GW_LOGIN_PASSWORD,
        "nexturl": "/newweb/gkmis/SaleRsvFabricback2013730/banner_new.asp",
    }
    response = session.post(
        GW_HTTP_BASE_URL.rstrip("/") + "/logon.asp",
        data=payload,
        timeout=GW_HTTP_TIMEOUT_SEC,
        allow_redirects=True,
    )
    text = response.text or ""
    if _gw_http_has_query_form(text):
        return
    probe = session.get(GW_HTTP_BASE_URL.rstrip("/") + "/banner_new.asp", timeout=GW_HTTP_TIMEOUT_SEC)
    probe_text = probe.text or ""
    if _gw_http_has_query_form(probe_text):
        return
    if _gw_http_is_login_form(probe_text) or _gw_http_is_login_form(text):
        raise RuntimeError("GW login failed. Username/password rejected or session blocked.")
    raise RuntimeError("GW login failed: query form not detected.")


def _gw_http_query_ppo(session, ppo: str, factory_flags: list[str] | None = None) -> list[dict]:
    ppo_key = _normalize_ppo_key(ppo)
    if not ppo_key:
        return []
    payload = {
        "selOperator": "=",
        "txtGKNO": "",
        "isppo": "2",
        "selOpPpo": "=",
        "txtPpoNo": ppo_key,
        "B1": "Query",
    }
    for flag in list(factory_flags or GW_DEFAULT_FACTORY_FLAGS):
        payload[str(flag)] = "ON"
    response = session.post(
        GW_HTTP_BASE_URL.rstrip("/") + "/contents.asp",
        data=payload,
        timeout=GW_HTTP_TIMEOUT_SEC,
        allow_redirects=True,
    )
    html_text = response.text or ""
    if _gw_http_is_login_form(html_text):
        raise RuntimeError("GW session expired.")
    parsed = _gw_parse_contents_table(html_text, ppo_filter=ppo_key)
    if not parsed.get("ok"):
        raise RuntimeError(parsed.get("error") or "Cannot parse GW table")
    return parsed.get("rows", [])


async def _gw_get_frames(page):
    await page.wait_for_timeout(300)
    board = page.frame(name="BoardTitle")
    bottom = page.frame(name="frmbuttom")
    if board is not None and bottom is not None:
        return board, bottom
    for frame in page.frames:
        url = str(frame.url or "")
        if board is None and "SaleRsvFabricback2013730/banner_new.asp" in url:
            board = frame
        if bottom is None and "SaleRsvFabricback2013730/contents.asp" in url:
            bottom = frame
    return board, bottom


async def _gw_login_frameset_if_needed(page):
    board, _bottom = await _gw_get_frames(page)
    if board is None:
        raise RuntimeError("GW board frame not found.")
    if await board.locator("#username").count():
        if not str(GW_LOGIN_USER or "").strip() or not str(GW_LOGIN_PASSWORD or "").strip():
            raise RuntimeError("GW credentials missing for auto-login.")
        await board.fill("#username", GW_LOGIN_USER)
        await board.fill("#password", GW_LOGIN_PASSWORD)
        await board.evaluate(
            """
            () => {
              const form = document.getElementById('form1') || document.forms['form1'];
              if (!form) throw new Error('GW login form not found');
              form.method = 'post';
              form.action = '/logon.asp';
              form.submit();
            }
            """
        )
        await page.wait_for_timeout(900)
    board, _bottom = await _gw_get_frames(page)
    if board is None:
        raise RuntimeError("GW board frame missing after login.")
    await board.wait_for_selector('input[name="txtPpoNo"]', timeout=GW_QUERY_TIMEOUT_MS)


async def _gw_query_ppo_in_frameset(page, ppo: str, factory_flags: list[str] | None = None) -> list[dict]:
    flags = list(factory_flags or GW_DEFAULT_FACTORY_FLAGS)
    board, bottom = await _gw_get_frames(page)
    if board is None or bottom is None:
        raise RuntimeError("GW frameset is not ready.")
    await board.wait_for_selector('input[name="txtPpoNo"]', timeout=GW_QUERY_TIMEOUT_MS)
    await board.evaluate(
        """
        ({ ppoNo, checkedFlags }) => {
          const f = document.forms['Query_Form'] || window.Query_Form;
          if (!f) throw new Error('Query_Form not found');
          if (typeof f.reset === 'function') f.reset();
          const allFlags = ['chkGEG','chkYMG','chkGEK','chkTDC','chkCEG','chkCEK','chkNBO','chkEGM','chkEGV','chkPTX','chkEAV'];
          allFlags.forEach((name) => {
            const el = f.elements[name];
            if (el) el.checked = checkedFlags.includes(name);
          });
          if (f.elements['txtPpoNo']) f.elements['txtPpoNo'].value = String(ppoNo || '');
          if (f.elements['txtGKNO']) f.elements['txtGKNO'].value = '';
          if (f.elements['selOpPpo']) f.elements['selOpPpo'].value = '=';
          if (f.elements['selOperator']) f.elements['selOperator'].value = '=';
          if (f.elements['isppo']) f.elements['isppo'].value = '2';
          try {
            const bf = window.parent && window.parent.frames
              ? (window.parent.frames['frmbuttom'] || window.parent.frames['frmBottom'])
              : null;
            if (bf && bf.document && bf.document.body) {
              bf.document.body.innerHTML = '<div id="coiGwLoading" style="font-size:12px;color:#666;padding:8px">Loading...</div>';
            }
          } catch (e) {}
          if (typeof window.submitdata === 'function') {
            window.submitdata();
          } else {
            f.submit();
          }
        }
        """,
        {"ppoNo": str(ppo or ""), "checkedFlags": flags},
    )
    await bottom.wait_for_selector("#maintable", timeout=GW_QUERY_TIMEOUT_MS)
    await page.wait_for_timeout(GW_POST_QUERY_WAIT_MS)
    parsed = _gw_parse_contents_table(await bottom.content(), ppo_filter=ppo)
    if not parsed.get("ok"):
        raise RuntimeError(parsed.get("error") or "Cannot parse GW table")
    return parsed.get("rows", [])


async def _gw_run_jobs_with_page(page, go_jobs: list[dict], factory_flags: list[str] | None = None) -> list[dict]:
    flags = list(factory_flags or GW_DEFAULT_FACTORY_FLAGS)
    await _gw_login_frameset_if_needed(page)
    request_cache: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    results = []

    for job in go_jobs:
        ppo_runs = []
        row_pack = []
        for ppo in job.get("ppo_list") or []:
            ppo_key = _normalize_ppo_key(ppo)
            if not ppo_key:
                continue
            cache_key = (ppo_key, _gw_flags_cache_key(flags))
            try:
                if cache_key in request_cache:
                    rows = [dict(row) for row in request_cache[cache_key]]
                    cache_hit = "request"
                else:
                    rows = _gw_get_ppo_rows_cached(ppo_key, flags)
                    if rows is not None:
                        rows = [dict(row) for row in rows]
                        request_cache[cache_key] = [dict(row) for row in rows]
                        cache_hit = "global"
                    else:
                        rows = []
                        last_error = None
                        for attempt in range(2):
                            try:
                                rows = await _gw_query_ppo_in_frameset(page, ppo_key, factory_flags=flags)
                                last_error = None
                                break
                            except Exception as exc:
                                last_error = exc
                                if attempt == 0:
                                    try:
                                        await page.goto(GW_FRAMESET_URL, wait_until="domcontentloaded", timeout=GW_QUERY_TIMEOUT_MS)
                                        await page.wait_for_timeout(900)
                                        await _gw_login_frameset_if_needed(page)
                                    except Exception:
                                        pass
                        if last_error is not None:
                            raise last_error
                        request_cache[cache_key] = [dict(row) for row in rows]
                        _gw_set_ppo_rows_cached(ppo_key, flags, rows)
                        cache_hit = ""
                row_pack.extend(rows)
                ppo_runs.append({"ppo": ppo_key, "ok": True, "row_count": len(rows), "cache_hit": cache_hit, "error": ""})
            except Exception as exc:
                ppo_runs.append({"ppo": ppo_key, "ok": False, "row_count": 0, "cache_hit": "", "error": str(exc)})
        dedup_rows = _dedupe_gw_rows(row_pack)
        results.append(
            {
                "go": job.get("go", ""),
                "style_no": job.get("style_no", ""),
                "ppo_list": list(job.get("ppo_list") or []),
                "ppo_count": len(job.get("ppo_list") or []),
                "ppo_runs": ppo_runs,
                "rows": dedup_rows,
                "row_count": len(dedup_rows),
                "ok": any(run.get("ok") for run in ppo_runs) if ppo_runs else False,
            }
        )
    return results


async def _gw_query_ppos_with_headless(go_jobs: list[dict], factory_flags: list[str] | None = None) -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await _launch_headless_browser(p)
        context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1360, "height": 900})
        page = await context.new_page()
        try:
            await page.goto(GW_FRAMESET_URL, wait_until="domcontentloaded", timeout=GW_QUERY_TIMEOUT_MS)
            await page.wait_for_timeout(700)
            return await _gw_run_jobs_with_page(page, go_jobs, factory_flags=factory_flags)
        finally:
            await context.close()
            await browser.close()


async def _gw_query_ppos_with_edge(go_jobs: list[dict], factory_flags: list[str] | None = None) -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(EDGE_CDP_URL)
        if not browser.contexts:
            raise RuntimeError("Edge debug has no context.")
        context = browser.contexts[0]
        page = None
        for item in context.pages:
            if "SaleRsvFabricback2013730/rsvroot.asp" in str(item.url or ""):
                page = item
                break
        created_page = False
        if page is None:
            page = await context.new_page()
            created_page = True
            await page.goto(GW_FRAMESET_URL, wait_until="domcontentloaded", timeout=GW_QUERY_TIMEOUT_MS)
            await page.wait_for_timeout(700)
        try:
            return await _gw_run_jobs_with_page(page, go_jobs, factory_flags=factory_flags)
        finally:
            if created_page:
                await page.close()


def _gw_query_ppos_with_http(go_jobs: list[dict], factory_flags: list[str] | None = None) -> list[dict]:
    flags = list(factory_flags or GW_DEFAULT_FACTORY_FLAGS)
    session = _gw_http_new_session()
    _gw_http_login(session)
    request_cache: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    results = []

    for job in go_jobs:
        ppo_runs = []
        row_pack = []
        for ppo in job.get("ppo_list") or []:
            ppo_key = _normalize_ppo_key(ppo)
            if not ppo_key:
                continue
            cache_key = (ppo_key, _gw_flags_cache_key(flags))
            try:
                if cache_key in request_cache:
                    rows = [dict(row) for row in request_cache[cache_key]]
                    cache_hit = "request"
                else:
                    rows = _gw_get_ppo_rows_cached(ppo_key, flags)
                    if rows is not None:
                        rows = [dict(row) for row in rows]
                        request_cache[cache_key] = [dict(row) for row in rows]
                        cache_hit = "global"
                    else:
                        rows = _gw_http_query_ppo(session, ppo_key, factory_flags=flags)
                        request_cache[cache_key] = [dict(row) for row in rows]
                        _gw_set_ppo_rows_cached(ppo_key, flags, rows)
                        cache_hit = ""
                row_pack.extend(rows)
                ppo_runs.append({"ppo": ppo_key, "ok": True, "row_count": len(rows), "cache_hit": cache_hit, "error": ""})
            except Exception as exc:
                ppo_runs.append({"ppo": ppo_key, "ok": False, "row_count": 0, "cache_hit": "", "error": str(exc)})
        dedup_rows = _dedupe_gw_rows(row_pack)
        results.append(
            {
                "go": job.get("go", ""),
                "style_no": job.get("style_no", ""),
                "ppo_list": list(job.get("ppo_list") or []),
                "ppo_count": len(job.get("ppo_list") or []),
                "ppo_runs": ppo_runs,
                "rows": dedup_rows,
                "row_count": len(dedup_rows),
                "ok": any(run.get("ok") for run in ppo_runs) if ppo_runs else False,
            }
        )
    return results


def _gw_collect_cached_results(go_jobs: list[dict], factory_flags: list[str] | None = None) -> tuple[bool, list[dict]]:
    flags = list(factory_flags or GW_DEFAULT_FACTORY_FLAGS)
    results = []
    for job in go_jobs:
        ppo_runs = []
        row_pack = []
        for ppo in job.get("ppo_list") or []:
            ppo_key = _normalize_ppo_key(ppo)
            rows = _gw_get_ppo_rows_cached(ppo_key, flags)
            if rows is None:
                return False, []
            row_pack.extend(rows)
            ppo_runs.append({"ppo": ppo_key, "ok": True, "row_count": len(rows), "cache_hit": "global", "error": ""})
        dedup_rows = _dedupe_gw_rows(row_pack)
        results.append(
            {
                "go": job.get("go", ""),
                "style_no": job.get("style_no", ""),
                "ppo_list": list(job.get("ppo_list") or []),
                "ppo_count": len(job.get("ppo_list") or []),
                "ppo_runs": ppo_runs,
                "rows": dedup_rows,
                "row_count": len(dedup_rows),
                "ok": True,
            }
        )
    return True, results


def query_gw_by_go_list(go_list: list[str], factory_flags: list[str] | None = None, backend: str = "auto") -> dict:
    started_at = time.time()
    go_values = []
    seen = set()
    for raw in go_list or []:
        go = str(raw or "").strip().upper()
        if not go or go in seen:
            continue
        seen.add(go)
        go_values.append(go)
    if not go_values:
        return {"ok": False, "error": "Please provide GO list", "results": []}

    flags = list(factory_flags or GW_DEFAULT_FACTORY_FLAGS)
    precheck = []
    go_jobs = []
    mapping_started_at = time.time()
    for go in go_values:
        info = fetch_go_ppo_mapping_only(go)
        precheck.append(
            {
                "go": go,
                "ok": bool(info.get("ok")),
                "style_no": info.get("style_no", ""),
                "ppo_count": len(info.get("ppo_list") or []),
                "ppo_list": info.get("ppo_list", []),
                "error": info.get("error", ""),
                "source_url": info.get("source_url", ""),
            }
        )
        if info.get("ok") and info.get("ppo_list"):
            go_jobs.append({"go": go, "style_no": info.get("style_no", ""), "ppo_list": info.get("ppo_list", [])})

    mapping_seconds = round(time.time() - mapping_started_at, 3)
    total_ppo = sum(len(job.get("ppo_list") or []) for job in go_jobs)
    if not go_jobs:
        return {
            "ok": False,
            "error": "No GO with PPO mapping found",
            "backend_requested": backend,
            "backend": "",
            "factory_flags": flags,
            "precheck": precheck,
            "results": [],
            "total_go": len(go_values),
            "total_ppo": 0,
            "total_rows": 0,
            "mapping_seconds": mapping_seconds,
            "gw_seconds": 0,
            "elapsed_seconds": round(time.time() - started_at, 3),
        }

    cache_hit, cached_results = _gw_collect_cached_results(go_jobs, flags)
    if cache_hit:
        return {
            "ok": True,
            "backend_requested": backend,
            "backend": "cache",
            "backend_attempts": ["cache"],
            "backend_errors": [],
            "factory_flags": flags,
            "precheck": precheck,
            "results": cached_results,
            "total_go": len(go_values),
            "total_ppo": total_ppo,
            "total_rows": sum(int(item.get("row_count") or 0) for item in cached_results),
            "mapping_seconds": mapping_seconds,
            "gw_seconds": 0,
            "elapsed_seconds": round(time.time() - started_at, 3),
        }

    backend_key = str(backend or "auto").strip().lower()
    if backend_key == "edge":
        attempt_order = ["edge", "headless", "http"]
    elif backend_key == "headless":
        attempt_order = ["headless", "edge", "http"]
    elif backend_key == "http":
        attempt_order = ["http", "headless", "edge"]
    else:
        attempt_order = ["headless", "edge", "http"]

    backend_errors = []
    attempts_done = []
    gw_started_at = time.time()
    for mode in attempt_order:
        attempts_done.append(mode)
        try:
            if mode == "headless":
                results = _run_async(_gw_query_ppos_with_headless(go_jobs, factory_flags=flags))
            elif mode == "edge":
                results = _run_async(_gw_query_ppos_with_edge(go_jobs, factory_flags=flags))
            else:
                results = _gw_query_ppos_with_http(go_jobs, factory_flags=flags)
            total_rows = sum(int(item.get("row_count") or 0) for item in results)
            if any(item.get("ok") for item in results):
                return {
                    "ok": True,
                    "backend_requested": backend_key,
                    "backend": mode,
                    "backend_attempts": attempts_done,
                    "backend_errors": backend_errors,
                    "factory_flags": flags,
                    "precheck": precheck,
                    "results": results,
                    "total_go": len(go_values),
                    "total_ppo": total_ppo,
                    "total_rows": total_rows,
                    "mapping_seconds": mapping_seconds,
                    "gw_seconds": round(time.time() - gw_started_at, 3),
                    "elapsed_seconds": round(time.time() - started_at, 3),
                }
            backend_errors.append({"backend": mode, "error": "No GW rows returned"})
        except Exception as exc:
            backend_errors.append({"backend": mode, "error": str(exc)})

    return {
        "ok": False,
        "error": backend_errors[-1]["error"] if backend_errors else "GW query failed",
        "backend_requested": backend_key,
        "backend": "",
        "backend_attempts": attempts_done,
        "backend_errors": backend_errors,
        "factory_flags": flags,
        "precheck": precheck,
        "results": [],
        "total_go": len(go_values),
        "total_ppo": total_ppo,
        "total_rows": 0,
        "mapping_seconds": mapping_seconds,
        "gw_seconds": round(time.time() - gw_started_at, 3),
        "elapsed_seconds": round(time.time() - started_at, 3),
    }


def fetch_tendam_ppo_status(ppo: str) -> dict:
    ppo_key = _normalize_ppo_key(ppo)
    if not ppo_key:
        return {"ok": False, "error": "PPO number required", "ppo": "", "lots": []}
    url = tendam_ppo_status_url(ppo_key)
    try:
        html_text, _response = _fetch_text(url, 30)
    except Exception as exc:
        return {"ok": False, "error": "Cannot fetch PPO page: %s" % exc, "ppo": ppo_key, "lots": [], "source_url": url}

    rows_html = re.findall(r'<tr>\s*\n?\s*<td height="42".*?</tr>', html_text, re.DOTALL | re.IGNORECASE)
    results = []
    for row_html in rows_html:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL | re.IGNORECASE)
        clean = [_gw_clean_cell(cell) for cell in cells]
        if len(clean) < 10:
            continue
        results.append(
            {
                "destination": clean[0] if len(clean) > 0 else "",
                "customer": clean[1] if len(clean) > 1 else "",
                "ppo_no": clean[2] if len(clean) > 2 else "",
                "go": clean[3] if len(clean) > 3 else "",
                "combo": clean[4] if len(clean) > 4 else "",
                "gf_no": clean[5] if len(clean) > 5 else "",
                "lot_no": clean[6] if len(clean) > 6 else "",
                "quantity": clean[7] if len(clean) > 7 else "",
                "material_receive_time": clean[8] if len(clean) > 8 else "",
                "gew_ex_mill_date": clean[9] if len(clean) > 9 else "",
                "plan_exit_cmill": clean[10] if len(clean) > 10 else "",
                "fi_last_tostore": clean[11] if len(clean) > 11 else "",
                "gmt_del_date": clean[12] if len(clean) > 12 else "",
                "plan_prod_comp": clean[13] if len(clean) > 13 else "",
                "attemper_remark": clean[14] if len(clean) > 14 else "",
                "hold_flag": clean[15] if len(clean) > 15 else "",
                "yd_actual_finish": clean[16] if len(clean) > 16 else "",
                "wv_actual_finish": clean[17] if len(clean) > 17 else "",
                "wv_status": clean[18] if len(clean) > 18 else "",
                "fn_status": clean[19] if len(clean) > 19 else "",
                "fi_status": clean[20] if len(clean) > 20 else "",
                "stock_qty": clean[21] if len(clean) > 21 else "",
                "shipped_qty": clean[22] if len(clean) > 22 else "",
                "balance": clean[23] if len(clean) > 23 else "",
            }
        )
    return {"ok": True, "ppo": ppo_key, "total": len(results), "lots": results, "source_url": url}


def fetch_ppo_browse(ppo: str, go: str = "") -> dict:
    ppo_key = _normalize_ppo_key(ppo)
    go_key = str(go or "").strip().upper()
    if not ppo_key:
        return {"ok": False, "error": "PPO number required", "ppo": "", "rows": []}
    url = ppo_browse_url(ppo_key)
    try:
        html_text, _response = _fetch_text(url, 30)
    except Exception as exc:
        return {"ok": False, "error": "Cannot fetch PPO page: %s" % exc, "ppo": ppo_key, "go": go_key, "rows": [], "source_url": url}
    if not str(html_text or "").strip():
        return {
            "ok": False,
            "error": "PPO browse returned empty response.",
            "ppo": ppo_key,
            "go": go_key,
            "rows": [],
            "source_url": url,
        }

    header = {}
    header_fields = [
        ("ppo_no", r"PPO No:.*?<b>(.*?)</b>"),
        ("customer", r"Customer:.*?<b>(.*?)</b>"),
        ("ppo_type", r"PPO Type:.*?<b>(.*?)</b>"),
        ("season", r"Season:.*?<b>(.*?)</b>"),
        ("style_no", r"Style No:.*?<b>(.*?)</b>"),
        ("receive_date", r"Receive Date:.*?<b>(.*?)</b>"),
    ]
    for key, pattern in header_fields:
        match = re.search(pattern, html_text, re.DOTALL | re.IGNORECASE)
        header[key] = _gw_clean_cell(match.group(1)) if match else ""

    results = []
    total_qty = 0
    if BeautifulSoup is not None:
        soup = BeautifulSoup(str(html_text or ""), "html.parser")
        header_map: dict[str, int] = {}
        target_rows = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr", recursive=False)
            if len(rows) < 2:
                continue
            header_cells = [
                _gw_clean_cell(cell.get_text(" ", strip=True))
                for cell in rows[0].find_all(["td", "th"], recursive=False)
            ]
            normalized = [re.sub(r"[^A-Z0-9]+", "", str(cell or "").upper()) for cell in header_cells]
            if "COMBO" in normalized and "LOT" in normalized and "QTY" in normalized and "BPODATE" in normalized:
                header_map = {token: index for index, token in enumerate(normalized) if token}
                target_rows = rows[1:]
                break

        current_section = ""
        max_idx = max(header_map.values()) if header_map else -1
        for row in target_rows:
            cells = row.find_all(["td", "th"], recursive=False)
            values = [_gw_clean_cell(cell.get_text(" ", strip=True)) for cell in cells]
            if not any(values):
                continue
            if len(values) == 1:
                current_section = values[0]
                continue
            if max_idx >= 0 and len(values) <= max_idx:
                continue

            combo = values[header_map.get("COMBO", 0)] if "COMBO" in header_map else ""
            lot_raw = values[header_map.get("LOT", 0)] if "LOT" in header_map else ""
            lot_match = re.match(r"^\s*(\d+)", lot_raw)
            lot_no = lot_match.group(1) if lot_match else ""
            if not combo or not lot_no:
                continue

            record = {
                "combo_group": current_section,
                "combo": combo,
                "lot": lot_no,
                "lot_raw": lot_raw,
                "del_date": values[header_map.get("DELDATE", 0)] if "DELDATE" in header_map else "",
                "plan_actual_date": values[header_map.get("PLANACTUALDATE", 0)] if "PLANACTUALDATE" in header_map else "",
                "del_cfm": values[header_map.get("DELCFM", 0)] if "DELCFM" in header_map else "",
                "dest": values[header_map.get("DEST", 0)] if "DEST" in header_map else "",
                "ship_by": values[header_map.get("SHIPBY", 0)] if "SHIPBY" in header_map else "",
                "qty": values[header_map.get("QTY", 0)] if "QTY" in header_map else "",
                "pkd_qty": values[header_map.get("PKDQTY", 0)] if "PKDQTY" in header_map else "0",
                "spd_qty": values[header_map.get("SPDQTY", 0)] if "SPDQTY" in header_map else "0",
                "ship_ratio": values[header_map.get("SHIP", 0)] if "SHIP" in header_map else "0",
                "end_flag": values[header_map.get("END", 0)] if "END" in header_map else "",
                "last_ship": values[header_map.get("LASTSHIP", 0)] if "LASTSHIP" in header_map else "",
                "allowance": values[header_map.get("ALLOWANCE", 0)] if "ALLOWANCE" in header_map else "",
                "color_direction": values[header_map.get("COLORDIRECTION", 0)] if "COLORDIRECTION" in header_map else "",
                "job_no": values[header_map.get("JOBNO", 0)] if "JOBNO" in header_map else "",
                "kn": values[header_map.get("KN", 0)] if "KN" in header_map else "",
                "gk_no": values[header_map.get("GKNO", 0)] if "GKNO" in header_map else "",
                "process": values[header_map.get("PROCESS", 0)] if "PROCESS" in header_map else "",
                "bpo_date": values[header_map.get("BPODATE", 0)] if "BPODATE" in header_map else "",
                # Preserve legacy keys used by older UI payloads.
                "gew_ex_mill": values[header_map.get("LASTSHIP", 0)] if "LASTSHIP" in header_map else "",
                "plan_exit_cmill": values[header_map.get("PLANACTUALDATE", 0)] if "PLANACTUALDATE" in header_map else "",
                "gmt_del": values[header_map.get("DELDATE", 0)] if "DELDATE" in header_map else "",
                "plan_prod_comp": values[header_map.get("PLANACTUALDATE", 0)] if "PLANACTUALDATE" in header_map else "",
            }
            try:
                total_qty += int(float(str(record["qty"]).replace(",", "")))
            except Exception:
                pass
            results.append(record)

    if not results:
        html_clean = re.sub(
            r"<div[^>]*id\s*=\s*[\"']Layer\d+[\"'][^>]*>.*?</div>",
            "",
            html_text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        rows_raw = re.findall(r"<TR>\s*\n?\s*<TD>\s*(\d+@.*?)</TD>(.*?)</TR>", html_clean, re.DOTALL | re.IGNORECASE)
        for combo_td, rest_td in rows_raw:
            cells = [combo_td] + re.findall(r"<TD[^>]*>(.*?)</TD>", rest_td, re.DOTALL | re.IGNORECASE)
            clean = [_gw_clean_cell(cell) for cell in cells]
            if len(clean) < 9:
                continue
            record = {
                "combo": clean[0] if len(clean) > 0 else "",
                "lot": clean[2] if len(clean) > 2 else "",
                "del_date": clean[3] if len(clean) > 3 else "",
                "qty": clean[8] if len(clean) > 8 else "",
                "gew_ex_mill": clean[5] if len(clean) > 5 else "",
                "plan_exit_cmill": clean[4] if len(clean) > 4 else "",
                "gmt_del": clean[3] if len(clean) > 3 else "",
                "plan_prod_comp": clean[4] if len(clean) > 4 else "",
                "pkd_qty": clean[9] if len(clean) > 9 else "0",
                "spd_qty": clean[10] if len(clean) > 10 else "0",
                "ship_ratio": clean[11] if len(clean) > 11 else "0",
                "gk_no": clean[18] if len(clean) > 18 else "",
                "process": clean[19] if len(clean) > 19 else "",
                "bpo_date": clean[20] if len(clean) > 20 else "",
            }
            try:
                total_qty += int(str(record["qty"]).replace(",", ""))
            except Exception:
                pass
            results.append(record)

    return {
        "ok": True,
        "ppo": ppo_key,
        "go": go_key,
        "header": header,
        "total": len(results),
        "total_qty": total_qty,
        "rows": results,
        "source_url": url,
    }
