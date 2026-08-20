from __future__ import annotations

import re

from .go_parser import _TableExtractor, _clean


def _normalize_color_key(text: object) -> str:
    raw = _clean(text).upper().replace("@", " ")
    raw = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _extract_fabric_lines(tables: list[list[list[str]]]) -> list[dict]:
    lines = []
    seen = set()
    for table in tables:
        header_idx = -1
        combo_idx = -1
        color_idx = -1
        fabric_idx = -1
        qty_idx = -1
        for i, row in enumerate(table[:8]):
            headers = [re.sub(r"[^A-Z0-9]+", " ", _clean(cell).upper()).strip() for cell in row]
            for j, header in enumerate(headers):
                if combo_idx < 0 and "FABRIC COMBO" in header:
                    combo_idx = j
                if color_idx < 0 and "COLOR" in header:
                    color_idx = j
                if fabric_idx < 0 and ("FABRIC TYPE" in header or header == "FABRIC"):
                    fabric_idx = j
                if qty_idx < 0 and (
                    "PPO PUR QTY" in header
                    or "ORDER QTY" in header
                    or "PPO QTY" in header
                ):
                    qty_idx = j
            if combo_idx >= 0 or color_idx >= 0:
                header_idx = i
                break
        if header_idx < 0:
            continue

        for row in table[header_idx + 1 :]:
            combo = _clean(row[combo_idx] if combo_idx >= 0 and len(row) > combo_idx else "")
            color = _clean(row[color_idx] if color_idx >= 0 and len(row) > color_idx else "")
            fabric_type = _clean(row[fabric_idx] if fabric_idx >= 0 and len(row) > fabric_idx else "")
            if not combo and not color:
                continue
            qty = _clean(row[qty_idx] if qty_idx >= 0 and len(row) > qty_idx else "")
            key = (combo.upper(), color.upper(), fabric_type.upper(), qty)
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                {
                    "fabric_combo": combo or color,
                    "fabric_color": color,
                    "fabric_type": fabric_type,
                    "order_qty": _parse_number(qty),
                }
            )
    return lines


def _parse_number(value: object):
    text = _clean(value).replace(",", "")
    if not text:
        return 0
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return 0


def _extract_header_meta_from_text(report_text: str) -> dict:
    lines = [str(line or "").strip() for line in str(report_text or "").splitlines()]
    cleaned = [line for line in lines if line]
    brand = ""
    avg_ppo_yy = 0
    for index, line in enumerate(cleaned):
        if not brand and line.upper() == "BRAND" and index + 1 < len(cleaned):
            brand = cleaned[index + 1]
        if line.upper() == "AVG.PPO YY" and index + 1 < len(cleaned):
            avg_ppo_yy = _parse_number(cleaned[index + 1])
    return {"brand": brand, "avg_ppo_yy": avg_ppo_yy}


def _extract_fabric_lines_from_text(report_text: str) -> tuple[list[dict], dict]:
    lines = [str(line or "").strip() for line in str(report_text or "").splitlines() if str(line or "").strip()]
    header_meta = _extract_header_meta_from_text(report_text)
    fabric_lines = []
    seen = set()
    current_part = ""
    combo_pattern = re.compile(r"^([A-Z0-9]{1,4})@(.+?)\(([^()]+)\)$")

    index = 0
    while index < len(lines):
        token = lines[index]
        if index + 1 < len(lines) and lines[index + 1] == "Quality Code:" and " - " in token:
            current_part = token

        if token != "Gmt Color Code @ Fabric Combo Name (Fabric Code)":
            index += 1
            continue

        cursor = index + 1
        while cursor < len(lines) and lines[cursor] != "PPO PUR QTY(YDS)":
            cursor += 1
        if cursor >= len(lines):
            index += 1
            continue
        cursor += 1

        while cursor < len(lines):
            current = lines[cursor]
            if current in {"Total", "PPO Remarks:", "BOM Revision Remarks:", "FabPart", "Gmt Color Code"}:
                break
            if cursor + 1 < len(lines) and lines[cursor + 1] == "Quality Code:" and " - " in current:
                break

            match = combo_pattern.match(current)
            if not match:
                cursor += 1
                continue

            color_code, combo_name, fabric_code = match.groups()
            values = []
            cursor += 1
            while cursor < len(lines):
                probe = lines[cursor]
                if probe in {"Total", "PPO Remarks:", "BOM Revision Remarks:", "FabPart", "Gmt Color Code"}:
                    break
                if combo_pattern.match(probe):
                    break
                if cursor + 1 < len(lines) and lines[cursor + 1] == "Quality Code:" and " - " in probe:
                    break
                values.append(probe)
                cursor += 1

            numeric_values = [_parse_number(item) for item in values if re.match(r"^[0-9][0-9,]*(?:\.[0-9]+)?$", item)]
            gmt_qty = numeric_values[0] if len(numeric_values) >= 1 else 0
            order_qty = numeric_values[1] if len(numeric_values) >= 2 else (numeric_values[-1] if numeric_values else 0)
            ppo_pur_qty = numeric_values[2] if len(numeric_values) >= 3 else (numeric_values[-1] if numeric_values else 0)
            fabric_total_qty = numeric_values[-1] if numeric_values else 0
            key = (current_part.upper(), color_code.upper(), combo_name.upper(), fabric_code.upper())
            if key in seen:
                continue
            seen.add(key)
            fabric_lines.append(
                {
                    "fabric_part": current_part,
                    "color_code": color_code,
                    "fabric_combo": combo_name.strip(),
                    "fabric_color": combo_name.strip(),
                    "fabric_code": fabric_code.strip(),
                    "fabric_type": current_part.split(" - ")[-1].strip() if " - " in current_part else "",
                    "gmt_qty": gmt_qty,
                    "fabric_total_qty": fabric_total_qty,
                    "order_qty": order_qty,
                    "ppo_pur_qty": ppo_pur_qty,
                }
            )
            continue
        index = cursor

    return fabric_lines, header_meta


def _build_combo_payload(
    fabric_lines: list[dict],
    table_count: int = 0,
    source_mode: str = "",
    header_meta: dict | None = None,
) -> dict:
    combos = []
    combo_keys = set()
    for line in fabric_lines:
        combo = line.get("fabric_combo") or ""
        if combo and combo.upper() not in combo_keys:
            combos.append(combo)
            combo_keys.add(combo.upper())

    color_keys = set()
    for combo in combos:
        key = _normalize_color_key(combo)
        if not key:
            continue
        color_keys.add(key)
        parts = [part for part in key.split() if part]
        if len(parts) >= 2:
            for i in range(1, len(parts)):
                suffix = " ".join(parts[i:])
                if len(suffix) >= 3:
                    color_keys.add(suffix)

    return {
        "fabric_lines": fabric_lines,
        "fabric_combos": combos,
        "fabric_color_keys": sorted(color_keys),
        "table_count": table_count,
        "source_mode": source_mode,
        "brand": str((header_meta or {}).get("brand") or "").strip(),
        "avg_ppo_yy": (header_meta or {}).get("avg_ppo_yy", 0),
    }


def parse_ppo_report(html_text: str = "", report_text: str = "") -> dict:
    if report_text:
        text_lines, header_meta = _extract_fabric_lines_from_text(report_text)
        if text_lines:
            return _build_combo_payload(text_lines, table_count=0, source_mode="report_text", header_meta=header_meta)

    parser = _TableExtractor()
    parser.feed(html_text or "")
    tables = parser.tables
    fabric_lines = _extract_fabric_lines(tables)
    return _build_combo_payload(fabric_lines, table_count=len(tables), source_mode="html_tables", header_meta={})
