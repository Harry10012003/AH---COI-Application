from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class YYRequest:
    raw_value: str
    workflow_no: str
    workflow_version_no: str | None = None


@dataclass(frozen=True)
class GOBomRow:
    yy_req_no: str
    ppo_yy: str
    gmt_color_code: str
    gmt_part: str
    combo_name: str
    gmt_color: str = ""
    flow: str = ""
    block_index: int = 0
    table_order: int = 0


@dataclass(frozen=True)
class GOBomBlock:
    flow: str
    section_title: str
    block_index: int
    bom_rows: list[GOBomRow] = field(default_factory=list)


@dataclass(frozen=True)
class GOReportData:
    go_no: str
    yy_requests: list[YYRequest]
    bom_rows: list[GOBomRow]
    bom_blocks: list[GOBomBlock] = field(default_factory=list)
    ppo_numbers: list[str] = field(default_factory=list)
    ppo_mapping_by_lot: dict[str, list[str]] = field(default_factory=dict)
    jo_lot_map: dict[str, str] = field(default_factory=dict)
    color_summary_rows: list["GoColorSummaryRow"] = field(default_factory=list)
    lot_color_rows: list["GoLotColorRow"] = field(default_factory=list)


@dataclass(frozen=True)
class GoColorSummaryRow:
    color_code: str
    color_desc: str
    total_quantity: str


@dataclass(frozen=True)
class GoLotColorRow:
    lot_no: str
    color_code: str
    color_desc: str
    total_quantity: str


@dataclass(frozen=True)
class CmQaAggregateRow:
    ppo_no: str
    combo_name: str
    usage: str
    received_qty: str


@dataclass(frozen=True)
class MarkerRow:
    marker_yy: str
    gmt_color: str
    fabric_part: str
    gmt_color_code: str


@dataclass(frozen=True)
class MESJoRow:
    jo_no: str
    order_qty: str
    color_code: str = ""
    color_name: str = ""
    minus_pct: str = "0"
    plus_pct: str = "0"
    fabric_color: str = ""


@dataclass(frozen=True)
class WebmergeSizeRow:
    go_no: str
    jo_no: str
    color_code: str
    color_desc: str
    size: str
    qty: str


@dataclass(frozen=True)
class WebmergeJoBlock:
    go_no: str
    jo_no: str
    size_rows: list[WebmergeSizeRow] = field(default_factory=list)


@dataclass(frozen=True)
class WebmergeColorSizeAggregate:
    go_no: str
    color_code: str
    color_desc: str
    size: str
    qty: str


@dataclass(frozen=True)
class PpoDetailRow:
    ppo_no: str
    ppo_qty: str
    combo_code: str
    combo_name: str
    fabric_type_code: str = ""


@dataclass(frozen=True)
class PpoColorAggregateRow:
    ppo_no: str
    fabric_type_code: str
    color_code: str
    ppo_qty: str


@dataclass(frozen=True)
class PpoComboAggregateRow:
    ppo_no: str
    fabric_type_code: str
    combo_name: str
    ppo_qty: str


@dataclass(frozen=True)
class KnitPpoAggregateRow:
    ppo_no: str
    fabric_type_code: str
    fabric_part: str
    combo_code: str
    combo_name: str
    ppo_qty: str


@dataclass(frozen=True)
class KnitPpoBulkColorSizeAggregate:
    ppo_no: str
    fabric_part: str
    color_code: str
    color_desc: str
    size: str
    ppo_qty: str


@dataclass(frozen=True)
class WovenPpoYYRow:
    ppo_no: str
    yy_req_no: str
    fabric_part: str
    fabric_combo: str
    ppo_yy: str


@dataclass(frozen=True)
class WovenPpoPartQtyRow:
    ppo_no: str
    fabric_part: str
    fabric_combo: str
    ppo_qty: str


@dataclass(frozen=True)
class COIRow:
    color_code: str
    color_desc: str
    fabric_color: str
    job_order_no: str
    minus_pct: str
    plus_pct: str
    qty: str
    ppo_no: str = ""
    marker_yy: str = ""
    ppo_yy: str = ""
    require_yy_each: str = ""
    require_yy_all: str = ""
    ppo_qty: str = ""


@dataclass(frozen=True)
class LeftExportRow:
    yy_req_no: str
    marker_yy: str
    ppo_yy: str
    gmt_color: str
    fabric_part: str


@dataclass(frozen=True)
class JoSeedRow:
    jo_no: str
    qty: str
    ppo_no: str = ""


@dataclass(frozen=True)
class GoBlock:
    go_no: str
    left_rows: list[LeftExportRow] = field(default_factory=list)
    jo_rows: list[JoSeedRow] = field(default_factory=list)


@dataclass(frozen=True)
class ExportRecord:
    go: str
    yy_req_no: str
    marker_yy: str
    ppo_yy: str
    gmt_color: str
    fabric_part: str
    color_code: str
    color_desc: str
    fabric_color: str
    jo: str
    minus_pct: str
    plus_pct: str
    qty: str
    ppo_no: str
    ppo_qty: str
    go_key: str
    row_index: int
    flow: str = "KNIT"
    combo_name: str = ""
    block_index: int = 0
    section_order: int = 0
    part_order: int = 0
    aggregate_key: str = ""
    is_separator: bool = False
    sheet_kind: str = "COI"
    size: str = ""
