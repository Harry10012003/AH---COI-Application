from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import re
from typing import Callable

from .clients import CMQaClient, GOClient, MESSummaryClient, PPOReportClient, PpoDatabaseClient, WebmergeClient, YPDClient
from .exceptions import (
    DatabaseQueryError,
    GORequestError,
    MESRequestError,
    PPORequestError,
    ValidationError,
    WorkbookFormatError,
    YPDAuthenticationError,
    YPDRequestError,
)
from .excel_exporter import COLLAR_SHEET_NAME, MASTER_FILE_NAME, read_records_from_workbook, save_master_workbook, write_workbook
from .models import (
    CmQaAggregateRow,
    ExportRecord,
    GOBomBlock,
    GOBomRow,
    GOReportData,
    GoColorSummaryRow,
    GoLotColorRow,
    KnitPpoBulkColorSizeAggregate,
    MESJoRow,
    MarkerRow,
    WebmergeColorSizeAggregate,
    YYRequest,
)
from .parsers import (
    classify_go_flow,
    normalize_lookup_text,
    normalize_number,
    normalize_space,
    parse_yy_request,
    split_go_batch,
)

LogFn = Callable[[str], None]
MULTI_PPO_CHECK_MESSAGE = "vui long check lai PPO matching"


@dataclass(frozen=True)
class ResolvedBomRow:
    flow: str
    yy_req_no: str
    marker_yy: str
    ppo_yy: str
    gmt_color: str
    fabric_part: str
    combo_name: str
    color_code: str
    block_index: int
    section_order: int
    part_order: int


class GetYYService:
    def __init__(
        self,
        go_client: GOClient | None = None,
        mes_client: MESSummaryClient | None = None,
        webmerge_client: WebmergeClient | None = None,
        cm_client: CMQaClient | None = None,
        ppo_client: PpoDatabaseClient | None = None,
        ppo_report_client: PPOReportClient | None = None,
    ) -> None:
        self.go_client = go_client or GOClient()
        self.mes_client = mes_client or MESSummaryClient()
        self.webmerge_client = webmerge_client or WebmergeClient()
        self.cm_client = cm_client or CMQaClient()
        # Database credentials are only required by PPO enrichment actions.
        # CM/workbook-only flows must remain usable without eagerly creating a
        # SQL client that they never call.
        self.ppo_client = ppo_client
        self.ppo_report_client = ppo_report_client or PPOReportClient()
        self._woven_ypd_version_cache: dict[str, str] = {}

    def _get_ppo_client(self) -> PpoDatabaseClient:
        if self.ppo_client is None:
            self.ppo_client = PpoDatabaseClient()
        return self.ppo_client

    def create_output(
        self,
        go_input: str,
        username: str,
        password: str,
        output_dir: str | Path,
        log: LogFn | None = None,
    ) -> Path:
        logger = log or (lambda message: None)
        output_path = Path(output_dir).expanduser()

        if not output_path.exists():
            raise ValidationError("Output directory does not exist.")
        if not output_path.is_dir():
            raise ValidationError("Output path must be a directory.")

        go_numbers, invalid_tokens = split_go_batch(go_input)
        for invalid_token in invalid_tokens:
            logger(f"[{invalid_token}] Invalid GO, skipped.")
        if not go_numbers:
            raise ValidationError("No valid GO found.")

        logger(f"Processing {len(go_numbers)} GO(s) into {MASTER_FILE_NAME}.")
        fetched_reports: list[tuple[str, GOReportData, str]] = []
        detected_modes: set[str] = set()

        for go_no in go_numbers:
            logger(f"[{go_no}] Loading GO report...")
            try:
                go_report = self.go_client.fetch_go_report(go_no)
            except GORequestError as exc:
                logger(f"[{go_no}] GO error: {exc}")
                continue

            go_mode = "CM" if self._is_cm_go_report(go_report) else "STANDARD"
            detected_modes.add(go_mode)
            fetched_reports.append((go_no, go_report, go_mode))
            logger(f"[{go_no}] Detected {go_mode} flow.")

        if not fetched_reports:
            raise ValidationError("No valid data available for Excel export.")
        if len(detected_modes) > 1:
            raise ValidationError("CM GO khong the batch chung voi GO Knit/Woven thuong vi workbook schema khac nhau.")

        ypd_client = None
        if "STANDARD" in detected_modes:
            ypd_client = YPDClient(username=username, password=password)
        all_records: list[ExportRecord] = []

        for go_no, go_report, go_mode in fetched_reports:
            if go_mode == "CM":
                cm_records = self._build_cm_records_for_go(go_no, go_report, logger)
                if not cm_records:
                    logger(f"[{go_no}] No eligible CM rows found.")
                    continue
                all_records.extend(cm_records)
                continue

            assert ypd_client is not None
            bom_rows = self._build_resolved_bom_rows(go_no, go_report, ypd_client, logger)
            if not bom_rows:
                logger(f"[{go_no}] No eligible BOM rows with complete create data.")
                continue

            mes_rows = self._build_mes_rows(go_no, logger)
            if not mes_rows:
                logger(f"[{go_no}] No JO rows found, skipped.")
                continue

            webmerge_aggregates: list[WebmergeColorSizeAggregate] = []
            if any(self._is_collar_cuff_part(bom_row.fabric_part) for bom_row in bom_rows):
                webmerge_aggregates = self._build_webmerge_go_aggregates(go_no, logger)

            all_records.extend(
                self._build_export_records_for_go(go_no, mes_rows, webmerge_aggregates, bom_rows, logger)
            )

        if not all_records:
            raise ValidationError("No valid data available for Excel export.")

        try:
            file_path = save_master_workbook(all_records, output_path)
        except PermissionError as exc:
            raise ValidationError("Cannot save workbook. Close the Excel file and try again.") from exc

        logger(f"Workbook created: {file_path}")
        return file_path

    def update_ppo_qty_from_workbook(
        self,
        workbook_path: str | Path,
        log: LogFn | None = None,
    ) -> Path:
        logger = log or (lambda message: None)
        path = Path(workbook_path).expanduser().resolve()

        if not path.exists():
            raise ValidationError("Workbook file does not exist.")
        if path.suffix.lower() != ".xlsx":
            raise ValidationError("Workbook file must be .xlsx.")

        logger(f"Preparing PPO update for workbook: {path}")

        try:
            records = read_records_from_workbook(path)
        except WorkbookFormatError:
            raise
        except Exception as exc:
            raise WorkbookFormatError(f"Cannot read workbook metadata: {exc}") from exc

        pure_knit_go_keys = self._pure_knit_go_keys(records)

        unique_ppos = list(
            dict.fromkeys(
                ppo_value
                for record in records
                for ppo_value in self._split_ppo_values(record.ppo_no)
            )
        )
        if not unique_ppos:
            raise ValidationError(
                "Khong tim thay PPO No trong file da luu. Save workbook truoc khi update va nhap PPO No o cot PPO."
            )

        collar_records = [record for record in records if record.sheet_kind == COLLAR_SHEET_NAME]
        cm_records = [record for record in records if record.flow == "CM"]
        woven_records = [record for record in records if record.flow == "WOVEN"]
        knit_records = [record for record in records if record.flow == "KNIT" and record.sheet_kind != COLLAR_SHEET_NAME]
        knit_collar_records = [record for record in collar_records if record.flow == "KNIT"]
        logger(
            f"Found {len(unique_ppos)} unique PPO NO(s) in workbook "
            f"({len(knit_records)} Knit row(s), {len(woven_records)} Woven row(s), "
            f"{len(knit_collar_records)} Collar/Cuff row(s), {len(cm_records)} CM row(s))."
        )

        knit_lookups, knit_stats = self._load_knit_qty_lookups(
            sorted(
                {
                    ppo_value
                    for record in knit_records
                    for ppo_value in self._split_ppo_values(record.ppo_no)
                }
            ),
            logger,
        )
        collar_ppo_lookups, collar_ppo_stats = self._load_knit_collar_cuff_ppo_lookups(
            sorted(
                {
                    ppo_value
                    for record in knit_collar_records
                    for ppo_value in self._split_ppo_values(record.ppo_no)
                }
            ),
            logger,
        )
        cm_lookups, cm_stats = self._load_cm_qty_lookups(
            sorted(
                {
                    ppo_value
                    for record in cm_records
                    for ppo_value in self._split_ppo_values(record.ppo_no)
                }
            ),
            logger,
        )
        woven_qty_lookup, woven_ppo_yy_lookup, woven_part_qty_lookup, woven_stats = self._load_woven_update_lookups(
            sorted(
                {
                    ppo_value
                    for record in (woven_records + knit_records)
                    for ppo_value in self._split_ppo_values(record.ppo_no)
                }
            ),
            logger,
        )

        missing_knit_qty = 0
        missing_collar_qty = 0
        missing_cm_qty = 0
        missing_woven_qty = 0
        missing_woven_yy = 0
        updated_records: list[ExportRecord] = []

        for record in records:
            ppo_candidates = self._split_ppo_values(record.ppo_no)
            if not ppo_candidates:
                updated_records.append(
                    replace(
                        record,
                        ppo_qty="",
                        ppo_yy="" if record.flow == "WOVEN" else record.ppo_yy,
                    )
                )
                continue

            if len(ppo_candidates) > 1:
                matched_records = self._resolve_multi_ppo_matches(
                    record=record,
                    ppo_candidates=ppo_candidates,
                    knit_lookups=knit_lookups,
                    collar_ppo_lookups=collar_ppo_lookups,
                    cm_lookups=cm_lookups,
                    woven_qty_lookup=woven_qty_lookup,
                    woven_ppo_yy_lookup=woven_ppo_yy_lookup,
                    woven_part_qty_lookup=woven_part_qty_lookup,
                    allow_woven_knit_fallback=record.go.upper() in pure_knit_go_keys,
                    logger=logger,
                )
                if matched_records:
                    updated_records.extend(
                        replace(candidate_record, row_index=len(updated_records) + index)
                        for index, candidate_record in enumerate(matched_records)
                    )
                    continue

                normalized_multi_ppo = ", ".join(ppo_candidates)
                if record.flow == "CM":
                    missing_cm_qty += 1
                    logger(
                        f"[{record.go_key}] No CM PPO Q'ty match for PPO list {normalized_multi_ppo} / "
                        f"{record.color_desc} / {record.fabric_part}. "
                        f"Marking PPO cell as '{MULTI_PPO_CHECK_MESSAGE}'."
                    )
                    updated_records.append(replace(record, ppo_no=MULTI_PPO_CHECK_MESSAGE, ppo_qty=""))
                    continue

                if record.sheet_kind == COLLAR_SHEET_NAME and record.flow == "KNIT":
                    missing_collar_qty += 1
                    logger(
                        f"[{record.go_key}] No Collar/Cuff PPO Q'ty match for PPO list {normalized_multi_ppo} / "
                        f"{record.fabric_part} / {record.color_code or record.color_desc} / {record.size}. "
                        f"Marking PPO cell as '{MULTI_PPO_CHECK_MESSAGE}'."
                    )
                    updated_records.append(replace(record, ppo_no=MULTI_PPO_CHECK_MESSAGE, ppo_qty=""))
                    continue

                if record.flow == "WOVEN":
                    missing_woven_qty += 1
                    logger(
                        f"[{record.go_key}] No Woven PPO Q'ty match for PPO list {normalized_multi_ppo} / "
                        f"{record.fabric_part} / {record.combo_name}. "
                        f"Marking PPO cell as '{MULTI_PPO_CHECK_MESSAGE}'."
                    )
                    updated_records.append(replace(record, ppo_no=MULTI_PPO_CHECK_MESSAGE, ppo_qty="", ppo_yy=""))
                    continue

                missing_knit_qty += 1
                logger(
                    f"[{record.go_key}] No Knit PPO Q'ty match for PPO list {normalized_multi_ppo} / "
                    f"{record.fabric_part} / {record.combo_name or record.color_code}. "
                    f"Marking PPO cell as '{MULTI_PPO_CHECK_MESSAGE}'."
                )
                updated_records.append(replace(record, ppo_no=MULTI_PPO_CHECK_MESSAGE, ppo_qty=""))
                continue

            record = replace(record, ppo_no=ppo_candidates[0])
            if record.flow == "CM":
                ppo_qty = self._resolve_cm_ppo_qty(record, cm_lookups, logger)
                if self._is_missing_numeric(ppo_qty):
                    missing_cm_qty += 1
                    logger(
                        f"[{record.go_key}] No CM PPO Q'ty match for PPO {record.ppo_no} / "
                        f"{record.color_desc} / {record.fabric_part}. Keeping row."
                    )
                updated_records.append(replace(record, ppo_qty=ppo_qty))
                continue

            if record.sheet_kind == COLLAR_SHEET_NAME and record.flow == "KNIT":
                ppo_qty = self._resolve_knit_collar_cuff_ppo_qty(record, collar_ppo_lookups, logger)
                if self._is_missing_numeric(ppo_qty):
                    missing_collar_qty += 1
                    logger(
                        f"[{record.go_key}] No Collar/Cuff PPO Q'ty match for PPO {record.ppo_no} / "
                        f"{record.fabric_part} / {record.color_code or record.color_desc} / {record.size}. Keeping row."
                    )
                updated_records.append(replace(record, ppo_qty=ppo_qty))
                continue

            if record.flow == "WOVEN":
                ppo_qty = self._resolve_woven_ppo_qty(record, woven_qty_lookup, woven_part_qty_lookup)
                ppo_yy = self._resolve_woven_ppo_yy(record, woven_ppo_yy_lookup, logger)
                if self._is_missing_numeric(ppo_qty):
                    missing_woven_qty += 1
                    logger(
                        f"[{record.go_key}] No Woven PPO Q'ty match for PPO {record.ppo_no} / "
                        f"{record.fabric_part} / {record.combo_name}. Keeping row."
                    )
                if self._is_missing_numeric(ppo_yy):
                    missing_woven_yy += 1
                    logger(
                        f"[{record.go_key}] No Woven PPO YY match for PPO {record.ppo_no} / "
                        f"{record.fabric_part} / {record.combo_name}. Keeping row."
                    )
                updated_records.append(replace(record, ppo_qty=ppo_qty, ppo_yy=ppo_yy))
                continue

            ppo_qty = self._resolve_knit_ppo_qty(
                record,
                knit_lookups,
                woven_qty_lookup,
                woven_part_qty_lookup,
                allow_woven_fallback=record.go.upper() in pure_knit_go_keys,
                logger=logger,
            )
            if self._is_missing_numeric(ppo_qty):
                missing_knit_qty += 1
                logger(
                    f"[{record.go_key}] No Knit PPO Q'ty match for PPO {record.ppo_no} / "
                    f"{record.fabric_part} / {record.combo_name or record.color_code}. Keeping row."
                )

            updated_records.append(replace(record, ppo_qty=ppo_qty))

        try:
            write_workbook(path, updated_records)
        except PermissionError as exc:
            raise ValidationError("Cannot save workbook. Close the Excel file and try again.") from exc

        logger(
            "Update summary: "
            f"Knit PPO matched {knit_stats['matched']}, "
            f"Knit PPO without DB data {knit_stats['empty']}, "
            f"Collar/Cuff PPO report matched {collar_ppo_stats['matched']}, "
            f"Collar/Cuff PPO report empty {collar_ppo_stats['empty']}, "
            f"CM PPO report matched {cm_stats['matched']}, "
            f"CM PPO report empty {cm_stats['empty']}, "
            f"Woven PPO report matched {woven_stats['report_matched']}, "
            f"Woven PPO report empty {woven_stats['report_empty']}, "
            f"Woven Fabric Lots matched {woven_stats['part_report_matched']}, "
            f"Woven Fabric Lots empty {woven_stats['part_report_empty']}, "
            f"Woven DB matched {woven_stats['db_matched']}, "
            f"Woven DB empty {woven_stats['db_empty']}, "
            f"Knit row(s) without PPO Q'ty {missing_knit_qty}, "
            f"Collar/Cuff row(s) without PPO Q'ty {missing_collar_qty}, "
            f"CM row(s) without PPO Q'ty {missing_cm_qty}, "
            f"Woven row(s) without PPO Q'ty {missing_woven_qty}, "
            f"Woven row(s) without PPO YY {missing_woven_yy}, "
            f"{len(updated_records)} workbook row(s) written."
        )
        logger(f"Workbook updated: {path}")
        return path

    def update_cm_from_workbook(
        self,
        workbook_path: str | Path,
        log: LogFn | None = None,
    ) -> Path:
        logger = log or (lambda message: None)
        path = Path(workbook_path).expanduser().resolve()

        if not path.exists():
            raise ValidationError("Workbook file does not exist.")
        if path.suffix.lower() != ".xlsx":
            raise ValidationError("Workbook file must be .xlsx.")

        logger(f"Preparing CM update for workbook: {path}")

        try:
            records = read_records_from_workbook(path)
        except WorkbookFormatError:
            raise
        except Exception as exc:
            raise WorkbookFormatError(f"Cannot read workbook metadata: {exc}") from exc

        business_rows = [record for record in records if not record.is_separator]
        if not business_rows:
            raise ValidationError("Workbook does not contain any data row.")
        if any(record.flow != "CM" for record in business_rows):
            raise ValidationError("Workbook nay khong phai CM flow.")

        logger(f"CM workbook validated with {len(business_rows)} row(s).")
        return self.update_ppo_qty_from_workbook(path, logger)

    def update_cm_from_go(
        self,
        go_input: str,
        output_dir: str | Path,
        log: LogFn | None = None,
    ) -> Path:
        logger = log or (lambda message: None)
        output_path = Path(output_dir).expanduser()

        if not output_path.exists():
            raise ValidationError("Output directory does not exist.")
        if not output_path.is_dir():
            raise ValidationError("Output path must be a directory.")

        go_numbers, invalid_tokens = split_go_batch(go_input)
        for invalid_token in invalid_tokens:
            logger(f"[{invalid_token}] Invalid GO, skipped.")
        if not go_numbers:
            raise ValidationError("No valid GO found.")

        logger(f"Running CM flow for {len(go_numbers)} GO(s) into {MASTER_FILE_NAME}.")
        records: list[ExportRecord] = []

        for go_no in go_numbers:
            logger(f"[{go_no}] Loading GO report...")
            try:
                go_report = self.go_client.fetch_go_report(go_no)
            except GORequestError as exc:
                logger(f"[{go_no}] GO error: {exc}")
                continue

            if not self._is_cm_go_report(go_report):
                raise ValidationError(
                    f"[{go_no}] Update CM chi dung cho hang CM (khong co YY Req No va khong co PPO trong GO report)."
                )

            cm_records = self._build_cm_records_for_go(go_no, go_report, logger, fill_ppo_qty=True)
            if not cm_records:
                logger(f"[{go_no}] No eligible CM rows found.")
                continue
            records.extend(cm_records)

        if not records:
            raise ValidationError("No valid CM data available for Excel export.")

        try:
            file_path = save_master_workbook(records, output_path)
        except PermissionError as exc:
            raise ValidationError("Cannot save workbook. Close the Excel file and try again.") from exc

        logger(f"CM workbook created: {file_path}")
        return file_path

    def update_yy_req_no_from_workbook(
        self,
        workbook_path: str | Path,
        username: str,
        password: str,
        log: LogFn | None = None,
    ) -> Path:
        logger = log or (lambda message: None)
        path = Path(workbook_path).expanduser().resolve()

        if not path.exists():
            raise ValidationError("Workbook file does not exist.")
        if path.suffix.lower() != ".xlsx":
            raise ValidationError("Workbook file must be .xlsx.")

        logger(f"Preparing YY Req No update for workbook: {path}")

        try:
            records = read_records_from_workbook(path)
        except WorkbookFormatError:
            raise
        except Exception as exc:
            raise WorkbookFormatError(f"Cannot read workbook metadata: {exc}") from exc

        eligible_records = [record for record in records if normalize_space(record.yy_req_no)]
        if not eligible_records:
            raise ValidationError("Khong tim thay YY Req No trong workbook. Nhap YY Req No truoc khi bam Update YY Req No.")

        ypd_client = YPDClient(username=username, password=password)
        updated_by_index = {
            record.row_index: record for record in self._refresh_records_from_manual_yy(records, ypd_client, logger)
        }
        updated_records = [updated_by_index.get(record.row_index, record) for record in records]

        try:
            write_workbook(path, updated_records)
        except PermissionError as exc:
            raise ValidationError("Cannot save workbook. Close the Excel file and try again.") from exc

        logger(f"YY Req No update completed: {path}")
        return path

    def _resolve_multi_ppo_matches(
        self,
        *,
        record: ExportRecord,
        ppo_candidates: list[str],
        knit_lookups: dict[str, dict[tuple[str, ...], str]],
        collar_ppo_lookups: dict[str, dict[tuple[str, str, str, str], str]],
        cm_lookups: dict[tuple[str, str, str], str],
        woven_qty_lookup: dict[tuple[str, str], str],
        woven_ppo_yy_lookup: dict[tuple[str, str, str, str], set[str]],
        woven_part_qty_lookup: dict[tuple[str, str, str], str],
        allow_woven_knit_fallback: bool,
        logger: LogFn,
    ) -> list[ExportRecord]:
        matched_records: list[ExportRecord] = []

        for ppo_candidate in ppo_candidates:
            candidate_record = replace(record, ppo_no=ppo_candidate, ppo_qty="")

            if candidate_record.flow == "CM":
                ppo_qty = self._resolve_cm_ppo_qty(candidate_record, cm_lookups, logger)
                if self._is_missing_numeric(ppo_qty):
                    continue
                matched_records.append(replace(candidate_record, ppo_qty=ppo_qty))
                continue

            if candidate_record.sheet_kind == COLLAR_SHEET_NAME and candidate_record.flow == "KNIT":
                ppo_qty = self._resolve_knit_collar_cuff_ppo_qty(candidate_record, collar_ppo_lookups, logger)
                if self._is_missing_numeric(ppo_qty):
                    continue
                matched_records.append(replace(candidate_record, ppo_qty=ppo_qty))
                continue

            if candidate_record.flow == "WOVEN":
                ppo_qty = self._resolve_woven_ppo_qty(candidate_record, woven_qty_lookup, woven_part_qty_lookup)
                if self._is_missing_numeric(ppo_qty):
                    continue
                ppo_yy = self._resolve_woven_ppo_yy(candidate_record, woven_ppo_yy_lookup, logger)
                matched_records.append(replace(candidate_record, ppo_qty=ppo_qty, ppo_yy=ppo_yy))
                continue

            ppo_qty = self._resolve_knit_ppo_qty(
                candidate_record,
                knit_lookups,
                woven_qty_lookup,
                woven_part_qty_lookup,
                allow_woven_fallback=allow_woven_knit_fallback,
                logger=lambda _message: None,
            )
            if self._is_missing_numeric(ppo_qty):
                continue
            matched_records.append(replace(candidate_record, ppo_qty=ppo_qty))

        if len(matched_records) > 1:
            logger(
                f"[{record.go_key}] Expanded PPO list {', '.join(ppo_candidates)} into "
                f"{len(matched_records)} matched row(s) for {record.fabric_part} / "
                f"{record.color_code or record.color_desc} / {record.size or record.combo_name}."
            )

        return matched_records

    @staticmethod
    def _split_ppo_values(value: str) -> list[str]:
        normalized_value = normalize_lookup_text(value)
        if normalized_value == normalize_lookup_text(MULTI_PPO_CHECK_MESSAGE):
            return []
        tokens = re.findall(r"\bP[A-Z0-9-]{5,}\b", (value or "").upper())
        ordered_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if normalize_lookup_text(token) == normalize_lookup_text(MULTI_PPO_CHECK_MESSAGE):
                continue
            if token in seen:
                continue
            seen.add(token)
            ordered_tokens.append(token)
        return ordered_tokens

    def _build_resolved_bom_rows(
        self,
        go_no: str,
        go_report: GOReportData,
        ypd_client: YPDClient,
        logger: LogFn,
    ) -> list[ResolvedBomRow]:
        resolved_rows: list[ResolvedBomRow] = []
        ordered_blocks = self._ordered_bom_blocks(go_report)
        if not ordered_blocks:
            logger(f"[{go_no}] No BOM block found in GO report.")
            return resolved_rows

        logger(f"[{go_no}] {len(ordered_blocks)} BOM block(s) loaded.")

        for section_order, block in enumerate(ordered_blocks):
            logger(f"[{go_no}] Processing {block.flow} block {block.block_index + 1} ({len(block.bom_rows)} row(s))...")
            block_requests = self._block_requests(block)
            marker_rows_by_request: dict[str, list[MarkerRow]] = {}

            for yy_request in block_requests:
                logger(f"[{go_no}] Loading {block.flow} YPD {yy_request.raw_value}...")
                try:
                    marker_rows = self._fetch_best_marker_rows(go_no, block, yy_request, ypd_client, logger)
                except YPDAuthenticationError:
                    raise
                except YPDRequestError as exc:
                    logger(f"[{go_no}] YPD error for {yy_request.raw_value}: {exc}")
                    continue

                if not marker_rows:
                    logger(f"[{go_no}] {yy_request.raw_value}: no Marker YY rows.")
                    continue
                marker_rows_by_request[yy_request.raw_value] = marker_rows

            kept_in_block = 0
            for bom_row in block.bom_rows:
                if bom_row.yy_req_no:
                    try:
                        yy_request = parse_yy_request(bom_row.yy_req_no)
                    except Exception:
                        logger(
                            f"[{go_no}] Skipped {block.flow} {bom_row.gmt_part} / {bom_row.gmt_color_code}: "
                            f"invalid YY Req No {bom_row.yy_req_no}."
                        )
                        continue

                    marker_rows = marker_rows_by_request.get(yy_request.raw_value, [])
                    marker_row = self._match_marker_row(go_no, yy_request.raw_value, bom_row, marker_rows, logger)
                    if marker_row is None:
                        continue

                    candidate = ResolvedBomRow(
                        flow=block.flow,
                        yy_req_no=yy_request.raw_value,
                        marker_yy=marker_row.marker_yy,
                        ppo_yy=bom_row.ppo_yy,
                        gmt_color=bom_row.gmt_color or marker_row.gmt_color,
                        fabric_part=bom_row.gmt_part or marker_row.fabric_part,
                        combo_name=bom_row.combo_name,
                        color_code=bom_row.gmt_color_code or marker_row.gmt_color_code,
                        block_index=block.block_index,
                        section_order=section_order,
                        part_order=bom_row.table_order,
                    )
                else:
                    candidate = ResolvedBomRow(
                        flow=block.flow,
                        yy_req_no="",
                        marker_yy="",
                        ppo_yy=bom_row.ppo_yy,
                        gmt_color=bom_row.gmt_color,
                        fabric_part=bom_row.gmt_part,
                        combo_name=bom_row.combo_name,
                        color_code=bom_row.gmt_color_code,
                        block_index=block.block_index,
                        section_order=section_order,
                        part_order=bom_row.table_order,
                    )

                if not self._is_create_complete(candidate):
                    logger(
                        f"[{go_no}] Skipped {block.flow} {candidate.fabric_part} / {candidate.color_code}: "
                        "incomplete create data."
                    )
                    continue

                resolved_rows.append(candidate)
                kept_in_block += 1

            logger(f"[{go_no}] {block.flow} block {block.block_index + 1}: kept {kept_in_block} eligible row(s).")

        return resolved_rows

    def _refresh_records_from_manual_yy(
        self,
        records: list[ExportRecord],
        ypd_client: YPDClient,
        logger: LogFn,
    ) -> list[ExportRecord]:
        updated_records = list(records)
        grouped_rows: dict[tuple[str, str, int, str], list[tuple[int, ExportRecord]]] = {}

        for index, record in enumerate(records):
            yy_value = normalize_space(record.yy_req_no)
            if not yy_value:
                continue
            group_key = (record.go_key.upper(), record.flow.upper(), record.block_index, yy_value.upper())
            grouped_rows.setdefault(group_key, []).append((index, record))

        if not grouped_rows:
            return updated_records

        logger(f"Found {len(grouped_rows)} YY Req No group(s) to refresh from workbook.")
        for (go_no, flow, block_index, yy_text), indexed_records in grouped_rows.items():
            sample_record = indexed_records[0][1]
            synthetic_rows = [
                GOBomRow(
                    yy_req_no=record.yy_req_no,
                    ppo_yy=record.ppo_yy,
                    gmt_color_code=record.color_code,
                    gmt_part=record.fabric_part,
                    combo_name=record.combo_name,
                    gmt_color=record.gmt_color or record.color_desc,
                    flow=record.flow,
                    block_index=record.block_index,
                    table_order=record.part_order,
                )
                for _, record in indexed_records
            ]
            synthetic_block = GOBomBlock(
                flow=flow,
                section_title=f"{flow.title()} Workbook Update",
                block_index=block_index,
                bom_rows=synthetic_rows,
            )
            try:
                yy_request = parse_yy_request(sample_record.yy_req_no)
            except Exception:
                logger(f"[{go_no}] Invalid YY Req No in workbook: {sample_record.yy_req_no}. Keeping existing values.")
                continue

            logger(f"[{go_no}] Refreshing {flow} YY Req No {yy_request.raw_value} for {len(indexed_records)} workbook row(s)...")
            try:
                marker_rows = self._fetch_best_marker_rows(go_no, synthetic_block, yy_request, ypd_client, logger)
            except YPDAuthenticationError:
                raise
            except YPDRequestError as exc:
                logger(f"[{go_no}] YPD error for workbook YY Req No {yy_request.raw_value}: {exc}")
                continue

            if not marker_rows:
                logger(f"[{go_no}] {yy_request.raw_value}: no Marker YY rows for workbook update.")
                continue

            for record_index, record in indexed_records:
                marker_row = self._match_marker_row(
                    go_no,
                    yy_request.raw_value,
                    GOBomRow(
                        yy_req_no=yy_request.raw_value,
                        ppo_yy=record.ppo_yy,
                        gmt_color_code=record.color_code,
                        gmt_part=record.fabric_part,
                        combo_name=record.combo_name,
                        gmt_color=record.gmt_color or record.color_desc,
                        flow=record.flow,
                        block_index=record.block_index,
                        table_order=record.part_order,
                    ),
                    marker_rows,
                    logger,
                )
                if marker_row is None:
                    continue

                updated_records[record_index] = replace(
                    record,
                    yy_req_no=yy_request.raw_value,
                    marker_yy=marker_row.marker_yy or record.marker_yy,
                    gmt_color=record.gmt_color or marker_row.gmt_color,
                )

        return updated_records

    def _build_export_records_for_go(
        self,
        go_no: str,
        mes_rows: list[MESJoRow],
        webmerge_aggregates: list[WebmergeColorSizeAggregate],
        bom_rows: list[ResolvedBomRow],
        logger: LogFn,
    ) -> list[ExportRecord]:
        regular_bom_rows = [bom_row for bom_row in bom_rows if not self._is_collar_cuff_part(bom_row.fabric_part)]
        collar_cuff_bom_rows = [bom_row for bom_row in bom_rows if self._is_collar_cuff_part(bom_row.fabric_part)]

        records = self._build_standard_export_records_for_go(go_no, mes_rows, regular_bom_rows, logger)
        collar_cuff_records = self._build_collar_cuff_records_for_go(
            go_no=go_no,
            mes_rows=mes_rows,
            webmerge_aggregates=webmerge_aggregates,
            bom_rows=collar_cuff_bom_rows,
            logger=logger,
        )
        records.extend(collar_cuff_records)

        logger(
            f"[{go_no}] {len([item for item in records if item.sheet_kind == 'COI'])} COI row(s) prepared, "
            f"{len([item for item in records if item.sheet_kind == COLLAR_SHEET_NAME])} COI Collar/Cuff row(s) prepared."
        )
        return records

    def _build_cm_records_for_go(
        self,
        go_no: str,
        go_report: GOReportData,
        logger: LogFn,
        fill_ppo_qty: bool = False,
    ) -> list[ExportRecord]:
        color_summary_lookup = {
            normalize_lookup_text(row.color_desc): row
            for row in go_report.color_summary_rows
            if normalize_space(row.color_desc)
        }
        if not color_summary_lookup:
            logger(f"[{go_no}] No Color Summary rows found for CM GO.")
            return []

        jo_lookup = self._build_cm_color_jo_lookup(go_report)
        if jo_lookup:
            logger(f"[{go_no}] CM JO mapping prepared for {len(jo_lookup)} color key(s).")
        else:
            logger(f"[{go_no}] No CM JO mapping derived from Lot Information / Color Breakdown.")

        logger(f"[{go_no}] Loading CM QA Color Shading Matching by GO as PPONO...")
        try:
            cm_rows = self.cm_client.fetch_aggregate_rows(go_no)
        except MESRequestError as exc:
            logger(f"[{go_no}] CM QA warning: {exc}")
            return []

        if not cm_rows:
            logger(f"[{go_no}] No CM QA rows found for GO-as-PPONO.")
            return []

        records: list[ExportRecord] = []
        warned_missing_colors: set[str] = set()
        for part_order, cm_row in enumerate(cm_rows):
            summary_row = color_summary_lookup.get(normalize_lookup_text(cm_row.combo_name))
            if summary_row is None:
                combo_key = normalize_lookup_text(cm_row.combo_name)
                if combo_key not in warned_missing_colors:
                    warned_missing_colors.add(combo_key)
                    logger(f"[{go_no}] No Color Summary match for CM combo {cm_row.combo_name}.")
                continue

            jo_values = self._resolve_cm_jo_list(summary_row, jo_lookup)
            if not jo_values:
                jo_values = [""]

            for jo_value in jo_values:
                records.append(
                    ExportRecord(
                        go=go_no,
                        yy_req_no="",
                        marker_yy="",
                        ppo_yy="",
                        gmt_color=summary_row.color_desc,
                        fabric_part=cm_row.usage,
                        color_code=summary_row.color_code,
                        color_desc=summary_row.color_desc,
                        fabric_color="",
                        jo=jo_value,
                        minus_pct="",
                        plus_pct="",
                        qty=summary_row.total_quantity,
                        ppo_no="",
                        ppo_qty=cm_row.received_qty if fill_ppo_qty else "",
                        go_key=go_no,
                        row_index=len(records),
                        flow="CM",
                        combo_name=summary_row.color_desc,
                        block_index=0,
                        section_order=0,
                        part_order=part_order,
                        aggregate_key=self._build_aggregate_key(
                            go_no=go_no,
                            flow="CM",
                            fabric_part=cm_row.usage,
                            color_code=summary_row.color_code,
                            combo_name=summary_row.color_desc,
                        ),
                    )
                )

        logger(f"[{go_no}] CM create prepared {len(records)} row(s).")
        return records

    def _build_standard_export_records_for_go(
        self,
        go_no: str,
        mes_rows: list[MESJoRow],
        bom_rows: list[ResolvedBomRow],
        logger: LogFn,
    ) -> list[ExportRecord]:
        records: list[ExportRecord] = []
        mes_by_color: dict[str, list[MESJoRow]] = {}
        mes_by_color_desc: dict[str, list[MESJoRow]] = {}
        for mes_row in mes_rows:
            mes_by_color.setdefault(mes_row.color_code.upper(), []).append(mes_row)
            mes_by_color_desc.setdefault(normalize_lookup_text(mes_row.color_name), []).append(mes_row)

        warned_missing_colors: set[tuple[str, str]] = set()

        for bom_row in bom_rows:
            matching_mes_rows = mes_by_color.get(bom_row.color_code.upper(), []) if bom_row.color_code else []
            if not matching_mes_rows and bom_row.gmt_color:
                matching_mes_rows = mes_by_color_desc.get(normalize_lookup_text(bom_row.gmt_color), [])
            if not matching_mes_rows:
                warning_key = (bom_row.flow, bom_row.color_code or normalize_lookup_text(bom_row.gmt_color))
                if warning_key not in warned_missing_colors:
                    warned_missing_colors.add(warning_key)
                    logger(
                        f"[{go_no}] No JO rows found for {bom_row.flow} COLOR_CODE "
                        f"{bom_row.color_code or bom_row.gmt_color} / "
                        f"{bom_row.fabric_part}."
                    )
                continue

            for mes_row in matching_mes_rows:
                records.append(
                    ExportRecord(
                        go=go_no,
                        yy_req_no=bom_row.yy_req_no,
                        marker_yy=bom_row.marker_yy,
                        ppo_yy=bom_row.ppo_yy if bom_row.flow == "KNIT" or not bom_row.yy_req_no else "",
                        gmt_color=bom_row.gmt_color,
                        fabric_part=bom_row.fabric_part,
                        color_code=mes_row.color_code,
                        color_desc=mes_row.color_name,
                        fabric_color=mes_row.fabric_color,
                        jo=mes_row.jo_no,
                        minus_pct=mes_row.minus_pct,
                        plus_pct=mes_row.plus_pct,
                        qty=mes_row.order_qty,
                        ppo_no="",
                        ppo_qty="",
                        go_key=go_no,
                        row_index=len(records),
                        flow=bom_row.flow,
                        combo_name=bom_row.combo_name,
                        block_index=bom_row.block_index,
                        section_order=bom_row.section_order,
                        part_order=bom_row.part_order,
                        aggregate_key=self._build_aggregate_key(
                            go_no=go_no,
                            flow=bom_row.flow,
                            fabric_part=bom_row.fabric_part,
                            color_code=mes_row.color_code,
                            combo_name=bom_row.combo_name,
                        ),
                    )
                )

        return records

    def _build_collar_cuff_records_for_go(
        self,
        *,
        go_no: str,
        mes_rows: list[MESJoRow],
        webmerge_aggregates: list[WebmergeColorSizeAggregate],
        bom_rows: list[ResolvedBomRow],
        logger: LogFn,
    ) -> list[ExportRecord]:
        if not bom_rows:
            return []

        webmerge_by_color, webmerge_by_desc = self._build_webmerge_aggregate_lookup(webmerge_aggregates)

        records: list[ExportRecord] = []
        for bom_row in bom_rows:
            matching_size_rows = webmerge_by_color.get(bom_row.color_code.upper(), []) if bom_row.color_code else []
            if not matching_size_rows and bom_row.gmt_color:
                matching_size_rows = webmerge_by_desc.get(normalize_lookup_text(bom_row.gmt_color), [])

            if matching_size_rows:
                for size_row in matching_size_rows:
                    records.append(
                        self._build_collar_cuff_record(
                            go_no=go_no,
                            bom_row=bom_row,
                            color_code=size_row.color_code,
                            color_desc=size_row.color_desc,
                            qty=size_row.qty,
                            size=size_row.size,
                            row_index=len(records),
                        )
                    )
                continue

            logger(
                f"[{go_no}] No Webmerge qty match for {bom_row.flow} "
                f"{bom_row.fabric_part} / {bom_row.color_code or bom_row.gmt_color}. Keeping blank Size row."
            )

            records.append(
                self._build_collar_cuff_record(
                    go_no=go_no,
                    bom_row=bom_row,
                    color_code=bom_row.color_code,
                    color_desc=bom_row.gmt_color,
                    qty="",
                    size="",
                    row_index=len(records),
                )
            )

        return records

    def _build_collar_cuff_record(
        self,
        *,
        go_no: str,
        bom_row: ResolvedBomRow,
        color_code: str,
        color_desc: str,
        qty: str,
        size: str,
        row_index: int,
    ) -> ExportRecord:
        return ExportRecord(
            go=go_no,
            yy_req_no=bom_row.yy_req_no,
            marker_yy=bom_row.marker_yy,
            ppo_yy=bom_row.ppo_yy if bom_row.flow == "KNIT" or not bom_row.yy_req_no else "",
            gmt_color=bom_row.gmt_color,
            fabric_part=bom_row.fabric_part,
            color_code=color_code.upper(),
            color_desc=color_desc,
            fabric_color="",
            jo="",
            minus_pct="",
            plus_pct="",
            qty=qty,
            ppo_no="",
            ppo_qty="",
            go_key=go_no,
            row_index=row_index,
            flow=bom_row.flow,
            combo_name=bom_row.combo_name,
            block_index=bom_row.block_index,
            section_order=bom_row.section_order,
            part_order=bom_row.part_order,
            aggregate_key=self._build_aggregate_key(
                go_no=go_no,
                flow=bom_row.flow,
                fabric_part=bom_row.fabric_part,
                color_code=color_code,
                combo_name=bom_row.combo_name,
                size=size,
                sheet_kind=COLLAR_SHEET_NAME,
            ),
            sheet_kind=COLLAR_SHEET_NAME,
            size=size,
        )

    def _build_mes_rows(self, go_no: str, logger: LogFn) -> list[MESJoRow]:
        logger(f"[{go_no}] Loading MES JO / Qty...")
        try:
            mes_rows = self.mes_client.fetch_jo_rows(go_no)
        except MESRequestError as exc:
            logger(f"[{go_no}] MES warning: {exc}")
            return []

        if not mes_rows:
            logger(f"[{go_no}] No JO rows found in MES report.")
            return []

        logger(f"[{go_no}] {len(mes_rows)} JO row(s) loaded.")
        return mes_rows

    def _build_webmerge_go_aggregates(self, go_no: str, logger: LogFn) -> list[WebmergeColorSizeAggregate]:
        logger(f"[{go_no}] Loading Webmerge garment Color/Size Breakdown...")
        try:
            webmerge_aggregates = self.webmerge_client.fetch_go_color_size_aggregates(go_no)
        except MESRequestError as exc:
            logger(f"[{go_no}] Webmerge warning: {exc}")
            return []

        if not webmerge_aggregates:
            logger(f"[{go_no}] No Webmerge garment Color/Size row found.")
            return []

        logger(f"[{go_no}] {len(webmerge_aggregates)} Webmerge garment Color/Size row(s) loaded.")
        return webmerge_aggregates

    @staticmethod
    def _build_webmerge_aggregate_lookup(
        webmerge_aggregates: list[WebmergeColorSizeAggregate],
    ) -> tuple[dict[str, list[WebmergeColorSizeAggregate]], dict[str, list[WebmergeColorSizeAggregate]]]:
        by_color: dict[str, list[WebmergeColorSizeAggregate]] = {}
        by_desc: dict[str, list[WebmergeColorSizeAggregate]] = {}
        for row in webmerge_aggregates:
            by_color.setdefault(row.color_code.upper(), []).append(row)
            by_desc.setdefault(normalize_lookup_text(row.color_desc), []).append(row)

        for lookup in (by_color, by_desc):
            for key in list(lookup):
                lookup[key] = sorted(
                    lookup[key],
                    key=lambda item: (item.color_code, GetYYService._size_sort_key(item.size)),
                )

        return by_color, by_desc

    def _load_collar_cuff_qty_lookups(
        self,
        go_numbers: list[str],
        logger: LogFn,
    ) -> tuple[dict[str, dict[str, dict[str, list[WebmergeColorSizeAggregate]]]], dict[str, int]]:
        go_lookup: dict[str, dict[str, dict[str, list[WebmergeColorSizeAggregate]]]] = {}
        matched = 0
        empty = 0

        for go_no in go_numbers:
            if not go_no:
                continue
            webmerge_aggregates = self._build_webmerge_go_aggregates(go_no, logger)
            if not webmerge_aggregates:
                empty += 1
                go_lookup[go_no.upper()] = {"by_color": {}, "by_desc": {}}
                continue

            matched += 1
            by_color, by_desc = self._build_webmerge_aggregate_lookup(webmerge_aggregates)
            go_lookup[go_no.upper()] = {"by_color": by_color, "by_desc": by_desc}

        return go_lookup, {"matched": matched, "empty": empty}

    def _load_cm_qty_lookups(
        self,
        ppo_numbers: list[str],
        logger: LogFn,
    ) -> tuple[dict[tuple[str, str, str], str], dict[str, int]]:
        lookup: dict[tuple[str, str, str], str] = {}
        matched = 0
        empty = 0

        for ppo_no in ppo_numbers:
            logger(f"Loading CM QA report {ppo_no}...")
            try:
                rows = self.cm_client.fetch_aggregate_rows(ppo_no)
            except MESRequestError as exc:
                logger(str(exc))
                rows = []

            if not rows:
                empty += 1
                logger(f"No CM QA rows found for PPO NO {ppo_no}.")
                continue

            matched += 1
            logger(f"CM PPO NO {ppo_no}: {len(rows)} combo/usage aggregate row(s) loaded.")
            for row in rows:
                key = (
                    row.ppo_no.upper(),
                    normalize_lookup_text(row.combo_name),
                    normalize_lookup_text(row.usage),
                )
                current_qty = Decimal(normalize_number(lookup.get(key, "0")) or "0")
                row_qty = Decimal(normalize_number(row.received_qty) or "0")
                lookup[key] = normalize_number(str(current_qty + row_qty))

        return lookup, {"matched": matched, "empty": empty}

    def _load_knit_qty_lookups(
        self,
        ppo_numbers: list[str],
        logger: LogFn,
    ) -> tuple[dict[str, dict[tuple[str, ...], str]], dict[str, int]]:
        by_combo_code: dict[tuple[str, str, str], str] = {}
        by_combo_name: dict[tuple[str, str, str], str] = {}
        by_combo_name_any_part: dict[tuple[str, str], str] = {}
        legacy_color: dict[tuple[str, str, str], str] = {}
        matched_ppo_count = 0
        empty_ppo_count = 0

        for ppo_no in ppo_numbers:
            logger(f"Querying Knit PPO NO {ppo_no}...")
            try:
                ppo_client = self._get_ppo_client()
                part_rows = ppo_client.fetch_knit_part_aggregates(ppo_no)
                color_rows = ppo_client.fetch_color_aggregates(ppo_no)
            except DatabaseQueryError:
                raise

            if not part_rows and not color_rows:
                logger(f"No Knit DB rows found for PPO NO {ppo_no}.")
                empty_ppo_count += 1
                continue

            matched_ppo_count += 1
            logger(
                f"Knit PPO NO {ppo_no}: "
                f"{len(part_rows)} part-aware row(s), {len(color_rows)} legacy color row(s) loaded."
            )

            combo_name_candidates: dict[str, set[str]] = {}

            for row in part_rows:
                fabric_part_key = normalize_lookup_text(row.fabric_part)
                combo_code_key = normalize_lookup_text(row.combo_code)
                combo_name_key = normalize_lookup_text(row.combo_name)
                if combo_code_key:
                    by_combo_code[(row.ppo_no.upper(), fabric_part_key, combo_code_key)] = row.ppo_qty
                if combo_name_key:
                    by_combo_name[(row.ppo_no.upper(), fabric_part_key, combo_name_key)] = row.ppo_qty
                    combo_name_candidates.setdefault(combo_name_key, set()).add(normalize_number(row.ppo_qty))

            for combo_name_key, qty_values in combo_name_candidates.items():
                normalized_qty_values = {value for value in qty_values if value not in {"", "0"}}
                if len(normalized_qty_values) == 1:
                    by_combo_name_any_part[(ppo_no.upper(), combo_name_key)] = next(iter(normalized_qty_values))

            for row in color_rows:
                if not row.fabric_type_code.upper().startswith("B"):
                    continue
                legacy_color[(row.ppo_no.upper(), row.fabric_type_code.upper(), row.color_code.upper())] = row.ppo_qty

        return {
            "by_combo_code": by_combo_code,
            "by_combo_name": by_combo_name,
            "by_combo_name_any_part": by_combo_name_any_part,
            "legacy_color": legacy_color,
        }, {"matched": matched_ppo_count, "empty": empty_ppo_count}

    def _load_knit_collar_cuff_ppo_lookups(
        self,
        ppo_numbers: list[str],
        logger: LogFn,
    ) -> tuple[dict[str, dict[tuple[str, str, str, str], str]], dict[str, int]]:
        by_color: dict[tuple[str, str, str, str], str] = {}
        by_desc: dict[tuple[str, str, str, str], str] = {}
        matched = 0
        empty = 0

        for ppo_no in ppo_numbers:
            logger(f"Loading Collar/Cuff PPO bulk report {ppo_no}...")
            try:
                rows = self.ppo_report_client.fetch_knit_collar_cuff_aggregates(ppo_no)
            except PPORequestError as exc:
                logger(str(exc))
                rows = []

            if not rows:
                empty += 1
                logger(f"No Collar/Cuff PPO size row found for PPO NO {ppo_no}.")
                continue

            matched += 1
            logger(f"Collar/Cuff PPO NO {ppo_no}: {len(rows)} size aggregate row(s) loaded.")
            for row in rows:
                part_key = normalize_lookup_text(row.fabric_part)
                size_key = row.size.upper()
                color_code_key = row.color_code.upper()
                color_desc_key = normalize_lookup_text(row.color_desc)
                if color_code_key and size_key:
                    by_color[(row.ppo_no.upper(), part_key, color_code_key, size_key)] = row.ppo_qty
                if color_desc_key and size_key:
                    by_desc[(row.ppo_no.upper(), part_key, color_desc_key, size_key)] = row.ppo_qty

        return {"by_color": by_color, "by_desc": by_desc}, {"matched": matched, "empty": empty}

    def _load_woven_update_lookups(
        self,
        ppo_numbers: list[str],
        logger: LogFn,
    ) -> tuple[
        dict[tuple[str, str], str],
        dict[tuple[str, str, str, str], set[str]],
        dict[tuple[str, str, str], str],
        dict[str, int],
    ]:
        qty_lookup: dict[tuple[str, str], str] = {}
        ppo_yy_lookup: dict[tuple[str, str, str, str], set[str]] = {}
        part_qty_lookup: dict[tuple[str, str, str], str] = {}
        report_matched = 0
        report_empty = 0
        part_report_matched = 0
        part_report_empty = 0
        db_matched = 0
        db_empty = 0

        for ppo_no in ppo_numbers:
            logger(f"Loading Woven PPO report {ppo_no}...")
            try:
                report_rows = self.ppo_report_client.fetch_woven_ppo_yy_rows(ppo_no)
            except PPORequestError as exc:
                logger(str(exc))
                report_rows = []

            if report_rows:
                report_matched += 1
                logger(f"Woven PPO NO {ppo_no}: {len(report_rows)} PPO YY row(s) loaded.")
                for row in report_rows:
                    key = (
                        row.ppo_no.upper(),
                        normalize_lookup_text(row.yy_req_no),
                        normalize_lookup_text(row.fabric_combo),
                        normalize_lookup_text(row.fabric_part),
                    )
                    ppo_yy_lookup.setdefault(key, set()).add(row.ppo_yy)
            else:
                report_empty += 1
                logger(f"No Woven PPO YY rows found for PPO NO {ppo_no}.")

            logger(f"Loading Woven Fabric Lots {ppo_no}...")
            try:
                part_rows = self.ppo_report_client.fetch_woven_part_qty_rows(ppo_no)
            except PPORequestError as exc:
                logger(str(exc))
                part_rows = []

            if part_rows:
                part_report_matched += 1
                logger(f"Woven PPO NO {ppo_no}: {len(part_rows)} Fabric Lots row(s) loaded.")
                for row in part_rows:
                    key = (
                        row.ppo_no.upper(),
                        normalize_lookup_text(row.fabric_part),
                        normalize_lookup_text(row.fabric_combo),
                    )
                    current_qty = Decimal(normalize_number(part_qty_lookup.get(key, "0")) or "0")
                    row_qty = Decimal(normalize_number(row.ppo_qty) or "0")
                    part_qty_lookup[key] = normalize_number(str(current_qty + row_qty))
            else:
                part_report_empty += 1
                logger(f"No Woven Fabric Lots rows found for PPO NO {ppo_no}.")

            logger(f"Querying Woven PPO NO {ppo_no}...")
            try:
                aggregate_rows = self._get_ppo_client().fetch_woven_combo_aggregates(ppo_no)
            except DatabaseQueryError:
                raise

            if not aggregate_rows:
                db_empty += 1
                logger(f"No Woven DB rows found for PPO NO {ppo_no}.")
                continue

            db_matched += 1
            logger(f"Woven PPO NO {ppo_no}: {len(aggregate_rows)} combo aggregate row(s) loaded.")
            for row in aggregate_rows:
                key = (row.ppo_no.upper(), normalize_lookup_text(row.combo_name))
                current_qty = Decimal(normalize_number(qty_lookup.get(key, "0")) or "0")
                row_qty = Decimal(normalize_number(row.ppo_qty) or "0")
                qty_lookup[key] = normalize_number(str(current_qty + row_qty))

        return qty_lookup, ppo_yy_lookup, part_qty_lookup, {
            "report_matched": report_matched,
            "report_empty": report_empty,
            "part_report_matched": part_report_matched,
            "part_report_empty": part_report_empty,
            "db_matched": db_matched,
            "db_empty": db_empty,
        }

    def _resolve_woven_ppo_yy(
        self,
        record: ExportRecord,
        ppo_yy_lookup: dict[tuple[str, str, str, str], set[str]],
        logger: LogFn,
    ) -> str:
        combo_key = normalize_lookup_text(record.combo_name)
        part_key = normalize_lookup_text(record.fabric_part)

        if record.yy_req_no:
            key = (
                record.ppo_no,
                normalize_lookup_text(record.yy_req_no),
                combo_key,
                part_key,
            )
            exact_matches = ppo_yy_lookup.get(key, set())
            if len(exact_matches) == 1:
                return next(iter(exact_matches))
            if len(exact_matches) > 1:
                logger(
                    f"[{record.go_key}] Multiple Woven PPO YY matches for PPO NO {record.ppo_no} / "
                    f"YY Req No {record.yy_req_no} / Combo {record.combo_name} / Fabric Part {record.fabric_part}."
                )
                return ""

        fallback_matches = {
            value
            for (ppo_no, _yy_req_no, combo_name, fabric_part), values in ppo_yy_lookup.items()
            if ppo_no == record.ppo_no and combo_name == combo_key and fabric_part == part_key
            for value in values
        }
        if len(fallback_matches) == 1:
            return next(iter(fallback_matches))
        if len(fallback_matches) > 1:
            logger(
                f"[{record.go_key}] Multiple Woven PPO YY fallback matches for PPO NO {record.ppo_no} / "
                f"Combo {record.combo_name} / Fabric Part {record.fabric_part}."
            )
            return ""

        logger(
            f"[{record.go_key}] No Woven PPO YY match for PPO NO {record.ppo_no} / "
            f"YY Req No {record.yy_req_no} / Combo {record.combo_name} / Fabric Part {record.fabric_part}."
        )
        return ""

    def _lookup_knit_ppo_qty(
        self,
        record: ExportRecord,
        lookups: dict[str, dict[tuple[str, ...], str]],
    ) -> str:
        fabric_part_key = normalize_lookup_text(record.fabric_part)
        combo_key = normalize_lookup_text(record.combo_name)
        if fabric_part_key and combo_key:
            combo_code_match = lookups["by_combo_code"].get((record.ppo_no, fabric_part_key, combo_key), "")
            if combo_code_match:
                return combo_code_match

            combo_name_match = lookups["by_combo_name"].get((record.ppo_no, fabric_part_key, combo_key), "")
            if combo_name_match:
                return combo_name_match

            combo_name_any_part_match = lookups["by_combo_name_any_part"].get((record.ppo_no, combo_key), "")
            if combo_name_any_part_match:
                return combo_name_any_part_match

        if not combo_key:
            legacy_match = lookups["legacy_color"].get((record.ppo_no, "B", record.color_code.upper()), "")
            if legacy_match:
                return legacy_match

        return ""

    def _resolve_woven_ppo_qty(
        self,
        record: ExportRecord,
        woven_qty_lookup: dict[tuple[str, str], str],
        woven_part_qty_lookup: dict[tuple[str, str, str], str],
    ) -> str:
        part_qty = self._lookup_woven_knit_fallback_qty(record, woven_qty_lookup, woven_part_qty_lookup)
        if part_qty:
            return part_qty

        combo_key = normalize_lookup_text(record.combo_name)
        if combo_key:
            return woven_qty_lookup.get((record.ppo_no, combo_key), "")
        return ""

    def _lookup_woven_knit_fallback_qty(
        self,
        record: ExportRecord,
        woven_qty_lookup: dict[tuple[str, str], str],
        woven_part_qty_lookup: dict[tuple[str, str, str], str],
    ) -> str:
        combo_key = normalize_lookup_text(record.combo_name)
        part_key = normalize_lookup_text(record.fabric_part)
        has_any_part_rows = any(ppo_no == record.ppo_no for ppo_no, _fabric_part, _combo_name in woven_part_qty_lookup)

        if part_key and combo_key:
            part_exact_match = woven_part_qty_lookup.get((record.ppo_no, part_key, combo_key), "")
            if part_exact_match:
                return part_exact_match

        combo_tokens = self._combo_match_tokens(record.combo_name)
        if part_key and combo_tokens:
            part_candidates = [
                (candidate_combo, qty)
                for (ppo_no, fabric_part, candidate_combo), qty in woven_part_qty_lookup.items()
                if ppo_no == record.ppo_no and fabric_part == part_key
            ]
            part_fuzzy_match = self._resolve_woven_fuzzy_combo_qty(part_candidates, combo_tokens)
            if part_fuzzy_match:
                return part_fuzzy_match

        if has_any_part_rows:
            return ""

        if combo_key:
            global_exact_match = woven_qty_lookup.get((record.ppo_no, combo_key), "")
            if global_exact_match:
                return global_exact_match

        if combo_tokens:
            global_candidates = [
                (candidate_combo, qty)
                for (ppo_no, candidate_combo), qty in woven_qty_lookup.items()
                if ppo_no == record.ppo_no
            ]
            return self._resolve_woven_fuzzy_combo_qty(global_candidates, combo_tokens)

        return ""

    def _resolve_knit_ppo_qty(
        self,
        record: ExportRecord,
        lookups: dict[str, dict[tuple[str, ...], str]],
        woven_qty_lookup: dict[tuple[str, str], str],
        woven_part_qty_lookup: dict[tuple[str, str, str], str],
        allow_woven_fallback: bool,
        logger: LogFn,
    ) -> str:
        knit_qty = self._lookup_knit_ppo_qty(record, lookups)
        woven_qty = ""
        if allow_woven_fallback:
            woven_qty = self._lookup_woven_knit_fallback_qty(record, woven_qty_lookup, woven_part_qty_lookup)

        if knit_qty and woven_qty:
            if knit_qty == woven_qty:
                logger(
                    f"[{record.go_key}] Knit and Woven PPO Q'ty both matched PPO NO {record.ppo_no} / "
                    f"{record.fabric_part} / {record.combo_name or record.color_code} = {knit_qty}. Keeping Knit result."
                )
            else:
                logger(
                    f"[{record.go_key}] Knit PPO Q'ty {knit_qty} and Woven fallback {woven_qty} both matched "
                    f"PPO NO {record.ppo_no} / {record.fabric_part} / {record.combo_name or record.color_code}. "
                    f"Keeping Knit result."
                )
            return knit_qty

        if knit_qty:
            return knit_qty

        if woven_qty:
            logger(
                f"[{record.go_key}] Using Woven PPO fallback for PPO NO {record.ppo_no} / "
                f"{record.fabric_part} / {record.combo_name or record.color_code}: {woven_qty}."
            )
            return woven_qty

        logger(
            f"[{record.go_key}] No Knit/Woven PPO Q'ty match for PPO NO {record.ppo_no} / "
            f"{record.fabric_part} / {record.combo_name or record.color_code}."
        )
        return ""

    def _resolve_cm_ppo_qty(
        self,
        record: ExportRecord,
        lookups: dict[tuple[str, str, str], str],
        logger: LogFn,
    ) -> str:
        combo_key = normalize_lookup_text(record.color_desc or record.combo_name or record.gmt_color)
        part_key = normalize_lookup_text(record.fabric_part)
        if not combo_key or not part_key:
            return ""

        ppo_qty = lookups.get((record.ppo_no.upper(), combo_key, part_key), "")
        if ppo_qty:
            return ppo_qty

        logger(
            f"[{record.go_key}] No CM PPO Q'ty match for PPO NO {record.ppo_no} / "
            f"{record.color_desc or record.combo_name} / {record.fabric_part}."
        )
        return ""

    def _build_cm_color_jo_lookup(self, go_report: GOReportData) -> dict[tuple[str, str], list[str]]:
        lot_to_jo: dict[str, str] = {}
        for jo_no, lot_no in go_report.jo_lot_map.items():
            if lot_no and jo_no:
                lot_to_jo[lot_no] = jo_no

        jo_by_color_desc: dict[str, list[str]] = {}
        jo_by_color_code: dict[str, list[str]] = {}

        for row in go_report.lot_color_rows:
            jo_no = lot_to_jo.get(row.lot_no, "")
            if not jo_no:
                continue

            desc_key = normalize_lookup_text(row.color_desc)
            code_key = row.color_code.upper()
            if desc_key:
                jo_by_color_desc.setdefault(desc_key, [])
                if jo_no not in jo_by_color_desc[desc_key]:
                    jo_by_color_desc[desc_key].append(jo_no)
            if code_key:
                jo_by_color_code.setdefault(code_key, [])
                if jo_no not in jo_by_color_code[code_key]:
                    jo_by_color_code[code_key].append(jo_no)

        lookup: dict[tuple[str, str], list[str]] = {}
        for desc_key, jo_list in jo_by_color_desc.items():
            lookup[("DESC", desc_key)] = list(jo_list)
        for code_key, jo_list in jo_by_color_code.items():
            lookup[("CODE", code_key)] = list(jo_list)
        return lookup

    @staticmethod
    def _resolve_cm_jo_list(
        summary_row: GoColorSummaryRow,
        jo_lookup: dict[tuple[str, str], list[str]],
    ) -> list[str]:
        desc_key = normalize_lookup_text(summary_row.color_desc)
        code_key = summary_row.color_code.upper()
        if desc_key and ("DESC", desc_key) in jo_lookup:
            return list(jo_lookup[("DESC", desc_key)])
        if code_key and ("CODE", code_key) in jo_lookup:
            return list(jo_lookup[("CODE", code_key)])
        return []

    @staticmethod
    def _pure_knit_go_keys(records: list[ExportRecord]) -> set[str]:
        flows_by_go: dict[str, set[str]] = {}
        for record in records:
            if record.is_separator:
                continue
            go_key = record.go.upper()
            flows_by_go.setdefault(go_key, set()).add(record.flow.upper())
        return {go_key for go_key, flows in flows_by_go.items() if flows == {"KNIT"}}

    def _resolve_collar_cuff_qty(
        self,
        record: ExportRecord,
        lookups: dict[str, dict[str, dict[str, list[WebmergeColorSizeAggregate]]]],
        logger: LogFn,
    ) -> str:
        if record.sheet_kind != COLLAR_SHEET_NAME:
            return record.qty

        go_lookup = lookups.get(record.go.upper(), {})
        size_key = self._normalize_size_key(record.size)
        if not size_key:
            return record.qty

        matches = go_lookup.get("by_color", {}).get(record.color_code.upper(), []) if record.color_code else []
        if not matches and record.color_desc:
            matches = go_lookup.get("by_desc", {}).get(normalize_lookup_text(record.color_desc), [])
        if not matches and record.gmt_color:
            matches = go_lookup.get("by_desc", {}).get(normalize_lookup_text(record.gmt_color), [])

        for match in matches:
            if self._normalize_size_key(match.size) == size_key:
                return match.qty

        logger(
            f"[{record.go_key}] No Collar/Cuff Qty match for GO {record.go} / "
            f"{record.fabric_part} / {record.color_code or record.color_desc} / {record.size}. Keeping row."
        )
        return record.qty

    def _resolve_knit_collar_cuff_ppo_qty(
        self,
        record: ExportRecord,
        lookups: dict[str, dict[tuple[str, str, str, str], str]],
        logger: LogFn,
    ) -> str:
        part_key = normalize_lookup_text(record.fabric_part)
        size_key = self._normalize_size_key(record.size)
        if not part_key or not size_key:
            return ""

        if record.color_code:
            color_match = lookups["by_color"].get((record.ppo_no, part_key, record.color_code.upper(), size_key), "")
            if color_match:
                return color_match

        color_desc_key = normalize_lookup_text(record.color_desc or record.gmt_color)
        if color_desc_key:
            desc_match = lookups["by_desc"].get((record.ppo_no, part_key, color_desc_key, size_key), "")
            if desc_match:
                return desc_match

        logger(
            f"[{record.go_key}] No Collar/Cuff PPO Q'ty match for PPO NO {record.ppo_no} / "
            f"{record.fabric_part} / {record.color_code or record.color_desc} / {record.size}."
        )
        return ""

    def _fetch_best_marker_rows(
        self,
        go_no: str,
        block: GOBomBlock,
        yy_request: YYRequest,
        ypd_client: YPDClient,
        logger: LogFn,
    ) -> list[MarkerRow]:
        if block.flow != "WOVEN" or yy_request.workflow_version_no:
            return ypd_client.fetch_marker_rows(yy_request)

        cached_version = self._woven_ypd_version_cache.get(yy_request.workflow_no)
        if cached_version:
            cached_request = YYRequest(
                raw_value=f"{yy_request.workflow_no}({cached_version})",
                workflow_no=yy_request.workflow_no,
                workflow_version_no=cached_version,
            )
            return ypd_client.fetch_marker_rows(cached_request)

        expected_rows = self._effective_block_rows_for_request(block, yy_request.raw_value)
        initial_rows = ypd_client.fetch_marker_rows(yy_request)
        best_rows = initial_rows
        best_score = self._score_woven_marker_rows(initial_rows, expected_rows)
        target_score = self._expected_marker_target_score(expected_rows)

        if best_score >= target_score and best_score > 0:
            return initial_rows

        best_version: str | None = None
        for version in range(1, 31):
            version_text = str(version)
            versioned_request = YYRequest(
                raw_value=f"{yy_request.workflow_no}({version_text})",
                workflow_no=yy_request.workflow_no,
                workflow_version_no=version_text,
            )
            try:
                candidate_rows = ypd_client.fetch_marker_rows(versioned_request)
            except YPDRequestError:
                continue

            score = self._score_woven_marker_rows(candidate_rows, expected_rows)
            if score > best_score:
                best_score = score
                best_rows = candidate_rows
                best_version = version_text
                if score >= target_score and score > 0:
                    break

        if best_version:
            self._woven_ypd_version_cache[yy_request.workflow_no] = best_version
            logger(f"[{go_no}] Resolved Woven YPD version {best_version} for {yy_request.workflow_no}.")

        return best_rows

    @staticmethod
    def _score_woven_marker_rows(marker_rows: list[MarkerRow], expected_rows: list[GOBomRow]) -> int:
        expected_keys: set[tuple[str, str, str]] = set()
        for row in expected_rows:
            fabric_part = normalize_lookup_text(row.gmt_part)
            if row.gmt_color_code:
                expected_keys.add(("CODE", fabric_part, row.gmt_color_code.upper()))
            elif row.gmt_color:
                expected_keys.add(("COLOR", fabric_part, normalize_lookup_text(row.gmt_color)))

        matched: set[tuple[str, str, str]] = set()
        for row in marker_rows:
            fabric_part = normalize_lookup_text(row.fabric_part)
            if row.gmt_color_code and ("CODE", fabric_part, row.gmt_color_code.upper()) in expected_keys:
                matched.add(("CODE", fabric_part, row.gmt_color_code.upper()))
                continue

            color_key = normalize_lookup_text(row.gmt_color)
            if color_key and ("COLOR", fabric_part, color_key) in expected_keys:
                matched.add(("COLOR", fabric_part, color_key))

        return len(matched)

    @staticmethod
    def _expected_marker_target_score(expected_rows: list[GOBomRow]) -> int:
        target_keys: set[tuple[str, str, str]] = set()
        for row in expected_rows:
            fabric_part = normalize_lookup_text(row.gmt_part)
            if row.gmt_color_code:
                target_keys.add(("CODE", fabric_part, row.gmt_color_code.upper()))
            elif row.gmt_color:
                target_keys.add(("COLOR", fabric_part, normalize_lookup_text(row.gmt_color)))
        return len(target_keys)

    @staticmethod
    def _combo_match_tokens(value: str) -> list[str]:
        normalized = normalize_lookup_text(value)
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(token: str) -> None:
            cleaned = normalize_lookup_text(token)
            if not cleaned or cleaned in seen:
                return
            if not any(character.isalpha() for character in cleaned):
                return
            seen.add(cleaned)
            tokens.append(cleaned)

        add_token(normalized)
        for segment in re.split(r"[+/]", normalized):
            segment = normalize_space(segment)
            if not segment:
                continue
            base = re.split(r"[\s(-]", segment, maxsplit=1)[0]
            add_token(base)

        return tokens

    @staticmethod
    def _resolve_woven_fuzzy_combo_qty(
        candidates: list[tuple[str, str]],
        combo_tokens: list[str],
    ) -> str:
        if not combo_tokens:
            return ""

        token_set = set(combo_tokens)
        matched_candidates: list[tuple[str, str, bool]] = []
        for candidate_combo, candidate_qty in candidates:
            candidate_tokens = set(GetYYService._combo_match_tokens(candidate_combo))
            if not candidate_tokens or not (candidate_tokens & token_set):
                continue
            matched_candidates.append(
                (
                    candidate_combo,
                    candidate_qty,
                    "SURCHARGE" in normalize_lookup_text(candidate_combo),
                )
            )

        if not matched_candidates:
            return ""

        non_surcharge_matches = [candidate for candidate in matched_candidates if not candidate[2]]
        if len(non_surcharge_matches) == 1:
            return non_surcharge_matches[0][1]
        if len(non_surcharge_matches) > 1:
            return ""
        if len(matched_candidates) == 1:
            return matched_candidates[0][1]
        return ""

    @staticmethod
    def _normalize_size_key(size: str) -> str:
        return normalize_space(size).upper()

    @staticmethod
    def _size_sort_key(size: str) -> int:
        order = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "XXXL": 6}
        return order.get(normalize_space(size).upper(), 99)

    @staticmethod
    def _match_marker_row(
        go_no: str,
        yy_req_no: str,
        bom_row: GOBomRow,
        marker_rows: list[MarkerRow],
        logger: LogFn,
    ) -> MarkerRow | None:
        part_key = normalize_lookup_text(bom_row.gmt_part)
        exact_matches = [
            row
            for row in marker_rows
            if normalize_lookup_text(row.fabric_part) == part_key
            and bom_row.gmt_color_code
            and row.gmt_color_code.upper() == bom_row.gmt_color_code.upper()
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            logger(
                f"[{go_no}] Multiple Marker YY matches for {yy_req_no} / {bom_row.gmt_color_code} / {bom_row.gmt_part}."
            )
            return None

        color_key = normalize_lookup_text(bom_row.gmt_color)
        fallback_matches = [
            row
            for row in marker_rows
            if normalize_lookup_text(row.fabric_part) == part_key
            and color_key
            and normalize_lookup_text(row.gmt_color) == color_key
        ]
        if len(fallback_matches) == 1:
            return fallback_matches[0]
        if len(fallback_matches) > 1:
            logger(
                f"[{go_no}] Multiple Marker YY fallback matches for {yy_req_no} / "
                f"{bom_row.gmt_color or bom_row.gmt_color_code} / {bom_row.gmt_part}."
            )
            return None

        part_only_matches = [
            row
            for row in marker_rows
            if normalize_lookup_text(row.fabric_part) == part_key
        ]
        if len(part_only_matches) == 1:
            logger(
                f"[{go_no}] Using single Marker YY part fallback for {yy_req_no} / "
                f"{bom_row.gmt_color_code or bom_row.gmt_color} / {bom_row.gmt_part}."
            )
            return part_only_matches[0]

        logger(
            f"[{go_no}] No Marker YY match for {yy_req_no} / "
            f"{bom_row.gmt_color_code or bom_row.gmt_color} / {bom_row.gmt_part}."
        )
        return None

    @staticmethod
    def _ordered_bom_blocks(go_report: GOReportData) -> list[GOBomBlock]:
        if go_report.bom_blocks:
            raw_blocks = go_report.bom_blocks
        elif go_report.bom_rows:
            try:
                flow = classify_go_flow(go_report.go_no)
            except ValidationError:
                flow = "KNIT"
            synthetic_rows = [
                replace(row, flow=row.flow or flow, block_index=0, table_order=index)
                for index, row in enumerate(go_report.bom_rows)
            ]
            raw_blocks = [
                GOBomBlock(
                    flow=flow,
                    section_title=f"{flow.title()} Fabric BOM Information",
                    block_index=0,
                    bom_rows=synthetic_rows,
                )
            ]
        else:
            return []

        return sorted(raw_blocks, key=lambda block: (0 if block.flow == "WOVEN" else 1, block.block_index))

    @staticmethod
    def _block_requests(block: GOBomBlock) -> list[YYRequest]:
        ordered_requests: list[YYRequest] = []
        seen: set[str] = set()
        for row in block.bom_rows:
            if not row.yy_req_no:
                continue
            try:
                yy_request = parse_yy_request(row.yy_req_no)
            except Exception:
                continue
            if yy_request.raw_value in seen:
                continue
            seen.add(yy_request.raw_value)
            ordered_requests.append(yy_request)
        return ordered_requests

    def _effective_block_rows_for_request(self, block: GOBomBlock, yy_req_no: str) -> list[GOBomRow]:
        return [row for row in block.bom_rows if row.yy_req_no == yy_req_no]

    @staticmethod
    def _build_aggregate_key(
        *,
        go_no: str,
        flow: str,
        fabric_part: str,
        color_code: str,
        combo_name: str,
        size: str = "",
        sheet_kind: str = "COI",
    ) -> str:
        return "|".join(
            [
                go_no.upper(),
                sheet_kind.upper(),
                flow.upper(),
                normalize_lookup_text(fabric_part),
                color_code.upper(),
                normalize_lookup_text(combo_name),
                normalize_lookup_text(size),
            ]
        )

    @staticmethod
    def _is_create_complete(candidate: ResolvedBomRow) -> bool:
        if not normalize_space(candidate.fabric_part):
            return False
        if not normalize_space(candidate.color_code) and not normalize_space(candidate.gmt_color):
            return False
        if candidate.yy_req_no and GetYYService._is_missing_numeric(candidate.marker_yy):
            return False
        if candidate.flow == "KNIT" and candidate.yy_req_no and GetYYService._is_missing_numeric(candidate.ppo_yy):
            return False
        return True

    @staticmethod
    def _is_cm_go_report(go_report: GOReportData) -> bool:
        return (
            not go_report.bom_rows
            and not go_report.yy_requests
            and not go_report.ppo_numbers
            and bool(go_report.color_summary_rows)
        )

    @staticmethod
    def _is_missing_numeric(value: str) -> bool:
        return normalize_number(value) in {"", "0"}

    @staticmethod
    def _sum_mes_qty(mes_rows: list[MESJoRow]) -> str:
        if not mes_rows:
            return ""
        total = Decimal("0")
        for row in mes_rows:
            total += Decimal(normalize_number(row.order_qty) or "0")
        return normalize_number(str(total))

    @staticmethod
    def _is_collar_cuff_part(fabric_part: str) -> bool:
        normalized = normalize_lookup_text(fabric_part)
        return "FK" in normalized
