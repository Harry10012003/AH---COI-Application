"""Embed a refreshable SQL Server ODBC query into the shared workbook.

The workbook owner explicitly approved saving the SQL credential in the
workbook connection so approved recipients can use Data > Refresh All without
performing first-time Power Query credential setup.  Do not distribute this
workbook outside the approved internal group.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pythoncom
import win32com.client


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = Path(r"C:\Users\kiddy.nguyen\Desktop\TGV TDV_Leftover fabric_YTD07.xlsx")
SHEET_NAME = "Weekly Shipment"
SNAPSHOT_SHEET_NAME = "Weekly Shipment Snapshot"
QUERY_TABLE_NAME = "WeeklyShipmentODBC"

SQL_QUERY = """
WITH WeeklyShipments AS (
    SELECT
        LTRIM(RTRIM([GO NO])) AS [GO],
        LTRIM(RTRIM([JO No])) AS [JO],
        CAST([BPO Date] AS date) AS [BPOD],
        CAST(COALESCE([Qty], 0) AS float) AS [Qty],
        COALESCE(
            NULLIF(LTRIM(RTRIM([Shipment Factory])), ''),
            NULLIF(LTRIM(RTRIM([Factory Code])), ''),
            'N/A'
        ) AS [Nơi xuất hàng],
        LTRIM(RTRIM(COALESCE([Brand Code], ''))) AS [Brand Code]
    FROM dbo.V_GO_BPO_HD_JO_ALL
    WHERE [BPO Date] >= DATEADD(day, -(DATEDIFF(day, 0, CAST(GETDATE() AS date)) % 7), CAST(GETDATE() AS date))
      AND [BPO Date] < DATEADD(day, 6 - (DATEDIFF(day, 0, CAST(GETDATE() AS date)) % 7), CAST(GETDATE() AS date))
      AND UPPER(LTRIM(RTRIM(COALESCE([Shipment Factory], [Factory Code], '')))) IN ('EGV', 'EAV')
),
WeeklyGOs AS (
    SELECT DISTINCT [GO]
    FROM WeeklyShipments
    WHERE [GO] <> ''
),
LastShipmentByGo AS (
    -- Calculate the last BPOD only for the GOs visible this week.  The date
    -- still comes from all factories, which preserves the GO-complete rule.
    SELECT
        weekly_go.[GO],
        MAX(CAST(all_shipment.[BPO Date] AS date)) AS [Last BPOD]
    FROM WeeklyGOs AS weekly_go
    INNER JOIN dbo.V_GO_BPO_HD_JO_ALL AS all_shipment
        ON LTRIM(RTRIM(all_shipment.[GO NO])) = weekly_go.[GO]
    WHERE all_shipment.[BPO Date] IS NOT NULL
    GROUP BY weekly_go.[GO]
),
DistinctPPOByGo AS (
    SELECT DISTINCT
        LTRIM(RTRIM(mapping.[GO NO])) AS [GO],
        LTRIM(RTRIM(mapping.[PPO NO])) AS [PPO]
    FROM dbo.V_GO_PPO_Mapping AS mapping
    INNER JOIN WeeklyGOs AS weekly_go
        ON weekly_go.[GO] = LTRIM(RTRIM(mapping.[GO NO]))
    WHERE mapping.[PPO NO] IS NOT NULL
      AND LTRIM(RTRIM(mapping.[PPO NO])) <> ''
),
RankedPPOByGo AS (
    SELECT
        [GO],
        [PPO],
        ROW_NUMBER() OVER (PARTITION BY [GO] ORDER BY [PPO]) AS [PPO Rank]
    FROM DistinctPPOByGo
),
PPOByGo AS (
    SELECT
        [GO],
        MAX(CASE WHEN [PPO Rank] = 1 THEN [PPO] END) AS [PPO 1],
        STRING_AGG(
            CONVERT(varchar(max), CASE WHEN [PPO Rank] >= 2 THEN [PPO] END),
            '; '
        ) WITHIN GROUP (ORDER BY [PPO Rank]) AS [PPO 2]
    FROM RankedPPOByGo
    GROUP BY [GO]
),
BrandCodes AS (
    SELECT DISTINCT [Brand Code]
    FROM WeeklyShipments
    WHERE [Brand Code] <> ''
),
BrandByCode AS (
    SELECT
        codes.[Brand Code],
        MAX(order_list.[BRAND_NAME]) AS [Brand Name]
    FROM BrandCodes AS codes
    LEFT JOIN dbo.ESCM_ORDER_LIST_SALES AS order_list
        ON order_list.[BRAND_CODE] = codes.[Brand Code]
    GROUP BY codes.[Brand Code]
)
SELECT
    weekly.[GO],
    weekly.[JO],
    weekly.[BPOD],
    CAST(SUM(weekly.[Qty]) AS decimal(18, 2)) AS [Số lượng],
    weekly.[Nơi xuất hàng],
    CASE
        WHEN weekly.[BPOD] = last_shipment.[Last BPOD] THEN 'YES'
        ELSE 'NO'
    END AS [Last JO đã xuất hàng?],
    ppo.[PPO 1],
    ppo.[PPO 2],
    MAX(brand.[Brand Name]) AS [Brand Name]
FROM WeeklyShipments AS weekly
INNER JOIN LastShipmentByGo AS last_shipment
    ON last_shipment.[GO] = weekly.[GO]
LEFT JOIN PPOByGo AS ppo
    ON ppo.[GO] = weekly.[GO]
LEFT JOIN BrandByCode AS brand
    ON brand.[Brand Code] = weekly.[Brand Code]
GROUP BY
    weekly.[GO],
    weekly.[JO],
    weekly.[BPOD],
    weekly.[Nơi xuất hàng],
    last_shipment.[Last BPOD],
    ppo.[PPO 1],
    ppo.[PPO 2]
ORDER BY weekly.[BPOD], weekly.[GO], weekly.[JO], weekly.[Nơi xuất hàng]
""".strip()


def _braced_odbc_value(value: object) -> str:
    return "{" + str(value or "").replace("}", "}}") + "}"


def _connection_string() -> str:
    if str(PROJECT_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECT_DIR))
    from backend import sources

    if not sources.SQL_SERVER_USER or not sources.SQL_SERVER_PASSWORD:
        raise RuntimeError("The longtat SQL credential is not available for this Windows user.")
    return (
        "ODBC;"
        f"DRIVER={{{sources.SQL_SERVER_DRIVER}}};"
        f"SERVER={sources.SQL_SERVER_HOST};"
        f"DATABASE={sources.SQL_SERVER_DATABASE};"
        f"UID={_braced_odbc_value(sources.SQL_SERVER_USER)};"
        f"PWD={_braced_odbc_value(sources.SQL_SERVER_PASSWORD)};"
        "Trusted_Connection=no;"
        "Encrypt=no;"
    )


def _try_item(collection, name: str):
    try:
        return collection.Item(name)
    except Exception:
        return None


def _weekly_query_table(worksheet):
    """Find the report query even when Excel adds a numeric name suffix."""
    for index in range(1, worksheet.QueryTables.Count + 1):
        query_table = worksheet.QueryTables(index)
        if str(query_table.Name).startswith(QUERY_TABLE_NAME):
            return query_table
    raise RuntimeError(f"No '{QUERY_TABLE_NAME}' query table exists on '{SHEET_NAME}'.")


def _format_header(worksheet) -> None:
    worksheet.Range("A1:I1").Merge()
    worksheet.Range("A1").Value = "BÁO CÁO JO XUẤT HÀNG THEO TUẦN — EGV / EAV"
    worksheet.Range("A1").Font.Bold = True
    worksheet.Range("A1").Font.Size = 14
    worksheet.Range("A1").HorizontalAlignment = -4108  # xlCenter
    worksheet.Range("A1").Interior.Color = 12611584
    worksheet.Range("A2").Value = "Nguồn: SQL Server | BPOD = BPO Date | phạm vi Thứ Hai đến Thứ Bảy của tuần hiện tại"
    worksheet.Range("A3").Value = "Cập nhật: Data → Refresh All. Chỉ lấy Shipment Factory EGV và EAV."
    worksheet.Range("A4").Value = "INTERNAL ONLY — workbook có SQL connection để Refresh All hoạt động trên máy người nhận."
    worksheet.Columns("A").ColumnWidth = 18
    worksheet.Columns("B").ColumnWidth = 20
    worksheet.Columns("C").ColumnWidth = 14
    worksheet.Columns("D").ColumnWidth = 15
    worksheet.Columns("E").ColumnWidth = 20
    worksheet.Columns("F").ColumnWidth = 22
    worksheet.Columns("G").ColumnWidth = 25
    worksheet.Columns("H").ColumnWidth = 42
    worksheet.Columns("I").ColumnWidth = 22


def update_existing_connection(workbook_path: Path) -> int:
    """Replace the SQL in the existing native Refresh All query in-place."""
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    pythoncom.CoInitialize()
    excel = None
    workbook = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False
        workbook = excel.Workbooks.Open(str(workbook_path), UpdateLinks=0, ReadOnly=False)
        worksheet = workbook.Worksheets(SHEET_NAME)
        # Excel cannot reliably change an existing QueryTable from five result
        # columns to six.  Rebuild it in the same sheet; no change is saved if
        # the new query fails to refresh.
        old_query_table = _weekly_query_table(worksheet)
        old_last_row = max(5, worksheet.Cells(worksheet.Rows.Count, 1).End(-4162).Row)
        old_query_table.Delete()
        worksheet.Range(f"A5:I{old_last_row}").ClearContents()
        worksheet.Range("A1:I1").UnMerge()
        _format_header(worksheet)

        query_table = worksheet.QueryTables.Add(_connection_string(), worksheet.Range("A5"))
        query_table.Name = QUERY_TABLE_NAME
        query_table.CommandType = 2  # xlCmdSql
        query_table.CommandText = SQL_QUERY
        query_table.BackgroundQuery = False
        query_table.RefreshOnFileOpen = False
        query_table.EnableRefresh = True
        query_table.SavePassword = True
        query_table.SaveData = True
        query_table.PreserveFormatting = True
        query_table.AdjustColumnWidth = True
        query_table.RefreshPeriod = 0
        query_table.Refresh(False)

        last_row = worksheet.Cells(worksheet.Rows.Count, 1).End(-4162).Row  # xlUp
        worksheet.Range(f"A5:I{last_row}").AutoFilter()
        worksheet.Range(f"C6:C{last_row}").NumberFormat = "dd/mm/yyyy"
        worksheet.Range(f"D6:D{last_row}").NumberFormat = "#,##0.00"
        worksheet.Range(f"A5:I{last_row}").Borders.LineStyle = 1
        worksheet.Columns("F").ColumnWidth = 22
        worksheet.Columns("G").ColumnWidth = 25
        worksheet.Columns("H").ColumnWidth = 42
        worksheet.Columns("I").ColumnWidth = 22
        workbook.Save()
        return max(0, last_row - 5)
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def enforce_sql_login(workbook_path: Path) -> None:
    """Persist SQL authentication rather than Windows/Trusted authentication."""
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    pythoncom.CoInitialize()
    excel = None
    workbook = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False
        workbook = excel.Workbooks.Open(str(workbook_path), UpdateLinks=0, ReadOnly=False)
        worksheet = workbook.Worksheets(SHEET_NAME)
        query_table = _weekly_query_table(worksheet)
        query_table.Connection = _connection_string()
        query_table.SavePassword = True
        query_table.EnableRefresh = True
        query_table.BackgroundQuery = False
        query_table.Refresh(False)
        workbook.Save()
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


def integrate(workbook_path: Path) -> int:
    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    pythoncom.CoInitialize()
    excel = None
    workbook = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False
        workbook = excel.Workbooks.Open(str(workbook_path), UpdateLinks=0, ReadOnly=False)
        existing_snapshot = _try_item(workbook.Worksheets, SNAPSHOT_SHEET_NAME)
        existing_source = _try_item(workbook.Worksheets, SHEET_NAME)
        if existing_snapshot is not None:
            raise RuntimeError(f"'{SNAPSHOT_SHEET_NAME}' already exists; integration stopped to protect existing data.")
        if existing_source is not None:
            existing_source.Name = SNAPSHOT_SHEET_NAME

        worksheet = workbook.Worksheets.Add(After=workbook.Worksheets(workbook.Worksheets.Count))
        worksheet.Name = SHEET_NAME
        _format_header(worksheet)
        query_table = worksheet.QueryTables.Add(_connection_string(), worksheet.Range("A5"))
        query_table.Name = QUERY_TABLE_NAME
        query_table.CommandType = 2  # xlCmdSql
        query_table.CommandText = SQL_QUERY
        query_table.BackgroundQuery = False
        query_table.RefreshOnFileOpen = False
        query_table.EnableRefresh = True
        query_table.SavePassword = True
        query_table.SaveData = True
        query_table.PreserveFormatting = True
        query_table.AdjustColumnWidth = True
        query_table.RefreshPeriod = 0
        query_table.Refresh(False)

        last_row = worksheet.Cells(worksheet.Rows.Count, 1).End(-4162).Row  # xlUp
        worksheet.Range(f"A5:I{last_row}").AutoFilter()
        worksheet.Range(f"C6:C{last_row}").NumberFormat = "dd/mm/yyyy"
        worksheet.Range(f"D6:D{last_row}").NumberFormat = "#,##0.00"
        worksheet.Range(f"A5:I{last_row}").Borders.LineStyle = 1
        workbook.Save()
        return max(0, last_row - 5)
    except Exception:
        # The disk workbook remains unchanged because it is only saved after
        # the query has completed and the sheet is fully populated.
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        raise
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    row_count = integrate(DEFAULT_WORKBOOK)
    print(f"Workbook SQL connection created and refreshed: {row_count:,} rows.")
