from __future__ import annotations

import sys
from pathlib import Path

ALERT_THRESHOLD = 1.04
ALERT_FILL_COLOR = 65535
ALERT_FONT_COLOR = 0
MAIN_SHEET_VISIBLE_COLUMNS = 16
MAIN_SHEET_TOTAL_COLUMNS = 28
CM_MAIN_SHEET_VISIBLE_COLUMNS = 10
CM_MAIN_SHEET_TOTAL_COLUMNS = 22
COLLAR_SHEET_VISIBLE_COLUMNS = 10
COLLAR_SHEET_TOTAL_COLUMNS = 22


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base_path = Path(__file__).resolve().parent.parent
    return base_path.joinpath(*parts)


def recalculate_workbook_with_excel(file_path: str | Path) -> bool:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False

    path = Path(file_path).expanduser().resolve()
    excel = None
    workbook = None
    try:
        pythoncom.CoInitialize()
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = excel.Workbooks.Open(str(path))
        excel.CalculateFullRebuild()
        _autofit_visible_tables(workbook)
        _apply_static_alert_highlights(workbook)
        workbook.Save()
        return True
    except Exception:
        return False
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=True)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _apply_static_alert_highlights(workbook) -> None:
    for worksheet in workbook.Worksheets:
        title = str(worksheet.Name)
        if title == "COI":
            layout = _worksheet_layout(worksheet)
            if layout == (CM_MAIN_SHEET_VISIBLE_COLUMNS, CM_MAIN_SHEET_TOTAL_COLUMNS):
                _highlight_worksheet_columns(worksheet, ["J"])
            else:
                _highlight_worksheet_columns(worksheet, ["E", "P"])
        elif "Collar" in title and "Cuff" in title:
            _highlight_worksheet_columns(worksheet, ["J"])


def _autofit_visible_tables(workbook) -> None:
    for worksheet in workbook.Worksheets:
        try:
            layout = _worksheet_layout(worksheet)
            if layout is None:
                continue
            visible_count, total_count = layout
            last_visible_column = _excel_column_letter(visible_count)
            last_row = _worksheet_last_row(worksheet)
            _hide_metadata_columns(worksheet, visible_count + 1, total_count)
            worksheet.Range(f"A:{last_visible_column}").Columns.AutoFit()
            worksheet.Range(f"A1:{last_visible_column}{last_row}").Rows.AutoFit()
            _hide_metadata_columns(worksheet, visible_count + 1, total_count)
        except Exception:
            continue


def _worksheet_layout(worksheet) -> tuple[int, int] | None:
    title = str(worksheet.Name)
    if title == "COI":
        second_header = str(worksheet.Cells(1, 2).Value or "").strip()
        if second_header == "YY Req No":
            return MAIN_SHEET_VISIBLE_COLUMNS, MAIN_SHEET_TOTAL_COLUMNS
        return CM_MAIN_SHEET_VISIBLE_COLUMNS, CM_MAIN_SHEET_TOTAL_COLUMNS
    if "Collar" in title and "Cuff" in title:
        return COLLAR_SHEET_VISIBLE_COLUMNS, COLLAR_SHEET_TOTAL_COLUMNS
    return None


def _worksheet_last_row(worksheet) -> int:
    used_range = worksheet.UsedRange
    start_row = int(used_range.Row or 1)
    row_count = int(used_range.Rows.Count or 1)
    return max(start_row + row_count - 1, 1)


def _hide_metadata_columns(worksheet, start_index: int, end_index: int) -> None:
    start_column = _excel_column_letter(start_index)
    end_column = _excel_column_letter(end_index)
    worksheet.Range(f"{start_column}:{end_column}").EntireColumn.Hidden = True
    for column_index in range(start_index, end_index + 1):
        worksheet.Columns(_excel_column_letter(column_index)).Hidden = True


def _excel_column_letter(column_index: int) -> str:
    letters: list[str] = []
    current = column_index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def _highlight_worksheet_columns(worksheet, columns: list[str]) -> None:
    last_row = int(worksheet.UsedRange.Rows.Count or 0)
    if last_row < 2:
        return

    for column in columns:
        for row_index in range(2, last_row + 1):
            cell = worksheet.Range(f"{column}{row_index}")
            value = cell.Value
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if numeric_value > ALERT_THRESHOLD:
                cell.Interior.Color = ALERT_FILL_COLOR
                cell.Font.Color = ALERT_FONT_COLOR
