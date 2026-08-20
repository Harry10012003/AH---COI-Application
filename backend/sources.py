from __future__ import annotations

import os
from pathlib import Path

from backend.config.credentials import resolve_credential


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
CACHE_DIR = Path(os.getenv("TEST_CACHE_DIR", str(DATA_DIR / "cache"))).expanduser()
FRONTEND_DIR = PROJECT_DIR / "frontend"
ASSETS_DIR = PROJECT_DIR / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
SAMPLES_DIR = ASSETS_DIR / "samples"

GO_REPORT_BASE = (
    "http://192.168.7.108/GORPT/rptsc.asp"
    "?ODBC=escm_83&GONO={go}&ISTSLMI=Y&showEDP=Y&GOTSFlag=Y&WRFlag=N&ISHsandingFlag=N"
)
PPO_REPORT_BASE = (
    "http://192.168.7.111/eSCMReport/ppoxReport.aspx"
    "?factory=GEW&SERVER=Prod&ppoNo={ppo}&PRICE_FLAG=Y"
)
GW_FRAMESET_URL = "http://192.168.7.70/newweb/gkmis/SaleRsvFabricback2013730/rsvroot.asp"
GW_HTTP_BASE_URL = "http://192.168.7.70/newweb/gkmis/SaleRsvFabricback2013730"
PPO_BROWSE_BASE = "http://192.168.7.70/newweb/gkmis/ppono/PPOBrowse1.asp?Ppono={ppo}"
TENDAM_PPO_STATUS_BASE = "http://192.168.7.70/wmis/ppodquery/ppodquery.asp?checkitype=d&ppo_no={ppo}"

MES_CUTTING_SITES = [
    "http://192.168.152.26/MesReports/Reports/CuttingForecast.aspx?site=EAV",
    "http://192.168.152.26/MesReports/Reports/CuttingForecast.aspx?site=EGV",
]
MES_WIPDATA_URL = "http://192.168.152.2/MES/WIPData.asp"
MES_CUTTING_RPT_URL = "http://192.168.152.2/MES_EAV/CuttingRptForSC.asp?SCNO={go}"
EDGE_CDP_URL = os.getenv("EDGE_CDP_URL", "http://127.0.0.1:9222")
SHAREPOINT_COI_FOLDER_URL = os.getenv(
    "SHAREPOINT_COI_FOLDER_URL",
    "https://esquel-my.sharepoint.com/shared?id=%2Fsites%2FEGV%5FEAV%5FPPC%2FShared%20Documents%2FCOI%20PPC&listurl=https%3A%2F%2Fesquel%2Esharepoint%2Ecom%2Fsites%2FEGV%5FEAV%5FPPC%2FShared%20Documents",
)
ONEDRIVE_COI_FOLDER_PATH = Path(
    os.getenv(
        "ONEDRIVE_COI_FOLDER_PATH",
        r"C:\Users\kiddy.nguyen\OneDrive - Esquel Group\EGV_EAV_PPC - Documents\COI PPC",
    )
).expanduser()

_GW_CREDENTIAL = resolve_credential(
    user_env="GW_LOGIN_USER",
    password_env="GW_LOGIN_PASSWORD",
    target_env="GW_CREDENTIAL_TARGET",
    default_target="TEST_GW_LOGIN",
)
GW_LOGIN_USER = _GW_CREDENTIAL.username
GW_LOGIN_PASSWORD = _GW_CREDENTIAL.password
GW_DEFAULT_FACTORY_FLAGS = ("chkGEK", "chkEGV", "chkEAV")

FABRIC_LEFT_DEFAULT_XLSX = os.getenv(
    "FABRIC_LEFT_DEFAULT_XLSX",
    r"C:\Users\kiddy.nguyen\Desktop\Vai TENDAM.xlsx",
)
FABRIC_UPLOAD_CACHE_JSON = CACHE_DIR / "fabric_stock_cache.json"
MES_CUTTING_CACHE_JSON = CACHE_DIR / "mes_cutting_cache.json"
LIVE_SHEET_UI_CACHE_JSON = CACHE_DIR / "live_sheet_ui_cache.json"

COI_SPEC_XLSX = TEMPLATES_DIR / "FORMAT COI REQUEST.xlsx"
FABRIC_SPEC_XLSX = TEMPLATES_DIR / "FABRIC COTROLLING AND WRITE OFF PROJECT.xlsx"
COI_SAMPLE_XLSX = SAMPLES_DIR / "S25V07971 COI.xlsx"
AUTO_PROJECT_DIR = Path(os.getenv("AUTO_PROJECT_DIR", r"C:\Users\kiddy.nguyen\Desktop\auto"))
AUTO_CUTTING_CACHE_JSON = AUTO_PROJECT_DIR / "data" / "cutting_cache.json"

SQL_SERVER_HOST = os.getenv(
    "SQL_SERVER_HOST",
    "esq-mssql-std-dm.cogfagymhkon.ap-southeast-2.rds.amazonaws.com",
)
SQL_SERVER_DATABASE = os.getenv("SQL_SERVER_DATABASE", "ESQ_DATA")
_SQL_SERVER_CREDENTIAL = resolve_credential(
    user_env="SQL_SERVER_USER",
    password_env="SQL_SERVER_PASSWORD",
    target_env="SQL_SERVER_CREDENTIAL_TARGET",
    default_target="ESQ_LEFTOVER_SQL",
    default_user="longtat",
)
SQL_SERVER_USER = _SQL_SERVER_CREDENTIAL.username
SQL_SERVER_PASSWORD = _SQL_SERVER_CREDENTIAL.password
SQL_SERVER_DRIVER = os.getenv("SQL_SERVER_DRIVER", "pymssql")
SQL_SERVER_TIMEOUT_SEC = int(os.getenv("SQL_SERVER_TIMEOUT_SEC", "15"))
SQL_SERVER_QUERY_TIMEOUT_SEC = int(os.getenv("SQL_SERVER_QUERY_TIMEOUT_SEC", "45"))
SQL_SERVER_ENCRYPT = _env_flag("SQL_SERVER_ENCRYPT", default=False)
SQL_SERVER_TRUST_SERVER_CERTIFICATE = _env_flag("SQL_SERVER_TRUST_SERVER_CERTIFICATE", default=False)
SQL_SERVER_REQUIRE_ENCRYPTION = _env_flag("SQL_SERVER_REQUIRE_ENCRYPTION", default=False)

# Inventory stock is hosted separately from the ESQ_DATA enrichment database.
# Keep its credential target isolated so a stock outage/configuration problem
# cannot be mistaken for a healthy main SQL connection.
STOCK_SQL_SERVER = os.getenv(
    "STOCK_SQL_SERVER",
    "esq-mssql-std-dm.cogfagymhkon.ap-southeast-2.rds.amazonaws.com",
)
STOCK_SQL_DATABASE = os.getenv("STOCK_SQL_DATABASE", "ESCM_EGV_EAV")
STOCK_SQL_SCHEMA = os.getenv("STOCK_SQL_SCHEMA", "invsubmat")
STOCK_SQL_VIEW = os.getenv("STOCK_SQL_VIEW", "V_Inv_Stock_EGV_EAV")
_STOCK_SQL_CREDENTIAL = resolve_credential(
    user_env="STOCK_SQL_USER",
    password_env="STOCK_SQL_PASSWORD",
    target_env="STOCK_SQL_CREDENTIAL_TARGET",
    default_target="COI_STOCK_SQL",
)
STOCK_SQL_USER = _STOCK_SQL_CREDENTIAL.username
STOCK_SQL_PASSWORD = _STOCK_SQL_CREDENTIAL.password
STOCK_SQL_DRIVER = os.getenv("STOCK_SQL_DRIVER", "pymssql")
STOCK_SQL_TIMEOUT_SEC = int(os.getenv("STOCK_SQL_TIMEOUT_SEC", "15"))
STOCK_SQL_QUERY_TIMEOUT_SEC = int(os.getenv("STOCK_SQL_QUERY_TIMEOUT_SEC", "45"))
STOCK_SQL_ENCRYPT = _env_flag("STOCK_SQL_ENCRYPT", default=False)
STOCK_SQL_TRUST_SERVER_CERTIFICATE = _env_flag("STOCK_SQL_TRUST_SERVER_CERTIFICATE", default=False)
STOCK_SQL_REQUIRE_ENCRYPTION = _env_flag("STOCK_SQL_REQUIRE_ENCRYPTION", default=False)

SHIPMENT_SQL_SERVER_HOST = os.getenv("SHIPMENT_SQL_SERVER_HOST", "EGVNT04")
_SHIPMENT_SQL_CREDENTIAL = resolve_credential(
    user_env="SHIPMENT_SQL_SERVER_USER",
    password_env="SHIPMENT_SQL_SERVER_PASSWORD",
    target_env="SHIPMENT_SQL_CREDENTIAL_TARGET",
    default_target="TEST_SHIPMENT_SQL",
)
SHIPMENT_SQL_SERVER_USER = _SHIPMENT_SQL_CREDENTIAL.username
SHIPMENT_SQL_SERVER_PASSWORD = _SHIPMENT_SQL_CREDENTIAL.password
SHIPMENT_SQL_SERVER_DRIVER = os.getenv("SHIPMENT_SQL_SERVER_DRIVER", "pymssql")
SHIPMENT_SQL_SERVER_TIMEOUT_SEC = int(os.getenv("SHIPMENT_SQL_SERVER_TIMEOUT_SEC", "15"))
SHIPMENT_SQL_SERVER_QUERY_TIMEOUT_SEC = int(os.getenv("SHIPMENT_SQL_SERVER_QUERY_TIMEOUT_SEC", "45"))
SHIPMENT_SQL_SERVER_ENCRYPT = _env_flag("SHIPMENT_SQL_SERVER_ENCRYPT", default=False)
SHIPMENT_SQL_SERVER_TRUST_SERVER_CERTIFICATE = _env_flag("SHIPMENT_SQL_SERVER_TRUST_SERVER_CERTIFICATE", default=False)
SHIPMENT_SQL_SERVER_REQUIRE_ENCRYPTION = _env_flag("SHIPMENT_SQL_SERVER_REQUIRE_ENCRYPTION", default=False)
SHIPMENT_SQL_EGV_DATABASE = os.getenv("SHIPMENT_SQL_EGV_DATABASE", "EsquelRptDB")
SHIPMENT_SQL_EAV_DATABASE = os.getenv("SHIPMENT_SQL_EAV_DATABASE", "EsquelEAVRptDB")
SHIPMENT_SQL_EGV_TABLE = os.getenv("SHIPMENT_SQL_EGV_TABLE", "dbo.GAK_ShipmentDetail_EGV")
SHIPMENT_SQL_EAV_TABLE = os.getenv("SHIPMENT_SQL_EAV_TABLE", "dbo.GAK_ShipmentDetail_EAV")


def sql_driver_configuration() -> dict:
    """Expose the actual runtime driver without leaking connection details."""
    configured_main = str(SQL_SERVER_DRIVER or "").strip()
    configured_shipment = str(SHIPMENT_SQL_SERVER_DRIVER or "").strip()
    warnings = []
    configured_stock = str(STOCK_SQL_DRIVER or "").strip()
    for label, configured in (("main", configured_main), ("shipment", configured_shipment), ("stock", configured_stock)):
        if configured and configured.casefold() != "pymssql":
            warnings.append(
                f"{label} SQL is configured as '{configured}', but this application uses pymssql. Set it to pymssql."
            )
    return {
        "runtime_driver": "pymssql",
        "configured_main_driver": configured_main or "pymssql",
        "configured_shipment_driver": configured_shipment or "pymssql",
        "configured_stock_driver": configured_stock or "pymssql",
        "valid": not warnings,
        "warnings": warnings,
    }


def go_report_url(go: str) -> str:
    return GO_REPORT_BASE.format(go=str(go or "").strip().upper())


def ppo_report_url(ppo: str) -> str:
    return PPO_REPORT_BASE.format(ppo=str(ppo or "").strip().upper())


def ppo_browse_url(ppo: str) -> str:
    return PPO_BROWSE_BASE.format(ppo=str(ppo or "").strip().upper())


def tendam_ppo_status_url(ppo: str) -> str:
    return TENDAM_PPO_STATUS_BASE.format(ppo=str(ppo or "").strip().upper())


def mes_cutting_rpt_url(go: str) -> str:
    return MES_CUTTING_RPT_URL.format(go=str(go or "").strip().upper())


def get_source_map(include_sensitive: bool = False) -> dict:
    sql_credentials = {
        "configured": bool(SQL_SERVER_USER and SQL_SERVER_PASSWORD),
        "user": SQL_SERVER_USER if include_sensitive else "<configured>",
        "source": _SQL_SERVER_CREDENTIAL.source,
        "target": _SQL_SERVER_CREDENTIAL.target,
    }
    shipment_credentials = {
        "configured": bool(SHIPMENT_SQL_SERVER_USER and SHIPMENT_SQL_SERVER_PASSWORD),
        "user": SHIPMENT_SQL_SERVER_USER if include_sensitive else "<configured>",
        "source": _SHIPMENT_SQL_CREDENTIAL.source,
        "target": _SHIPMENT_SQL_CREDENTIAL.target,
    }
    stock_credentials = {
        "configured": bool(STOCK_SQL_USER and STOCK_SQL_PASSWORD),
        "user": STOCK_SQL_USER if include_sensitive else "<configured>",
        "source": _STOCK_SQL_CREDENTIAL.source,
        "target": _STOCK_SQL_CREDENTIAL.target,
    }
    return {
        "excel_specs": {
            "coi_format": str(COI_SPEC_XLSX),
            "fabric_left_format": str(FABRIC_SPEC_XLSX),
            "coi_real_sample": str(COI_SAMPLE_XLSX),
            "fabric_default_stock": FABRIC_LEFT_DEFAULT_XLSX,
        },
        "urls": {
            "go_report": GO_REPORT_BASE,
            "ppo_report": PPO_REPORT_BASE,
            "gw_frameset": GW_FRAMESET_URL,
            "gw_http_base": GW_HTTP_BASE_URL,
            "ppo_browse": PPO_BROWSE_BASE,
            "tendam_ppo_status": TENDAM_PPO_STATUS_BASE,
            "mes_cutting_sites": MES_CUTTING_SITES,
            "mes_wipdata": MES_WIPDATA_URL,
            "edge_cdp": EDGE_CDP_URL,
            "sharepoint_coi": SHAREPOINT_COI_FOLDER_URL,
            "onedrive_coi_folder": str(ONEDRIVE_COI_FOLDER_PATH),
        },
        "fabric_excel_columns": {
            "A": "Warehouse",
            "F": "Lot No",
            "G": "PO No",
            "K": "Shade",
            "L": "Combo Name",
            "M": "Fabric Type",
            "V": "Available Qty",
            "W": "UOM",
        },
        "sql_live": {
            "host": SQL_SERVER_HOST,
            "database": SQL_SERVER_DATABASE,
            "driver": SQL_SERVER_DRIVER,
            "timeout_sec": SQL_SERVER_TIMEOUT_SEC,
            "query_timeout_sec": SQL_SERVER_QUERY_TIMEOUT_SEC,
            "encrypted": SQL_SERVER_ENCRYPT,
            "trust_server_certificate": SQL_SERVER_TRUST_SERVER_CERTIFICATE,
            "encryption_required": SQL_SERVER_REQUIRE_ENCRYPTION,
            "credentials": sql_credentials,
        },
        "stock_sql": {
            "host": STOCK_SQL_SERVER,
            "database": STOCK_SQL_DATABASE,
            "schema": STOCK_SQL_SCHEMA,
            "view": STOCK_SQL_VIEW,
            "driver": STOCK_SQL_DRIVER,
            "timeout_sec": STOCK_SQL_TIMEOUT_SEC,
            "query_timeout_sec": STOCK_SQL_QUERY_TIMEOUT_SEC,
            "encrypted": STOCK_SQL_ENCRYPT,
            "trust_server_certificate": STOCK_SQL_TRUST_SERVER_CERTIFICATE,
            "encryption_required": STOCK_SQL_REQUIRE_ENCRYPTION,
            "credentials": stock_credentials,
        },
        "shipment_on_way_sql": {
            "host": SHIPMENT_SQL_SERVER_HOST,
            "driver": SHIPMENT_SQL_SERVER_DRIVER,
            "timeout_sec": SHIPMENT_SQL_SERVER_TIMEOUT_SEC,
            "query_timeout_sec": SHIPMENT_SQL_SERVER_QUERY_TIMEOUT_SEC,
            "encrypted": SHIPMENT_SQL_SERVER_ENCRYPT,
            "trust_server_certificate": SHIPMENT_SQL_SERVER_TRUST_SERVER_CERTIFICATE,
            "encryption_required": SHIPMENT_SQL_SERVER_REQUIRE_ENCRYPTION,
            "credentials": shipment_credentials,
            "egv_database": SHIPMENT_SQL_EGV_DATABASE,
            "egv_table": SHIPMENT_SQL_EGV_TABLE,
            "eav_database": SHIPMENT_SQL_EAV_DATABASE,
            "eav_table": SHIPMENT_SQL_EAV_TABLE,
        },
    }
