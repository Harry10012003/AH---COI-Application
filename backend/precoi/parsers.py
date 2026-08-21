from __future__ import annotations

from collections import defaultdict
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from bs4 import BeautifulSoup
from bs4.element import Tag

from .exceptions import ParseError, ValidationError
from .models import (
    CmQaAggregateRow,
    GOBomBlock,
    GOBomRow,
    GOReportData,
    GoColorSummaryRow,
    GoLotColorRow,
    JoSeedRow,
    KnitPpoBulkColorSizeAggregate,
    MarkerRow,
    MESJoRow,
    WebmergeColorSizeAggregate,
    WebmergeJoBlock,
    WebmergeSizeRow,
    WovenPpoPartQtyRow,
    WovenPpoYYRow,
    YYRequest,
)

GO_PATTERN = re.compile(r"^([A-Z])(\d+)([A-Z])(\d+)$")
YY_REQUEST_PATTERN = re.compile(r"^\s*([A-Z0-9]+)(?:\s*\(\s*(\d+)\s*\))?\s*$", re.IGNORECASE)
GO_FLOW_MAP = {
    "S": "KNIT",
    "V": "KNIT",
    "D": "WOVEN",
    "K": "WOVEN",
}
SECTION_FLOW_MAP = {
    "woven fabric bom information": "WOVEN",
    "knit fabric bom information": "KNIT",
}
PPO_NUMBER_PATTERN = re.compile(r"^[A-Z0-9-]{6,}$")


def normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def compact_upper(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def normalize_header(text: str | None) -> str:
    return normalize_space(text).casefold()


def normalize_lookup_text(text: str | None) -> str:
    return normalize_space(text).upper()


def parse_go_batch(text: str) -> list[str]:
    ordered_gos, invalid_tokens = split_go_batch(text)
    if invalid_tokens:
        raise ValidationError(f"Invalid GO: {invalid_tokens[0]}")
    if not ordered_gos:
        raise ValidationError("No valid GO found.")
    return ordered_gos


def split_go_batch(text: str) -> tuple[list[str], list[str]]:
    ordered_gos: list[str] = []
    seen: set[str] = set()
    invalid_tokens: list[str] = []

    for raw_token in re.split(r"[\s,;]+", text or ""):
        candidate = compact_upper(raw_token)
        if not candidate:
            continue
        if not GO_PATTERN.fullmatch(candidate):
            invalid_tokens.append(raw_token or candidate)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered_gos.append(candidate)

    return ordered_gos, invalid_tokens


def parse_yy_request(value: str) -> YYRequest:
    candidate = compact_upper(value)
    match = YY_REQUEST_PATTERN.fullmatch(candidate)
    if not match:
        raise ParseError(f"Invalid YY Req No: {value}")

    workflow_no = match.group(1).upper()
    workflow_version_no = match.group(2) or None
    raw_value = workflow_no if workflow_version_no is None else f"{workflow_no}({workflow_version_no})"
    return YYRequest(
        raw_value=raw_value,
        workflow_no=workflow_no,
        workflow_version_no=workflow_version_no,
    )


def classify_go_flow(go_no: str) -> str:
    candidate = compact_upper(go_no)
    match = GO_PATTERN.fullmatch(candidate)
    if not match:
        raise ValidationError(f"Invalid GO: {go_no}")

    flow_code = match.group(3)
    flow = GO_FLOW_MAP.get(flow_code)
    if flow is None:
        raise ValidationError(f"Unsupported GO type for {candidate}. Expected middle code S/V/D/K.")
    return flow


def extract_hidden_inputs(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    values: dict[str, str] = {}
    for input_tag in soup.find_all("input"):
        if str(input_tag.get("type", "")).lower() != "hidden":
            continue
        name = input_tag.get("name")
        if not name:
            continue
        values[name] = input_tag.get("value", "")
    return values


def parse_go_report(html: str, go_no: str) -> GOReportData:
    soup = BeautifulSoup(html, "html.parser")
    bom_rows: list[GOBomRow] = []
    bom_blocks: list[GOBomBlock] = []
    ordered_yy: dict[str, YYRequest] = {}
    ppo_mapping_by_lot = _parse_go_ppo_mapping_by_lot(soup)
    jo_lot_map = _parse_go_jo_lot_map(html)
    color_summary_rows = _parse_go_color_summary_rows(soup)
    lot_color_rows = _parse_go_lot_color_rows(soup)
    ppo_numbers = [
        ppo_no
        for lot_no, ppo_list in ppo_mapping_by_lot.items()
        for ppo_no in ppo_list
        if lot_no != "0"
    ]
    ppo_numbers = list(dict.fromkeys(ppo_numbers))

    required_headers = ["GMT Color Code", "GMT Part", "Combo Name", "YY Req No", "PPO YY"]

    current_section_title = ""
    current_flow = _detect_go_flow_from_code(go_no)

    for element in soup.find_all(["span", "table"]):
        if element.name == "span":
            section_title = normalize_space(element.get_text(" ", strip=True))
            detected_flow = SECTION_FLOW_MAP.get(normalize_header(section_title))
            if detected_flow:
                current_section_title = section_title
                current_flow = detected_flow
            continue

        rows = _extract_table_rows_with_spans(element)
        header_index, header_map = _find_header_row(rows, required_headers)
        if header_index is None or header_map is None:
            continue

        block_flow = current_flow or _detect_go_flow_from_code(go_no)
        if not block_flow:
            continue

        color_code_index = header_map[normalize_header("GMT Color Code")]
        gmt_color_index = header_map.get(normalize_header("GMT Color Desc"))
        gmt_part_index = header_map[normalize_header("GMT Part")]
        combo_name_index = header_map[normalize_header("Combo Name")]
        yy_index = header_map[normalize_header("YY Req No")]
        ppo_yy_index = header_map[normalize_header("PPO YY")]
        max_index = max(
            value
            for value in (color_code_index, gmt_color_index, gmt_part_index, combo_name_index, yy_index, ppo_yy_index)
            if value is not None
        )

        block_index = len(bom_blocks)
        block_rows: list[GOBomRow] = []

        for table_order, row in enumerate(rows[header_index + 1 :]):
            if len(row) <= max_index:
                continue

            gmt_part = normalize_space(row[gmt_part_index]).upper()
            gmt_color_code = normalize_space(row[color_code_index]).upper()
            combo_name = normalize_space(row[combo_name_index])
            gmt_color = "" if gmt_color_index is None else normalize_space(row[gmt_color_index])
            yy_value = normalize_space(row[yy_index])
            ppo_yy = normalize_number(row[ppo_yy_index])

            if not _is_meaningful_bom_row(
                gmt_part=gmt_part,
                gmt_color_code=gmt_color_code,
                gmt_color=gmt_color,
                combo_name=combo_name,
                yy_req_no=yy_value,
                ppo_yy=ppo_yy,
            ):
                continue

            yy_request: YYRequest | None = None
            if yy_value:
                try:
                    yy_request = parse_yy_request(yy_value)
                except ParseError:
                    yy_request = None

            if yy_request is not None:
                ordered_yy.setdefault(yy_request.raw_value, yy_request)

            block_rows.append(
                GOBomRow(
                    yy_req_no="" if yy_request is None else yy_request.raw_value,
                    ppo_yy=ppo_yy,
                    gmt_color_code=gmt_color_code,
                    gmt_part=gmt_part,
                    combo_name=combo_name,
                    gmt_color=gmt_color,
                    flow=block_flow,
                    block_index=block_index,
                    table_order=table_order,
                )
            )

        if not block_rows:
            continue

        bom_blocks.append(
            GOBomBlock(
                flow=block_flow,
                section_title=current_section_title or f"{block_flow.title()} Fabric BOM Information",
                block_index=block_index,
                bom_rows=block_rows,
            )
        )
        bom_rows.extend(block_rows)

    return GOReportData(
        go_no=go_no,
        yy_requests=list(ordered_yy.values()),
        bom_rows=bom_rows,
        bom_blocks=bom_blocks,
        ppo_numbers=ppo_numbers,
        ppo_mapping_by_lot=ppo_mapping_by_lot,
        jo_lot_map=jo_lot_map,
        color_summary_rows=color_summary_rows,
        lot_color_rows=lot_color_rows,
    )


def parse_ypd_marker_rows(html: str) -> list[MarkerRow]:
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        rows = _extract_table_rows(table)
        header_index, gmt_color_index, fabric_part_index, marker_yy_index = _find_ypd_header_indices(rows)
        if header_index is None:
            continue

        max_index = max(gmt_color_index, fabric_part_index, marker_yy_index)
        marker_rows: list[MarkerRow] = []

        for row in rows[header_index + 1 :]:
            if len(row) <= max_index:
                continue

            marker_yy = normalize_space(row[marker_yy_index])
            if not marker_yy or normalize_header(marker_yy) == normalize_header("Marker YY"):
                continue

            gmt_color = normalize_space(row[gmt_color_index])
            fabric_part = normalize_space(row[fabric_part_index])
            marker_rows.append(
                MarkerRow(
                    marker_yy=marker_yy,
                    gmt_color=gmt_color,
                    fabric_part=fabric_part.upper(),
                    gmt_color_code=extract_gmt_color_code(gmt_color),
                )
            )

        return marker_rows

    raise ParseError("Marker YY table not found in YPD report.")


def parse_mes_jo_rows(html: str, go_no: str) -> list[MESJoRow]:
    soup = BeautifulSoup(html, "html.parser")
    go_core = compact_upper(go_no)[1:]
    required_headers = ["COLOR_CODE", "COLOR_NAME", "JO_NO", "OrderQty", "Over/Short% Allowance"]

    for table in soup.find_all("table"):
        rows = _extract_table_rows(table)
        header_index, header_map = _find_header_row(rows, required_headers)
        if header_index is None or header_map is None:
            continue

        color_code_index = header_map[normalize_header("COLOR_CODE")]
        color_name_index = header_map[normalize_header("COLOR_NAME")]
        jo_index = header_map[normalize_header("JO_NO")]
        qty_index = header_map[normalize_header("OrderQty")]
        allowance_index = header_map[normalize_header("Over/Short% Allowance")]
        max_index = max(color_code_index, color_name_index, jo_index, qty_index, allowance_index)
        result: list[MESJoRow] = []

        for row in rows[header_index + 1 :]:
            if len(row) <= max_index:
                continue

            color_code = normalize_space(row[color_code_index]).upper()
            color_name = normalize_space(row[color_name_index])
            jo_no = normalize_space(row[jo_index]).upper()
            order_qty = normalize_space(row[qty_index])
            plus_pct, minus_pct = _parse_allowance(row[allowance_index])
            if not jo_no or "SUB TOTAL" in jo_no or jo_no == "TOTAL":
                continue
            if go_core not in jo_no:
                continue
            if not color_code:
                continue
            if not order_qty:
                continue

            result.append(
                MESJoRow(
                    jo_no=jo_no,
                    order_qty=normalize_number(order_qty),
                    color_code=color_code,
                    color_name=color_name,
                    minus_pct=minus_pct,
                    plus_pct=plus_pct,
                    fabric_color="",
                )
            )

        return result

    raise ParseError("JO_NO / OrderQty table not found in MES report.")


def parse_woven_ppo_yy_rows(html: str, ppo_no: str) -> list[WovenPpoYYRow]:
    soup = BeautifulSoup(html, "html.parser")
    required_headers = ["Fabric Part", "Fabric Combo", "YY JOB No", "PPO YY"]

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        header_index, header_map = _find_header_row(rows, required_headers)
        if header_index is None or header_map is None:
            continue

        fabric_part_index = header_map[normalize_header("Fabric Part")]
        fabric_combo_index = header_map[normalize_header("Fabric Combo")]
        yy_index = header_map[normalize_header("YY JOB No")]
        ppo_yy_index = header_map[normalize_header("PPO YY")]
        max_index = max(fabric_part_index, fabric_combo_index, yy_index, ppo_yy_index)

        result: list[WovenPpoYYRow] = []
        for row in rows[header_index + 1 :]:
            if len(row) <= max_index:
                continue

            yy_req_no = normalize_space(row[yy_index])
            ppo_yy = normalize_number(row[ppo_yy_index])
            if not ppo_yy or ppo_yy == "0":
                continue

            result.append(
                WovenPpoYYRow(
                    ppo_no=ppo_no.strip().upper(),
                    yy_req_no=yy_req_no,
                    fabric_part=normalize_space(row[fabric_part_index]).upper(),
                    fabric_combo=normalize_space(row[fabric_combo_index]),
                    ppo_yy=ppo_yy,
                )
            )

        return result

    raise ParseError("Gament Info + YY table not found in PPO report.")


def parse_woven_ppo_part_qty_rows(html: str, ppo_no: str) -> list[WovenPpoPartQtyRow]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[tuple[str, str], Decimal] = {}
    current_part = ""
    current_combo = ""

    for element in soup.find_all(["span", "table"]):
        if element.name == "span":
            span_classes = {normalize_lookup_text(class_name) for class_name in element.get("class", [])}
            if "BIGFONT1" in span_classes:
                current_part = normalize_space(element.get_text(" ", strip=True)).upper()
                current_combo = ""
            continue

        if not current_part:
            continue

        rows = _extract_table_rows_with_spans(element)
        header_index, header_map = _find_header_row(rows, ["Fabric Combo", "Lot No.", "Order Qty (YDS)"])
        if header_index is None or header_map is None:
            continue

        combo_index = header_map[normalize_header("Fabric Combo")]
        qty_index = header_map[normalize_header("Order Qty (YDS)")]
        max_index = max(combo_index, qty_index)

        for row in rows[header_index + 1 :]:
            if len(row) <= max_index:
                continue

            combo_name = normalize_space(row[combo_index])
            qty = normalize_number(row[qty_index])
            if normalize_header(combo_name).startswith("total"):
                current_combo = ""
                continue
            if combo_name:
                current_combo = combo_name
            else:
                combo_name = current_combo
            if not combo_name:
                continue
            if qty in {"", "0"}:
                continue

            key = (current_part, combo_name)
            current_qty = result.get(key, Decimal("0"))
            result[key] = current_qty + Decimal(normalize_number(qty) or "0")

    if not result:
        raise ParseError("Fabric Lots table not found in PPO report.")

    return [
        WovenPpoPartQtyRow(
            ppo_no=ppo_no.strip().upper(),
            fabric_part=fabric_part,
            fabric_combo=fabric_combo,
            ppo_qty=normalize_number(str(total_qty)),
        )
        for (fabric_part, fabric_combo), total_qty in result.items()
    ]


def parse_webmerge_size_rows(html: str, go_no: str) -> list[WebmergeSizeRow]:
    blocks = parse_webmerge_jo_blocks(html, go_no)
    return [row for block in blocks for row in block.size_rows]


def parse_webmerge_go_color_size_aggregates(html: str, go_no: str) -> list[WebmergeColorSizeAggregate]:
    soup = BeautifulSoup(html, "html.parser")
    collected: dict[tuple[str, str], WebmergeColorSizeAggregate] = {}
    in_color_size_breakdown = False

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        if not rows:
            continue

        flattened_text = normalize_header(" ".join(cell for row in rows for cell in row))
        if "color/size breakdown" in flattened_text:
            in_color_size_breakdown = True
            continue

        if _extract_webmerge_jo_no(rows):
            if in_color_size_breakdown and collected:
                break
            continue

        if not in_color_size_breakdown:
            continue

        for aggregate in _parse_webmerge_go_size_table_rows(rows, go_no=go_no):
            key = (aggregate.color_code.upper(), aggregate.size.upper())
            existing = collected.get(key)
            if existing is None:
                collected[key] = aggregate
                continue

            existing_qty = Decimal(normalize_number(existing.qty) or "0")
            candidate_qty = Decimal(normalize_number(aggregate.qty) or "0")
            if candidate_qty > existing_qty or (
                candidate_qty == existing_qty
                and not normalize_space(existing.color_desc)
                and normalize_space(aggregate.color_desc)
            ):
                collected[key] = aggregate

    if not collected:
        raise ParseError("GO-level Color/Size Breakdown table not found in Webmerge report.")

    return sorted(collected.values(), key=lambda item: (item.color_code, _size_sort_key(item.size)))


def parse_webmerge_jo_blocks(html: str, go_no: str) -> list[WebmergeJoBlock]:
    soup = BeautifulSoup(html, "html.parser")
    block_map: dict[str, list[WebmergeSizeRow]] = {}
    ordered_jo: list[str] = []
    current_jo = ""

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        if not rows:
            continue

        jo_no = _extract_webmerge_jo_no(rows)
        if jo_no:
            current_jo = jo_no
            if current_jo not in block_map:
                block_map[current_jo] = []
                ordered_jo.append(current_jo)

        if not current_jo:
            continue

        for size_row in _parse_webmerge_size_table_rows(rows, go_no=go_no, jo_no=current_jo):
            block_map[current_jo].append(size_row)

    blocks = [WebmergeJoBlock(go_no=go_no, jo_no=jo_no, size_rows=block_map[jo_no]) for jo_no in ordered_jo]
    if any(block.size_rows for block in blocks):
        return blocks

    raise ParseError("Color/Size Breakdown tables not found in Webmerge report.")


def aggregate_webmerge_size_rows(rows: list[WebmergeSizeRow]) -> list[WebmergeColorSizeAggregate]:
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    descriptions: dict[tuple[str, str, str], str] = {}

    for row in rows:
        color_code = normalize_space(row.color_code).upper()
        size = _normalize_size_label(row.size)
        if not color_code or not size:
            continue

        key = (row.go_no.upper(), color_code, size)
        qty_text = normalize_number(row.qty)
        if qty_text in {"", "0"}:
            continue

        totals[key] += Decimal(qty_text)
        if key not in descriptions:
            descriptions[key] = normalize_space(row.color_desc)

    aggregates = [
        WebmergeColorSizeAggregate(
            go_no=go_no,
            color_code=color_code,
            color_desc=descriptions[(go_no, color_code, size)],
            size=size,
            qty=normalize_number(str(qty)),
        )
        for (go_no, color_code, size), qty in totals.items()
        if normalize_number(str(qty)) not in {"", "0"}
    ]
    return sorted(aggregates, key=lambda item: (item.color_code, _size_sort_key(item.size)))


def parse_knit_ppo_bulk_color_size_aggregates(html: str, ppo_no: str) -> list[KnitPpoBulkColorSizeAggregate]:
    soup = BeautifulSoup(html, "html.parser")
    current_part = ""
    totals: dict[tuple[str, str, str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    descriptions: dict[tuple[str, str, str, str], str] = {}

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        if not rows:
            continue

        part_name = _extract_knit_bulk_part_name(rows)
        if part_name:
            current_part = part_name
            continue

        if not current_part:
            continue

        for aggregate in _parse_knit_bulk_size_table(rows, ppo_no=ppo_no, fabric_part=current_part):
            key = (
                aggregate.ppo_no.upper(),
                normalize_lookup_text(aggregate.fabric_part),
                aggregate.color_code.upper(),
                aggregate.size.upper(),
            )
            totals[key] += Decimal(normalize_number(aggregate.ppo_qty) or "0")
            if key not in descriptions:
                descriptions[key] = aggregate.color_desc

    result = [
        KnitPpoBulkColorSizeAggregate(
            ppo_no=ppo_key,
            fabric_part=fabric_part_key,
            color_code=color_code,
            color_desc=descriptions[(ppo_key, fabric_part_key, color_code, size)],
            size=size,
            ppo_qty=normalize_number(str(qty)),
        )
        for (ppo_key, fabric_part_key, color_code, size), qty in totals.items()
        if normalize_number(str(qty)) not in {"", "0"}
    ]
    if result:
        return sorted(result, key=lambda item: (item.fabric_part, item.color_code, _size_sort_key(item.size)))

    raise ParseError("Collar/Cuff size tables not found in Knit PPO bulk report.")


def parse_cm_qa_aggregate_rows(html: str, ppo_no: str) -> list[CmQaAggregateRow]:
    soup = BeautifulSoup(html, "html.parser")
    required_headers = ["InvoiceNo", "Usage", "Received Qty"]

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        header_index, header_map = _find_header_row(rows, required_headers)
        if header_index is None or header_map is None:
            continue

        usage_index = header_map[normalize_header("Usage")]
        received_qty_index = header_map[normalize_header("Received Qty")]
        totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        combo_labels: dict[tuple[str, str], str] = {}
        current_combo = ""

        for row in rows[header_index + 1 :]:
            if not row:
                continue

            first_cell = normalize_space(row[0])
            if normalize_header(first_cell).startswith(normalize_header("Combo :")):
                current_combo = normalize_space(first_cell.split(":", 1)[1])
                continue

            if not current_combo:
                continue
            if len(row) <= max(usage_index, received_qty_index):
                continue

            usage = normalize_space(row[usage_index]).upper()
            received_qty = normalize_number(row[received_qty_index])
            if not usage or not received_qty or received_qty == "0":
                continue

            key = (normalize_lookup_text(current_combo), usage)
            totals[key] += Decimal(received_qty)
            combo_labels.setdefault(key, current_combo)

        aggregates = [
            CmQaAggregateRow(
                ppo_no=compact_upper(ppo_no),
                combo_name=combo_labels[(combo_key, usage)],
                usage=usage,
                received_qty=normalize_number(str(total_qty)),
            )
            for (combo_key, usage), total_qty in totals.items()
            if normalize_number(str(total_qty)) not in {"", "0"}
        ]
        if aggregates:
            return sorted(aggregates, key=lambda item: (normalize_lookup_text(item.combo_name), item.usage))

    raise ParseError("CM QA Color Shading Matching table not found.")


def build_jo_seed_rows(jo_rows: list[MESJoRow]) -> list[JoSeedRow]:
    return [JoSeedRow(jo_no=item.jo_no, qty=item.order_qty) for item in jo_rows]


def extract_gmt_color_code(gmt_color: str) -> str:
    value = normalize_space(gmt_color)
    if " - " in value:
        return normalize_space(value.split(" - ", 1)[0]).upper()
    return value.upper()


def normalize_number(value: str | int | float | Decimal | None) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        stripped = normalize_space(value)
        if not stripped:
            return ""
        candidate = stripped.replace(",", "")
    else:
        candidate = str(value)

    if candidate.startswith("."):
        candidate = f"0{candidate}"

    try:
        decimal_value = Decimal(candidate)
    except (InvalidOperation, ValueError):
        return str(value).strip()

    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _find_ypd_header_indices(rows: list[list[str]]) -> tuple[int | None, int, int, int]:
    marker_group_tokens = (
        normalize_header("Available Width"),
        normalize_header("Fabric Width"),
        normalize_header("Marker Width"),
        normalize_header("Marker YY"),
    )

    for row_index, row in enumerate(rows):
        normalized_row = [normalize_header(value) for value in row]
        gmt_color_index = _find_exact_index(normalized_row, normalize_header("Gmt Color"))
        fabric_part_index = _find_exact_index(normalized_row, normalize_header("Fabric Part"))
        marker_yy_index = _find_exact_index(normalized_row, normalize_header("Marker YY"))

        if gmt_color_index is None or fabric_part_index is None:
            continue

        if marker_yy_index is not None:
            return row_index, gmt_color_index, fabric_part_index, marker_yy_index

        marker_group_index = _find_marker_group_index(normalized_row, marker_group_tokens)
        if marker_group_index is not None:
            return row_index, gmt_color_index, fabric_part_index, marker_group_index + 3

    return None, -1, -1, -1


def _extract_table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in _iter_top_level_rows(table):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue

        flattened_row: list[str] = []
        for cell in cells:
            cell_text = normalize_space(cell.get_text(" ", strip=True))
            colspan = _safe_int(cell.get("colspan"), 1)
            repeat = max(colspan, 1)
            flattened_row.extend([cell_text] * repeat)

        if flattened_row:
            rows.append(flattened_row)

    return rows


def _extract_table_rows_with_spans(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    active_rowspans: dict[int, tuple[str, int]] = {}

    for row in _iter_top_level_rows(table):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue

        flattened_row: list[str] = []
        column_index = 0

        def fill_active_spans() -> None:
            nonlocal column_index
            while column_index in active_rowspans:
                value, remaining = active_rowspans[column_index]
                flattened_row.append(value)
                if remaining <= 1:
                    active_rowspans.pop(column_index, None)
                else:
                    active_rowspans[column_index] = (value, remaining - 1)
                column_index += 1

        fill_active_spans()

        for cell in cells:
            fill_active_spans()
            cell_text = normalize_space(cell.get_text(" ", strip=True))
            colspan = max(_safe_int(cell.get("colspan"), 1), 1)
            rowspan = max(_safe_int(cell.get("rowspan"), 1), 1)

            for _ in range(colspan):
                flattened_row.append(cell_text)
                if rowspan > 1:
                    active_rowspans[column_index] = (cell_text, rowspan - 1)
                column_index += 1
                fill_active_spans()

        fill_active_spans()

        if flattened_row:
            rows.append(flattened_row)

    return rows


def _iter_top_level_rows(table: Tag) -> Iterable[Tag]:
    for row in table.find_all("tr"):
        if row.find_parent("table") is table:
            yield row


def _find_header_row(
    rows: list[list[str]],
    required_headers: list[str],
) -> tuple[int | None, dict[str, int] | None]:
    normalized_required = [normalize_header(header) for header in required_headers]

    for row_index, row in enumerate(rows):
        header_map: dict[str, int] = {}
        for column_index, value in enumerate(row):
            normalized_value = normalize_header(value)
            if normalized_value and normalized_value not in header_map:
                header_map[normalized_value] = column_index

        if all(header in header_map for header in normalized_required):
            return row_index, header_map

    return None, None


def _find_exact_index(values: list[str], target: str) -> int | None:
    for index, value in enumerate(values):
        if value == target:
            return index
    return None


def _find_marker_group_index(values: list[str], required_tokens: tuple[str, ...]) -> int | None:
    for index, value in enumerate(values):
        if value and all(token in value for token in required_tokens):
            return index
    return None


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _detect_go_flow_from_code(go_no: str) -> str:
    try:
        return classify_go_flow(go_no)
    except ValidationError:
        return ""


def _parse_go_ppo_mapping_by_lot(soup: BeautifulSoup) -> dict[str, list[str]]:
    mapping_by_lot: dict[str, list[str]] = {}
    candidate_tables: list[Tag] = []

    for node in soup.find_all(string=lambda text: text and "ppo mapping" in normalize_header(text)):
        parent = node.parent
        if parent is None:
            continue
        next_table = parent.find_next("table")
        if next_table is not None and next_table not in candidate_tables:
            candidate_tables.append(next_table)

    if not candidate_tables:
        candidate_tables = soup.find_all("table")

    for table in candidate_tables:
        rows = _extract_table_rows_with_spans(table)
        header_index, header_map = _find_header_row(rows, ["Lot", "PPO"])
        if header_index is None or header_map is None:
            continue

        lot_index = header_map[normalize_header("Lot")]
        ppo_index = header_map[normalize_header("PPO")]
        for row in rows[header_index + 1 :]:
            if len(row) <= max(lot_index, ppo_index):
                continue
            lot_no = normalize_space(row[lot_index])
            ppo_no = compact_upper(row[ppo_index])
            if not lot_no or lot_no == "0" or not ppo_no or ppo_no == "PPO":
                continue
            if not PPO_NUMBER_PATTERN.fullmatch(ppo_no):
                continue
            existing = mapping_by_lot.setdefault(lot_no, [])
            if ppo_no not in existing:
                existing.append(ppo_no)

    return mapping_by_lot


def _parse_go_jo_lot_map(html: str) -> dict[str, str]:
    jo_lot_map: dict[str, str] = {}
    for lot_no, jo_no in re.findall(r"<td>\s*(\d+)\s*/\s*([A-Z0-9]+)\s*</td>", html, flags=re.IGNORECASE):
        if lot_no == "0":
            continue
        jo_lot_map[jo_no.upper()] = lot_no
    return jo_lot_map


def _parse_go_color_summary_rows(soup: BeautifulSoup) -> list[GoColorSummaryRow]:
    required_headers = ["COLOR CODE", "COLOR DESC", "TOTAL QUANTITY"]

    for table in soup.find_all("table"):
        rows = _extract_table_rows_with_spans(table)
        header_index, header_map = _find_header_row(rows, required_headers)
        if header_index is None or header_map is None:
            continue

        code_index = header_map[normalize_header("COLOR CODE")]
        desc_index = header_map[normalize_header("COLOR DESC")]
        qty_index = header_map[normalize_header("TOTAL QUANTITY")]
        result: list[GoColorSummaryRow] = []

        for row in rows[header_index + 1 :]:
            if len(row) <= max(code_index, desc_index, qty_index):
                continue

            color_code = normalize_space(row[code_index]).upper()
            color_desc = normalize_space(row[desc_index])
            total_quantity = normalize_number(row[qty_index])

            if not color_desc or normalize_header(color_desc).startswith(normalize_header("Color Total")):
                continue
            if not total_quantity:
                continue

            result.append(
                GoColorSummaryRow(
                    color_code=color_code,
                    color_desc=color_desc,
                    total_quantity=total_quantity,
                )
            )

        if result:
            return result

    return []


def _parse_go_lot_color_rows(soup: BeautifulSoup) -> list[GoLotColorRow]:
    result: list[GoLotColorRow] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    for node in soup.find_all(string=lambda text: text and "color breakdown -lot" in normalize_header(text)):
        label_text = normalize_space(str(node))
        match = re.search(r"color breakdown\s*-\s*lot\s*:\s*(\d+)", label_text, flags=re.IGNORECASE)
        if not match:
            continue

        lot_no = match.group(1)
        parent = node.parent
        if parent is None:
            continue

        table = parent.find_next("table")
        if table is None:
            continue

        rows = _extract_loose_html_table_rows(str(table))
        header_index, header_map = _find_header_row(rows, ["Gmt Color Code", "Total"])
        if header_index is None or header_map is None:
            continue

        code_index = header_map[normalize_header("Gmt Color Code")]
        desc_index = (
            header_map.get(normalize_header("Gmt Color Desc."))
            if header_map.get(normalize_header("Gmt Color Desc.")) is not None
            else header_map.get(normalize_header("Gmt Color Desc"))
        )
        total_index = header_map[normalize_header("Total")]
        if desc_index is None:
            continue

        for row in rows[header_index + 1 :]:
            if len(row) <= max(code_index, desc_index, total_index):
                continue

            color_code = normalize_space(row[code_index]).upper()
            color_desc = normalize_space(row[desc_index])
            total_quantity = normalize_number(row[total_index])

            if not color_desc or color_code == "TOTAL" or normalize_header(color_desc) == "total":
                continue
            if not total_quantity or total_quantity == "0":
                continue

            key = (lot_no, color_code, normalize_lookup_text(color_desc), total_quantity)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result.append(
                GoLotColorRow(
                    lot_no=lot_no,
                    color_code=color_code,
                    color_desc=color_desc,
                    total_quantity=total_quantity,
                )
            )

    return result


def _extract_loose_html_table_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_match in re.finditer(r"<tr\b[^>]*>(.*?)(?=(?:<tr\b|</table>|$))", table_html, flags=re.IGNORECASE | re.DOTALL):
        row_html = row_match.group(1)
        flattened_row: list[str] = []
        for cell_match in re.finditer(
            r"<(td|th)\b([^>]*)>(.*?)(?=(?:<(?:td|th)\b|</tr>|$))",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            attrs = cell_match.group(2) or ""
            cell_html = cell_match.group(3) or ""
            cell_text = normalize_space(BeautifulSoup(cell_html, "html.parser").get_text(" ", strip=True))
            colspan_match = re.search(r"colspan\s*=\s*['\"]?(\d+)", attrs, flags=re.IGNORECASE)
            colspan = max(_safe_int(colspan_match.group(1), 1), 1) if colspan_match else 1
            flattened_row.extend([cell_text] * colspan)

        if flattened_row:
            rows.append(flattened_row)

    return rows


def _is_meaningful_bom_row(
    *,
    gmt_part: str,
    gmt_color_code: str,
    gmt_color: str,
    combo_name: str,
    yy_req_no: str,
    ppo_yy: str,
) -> bool:
    if not gmt_part:
        return False
    return any([gmt_color_code, gmt_color, combo_name, yy_req_no, ppo_yy])


def _parse_allowance(value: str) -> tuple[str, str]:
    text = normalize_space(value).replace("%", "")
    if not text:
        return "0", "0"

    match = re.search(r"\+?\s*([0-9.]+)\s*/\s*-\s*([0-9.]+)", text)
    if not match:
        return "0", "0"

    plus_pct = normalize_number(match.group(1))
    minus_pct = normalize_number(match.group(2))
    return plus_pct, minus_pct


def _extract_webmerge_jo_no(rows: list[list[str]]) -> str:
    for row in rows[:6]:
        for cell in row:
            match = re.search(r"JO\s*#:\s*([A-Z0-9]+)", normalize_space(cell), flags=re.IGNORECASE)
            if match:
                return match.group(1).upper()
    return ""


def _parse_webmerge_go_size_table_rows(
    rows: list[list[str]],
    *,
    go_no: str,
) -> list[WebmergeColorSizeAggregate]:
    header_index = _find_go_webmerge_size_header_row(rows)
    if header_index is None:
        return []

    header_row = rows[header_index]
    data_rows = rows[header_index + 1 :]
    sample_row = next((row for row in data_rows if _find_webmerge_color_pair_start(row) is not None), None)
    if sample_row is None:
        return []

    header_profile = _build_go_webmerge_header_profile(header_row, sample_row)
    if header_profile is None:
        return []

    has_sensitive_column, size_labels = header_profile
    leading_field_count = 5 if has_sensitive_column else 4
    result: list[WebmergeColorSizeAggregate] = []

    for row in data_rows:
        color_fields = _extract_webmerge_color_fields(row)
        if color_fields is None:
            continue

        pair_start = color_fields[0]
        color_code = color_fields[1]
        color_desc = color_fields[2]
        size_start_index = pair_start + leading_field_count
        if not color_code or color_code == "TOTAL":
            continue
        if color_desc.upper() == "TOTAL":
            continue

        for offset, size in enumerate(size_labels):
            value_index = size_start_index + offset
            if value_index >= len(row):
                continue

            qty = normalize_number(row[value_index])
            if qty in {"", "0"}:
                continue

            result.append(
                WebmergeColorSizeAggregate(
                    go_no=go_no,
                    color_code=color_code,
                    color_desc=color_desc,
                    size=size,
                    qty=qty,
                )
            )

    return result


def _parse_webmerge_size_table_rows(
    rows: list[list[str]],
    *,
    go_no: str,
    jo_no: str,
) -> list[WebmergeSizeRow]:
    header_index = _find_webmerge_size_header_row(rows)
    if header_index is None:
        return []

    header_row = rows[header_index]
    header_profile = _build_webmerge_header_profile(header_row)
    if header_profile is None:
        return []
    color_code_index, color_desc_index, size_columns = header_profile

    result: list[WebmergeSizeRow] = []
    for row in rows[header_index + 1 :]:
        if len(row) <= color_code_index:
            continue

        color_code = normalize_space(row[color_code_index]).upper()
        color_desc = normalize_space(row[color_desc_index]) if len(row) > color_desc_index else ""
        if not color_code or color_code == "TOTAL":
            continue

        for offset, size in size_columns:
            if offset >= len(row):
                continue

            qty = normalize_number(row[offset])
            if qty in {"", "0"}:
                continue

            result.append(
                WebmergeSizeRow(
                    go_no=go_no,
                    jo_no=jo_no,
                    color_code=color_code,
                    color_desc=color_desc,
                    size=size,
                    qty=qty,
                )
            )

    return result


def _find_webmerge_size_header_row(rows: list[list[str]]) -> int | None:
    for row_index, row in enumerate(rows):
        if row_index > 3:
            break
        if _build_webmerge_header_profile(row) is not None:
            return row_index

    return None


def _find_go_webmerge_size_header_row(rows: list[list[str]]) -> int | None:
    for row_index, row in enumerate(rows):
        if row_index > 4:
            break
        if _build_go_webmerge_header_profile(row, None) is not None:
            return row_index
    return None


def _is_size_token(value: str) -> bool:
    token = normalize_header(value)
    if not token:
        return False
    return bool(_normalize_size_label(token)) or token == "total"


def _build_webmerge_header_profile(header_row: list[str]) -> tuple[int, int, list[tuple[int, str]]] | None:
    normalized_row = [normalize_header(value) for value in header_row]
    if len(header_row) < 5:
        return None
    if not any("colorway#" in value for value in normalized_row):
        return None

    cust_colorway_index = next(
        (index for index, value in enumerate(normalized_row) if "cust colorway#" in value),
        None,
    )
    cust_color_desc_index = next(
        (index for index, value in enumerate(normalized_row) if "cust colorway desc" in value),
        None,
    )
    if cust_colorway_index is None or cust_color_desc_index is None:
        return None

    size_columns = [
        (index, normalize_space(header_row[index]).upper())
        for index in range(cust_color_desc_index + 1, len(header_row))
        if _is_size_token(normalized_row[index]) or normalized_row[index] == "total"
    ]
    if not size_columns:
        return None

    color_code_index = max(cust_colorway_index - 2, 0)
    color_desc_index = max(cust_colorway_index - 1, 0)
    if color_code_index >= len(header_row):
        return None

    if not any(size for _, size in size_columns if size != "TOTAL"):
        return None
    return color_code_index, color_desc_index, [(index, size) for index, size in size_columns if size != "TOTAL"]


def _build_go_webmerge_header_profile(
    header_row: list[str],
    sample_row: list[str] | None,
) -> tuple[bool, list[str]] | None:
    normalized_row = [normalize_header(value) for value in header_row]
    if len(header_row) < 5:
        return None

    has_colorway_header = any("colorway#" in value for value in normalized_row) and any(
        "cust colorway#" in value for value in normalized_row
    )
    has_color_code_header = any("color code" in value for value in normalized_row) and any(
        "cust color code" in value for value in normalized_row
    )
    if not has_colorway_header and not has_color_code_header:
        return None

    size_labels = [_normalize_size_label(value) for value in header_row if _normalize_size_label(value)]
    if not size_labels:
        return None

    if sample_row is not None and _extract_webmerge_color_fields(sample_row) is None:
        return None

    return any("sensitive" in value for value in normalized_row), size_labels


def _find_webmerge_color_pair_start(row: list[str]) -> int | None:
    color_fields = _extract_webmerge_color_fields(row)
    if color_fields is None:
        return None
    return color_fields[0]


def _extract_webmerge_color_fields(row: list[str]) -> tuple[int, str, str] | None:
    for index in range(len(row) - 3):
        first_code = normalize_space(row[index]).upper()
        first_desc = normalize_space(row[index + 1])
        second_code = normalize_space(row[index + 2]).upper()
        second_desc = normalize_space(row[index + 3])
        if not first_code or not first_desc or not second_code or not second_desc:
            continue
        if first_code == "TOTAL" or second_code == "TOTAL":
            continue
        if not re.fullmatch(r"[A-Z0-9()./-]+", first_code):
            continue
        if first_code != second_code:
            continue
        preferred_desc = _pick_webmerge_color_desc(first_desc, second_desc)
        return index, first_code, preferred_desc
    return None


def _pick_webmerge_color_desc(first_desc: str, second_desc: str) -> str:
    normalized_first = normalize_space(first_desc)
    normalized_second = normalize_space(second_desc)
    if normalized_second and len(normalized_second) >= len(normalized_first):
        return normalized_second
    return normalized_first


def _extract_knit_bulk_part_name(rows: list[list[str]]) -> str:
    for row in rows[:4]:
        for cell in row:
            value = normalize_space(cell)
            match = re.match(r"^(FK\s+[A-Z0-9 ]+?)\s*-\s*[A-Z0-9]+$", value, flags=re.IGNORECASE)
            if match:
                return normalize_space(match.group(1)).upper()
    return ""


def _parse_knit_bulk_size_table(
    rows: list[list[str]],
    *,
    ppo_no: str,
    fabric_part: str,
) -> list[KnitPpoBulkColorSizeAggregate]:
    header_index = _find_knit_bulk_size_header_row(rows)
    if header_index is None:
        return []

    header_profile = _build_knit_bulk_header_profile(rows[header_index])
    if header_profile is None:
        return []

    color_code_index, color_desc_index, size_columns = header_profile
    result: list[KnitPpoBulkColorSizeAggregate] = []
    for row in rows[header_index + 1 :]:
        if len(row) <= color_code_index:
            continue

        color_code = normalize_space(row[color_code_index]).upper()
        color_desc = normalize_space(row[color_desc_index]) if len(row) > color_desc_index else ""
        if not color_code or color_code == "TOTAL" or not re.fullmatch(r"[A-Z0-9()/-]{2,}", color_code):
            continue
        if not color_desc or color_desc.upper() == "TOTAL":
            continue

        for column_index, size in size_columns:
            if column_index >= len(row):
                continue
            qty = normalize_number(row[column_index])
            if qty in {"", "0"}:
                continue

            result.append(
                KnitPpoBulkColorSizeAggregate(
                    ppo_no=ppo_no.strip().upper(),
                    fabric_part=fabric_part,
                    color_code=color_code,
                    color_desc=color_desc,
                    size=size,
                    ppo_qty=qty,
                )
            )

    return result


def _find_knit_bulk_size_header_row(rows: list[list[str]]) -> int | None:
    for row_index, row in enumerate(rows):
        if row_index > 4:
            break
        if _build_knit_bulk_header_profile(row) is not None:
            return row_index
    return None


def _build_knit_bulk_header_profile(header_row: list[str]) -> tuple[int, int, list[tuple[int, str]]] | None:
    normalized_row = [normalize_header(value) for value in header_row]
    if len(header_row) < 7:
        return None

    color_code_index = _find_exact_index(normalized_row, normalize_header("Gmt Color Code"))
    color_desc_index = next(
        (index for index, value in enumerate(normalized_row) if "combo" in value and "size" in value),
        None,
    )
    if color_code_index is None or color_desc_index is None:
        return None

    size_columns = [
        (index, _normalize_size_label(header_row[index]))
        for index in range(color_desc_index + 1, len(header_row))
        if _normalize_size_label(header_row[index]) and normalize_header(header_row[index]) != "total"
    ]
    if not size_columns:
        return None
    return color_code_index, color_desc_index, size_columns


def _normalize_size_label(value: str) -> str:
    text = normalize_space(value).upper()
    if not text:
        return ""
    match = re.match(r"^(XXXL|XXL|XL|XS|S|M|L)\b", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d{1,4}", text):
        return text
    return ""


def _size_sort_key(size: str) -> int:
    order = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "XXXL": 6}
    return order.get(size.upper(), 99)
