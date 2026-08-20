from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.sources import (
    MES_CUTTING_CACHE_JSON,
    MES_CUTTING_SITES,
    mes_cutting_rpt_url,
)

_cutting_cache: dict[str, dict] = {}


async def _launch_hidden_browser(playwright):
    errors = []
    for kwargs in ({"headless": True}, {"headless": True, "channel": "msedge"}):
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Cannot launch hidden MES browser: %s" % " | ".join(errors))


def _safe_float(value: object):
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text:
        return 0
    try:
        return float(text)
    except ValueError:
        return text


def _normalize_go_list(go_list) -> list[str]:
    if isinstance(go_list, str):
        go_list = re.split(r"[,;\s]+", go_list)
    values: list[str] = []
    seen: set[str] = set()
    for raw in go_list or []:
        go = str(raw or "").strip().upper()
        if not go or go in seen:
            continue
        seen.add(go)
        values.append(go)
    return values


def load_cutting_forecast_cache() -> dict[str, dict]:
    global _cutting_cache
    if MES_CUTTING_CACHE_JSON.exists():
        try:
            _cutting_cache = json.loads(MES_CUTTING_CACHE_JSON.read_text(encoding="utf-8"))
        except Exception:
            _cutting_cache = {}
    return dict(_cutting_cache)


def _save_cutting_forecast_cache() -> None:
    MES_CUTTING_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    MES_CUTTING_CACHE_JSON.write_text(
        json.dumps(_cutting_cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_cutting_forecast_cache() -> dict:
    global _cutting_cache
    _cutting_cache = {}
    if MES_CUTTING_CACHE_JSON.exists():
        MES_CUTTING_CACHE_JSON.unlink()
    return get_cutting_cache_status()


def get_cutting_cache_status() -> dict:
    return {
        "cache_file": str(MES_CUTTING_CACHE_JSON),
        "cached_go_count": len(_cutting_cache),
        "cached_gos": sorted(_cutting_cache.keys())[:100],
        "exists": MES_CUTTING_CACHE_JSON.exists(),
    }


async def _parse_cutting_div1(page):
    raw_rows = await page.evaluate(
        """() => {
            const rows = document.querySelectorAll('#Div1 table tr');
            if (rows.length < 2) return [];
            return Array.from(rows).slice(1).map((row) =>
                Array.from(row.querySelectorAll('td')).map((cell) => cell.innerText.trim())
            ).filter((row) => row.length >= 10);
        }"""
    )
    parsed = []
    for cells in raw_rows or []:
        parsed.append(
            {
                "GO": cells[0] if len(cells) > 0 else "",
                "Color": cells[1] if len(cells) > 1 else "",
                "Color_Desc": cells[2] if len(cells) > 2 else "",
                "Order_QTY": _safe_float(cells[3] if len(cells) > 3 else 0),
                "Fab_Type": cells[4] if len(cells) > 4 else "",
                "Over_Short_Per": cells[5] if len(cells) > 5 else "",
                "Over_Short_QTY": _safe_float(cells[6] if len(cells) > 6 else 0),
                "Plan_Cut_QTY": _safe_float(cells[7] if len(cells) > 7 else 0),
                "GO_Rec_Yds": _safe_float(cells[8] if len(cells) > 8 else 0),
                "Spare_Fab": _safe_float(cells[9] if len(cells) > 9 else 0),
                "Binding_Fab": _safe_float(cells[10] if len(cells) > 10 else 0),
                "Bulk_Fab_Qty": _safe_float(cells[11] if len(cells) > 11 else 0),
                "Marker_Allocated_Yds": _safe_float(cells[12] if len(cells) > 12 else 0),
                "Balance_Qty": _safe_float(cells[13] if len(cells) > 13 else 0),
                "Net_YY": _safe_float(cells[14] if len(cells) > 14 else 0),
                "Marker_YY": _safe_float(cells[15] if len(cells) > 15 else 0),
                "PPO_YY": _safe_float(cells[16] if len(cells) > 16 else 0),
                "MU": _safe_float(cells[17] if len(cells) > 17 else 0),
                "Wastage": cells[18] if len(cells) > 18 else "",
                "Fab_Width": _safe_float(cells[19] if len(cells) > 19 else 0),
            }
        )
    return parsed


async def _parse_cutting_div2(page):
    raw_rows = await page.evaluate(
        """() => {
            const rows = document.querySelectorAll('#Div2 table tr');
            if (rows.length < 3) return [];
            return Array.from(rows).slice(2).map((row) =>
                Array.from(row.querySelectorAll('td')).map((cell) => cell.innerText.trim())
            ).filter((row) => row.length > 0);
        }"""
    )
    details = []
    index = 0
    while index < len(raw_rows or []):
        cells = raw_rows[index]
        if len(cells) >= 10:
            details.append(
                {
                    "JO": cells[0] if len(cells) > 0 else "",
                    "Color": cells[1] if len(cells) > 1 else "",
                    "Color_Desc": cells[2] if len(cells) > 2 else "",
                    "Order_QTY": _safe_float(cells[3] if len(cells) > 3 else 0),
                    "Over_Short_Per": cells[5] if len(cells) > 5 else "",
                    "Plan_Cut_QTY": _safe_float(cells[7] if len(cells) > 7 else 0),
                    "PPO_No": cells[-3] if len(cells) >= 3 else "",
                    "Marker_YY": _safe_float(cells[-2] if len(cells) >= 2 else 0),
                    "PPO_YY": _safe_float(cells[-1] if len(cells) >= 1 else 0),
                }
            )
            index += 3
        else:
            index += 1
    return details


async def _query_single_go_on_page(page, go: str):
    await page.locator("#txtGO").fill("")
    await page.locator("#txtGO").fill(go)
    await page.locator("#btnQuery").click(no_wait_after=True)
    await page.wait_for_selector("#Div1 table tr", timeout=8000)
    await page.wait_for_timeout(300)
    return await _parse_cutting_div1(page), await _parse_cutting_div2(page)


async def _query_gos_from_mes(go_list: list[str], save_cache: bool = True):
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return [], [{"GO": "system", "error": f"Playwright not available: {exc}"}]
    results = []
    errors = []
    async with async_playwright() as playwright:
        browser = await _launch_hidden_browser(playwright)
        context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1360, "height": 900})
        page = await context.new_page()
        try:
            current_site = ""
            for go in go_list:
                found = False
                for site_url in MES_CUTTING_SITES:
                    site_name = "EAV" if "EAV" in site_url else "EGV"
                    if current_site != site_url:
                        await page.goto(site_url, wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(400)
                        current_site = site_url
                    try:
                        summary, details = await _query_single_go_on_page(page, go)
                    except Exception:
                        summary, details = [], []
                    if summary:
                        payload = {
                            "GO": go,
                            "summary": summary,
                            "jo_details": details or [],
                            "site": site_name,
                            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        results.append(payload)
                        _cutting_cache[go] = payload
                        found = True
                        break
                if not found:
                    errors.append({"GO": go, "error": "No data found on EAV or EGV"})
        finally:
            await context.close()
            await browser.close()
    if save_cache and results:
        _save_cutting_forecast_cache()
    return results, errors


def query_mes_cutting(go_list, prefer_cache: bool = True, save_cache: bool = True, allow_live_query: bool = True) -> dict:
    load_cutting_forecast_cache()
    gos = _normalize_go_list(go_list)
    cached = []
    to_query = []
    for go in gos:
        if prefer_cache and go in _cutting_cache:
            cached.append(dict(_cutting_cache[go]))
        else:
            to_query.append(go)
    fresh = []
    errors = []
    if to_query and allow_live_query:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            fresh, errors = loop.run_until_complete(_query_gos_from_mes(to_query, save_cache=save_cache))
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    return {
        "results": cached + fresh,
        "errors": errors,
        "count": len(cached) + len(fresh),
        "from_cache": len(cached),
        "from_live": len(fresh),
        "live_skipped": 0 if allow_live_query else len(to_query),
    }


def get_cutting_forecast(go: str, prefer_cache: bool = True, allow_live_query: bool = True) -> dict:
    result = query_mes_cutting([go], prefer_cache=prefer_cache, allow_live_query=allow_live_query)
    if result["results"]:
        return result["results"][0]
    return {
        "GO": str(go or "").strip().upper(),
        "summary": [],
        "jo_details": [],
        "site": "",
        "error": "" if not allow_live_query else "; ".join(err.get("error", "") for err in result["errors"]),
    }


def get_mes_yy_for_go(go: str, prefer_cache: bool = True) -> dict:
    forecast = get_cutting_forecast(go, prefer_cache=prefer_cache)
    summary = forecast.get("summary") or []
    return {
        "go": str(go or "").strip().upper(),
        "marker_yy": sorted({row.get("Marker_YY") for row in summary if row.get("Marker_YY") not in {"", None, 0}}),
        "ppo_yy": sorted({row.get("PPO_YY") for row in summary if row.get("PPO_YY") not in {"", None, 0}}),
        "net_yy": sorted({row.get("Net_YY") for row in summary if row.get("Net_YY") not in {"", None, 0}}),
        "site": forecast.get("site", ""),
        "error": forecast.get("error", ""),
    }


def get_mes_yy_for_gos(go_list, prefer_cache: bool = True) -> list[dict]:
    return [get_mes_yy_for_go(go, prefer_cache=prefer_cache) for go in _normalize_go_list(go_list)]


def fetch_cutting_report(go: str) -> dict | None:
    try:
        url = mes_cutting_rpt_url(go)
        response = urlopen(Request(url, headers={"User-Agent": "TEST/1.0"}), timeout=10)
        html_text = response.read().decode("utf-8", errors="replace")
    except (URLError, OSError, Exception):
        return None
    if "Please input SCNO" in html_text or len(html_text) < 500:
        return None

    def _extract_cells(row_html: str) -> list[str]:
        cells = re.findall(r"<td[^>]*>.*?</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
        return [re.sub(r"<[^>]+>", "", cell).replace("&nbsp;", "").strip() for cell in cells]

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, flags=re.IGNORECASE | re.DOTALL)
    sizes: list[str] = []
    data_rows: list[list[str]] = []
    header_found = False
    for row_html in rows:
        cells = _extract_cells(row_html)
        if not cells:
            continue
        if not header_found and "Color" in cells and "Size" in cells:
            start = cells.index("Size") + 1
            for item in cells[start:]:
                if item and item != "-":
                    sizes.append(item)
                elif item == "-":
                    break
            header_found = True
        elif header_found:
            data_rows.append(cells)
    if not sizes or not data_rows:
        return None

    def _parse_values(cells: list[str], start_idx: int) -> dict:
        return {
            size: _safe_float(cells[start_idx + index] if start_idx + index < len(cells) else "0")
            for index, size in enumerate(sizes)
        }

    result = {"sizes": sizes, "colors": [], "totals": {}}
    index = 0
    while index < len(data_rows):
        cells = data_rows[index]
        row_type = str(cells[1] if len(cells) > 1 else "").lower()
        if "orderqty" not in row_type:
            index += 1
            continue
        block = {"orderqty": _parse_values(cells, 2), "cutqty": {}, "overper": {}}
        if index + 1 < len(data_rows) and "cutqty" in str(data_rows[index + 1][1] if len(data_rows[index + 1]) > 1 else "").lower():
            block["cutqty"] = _parse_values(data_rows[index + 1], 2)
        if index + 2 < len(data_rows) and "overper" in str(data_rows[index + 2][1] if len(data_rows[index + 2]) > 1 else "").lower():
            block["overper"] = _parse_values(data_rows[index + 2], 2)
        color_name = str(cells[0] if cells else "").strip()
        if color_name.lower() == "total":
            result["totals"] = block
        else:
            block["color"] = color_name
            result["colors"].append(block)
        index += 3
    return result if result["colors"] else None


load_cutting_forecast_cache()
