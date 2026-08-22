from __future__ import annotations

import base64
import getpass
import subprocess
from typing import Final
from urllib.parse import quote

import requests
from requests import Response, Session
from requests.exceptions import RequestException

try:
    import pymssql
except ImportError:  # pragma: no cover - dependency guard
    pymssql = None

try:
    from requests_ntlm import HttpNtlmAuth
except ImportError:  # pragma: no cover - dependency guard
    HttpNtlmAuth = None

from .exceptions import (
    DatabaseQueryError,
    GORequestError,
    MESRequestError,
    PPORequestError,
    ParseError,
    ValidationError,
    YPDAuthenticationError,
    YPDRequestError,
)
from .models import (
    CmQaAggregateRow,
    GOReportData,
    KnitPpoBulkColorSizeAggregate,
    KnitPpoAggregateRow,
    MESJoRow,
    MarkerRow,
    PpoColorAggregateRow,
    PpoComboAggregateRow,
    PpoDetailRow,
    WebmergeColorSizeAggregate,
    WebmergeSizeRow,
    WovenPpoPartQtyRow,
    WovenPpoYYRow,
    YYRequest,
)
from .parsers import (
    extract_hidden_inputs,
    parse_cm_qa_aggregate_rows,
    parse_knit_ppo_bulk_color_size_aggregates,
    normalize_number,
    parse_go_report,
    parse_mes_jo_rows,
    parse_webmerge_go_color_size_aggregates,
    parse_webmerge_size_rows,
    parse_woven_ppo_part_qty_rows,
    parse_woven_ppo_yy_rows,
    parse_ypd_marker_rows,
)
from backend.sources import SQL_SERVER_DATABASE, SQL_SERVER_HOST, SQL_SERVER_PASSWORD, SQL_SERVER_USER

GO_REPORT_URL: Final[str] = "http://192.168.7.108/GORPT/rptsc.asp"
MES_REPORT_URL: Final[str] = "http://192.168.152.2/MES/DCGoSummaryReport.asp"
MES_EAV_REPORT_URL: Final[str] = "http://192.168.152.2/MES_EAV/DCGoSummaryReport.asp"
CM_QA_REPORT_URL: Final[str] = "http://192.168.152.2/MES/QAColorShadingMatchingRpt.asp"
YPD_REPORT_URL: Final[str] = "http://getnt46.gfg1.esquel.com/YPD/Modules/Report/YPDReportViews.aspx"
PPO_REPORT_URL: Final[str] = "http://192.168.7.111/eSCMReport/ppoxReport.aspx"
WEBMERGE_REPORT_URL: Final[str] = "http://192.168.152.26:81/EMI_WEBMERGE/Webmerge.aspx"
KNIT_PPO_BULK_VIEWER_URL: Final[str] = (
    "http://getnt50.gfg1.esquel.com/ReportServer/Pages/ReportViewer.aspx"
    "?%2fPPO%2fKnitPPO_NEW%2fPPOKintReport_Bulk"
    "&rs%3aCommand=Render&rc:Parameters=False"
)
KNIT_PPO_BULK_HOST: Final[str] = "http://getnt50.gfg1.esquel.com"

class GOClient:
    def __init__(self, session: Session | None = None, timeout: int = 30) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_go_report(self, go_no: str) -> GOReportData:
        params = {
            "ODBC": "escm_83",
            "GONO": go_no,
            "ISTSLMI": "Y",
            "showEDP": "Y",
            "GOTSFlag": "Y",
            "WRFlag": "N",
            "ISHsandingFlag": "N",
        }

        try:
            response = self.session.get(GO_REPORT_URL, params=params, timeout=self.timeout)
        except RequestException as exc:
            raise GORequestError(f"Cannot load GO report for {go_no}: {exc}") from exc

        self._raise_for_status(response, go_no)
        self._fix_text_encoding(response, "gb2312")

        try:
            return parse_go_report(response.text, go_no)
        except ParseError as exc:
            raise GORequestError(f"Cannot parse GO report for {go_no}: {exc}") from exc

    @staticmethod
    def _raise_for_status(response: Response, go_no: str) -> None:
        if response.status_code >= 400:
            raise GORequestError(f"GO report returned HTTP {response.status_code} for {go_no}.")

    @staticmethod
    def _fix_text_encoding(response: Response, fallback: str) -> None:
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or fallback


class MESSummaryClient:
    def __init__(self, session: Session | None = None, timeout: int = 30) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_jo_rows(self, go_no: str) -> list[MESJoRow]:
        params = {
            "txtSCNo": go_no,
            "txtCTNo": "",
            "txtColor": "",
            "OrderBy": "Color",
            "submit1": "Query",
        }
        source_urls = [
            ("MES", MES_REPORT_URL),
            ("MES_EAV", MES_EAV_REPORT_URL),
        ]
        errors: list[str] = []
        successful_sources: list[tuple[str, list[MESJoRow]]] = []

        for source_name, source_url in source_urls:
            try:
                response = self.session.get(source_url, params=params, timeout=self.timeout)
            except RequestException as exc:
                errors.append(f"{source_name}: {exc}")
                continue

            if response.status_code >= 400:
                errors.append(f"{source_name}: HTTP {response.status_code}")
                continue

            GOClient._fix_text_encoding(response, "windows-1252")

            try:
                rows = parse_mes_jo_rows(response.text, go_no)
            except ParseError as exc:
                errors.append(f"{source_name}: {exc}")
                continue

            if rows:
                successful_sources.append((source_name, rows))
                continue

            errors.append(f"{source_name}: no JO rows")

        if len(successful_sources) == 1:
            return successful_sources[0][1]

        if len(successful_sources) >= 2:
            primary_name, primary_rows = successful_sources[0]
            secondary_name, secondary_rows = successful_sources[1]
            if self._rows_equivalent(primary_rows, secondary_rows):
                return primary_rows
            raise MESRequestError(
                f"MES data mismatch between {primary_name} and {secondary_name} for {go_no}."
            )

        joined_errors = "; ".join(errors) if errors else "unknown error"
        raise MESRequestError(f"Cannot load/parse MES report for {go_no}: {joined_errors}")

    @staticmethod
    def _rows_equivalent(left: list[MESJoRow], right: list[MESJoRow]) -> bool:
        return sorted(MESSummaryClient._row_key(row) for row in left) == sorted(
            MESSummaryClient._row_key(row) for row in right
        )

    @staticmethod
    def _row_key(row: MESJoRow) -> tuple[str, str, str, str, str, str, str]:
        return (
            row.jo_no.upper(),
            row.color_code.upper(),
            row.color_name.upper(),
            normalize_number(row.order_qty),
            normalize_number(row.minus_pct),
            normalize_number(row.plus_pct),
            row.fabric_color.upper(),
        )


class WebmergeClient:
    def __init__(self, session: Session | None = None, timeout: int = 45) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_go_color_size_aggregates(self, go_no: str) -> list[WebmergeColorSizeAggregate]:
        response = self._fetch_report(go_no)
        try:
            return parse_webmerge_go_color_size_aggregates(response.text, go_no)
        except ParseError as exc:
            raise MESRequestError(f"Cannot parse GO-level Webmerge size rows for {go_no}: {exc}") from exc

    def fetch_size_rows(self, go_no: str) -> list[WebmergeSizeRow]:
        response = self._fetch_report(go_no)
        try:
            return parse_webmerge_size_rows(response.text, go_no)
        except ParseError as exc:
            raise MESRequestError(f"Cannot parse Webmerge size rows for {go_no}: {exc}") from exc

    def _fetch_report(self, go_no: str) -> Response:
        params = {
            "scNo": go_no,
            "jobno": go_no,
            "sono": "",
            "LanguageFlag": "EN",
            "FTY": "EGV",
            "BPO": "Y",
            "ShowMI": "Y",
            "ShowSJO": "N",
            "DEPT_GROUP": "AllOrder",
            "odbc": "escm_83",
            "Time": "",
            "ftyCd": "EGV",
        }

        try:
            response = self.session.get(WEBMERGE_REPORT_URL, params=params, timeout=self.timeout)
        except RequestException as exc:
            raise MESRequestError(f"Cannot load Webmerge report for {go_no}: {exc}") from exc

        if response.status_code >= 400:
            raise MESRequestError(f"Webmerge report returned HTTP {response.status_code} for {go_no}.")

        GOClient._fix_text_encoding(response, "utf-8")
        return response


class CMQaClient:
    def __init__(self, session: Session | None = None, timeout: int = 45) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_aggregate_rows(self, ppo_no: str) -> list[CmQaAggregateRow]:
        normalized_ppo = ppo_no.strip().upper()
        if not normalized_ppo:
            return []

        params = {
            "PPONO": normalized_ppo,
            "Color": "",
            "InvoiceNo": "",
            "BatchNo": "",
            "ItemDesc": "",
            "Submit": "Query",
        }

        try:
            response = self.session.get(CM_QA_REPORT_URL, params=params, timeout=self.timeout)
        except RequestException as exc:
            raise MESRequestError(f"Cannot load CM QA report for {normalized_ppo}: {exc}") from exc

        if response.status_code >= 400:
            raise MESRequestError(f"CM QA report returned HTTP {response.status_code} for {normalized_ppo}.")

        GOClient._fix_text_encoding(response, "utf-8")
        try:
            return parse_cm_qa_aggregate_rows(response.text, normalized_ppo)
        except ParseError as exc:
            raise MESRequestError(f"Cannot parse CM QA report for {normalized_ppo}: {exc}") from exc


class YPDClient:
    def __init__(
        self,
        username: str,
        password: str,
        session: Session | None = None,
        timeout: int = 45,
    ) -> None:
        if not username.strip():
            raise ValidationError("Account is required.")
        if not password:
            raise ValidationError("Password is required.")
        if HttpNtlmAuth is None:
            raise ValidationError("requests-ntlm is missing. Run: py -3.13 -m pip install -r requirements.txt")

        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.auth = HttpNtlmAuth(username.strip(), password)
        self.session.headers.update({"User-Agent": "GetYY/0.2"})

    def fetch_marker_rows(self, yy_request: YYRequest) -> list[MarkerRow]:
        params = {
            "WorkflowNo": yy_request.workflow_no,
            "ReportType": "1",
        }
        if yy_request.workflow_version_no:
            params["WorkflowVersionNo"] = yy_request.workflow_version_no

        try:
            initial_response = self.session.get(YPD_REPORT_URL, params=params, timeout=self.timeout)
        except RequestException as exc:
            raise YPDRequestError(f"Cannot load YPD page for {yy_request.raw_value}: {exc}") from exc

        self._raise_for_status(initial_response)

        payload = extract_hidden_inputs(initial_response.text)
        if "__VIEWSTATE" not in payload or "__EVENTVALIDATION" not in payload:
            raise YPDRequestError(f"YPD page for {yy_request.raw_value} does not contain render state.")

        payload.update(
            {
                "__EVENTTARGET": "rvReportViewer$ctl09$Reserved_AsyncLoadTarget",
                "__EVENTARGUMENT": "",
                "rvReportViewer$AsyncWait$HiddenCancelField": payload.get(
                    "rvReportViewer$AsyncWait$HiddenCancelField", "False"
                )
                or "False",
                "rvReportViewer$ctl07$collapse": payload.get("rvReportViewer$ctl07$collapse", "false")
                or "false",
                "rvReportViewer$ctl09$VisibilityState$ctl00": "None",
                "rvReportViewer$ctl09$ScrollPosition": "",
                "rvReportViewer$ctl09$ReportControl$ctl04": "FullPage",
                "rvReportViewer$ctl10": payload.get("rvReportViewer$ctl10", ""),
                "rvReportViewer$ctl11": payload.get("rvReportViewer$ctl11", ""),
            }
        )

        try:
            rendered_response = self.session.post(
                YPD_REPORT_URL,
                params=params,
                data=payload,
                timeout=self.timeout,
            )
        except RequestException as exc:
            raise YPDRequestError(f"Cannot render YPD report for {yy_request.raw_value}: {exc}") from exc

        self._raise_for_status(rendered_response)

        try:
            return parse_ypd_marker_rows(rendered_response.text)
        except ParseError as exc:
            raise YPDRequestError(f"Cannot parse Marker YY for {yy_request.raw_value}: {exc}") from exc

    @staticmethod
    def _raise_for_status(response: Response) -> None:
        if response.status_code == 401:
            raise YPDAuthenticationError("Invalid credentials or no access to YPD report.")
        if response.status_code >= 400:
            raise YPDRequestError(f"YPD report returned HTTP {response.status_code}.")


class PPOReportClient:
    def __init__(self, session: Session | None = None, timeout: int = 30) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_woven_ppo_yy_rows(self, ppo_no: str) -> list[WovenPpoYYRow]:
        normalized_ppo = ppo_no.strip().upper()
        response = self._fetch_woven_report(normalized_ppo)
        try:
            return parse_woven_ppo_yy_rows(response.text, normalized_ppo)
        except ParseError as exc:
            raise PPORequestError(f"Cannot parse PPO YY for {normalized_ppo}: {exc}") from exc

    def fetch_woven_part_qty_rows(self, ppo_no: str) -> list[WovenPpoPartQtyRow]:
        normalized_ppo = ppo_no.strip().upper()
        response = self._fetch_woven_report(normalized_ppo)
        try:
            return parse_woven_ppo_part_qty_rows(response.text, normalized_ppo)
        except ParseError as exc:
            raise PPORequestError(f"Cannot parse woven PPO Fabric Lots for {normalized_ppo}: {exc}") from exc

    def fetch_knit_collar_cuff_aggregates(self, ppo_no: str) -> list[KnitPpoBulkColorSizeAggregate]:
        normalized_ppo = ppo_no.strip().upper()
        if not normalized_ppo:
            return []

        viewer_url = (
            f"{KNIT_PPO_BULK_VIEWER_URL}&ppo_no={quote(normalized_ppo)}"
            f"&PRINT_BY={quote(self._default_print_by())}&REPORT_TYPE=FTY"
        )

        try:
            export_html = self._fetch_ssrs_export_html(viewer_url)
        except Exception as exc:
            raise PPORequestError(f"Cannot load Knit PPO bulk report for {normalized_ppo}: {exc}") from exc

        try:
            return parse_knit_ppo_bulk_color_size_aggregates(export_html, normalized_ppo)
        except ParseError as exc:
            raise PPORequestError(f"Cannot parse Knit PPO bulk size rows for {normalized_ppo}: {exc}") from exc

    def _fetch_ssrs_export_html(self, viewer_url: str) -> str:
        script = f"""
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Web
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseDefaultCredentials = $true
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds({max(self.timeout, 30)})
$viewerUrl = '{viewer_url}'
$viewerHtml = $client.GetStringAsync($viewerUrl).GetAwaiter().GetResult()
$match = [regex]::Match($viewerHtml, 'ExportUrlBase\":\"([^\"]+)')
if (-not $match.Success) {{
    throw 'ExportUrlBase was not found in the ReportViewer response.'
}}
$exportBase = [System.Web.HttpUtility]::HtmlDecode($match.Groups[1].Value)
$exportBase = $exportBase -replace '\\u0026', '&'
if ($exportBase.StartsWith('/')) {{
    $exportBase = '{KNIT_PPO_BULK_HOST}' + $exportBase
}}
$exportUrl = $exportBase + 'HTML4.0'
$bytes = $client.GetByteArrayAsync($exportUrl).GetAwaiter().GetResult()
[Convert]::ToBase64String($bytes)
"""
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-EncodedCommand", self._encode_powershell(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=max(self.timeout * 2, 60),
                check=False,
            )
        except FileNotFoundError as exc:
            raise PPORequestError("PowerShell is required to load the Knit PPO bulk report.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PPORequestError("Timed out while loading the Knit PPO bulk report.") from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise PPORequestError(stderr or "PowerShell could not load the Knit PPO bulk report.")

        payload = (completed.stdout or "").strip()
        if not payload:
            raise PPORequestError("Knit PPO bulk report returned empty content.")

        return base64.b64decode(payload).decode("utf-8", errors="ignore")

    @staticmethod
    def _encode_powershell(script: str) -> str:
        return base64.b64encode(script.encode("utf-16le")).decode("ascii")

    @staticmethod
    def _default_print_by() -> str:
        return (getpass.getuser() or "GetYY").strip()

    def _fetch_woven_report(self, normalized_ppo: str) -> Response:
        params = {
            "factory": "GEW",
            "SERVER": "Prod",
            "ppoNo": normalized_ppo,
            "PRICE_FLAG": "Y",
        }

        try:
            response = self.session.get(PPO_REPORT_URL, params=params, timeout=self.timeout)
        except RequestException as exc:
            raise PPORequestError(f"Cannot load PPO report for {normalized_ppo}: {exc}") from exc

        if response.status_code >= 400:
            raise PPORequestError(f"PPO report returned HTTP {response.status_code} for {normalized_ppo}.")

        GOClient._fix_text_encoding(response, "windows-1252")
        return response


class PpoDatabaseClient:
    def __init__(self) -> None:
        if pymssql is None:
            raise ValidationError("pymssql is missing. Run: py -m pip install -r requirements.txt")
        if not (SQL_SERVER_HOST and SQL_SERVER_DATABASE and SQL_SERVER_USER and SQL_SERVER_PASSWORD):
            raise ValidationError("Pre-COI SQL credential is not configured.")
        self._cache: dict[str, list[PpoDetailRow]] = {}
        self._aggregate_cache: dict[str, list[PpoColorAggregateRow]] = {}
        self._knit_part_aggregate_cache: dict[str, list[KnitPpoAggregateRow]] = {}
        self._woven_aggregate_cache: dict[str, list[PpoComboAggregateRow]] = {}

    def fetch_details(self, ppo_no: str) -> list[PpoDetailRow]:
        normalized_ppo = ppo_no.strip()
        if not normalized_ppo:
            return []
        if normalized_ppo in self._cache:
            return self._cache[normalized_ppo]

        query = """
        SELECT
            [PPO NO],
            [Order Qty],
            [Combo Code],
            [Combo Name]
        FROM dbo.V_Knit_PPO_Infor
        WHERE [PPO NO] = %(ppo_no)s
        ORDER BY [PPO NO], [Combo Code], [Combo Name], [Order Qty]
        """

        try:
            rows = self._fetch_rows(query, {"ppo_no": normalized_ppo})
        except Exception as exc:  # pragma: no cover - integration path
            raise DatabaseQueryError(f"Cannot query PPO data for {normalized_ppo}: {exc}") from exc

        result = [
            PpoDetailRow(
                ppo_no=str(row[0]).strip(),
                ppo_qty=normalize_number(row[1]),
                combo_code="" if row[2] is None else str(row[2]).strip(),
                combo_name="" if row[3] is None else str(row[3]).strip(),
                fabric_type_code="",
            )
            for row in rows
        ]
        self._cache[normalized_ppo] = result
        return result

    def fetch_color_aggregates(self, ppo_no: str) -> list[PpoColorAggregateRow]:
        normalized_ppo = ppo_no.strip().upper()
        if not normalized_ppo:
            return []
        if normalized_ppo in self._aggregate_cache:
            return self._aggregate_cache[normalized_ppo]

        query = """
        SELECT
            [PPO NO],
            [Fabric Type Code],
            [Combo Code],
            SUM([Order Qty]) AS PPO_QTY
        FROM dbo.V_Knit_PPO_Infor
        WHERE [PPO NO] = %(ppo_no)s
        GROUP BY [PPO NO], [Fabric Type Code], [Combo Code]
        ORDER BY [PPO NO], [Fabric Type Code], [Combo Code]
        """

        try:
            rows = self._fetch_rows(query, {"ppo_no": normalized_ppo})
        except Exception as exc:  # pragma: no cover - integration path
            raise DatabaseQueryError(f"Cannot query PPO aggregate data for {normalized_ppo}: {exc}") from exc

        result = [
            PpoColorAggregateRow(
                ppo_no=str(row[0]).strip(),
                fabric_type_code="" if row[1] is None else str(row[1]).strip().upper(),
                color_code="" if row[2] is None else str(row[2]).strip().upper(),
                ppo_qty=normalize_number(row[3]),
            )
            for row in rows
        ]
        self._aggregate_cache[normalized_ppo] = result
        return result

    def fetch_knit_part_aggregates(self, ppo_no: str) -> list[KnitPpoAggregateRow]:
        normalized_ppo = ppo_no.strip().upper()
        if not normalized_ppo:
            return []
        if normalized_ppo in self._knit_part_aggregate_cache:
            return self._knit_part_aggregate_cache[normalized_ppo]

        query = """
        SELECT
            [PPO NO],
            [Fabric Type Code],
            [Fabric Part],
            [Combo Code],
            [Combo Name],
            SUM([Order Qty]) AS PPO_QTY
        FROM dbo.V_Knit_PPO_Infor
        WHERE [PPO NO] = %(ppo_no)s
        GROUP BY [PPO NO], [Fabric Type Code], [Fabric Part], [Combo Code], [Combo Name]
        ORDER BY [PPO NO], [Fabric Type Code], [Fabric Part], [Combo Code], [Combo Name]
        """

        try:
            rows = self._fetch_rows(query, {"ppo_no": normalized_ppo})
        except Exception as exc:  # pragma: no cover - integration path
            raise DatabaseQueryError(f"Cannot query Knit PPO part aggregate data for {normalized_ppo}: {exc}") from exc

        result = [
            KnitPpoAggregateRow(
                ppo_no=str(row[0]).strip(),
                fabric_type_code="" if row[1] is None else str(row[1]).strip().upper(),
                fabric_part="" if row[2] is None else str(row[2]).strip().upper(),
                combo_code="" if row[3] is None else str(row[3]).strip(),
                combo_name="" if row[4] is None else str(row[4]).strip(),
                ppo_qty=normalize_number(row[5]),
            )
            for row in rows
        ]
        self._knit_part_aggregate_cache[normalized_ppo] = result
        return result

    def fetch_woven_combo_aggregates(self, ppo_no: str) -> list[PpoComboAggregateRow]:
        normalized_ppo = ppo_no.strip().upper()
        if not normalized_ppo:
            return []
        if normalized_ppo in self._woven_aggregate_cache:
            return self._woven_aggregate_cache[normalized_ppo]

        query = """
        SELECT
            [PPO NO],
            [Fabric Type Code],
            [Combo Name],
            SUM([Order Qty]) AS PPO_QTY
        FROM dbo.V_Woven_PPO_Infor
        WHERE [PPO NO] = %(ppo_no)s
        GROUP BY [PPO NO], [Fabric Type Code], [Combo Name]
        ORDER BY [PPO NO], [Fabric Type Code], [Combo Name]
        """

        try:
            rows = self._fetch_rows(query, {"ppo_no": normalized_ppo})
        except Exception as exc:  # pragma: no cover - integration path
            raise DatabaseQueryError(f"Cannot query woven PPO aggregate data for {normalized_ppo}: {exc}") from exc

        result = [
            PpoComboAggregateRow(
                ppo_no=str(row[0]).strip(),
                fabric_type_code="" if row[1] is None else str(row[1]).strip().upper(),
                combo_name="" if row[2] is None else str(row[2]).strip(),
                ppo_qty=normalize_number(row[3]),
            )
            for row in rows
        ]
        self._woven_aggregate_cache[normalized_ppo] = result
        return result

    def _fetch_rows(self, query: str, params: dict[str, str]):
        if pymssql is None:  # pragma: no cover - defensive guard
            raise ValidationError("pymssql is missing.")
        sql = query.replace("%(ppo_no)s", "%s")
        with pymssql.connect(
            server=SQL_SERVER_HOST,
            user=SQL_SERVER_USER,
            password=SQL_SERVER_PASSWORD,
            database=SQL_SERVER_DATABASE,
            login_timeout=15,
            timeout=30,
            autocommit=True,
        ) as connection:
            cursor = connection.cursor()
            cursor.execute(sql, (params["ppo_no"],))
            return cursor.fetchall()
