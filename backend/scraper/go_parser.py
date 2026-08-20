from __future__ import annotations

import html
import re
from html.parser import HTMLParser


def _clean(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _to_number(value: object):
    text = _clean(value).replace(",", "").replace("%", "")
    if not text:
        return 0
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


class _TableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            if self._table is None:
                self._table = []
        elif tag == "tr":
            if self._table is not None:
                self._row = []
        elif tag in {"td", "th"}:
            if self._row is not None:
                self._cell = []
                self._in_cell = True
        elif tag == "br" and self._in_cell and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._in_cell and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"}:
            if self._row is not None and self._cell is not None:
                self._row.append(_clean("".join(self._cell)))
            self._cell = None
            self._in_cell = False
        elif tag == "tr":
            if self._table is not None and self._row is not None and any(_clean(c) for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            if self._table is not None and self._table:
                self.tables.append(self._table)
            self._table = None


def _normalize_header(text: object) -> str:
    return re.sub(r"[^A-Z0-9/#%]+", " ", _clean(text).upper()).strip()


def _normalize_ppo(text: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", _clean(text).upper())


def _first_label_value(html_text: str, label: str) -> str:
    pattern = re.compile(
        rf"{re.escape(label)}\s*:\s*</td>\s*<td[^>]*>(.*?)</td>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    return _clean(re.sub(r"<[^>]+>", " ", match.group(1))) if match else ""


def _extract_header_meta(html_text: str) -> dict:
    return {
        "style_no": _first_label_value(html_text, "Style No"),
        "style_desc": _first_label_value(html_text, "Style Description"),
        "customer_style": _first_label_value(html_text, "Customer Style No"),
        "customer_name_code": _first_label_value(html_text, "Customer Name(Code)"),
        "brand_name_code": _first_label_value(html_text, "Brand Name(Code)"),
        "customer_label": _first_label_value(html_text, "Customer Label"),
        "garment_type": _first_label_value(html_text, "Garment Type"),
        "season": _first_label_value(html_text, "Season"),
        "buyer": _first_label_value(html_text, "Buyer"),
    }


def _extract_color_summary(tables: list[list[list[str]]]) -> list[dict]:
    best = []
    best_score = -1
    for table in tables:
        header_idx = -1
        color_idx = -1
        desc_idx = -1
        qty_idx = -1
        score = 0
        for i, row in enumerate(table[:8]):
            headers = [_normalize_header(cell) for cell in row]
            for j, header in enumerate(headers):
                if color_idx < 0 and header in {"COLOR", "COLOR CODE"}:
                    color_idx = j
                    score += 1
                elif desc_idx < 0 and ("COLOR DESC" in header or "COLOR DESCRIPTION" in header):
                    desc_idx = j
                    score += 1
                elif qty_idx < 0 and (
                    "ORDER QTY" in header
                    or header == "QTY"
                    or "ORDERQTY" in header
                    or "TOTAL QUANTITY" in header
                ):
                    qty_idx = j
                    score += 1
            if score >= 2:
                header_idx = i
                break
        if header_idx < 0:
            continue

        rows = []
        for row in table[header_idx + 1 :]:
            color = _clean(row[color_idx] if len(row) > color_idx else "")
            desc = _clean(row[desc_idx] if desc_idx >= 0 and len(row) > desc_idx else "")
            qty = _to_number(row[qty_idx] if qty_idx >= 0 and len(row) > qty_idx else "")
            if not color and not desc:
                continue
            if color.upper().startswith("TOTAL"):
                continue
            rows.append(
                {
                    "color_code": color,
                    "color_desc": desc,
                    "qty": qty,
                }
            )
        if rows and score > best_score:
            best_score = score
            best = rows
    return best


def _extract_loose_cells(row_html: str) -> list[str]:
    cells = re.findall(
        r"<t[dh][^>]*>(.*?)(?=(?:<t[dh][^>]*>)|(?:</tr>)|(?:<tr[^>]*>)|$)",
        row_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not cells:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
    return [_clean(re.sub(r"<[^>]+>", " ", cell)) for cell in cells]


def _extract_loose_table_rows(table_html: str) -> list[list[str]]:
    rows = []
    parts = re.split(r"<tr[^>]*>", str(table_html or ""), flags=re.IGNORECASE)
    for part in parts[1:]:
        row_html = re.split(r"</tr>|<tr[^>]*>", part, maxsplit=1, flags=re.IGNORECASE)[0]
        cells = _extract_loose_cells(row_html)
        if any(cells):
            rows.append(cells)
    return rows


def _parse_pct_token(value: object):
    text = _clean(value).replace("%", "")
    if not text:
        return ""
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


def _parse_ship_allowance_token(value: object) -> tuple[object, object]:
    text = _clean(value)
    if not text:
        return "", ""

    normalized = re.sub(r"\s+", "", text).upper()
    normalized = normalized.replace("SHIPMENT", "")
    normalized = normalized.replace("SHIP", "")
    normalized = normalized.replace("%", "")
    normalized = normalized.replace("＋", "+").replace("－", "-")

    def _allowance_number(raw: object) -> object:
        parsed = _parse_pct_token(raw)
        if isinstance(parsed, (int, float)):
            number = abs(float(parsed))
            return int(number) if number.is_integer() else number
        return parsed

    # GO reports use several equivalent formats for a symmetric allowance.
    if "/" in normalized:
        left, right = normalized.split("/", 1)
        if left in {"+", "-", "+-", "-+", "±"}:
            shared = _allowance_number(right)
            return shared, shared
        return _allowance_number(left), _allowance_number(right)

    symmetric = re.fullmatch(r"(?:\+/-|\+-|-/\+|-\+|±)([-+]?\d+(?:\.\d+)?)", normalized)
    if symmetric:
        shared = _allowance_number(symmetric.group(1))
        return shared, shared

    try:
        if float(normalized) == 100.0:
            return 0, 0
    except ValueError:
        pass
    return "", ""


def _parse_total_pieces(value: object):
    text = _clean(value).replace(",", "")
    if not text:
        return 0
    match = re.match(r"^(\d+)", text)
    if match:
        return int(match.group(1))
    try:
        return int(float(text))
    except ValueError:
        return 0


def _split_lot_token(value: object) -> tuple[str, str]:
    text = _clean(value)
    if not text:
        return "", ""
    if "/" in text:
        lot_no, job = text.split("/", 1)
        return _clean(lot_no), _clean(job).upper()
    match = re.match(r"^(\d+)(.*)$", text)
    if match:
        return _clean(match.group(1)), _clean(match.group(2)).upper()
    return text, text.upper()


def _looks_like_job_order_no(value: object) -> bool:
    text = _clean(value).upper()
    if len(text) < 6:
        return False
    if not re.search(r"[A-Z]", text) or not re.search(r"\d", text):
        return False
    if not re.match(r"^[A-Z0-9][A-Z0-9()/-]*$", text):
        return False
    if text.startswith("("):
        return False
    return True


def _find_table_block(html_text: str, header_pattern: str) -> str:
    match = re.search(
        rf"(<table[^>]*>.*?{header_pattern}.*?</table>)",
        str(html_text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _extract_lot_rows_from_html(html_text: str) -> list[dict]:
    table_html = _find_table_block(html_text, r"Lot No\./JO #")
    if not table_html:
        return []
    rows = _extract_loose_table_rows(table_html)
    if len(rows) < 2:
        return []

    results = []
    seen = set()
    for row in rows[1:]:
        if len(row) < 6:
            continue
        raw_lot = _clean(row[0])
        if not raw_lot or raw_lot.upper().startswith("TOTAL"):
            continue
        lot_no, job_order_no = _split_lot_token(raw_lot)
        if not str(lot_no or "").isdigit():
            continue
        if not _looks_like_job_order_no(job_order_no):
            continue
        key = (lot_no, job_order_no)
        if key in seen:
            continue
        seen.add(key)
        ship_token = _clean(row[5] if len(row) > 5 else "")
        minus_pct, plus_pct = _parse_ship_allowance_token(ship_token)
        results.append(
            {
                "lot": lot_no,
                "raw_lot": raw_lot,
                "job_order_no": job_order_no,
                "bpo_date": _clean(row[1] if len(row) > 1 else ""),
                "buyer_po_del_date": _clean(row[1] if len(row) > 1 else ""),
                "original_buyer_po_date": _clean(row[2] if len(row) > 2 else ""),
                "ppc_date": _clean(row[3] if len(row) > 3 else ""),
                "qty": _parse_total_pieces(row[4] if len(row) > 4 else ""),
                "ship_allowance": ship_token,
                "minus_pct": minus_pct,
                "plus_pct": plus_pct,
                "remarks": _clean(row[14] if len(row) > 14 else ""),
            }
        )
    return results


def _extract_ppo_mapping_from_html(html_text: str) -> list[dict]:
    match = re.search(
        r"PPO Mapping</span>\s*(<table[^>]*>.*?</table>)",
        str(html_text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    table_html = match.group(1) if match else ""
    if not table_html:
        return []

    rows = _extract_loose_table_rows(table_html)
    if len(rows) < 2:
        return []

    results = []
    seen = set()
    for row in rows[1:]:
        if len(row) < 2:
            continue
        lot_no = _clean(row[0])
        ppo = _normalize_ppo(row[1])
        if not lot_no or not ppo:
            continue
        key = (lot_no, ppo)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "lot": lot_no,
                "raw_lot": lot_no,
                "ppo": ppo,
            }
        )
    return results


def _extract_ppo_mapping_from_tables(tables: list[list[list[str]]]) -> list[dict]:
    mappings = []
    seen = set()
    for table in tables:
        header_idx = -1
        lot_idx = -1
        ppo_idx = -1
        for i, row in enumerate(table[:6]):
            headers = [_normalize_header(cell) for cell in row]
            for j, header in enumerate(headers):
                if lot_idx < 0 and header == "LOT":
                    lot_idx = j
                if ppo_idx < 0 and header == "PPO":
                    ppo_idx = j
            if lot_idx >= 0 and ppo_idx >= 0:
                header_idx = i
                break
        if header_idx < 0:
            continue

        for row in table[header_idx + 1 :]:
            lot_no = _clean(row[lot_idx] if len(row) > lot_idx else "")
            ppo = _normalize_ppo(row[ppo_idx] if len(row) > ppo_idx else "")
            if not lot_no or not ppo:
                continue
            key = (lot_no, ppo)
            if key in seen:
                continue
            seen.add(key)
            mappings.append({"lot": lot_no, "raw_lot": lot_no, "ppo": ppo})
    return mappings


def _fabric_type_from_gmt_part(value: object) -> str:
    token = _normalize_header(value)
    if token.startswith("FK COLLAR"):
        return "O"
    if token.startswith("FK CUFF"):
        return "F"
    if token.startswith("MAIN BODY2"):
        return "D"
    if token.startswith("TRIM RIB1"):
        return "R"
    if token.startswith("TRIM RIB2"):
        return "I"
    if token.startswith("TRIM FAB1"):
        return "M1"
    if token.startswith("TRIM FAB2"):
        return "M2"
    if token.startswith("MAIN BODY"):
        return "B"
    return ""


def _extract_knit_bom_rows_from_html(html_text: str) -> list[dict]:
    table_html = _find_table_block(html_text, r"PPO Marker YY")
    if not table_html:
        return []

    rows = _extract_loose_table_rows(table_html)
    if len(rows) < 2:
        return []

    results = []
    for row in rows[1:]:
        if len(row) < 12:
            continue
        color_code = _clean(row[0] if len(row) > 0 else "")
        color_desc = _clean(row[1] if len(row) > 1 else "")
        gmt_part = _clean(row[2] if len(row) > 2 else "")
        if not color_code or color_code.upper().startswith("GMT COLOR"):
            continue
        results.append(
            {
                "color_code": color_code,
                "color_desc": color_desc,
                "gmt_part": gmt_part,
                "quality_code": _clean(row[3] if len(row) > 3 else ""),
                "fabric_width": _clean(row[4] if len(row) > 4 else ""),
                "pattern": _clean(row[5] if len(row) > 5 else ""),
                "component_part": _clean(row[6] if len(row) > 6 else ""),
                "fabric_code": _clean(row[7] if len(row) > 7 else ""),
                "combo_name": _clean(row[8] if len(row) > 8 else ""),
                "yy_req_no": _clean(row[9] if len(row) > 9 else ""),
                "ppo_marker_yy": _to_number(row[10] if len(row) > 10 else ""),
                "ppo_yy": _to_number(row[11] if len(row) > 11 else ""),
                "fabric_type_hint": _fabric_type_from_gmt_part(gmt_part),
            }
        )
    return results


def _extract_color_breakdown_rows_from_html(html_text: str) -> list[dict]:
    text = str(html_text or "")
    if not text:
        return []

    pattern = re.compile(
        r"Color Breakdown -Lot\s*:\s*(\d+)\s*&nbsp;&nbsp;&nbsp;([^<]*)</b>\s*<table[^>]*>(.*?)</table>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    results = []
    seen = set()
    for match in pattern.finditer(text):
        lot_no = _clean(match.group(1))
        location = _clean(match.group(2))
        table_html = f"<table>{match.group(3)}</table>"
        rows = _extract_loose_table_rows(table_html)
        if len(rows) < 2:
            continue
        for row in rows[1:]:
            if len(row) < 5:
                continue
            color_code = _clean(row[0] if len(row) > 0 else "")
            color_desc = _clean(row[1] if len(row) > 1 else "")
            cust_color_code = _clean(row[2] if len(row) > 2 else "")
            cust_color_desc = _clean(row[3] if len(row) > 3 else "")
            if not color_code or color_code.upper() == "TOTAL":
                continue
            qty = _to_number(row[-1] if row else "")
            key = (lot_no, color_code, cust_color_code, qty)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "lot": lot_no,
                    "location": location,
                    "gmt_color_code": color_code,
                    "gmt_color_desc": color_desc,
                    "color_code": color_code,
                    "color_desc": color_desc or cust_color_desc,
                    "cust_color_code": cust_color_code,
                    "cust_color_desc": cust_color_desc,
                    "qty": qty,
                }
            )
    return results


def parse_go_report(html_text: str) -> dict:
    parser = _TableExtractor()
    parser.feed(html_text or "")
    tables = parser.tables

    header = _extract_header_meta(html_text or "")
    color_summary = _extract_color_summary(tables)

    lot_rows = _extract_lot_rows_from_html(html_text or "")
    ppo_mapping = _extract_ppo_mapping_from_html(html_text or "")
    knit_bom_rows = _extract_knit_bom_rows_from_html(html_text or "")
    color_breakdown_rows = _extract_color_breakdown_rows_from_html(html_text or "")
    if not ppo_mapping:
        ppo_mapping = _extract_ppo_mapping_from_tables(tables)

    lot_index = {str(item.get("lot") or ""): dict(item) for item in lot_rows if item.get("lot")}
    enriched_mapping = []
    for item in ppo_mapping:
        lot_no = str(item.get("lot") or "")
        lot_info = lot_index.get(lot_no, {})
        enriched_mapping.append(
            {
                **item,
                "job_order_no": lot_info.get("job_order_no", ""),
                "buyer_po_del_date": lot_info.get("buyer_po_del_date", ""),
                "minus_pct": lot_info.get("minus_pct", ""),
                "plus_pct": lot_info.get("plus_pct", ""),
            }
        )

    ppo_refs = sorted({row["ppo"] for row in enriched_mapping if row.get("ppo")})
    return {
        "header": header,
        "ppo_mapping": enriched_mapping,
        "ppo_refs": ppo_refs,
        "color_summary": color_summary,
        "lot_rows": lot_rows,
        "knit_bom_rows": knit_bom_rows,
        "color_breakdown_rows": color_breakdown_rows,
        "table_count": len(tables),
    }
