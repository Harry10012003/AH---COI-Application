"""Embed a shareable SQL Server Power Query in the leftover workbook.

The query contains no username or password.  Each colleague supplies an
authorized SQL Server credential once in Excel's Data Source Settings, then
uses Data > Refresh All to update the shared workbook.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pythoncom
import win32com.client


DEFAULT_WORKBOOK = Path(r"C:\Users\kiddy.nguyen\Desktop\TGV TDV_Leftover fabric_YTD07.xlsx")
SHEET_NAME = "Weekly Shipment"
SNAPSHOT_SHEET_NAME = "Weekly Shipment Snapshot"
QUERY_NAME = "Weekly Shipment EGV EAV"
CONNECTION_NAME = f"Query - {QUERY_NAME}"
TABLE_NAME = "WeeklyShipmentPQ"


def _m_escape(value: object) -> str:
    """Escape an M string literal without writing credential text to source code."""
    return str(value or "").replace('"', '""')


def _odbc_braced(value: object) -> str:
    return "{" + str(value or "").replace("}", "}}") + "}"


def _build_m_formula() -> str:
    """Build the shareable Power Query formula at runtime.

    The user explicitly approved storing the current longtat SQL credential in
    the workbook so recipients can use Refresh All without a separate setup.
    The credential is read from Windows Credential Manager and is never saved
    in this Python source file or printed to the console.
    """
    project_dir = Path(__file__).resolve().parents[1]
    if str(project_dir) not in sys.path:
        sys.path.insert(0, str(project_dir))
    from backend import sources

    if not sources.SQL_SERVER_USER or not sources.SQL_SERVER_PASSWORD:
        raise RuntimeError("The longtat SQL credential is not available for this Windows user.")
    connection_string = (
        f"Driver={{{sources.SQL_SERVER_DRIVER}}};"
        f"Server={sources.SQL_SERVER_HOST};"
        f"Database={sources.SQL_SERVER_DATABASE};"
        f"UID={_odbc_braced(sources.SQL_SERVER_USER)};"
        f"PWD={_odbc_braced(sources.SQL_SERVER_PASSWORD)};"
        "Encrypt=no;"
    )
    native_sql = """
SELECT
    LTRIM(RTRIM([GO NO])) AS [GO],
    LTRIM(RTRIM([JO No])) AS [JO],
    CAST([BPO Date] AS date) AS [BPOD],
    CAST(SUM(CAST(COALESCE([Qty], 0) AS float)) AS decimal(18, 2)) AS [Số lượng],
    COALESCE(
        NULLIF(LTRIM(RTRIM([Shipment Factory])), ''),
        NULLIF(LTRIM(RTRIM([Factory Code])), ''),
        'N/A'
    ) AS [Nơi xuất hàng]
FROM dbo.V_GO_BPO_HD_JO_ALL
WHERE [BPO Date] >= DATEADD(day, -(DATEDIFF(day, 0, CAST(GETDATE() AS date)) % 7), CAST(GETDATE() AS date))
  AND [BPO Date] < DATEADD(day, 6 - (DATEDIFF(day, 0, CAST(GETDATE() AS date)) % 7), CAST(GETDATE() AS date))
  AND UPPER(LTRIM(RTRIM(COALESCE([Shipment Factory], [Factory Code], '')))) IN ('EGV', 'EAV')
GROUP BY
    LTRIM(RTRIM([GO NO])),
    LTRIM(RTRIM([JO No])),
    CAST([BPO Date] AS date),
    COALESCE(
        NULLIF(LTRIM(RTRIM([Shipment Factory])), ''),
        NULLIF(LTRIM(RTRIM([Factory Code])), ''),
        'N/A'
    )
ORDER BY [BPOD], [GO], [JO], [Nơi xuất hàng]
""".strip()
    return f'''
let
    Source = Odbc.Query("{_m_escape(connection_string)}", "{_m_escape(native_sql)}"),
    #"Changed Type" = Table.TransformColumnTypes(
        Source,
        {{{{"GO", type text}}, {{"JO", type text}}, {{"BPOD", type date}}, {{"Số lượng", type number}}, {{"Nơi xuất hàng", type text}}}},
        "en-US"
    ),
    #"Cleaned Text" = Table.TransformColumns(
        #"Changed Type",
        {{
            {{"GO", each if _ = null then "" else Text.Upper(Text.Trim(Text.From(_))), type text}},
            {{"JO", each if _ = null then "" else Text.Upper(Text.Trim(Text.From(_))), type text}},
            {{"Nơi xuất hàng", each if _ = null or Text.Trim(Text.From(_)) = "" then "N/A" else Text.Upper(Text.Trim(Text.From(_))), type text}}
        }}
    ),
    #"Removed Blank GO JO" = Table.SelectRows(#"Cleaned Text", each [GO] <> "" and [JO] <> ""),
    #"Sorted Rows" = Table.Sort(#"Removed Blank GO JO", {{{{"BPOD", Order.Ascending}}, {{"GO", Order.Ascending}}, {{"JO", Order.Ascending}}}})
in
    #"Sorted Rows"
'''.strip()


def _normal_path(value: object) -> str:
    return os.path.normcase(os.path.abspath(str(value)))


def _open_workbook(excel, workbook_path: Path):
    target = _normal_path(workbook_path)
    try:
        for index in range(1, excel.Workbooks.Count + 1):
            candidate = excel.Workbooks(index)
            if _normal_path(candidate.FullName) == target:
                return candidate, True
    except Exception:
        pass
    return excel.Workbooks.Open(str(workbook_path), UpdateLinks=0, ReadOnly=False), False


def _try_item(collection, name: str):
    try:
        return collection.Item(name)
    except Exception:
        return None


def _delete_query_artifacts(workbook, worksheet) -> None:
    table = _try_item(worksheet.ListObjects, TABLE_NAME)
    if table is not None:
        table.Delete()
    connection = _try_item(workbook.Connections, CONNECTION_NAME)
    if connection is not None:
        connection.Delete()
    query = _try_item(workbook.Queries, QUERY_NAME)
    if query is not None:
        query.Delete()


def _format_sheet(worksheet) -> None:
    worksheet.Range("A1:E1").Merge()
    worksheet.Range("A1").Value = "BÁO CÁO JO XUẤT HÀNG THEO TUẦN — EGV / EAV"
    worksheet.Range("A1").Font.Bold = True
    worksheet.Range("A1").Font.Size = 14
    worksheet.Range("A1").HorizontalAlignment = -4108  # xlCenter
    worksheet.Range("A1").Interior.Color = 12611584
    worksheet.Range("A2").Value = "Power Query từ SQL Server | BPOD = BPO Date | tự lấy Thứ Hai đến Thứ Bảy của tuần hiện tại"
    worksheet.Range("A3").Value = "Cập nhật: Data → Refresh All. Chỉ lấy Shipment Factory EGV và EAV."
    worksheet.Range("A4").Value = "Lần đầu trên mỗi máy: nhập SQL Server credential được cấp; workbook không lưu mật khẩu."
    worksheet.Columns("A").ColumnWidth = 18
    worksheet.Columns("B").ColumnWidth = 20
    worksheet.Columns("C").ColumnWidth = 14
    worksheet.Columns("D").ColumnWidth = 15
    worksheet.Columns("E").ColumnWidth = 20


def integrate(workbook_path: Path) -> None:
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    pythoncom.CoInitialize()
    excel = None
    excel_was_created = False
    workbook = None
    workbook_was_open = False
    snapshot = None
    target_sheet = None
    snapshot_renamed = False
    target_sheet_created = False
    completed = False
    try:
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = True
            excel_was_created = True
        workbook, workbook_was_open = _open_workbook(excel, workbook_path)

        existing_query = _try_item(workbook.Queries, QUERY_NAME)
        if existing_query is not None:
            raise RuntimeError("The workbook already contains the Weekly Shipment Power Query.")

        source_sheet = _try_item(workbook.Worksheets, SHEET_NAME)
        snapshot = _try_item(workbook.Worksheets, SNAPSHOT_SHEET_NAME)
        if source_sheet is not None:
            if snapshot is not None:
                raise RuntimeError(f"Both '{SHEET_NAME}' and '{SNAPSHOT_SHEET_NAME}' already exist; no changes were made.")
            source_sheet.Name = SNAPSHOT_SHEET_NAME
            snapshot = source_sheet
            snapshot_renamed = True

        target_sheet = workbook.Worksheets.Add(After=workbook.Worksheets(workbook.Worksheets.Count))
        target_sheet.Name = SHEET_NAME
        target_sheet_created = True
        _format_sheet(target_sheet)

        workbook.Queries.Add(QUERY_NAME, _build_m_formula())
        connection_string = (
            "OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;"
            f"Location={QUERY_NAME};Extended Properties=\"\""
        )
        command_text = f"SELECT * FROM [{QUERY_NAME}]"
        workbook.Connections.Add2(
            CONNECTION_NAME,
            f"Connection to the '{QUERY_NAME}' Power Query in this workbook.",
            connection_string,
            command_text,
            2,  # xlCmdSql; the command text is SELECT * FROM [Power Query name]
            True,
            False,
        )
        list_object = target_sheet.ListObjects.Add(
            0,
            connection_string,
            True,
            1,
            target_sheet.Range("A5"),
        )
        query_table = list_object.QueryTable
        query_table.CommandType = 2  # xlCmdSql
        query_table.CommandText = command_text
        query_table.BackgroundQuery = False
        query_table.RefreshOnFileOpen = False
        query_table.SavePassword = False
        query_table.SaveData = True
        query_table.PreserveFormatting = True
        query_table.AdjustColumnWidth = True
        query_table.RefreshPeriod = 0
        query_table.ListObject.Name = TABLE_NAME
        target_sheet.Range("C:C").NumberFormat = "dd/mm/yyyy"
        target_sheet.Range("D:D").NumberFormat = "#,##0.00"
        target_sheet.Activate()
        workbook.Save()
        completed = True
    except Exception:
        # Revert in-memory changes too, so an already-open workbook cannot be
        # accidentally saved in a partially configured state after an error.
        original_alerts = None
        if excel is not None:
            try:
                original_alerts = excel.DisplayAlerts
                excel.DisplayAlerts = False
            except Exception:
                original_alerts = None
        if workbook is not None:
            try:
                if target_sheet is not None:
                    _delete_query_artifacts(workbook, target_sheet)
            except Exception:
                pass
            if target_sheet_created:
                try:
                    target_sheet.Delete()
                except Exception:
                    pass
            if snapshot_renamed and snapshot is not None:
                try:
                    snapshot.Name = SHEET_NAME
                except Exception:
                    pass
        if excel is not None and original_alerts is not None:
            try:
                excel.DisplayAlerts = original_alerts
            except Exception:
                pass
        raise
    finally:
        if workbook is not None and not workbook_was_open:
            workbook.Close(SaveChanges=completed)
        if excel is not None and excel_was_created:
            excel.Quit()
        pythoncom.CoUninitialize()


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed the Weekly Shipment EGV/EAV Power Query.")
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    args = parser.parse_args()
    integrate(args.workbook.expanduser().resolve())
    print("Power Query created: Weekly Shipment EGV EAV. Use Data > Refresh All after entering authorized credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
