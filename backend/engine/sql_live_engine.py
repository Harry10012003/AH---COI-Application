from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

import pymssql
from dbutils.pooled_db import PooledDB

from backend.engine.sql_sources.read_only import assert_read_only_sql
from backend.engine.sql_sources.metrics import query_metrics, timed_execute
from backend.engine.refresh_scheduler import InteractiveGoQueue


class _CursorWrapper:
    def __init__(self, cursor, source_key: str = "main"):
        self._cursor = cursor
        self._source_key = source_key

    def execute(self, operation, params=None):
        assert_read_only_sql(operation)
        if isinstance(operation, str) and "?" in operation:
            operation = operation.replace("?", "%s")
        return timed_execute(
            self._source_key,
            lambda: self._cursor.execute(operation, params),
        )

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class _ConnectionWrapper:
    def __init__(self, conn, source_key: str = "main"):
        self._conn = conn
        self._source_key = source_key

    def cursor(self):
        return _CursorWrapper(self._conn.cursor(), self._source_key)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def timeout(self):
        return getattr(self._conn, "timeout", 0)

    @timeout.setter
    def timeout(self, value):
        self._conn.timeout = value

    def __getattr__(self, name):
        return getattr(self._conn, name)

_pool_main = None
_pool_shipment: dict[str, PooledDB] = {}
_pool_stock = None
_pool_sc_master = None

_CUSTOMER_NAME_CACHE: dict[str, str] = {}
_CUSTOMER_NAME_CACHE_LOCK = threading.Lock()


def _get_main_pool():
    global _pool_main
    if _pool_main is None:
        host = SQL_SERVER_HOST
        port = 1433
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                pass
        _pool_main = PooledDB(
            creator=pymssql,
            maxconnections=20,
            mincached=2,
            maxcached=10,
            blocking=True,
            server=host,
            port=port,
            user=SQL_SERVER_USER,
            password=SQL_SERVER_PASSWORD,
            database=SQL_SERVER_DATABASE,
            timeout=max(SQL_SERVER_TIMEOUT_SEC, SQL_SERVER_QUERY_TIMEOUT_SEC),
            tds_version="7.0",
        )
    return _pool_main


def _get_shipment_pool(database: str):
    if database not in _pool_shipment:
        host = SHIPMENT_SQL_SERVER_HOST
        port = 1433
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                pass
        _pool_shipment[database] = PooledDB(
            creator=pymssql,
            maxconnections=10,
            mincached=1,
            maxcached=5,
            blocking=True,
            server=host,
            port=port,
            user=SHIPMENT_SQL_SERVER_USER,
            password=SHIPMENT_SQL_SERVER_PASSWORD,
            database=database,
            timeout=SHIPMENT_SQL_SERVER_TIMEOUT_SEC,
            tds_version="7.0",
        )
    return _pool_shipment[database]


def _get_stock_pool():
    global _pool_stock
    if _pool_stock is None:
        host = STOCK_SQL_SERVER
        port = 1433
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                pass
        _pool_stock = PooledDB(
            creator=pymssql,
            maxconnections=10,
            mincached=1,
            maxcached=5,
            blocking=True,
            server=host,
            port=port,
            user=STOCK_SQL_USER,
            password=STOCK_SQL_PASSWORD,
            database=STOCK_SQL_DATABASE,
            timeout=max(STOCK_SQL_TIMEOUT_SEC, STOCK_SQL_QUERY_TIMEOUT_SEC),
            tds_version="7.0",
        )
    return _pool_stock

from backend.engine.live_sheet_store import (
    delete_live_sheet_payload,
    live_sheet_store_status,
    load_live_sheet_payload,
    patch_live_sheet_payload_edits,
    save_live_sheet_payload,
)
from backend.engine.color_audit_engine import color_audit_priority_go_nos
from backend.scraper.gw_client import _fetch_go_report_detail, fetch_ppo_fabric_combos
from backend.scraper.mes_client import get_cutting_forecast
from backend.scraper.sample_tracking_client import sample_status_lookup_for_go
from backend.sources import (
    AUTO_CUTTING_CACHE_JSON,
    CACHE_DIR,
    LIVE_SHEET_UI_CACHE_JSON,
    SHIPMENT_SQL_EAV_DATABASE,
    SHIPMENT_SQL_EAV_TABLE,
    SHIPMENT_SQL_EGV_DATABASE,
    SHIPMENT_SQL_EGV_TABLE,
    SHIPMENT_SQL_SERVER_DRIVER,
    SHIPMENT_SQL_SERVER_ENCRYPT,
    SHIPMENT_SQL_SERVER_HOST,
    SHIPMENT_SQL_SERVER_PASSWORD,
    SHIPMENT_SQL_SERVER_QUERY_TIMEOUT_SEC,
    SHIPMENT_SQL_SERVER_REQUIRE_ENCRYPTION,
    SHIPMENT_SQL_SERVER_TIMEOUT_SEC,
    SHIPMENT_SQL_SERVER_TRUST_SERVER_CERTIFICATE,
    SHIPMENT_SQL_SERVER_USER,
    STOCK_SQL_DATABASE,
    STOCK_SQL_DRIVER,
    STOCK_SQL_ENCRYPT,
    STOCK_SQL_PASSWORD,
    STOCK_SQL_QUERY_TIMEOUT_SEC,
    STOCK_SQL_REQUIRE_ENCRYPTION,
    STOCK_SQL_SCHEMA,
    STOCK_SQL_SERVER,
    STOCK_SQL_TIMEOUT_SEC,
    STOCK_SQL_TRUST_SERVER_CERTIFICATE,
    STOCK_SQL_USER,
    STOCK_SQL_VIEW,
    SQL_SERVER_DATABASE,
    SQL_SERVER_DRIVER,
    SQL_SERVER_ENCRYPT,
    SQL_SERVER_HOST,
    SQL_SERVER_PASSWORD,
    SQL_SERVER_QUERY_TIMEOUT_SEC,
    SQL_SERVER_REQUIRE_ENCRYPTION,
    SQL_SERVER_TIMEOUT_SEC,
    SQL_SERVER_TRUST_SERVER_CERTIFICATE,
    SQL_SERVER_USER,
    sql_driver_configuration,
)


_FOC_VIEW_BY_FACTORY = {
    "EAV": "dbo.V_F_RCV_FOC_QTY_FOR_WO_EAV",
    "EGV": "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV",
    "EGM": "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGM",
    "EHV": "dbo.V_F_RCV_FOC_QTY_FOR_WO_EHV",
    "CEG": "dbo.V_F_RCV_FOC_QTY_FOR_WO_CEG",
    "CEK": "dbo.V_F_RCV_FOC_QTY_FOR_WO_CEK",
    "GEG": "dbo.V_F_RCV_FOC_QTY_FOR_WO_GEG",
    "GLG": "dbo.V_F_RCV_FOC_QTY_FOR_WO_GLG",
    "NBO": "dbo.V_F_RCV_FOC_QTY_FOR_WO_NBO",
    "PTX": "dbo.V_F_RCV_FOC_QTY_FOR_WO_PTX",
    "TIL": "dbo.V_F_RCV_FOC_QTY_FOR_WO_TIL",
    "YMG": "dbo.V_F_RCV_FOC_QTY_FOR_WO_YMG",
}
_FABRIC_GRN_FALLBACK_VIEW = "dbo.V_Fabric_GRN_Trans_Data_All_Fty"
# The RDS inventory view is keyed by PPO, fabric type,
# combo and size. Its ``ON_HAND_QTY`` is net of production/stock issues,
# sample/SR issues, returns and adjustments, which is exactly the physical
# quantity COI can allocate. The monthly stock-balance view remains a
# compatibility fallback only: it can lag the latest issue transactions.
_STOCK_BALANCE_VIEW_BY_FACTORY = {
    "EAV": f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}",
    "EGV": f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}",
}
_STOCK_BALANCE_FALLBACK_VIEW_BY_FACTORY = {
    "EAV": "dbo.V_Fabric_Submat_Stock_Data_EGV_EAV",
    "EGV": "dbo.V_Fabric_Submat_Stock_Data_EGV_EAV",
}

_ALLOWED_FACTORIES = ("EGV", "EAV")
_FORMAT_COI_COLUMNS = [
    {"letter": "A", "key": "BRAND", "label": "BRAND", "editable": False, "width": 180, "source": "GO"},
    {"letter": "B", "key": "GO#", "label": "GO#", "editable": False, "width": 120, "source": "SQL"},
    {"letter": "C", "key": "PPO", "label": "PPO", "editable": True, "width": 190, "source": "SQL/UI"},
    {"letter": "D", "key": "Type", "label": "Type", "editable": False, "width": 72, "source": "SQL"},
    {"letter": "E", "key": "COLOR_CODE", "label": "COLOR_CODE", "editable": False, "width": 98, "source": "SQL"},
    {"letter": "F", "key": "COLOR_DESC", "label": "COLOR_DESC", "editable": False, "width": 170, "source": "SQL"},
    {"letter": "G", "key": "FABRIC COLOR (For piecing only)", "label": "FABRIC COLOR (For piecing only)", "editable": False, "width": 210, "source": "SQL"},
    {"letter": "H", "key": "JOB ORDER NO", "label": "JOB ORDER NO", "editable": False, "width": 160, "source": "SQL"},
    {"letter": "H+", "key": "LOT", "label": "LOT", "editable": False, "width": 82, "source": "SQL"},
    {"letter": "H++", "key": "SIZE", "label": "SIZE", "editable": False, "width": 82, "source": "SQL"},
    {"letter": "I", "key": "- %", "label": "- %", "editable": False, "width": 64, "source": "GO/SQL"},
    {"letter": "J", "key": "+%", "label": "+%", "editable": False, "width": 64, "source": "GO/SQL"},
    {"letter": "K", "key": "Qty (pcs)", "label": "Qty", "editable": False, "width": 108, "source": "GO"},
    {"letter": "L", "key": "BUYER_PO_DEL_DATE", "label": "BUYER_PO_DEL_DATE", "editable": False, "width": 138, "source": "GO/SQL"},
    {"letter": "M", "key": "Net YY", "label": "Net YY", "editable": False, "width": 92, "source": "SQL"},
    {"letter": "N", "key": "PPO YY", "label": "PPO YY", "editable": False, "width": 92, "source": "SQL"},
    {"letter": "O", "key": "Marker YY", "label": "Marker YY", "editable": False, "width": 92, "source": "SQL"},
    {"letter": "P", "key": "Required Q'ty (Yds)", "label": "Required Q'ty (Yds)", "editable": False, "width": 120, "source": "Calculated"},
    {"letter": "Q", "key": "Rcv Q'ty (PPO)", "label": "Rcv Q'ty (PPO)", "editable": False, "width": 120, "source": "SQL"},
    {"letter": "Q+", "key": "On The Way Q'ty (Yds)", "label": "On The Way Q'ty (Yds)", "editable": False, "width": 148, "source": "Shipment SQL"},
    {"letter": "R", "key": "Allocate Q'ty (Yds)", "label": "Allocate Q'ty (Lot)", "editable": False, "width": 120, "source": "Calculated"},
    {"letter": "R+", "key": "Shortage Q'ty (Yds)", "label": "Shortage Q'ty (Yds)", "editable": False, "width": 124, "source": "Calculated"},
    {"letter": "S", "key": "AH Allocate Q'ty (yds)", "label": "AH Allocate Q'ty (yds)", "editable": True, "width": 140, "source": "UI"},
    {"letter": "T", "key": "Allocate %", "label": "Allocate %", "editable": False, "width": 92, "source": "Calculated"},
    {"letter": "U", "key": "ETD Fabric", "label": "ETD Fabric", "editable": False, "width": 230, "source": "BE ETA"},
    {"letter": "V", "key": "User Remark", "label": "User Remark", "editable": True, "width": 230, "source": "UI"},
    {"letter": "W", "key": "PPO Order Total (Yds)", "label": "PPO Q'ty", "editable": False, "width": 148, "source": "SQL/PPO"},
    {"letter": "Y", "key": "SAMPLE STATUS", "label": "SAMPLE STATUS", "editable": False, "width": 148, "source": "MES Sample Tracking"},
]
_COI_ETD_FABRIC_FIELD = "ETD Fabric"
_COI_ON_WAY_FIELD = "On The Way Q'ty (Yds)"
_COI_USER_REMARK_FIELD = "User Remark"
_COI_LEGACY_REMARK_FIELD = "Remark"
_COI_PPO_FIELD = "PPO"
_SHEET_EDITABLE_KEYS = {
    _COI_PPO_FIELD,
    "AH Allocate Q'ty (yds)",
    _COI_USER_REMARK_FIELD,
    _COI_ETD_FABRIC_FIELD,
    _COI_LEGACY_REMARK_FIELD,
}
_ALLOCATION_TABLE = "dbo.COI_UI_ALLOCATIONS"
_SNAPSHOT_DB = Path(
    os.getenv("SQL_SNAPSHOT_DB", str(CACHE_DIR / "live_sheet_snapshot_v56.db"))
).expanduser()
# v58: Required quantity is strictly PPO YY-based (except O/F's sanctioned
# fallback) and allocation pools deduplicate physical source combos. Older
# snapshots can therefore overstate demand or available stock.
_SNAPSHOT_PAYLOAD_VERSION = 58
_SNAPSHOT_CACHE_SCHEMA_VERSION = 1
_SNAPSHOT_LEGACY_MIGRATION_KEY = "legacy_snapshot_migration_v1"
_FLATKNIT_SIZE_TYPES = frozenset({"O", "F"})
_FLATKNIT_SIZE_SNAPSHOT_MIGRATION_KEY = "flatknit_size_split_v2"
_FLATKNIT_RECEIVED_SIZE_CONTRACT_VERSION = 1
_STOCK_BALANCE_CONTRACT_VERSION = 1
_AH_ALLOCATE_MODE_REDISTRIBUTE = "redistribute"
_AH_ALLOCATE_MODE_PRESERVE = "preserve"
_SNAPSHOT_META_KEY = "sheet_preload"
_SNAPSHOT_BATCH_SIZE = max(1, int(os.getenv("SNAPSHOT_BATCH_SIZE", "25") or "25"))
_SNAPSHOT_PRIORITY_QUEUE_LIMIT = 3000
_INLINE_SNAPSHOT_MAX_WORKERS = max(
    1,
    int(os.getenv("INLINE_SNAPSHOT_MAX_WORKERS", "2") or "2"),
)
_SNAPSHOT_ACTIVE_REFRESH_SEC = 5
_SNAPSHOT_IDLE_REFRESH_SEC = 5
_SQLITE_STARTUP_WAIT_SEC = int(os.getenv("SQLITE_STARTUP_WAIT_SEC", "45") or "45")
_SQL_SOURCE_CACHE_MAX_AGE_SEC = int(os.getenv("SQL_SOURCE_CACHE_MAX_AGE_SEC", "900") or "900")
_CACHE_READY_STATE = "READY"
_GO_FEED_RECENT_SYNC_LOOKBACK_DAYS = 7
_GO_FEED_RECENT_SYNC_INTERVAL_SEC = max(
    60,
    int(os.getenv("GO_FEED_RECENT_SYNC_INTERVAL_SEC", "300") or "300"),
)
_GO_FEED_FULL_SYNC_INTERVAL_SEC = 6 * 60 * 60
_SNAPSHOT_PRIORITY_SEED_INTERVAL_SEC = 5 * 60
_CACHE_PROFILE_REPAIR_INTERVAL_SEC = 30 * 60
_AUDIT_PRIORITY_GO_LIMIT = 200
_CACHE_STATE_PRIORITY_LIMIT = 240
_UNCACHED_PRIORITY_LIMIT = 1200
_OUTDATED_PRIORITY_LIMIT = 600
_WORKER_LIVE_SOURCE_LOOKBACK_DAYS = 365
_SOURCE_REFRESH_LOOKBACK_DAYS = int(os.getenv("SOURCE_REFRESH_LOOKBACK_DAYS", "90") or "90")
_SOURCE_REFRESH_INTERVAL_SEC = int(os.getenv("SOURCE_REFRESH_INTERVAL_SEC", "15") or "15")
_SOURCE_REFRESH_BATCH_SIZE = int(os.getenv("SOURCE_REFRESH_BATCH_SIZE", "200") or "200")
_SOURCE_REFRESH_QUERY_TIMEOUT_SEC = int(os.getenv("SOURCE_REFRESH_QUERY_TIMEOUT_SEC", "90") or "90")
# Current stock is the allocation authority.  The RDS view can take longer
# than the general SQL timeout for an older PPO, so give this single,
# background-cache query a bounded but sufficient budget instead of treating
# a timeout as zero usable fabric.
_STOCK_BALANCE_QUERY_TIMEOUT_SEC = int(os.getenv("STOCK_BALANCE_QUERY_TIMEOUT_SEC", "120") or "120")
_STOCK_BALANCE_RETRY_TIMEOUT_SEC = int(os.getenv("STOCK_BALANCE_RETRY_TIMEOUT_SEC", "180") or "180")
_PPO_ENRICHMENT_BATCH_SIZE = max(1, int(os.getenv("PPO_ENRICHMENT_BATCH_SIZE", "40") or "40"))
_GO_FEED_PAGE_SIZE = max(100, int(os.getenv("GO_FEED_PAGE_SIZE", "500") or "500"))
_GO_FEED_RECENT_LIMIT = max(100, int(os.getenv("GO_FEED_RECENT_LIMIT", "2000") or "2000"))
_WORKER_LEASE_RETRY_SEC = int(os.getenv("SQL_WORKER_LEASE_RETRY_SEC", "5") or "5")
_WORKER_SQL_BACKOFF_INITIAL_SEC = int(os.getenv("WORKER_SQL_BACKOFF_INITIAL_SEC", "15") or "15")
_WORKER_SQL_BACKOFF_MAX_SEC = int(os.getenv("WORKER_SQL_BACKOFF_MAX_SEC", "300") or "300")
_CACHE_RETRY_HOURS = {
    "WAIT_PPO": 8,
    "WAIT_LOT": 8,
    "WAIT_CUTTING": 8,
    "WAIT_PPO_CUTTING": 6,
    "WAIT_SOURCE": 6,
    "ISSUE": 6,
    "EMPTY": 4,
    "ERROR": 4,
}
_IGNORED_BRAND_NAMES = {
    "THESIS INTERNATIONAL CO. LTD",
}
_IGNORED_CUSTOMER_CODES = {
    "36086",
}
_external_cutting_cache: dict[str, dict] | None = None
_received_rows_cache: dict[tuple[str, tuple[str, ...]], dict] = {}
_received_rows_cache_lock = threading.Lock()
_RECEIVED_ROWS_CACHE_TTL_SEC = 120
_stock_balance_rows_cache: dict[tuple[str, tuple[str, ...]], dict] = {}
_stock_balance_rows_cache_lock = threading.Lock()
_STOCK_BALANCE_ROWS_CACHE_TTL_SEC = 120
_shipment_on_way_cache: dict[tuple[str, tuple[str, ...]], dict] = {}
_shipment_on_way_cache_lock = threading.Lock()
_SHIPMENT_ON_WAY_CACHE_TTL_SEC = 60
_SHIPMENT_ETA_RULE_VERSION = 2
_SHIPMENT_ON_WAY_TOLERANCE_YDS = 1.0
_ACTIVE_READY_SNAPSHOT_REFRESH_SEC = 5 * 60
_snapshot_worker_lock = threading.Lock()
_interactive_go_queue = InteractiveGoQueue(_SNAPSHOT_PRIORITY_QUEUE_LIMIT)
_snapshot_schema_lock = threading.RLock()
_snapshot_schema_ready = False
_worker_process_lease_lock = threading.Lock()
_worker_process_lease_handle = None
_sheet_build_locks_guard = threading.Lock()
_sheet_build_locks: dict[str, threading.RLock] = {}
_sheet_build_context = threading.local()
_sqlite_startup_ready_event = threading.Event()
_go_source_sync_locks_guard = threading.Lock()
_go_source_sync_locks: dict[str, threading.Lock] = {}
_snapshot_worker_state = {
    "running": False,
    "started_at": "",
    "startup_ready": False,
    "startup_ready_at": "",
    "startup_ready_detail": "",
    "startup_ready_error": "",
    "last_cycle_at": "",
    "current_go": "",
    "current_task": "",
    "current_detail": "",
    "last_error": "",
    "last_batch_size": 0,
    "stale_backlog": 0,
    "priority_queue_size": 0,
    "priority_go_nos": [],
    "inline_building_go_nos": [],
    "last_priority_seed_at": "",
    "last_priority_seed_count": 0,
    "last_full_go_feed_sync_at": "",
    "last_full_go_feed_sync_rows": 0,
    "last_cache_profile_repair_at": "",
    "last_cache_profile_repair_count": 0,
    "status_logger_started": False,
    "source_refresh_running": False,
    "source_refresh_thread": None,
    "source_refresh_started_at": "",
    "source_refresh_last_cycle_at": "",
    "source_refresh_last_success_at": "",
    "source_refresh_last_error": "",
    "source_refresh_scope_go_count": 0,
    "source_refresh_scope_ppo_count": 0,
    "source_refresh_changed_go_count": 0,
    "source_refresh_verified_go_count": 0,
    "process_lease_acquired": False,
    "worker_standby": False,
    "lease_monitor_thread": None,
    "lease_monitor_running": False,
    "sql_failure_count": 0,
    "sql_retry_delay_sec": 0,
    "sql_retry_at": "",
    "sql_connectivity_state": "UNKNOWN",
}
_TYPE_DISPLAY_ORDER = {
    "B": 0,
    "L": 1,
    "U": 2,
    "M1": 3,
    "M2": 4,
    "D": 5,
    "I": 6,
    "R": 7,
    "O": 8,
    "F": 9,
}
_SOURCE_REFRESH_STATES = {"WAIT_SOURCE", "WAIT_PPO", "WAIT_LOT", "WAIT_PPO_CUTTING", "ERROR", "EMPTY"}
_SOURCE_REFRESH_FLAGS = {
    "WAIT_SQL_ENRICH",
    "SQL_ENRICH_ERROR",
    "SOURCE_MISMATCH_RECEIVED",
    "SOURCE_DATA_CHANGED",
    "RECEIVED_NOT_FOUND",
    "SHIPMENT_SOURCE_STALE",
    "STOCK_BALANCE_UNAVAILABLE",
}


def _error(message: str, **extra) -> dict:
    return {"ok": False, "error": message, **extra}


def classify_source_error(error: object) -> dict[str, str]:
    """Convert volatile upstream errors into stable, user-safe source states."""
    raw = str(error or "").strip()
    normalized = raw.casefold()
    if "collation conflict" in normalized or "v_escm_order_colorsize_sales" in normalized:
        return {
            "code": "COLLATION_CONFLICT",
            "message": "SQL sales view has a collation conflict; DBA remediation is required.",
        }
    if "shipment" in normalized and ("connection" in normalized or "timeout" in normalized):
        return {
            "code": "SHIPMENT_SQL_UNAVAILABLE",
            "message": "Shipment SQL source is temporarily unavailable; cached COI data is retained.",
        }
    if "stock_sql" in normalized or "current_stock_unavailable" in normalized:
        return {
            "code": "STOCK_SQL_UNAVAILABLE",
            "message": "Inventory stock source is temporarily unavailable; stock allocation is unverified.",
        }
    if any(
        token in normalized
        for token in (
            "not connected",
            "connection timed out",
            "query timed out",
            "timeout",
            "dbprocess is dead",
            "connection settings are missing",
        )
    ):
        return {
            "code": "SQL_UNAVAILABLE",
            "message": "Main SQL source is temporarily unavailable; cached COI data is retained.",
        }
    return {
        "code": "SQL_SOURCE_ERROR",
        "message": "A required SQL source could not be read. Please retry or contact support.",
    }


def _public_source_reason(value: object) -> tuple[str, str]:
    raw = str(value or "").strip()
    normalized = raw.casefold()
    is_database_error = any(
        token in normalized
        for token in ("programmingerror", "collation conflict", "db-lib", "dbprocess", "sql server", "stock_sql", "not connected")
    )
    if is_database_error:
        classification = classify_source_error(raw)
        return classification["code"], classification["message"]
    return "", raw


def _worker_sql_backoff(error: object) -> int:
    classification = classify_source_error(error)
    if classification["code"] not in {"SQL_UNAVAILABLE", "SHIPMENT_SQL_UNAVAILABLE", "STOCK_SQL_UNAVAILABLE"}:
        return _SNAPSHOT_IDLE_REFRESH_SEC
    with _snapshot_worker_lock:
        failures = int(_snapshot_worker_state.get("sql_failure_count") or 0) + 1
        delay = min(
            max(1, _WORKER_SQL_BACKOFF_MAX_SEC),
            max(1, _WORKER_SQL_BACKOFF_INITIAL_SEC) * (2 ** min(failures - 1, 6)),
        )
        _snapshot_worker_state["sql_failure_count"] = failures
        _snapshot_worker_state["sql_retry_delay_sec"] = delay
        _snapshot_worker_state["sql_retry_at"] = (datetime.now() + timedelta(seconds=delay)).isoformat(
            sep=" ", timespec="seconds"
        )
        _snapshot_worker_state["sql_connectivity_state"] = classification["code"]
    return delay


def _clear_worker_sql_backoff() -> None:
    with _snapshot_worker_lock:
        _snapshot_worker_state["sql_failure_count"] = 0
        _snapshot_worker_state["sql_retry_delay_sec"] = 0
        _snapshot_worker_state["sql_retry_at"] = ""
        _snapshot_worker_state["sql_connectivity_state"] = "OK"


def _acquire_worker_process_lease() -> bool:
    """Allow only one application process to run SQL preload/poll workers."""
    global _worker_process_lease_handle
    with _worker_process_lease_lock:
        if _worker_process_lease_handle is not None:
            return True
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        lease_path = CACHE_DIR / "sql_background_workers.lock"
        handle = open(lease_path, "a+b", buffering=0)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            handle.close()
            return False
        _worker_process_lease_handle = handle
        return True


def _sheet_build_lock(go: object) -> threading.RLock:
    go_key = str(go or "").strip().upper()
    with _sheet_build_locks_guard:
        lock = _sheet_build_locks.get(go_key)
        if lock is None:
            lock = threading.RLock()
            _sheet_build_locks[go_key] = lock
        return lock


@contextmanager
def _serialized_sheet_build(go: object):
    """Serialize a GO build in-process and give every nested save one build token."""
    go_key = str(go or "").strip().upper()
    lock = _sheet_build_lock(go_key)
    with lock:
        previous_go = getattr(_sheet_build_context, "go_key", "")
        previous_token = getattr(_sheet_build_context, "started_ns", 0)
        nested = previous_go == go_key and int(previous_token or 0) > 0
        if not nested:
            _sheet_build_context.go_key = go_key
            _sheet_build_context.started_ns = time.time_ns()
        try:
            yield int(getattr(_sheet_build_context, "started_ns", 0) or 0)
        finally:
            if not nested:
                _sheet_build_context.go_key = previous_go
                _sheet_build_context.started_ns = previous_token


def _serialize_sheet_build(function):
    @wraps(function)
    def _wrapped(go, *args, **kwargs):
        with _serialized_sheet_build(go):
            return function(go, *args, **kwargs)

    return _wrapped


def _current_sheet_build_started_ns(go: object) -> int:
    go_key = str(go or "").strip().upper()
    if str(getattr(_sheet_build_context, "go_key", "") or "") == go_key:
        return int(getattr(_sheet_build_context, "started_ns", 0) or 0)
    return time.time_ns()


def _sanitize_limit(value: object, default: int = 200, minimum: int = 20, maximum: int = 1000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _parse_iso_datetime(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _preload_lookback_cutoff() -> datetime:
    return datetime.now() - timedelta(days=_WORKER_LIVE_SOURCE_LOOKBACK_DAYS)


def _go_row_stamp(row: dict | None) -> datetime | None:
    payload = row or {}
    return _parse_iso_datetime(payload.get("modify_date") or payload.get("create_date") or "")


def _go_row_in_preload_window(row: dict | None) -> bool:
    stamp_dt = _go_row_stamp(row)
    if stamp_dt is None:
        return True
    return stamp_dt >= _preload_lookback_cutoff()


def _filter_preload_window_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows or []
        if _go_row_in_preload_window(row)
        and str((row or {}).get("status") or "").strip().upper() != "CANCEL"
    ]


def _normalize_factories(raw: object) -> list[str]:
    if raw is None or raw == "":
        return list(_ALLOWED_FACTORIES)

    tokens: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(item or "") for item in raw]
    else:
        text = str(raw or "")
        text = text.replace(";", ",").replace("|", ",").replace("/", ",")
        tokens = text.split(",")

    normalized = []
    seen = set()
    for token in tokens:
        value = re.sub(r"[^A-Z0-9]", "", str(token or "").upper())
        if not value or value in seen:
            continue
        if value in _ALLOWED_FACTORIES:
            seen.add(value)
            normalized.append(value)

    return normalized or list(_ALLOWED_FACTORIES)


def _normalize_text(value: object) -> str:
    raw = str(value or "").upper().strip()
    raw = raw.replace("\xa0", " ").replace("@", " ")
    raw = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_color_code_key(value: object) -> str:
    raw = str(value or "").upper().strip().replace("\xa0", " ")
    if not raw:
        return ""
    raw = re.sub(r"\s+", "", raw)
    return re.sub(r"[^A-Z0-9.]+", "", raw)


def _is_ignored_brand_name(value: object) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    return any(normalized == _normalize_text(item) for item in _IGNORED_BRAND_NAMES)


def _is_ignored_customer_code(value: object) -> bool:
    return str(value or "").strip() in _IGNORED_CUSTOMER_CODES


def _normalize_combo_key(value: object) -> str:
    return _normalize_text(value).replace(" ", "")


def _collapse_duplicate_combo_prefix(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    # Some warehouse rows accidentally duplicate the same color token:
    # `12@12@C47-...` or even `12@12@12@C47-...`.
    # Collapse them to a stable form so received qty can still match.
    for _ in range(6):
        match = re.match(r"^\s*([^@]+?)\s*@\s*([^@]+?)\s*@\s*(.+?)\s*$", text)
        if not match:
            break
        first = _normalize_text(match.group(1))
        second = _normalize_text(match.group(2))
        if not first or first != second:
            break
        text = f"{match.group(1).strip()}@{match.group(3).strip()}"

    return text


def _combo_match_candidates(value: object) -> list[str]:
    raw = str(value or "").strip()
    candidates: list[str] = []

    raw_variants: list[str] = []
    for variant in (raw, _collapse_duplicate_combo_prefix(raw)):
        normalized_variant = str(variant or "").strip()
        if normalized_variant and normalized_variant not in raw_variants:
            raw_variants.append(normalized_variant)

    for variant in raw_variants:
        for item in (
            variant,
            _extract_color_desc_from_combo(variant),
            _collapse_duplicate_combo_prefix(_extract_color_desc_from_combo(variant)),
        ):
            key = _normalize_combo_key(item)
            if key and key not in candidates:
                candidates.append(key)

        if "@" in variant:
            _prefix, suffix = variant.split("@", 1)
            for suffix_variant in (suffix, _collapse_duplicate_combo_prefix(suffix)):
                suffix_key = _normalize_combo_key(suffix_variant)
                if suffix_key and suffix_key not in candidates:
                    candidates.append(suffix_key)
    return candidates


def _extract_color_code_from_combo(value: object) -> str:
    text = str(value or "").strip()
    match = re.match(r"^\s*(\d+)\s*@", text)
    return match.group(1).lstrip("0") or "0" if match else ""


def _color_lookup_keys_from_combo(value: object) -> list[str]:
    keys: list[str] = []
    for candidate in (
        _extract_color_code_from_combo(value),
        _extract_color_token_from_combo(value),
        _extract_color_desc_from_combo(value),
        _canonical_combo_color_key(value),
    ):
        for key in _color_code_lookup_keys(candidate):
            if key and key not in keys:
                keys.append(key)
    return keys


def _extract_color_token_from_combo(value: object) -> str:
    text = str(value or "").strip()
    match = re.match(r"^\s*([^@]+?)\s*@", text)
    return str(match.group(1) if match else "").strip()


def _extract_color_desc_from_combo(value: object) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return ""
    _prefix, suffix = text.split("@", 1)
    return str(suffix or "").strip()


def _display_color_code(row: dict) -> str:
    color_raw = str(row.get("COLOR_CODE") or "").strip()
    combo_token = _extract_color_token_from_combo(
        row.get("FABRIC_COMBO") or row.get("FABRIC COLOR (For piecing only)")
    )
    if combo_token and ("," in color_raw or not color_raw):
        return combo_token
    return color_raw or combo_token


def _display_color_sort_key(value: object) -> tuple[int, int, str]:
    text = str(value or "").strip()
    if not text:
        return (2, 999999, "")
    token = text.split(",", 1)[0].strip()
    if token.isdigit():
        return (0, int(token), token.zfill(4))
    return (1, 999999, token.upper())


def _color_code_lookup_keys(value: object) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    keys: list[str] = []
    parts = [part.strip() for part in re.split(r"\s*,\s*", raw) if part.strip()] if "," in raw else [raw]
    for part in parts:
        variants = [
            _normalize_color_code_key(part),
            _normalize_text(part).replace(" ", ""),
        ]
        if str(part or "").strip().isdigit():
            variants.append(str(part or "").strip().lstrip("0") or "0")
        for item in variants:
            key = str(item or "").strip().upper()
            if key and key not in keys:
                keys.append(key)
    return keys


def _expand_multicolor_fabric_rows(rows: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    for item in rows or []:
        raw_codes = str(item.get("color_code") or "").strip()
        code_parts = [part.strip() for part in re.split(r"\s*,\s*", raw_codes) if part.strip()]
        if len(code_parts) <= 1:
            expanded.append(item)
            continue
        for code_part in code_parts:
            cloned = dict(item)
            cloned["color_code"] = code_part
            expanded.append(cloned)
    return expanded


def _type_sort_key(value: object) -> tuple[int, str]:
    token = str(value or "").strip().upper()
    return (_TYPE_DISPLAY_ORDER.get(token, 99), token)


def _sorted_type_totals(payload: dict[str, float]) -> dict[str, float]:
    return {
        str(type_key): round(_to_float(total), 3)
        for type_key, total in sorted((payload or {}).items(), key=lambda item: _type_sort_key(item[0]))
        if str(type_key or "").strip() and abs(_to_float(total)) > 0.0004
    }


def _ppo_family_prefix(value: object) -> str:
    ppo_no = str(value or "").strip().upper()
    if not ppo_no:
        return ""
    match = re.match(r"^[A-Z]+", ppo_no)
    return match.group(0) if match else ppo_no[:4]


def _ppo_allocation_family_key(value: object) -> str:
    ppo_no = str(value or "").strip().upper()
    if not ppo_no:
        return ""
    stripped = re.sub(r"[A-Z]+$", "", ppo_no)
    if stripped and stripped != ppo_no and any(ch.isdigit() for ch in stripped):
        return stripped
    return ppo_no


def _fabric_type_from_part(value: object) -> str:
    token = _normalize_text(value)
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


def _fabric_type_from_grn_identity(value: object, ppo_no: object = "") -> str:
    text = str(value or "").strip().upper()
    ppo_key = str(ppo_no or "").strip().upper()
    if not text:
        return ""
    if ppo_key and text.startswith(f"{ppo_key}-"):
        rest = text[len(ppo_key) + 1 :]
        match = re.match(r"^([A-Z0-9]+)-", rest)
        if match:
            return _normalize_sql_fabric_type_code(match.group(1))
    match = re.match(r"^[A-Z0-9]+-([A-Z0-9]+)-", text)
    if match:
        return _normalize_sql_fabric_type_code(match.group(1))
    return ""


def _fabric_type_hint_from_remark(value: object) -> str:
    token = _normalize_text(value)
    if not token:
        return ""
    if "RIB" in token:
        return "R"
    if "NECK" in token or "BIND" in token or "DAY VIEN" in token or "VIEN CO" in token:
        return "M1"
    return ""


def _normalize_sql_fabric_type_code(value: object) -> str:
    token = str(value or "").strip().upper()
    if token == "BD":
        return "B"
    return token


def _fabric_type_lookup_candidates(value: object) -> list[str]:
    token = _normalize_sql_fabric_type_code(value)
    if not token:
        return []
    aliases = {
        "L": ("O",),
        "U": ("F",),
        "R": ("M1",),
        "I": ("M2",),
        "M1": ("R",),
        "M2": ("I",),
    }
    candidates = [token, *aliases.get(token, ())]
    output: list[str] = []
    for item in candidates:
        item_key = _normalize_sql_fabric_type_code(item)
        if item_key and item_key not in output:
            output.append(item_key)
    return output


def _format_fabric_part_with_type(value: object) -> str:
    part = str(value or "").strip()
    fabric_type = _fabric_type_from_part(part)
    if not part:
        return fabric_type
    if fabric_type and not re.search(rf"\s-\s{re.escape(fabric_type)}$", part, flags=re.IGNORECASE):
        return f"{part} - {fabric_type}"
    return part


def _fabric_color_tokens(color_code: object, combo_name: object) -> set[str]:
    combo_color = _extract_color_code_from_combo(combo_name)
    tokens: set[str] = set()
    if combo_color:
        tokens.add(combo_color.lstrip("0") or "0")
        return tokens

    text = str(color_code or "").strip()
    for part in re.split(r"[,;/\s]+", text):
        token = str(part or "").strip()
        if not token:
            continue
        if token.isdigit():
            tokens.add(token.lstrip("0") or "0")
        else:
            tokens.add(token.upper())
    return tokens


def _to_float(value: object) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("%", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_allowance_pct(*values: object, default: float = 0.0) -> float:
    for value in values:
        parsed = _to_optional_float(value)
        if parsed is not None:
            return parsed
    return default


def _display_received_qty(item: dict | None) -> float:
    # FOC is already physically available for COI allocation, so UI received
    # quantity follows warehouse available quantity: received + FOC.
    return _to_float((item or {}).get("received_qty")) + _to_float((item or {}).get("foc_qty"))


def _display_stock_on_hand_qty(item: dict | None) -> float:
    """Physical warehouse balance available for COI allocation.

    A negative inventory balance is kept in source diagnostics but cannot be
    allocated.  Do not subtract ``allocated_qty``/``reserved_qty`` here: they
    are WMS planning reservations, not fabric physically issued to stock or
    SR/sample sewing.
    """
    return max(_to_float((item or {}).get("on_hand_qty")), 0.0)


def _system_allocation_available_qty(
    stock_on_hand_qty: object,
    on_way_qty: object,
    stock_balance_complete: bool,
) -> float:
    """Return system-allocatable quantity only with a verified stock balance.

    Receipt is an audit/display value, not a fallback for physical stock.  If
    both current stock sources fail, returning zero avoids allocating fabric
    that may already have been issued to stock or SR/sample sewing.
    """
    if not stock_balance_complete:
        return 0.0
    return max(_to_float(stock_on_hand_qty), 0.0) + max(_to_float(on_way_qty), 0.0)


def _display_shipment_qty(item: dict | None) -> float:
    # Shipment qty includes FOC in GAK shipment detail; UI on-way/allocation ignores FOC.
    return max(_to_float((item or {}).get("shipment_qty")) - _to_float((item or {}).get("foc_qty")), 0.0)


def _sheet_cutting_priority(item: dict | None) -> int:
    return 0 if str((item or {}).get("CUTTING STATUS") or "").strip().upper() == "CUTTED" else 1


def _required_qty_from_ppo_yy(
    garment_qty: object,
    ppo_yy: object,
    fallback_yy: object = 0.0,
    *,
    allow_flatknit_fallback: bool = False,
) -> float:
    """Calculate COI fabric requirement from PPO YY, never from Net/Marker YY.

    PPC purchases fabric against PPO YY.  Net/Marker YY must never replace a
    missing PPO YY for body fabric.  Only O/F flatknit rows may use their
    neutral marker value (normally 1.0) when the source genuinely has no PPO
    YY, because those pieces are purchased and received by garment size.
    """
    qty = max(_to_float(garment_qty), 0.0)
    yy = _to_float(ppo_yy)
    if yy <= 0 and allow_flatknit_fallback:
        yy = _to_float(fallback_yy)
    return qty * max(yy, 0.0)


def _normalize_size_code(value: object) -> str:
    token = re.sub(r"\s+", "", str(value or "").strip().upper())
    aliases = {"1XL": "XL", "2XL": "XXL", "3XL": "XXXL", "4XL": "XXXXL"}
    return aliases.get(token, token)


def _aggregate_received_rows(rows: list[dict]) -> list[dict]:
    totals: dict[tuple[str, str, str], dict] = {}
    for item in rows or []:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        combo_name = str(item.get("combo_name") or "").strip()
        if not ppo_no or not fabric_type or not combo_name:
            continue
        key = (ppo_no, fabric_type, combo_name)
        current = totals.setdefault(
            key,
            {
                "ppo_no": ppo_no,
                "fabric_type": fabric_type,
                "combo_name": combo_name,
                "received_qty": 0.0,
                "foc_qty": 0.0,
            },
        )
        current["received_qty"] += _to_float(item.get("received_qty"))
        current["foc_qty"] += _to_float(item.get("foc_qty"))
    return [totals[key] for key in sorted(totals)]


def _distribute_flatknit_total_by_size(
    total_qty: object,
    qty_by_size_key: dict[tuple, object],
) -> dict[tuple, float]:
    """Split an aggregate O/F quantity across size groups without duplication.

    The shipment source does not carry a garment size, whereas O/F warehouse
    receipts do.  A shipment total must therefore be distributed over the
    garment-size demand; assigning the whole value to every size inflates both
    on-way and allocation quantities.
    """
    total = round(max(_to_float(total_qty), 0.0), 3)
    keys = sorted(
        [key for key, value in (qty_by_size_key or {}).items() if _to_float(value) > 0],
        key=lambda item: tuple(str(part) for part in item),
    )
    if total <= 0 or not keys:
        return {key: 0.0 for key in (qty_by_size_key or {})}

    denominator = sum(max(_to_float(qty_by_size_key.get(key)), 0.0) for key in keys)
    if denominator <= 0:
        return {key: 0.0 for key in (qty_by_size_key or {})}

    distributed: dict[tuple, float] = {key: 0.0 for key in (qty_by_size_key or {})}
    assigned = 0.0
    for index, key in enumerate(keys):
        if index == len(keys) - 1:
            amount = round(max(total - assigned, 0.0), 3)
        else:
            amount = round(total * max(_to_float(qty_by_size_key.get(key)), 0.0) / denominator, 3)
            amount = min(amount, max(total - assigned, 0.0))
        distributed[key] = amount
        assigned += amount
    return distributed


def _to_int(value: object) -> int:
    return int(round(_to_float(value)))


def _parse_manual_allocate_input(value: object) -> tuple[bool, float | None, str]:
    if value is None:
        return True, None, ""
    if isinstance(value, Decimal):
        return True, float(value), ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
            return False, None, "AH Allocate must be a finite number"
        return True, float(value), ""
    raw = str(value or "").strip()
    if raw == "":
        return True, None, ""
    normalized = raw.replace(",", "")
    if not re.fullmatch(r"[+\-]?\d+(?:\.\d+)?", normalized):
        return False, None, "AH Allocate supports only numeric values or resolved formulas"
    try:
        return True, float(normalized), ""
    except (TypeError, ValueError):
        return False, None, "AH Allocate value is invalid"


def _normalize_manual_allocation_mode(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"preserve", "keep", "hold", "no_redistribute", "no-redistribute", "manual_hold"}:
        return _AH_ALLOCATE_MODE_PRESERVE
    return _AH_ALLOCATE_MODE_REDISTRIBUTE


def _to_jsonable(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _load_external_cutting_cache() -> dict[str, dict]:
    global _external_cutting_cache
    if _external_cutting_cache is not None:
        return _external_cutting_cache

    try:
        if AUTO_CUTTING_CACHE_JSON.exists():
            payload = json.loads(AUTO_CUTTING_CACHE_JSON.read_text(encoding="utf-8"))
            _external_cutting_cache = payload if isinstance(payload, dict) else {}
        else:
            _external_cutting_cache = {}
    except Exception:
        _external_cutting_cache = {}
    return _external_cutting_cache


def _load_local_edit_cache() -> dict:
    try:
        if LIVE_SHEET_UI_CACHE_JSON.exists():
            payload = json.loads(LIVE_SHEET_UI_CACHE_JSON.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _write_local_edit_cache(payload: dict) -> None:
    LIVE_SHEET_UI_CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    LIVE_SHEET_UI_CACHE_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _migrate_local_edit_cache_to_sqlite(conn: sqlite3.Connection) -> None:
    """One-time migration from the old JSON edit cache into SQLite."""
    migrated_row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        ("coi_ui_allocations_json_migrated_v1",),
    ).fetchone()
    if migrated_row:
        return

    payload = _load_local_edit_cache()
    params = []
    timestamp = _snapshot_now()
    for raw_go, raw_bucket in payload.items():
        go_key = str(raw_go or "").strip().upper()
        if not go_key or not isinstance(raw_bucket, dict):
            continue
        for raw_row_key, item in raw_bucket.items():
            if not isinstance(item, dict):
                continue
            storage = item.get("storage") if isinstance(item.get("storage"), dict) else {}
            row_key = str(raw_row_key or "").strip()
            if not row_key:
                row_key = _row_storage_key(
                    {
                        "go_no": go_key,
                        "ppo_no": storage.get("ppo_no"),
                        "lot_no": _to_int(storage.get("lot_no")),
                        "jo_no": storage.get("jo_no"),
                        "fabric_type": storage.get("fabric_type"),
                        "color_code": storage.get("color_code"),
                        "fabric_combo": storage.get("fabric_combo"),
                        "size_code": storage.get("size_code"),
                    }
                )
            if not row_key:
                continue
            legacy_etd, legacy_user = _split_legacy_remark(item.get("remark"))
            manual_raw = item.get("manual_allocate_qty")
            params.append(
                (
                    go_key,
                    row_key,
                    str(storage.get("ppo_no") or ""),
                    _to_int(storage.get("lot_no")),
                    str(storage.get("jo_no") or ""),
                    str(storage.get("fabric_type") or ""),
                    str(storage.get("color_code") or ""),
                    str(storage.get("fabric_combo") or ""),
                    None if manual_raw in (None, "") else _to_float(manual_raw),
                    str(item.get("ppo_override") or "").strip().upper(),
                    str(item.get("etd_fabric") or legacy_etd).strip(),
                    str(item.get("user_remark") or legacy_user).strip(),
                    str(item.get("remark") or "").strip(),
                    str(item.get("updated_at") or timestamp).strip() or timestamp,
                )
            )
    if params:
        conn.executemany(
            """
            INSERT OR IGNORE INTO coi_ui_allocations (
                go_no, row_key, ppo_no, lot_no, jo_no, fabric_type, color_code,
                fabric_combo, manual_allocate_qty, ppo_override, etd_fabric, user_remark, remark, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("coi_ui_allocations_json_migrated_v1", timestamp),
    )


def _looks_like_auto_etd_remark(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.search(r"(?i)\b(?:ETA|ETD|EST\s*ETA)\b", text))


def _split_legacy_remark(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if _looks_like_auto_etd_remark(text):
        return text, ""
    return "", text


def _load_local_saved_sheet_state(go: str) -> dict[str, dict]:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {}
    try:
        _ensure_snapshot_tables()
        with _snapshot_connect() as conn:
            rows = conn.execute(
                """
                SELECT row_key, ppo_no, lot_no, jo_no, fabric_type, color_code, fabric_combo,
                       manual_allocate_qty, remark, etd_fabric, user_remark, ppo_override, updated_at
                FROM coi_ui_allocations
                WHERE go_no = ?
                """,
                (go_key,),
            ).fetchall()
        saved: dict[str, dict] = {}
        for row in rows:
            legacy_etd, legacy_user = _split_legacy_remark(row["remark"])
            manual_raw = row["manual_allocate_qty"]
            saved[str(row["row_key"] or "")] = {
                "_storage": {
                    "go_no": go_key,
                    "ppo_no": str(row["ppo_no"] or ""),
                    "lot_no": _to_int(row["lot_no"]),
                    "jo_no": str(row["jo_no"] or ""),
                    "fabric_type": str(row["fabric_type"] or ""),
                    "color_code": str(row["color_code"] or ""),
                    "fabric_combo": str(row["fabric_combo"] or ""),
                },
                "manual_allocate_qty": None if manual_raw in (None, "") else _to_float(manual_raw),
                "remark": str(row["remark"] or "").strip(),
                "etd_fabric": str(row["etd_fabric"] or legacy_etd).strip(),
                "user_remark": str(row["user_remark"] or legacy_user).strip(),
                "ppo_override": str(row["ppo_override"] or "").strip().upper(),
                "updated_at": str(row["updated_at"] or "").strip(),
            }
        return saved
    except Exception:
        payload = _load_local_edit_cache()
        raw_go = payload.get(go_key)
        if not isinstance(raw_go, dict):
            return {}

        saved: dict[str, dict] = {}
        for row_key, item in raw_go.items():
            if not isinstance(item, dict):
                continue
            manual_raw = item.get("manual_allocate_qty")
            legacy_etd, legacy_user = _split_legacy_remark(item.get("remark"))
            saved[str(row_key)] = {
                "_storage": dict(item.get("storage") or {}),
                "manual_allocate_qty": None if manual_raw in (None, "") else _to_float(manual_raw),
                "remark": str(item.get("remark") or "").strip(),
                "etd_fabric": str(item.get("etd_fabric") or legacy_etd).strip(),
                "user_remark": str(item.get("user_remark") or legacy_user).strip(),
                "ppo_override": str(item.get("ppo_override") or "").strip().upper(),
                "updated_at": str(item.get("updated_at") or "").strip(),
            }
        return saved


def _saved_sheet_state_for_storage(persisted_state: dict[str, dict], storage: dict) -> dict:
    if not persisted_state or not storage:
        return {}
    exact = persisted_state.get(_row_storage_key(storage))
    if exact:
        return exact
    target = {
        "ppo_no": str(storage.get("ppo_no") or "").strip().upper(),
        "lot_no": _to_int(storage.get("lot_no")),
        "jo_no": str(storage.get("jo_no") or "").strip().upper(),
        "fabric_type": str(storage.get("fabric_type") or "").strip().upper(),
        "color_code": str(storage.get("color_code") or "").strip().upper(),
        "size_code": str(storage.get("size_code") or "").strip().upper(),
    }
    for item in persisted_state.values():
        item_storage = item.get("_storage") if isinstance(item, dict) else {}
        if not isinstance(item_storage, dict):
            continue
        candidate = {
            "ppo_no": str(item_storage.get("ppo_no") or "").strip().upper(),
            "lot_no": _to_int(item_storage.get("lot_no")),
            "jo_no": str(item_storage.get("jo_no") or "").strip().upper(),
            "fabric_type": str(item_storage.get("fabric_type") or "").strip().upper(),
            "color_code": str(item_storage.get("color_code") or "").strip().upper(),
            "size_code": str(item_storage.get("size_code") or "").strip().upper(),
        }
        if candidate == target:
            return item
    return {}


def _delete_local_saved_sheet_state(go_list: list[str]) -> None:
    clean_keys = [str(item or "").strip().upper() for item in go_list if str(item or "").strip()]
    if not clean_keys:
        return
    try:
        _ensure_snapshot_tables()
        placeholders = ",".join("?" for _ in clean_keys)
        with _snapshot_connect() as conn:
            conn.execute(f"DELETE FROM coi_ui_allocations WHERE go_no IN ({placeholders})", clean_keys)
            conn.commit()
    except Exception:
        pass
    payload = _load_local_edit_cache()
    changed = False
    for go_key in clean_keys:
        if payload.pop(go_key, None) is not None:
            changed = True
    if changed:
        _write_local_edit_cache(payload)


def _save_local_sheet_edits(go: str, edits: list[dict]) -> dict:
    go_key = str(go or "").strip().upper()
    timestamp = datetime.now().isoformat(sep=" ", timespec="seconds")
    sqlite_warning = ""
    try:
        _ensure_snapshot_tables()
        with _snapshot_connect() as conn:
            for edit in edits:
                storage = edit.get("storage") if isinstance(edit.get("storage"), dict) else {}
                row_key = _row_storage_key(
                    {
                        "go_no": go_key,
                        "ppo_no": storage.get("ppo_no"),
                        "lot_no": _to_int(storage.get("lot_no")),
                        "jo_no": storage.get("jo_no"),
                        "fabric_type": storage.get("fabric_type"),
                        "color_code": storage.get("color_code"),
                        "fabric_combo": storage.get("fabric_combo"),
                        "size_code": storage.get("size_code"),
                    }
                )
                if not row_key:
                    continue
                conn.execute(
                    """
                    INSERT INTO coi_ui_allocations (
                        go_no, row_key, ppo_no, lot_no, jo_no, fabric_type,
                        color_code, fabric_combo, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(go_no, row_key) DO UPDATE SET
                        ppo_no = excluded.ppo_no,
                        lot_no = excluded.lot_no,
                        jo_no = excluded.jo_no,
                        fabric_type = excluded.fabric_type,
                        color_code = excluded.color_code,
                        fabric_combo = excluded.fabric_combo,
                        updated_at = excluded.updated_at
                    """,
                    (
                        go_key,
                        row_key,
                        str(storage.get("ppo_no") or ""),
                        _to_int(storage.get("lot_no")),
                        str(storage.get("jo_no") or ""),
                        str(storage.get("fabric_type") or ""),
                        str(storage.get("color_code") or ""),
                        str(storage.get("fabric_combo") or ""),
                        timestamp,
                    ),
                )

                field = str(edit.get("field") or "").strip()
                value = edit.get("value")
                if field == "AH Allocate Q'ty (yds)":
                    parsed_value = edit.get("parsed_manual_allocate")
                    if parsed_value is None and str(value or "").strip() != "":
                        ok_value, parsed_value, _message = _parse_manual_allocate_input(value)
                        if not ok_value:
                            continue
                    conn.execute(
                        """
                        UPDATE coi_ui_allocations
                        SET manual_allocate_qty = ?, updated_at = ?
                        WHERE go_no = ? AND row_key = ?
                        """,
                        (parsed_value, timestamp, go_key, row_key),
                    )
                elif field == _COI_ETD_FABRIC_FIELD:
                    conn.execute(
                        """
                        UPDATE coi_ui_allocations
                        SET etd_fabric = ?, updated_at = ?
                        WHERE go_no = ? AND row_key = ?
                        """,
                        (str(value or "").strip(), timestamp, go_key, row_key),
                    )
                elif field == _COI_PPO_FIELD:
                    conn.execute(
                        """
                        UPDATE coi_ui_allocations
                        SET ppo_override = ?, updated_at = ?
                        WHERE go_no = ? AND row_key = ?
                        """,
                        (str(value or "").strip().upper(), timestamp, go_key, row_key),
                    )
                elif field in {_COI_USER_REMARK_FIELD, _COI_LEGACY_REMARK_FIELD}:
                    text_value = str(value or "").strip()
                    conn.execute(
                        """
                        UPDATE coi_ui_allocations
                        SET user_remark = ?, remark = ?, updated_at = ?
                        WHERE go_no = ? AND row_key = ?
                        """,
                        (text_value, text_value, timestamp, go_key, row_key),
                    )
            conn.commit()
        return {"ok": True, "go": go_key, "saved_count": len(edits), "persistence": "sqlite"}
    except Exception as sqlite_exc:
        # Last-resort fallback only. Normal runtime should use SQLite above.
        sqlite_warning = f"SQLite edit cache unavailable: {sqlite_exc}"

    payload = _load_local_edit_cache()
    go_bucket = payload.get(go_key)
    if not isinstance(go_bucket, dict):
        go_bucket = {}
        payload[go_key] = go_bucket

    for edit in edits:
        storage = edit.get("storage") if isinstance(edit.get("storage"), dict) else {}
        row_key = _row_storage_key(
            {
                "go_no": go_key,
                "ppo_no": storage.get("ppo_no"),
                "lot_no": _to_int(storage.get("lot_no")),
                "jo_no": storage.get("jo_no"),
                "fabric_type": storage.get("fabric_type"),
                "color_code": storage.get("color_code"),
                "fabric_combo": storage.get("fabric_combo"),
            }
        )
        item = go_bucket.get(row_key)
        if not isinstance(item, dict):
            item = {
                "storage": {
                    "go_no": go_key,
                    "ppo_no": str(storage.get("ppo_no") or ""),
                    "lot_no": _to_int(storage.get("lot_no")),
                    "jo_no": str(storage.get("jo_no") or ""),
                    "fabric_type": str(storage.get("fabric_type") or ""),
                    "color_code": str(storage.get("color_code") or ""),
                    "fabric_combo": str(storage.get("fabric_combo") or ""),
                    "size_code": str(storage.get("size_code") or ""),
                },
                "manual_allocate_qty": None,
                "ppo_override": "",
                "remark": "",
                "etd_fabric": "",
                "user_remark": "",
                "updated_at": timestamp,
            }
            go_bucket[row_key] = item

        field = str(edit.get("field") or "").strip()
        value = edit.get("value")
        if field == "AH Allocate Q'ty (yds)":
            parsed_value = edit.get("parsed_manual_allocate")
            if parsed_value is None and str(value or "").strip() != "":
                ok_value, parsed_value, _message = _parse_manual_allocate_input(value)
                if not ok_value:
                    continue
            item["manual_allocate_qty"] = parsed_value
        elif field == _COI_ETD_FABRIC_FIELD:
            item["etd_fabric"] = str(value or "").strip()
        elif field == _COI_PPO_FIELD:
            item["ppo_override"] = str(value or "").strip().upper()
        elif field in {_COI_USER_REMARK_FIELD, _COI_LEGACY_REMARK_FIELD}:
            item["user_remark"] = str(value or "").strip()
            item["remark"] = str(value or "").strip()
        item["updated_at"] = timestamp

    _write_local_edit_cache(payload)
    return {
        "ok": True,
        "go": go_key,
        "saved_count": len(edits),
        "persistence": "json-fallback",
        "warning": sqlite_warning,
    }


def _extract_name_before_code(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.match(r"^(.*?)(?:\s*\(\s*[A-Z0-9]+\s*\))?$", text)
    return str(match.group(1) if match else text).strip()


def _display_customer_name(go_report: dict | None) -> str:
    report = go_report or {}
    for key in ("customer_name_code", "brand_name_code", "buyer"):
        name = _extract_name_before_code(report.get(key))
        if _is_ignored_brand_name(name):
            continue
        if name:
            return name
    return ""


def _display_customer_name_from_sql_rows(rows: list[dict] | None) -> str:
    for row in rows or []:
        for key in (
            "customer_name",
            "CUSTOMER_NAME",
            "brand_name",
            "BRAND_NAME",
            "brand_owner",
            "BRAND_OWNER",
            "customer_label",
            "CUSTOMER_LABEL",
        ):
            name = _extract_name_before_code((row or {}).get(key))
            if _is_ignored_brand_name(name):
                continue
            if name:
                return name
    return ""


def _load_go_customer_name_from_sales(go: str) -> str:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return ""
    with _CUSTOMER_NAME_CACHE_LOCK:
        if go_key in _CUSTOMER_NAME_CACHE:
            return _CUSTOMER_NAME_CACHE[go_key]
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT [Customer Code] FROM [V_GO_Head_Infor] WHERE [GO No] = %s",
                (go_key,),
            )
            row = cursor.fetchone()
            cust_code = str(row[0] or "").strip() if row else ""
            if not cust_code:
                return ""
        global _pool_sc_master
        if _pool_sc_master is None:
            _pool_sc_master = PooledDB(
                creator=pymssql,
                maxconnections=5,
                mincached=1,
                maxcached=3,
                blocking=True,
                server=SHIPMENT_SQL_SERVER_HOST,
                user=SHIPMENT_SQL_SERVER_USER,
                password=SHIPMENT_SQL_SERVER_PASSWORD,
                database="EsquelRptDB",
                timeout=10,
                tds_version="7.0",
            )
        conn2 = _ConnectionWrapper(_pool_sc_master.connection(), "sc-master")
        try:
            cursor2 = conn2.cursor()
            cursor2.execute(
                "SELECT TOP 1 CustomerName FROM [SC_Master] WHERE CustomerCode = %s ORDER BY LastModified DESC",
                (cust_code,),
            )
            row2 = cursor2.fetchone()
            name = str(row2[0] or "").strip() if row2 else ""
            with _CUSTOMER_NAME_CACHE_LOCK:
                _CUSTOMER_NAME_CACHE[go_key] = name
            return name
        finally:
            conn2.close()
    except Exception:
        return ""


def _build_cutting_summary_lookup(summary_rows: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in summary_rows or []:
        color_raw = str(row.get("Color") or "").strip()
        color_text = _normalize_text(color_raw)
        color_desc = _normalize_text(row.get("Color_Desc"))
        numeric_key = ""
        if color_raw.isdigit():
            numeric_key = _normalize_text(color_raw.lstrip("0") or "0")

        for key in (color_text, color_desc, numeric_key):
            if key:
                lookup[key] = row
    return lookup


def _resolve_cutting_summary_row(row: dict, summary_lookup: dict[str, dict]) -> dict:
    color_raw = str(row.get("COLOR_CODE") or "").strip()
    color_desc = str(row.get("COLOR_DESC") or "").strip()
    combo_name = str(row.get("FABRIC_COMBO") or "").strip()
    combo_color = _extract_color_token_from_combo(combo_name)

    candidates = [
        _normalize_text(combo_color),
        _normalize_text(combo_color.lstrip("0") or "0") if combo_color.isdigit() else "",
        _normalize_text(color_raw),
        _normalize_text(color_raw.lstrip("0") or "0") if color_raw.isdigit() else "",
        _normalize_text(color_desc),
        _normalize_text(combo_name),
    ]
    for key in candidates:
        if not key:
            continue
        summary_row = summary_lookup.get(key)
        if not summary_row:
            continue
        return summary_row
    return {}


def _build_cutting_jo_lookup(jo_rows: list[dict]) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    for row in jo_rows or []:
        jo_key = str(row.get("JO") or "").strip().upper()
        color_key = _normalize_text(row.get("Color"))
        if jo_key:
            lookup[(jo_key, color_key)] = row
            lookup[(jo_key, "")] = row
    return lookup


def _parse_due_date(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    parsed = _parse_iso_datetime(raw)
    if parsed is not None:
        return parsed
    return None


def _due_date_sort_key(value: object) -> tuple:
    parsed = _parse_due_date(value)
    if parsed is None:
        return (9999, 12, 31, 23, 59, 59)
    return (parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second)


def _row_storage_key(parts: dict[str, object]) -> str:
    return "|".join(
        [
            str(parts.get("go_no") or "").strip().upper(),
            str(parts.get("ppo_no") or "").strip().upper(),
            str(parts.get("lot_no") or 0),
            str(parts.get("jo_no") or "").strip().upper(),
            str(parts.get("fabric_type") or "").strip().upper(),
            str(parts.get("color_code") or "").strip().upper(),
            str(parts.get("fabric_combo") or "").strip().upper(),
            str(parts.get("size_code") or "").strip().upper(),
        ]
    )


def _load_cutting_payload(go: str, prefer_cache: bool = True, allow_live_query: bool = False) -> dict:
    go_key = str(go or "").strip().upper()
    external_payload = _load_external_cutting_cache().get(go_key)
    if isinstance(external_payload, dict) and external_payload.get("summary"):
        return {**external_payload, "source_label": "auto cutting cache"}

    cached_payload = get_cutting_forecast(go_key, prefer_cache=True, allow_live_query=False)
    if cached_payload.get("summary"):
        source_label = "local MES cache" if cached_payload.get("cached_at") else "MES live"
        return {**cached_payload, "source_label": source_label}

    if allow_live_query:
        live_payload = get_cutting_forecast(go_key, prefer_cache=False, allow_live_query=True)
        if live_payload.get("summary"):
            return {**live_payload, "source_label": "MES live"}
        return {**live_payload, "source_label": "MES unavailable"}

    source_label = "MES cache miss" if prefer_cache else "MES unavailable"
    return {**cached_payload, "source_label": source_label}


def _connect():
    if not SQL_SERVER_HOST or not SQL_SERVER_DATABASE or not SQL_SERVER_USER or not SQL_SERVER_PASSWORD:
        raise RuntimeError("SQL connection settings are missing")
    if SQL_SERVER_REQUIRE_ENCRYPTION and not SQL_SERVER_ENCRYPT:
        raise RuntimeError("Main SQL encryption is required but SQL_SERVER_ENCRYPT is disabled")
    return _ConnectionWrapper(_get_main_pool().connection(), "main")


def _shipment_source_for_factory(factory_code: object) -> tuple[str, str, str]:
    factory = str(factory_code or "").strip().upper()
    if factory == "EAV":
        return SHIPMENT_SQL_EAV_DATABASE, SHIPMENT_SQL_EAV_TABLE, "EAV"
    return SHIPMENT_SQL_EGV_DATABASE, SHIPMENT_SQL_EGV_TABLE, "EGV"


def _connect_shipment(database: str):
    if not SHIPMENT_SQL_SERVER_HOST or not database or not SHIPMENT_SQL_SERVER_USER or not SHIPMENT_SQL_SERVER_PASSWORD:
        raise RuntimeError("Shipment SQL connection settings are missing")
    return _ConnectionWrapper(_get_shipment_pool(database).connection(), f"shipment:{database}")


def _connect_stock():
    if not STOCK_SQL_SERVER or not STOCK_SQL_DATABASE or not STOCK_SQL_USER or not STOCK_SQL_PASSWORD:
        raise RuntimeError("Stock SQL connection settings are missing")
    if STOCK_SQL_REQUIRE_ENCRYPTION and not STOCK_SQL_ENCRYPT:
        raise RuntimeError("Stock SQL encryption is required but STOCK_SQL_ENCRYPT is disabled")
    return _ConnectionWrapper(_get_stock_pool().connection(), "stock-rds")


@contextmanager
def _snapshot_connect():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_SNAPSHOT_DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _snapshot_payload_has_unsplit_flatknit_rows(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        fabric_type = str(row.get("Type") or "").strip().upper()
        size_code = str(row.get("SIZE") or "").strip()
        if fabric_type in _FLATKNIT_SIZE_TYPES and not size_code:
            return True
    return False


def _snapshot_payload_has_flatknit_rows(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(
        isinstance(row, dict)
        and str(row.get("Type") or "").strip().upper() in _FLATKNIT_SIZE_TYPES
        for row in payload.get("rows") or []
    )


def _snapshot_has_flatknit_received_size_contract(payload: object) -> bool:
    if not _snapshot_payload_has_flatknit_rows(payload):
        return True
    snapshot_meta = payload.get("snapshot") if isinstance(payload, dict) else {}
    return int((snapshot_meta or {}).get("flatknit_received_size_contract") or 0) >= _FLATKNIT_RECEIVED_SIZE_CONTRACT_VERSION


def _snapshot_has_stock_balance_contract(payload: object) -> bool:
    """Reject snapshots made before allocation used net warehouse stock."""
    if not isinstance(payload, dict):
        return False
    snapshot_meta = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    return int((snapshot_meta or {}).get("stock_balance_contract") or 0) >= _STOCK_BALANCE_CONTRACT_VERSION


def _invalidate_legacy_unsplit_flatknit_snapshots(conn: sqlite3.Connection) -> int:
    migrated = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (_FLATKNIT_SIZE_SNAPSHOT_MIGRATION_KEY,),
    ).fetchone()
    if migrated:
        return 0

    stale_go_nos: list[tuple[str]] = []
    rows = conn.execute(
        "SELECT go_no, payload_json FROM sheet_snapshots WHERE payload_version = ?",
        (_SNAPSHOT_PAYLOAD_VERSION,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except Exception:
            continue
        if _snapshot_payload_has_unsplit_flatknit_rows(payload):
            go_key = str(row["go_no"] or "").strip().upper()
            if go_key:
                stale_go_nos.append((go_key,))

    if stale_go_nos:
        # -1 deliberately bypasses the version backfill above. The preload
        # worker treats these as uncached and rebuilds each affected GO using
        # the new O/F size expansion rule.
        conn.executemany(
            "UPDATE sheet_snapshots SET payload_version = -1 WHERE go_no = ?",
            stale_go_nos,
        )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_FLATKNIT_SIZE_SNAPSHOT_MIGRATION_KEY, _snapshot_now()),
    )
    return len(stale_go_nos)


def _migrate_compatible_legacy_snapshots(conn: sqlite3.Connection) -> int:
    """Backfill only missing current snapshots from compatible versioned caches.

    Legacy files remain untouched. A payload must already satisfy the current
    contract, so a schema bump never converts an unknown cache into live data.
    """
    migrated = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_SNAPSHOT_LEGACY_MIGRATION_KEY,)
    ).fetchone()
    if migrated:
        return 0

    copied = 0
    for candidate in sorted(_SNAPSHOT_DB.parent.glob("live_sheet_snapshot_v*.db")):
        if candidate.resolve() == _SNAPSHOT_DB.resolve():
            continue
        try:
            source = sqlite3.connect(str(candidate))
            source.row_factory = sqlite3.Row
            integrity = source.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0] or "").lower() != "ok":
                source.close()
                continue
            columns = {
                str(row[1] or "").strip().lower()
                for row in source.execute("PRAGMA table_info(sheet_snapshots)").fetchall()
            }
            required = {
                "go_no", "factory_code", "style_no", "style_desc", "source_modify_date",
                "row_count", "payload_version", "payload_json", "updated_at", "built_from",
                "build_started_ns",
            }
            if not required.issubset(columns):
                source.close()
                continue
            rows = source.execute(
                """
                SELECT go_no, factory_code, style_no, style_desc, source_modify_date,
                       row_count, payload_version, payload_json, updated_at, built_from, build_started_ns
                FROM sheet_snapshots
                WHERE payload_version = ? AND row_count > 0
                """,
                (_SNAPSHOT_PAYLOAD_VERSION,),
            ).fetchall()
            source.close()
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"] or ""))
                except Exception:
                    continue
                if not _snapshot_has_stock_balance_contract(payload) or _snapshot_payload_has_unsplit_flatknit_rows(payload):
                    continue
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO sheet_snapshots (
                        go_no, factory_code, style_no, style_desc, source_modify_date,
                        row_count, payload_version, payload_json, updated_at, built_from, build_started_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["go_no"], row["factory_code"], row["style_no"], row["style_desc"],
                        row["source_modify_date"], row["row_count"], row["payload_version"],
                        row["payload_json"], row["updated_at"], f"legacy-migration:{candidate.name}",
                        row["build_started_ns"],
                    ),
                )
                copied += max(0, int(result.rowcount or 0))
        except (OSError, sqlite3.Error):
            continue
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_SNAPSHOT_LEGACY_MIGRATION_KEY, json.dumps({"at": _snapshot_now(), "copied": copied})),
    )
    return copied


def _initialize_snapshot_tables() -> None:
    with _snapshot_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sheet_snapshots (
                go_no TEXT PRIMARY KEY,
                factory_code TEXT,
                style_no TEXT,
                style_desc TEXT,
                source_modify_date TEXT,
                row_count INTEGER NOT NULL DEFAULT 0,
                payload_version INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                built_from TEXT NOT NULL DEFAULT 'ui',
                build_started_ns INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS go_feed (
                go_no TEXT PRIMARY KEY,
                factory_code TEXT,
                style_no TEXT,
                style_desc TEXT,
                status TEXT,
                season TEXT,
                customer_code TEXT,
                create_date TEXT,
                modify_date TEXT,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS go_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                go_no TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS go_issue_state (
                go_no TEXT PRIMARY KEY,
                issue_count INTEGER NOT NULL DEFAULT 0,
                last_issued_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS go_issue_locks (
                go_no TEXT NOT NULL,
                row_key TEXT NOT NULL,
                locked_allocate_qty REAL NOT NULL DEFAULT 0,
                locked_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_key)
            );

            CREATE TABLE IF NOT EXISTS coi_ui_allocations (
                go_no TEXT NOT NULL,
                row_key TEXT NOT NULL,
                ppo_no TEXT NOT NULL DEFAULT '',
                lot_no INTEGER NOT NULL DEFAULT 0,
                jo_no TEXT NOT NULL DEFAULT '',
                fabric_type TEXT NOT NULL DEFAULT '',
                color_code TEXT NOT NULL DEFAULT '',
                fabric_combo TEXT NOT NULL DEFAULT '',
                ppo_override TEXT NOT NULL DEFAULT '',
                manual_allocate_qty REAL,
                etd_fabric TEXT NOT NULL DEFAULT '',
                user_remark TEXT NOT NULL DEFAULT '',
                remark TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_key)
            );

            CREATE TABLE IF NOT EXISTS go_ui_settings (
                go_no TEXT PRIMARY KEY,
                manual_allocation_mode TEXT NOT NULL DEFAULT 'redistribute',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sql_source_sync (
                source_key TEXT PRIMARY KEY,
                synced_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL DEFAULT '',
                row_count INTEGER NOT NULL DEFAULT 0,
                source_status TEXT NOT NULL DEFAULT 'OK',
                last_error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sql_go_head (
                go_no TEXT PRIMARY KEY,
                style_no TEXT,
                style_desc TEXT,
                season TEXT,
                factory_code TEXT,
                status TEXT,
                customer_code TEXT,
                create_date TEXT,
                modify_date TEXT,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sql_go_colors (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                color_code TEXT,
                color_desc TEXT,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_lots (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                lot_no INTEGER NOT NULL DEFAULT 0,
                jo_no TEXT,
                qty REAL NOT NULL DEFAULT 0,
                buyer_po_del_date TEXT,
                buyer_po_no TEXT,
                short_pct REAL NOT NULL DEFAULT 0,
                over_pct REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_jo_color_qty (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                lot_no INTEGER NOT NULL DEFAULT 0,
                jo_no TEXT,
                color_code TEXT,
                color_desc TEXT,
                buyer_po_del_date TEXT,
                qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_ppo_mapping (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                ppo_no TEXT,
                lot_no INTEGER NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_fabric_rows (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                lot_no INTEGER NOT NULL DEFAULT 0,
                ppo_no TEXT,
                fabric_type TEXT,
                color_code TEXT,
                combo_name TEXT,
                ppo_yy REAL NOT NULL DEFAULT 0,
                marker_yy REAL NOT NULL DEFAULT 0,
                related_jo_list TEXT,
                remarks TEXT,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_bom_rows (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                style_color_code TEXT,
                style_color_desc TEXT,
                fabric_type_cd TEXT,
                fabric_type_desc TEXT,
                combo_name TEXT,
                yy REAL NOT NULL DEFAULT 0,
                marker_yy REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_go_jo_ppo_yy (
                go_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                lot_no INTEGER NOT NULL DEFAULT 0,
                jo_no TEXT,
                ppo_no TEXT,
                ppo_yy REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (go_no, row_index)
            );

            CREATE TABLE IF NOT EXISTS sql_received_foc (
                view_name TEXT NOT NULL,
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                combo_name TEXT NOT NULL,
                received_qty REAL NOT NULL DEFAULT 0,
                foc_qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (view_name, ppo_no, fabric_type, combo_name)
            );

            CREATE TABLE IF NOT EXISTS sql_received_foc_by_size (
                view_name TEXT NOT NULL,
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                combo_name TEXT NOT NULL,
                size_code TEXT NOT NULL DEFAULT '',
                received_qty REAL NOT NULL DEFAULT 0,
                foc_qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (view_name, ppo_no, fabric_type, combo_name, size_code)
            );

            CREATE TABLE IF NOT EXISTS sql_stock_balance (
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                combo_name TEXT NOT NULL,
                size_code TEXT NOT NULL DEFAULT '',
                on_hand_qty REAL NOT NULL DEFAULT 0,
                allocated_qty REAL NOT NULL DEFAULT 0,
                reserved_qty REAL NOT NULL DEFAULT 0,
                source_view TEXT NOT NULL DEFAULT '',
                source_as_of TEXT NOT NULL DEFAULT '',
                synced_at TEXT NOT NULL,
                PRIMARY KEY (ppo_no, fabric_type, combo_name, size_code)
            );

            CREATE TABLE IF NOT EXISTS sql_shipment_on_way (
                source_key TEXT NOT NULL,
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                combo_name TEXT NOT NULL,
                shipment_qty REAL NOT NULL DEFAULT 0,
                foc_qty REAL NOT NULL DEFAULT 0,
                eta_date TEXT,
                ship_type TEXT,
                source_table TEXT,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (source_key, ppo_no, fabric_type, combo_name)
            );

            CREATE TABLE IF NOT EXISTS sql_ppo_order_totals (
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                fabric_part TEXT,
                ppo_order_qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (ppo_no, fabric_type)
            );

            CREATE TABLE IF NOT EXISTS sql_ppo_order_totals_by_color (
                ppo_no TEXT NOT NULL,
                fabric_type TEXT NOT NULL,
                color_key TEXT NOT NULL,
                color_code TEXT,
                fabric_combo TEXT,
                fabric_part TEXT,
                ppo_order_qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (ppo_no, fabric_type, color_key)
            );

            CREATE TABLE IF NOT EXISTS sql_ppo_detail_rows (
                ppo_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                fabric_type TEXT,
                fabric_part TEXT,
                color_code TEXT,
                fabric_combo TEXT,
                fabric_color TEXT,
                fabric_code TEXT,
                gmt_qty REAL NOT NULL DEFAULT 0,
                fabric_total_qty REAL NOT NULL DEFAULT 0,
                ppo_order_qty REAL NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (ppo_no, row_index)
            );
            """
        )
        existing_cols = {
            str(row["name"] or "")
            for row in conn.execute("PRAGMA table_info(coi_ui_allocations)").fetchall()
        }
        if "ppo_override" not in existing_cols:
            conn.execute("ALTER TABLE coi_ui_allocations ADD COLUMN ppo_override TEXT NOT NULL DEFAULT ''")
        snapshot_columns = {
            str(row[1] or "").strip().lower()
            for row in conn.execute("PRAGMA table_info(sheet_snapshots)").fetchall()
        }
        if "payload_version" not in snapshot_columns:
            conn.execute("ALTER TABLE sheet_snapshots ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 0")
        if "build_started_ns" not in snapshot_columns:
            conn.execute(
                "ALTER TABLE sheet_snapshots ADD COLUMN build_started_ns INTEGER NOT NULL DEFAULT 0"
            )
        source_sync_columns = {
            str(row[1] or "").strip().lower()
            for row in conn.execute("PRAGMA table_info(sql_source_sync)").fetchall()
        }
        if "last_checked_at" not in source_sync_columns:
            conn.execute("ALTER TABLE sql_source_sync ADD COLUMN last_checked_at TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE sql_source_sync SET last_checked_at = synced_at WHERE COALESCE(last_checked_at, '') = ''"
        )
        # One-time metadata backfill. Future queue/status checks use this
        # indexed integer instead of parsing hundreds of MB of JSON every loop.
        version_rows = conn.execute(
            "SELECT go_no, payload_json FROM sheet_snapshots WHERE payload_version = 0"
        ).fetchall()
        version_updates: list[tuple[int, str]] = []
        for version_row in version_rows:
            try:
                version_payload = json.loads(str(version_row["payload_json"] or ""))
                version_meta = (
                    version_payload.get("snapshot")
                    if isinstance(version_payload, dict) and isinstance(version_payload.get("snapshot"), dict)
                    else {}
                )
                payload_version = int(version_meta.get("version") or 0)
            except Exception:
                payload_version = -1
            version_updates.append((payload_version, str(version_row["go_no"] or "")))
        if version_updates:
            conn.executemany(
                "UPDATE sheet_snapshots SET payload_version = ? WHERE go_no = ?",
                version_updates,
            )
        _invalidate_legacy_unsplit_flatknit_snapshots(conn)
        existing_columns = {
            str(row[1] or "").strip().lower()
            for row in conn.execute("PRAGMA table_info(go_feed)").fetchall()
        }
        column_defs = {
            "status": "TEXT",
            "season": "TEXT",
            "customer_code": "TEXT",
            "cache_state": "TEXT",
            "cache_flags": "TEXT",
            "cache_reason": "TEXT",
            "snapshot_row_count": "INTEGER NOT NULL DEFAULT 0",
            "snapshot_updated_at": "TEXT",
            "snapshot_built_from": "TEXT",
            "last_build_attempt_at": "TEXT",
            "next_refresh_at": "TEXT",
            "last_build_error": "TEXT",
            "ready_at": "TEXT",
            "snapshot_build_started_ns": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_def in column_defs.items():
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE go_feed ADD COLUMN {column_name} {column_def}")
        jo_color_columns = {
            str(row[1] or "").strip().lower()
            for row in conn.execute("PRAGMA table_info(sql_go_jo_color_qty)").fetchall()
        }
        jo_color_column_defs = {
            "customer_name": "TEXT",
            "brand_name": "TEXT",
            "brand_owner": "TEXT",
            "customer_label": "TEXT",
        }
        for column_name, column_def in jo_color_column_defs.items():
            if column_name not in jo_color_columns:
                conn.execute(f"ALTER TABLE sql_go_jo_color_qty ADD COLUMN {column_name} {column_def}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_go_feed_next_refresh ON go_feed(next_refresh_at, cache_state)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_go_feed_scope_stamp ON go_feed(factory_code, status, modify_date, create_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sql_go_head_stamp ON sql_go_head(go_no, modify_date, create_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sql_go_ppo_by_ppo ON sql_go_ppo_mapping(ppo_no, go_no)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_coi_ui_allocations_go ON coi_ui_allocations(go_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_received_ppo ON sql_received_foc(view_name, ppo_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_received_size_ppo ON sql_received_foc_by_size(view_name, ppo_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_stock_balance_ppo ON sql_stock_balance(ppo_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_shipment_ppo ON sql_shipment_on_way(source_key, ppo_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_ppo_detail_ppo ON sql_ppo_detail_rows(ppo_no)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sheet_snapshots_version ON sheet_snapshots(payload_version, updated_at)"
        )
        _migrate_local_edit_cache_to_sqlite(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("snapshot_cache_schema_version", str(_SNAPSHOT_CACHE_SCHEMA_VERSION)),
        )
        _migrate_compatible_legacy_snapshots(conn)


def _ensure_snapshot_tables() -> None:
    global _snapshot_schema_ready
    if _snapshot_schema_ready:
        return
    with _snapshot_schema_lock:
        if _snapshot_schema_ready:
            return
        _initialize_snapshot_tables()
        _snapshot_schema_ready = True


def _load_go_manual_allocation_mode(go: str) -> str:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _AH_ALLOCATE_MODE_REDISTRIBUTE
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        row = conn.execute(
            "SELECT manual_allocation_mode FROM go_ui_settings WHERE go_no = ?",
            (go_key,),
        ).fetchone()
    if not row:
        return _AH_ALLOCATE_MODE_REDISTRIBUTE
    return _normalize_manual_allocation_mode(row["manual_allocation_mode"])


def _save_go_manual_allocation_mode(go: str, mode: object) -> str:
    go_key = str(go or "").strip().upper()
    normalized_mode = _normalize_manual_allocation_mode(mode)
    if not go_key:
        return normalized_mode
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        conn.execute(
            """
            INSERT INTO go_ui_settings (go_no, manual_allocation_mode, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                manual_allocation_mode = excluded.manual_allocation_mode,
                updated_at = excluded.updated_at
            """,
            (go_key, normalized_mode, _snapshot_now()),
        )
        conn.commit()
    return normalized_mode


def _resolve_go_manual_allocation_mode(go: str, requested_mode: object = None) -> str:
    if requested_mode is not None and str(requested_mode or "").strip():
        return _save_go_manual_allocation_mode(go, requested_mode)
    return _load_go_manual_allocation_mode(go)


def _snapshot_now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _set_snapshot_worker_task(task: str = "", detail: str = "", go: str = "") -> None:
    with _snapshot_worker_lock:
        _snapshot_worker_state["current_task"] = str(task or "")
        _snapshot_worker_state["current_detail"] = str(detail or "")
        if go:
            _snapshot_worker_state["current_go"] = str(go or "").strip().upper()


def _mark_sqlite_startup_ready(detail: str = "") -> None:
    with _snapshot_worker_lock:
        if _snapshot_worker_state.get("startup_ready"):
            return
        _snapshot_worker_state["startup_ready"] = True
        _snapshot_worker_state["startup_ready_at"] = _snapshot_now()
        _snapshot_worker_state["startup_ready_detail"] = str(detail or "")
        _snapshot_worker_state["startup_ready_error"] = ""
    _sqlite_startup_ready_event.set()


def _mark_sqlite_startup_error(error: object) -> None:
    with _snapshot_worker_lock:
        _snapshot_worker_state["startup_ready_error"] = str(error or "")


def wait_sqlite_startup_ready(timeout_sec: int | float | None = None) -> dict:
    ensure_sql_snapshot_worker()
    try:
        wait_seconds = float(_SQLITE_STARTUP_WAIT_SEC if timeout_sec is None else timeout_sec)
    except (TypeError, ValueError):
        wait_seconds = float(_SQLITE_STARTUP_WAIT_SEC)
    wait_seconds = max(0.0, wait_seconds)
    ready = _sqlite_startup_ready_event.wait(wait_seconds)
    status = sql_snapshot_status()
    status["startup_ready"] = bool(ready or status.get("startup_ready"))
    status["startup_wait_timeout_sec"] = wait_seconds
    return status


def _head_source_stamp(head: dict | None) -> str:
    payload = head or {}
    return str(payload.get("modify_date") or payload.get("create_date") or "")


def _snapshot_matches_head(payload: dict | None, head: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    head_stamp = _head_source_stamp(head)
    if not head_stamp:
        return True
    snapshot_head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
    snapshot_stamp = _head_source_stamp(snapshot_head)
    return bool(snapshot_stamp) and snapshot_stamp >= head_stamp


def _cache_color_key(code: object, desc: object) -> str:
    code_text = str(code or "").strip().upper()
    if code_text and code_text not in {"TOTAL", "COLOR TOTAL :"}:
        return code_text.zfill(2) if code_text.isdigit() else code_text
    return _normalize_text(desc)


def _split_cache_flags(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, list):
        return sorted({str(item or "").strip().upper() for item in data if str(item or "").strip()})
    return sorted({part.strip().upper() for part in text.split(",") if part.strip()})


def _encode_cache_flags(flags: list[str]) -> str:
    clean = sorted({str(item or "").strip().upper() for item in (flags or []) if str(item or "").strip()})
    return json.dumps(clean, ensure_ascii=False)


def _next_refresh_at_for_cache_state(state: object) -> str:
    hours = int(_CACHE_RETRY_HOURS.get(str(state or "").strip().upper(), 0) or 0)
    if hours <= 0:
        return ""
    return (datetime.now() + timedelta(hours=hours)).isoformat(sep=" ", timespec="seconds")


def _compare_qty_maps(expected: dict, actual: dict, sample_limit: int = 6) -> tuple[int, list[str]]:
    mismatch_count = 0
    samples: list[str] = []
    for key in sorted(set(expected) | set(actual), key=lambda item: str(item)):
        expected_qty = round(_to_float(expected.get(key)), 3)
        actual_qty = round(_to_float(actual.get(key)), 3)
        if abs(expected_qty - actual_qty) <= 0.001:
            continue
        mismatch_count += 1
        if len(samples) < sample_limit:
            samples.append(f"{key}: {expected_qty}->{actual_qty}")
    return mismatch_count, samples

def _build_sheet_b_qty_maps(rows: list[dict]) -> dict:
    by_jo_color: dict[tuple[str, str], float] = defaultdict(float)
    by_lot_jo_color: dict[tuple[int, str, str], float] = defaultdict(float)
    blank_ppo_rows = 0
    blank_lot_rows = 0
    b_row_count = 0
    for row in rows or []:
        if str(row.get("Type") or "").strip().upper() != "B":
            continue
        b_row_count += 1
        if not str(row.get("PPO") or "").strip():
            blank_ppo_rows += 1
        lot_no = _to_int(row.get("LOT") or ((row.get("_storage") or {}).get("lot_no")))
        if lot_no <= 0:
            blank_lot_rows += 1
        jo_no = str(row.get("JOB ORDER NO") or "").strip().upper()
        color_key = _cache_color_key(row.get("COLOR_CODE"), row.get("COLOR_DESC"))
        qty = _to_float(row.get("Qty (pcs)"))
        if jo_no and color_key:
            by_jo_color[(jo_no, color_key)] += qty
            if lot_no > 0:
                by_lot_jo_color[(lot_no, jo_no, color_key)] += qty
    return {
        "b_row_count": b_row_count,
        "blank_ppo_rows": blank_ppo_rows,
        "blank_lot_rows": blank_lot_rows,
        "by_jo_color": dict(by_jo_color),
        "by_lot_jo_color": dict(by_lot_jo_color),
    }


def _build_sql_b_qty_maps(jo_color_qty_rows: list[dict]) -> dict:
    by_jo_color: dict[tuple[str, str], float] = defaultdict(float)
    by_lot_jo_color: dict[tuple[int, str, str], float] = defaultdict(float)
    for row in jo_color_qty_rows or []:
        lot_no = _to_int(row.get("lot_no"))
        jo_no = str(row.get("jo_no") or "").strip().upper()
        color_key = _cache_color_key(row.get("color_code"), row.get("color_desc"))
        qty = _to_float(row.get("qty"))
        if jo_no and color_key:
            by_jo_color[(jo_no, color_key)] += qty
            if lot_no > 0:
                by_lot_jo_color[(lot_no, jo_no, color_key)] += qty
    return {
        "by_jo_color": dict(by_jo_color),
        "by_lot_jo_color": dict(by_lot_jo_color),
    }


def _summarize_sheet_cache_profile(
    public_rows: list[dict],
    summary: dict,
    jo_color_qty_rows: list[dict],
) -> dict:
    row_count = int((summary or {}).get("rows") or len(public_rows or []))
    sheet_maps = _build_sheet_b_qty_maps(public_rows or [])
    sql_maps = _build_sql_b_qty_maps(jo_color_qty_rows or [])
    jo_color_mismatch_count, jo_color_samples = _compare_qty_maps(
        sql_maps["by_jo_color"],
        sheet_maps["by_jo_color"],
    )
    lot_color_mismatch_count, lot_color_samples = _compare_qty_maps(
        sql_maps["by_lot_jo_color"],
        sheet_maps["by_lot_jo_color"],
    )

    flags: list[str] = []
    reasons: list[str] = []
    if row_count <= 0:
        flags.append("EMPTY_SHEET")
        reasons.append("no rows")
    if sheet_maps["blank_ppo_rows"] > 0:
        flags.append("MISSING_PPO")
        reasons.append(f"blank PPO {sheet_maps['blank_ppo_rows']}")
    if sheet_maps["blank_lot_rows"] > 0:
        flags.append("MISSING_LOT")
        reasons.append(f"blank LOT {sheet_maps['blank_lot_rows']}")
    if jo_color_mismatch_count > 0:
        flags.append("SQL_JO_COLOR_MISMATCH")
        reasons.append(f"sql jo/color mismatch {jo_color_mismatch_count}")
    if lot_color_mismatch_count > 0:
        flags.append("SQL_LOT_COLOR_MISMATCH")
        reasons.append(f"sql lot/color mismatch {lot_color_mismatch_count}")

    state = _CACHE_READY_STATE
    if row_count <= 0:
        state = "EMPTY"
    elif jo_color_mismatch_count > 0 or lot_color_mismatch_count > 0:
        state = "ISSUE"
    elif sheet_maps["blank_ppo_rows"] > 0:
        state = "WAIT_PPO"
    elif sheet_maps["blank_lot_rows"] > 0:
        state = "WAIT_LOT"

    return {
        "state": state,
        "flags": sorted(set(flags)),
        "reason": "; ".join(reasons[:6]),
        "next_refresh_at": _next_refresh_at_for_cache_state(state),
        "row_count": row_count,
        "b_row_count": int(sheet_maps["b_row_count"] or 0),
        "blank_ppo_rows": int(sheet_maps["blank_ppo_rows"] or 0),
        "blank_lot_rows": int(sheet_maps["blank_lot_rows"] or 0),
        "sql_jo_color_mismatch_count": jo_color_mismatch_count,
        "sql_lot_color_mismatch_count": lot_color_mismatch_count,
        "sql_jo_color_mismatch_samples": jo_color_samples,
        "sql_lot_color_mismatch_samples": lot_color_samples,
    }

def _load_ignored_go_nos() -> list[str]:
    placeholders = ",".join("?" for _ in _IGNORED_CUSTOMER_CODES)
    factory_placeholders = ",".join("?" for _ in _ALLOWED_FACTORIES)
    sql = f"""
        SELECT [GO No] AS go_no
        FROM dbo.V_GO_Head_Infor
        WHERE [Factory Code] IN ({factory_placeholders})
          AND [Customer Code] IN ({placeholders})
    """
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, [*_ALLOWED_FACTORIES, *sorted(_IGNORED_CUSTOMER_CODES)])
            return [
                str(row[0] or "").strip().upper()
                for row in cursor.fetchall()
                if str(row[0] or "").strip()
            ]
    except Exception:
        return []


def _purge_go_cache_entries(go_list: list[str]) -> None:
    clean_keys = sorted({str(item or "").strip().upper() for item in go_list if str(item or "").strip()})
    if not clean_keys:
        return
    _ensure_snapshot_tables()
    placeholders = ",".join("?" for _ in clean_keys)
    with _snapshot_connect() as conn:
        conn.execute(f"DELETE FROM sheet_snapshots WHERE go_no IN ({placeholders})", clean_keys)
        conn.execute(f"DELETE FROM go_feed WHERE go_no IN ({placeholders})", clean_keys)
        conn.execute(f"DELETE FROM go_events WHERE go_no IN ({placeholders})", clean_keys)
        conn.commit()
    for go_key in clean_keys:
        try:
            delete_live_sheet_payload(go_key)
        except Exception:
            pass
    _delete_local_saved_sheet_state(clean_keys)


def _load_sheet_snapshot(go: str) -> dict | None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return None
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        snapshot_row = conn.execute(
            """
            SELECT payload_json, payload_version, updated_at, built_from
            FROM sheet_snapshots
            WHERE go_no = ?
            """,
            (go_key,),
        ).fetchone()
    if not snapshot_row:
        return None
    if int(snapshot_row["payload_version"] or 0) != _SNAPSHOT_PAYLOAD_VERSION:
        return None
    try:
        payload = json.loads(str(snapshot_row["payload_json"] or ""))
    except Exception:
        return None
    if isinstance(payload, dict):
        snapshot_meta = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        if int(snapshot_meta.get("version") or 0) != _SNAPSHOT_PAYLOAD_VERSION:
            return None
        if _is_ignored_customer_code((payload.get("head") or {}).get("customer_code")):
            return None
        for payload_row in payload.get("rows") or []:
            if _is_ignored_brand_name((payload_row or {}).get("BRAND")):
                return None
        payload.setdefault("snapshot", {})
        payload["snapshot"].update(
            {
                "served_from_snapshot": True,
                "snapshot_updated_at": str(snapshot_row["updated_at"] or ""),
                "built_from": str(snapshot_row["built_from"] or "snapshot"),
            }
        )
        cache_profile = _load_go_cache_profile(go_key)
        if cache_profile:
            payload["cache_profile"] = cache_profile
        payload.setdefault("sources", {})["edit_persistence"] = "sqlite"
        return payload
    return None


def _payload_is_allowed_for_sheet_cache(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    snapshot_meta = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    if int(snapshot_meta.get("version") or 0) != _SNAPSHOT_PAYLOAD_VERSION:
        return False
    if not _snapshot_has_stock_balance_contract(payload):
        return False
    if _is_ignored_customer_code((payload.get("head") or {}).get("customer_code")):
        return False
    for payload_row in payload.get("rows") or []:
        if _is_ignored_brand_name((payload_row or {}).get("BRAND")):
            return False
    return True


def _load_persisted_sheet_payload(go: str) -> dict | None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return None
    payload = load_live_sheet_payload(go_key)
    if (
        _payload_is_allowed_for_sheet_cache(payload)
        and not _snapshot_payload_has_unsplit_flatknit_rows(payload)
        and _snapshot_has_flatknit_received_size_contract(payload)
        and _snapshot_has_stock_balance_contract(payload)
    ):
        cache_profile = _load_go_cache_profile(go_key)
        if cache_profile:
            payload["cache_profile"] = cache_profile
        payload.setdefault("sources", {})["edit_persistence"] = "sqlite"
        return payload

    legacy_payload = _load_sheet_snapshot(go_key)
    if (
        _payload_is_allowed_for_sheet_cache(legacy_payload)
        and not _snapshot_payload_has_unsplit_flatknit_rows(legacy_payload)
        and _snapshot_has_flatknit_received_size_contract(legacy_payload)
        and _snapshot_has_stock_balance_contract(legacy_payload)
    ):
        try:
            save_live_sheet_payload(go_key, legacy_payload, built_from="legacy-snapshot-backfill")
        except Exception:
            pass
        return legacy_payload
    return None


def _build_pending_sheet_payload(go: str, head: dict | None, cached: dict | None = None) -> dict:
    go_key = str(go or "").strip().upper()
    head_payload = head or {}
    cached_payload = cached if isinstance(cached, dict) and cached.get("ok") else None
    payload = dict(cached_payload or {})
    # A cache miss is intentionally served quickly, but it must not look like a
    # valid empty COI.  Preserve the cache profile so callers can distinguish
    # an in-progress build from a GO whose PPO/fabric data is not available.
    if not isinstance(payload.get("cache_profile"), dict):
        cache_profile = _load_go_cache_profile(go_key)
        if cache_profile:
            payload["cache_profile"] = cache_profile
    payload.update(
        {
            "ok": True,
            "go": go_key,
            "factory_code": str(
                payload.get("factory_code")
                or head_payload.get("factory_code")
                or ""
            ).strip().upper(),
            "style_no": str(payload.get("style_no") or head_payload.get("style_no") or "").strip(),
            "style_desc": str(payload.get("style_desc") or head_payload.get("style_desc") or "").strip(),
            "head": dict(payload.get("head") or head_payload or {}),
            "sheet": dict(payload.get("sheet") or {"name": "FORMAT COI REQUEST", "template": "FORMAT COI REQUEST.xlsx"}),
            "columns": list(payload.get("columns") or _FORMAT_COI_COLUMNS),
            "rows": list(payload.get("rows") or []),
            "row_count": int(payload.get("row_count") or len(payload.get("rows") or [])),
            "summary": dict(
                payload.get("summary")
                or {
                    "row_count": int(payload.get("row_count") or len(payload.get("rows") or [])),
                    "required_qty": 0.0,
                    "received_qty": 0.0,
                    "system_allocate_qty": 0.0,
                    "effective_allocate_qty": 0.0,
                    "shortage_qty": 0.0,
                    "coverage_pct": 0.0,
                    "manual_rows": 0,
                    "cutted_rows": 0,
                }
            ),
            "sources": dict(payload.get("sources") or {}),
            "timestamp": _snapshot_now(),
            "pending": True,
        }
    )
    snapshot_meta = dict(payload.get("snapshot") or {})
    snapshot_meta.update(
        {
            "version": _SNAPSHOT_PAYLOAD_VERSION,
            "served_from_snapshot": bool(cached_payload),
            "snapshot_updated_at": str(snapshot_meta.get("snapshot_updated_at") or ""),
            "snapshot_pending": True,
            "snapshot_stale": bool(cached_payload and not _snapshot_matches_head(cached_payload, head_payload)),
            "built_from": str(snapshot_meta.get("built_from") or ("snapshot" if cached_payload else "pending")),
        }
    )
    payload["snapshot"] = snapshot_meta
    return payload


def _load_go_cache_profile(go: str) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {}
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        row = conn.execute(
            """
            SELECT cache_state, cache_flags, cache_reason, snapshot_row_count, snapshot_updated_at,
                   snapshot_built_from, last_build_attempt_at, next_refresh_at, last_build_error, ready_at
            FROM go_feed
            WHERE go_no = ?
            """,
            (go_key,),
        ).fetchone()
    if not row:
        return {}
    source_reason_code, public_reason = _public_source_reason(
        row["cache_reason"] or row["last_build_error"]
    )
    return {
        "state": str(row["cache_state"] or "").strip().upper(),
        "flags": _split_cache_flags(row["cache_flags"]),
        "reason": public_reason,
        "source_reason_code": source_reason_code,
        "row_count": int(row["snapshot_row_count"] or 0),
        "snapshot_updated_at": str(row["snapshot_updated_at"] or ""),
        "built_from": str(row["snapshot_built_from"] or ""),
        "last_build_attempt_at": str(row["last_build_attempt_at"] or ""),
        "next_refresh_at": str(row["next_refresh_at"] or ""),
        "last_build_error": _public_source_reason(row["last_build_error"])[1],
        "ready_at": str(row["ready_at"] or ""),
    }


def _is_cache_refresh_due(profile: dict | None) -> bool:
    payload = profile if isinstance(profile, dict) else {}
    state = str(payload.get("state") or "").strip().upper()
    if not state or state == _CACHE_READY_STATE:
        return False
    due_at = _parse_iso_datetime(payload.get("next_refresh_at"))
    return due_at is None or due_at <= datetime.now()


def _cache_profile_requires_source_refresh(profile: dict | None) -> bool:
    payload = profile if isinstance(profile, dict) else {}
    state = str(payload.get("state") or "").strip().upper()
    if state in _SOURCE_REFRESH_STATES:
        return True
    flags = {str(item or "").strip().upper() for item in (payload.get("flags") or [])}
    return bool(flags & _SOURCE_REFRESH_FLAGS)


def _active_ready_snapshot_refresh_due(profile: dict | None) -> bool:
    payload = profile if isinstance(profile, dict) else {}
    state = str(payload.get("state") or "").strip().upper()
    if state != _CACHE_READY_STATE:
        return False
    updated_at = _parse_iso_datetime(payload.get("snapshot_updated_at"))
    if updated_at is None:
        return True
    return (datetime.now() - updated_at).total_seconds() >= _ACTIVE_READY_SNAPSHOT_REFRESH_SEC


def _snapshot_has_received_gap(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    row_count = int(payload.get("row_count") or len(payload.get("rows") or []))
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    total_required = _to_float(summary.get("total_required_qty"))
    total_received = _to_float(summary.get("total_received_qty"))
    return row_count > 0 and total_required > 0 and total_received <= 0


def _snapshot_source_cache_received_changed(go: str, cached_payload: dict | None) -> tuple[bool, dict]:
    if not isinstance(cached_payload, dict):
        return False, {}
    rows = list((cached_payload or {}).get("rows") or [])
    ppo_list = sorted(
        {
            str(item.get("PPO") or "").strip().upper()
            for item in rows
            if str(item.get("PPO") or "").strip()
        }
    )
    if not ppo_list:
        return False, {}

    summary = cached_payload.get("summary") if isinstance(cached_payload.get("summary"), dict) else {}
    cached_received = _to_float(summary.get("total_received_qty"))
    snapshot_meta = cached_payload.get("snapshot") if isinstance(cached_payload.get("snapshot"), dict) else {}
    snapshot_dt = _parse_iso_datetime(snapshot_meta.get("snapshot_updated_at"))
    factory_code = str(cached_payload.get("factory_code") or (cached_payload.get("head") or {}).get("factory_code") or "").strip().upper()
    received_view = _FOC_VIEW_BY_FACTORY.get(factory_code, "")

    placeholders = ",".join("?" for _ in ppo_list)
    go_key = str(go or "").strip().upper()
    try:
        with _snapshot_connect() as conn:
            received_params: list[object] = list(ppo_list)
            received_view_filter = ""
            if received_view:
                received_view_filter = "AND view_name = ?"
                received_params.append(received_view)
            row = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(received_qty), 0) AS total_received,
                    MAX(synced_at) AS synced_at,
                    COUNT(*) AS row_count
                FROM sql_received_foc
                WHERE ppo_no IN ({placeholders})
                  {received_view_filter}
                """,
                received_params,
            ).fetchone()
            sync_row = conn.execute(
                """
                SELECT synced_at, row_count
                FROM sql_source_sync
                WHERE source_key LIKE ?
                ORDER BY synced_at DESC
                LIMIT 1
                """,
                (f"RECEIVED:%:{go_key}",),
            ).fetchone()
    except Exception:
        return False, {}

    source_received = _to_float(row["total_received"] if row else 0)
    source_synced_at = str((row["synced_at"] if row else "") or "").strip()
    source_dt = _parse_iso_datetime(source_synced_at)
    sync_synced_at = str((sync_row["synced_at"] if sync_row else "") or "").strip()
    sync_dt = _parse_iso_datetime(sync_synced_at)
    source_count = int((row["row_count"] if row else 0) or 0)
    sync_count = int((sync_row["row_count"] if sync_row else 0) or 0)

    source_newer = bool(source_dt and snapshot_dt and source_dt > snapshot_dt)
    sync_newer = bool(sync_dt and snapshot_dt and sync_dt > snapshot_dt)
    received_diff = abs(source_received - cached_received) > 0.001
    cached_has_no_received = cached_received <= 0.001
    changed = bool(
        (source_newer and received_diff)
        or
        (cached_has_no_received and source_received > 0.001)
        or (cached_has_no_received and received_diff and source_newer)
        or (source_count == 0 and sync_count == 0 and cached_received > 0.001 and sync_newer)
    )
    if not changed:
        return False, {
            "cached_received": cached_received,
            "source_received": source_received,
            "source_synced_at": source_synced_at,
            "source_row_count": source_count,
            "sync_synced_at": sync_synced_at,
            "sync_row_count": sync_count,
        }
    return True, {
        "cached_received": cached_received,
        "source_received": source_received,
        "source_synced_at": source_synced_at,
        "source_row_count": source_count,
        "sync_synced_at": sync_synced_at,
        "sync_row_count": sync_count,
    }


def _snapshot_sql_received_positive(go: str, head: dict | None, cached_payload: dict | None) -> bool:
    if not isinstance(cached_payload, dict):
        return False
    rows = list((cached_payload or {}).get("rows") or [])
    ppo_list = sorted(
        {
            str(item.get("PPO") or "").strip().upper()
            for item in rows
            if str(item.get("PPO") or "").strip()
        }
    )
    if not ppo_list:
        return False
    factory_code = str((head or {}).get("factory_code") or (cached_payload or {}).get("factory_code") or "").strip().upper()
    cached_summary = cached_payload.get("summary") if isinstance(cached_payload.get("summary"), dict) else {}
    cached_received = _to_float(cached_summary.get("total_received_qty"))
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            received_rows, _ = _load_received_foc_rows(cursor, factory_code, ppo_list)
    except Exception:
        return False
    total_received = 0.0
    for item in received_rows or []:
        total_received += _display_received_qty(item)
    if _snapshot_has_received_gap(cached_payload):
        return total_received > 0
    return total_received > cached_received + 0.001


def _source_cache_live_received_changed(go: str) -> tuple[bool, dict]:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return False, {}
    try:
        cached_bundle = _load_cached_go_source_bundle(go_key, include_ppo_detail=False)
    except Exception:
        return False, {}
    if not cached_bundle.get("ok"):
        return False, {}

    ppo_list = _source_cache_ppos(
        list(cached_bundle.get("ppo_mapping") or []),
        list(cached_bundle.get("fabric_rows") or []),
        list(cached_bundle.get("jo_ppo_yy_rows") or []),
    )
    if not ppo_list:
        return False, {}

    cached_received = 0.0
    for item in cached_bundle.get("received_rows") or []:
        cached_received += _display_received_qty(item)

    factory_code = str((cached_bundle.get("head") or {}).get("factory_code") or "").strip().upper()
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            live_rows, received_view = _load_received_foc_rows(
                cursor,
                factory_code,
                ppo_list,
                bypass_cache=True,
            )
    except Exception as exc:
        return False, {
            "cached_received": round(cached_received, 3),
            "live_received": 0.0,
            "live_error": f"{type(exc).__name__}: {exc}",
        }

    live_received = 0.0
    for item in live_rows or []:
        live_received += _display_received_qty(item)

    changed = bool(live_received > cached_received + 0.001)
    return changed, {
        "cached_received": round(cached_received, 3),
        "live_received": round(live_received, 3),
        "received_view": received_view,
        "source_synced_at": str(cached_bundle.get("source_synced_at") or ""),
        "ppo_count": len(ppo_list),
    }


def _can_build_sheet_from_sqlite_source_cache(go: str) -> bool:
    try:
        return bool(_load_cached_go_source_bundle(go, include_ppo_detail=False).get("ok"))
    except Exception:
        return False


def _save_go_cache_profile(go: str, payload: dict, built_from: str = "ui-live") -> None:
    go_key = str(go or "").strip().upper()
    if not go_key or not isinstance(payload, dict) or not payload.get("ok"):
        return
    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    profile = dict(payload.get("cache_profile") or {})
    state = str(profile.get("state") or _CACHE_READY_STATE).strip().upper() or _CACHE_READY_STATE
    next_refresh_at = str(profile.get("next_refresh_at") or _next_refresh_at_for_cache_state(state) or "")
    ready_at = now_text if state == _CACHE_READY_STATE else ""
    build_started_ns = int(
        ((payload.get("snapshot") or {}).get("build_started_ns"))
        or _current_sheet_build_started_ns(go_key)
    )
    with _snapshot_connect() as conn:
        conn.execute(
            """
            INSERT INTO go_feed (
                go_no,
                last_seen_at,
                cache_state,
                cache_flags,
                cache_reason,
                snapshot_row_count,
                snapshot_updated_at,
                snapshot_built_from,
                last_build_attempt_at,
                next_refresh_at,
                last_build_error,
                ready_at,
                snapshot_build_started_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                cache_state = excluded.cache_state,
                cache_flags = excluded.cache_flags,
                cache_reason = excluded.cache_reason,
                snapshot_row_count = excluded.snapshot_row_count,
                snapshot_updated_at = excluded.snapshot_updated_at,
                snapshot_built_from = excluded.snapshot_built_from,
                last_build_attempt_at = excluded.last_build_attempt_at,
                next_refresh_at = excluded.next_refresh_at,
                last_build_error = '',
                snapshot_build_started_ns = excluded.snapshot_build_started_ns,
                ready_at = CASE
                    WHEN excluded.ready_at <> '' THEN excluded.ready_at
                    ELSE go_feed.ready_at
                END
            WHERE excluded.snapshot_build_started_ns >=
                  COALESCE(go_feed.snapshot_build_started_ns, 0)
            """,
            (
                go_key,
                now_text,
                state,
                _encode_cache_flags(profile.get("flags") or []),
                str(profile.get("reason") or "").strip(),
                int(profile.get("row_count") or payload.get("row_count") or ((payload.get("summary") or {}).get("rows")) or 0),
                now_text,
                built_from,
                now_text,
                next_refresh_at,
                ready_at,
                build_started_ns,
            ),
        )
        conn.commit()


def _mark_go_cache_error(go: str, error: str, built_from: str = "") -> None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return
    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    error_text = str(error or "Unknown build error").strip() or "Unknown build error"
    error_code, public_error = _public_source_reason(error_text)
    cache_reason = f"{error_code}: {public_error}" if error_code else error_text
    build_started_ns = _current_sheet_build_started_ns(go_key)
    with _snapshot_connect() as conn:
        conn.execute(
            """
            INSERT INTO go_feed (
                go_no,
                last_seen_at,
                cache_state,
                cache_flags,
                cache_reason,
                snapshot_built_from,
                last_build_attempt_at,
                next_refresh_at,
                last_build_error,
                snapshot_build_started_ns
            )
            VALUES (?, ?, 'ERROR', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                cache_state = 'ERROR',
                cache_flags = excluded.cache_flags,
                cache_reason = excluded.cache_reason,
                snapshot_built_from = excluded.snapshot_built_from,
                last_build_attempt_at = excluded.last_build_attempt_at,
                next_refresh_at = excluded.next_refresh_at,
                last_build_error = excluded.last_build_error,
                snapshot_build_started_ns = excluded.snapshot_build_started_ns
            WHERE excluded.snapshot_build_started_ns >=
                  COALESCE(go_feed.snapshot_build_started_ns, 0)
            """,
            (
                go_key,
                now_text,
                _encode_cache_flags(["BUILD_ERROR"]),
                cache_reason,
                built_from or "error",
                now_text,
                _next_refresh_at_for_cache_state("ERROR"),
                error_text,
                build_started_ns,
            ),
        )
        conn.commit()

def _save_sheet_snapshot(go: str, payload: dict, built_from: str = "ui-live") -> bool:
    go_key = str(go or "").strip().upper()
    if not go_key or not isinstance(payload, dict) or not payload.get("ok"):
        return False
    _ensure_snapshot_tables()
    payload_for_store = dict(payload)
    snapshot_meta = dict(payload_for_store.get("snapshot") or {})
    snapshot_now = _snapshot_now()
    build_started_ns = int(
        snapshot_meta.get("build_started_ns")
        or _current_sheet_build_started_ns(go_key)
    )
    snapshot_meta.update(
        {
            "version": _SNAPSHOT_PAYLOAD_VERSION,
            "flatknit_received_size_contract": _FLATKNIT_RECEIVED_SIZE_CONTRACT_VERSION,
            "stock_balance_contract": _STOCK_BALANCE_CONTRACT_VERSION,
            "served_from_snapshot": False,
            "snapshot_updated_at": snapshot_now,
            "built_from": built_from,
            "build_started_ns": build_started_ns,
        }
    )
    payload_for_store["snapshot"] = snapshot_meta
    with _snapshot_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sheet_snapshots (
                go_no,
                factory_code,
                style_no,
                style_desc,
                source_modify_date,
                row_count,
                payload_version,
                payload_json,
                updated_at,
                built_from,
                build_started_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                factory_code = excluded.factory_code,
                style_no = excluded.style_no,
                style_desc = excluded.style_desc,
                source_modify_date = excluded.source_modify_date,
                row_count = excluded.row_count,
                payload_version = excluded.payload_version,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                built_from = excluded.built_from,
                build_started_ns = excluded.build_started_ns
            WHERE excluded.build_started_ns >= sheet_snapshots.build_started_ns
              AND (
                    COALESCE(sheet_snapshots.source_modify_date, '') = ''
                 OR COALESCE(excluded.source_modify_date, '') >= COALESCE(sheet_snapshots.source_modify_date, '')
              )
            """,
            (
                go_key,
                str(payload.get("factory_code") or ""),
                str(payload.get("style_no") or ""),
                str(payload.get("style_desc") or ""),
                str((payload.get("head") or {}).get("modify_date") or (payload.get("head") or {}).get("create_date") or ""),
                int(payload.get("row_count") or 0),
                _SNAPSHOT_PAYLOAD_VERSION,
                json.dumps(payload_for_store, ensure_ascii=False),
                snapshot_now,
                built_from,
                build_started_ns,
            ),
        )
        conn.commit()
    if int(cursor.rowcount or 0) <= 0:
        return False
    _save_go_cache_profile(go_key, payload_for_store, built_from=built_from)
    try:
        save_live_sheet_payload(go_key, payload_for_store, built_from=built_from)
    except Exception:
        pass
    return True


def _delete_sheet_snapshot(go: str) -> None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        conn.execute("DELETE FROM sheet_snapshots WHERE go_no = ?", (go_key,))
        conn.commit()
    try:
        delete_live_sheet_payload(go_key)
    except Exception:
        pass


def _edit_storage_key(go_key: str, storage: dict) -> str:
    return _row_storage_key(
        {
            "go_no": go_key,
            "ppo_no": storage.get("ppo_no"),
            "lot_no": _to_int(storage.get("lot_no")),
            "jo_no": storage.get("jo_no"),
            "fabric_type": storage.get("fabric_type"),
            "color_code": storage.get("color_code"),
            "fabric_combo": storage.get("fabric_combo"),
            "size_code": storage.get("size_code"),
        }
    )


def _patch_sheet_snapshot_edits(go: str, edits: list[dict]) -> bool:
    go_key = str(go or "").strip().upper()
    if not go_key or not edits:
        return False

    patch_by_key: dict[str, dict] = {}
    for edit in edits:
        storage = edit.get("storage") if isinstance(edit.get("storage"), dict) else {}
        row_key = _edit_storage_key(go_key, storage)
        field = str(edit.get("field") or "").strip()
        if not row_key or field not in _SHEET_EDITABLE_KEYS:
            continue
        patch_by_key[row_key] = edit
    if not patch_by_key:
        return False

    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    with _snapshot_connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM sheet_snapshots WHERE go_no = ?",
            (go_key,),
        ).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except Exception:
            return False
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return False

        changed = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("_row_key") or "").strip()
            if not item_key:
                item_storage = item.get("_storage") if isinstance(item.get("_storage"), dict) else {}
                item_key = _edit_storage_key(go_key, item_storage)
            edit = patch_by_key.get(item_key)
            if not edit:
                continue
            field = str(edit.get("field") or "").strip()
            if field in {_COI_USER_REMARK_FIELD, _COI_ETD_FABRIC_FIELD, _COI_LEGACY_REMARK_FIELD}:
                next_value = str(edit.get("value") or "").strip()
            elif field == "AH Allocate Q'ty (yds)":
                parsed_value = edit.get("parsed_manual_allocate")
                next_value = "" if parsed_value is None else round(_to_float(parsed_value), 3)
            else:
                continue
            if field == _COI_LEGACY_REMARK_FIELD:
                field = _COI_USER_REMARK_FIELD
            if item.get(field) != next_value:
                item[field] = next_value
                changed = True

        if not changed:
            return False
        snapshot_meta = dict(payload.get("snapshot") or {})
        snapshot_meta["snapshot_updated_at"] = now_text
        payload["snapshot"] = snapshot_meta
        conn.execute(
            "UPDATE sheet_snapshots SET payload_json = ?, updated_at = ? WHERE go_no = ?",
            (json.dumps(payload, ensure_ascii=False), now_text, go_key),
        )
        conn.commit()
        return True


def _recent_snapshot_events(limit: int = 5) -> list[dict]:
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        rows = conn.execute(
            """
            SELECT go_no, event_type, message, created_at
            FROM go_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit or 5)),),
        ).fetchall()
    return [
        {
            "go_no": str(row["go_no"] or ""),
            "event_type": str(row["event_type"] or ""),
            "message": str(row["message"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def sql_snapshot_status() -> dict:
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        # Keep all counters on one WAL read snapshot while background workers
        # continue writing, so feed = cached + uncached remains coherent.
        conn.execute("BEGIN")
        snapshot_total_count = int(conn.execute("SELECT COUNT(*) FROM sheet_snapshots").fetchone()[0])
        cutoff_text = _preload_lookback_cutoff().isoformat(sep=" ", timespec="seconds")
        ignored_customer_codes = sorted(_IGNORED_CUSTOMER_CODES) or [""]
        ignored_customer_placeholders = ",".join("?" for _ in ignored_customer_codes)
        cached_go_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM sheet_snapshots ss
                JOIN go_feed gf ON gf.go_no = ss.go_no
                WHERE ss.payload_version = ?
                  AND COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                """,
                (
                    _SNAPSHOT_PAYLOAD_VERSION,
                    cutoff_text,
                    *_ALLOWED_FACTORIES,
                    *ignored_customer_codes,
                ),
            ).fetchone()[0]
        )
        invalid_payload_version_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM sheet_snapshots ss
                JOIN go_feed gf ON gf.go_no = ss.go_no
                WHERE ss.payload_version <> ?
                  AND COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                """,
                (
                    _SNAPSHOT_PAYLOAD_VERSION,
                    cutoff_text,
                    *_ALLOWED_FACTORIES,
                    *ignored_customer_codes,
                ),
            ).fetchone()[0]
        )
        preload_feed_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM go_feed gf
                WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                """,
                (cutoff_text, *_ALLOWED_FACTORIES, *ignored_customer_codes),
            ).fetchone()[0]
        )
        preload_uncached_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM go_feed gf
                LEFT JOIN sheet_snapshots ss
                  ON ss.go_no = gf.go_no
                 AND ss.payload_version = ?
                WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                  AND ss.go_no IS NULL
                """,
                (
                    _SNAPSHOT_PAYLOAD_VERSION,
                    cutoff_text,
                    *_ALLOWED_FACTORIES,
                    *ignored_customer_codes,
                ),
            ).fetchone()[0]
        )
        preload_outdated_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM go_feed gf
                JOIN sheet_snapshots ss
                  ON ss.go_no = gf.go_no
                 AND ss.payload_version = ?
                WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                  AND COALESCE(gf.modify_date, gf.create_date, '') >
                      COALESCE(ss.source_modify_date, '')
                """,
                (
                    _SNAPSHOT_PAYLOAD_VERSION,
                    cutoff_text,
                    *_ALLOWED_FACTORIES,
                    *ignored_customer_codes,
                ),
            ).fetchone()[0]
        )
        preload_staged_missing_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM go_feed gf
                LEFT JOIN sql_go_head h ON h.go_no = gf.go_no
                WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                  AND h.go_no IS NULL
                """,
                (cutoff_text, *_ALLOWED_FACTORIES, *ignored_customer_codes),
            ).fetchone()[0]
        )
        preload_staged_outdated_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM go_feed gf
                JOIN sql_go_head h ON h.go_no = gf.go_no
                WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
                  AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                  AND COALESCE(gf.modify_date, gf.create_date, '') >
                      COALESCE(h.modify_date, h.create_date, '')
                """,
                (cutoff_text, *_ALLOWED_FACTORIES, *ignored_customer_codes),
            ).fetchone()[0]
        )
        cache_state_counts = {
            str(row[0] or "UNSET").strip().upper() or "UNSET": int(row[1] or 0)
            for row in conn.execute(
                f"""
                SELECT COALESCE(cache_state, ''), COUNT(*)
                FROM go_feed
                WHERE COALESCE(modify_date, create_date, '') >= ?
                  AND UPPER(COALESCE(status, '')) <> 'CANCEL'
                  AND UPPER(COALESCE(factory_code, '')) IN (?, ?)
                  AND UPPER(TRIM(COALESCE(customer_code, ''))) NOT IN ({ignored_customer_placeholders})
                GROUP BY COALESCE(cache_state, '')
                """,
                (cutoff_text, *_ALLOWED_FACTORIES, *ignored_customer_codes),
            ).fetchall()
        }
    with _snapshot_worker_lock:
        state = dict(_snapshot_worker_state)
    thread = state.pop("thread", None)
    source_refresh_thread = state.pop("source_refresh_thread", None)
    state.pop("priority_go_nos", None)
    state["cached_go_count"] = cached_go_count
    state["snapshot_total_count"] = snapshot_total_count
    state["invalid_payload_version_count"] = invalid_payload_version_count
    state["snapshot_payload_version"] = _SNAPSHOT_PAYLOAD_VERSION
    state["preload_lookback_days"] = _WORKER_LIVE_SOURCE_LOOKBACK_DAYS
    state["preload_feed_count"] = preload_feed_count
    state["preload_uncached_count"] = preload_uncached_count
    state["preload_outdated_count"] = preload_outdated_count
    state["preload_staged_missing_count"] = preload_staged_missing_count
    state["preload_staged_outdated_count"] = preload_staged_outdated_count
    state["cache_state_counts"] = cache_state_counts
    state["source_refresh_lookback_days"] = _SOURCE_REFRESH_LOOKBACK_DAYS
    state["source_refresh_interval_sec"] = _SOURCE_REFRESH_INTERVAL_SEC
    state["source_refresh_batch_size"] = _SOURCE_REFRESH_BATCH_SIZE
    state["source_max_age_sec"] = _SQL_SOURCE_CACHE_MAX_AGE_SEC
    state["query_metrics"] = query_metrics.snapshot()
    state["interactive_queue_size"] = _interactive_go_queue.size()
    state["inline_building_count"] = len(state.get("inline_building_go_nos") or [])
    try:
        source_scope, _selected_scope = _active_source_refresh_scope()
        current_cutoff = datetime.now() - timedelta(seconds=max(0, _SQL_SOURCE_CACHE_MAX_AGE_SEC))
        source_current_count = sum(
            1
            for item in source_scope.values()
            if item.get("verification_complete")
            and not item.get("has_error")
            and (_parse_iso_datetime(item.get("last_checked_at")) or datetime.min) >= current_cutoff
        )
        state["source_active_go_count"] = len(source_scope)
        state["source_current_go_count"] = source_current_count
        state["source_uncurrent_go_count"] = max(len(source_scope) - source_current_count, 0)
        state["source_missing_staged_head_count"] = sum(
            1 for item in source_scope.values() if not item.get("has_staged_head")
        )
        state["source_outdated_topology_count"] = sum(
            1
            for item in source_scope.values()
            if item.get("has_staged_head") and not item.get("topology_current")
        )
        state["source_missing_ppo_count"] = sum(
            1 for item in source_scope.values() if not item.get("has_ppo")
        )
        state["source_scope_evaluated"] = True
    except Exception as exc:
        state["source_active_go_count"] = int(state.get("source_refresh_scope_go_count") or 0)
        state["source_current_go_count"] = 0
        state["source_uncurrent_go_count"] = state["source_active_go_count"]
        state["source_missing_staged_head_count"] = 0
        state["source_outdated_topology_count"] = 0
        state["source_missing_ppo_count"] = 0
        state["source_scope_evaluated"] = False
        state["source_coverage_error"] = str(exc)
    state["warmup_scope"] = {
        "snapshot_lookback_days": _WORKER_LIVE_SOURCE_LOOKBACK_DAYS,
        "volatile_source_lookback_days": _SOURCE_REFRESH_LOOKBACK_DAYS,
        "factories": list(_ALLOWED_FACTORIES),
        "cancelled_go_excluded": True,
    }
    state["warmup_complete"] = bool(
        preload_uncached_count == 0
        and preload_outdated_count == 0
        and preload_staged_missing_count == 0
        and preload_staged_outdated_count == 0
        and int(state.get("source_uncurrent_go_count") or 0) == 0
        and bool(state.get("source_scope_evaluated"))
        and bool(state.get("last_full_go_feed_sync_at"))
    )
    state["thread_alive"] = bool(isinstance(thread, threading.Thread) and thread.is_alive())
    state["source_refresh_thread_alive"] = bool(
        isinstance(source_refresh_thread, threading.Thread) and source_refresh_thread.is_alive()
    )
    lease_monitor_thread = state.pop("lease_monitor_thread", None)
    state["lease_monitor_thread_alive"] = bool(
        isinstance(lease_monitor_thread, threading.Thread) and lease_monitor_thread.is_alive()
    )
    state["recent_events"] = _recent_snapshot_events(limit=5)
    state["db_file"] = str(_SNAPSHOT_DB)
    try:
        state["sheet_store"] = live_sheet_store_status()
    except Exception as exc:
        state["sheet_store"] = {"ok": False, "error": str(exc)}
    return state


def _format_sqlite_preload_status_line(status: dict | None = None) -> str:
    payload = status if isinstance(status, dict) else sql_snapshot_status()
    inline = payload.get("inline_building_go_nos") or []
    inline_label = ",".join(str(item or "") for item in inline[:4]) if isinstance(inline, list) else str(inline or "")
    if isinstance(inline, list) and len(inline) > 4:
        inline_label += f"+{len(inline) - 4}"
    return (
        "[SQLite preload] "
        f"task={payload.get('current_task') or '-'} "
        f"go={payload.get('current_go') or '-'} "
        f"detail={payload.get('current_detail') or '-'} "
        f"cached={payload.get('cached_go_count', 0)} "
        f"feed={payload.get('preload_feed_count', 0)} "
        f"uncached={payload.get('preload_uncached_count', 0)} "
        f"backlog={payload.get('stale_backlog', 0)} "
        f"batch={payload.get('last_batch_size', 0)} "
        f"queue={payload.get('priority_queue_size', 0)} "
        f"inline={inline_label or '-'} "
        f"last_cycle={payload.get('last_cycle_at') or '-'} "
        f"error={payload.get('last_error') or '-'}"
    )


def ensure_sql_snapshot_status_logger(interval_sec: int | None = None) -> None:
    with _snapshot_worker_lock:
        if _snapshot_worker_state.get("status_logger_started"):
            return
        _snapshot_worker_state["status_logger_started"] = True

    try:
        interval = int(interval_sec or os.getenv("SQLITE_STATUS_LOG_INTERVAL_SEC", "15"))
    except ValueError:
        interval = 15
    interval = max(5, interval)
    log_file = CACHE_DIR / "sqlite_preload_status.log"

    def _write_line(line: str) -> None:
        print(line, flush=True)
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{_snapshot_now()} {line}\n")
        except Exception:
            pass

    def _runner() -> None:
        _write_line(f"[SQLite preload] logger started db={_SNAPSHOT_DB} interval={interval}s")
        while True:
            try:
                _write_line(_format_sqlite_preload_status_line())
            except Exception as exc:
                print(f"[SQLite preload] status logger error={exc}", flush=True)
            time.sleep(interval)

    threading.Thread(target=_runner, daemon=True, name="sqlite-preload-status").start()


def get_go_issue_state(go: str) -> dict:
    go_key = str(go or "").strip().upper()
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        row = conn.execute(
            "SELECT issue_count, last_issued_at FROM go_issue_state WHERE go_no = ?",
            (go_key,),
        ).fetchone()
    if not row:
        return {"go": go_key, "issue_count": 0, "last_issued_at": ""}
    return {
        "go": go_key,
        "issue_count": int(row["issue_count"] or 0),
        "last_issued_at": str(row["last_issued_at"] or ""),
    }


def _load_go_issue_locks(go: str) -> dict[str, float]:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {}
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        rows = conn.execute(
            "SELECT row_key, locked_allocate_qty FROM go_issue_locks WHERE go_no = ?",
            (go_key,),
        ).fetchall()
    return {
        str(row["row_key"] or ""): _to_float(row["locked_allocate_qty"])
        for row in rows
        if str(row["row_key"] or "").strip()
    }


def upsert_go_issue_locks(go: str, rows: list[dict]) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key or not rows:
        return {"ok": True, "go": go_key, "saved_count": 0}
    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    saved_count = 0
    with _snapshot_connect() as conn:
        for row in rows:
            row_key = str(row.get("row_key") or "").strip()
            if not row_key:
                continue
            locked_qty = max(_to_float(row.get("locked_allocate_qty")), 0.0)
            existing = conn.execute(
                "SELECT 1 FROM go_issue_locks WHERE go_no = ? AND row_key = ?",
                (go_key, row_key),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO go_issue_locks (go_no, row_key, locked_allocate_qty, locked_at)
                VALUES (?, ?, ?, ?)
                """,
                (go_key, row_key, locked_qty, now_text),
            )
            saved_count += 1
        conn.commit()
    return {"ok": True, "go": go_key, "saved_count": saved_count}


def record_go_issue_event(go: str, issued_at: str = "") -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"ok": False, "error": "GO number required"}
    _ensure_snapshot_tables()
    issued_text = str(issued_at or _snapshot_now())
    with _snapshot_connect() as conn:
        conn.execute(
            """
            INSERT INTO go_issue_state (go_no, issue_count, last_issued_at)
            VALUES (?, 1, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                issue_count = go_issue_state.issue_count + 1,
                last_issued_at = excluded.last_issued_at
            """,
            (go_key, issued_text),
        )
        conn.commit()
    return {"ok": True, **get_go_issue_state(go_key)}


def _load_all_live_go_rows(on_page=None) -> list[dict]:
    factory_placeholders = ",".join("?" for _ in _ALLOWED_FACTORIES)
    cutoff_date = _preload_lookback_cutoff().strftime("%Y-%m-%d")
    select_sql = f"""
        SELECT TOP {_GO_FEED_PAGE_SIZE}
            [GO No] AS go_no,
            [Style No] AS style_no,
            [Style Desc] AS style_desc,
            [Factory Code] AS factory_code,
            [Status] AS status,
            [Season] AS season,
            [Customer Code] AS customer_code,
            [Create Date] AS create_date,
            [Last Modify Date] AS modify_date
        FROM dbo.V_GO_Head_Infor
        WHERE [Factory Code] IN ({factory_placeholders})
          AND [Last Modify Date] >= '{cutoff_date}'
    """
    rows: list[dict] = []
    last_go = ""
    with _connect() as conn:
        try:
            conn.timeout = max(int(getattr(conn, "timeout", 0) or 0), int(SQL_SERVER_QUERY_TIMEOUT_SEC))
        except Exception:
            pass
        cursor = conn.cursor()
        while True:
            sql = select_sql
            params: list[object] = [*_ALLOWED_FACTORIES]
            if last_go:
                sql += " AND [GO No] > ?"
                params.append(last_go)
            sql += " ORDER BY [GO No] ASC"
            cursor.execute(sql, params)
            page = _rows_to_dicts(cursor, cursor.fetchall())
            if not page:
                break
            rows.extend(page)
            if callable(on_page):
                # Make a cold cache searchable after the first page instead of
                # waiting for the entire year-long view scan to finish.
                on_page(page)
            tail = page[-1]
            next_go = str(tail.get("go_no") or "").strip().upper()
            if not next_go or next_go == last_go:
                break
            last_go = next_go
            if len(page) < _GO_FEED_PAGE_SIZE:
                break
    filtered_rows = []
    for row in rows:
        stamp = row.get("modify_date") or row.get("create_date")
        parsed_stamp = stamp if isinstance(stamp, datetime) else _parse_iso_datetime(stamp)
        if not _is_ignored_customer_code(row.get("customer_code")):
            filtered_rows.append(row)
    return filtered_rows


def _load_recent_live_go_rows(since_dt: datetime | None = None) -> list[dict]:
    factory_placeholders = ",".join("?" for _ in _ALLOWED_FACTORIES)
    sql = f"""
        SELECT TOP {_GO_FEED_RECENT_LIMIT}
            [GO No] AS go_no,
            [Style No] AS style_no,
            [Style Desc] AS style_desc,
            [Factory Code] AS factory_code,
            [Status] AS status,
            [Season] AS season,
            [Customer Code] AS customer_code,
            [Create Date] AS create_date,
            [Last Modify Date] AS modify_date
        FROM dbo.V_GO_Head_Infor
        WHERE [Factory Code] IN ({factory_placeholders})
    """
    params: list[object] = list(_ALLOWED_FACTORIES)
    if since_dt is not None:
        # A literal range predicate lets the view use its date index. The
        # previous ISNULL expression forced a scan and timed out even for a
        # small recent-change window.
        sql += f" AND [Last Modify Date] >= '{since_dt.strftime('%Y-%m-%d %H:%M:%S')}'"
    # The range predicate excludes NULL modification dates, so ISNULL in the
    # ORDER BY only forces an unnecessary sort/scan on the source view.
    sql += " ORDER BY [Last Modify Date] DESC, [GO No] DESC"
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = _rows_to_dicts(cursor, cursor.fetchall())
    return [row for row in rows if not _is_ignored_customer_code(row.get("customer_code"))]


def _load_local_go_feed_rows() -> list[dict]:
    _ensure_snapshot_tables()
    factory_placeholders = ",".join("?" for _ in _ALLOWED_FACTORIES)
    sql = f"""
        SELECT go_no, style_no, style_desc, factory_code, status, season, customer_code, create_date, modify_date
        FROM go_feed
        WHERE factory_code IN ({factory_placeholders})
        ORDER BY COALESCE(modify_date, create_date) DESC, go_no DESC
    """
    with _snapshot_connect() as conn:
        rows = conn.execute(sql, list(_ALLOWED_FACTORIES)).fetchall()
    return [
        {
            "go_no": str(row["go_no"] or ""),
            "style_no": str(row["style_no"] or ""),
            "style_desc": str(row["style_desc"] or ""),
            "factory_code": str(row["factory_code"] or ""),
            "status": str(row["status"] or ""),
            "season": str(row["season"] or ""),
            "customer_code": str(row["customer_code"] or ""),
            "create_date": str(row["create_date"] or ""),
            "modify_date": str(row["modify_date"] or ""),
        }
        for row in rows
        if not _is_ignored_customer_code(row["customer_code"])
    ]


def _sync_recent_go_feed_rows(force: bool = False) -> list[dict]:
    now = datetime.now()
    with _snapshot_worker_lock:
        last_sync_at = str(_snapshot_worker_state.get("last_go_feed_sync_at") or "")
    if not force:
        last_sync_dt = _parse_iso_datetime(last_sync_at)
        if last_sync_dt is not None and (now - last_sync_dt).total_seconds() < _GO_FEED_RECENT_SYNC_INTERVAL_SEC:
            return []

    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        row = conn.execute(
            "SELECT MAX(COALESCE(modify_date, create_date)) AS max_stamp FROM go_feed WHERE factory_code IN (?, ?)",
            list(_ALLOWED_FACTORIES),
        ).fetchone()
    since_dt = _parse_iso_datetime((row["max_stamp"] if row else ""))
    if since_dt is not None:
        since_dt = since_dt - timedelta(days=_GO_FEED_RECENT_SYNC_LOOKBACK_DAYS)
    rows = _load_recent_live_go_rows(since_dt)
    if rows:
        _record_go_feed_rows(rows)
    with _snapshot_worker_lock:
        _snapshot_worker_state["last_go_feed_sync_at"] = _snapshot_now()
        _snapshot_worker_state["last_go_feed_sync_rows"] = len(rows)
    return rows


def _sync_full_go_feed_rows(force: bool = False) -> list[dict]:
    now = datetime.now()
    with _snapshot_worker_lock:
        last_sync_at = str(_snapshot_worker_state.get("last_full_go_feed_sync_at") or "")
    if not force:
        last_sync_dt = _parse_iso_datetime(last_sync_at)
        if last_sync_dt is not None and (now - last_sync_dt).total_seconds() < _GO_FEED_FULL_SYNC_INTERVAL_SEC:
            return []
    rows = _load_all_live_go_rows(on_page=_record_go_feed_rows)
    with _snapshot_worker_lock:
        _snapshot_worker_state["last_full_go_feed_sync_at"] = _snapshot_now()
        _snapshot_worker_state["last_full_go_feed_sync_rows"] = len(rows)
    return rows


def _load_go_head_fast(go: str, allow_live: bool = True) -> dict | None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return None
    try:
        _ensure_snapshot_tables()
        with _snapshot_connect() as conn:
            staged_row = conn.execute(
                """
                SELECT go_no, style_no, style_desc, season, factory_code, status,
                       customer_code, create_date, modify_date
                FROM sql_go_head
                WHERE go_no = ?
                """,
                (go_key,),
            ).fetchone()
            feed_row = conn.execute(
                """
                SELECT go_no, style_no, style_desc, season, factory_code, status,
                       customer_code, create_date, modify_date
                FROM go_feed
                WHERE go_no = ?
                """,
                (go_key,),
            ).fetchone()
            staged_stamp = str(
                (staged_row["modify_date"] or staged_row["create_date"] or "")
                if staged_row
                else ""
            )
            feed_stamp = str(
                (feed_row["modify_date"] or feed_row["create_date"] or "")
                if feed_row
                else ""
            )
            # go_feed is refreshed directly from V_GO_Head_Infor. If it is
            # newer, using sql_go_head would make an old topology appear current.
            row = feed_row if feed_row and feed_stamp > staged_stamp else (staged_row or feed_row)
        if row:
            return {
                "go_no": str(row["go_no"] or ""),
                "style_no": str(row["style_no"] or ""),
                "style_desc": str(row["style_desc"] or ""),
                "season": str(row["season"] or ""),
                "factory_code": str(row["factory_code"] or ""),
                "status": str(row["status"] or ""),
                "customer_code": str(row["customer_code"] or ""),
                "create_date": str(row["create_date"] or ""),
                "modify_date": str(row["modify_date"] or ""),
            }
    except Exception:
        pass
    if not allow_live:
        return None
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            return _load_go_head(cursor, go_key)
    except Exception:
        return None


def _queue_snapshot_priorities(go_list: list[str]) -> None:
    clean_keys: list[str] = []
    seen: set[str] = set()
    for go in go_list or []:
        go_key = str(go or "").strip().upper()
        if not go_key or go_key in seen:
            continue
        seen.add(go_key)
        clean_keys.append(go_key)
    if not clean_keys:
        return
    queue_size = _interactive_go_queue.promote(clean_keys)
    with _snapshot_worker_lock:
        _snapshot_worker_state["priority_queue_size"] = queue_size


def _queue_snapshot_priority(go: str) -> None:
    _queue_snapshot_priorities([go])


def _start_inline_snapshot_build(go: str) -> None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return
    with _snapshot_worker_lock:
        active = {
            str(item or "").strip().upper()
            for item in (_snapshot_worker_state.get("inline_building_go_nos") or [])
            if str(item or "").strip()
        }
        if go_key in active:
            return
        if len(active) >= _INLINE_SNAPSHOT_MAX_WORKERS:
            # The background worker will pick this GO from the interactive
            # lane. Do not create an unbounded thread per browser request.
            _queue_snapshot_priority(go_key)
            return
        active.add(go_key)
        _snapshot_worker_state["inline_building_go_nos"] = sorted(active)

    def _runner() -> None:
        try:
            try:
                cached = _load_sheet_snapshot(go_key)
                cached_source = _load_cached_go_source_bundle(go_key, include_ppo_detail=True)
                if not (isinstance(cached, dict) and cached.get("ok")) and cached_source.get("ok"):
                    quick_payload = _build_live_coi_sheet_impl(
                        go_key,
                        prefer_mes_cache=True,
                        allow_live_mes=False,
                        allow_live_go_report=False,
                        allow_slow_sql_enrichment=True,
                        prefer_source_cache=True,
                        sample_type="PPS",
                        allow_live_sample_status=False,
                        allow_live_size_breakdown=False,
                        manual_allocation_mode=_load_go_manual_allocation_mode(go_key),
                    )
                    if quick_payload.get("ok"):
                        quick_payload.setdefault("snapshot", {})["source_refresh_needed"] = True
                        quick_payload.setdefault("sources", {})["quick_cache_snapshot"] = "SQLite source cache while live refresh continues"
                        _save_sheet_snapshot(go_key, quick_payload, built_from="priority-cache")
            except Exception:
                pass
            build_live_coi_sheet(
                go_key,
                prefer_mes_cache=True,
                allow_live_mes=False,
                allow_live_go_report=True,
                allow_slow_sql_enrichment=True,
                use_snapshot=False,
                persist_snapshot=True,
                allow_inline_build=True,
                snapshot_built_from="priority-inline",
                allow_live_sample_status=False,
                allow_live_size_breakdown=False,
            )
        finally:
            with _snapshot_worker_lock:
                active_inner = {
                    str(item or "").strip().upper()
                    for item in (_snapshot_worker_state.get("inline_building_go_nos") or [])
                    if str(item or "").strip()
                }
                active_inner.discard(go_key)
                _snapshot_worker_state["inline_building_go_nos"] = sorted(active_inner)

    def _serialized_runner() -> None:
        with _serialized_sheet_build(go_key):
            _runner()

    threading.Thread(target=_serialized_runner, daemon=True, name=f"sheet-priority-{go_key}").start()


def _take_snapshot_priorities(limit: int) -> list[str]:
    max_items = max(0, int(limit or 0))
    if max_items <= 0:
        return []
    selected = _interactive_go_queue.take(max_items)
    with _snapshot_worker_lock:
        _snapshot_worker_state["priority_queue_size"] = _interactive_go_queue.size()
    return selected


def _repair_go_feed_cache_profiles(force: bool = False) -> int:
    now = datetime.now()
    with _snapshot_worker_lock:
        last_repair_at = str(_snapshot_worker_state.get("last_cache_profile_repair_at") or "")
    if not force:
        last_repair_dt = _parse_iso_datetime(last_repair_at)
        if last_repair_dt is not None and (now - last_repair_dt).total_seconds() < _CACHE_PROFILE_REPAIR_INTERVAL_SEC:
            return 0

    _ensure_snapshot_tables()
    repaired = 0
    now_text = _snapshot_now()
    with _snapshot_connect() as conn:
        rows = conn.execute(
            """
            SELECT ss.go_no, ss.payload_json, ss.updated_at, ss.built_from
            FROM sheet_snapshots ss
            LEFT JOIN go_feed gf ON gf.go_no = ss.go_no
            WHERE COALESCE(gf.cache_state, '') = ''
              AND ss.payload_version = ?
            ORDER BY COALESCE(ss.updated_at, '') DESC, ss.go_no DESC
            """
            ,
            (_SNAPSHOT_PAYLOAD_VERSION,),
        ).fetchall()
        for row in rows:
            go_key = str(row["go_no"] or "").strip().upper()
            if not go_key:
                continue
            payload_json = str(row["payload_json"] or "").strip()
            if not payload_json:
                continue
            try:
                payload = json.loads(payload_json)
            except Exception:
                continue
            cache_profile = dict(payload.get("cache_profile") or {})
            state = str(cache_profile.get("state") or _CACHE_READY_STATE).strip().upper() or _CACHE_READY_STATE
            flags = _encode_cache_flags(cache_profile.get("flags") or [])
            reason = str(cache_profile.get("reason") or "").strip()
            next_refresh_at = str(cache_profile.get("next_refresh_at") or _next_refresh_at_for_cache_state(state))
            snapshot_updated_at = str((payload.get("snapshot") or {}).get("snapshot_updated_at") or row["updated_at"] or now_text)
            ready_at = snapshot_updated_at if state == _CACHE_READY_STATE else ""
            conn.execute(
                """
                UPDATE go_feed
                SET cache_state = ?,
                    cache_flags = ?,
                    cache_reason = ?,
                    snapshot_built_from = ?,
                    last_build_attempt_at = ?,
                    next_refresh_at = ?,
                    ready_at = CASE
                        WHEN ? = ? THEN COALESCE(NULLIF(ready_at, ''), ?)
                        ELSE ''
                    END
                WHERE go_no = ?
                """,
                (
                    state,
                    flags,
                    reason,
                    str(row["built_from"] or ""),
                    snapshot_updated_at,
                    next_refresh_at,
                    state,
                    _CACHE_READY_STATE,
                    ready_at,
                    go_key,
                ),
            )
            repaired += 1
        conn.commit()
    with _snapshot_worker_lock:
        _snapshot_worker_state["last_cache_profile_repair_at"] = _snapshot_now()
        _snapshot_worker_state["last_cache_profile_repair_count"] = repaired
    return repaired


def _seed_snapshot_priorities(force: bool = False) -> list[str]:
    now = datetime.now()
    with _snapshot_worker_lock:
        last_seed_at = str(_snapshot_worker_state.get("last_priority_seed_at") or "")
    if not force:
        last_seed_dt = _parse_iso_datetime(last_seed_at)
        if last_seed_dt is not None and (now - last_seed_dt).total_seconds() < _SNAPSHOT_PRIORITY_SEED_INTERVAL_SEC:
            return []

    candidates: list[str] = []
    seen: set[str] = set()

    def _append(go_value: object) -> None:
        go_key = str(go_value or "").strip().upper()
        if not go_key or go_key in seen:
            return
        seen.add(go_key)
        candidates.append(go_key)

    try:
        audit_go_nos = color_audit_priority_go_nos(_AUDIT_PRIORITY_GO_LIMIT)
    except Exception:
        # The audit is advisory. A missing/corrupt audit output must never
        # prevent the normal GO cache queue from being seeded.
        audit_go_nos = []
    for go_key in audit_go_nos:
        _append(go_key)

    _ensure_snapshot_tables()
    cutoff_text = _preload_lookback_cutoff().isoformat(sep=" ", timespec="seconds")
    with _snapshot_connect() as conn:
        issue_rows = conn.execute(
            """
            SELECT go_no
            FROM go_feed
            WHERE COALESCE(cache_state, '') IN ('ISSUE', 'WAIT_PPO', 'WAIT_LOT', 'WAIT_CUTTING', 'WAIT_PPO_CUTTING', 'WAIT_SOURCE', 'ERROR', 'EMPTY')
              AND COALESCE(modify_date, create_date, '') >= ?
            ORDER BY COALESCE(modify_date, create_date) DESC, go_no DESC
            LIMIT ?
            """,
            (cutoff_text, _CACHE_STATE_PRIORITY_LIMIT),
        ).fetchall()
        uncached_rows = conn.execute(
            """
            SELECT gf.go_no
            FROM go_feed gf
            LEFT JOIN sheet_snapshots ss
              ON ss.go_no = gf.go_no
             AND ss.payload_version = ?
            WHERE ss.go_no IS NULL
              AND COALESCE(gf.modify_date, gf.create_date, '') >= ?
            ORDER BY COALESCE(gf.modify_date, gf.create_date) DESC, gf.go_no DESC
            LIMIT ?
            """,
            (_SNAPSHOT_PAYLOAD_VERSION, cutoff_text, _UNCACHED_PRIORITY_LIMIT),
        ).fetchall()
        outdated_rows = conn.execute(
            """
            SELECT gf.go_no
            FROM go_feed gf
            JOIN sheet_snapshots ss
              ON ss.go_no = gf.go_no
             AND ss.payload_version = ?
            WHERE COALESCE(gf.modify_date, gf.create_date, '') > COALESCE(ss.source_modify_date, '')
              AND COALESCE(gf.modify_date, gf.create_date, '') >= ?
            ORDER BY COALESCE(gf.modify_date, gf.create_date) DESC, gf.go_no DESC
            LIMIT ?
            """,
            (_SNAPSHOT_PAYLOAD_VERSION, cutoff_text, _OUTDATED_PRIORITY_LIMIT),
        ).fetchall()

    for row in issue_rows:
        _append(row["go_no"] if hasattr(row, "keys") else row[0])
    for row in uncached_rows:
        _append(row["go_no"] if hasattr(row, "keys") else row[0])
    for row in outdated_rows:
        _append(row["go_no"] if hasattr(row, "keys") else row[0])

    with _snapshot_worker_lock:
        _snapshot_worker_state["last_priority_seed_at"] = _snapshot_now()
        _snapshot_worker_state["last_priority_seed_count"] = len(candidates)
    return candidates


def _record_go_feed_rows(rows: list[dict]) -> None:
    if not rows:
        return
    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    priority_go_nos: list[str] = []
    with _snapshot_connect() as conn:
        existing = {
            str(row["go_no"] or "").strip().upper(): str(row["source_stamp"] or "")
            for row in conn.execute(
                "SELECT go_no, COALESCE(modify_date, create_date, '') AS source_stamp FROM go_feed"
            ).fetchall()
        }
        event_rows: list[tuple[str, str, str, str]] = []
        upsert_rows: list[tuple[object, ...]] = []
        for row in rows:
            go_key = str(row.get("go_no") or "").strip().upper()
            if not go_key:
                continue
            source_stamp = str(row.get("modify_date") or row.get("create_date") or "")
            is_new_go = go_key not in existing
            is_changed_go = bool((not is_new_go) and source_stamp and source_stamp > str(existing.get(go_key) or ""))
            if is_new_go:
                event_rows.append((go_key, "new_go", f"New GO detected: {go_key}", now_text))
            elif is_changed_go:
                event_rows.append((go_key, "go_changed", f"GO changed in SQL Server: {go_key}", now_text))
            # A cold feed can contain thousands of new rows. They belong to
            # the background lane; only actual changes jump into the urgent
            # queue used by UI requests.
            if is_changed_go and _go_row_in_preload_window(row):
                priority_go_nos.append(go_key)
            existing[go_key] = source_stamp
            upsert_rows.append(
                (
                    go_key,
                    str(row.get("factory_code") or ""),
                    str(row.get("style_no") or ""),
                    str(row.get("style_desc") or ""),
                    str(row.get("status") or ""),
                    str(row.get("season") or ""),
                    str(row.get("customer_code") or ""),
                    str(row.get("create_date") or ""),
                    str(row.get("modify_date") or ""),
                    now_text,
                )
            )
        if event_rows:
            conn.executemany(
                """
                INSERT INTO go_events (go_no, event_type, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                event_rows,
            )
        if upsert_rows:
            conn.executemany(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, style_no, style_desc, status, season, customer_code, create_date, modify_date, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(go_no) DO UPDATE SET
                    factory_code = excluded.factory_code,
                    style_no = excluded.style_no,
                    style_desc = excluded.style_desc,
                    status = excluded.status,
                    season = excluded.season,
                    customer_code = excluded.customer_code,
                    create_date = excluded.create_date,
                    modify_date = excluded.modify_date,
                    last_seen_at = excluded.last_seen_at
                """,
                upsert_rows,
            )
        conn.commit()
    if priority_go_nos:
        _queue_snapshot_priorities(priority_go_nos)


def _select_stale_go_rows(
    rows: list[dict],
    batch_size: int = _SNAPSHOT_BATCH_SIZE,
    priority_go_nos: list[str] | None = None,
    interactive_go_nos: list[str] | None = None,
) -> tuple[list[dict], int]:
    _ensure_snapshot_tables()
    now_text = _snapshot_now()
    with _snapshot_connect() as conn:
        snapshot_rows = conn.execute(
            "SELECT go_no, source_modify_date, updated_at, payload_version FROM sheet_snapshots"
        ).fetchall()
        cached_source_stamps = {
            str(row["go_no"] or "").strip().upper(): str(row["source_stamp"] or "")
            for row in conn.execute(
                """
                SELECT go_no, COALESCE(modify_date, create_date, '') AS source_stamp
                FROM sql_go_head
                """
            ).fetchall()
            if str(row["go_no"] or "").strip()
        }
        retry_due_go_nos = {
            str(row[0] or "").strip().upper()
            for row in conn.execute(
                """
                SELECT go_no
                FROM go_feed
                WHERE UPPER(COALESCE(cache_state, '')) <> ?
                  AND (COALESCE(next_refresh_at, '') = '' OR next_refresh_at <= ?)
                """,
                (_CACHE_READY_STATE, now_text),
            ).fetchall()
            if str(row[0] or "").strip()
        }
    snapshot_lookup = {
        str(row["go_no"] or "").strip().upper(): {
            "source_modify_date": str(row["source_modify_date"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "payload_valid": int(row["payload_version"] or 0) == _SNAPSHOT_PAYLOAD_VERSION,
        }
        for row in snapshot_rows
    }
    row_lookup = {
        str(row.get("go_no") or "").strip().upper(): row
        for row in rows
        if str(row.get("go_no") or "").strip()
    }
    topology_missing: list[dict] = []
    uncached: list[dict] = []
    outdated: list[dict] = []
    retry_due: list[dict] = []
    for row in rows:
        go_key = str(row.get("go_no") or "").strip().upper()
        if not go_key:
            continue
        stamp = str(row.get("modify_date") or row.get("create_date") or "")
        snapshot = snapshot_lookup.get(go_key)
        if go_key not in cached_source_stamps:
            topology_missing.append(row)
            continue
        if not snapshot or not snapshot.get("payload_valid"):
            uncached.append(row)
            continue
        if stamp and stamp > str(cached_source_stamps.get(go_key) or ""):
            outdated.append(row)
            continue
        if stamp and stamp > str(snapshot.get("source_modify_date") or ""):
            outdated.append(row)
            continue
        if go_key in retry_due_go_nos:
            retry_due.append(row)
    stale_count = len(topology_missing) + len(uncached) + len(outdated) + len(retry_due)
    limit = max(1, int(batch_size or _SNAPSHOT_BATCH_SIZE))
    selected: list[dict] = []
    seen: set[str] = set()

    # Explicit UI requests always jump ahead of background warmup.
    for go_key in interactive_go_nos or []:
        go_key = str(go_key or "").strip().upper()
        row = row_lookup.get(go_key)
        if not row:
            continue
        stamp = str(row.get("modify_date") or row.get("create_date") or "")
        snapshot = snapshot_lookup.get(go_key)
        is_stale = (
            (not snapshot)
            or (not snapshot.get("payload_valid"))
            or (go_key not in cached_source_stamps)
            or (stamp and stamp > str(cached_source_stamps.get(go_key) or ""))
            or (stamp and stamp > str(snapshot.get("source_modify_date") or ""))
            or (go_key in retry_due_go_nos)
        )
        if is_stale and go_key not in seen:
            selected.append(row)
            seen.add(go_key)
            if len(selected) >= limit:
                return selected, stale_count

    # Changed topology comes before the cold historical backlog when there is
    # no explicit interactive request.
    for row in outdated:
        go_key = str(row.get("go_no") or "").strip().upper()
        if not go_key or go_key in seen:
            continue
        selected.append(row)
        seen.add(go_key)
        if len(selected) >= limit:
            return selected, stale_count

    for row in topology_missing:
        go_key = str(row.get("go_no") or "").strip().upper()
        if not go_key or go_key in seen:
            continue
        selected.append(row)
        seen.add(go_key)
        if len(selected) >= limit:
            return selected, stale_count

    # Advisory seeds (audit/cache-state candidates) retain the historical
    # ordering below changed/missing topology. This is the background lane.
    for go_key in priority_go_nos or []:
        go_key = str(go_key or "").strip().upper()
        row = row_lookup.get(go_key)
        if not row or go_key in seen:
            continue
        stamp = str(row.get("modify_date") or row.get("create_date") or "")
        snapshot = snapshot_lookup.get(go_key)
        is_stale = (
            (not snapshot)
            or (not snapshot.get("payload_valid"))
            or (go_key not in cached_source_stamps)
            or (stamp and stamp > str(cached_source_stamps.get(go_key) or ""))
            or (stamp and stamp > str(snapshot.get("source_modify_date") or ""))
            or (go_key in retry_due_go_nos)
        )
        if is_stale:
            selected.append(row)
            seen.add(go_key)
            if len(selected) >= limit:
                return selected, stale_count

    for bucket in (uncached, retry_due):
        for row in bucket:
            go_key = str(row.get("go_no") or "").strip().upper()
            if go_key in seen:
                continue
            selected.append(row)
            seen.add(go_key)
            if len(selected) >= limit:
                return selected, stale_count
    return selected, stale_count


def _snapshot_worker_loop() -> None:
    while True:
        sleep_seconds = _SNAPSHOT_IDLE_REFRESH_SEC
        with _snapshot_worker_lock:
            _snapshot_worker_state["running"] = True
            if not _snapshot_worker_state["started_at"]:
                _snapshot_worker_state["started_at"] = _snapshot_now()
        try:
            _set_snapshot_worker_task("load-local-feed", "SQLite go_feed")
            rows = _load_local_go_feed_rows()
            if not rows:
                _set_snapshot_worker_task("sync-full-go-feed", "SQL Server V_GO_Head_Infor")
                rows = _sync_full_go_feed_rows(force=True)
                with _snapshot_worker_lock:
                    _snapshot_worker_state["last_go_feed_sync_at"] = _snapshot_now()
                    _snapshot_worker_state["last_go_feed_sync_rows"] = len(rows)
            else:
                # Process the usable local feed immediately after a restart.
                # Worker timestamps are in-memory, so without this bootstrap
                # every process restart blocks on the expensive source view
                # before it can build a single snapshot.
                _mark_sqlite_startup_ready(f"go_feed local baseline rows={len(rows)}")
                with _snapshot_worker_lock:
                    if not _snapshot_worker_state.get("last_go_feed_sync_at"):
                        _snapshot_worker_state["last_go_feed_sync_at"] = _snapshot_now()
                    if not _snapshot_worker_state.get("last_full_go_feed_sync_at"):
                        _snapshot_worker_state["last_full_go_feed_sync_at"] = _snapshot_now()
                _set_snapshot_worker_task("sync-recent-go-feed", "SQL Server recent GO changes")
                _sync_recent_go_feed_rows(force=False)
                _set_snapshot_worker_task("load-local-feed", "reload after recent sync")
                rows = _load_local_go_feed_rows()
                _mark_sqlite_startup_ready(f"go_feed recent baseline rows={len(rows)}")
                _set_snapshot_worker_task("sync-full-go-feed", "periodic 365-day GO feed")
                _sync_full_go_feed_rows(force=False)
                _set_snapshot_worker_task("load-local-feed", "reload after sync")
                rows = _load_local_go_feed_rows()
            _clear_worker_sql_backoff()
            _set_snapshot_worker_task("filter-preload-window", f"{_WORKER_LIVE_SOURCE_LOOKBACK_DAYS} days")
            rows = _filter_preload_window_rows(rows)
            _mark_sqlite_startup_ready(f"go_feed baseline rows={len(rows)}")
            _set_snapshot_worker_task("repair-cache-profile", "SQLite cache profile")
            _repair_go_feed_cache_profiles(force=False)
            _set_snapshot_worker_task("seed-priority", "uncached/outdated/issue GO")
            seeded_priorities = _seed_snapshot_priorities(force=False)
            priority_go_nos = _take_snapshot_priorities(_SNAPSHOT_BATCH_SIZE)
            priority_go_set = {
                str(item or "").strip().upper()
                for item in list(priority_go_nos)
                if str(item or "").strip()
            }
            stale_rows, stale_count = _select_stale_go_rows(
                rows,
                batch_size=_SNAPSHOT_BATCH_SIZE,
                priority_go_nos=seeded_priorities,
                interactive_go_nos=priority_go_nos,
            )
            with _snapshot_worker_lock:
                inline_active = {
                    str(item or "").strip().upper()
                    for item in (_snapshot_worker_state.get("inline_building_go_nos") or [])
                    if str(item or "").strip()
                }
            if inline_active:
                # Inline builders already own a per-GO serialization lock.
                # Skipping them here keeps the sole background worker useful
                # instead of waiting behind the same GO.
                stale_rows = [
                    row
                    for row in stale_rows
                    if str(row.get("go_no") or "").strip().upper() not in inline_active
                ]
            with _snapshot_worker_lock:
                _snapshot_worker_state["stale_backlog"] = stale_count
                _snapshot_worker_state["last_batch_size"] = len(stale_rows)
                _snapshot_worker_state["priority_queue_size"] = _interactive_go_queue.size()
            if stale_rows:
                sleep_seconds = _SNAPSHOT_ACTIVE_REFRESH_SEC
            for row in stale_rows:
                go_key = str(row.get("go_no") or "").strip().upper()
                if not go_key:
                    continue
                topology_staged = False
                topology_ppo_count = 0
                cached_topology = _load_cached_go_source_bundle(
                    go_key,
                    include_ppo_detail=False,
                )
                if not cached_topology.get("ok"):
                    _set_snapshot_worker_task(
                        "stage-go-topology",
                        "SQL GO/lot/PPO without warehouse wait",
                        go_key,
                    )
                    topology_result = _refresh_go_topology_cache(go_key)
                    if not topology_result.get("ok"):
                        _mark_go_cache_error(
                            go_key,
                            str(
                                topology_result.get("detail")
                                or topology_result.get("error")
                                or "Cannot stage GO topology"
                            ),
                            built_from="topology-preload",
                        )
                        with _snapshot_worker_lock:
                            _snapshot_worker_state["last_error"] = str(
                                topology_result.get("error") or "Cannot stage GO topology"
                            )
                        continue
                    topology_staged = True
                    topology_ppo_count = int(topology_result.get("ppo_count") or 0)
                profile = _load_go_cache_profile(go_key)
                profile_state = str(profile.get("state") or "").strip().upper()
                stamp_dt = _parse_iso_datetime(row.get("modify_date") or row.get("create_date") or "")
                is_recent_go = bool(
                    stamp_dt is not None
                    and (datetime.now() - stamp_dt).days <= _WORKER_LIVE_SOURCE_LOOKBACK_DAYS
                )
                is_priority_go = go_key in priority_go_set
                # Bulk rebuilds must stay on staged SQL/GO-report data. Live
                # browser and per-PPO enrichments are handled on demand and
                # must not serialize the critical preload backlog.
                allow_live_go_report = False
                allow_slow_sql_enrichment = False
                # Cutting/sample are optional enrichment. They must not block
                # the critical GO/PPO/allowance/warehouse preload queue.
                with _snapshot_worker_lock:
                    _snapshot_worker_state["current_go"] = go_key
                    _snapshot_worker_state["current_task"] = "build-snapshot"
                    _snapshot_worker_state["current_detail"] = f"state={profile_state or 'UNCACHED'} priority={is_priority_go}"
                payload = build_live_coi_sheet(
                    go_key,
                    prefer_mes_cache=True,
                    allow_live_mes=False,
                    allow_live_go_report=allow_live_go_report,
                    allow_slow_sql_enrichment=allow_slow_sql_enrichment,
                    prefer_source_cache=True,
                    use_snapshot=False,
                    persist_snapshot=True,
                    allow_inline_build=False,
                    snapshot_built_from="preload-worker",
                    allow_live_sample_status=False,
                    allow_live_size_breakdown=False,
                )
                if not payload.get("ok"):
                    with _snapshot_worker_lock:
                        _snapshot_worker_state["last_error"] = str(payload.get("error") or "Unknown preload error")
                elif topology_staged and topology_ppo_count > 0:
                    _mark_go_waiting_for_volatile_source(go_key)
            with _snapshot_worker_lock:
                _snapshot_worker_state["current_go"] = ""
                _snapshot_worker_state["current_task"] = "idle"
                _snapshot_worker_state["current_detail"] = f"sleep={sleep_seconds}s"
                _snapshot_worker_state["last_cycle_at"] = _snapshot_now()
                _snapshot_worker_state["last_error"] = ""
        except Exception as exc:
            classification = classify_source_error(exc)
            sleep_seconds = _worker_sql_backoff(exc)
            with _snapshot_worker_lock:
                _snapshot_worker_state["last_error"] = classification["message"]
                _snapshot_worker_state["current_task"] = "sql-backoff"
                _snapshot_worker_state["current_detail"] = (
                    f"{classification['code']}; retry in {sleep_seconds}s"
                )
            _mark_sqlite_startup_error(exc)
        time.sleep(sleep_seconds)


def _start_sql_workers_with_owned_lease() -> None:
    with _snapshot_worker_lock:
        _snapshot_worker_state["process_lease_acquired"] = True
        _snapshot_worker_state["worker_standby"] = False
        thread = _snapshot_worker_state.get("thread")
        if not isinstance(thread, threading.Thread) or not thread.is_alive():
            worker = threading.Thread(target=_snapshot_worker_loop, name="sql-sheet-preload", daemon=True)
            _snapshot_worker_state["thread"] = worker
            worker.start()
    ensure_sql_source_refresh_worker()


def _retry_worker_process_lease_once() -> bool:
    if not _acquire_worker_process_lease():
        return False
    _start_sql_workers_with_owned_lease()
    return True


def _ensure_worker_lease_monitor() -> None:
    with _snapshot_worker_lock:
        monitor = _snapshot_worker_state.get("lease_monitor_thread")
        if isinstance(monitor, threading.Thread) and monitor.is_alive():
            return

        def _monitor() -> None:
            with _snapshot_worker_lock:
                _snapshot_worker_state["lease_monitor_running"] = True
            try:
                while not _retry_worker_process_lease_once():
                    time.sleep(max(1, int(_WORKER_LEASE_RETRY_SEC)))
            finally:
                with _snapshot_worker_lock:
                    _snapshot_worker_state["lease_monitor_running"] = False

        monitor = threading.Thread(
            target=_monitor,
            name="sql-worker-lease-standby",
            daemon=True,
        )
        _snapshot_worker_state["lease_monitor_thread"] = monitor
        monitor.start()


def ensure_sql_snapshot_worker() -> None:
    _ensure_snapshot_tables()
    if not _acquire_worker_process_lease():
        with _snapshot_worker_lock:
            _snapshot_worker_state["process_lease_acquired"] = False
            _snapshot_worker_state["worker_standby"] = True
            _snapshot_worker_state["current_task"] = "standby"
            _snapshot_worker_state["current_detail"] = "another process owns SQL background workers"
        _ensure_worker_lease_monitor()
        return
    _start_sql_workers_with_owned_lease()


def _rows_to_dicts(cursor, rows) -> list[dict]:
    columns = [desc[0] for desc in cursor.description]
    result = []
    for row in rows:
        item = {}
        for index, column in enumerate(columns):
            item[column] = _to_jsonable(row[index])
        result.append(item)
    return result


def _ensure_allocation_table(cursor) -> None:
    cursor.execute(
        f"""
        IF OBJECT_ID('{_ALLOCATION_TABLE}', 'U') IS NULL
        BEGIN
            CREATE TABLE {_ALLOCATION_TABLE} (
                go_no NVARCHAR(40) NOT NULL,
                ppo_no NVARCHAR(80) NOT NULL,
                lot_no INT NOT NULL,
                jo_no NVARCHAR(80) NOT NULL,
                fabric_type NVARCHAR(40) NOT NULL,
                color_code NVARCHAR(120) NOT NULL,
                fabric_combo NVARCHAR(255) NOT NULL,
                ppo_override NVARCHAR(80) NULL,
                customer_name NVARCHAR(255) NULL,
                due_date DATETIME NULL,
                qty_pcs DECIMAL(18, 3) NOT NULL DEFAULT 0,
                net_yy DECIMAL(18, 6) NOT NULL DEFAULT 0,
                ppo_yy DECIMAL(18, 6) NOT NULL DEFAULT 0,
                marker_yy DECIMAL(18, 6) NOT NULL DEFAULT 0,
                required_qty DECIMAL(18, 3) NOT NULL DEFAULT 0,
                received_qty DECIMAL(18, 3) NOT NULL DEFAULT 0,
                system_allocate_qty DECIMAL(18, 3) NOT NULL DEFAULT 0,
                manual_allocate_qty DECIMAL(18, 3) NULL,
                shortage_qty DECIMAL(18, 3) NOT NULL DEFAULT 0,
                allocate_pct DECIMAL(18, 6) NOT NULL DEFAULT 0,
                target_pct DECIMAL(18, 6) NOT NULL DEFAULT 1,
                target_qty DECIMAL(18, 3) NOT NULL DEFAULT 0,
                remark NVARCHAR(1000) NULL,
                updated_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
                CONSTRAINT PK_COI_UI_ALLOCATIONS PRIMARY KEY (
                    go_no, ppo_no, lot_no, jo_no, fabric_type, color_code, fabric_combo
                )
            )
        END
        IF COL_LENGTH('{_ALLOCATION_TABLE}', 'etd_fabric') IS NULL
        BEGIN
            ALTER TABLE {_ALLOCATION_TABLE} ADD etd_fabric NVARCHAR(1000) NULL
        END
        IF COL_LENGTH('{_ALLOCATION_TABLE}', 'user_remark') IS NULL
        BEGIN
            ALTER TABLE {_ALLOCATION_TABLE} ADD user_remark NVARCHAR(1000) NULL
        END
        IF COL_LENGTH('{_ALLOCATION_TABLE}', 'ppo_override') IS NULL
        BEGIN
            ALTER TABLE {_ALLOCATION_TABLE} ADD ppo_override NVARCHAR(80) NULL
        END
        """
    )


def _load_saved_sheet_state(cursor, go: str) -> dict[str, dict]:
    _ensure_allocation_table(cursor)
    cursor.execute(
        f"""
        SELECT
            go_no,
            ppo_no,
            lot_no,
            jo_no,
            fabric_type,
            color_code,
            fabric_combo,
            ppo_override,
            manual_allocate_qty,
            remark,
            etd_fabric,
            user_remark
        FROM {_ALLOCATION_TABLE}
        WHERE go_no = ?
        """,
        go,
    )
    saved: dict[str, dict] = {}
    for row in _rows_to_dicts(cursor, cursor.fetchall()):
        storage_key = _row_storage_key(row)
        manual_raw = row.get("manual_allocate_qty")
        legacy_etd, legacy_user = _split_legacy_remark(row.get("remark"))
        saved[storage_key] = {
            "manual_allocate_qty": None if manual_raw in (None, "") else _to_float(manual_raw),
            "ppo_override": str(row.get("ppo_override") or "").strip().upper(),
            "remark": str(row.get("remark") or "").strip(),
            "etd_fabric": str(row.get("etd_fabric") or legacy_etd).strip(),
            "user_remark": str(row.get("user_remark") or legacy_user).strip(),
        }
    return saved


def _replace_sheet_snapshot(cursor, go: str, rows: list[dict]) -> None:
    _ensure_allocation_table(cursor)
    cursor.execute(f"DELETE FROM {_ALLOCATION_TABLE} WHERE go_no = ?", go)
    insert_sql = f"""
        INSERT INTO {_ALLOCATION_TABLE} (
            go_no,
            ppo_no,
            lot_no,
            jo_no,
            fabric_type,
            color_code,
            fabric_combo,
            ppo_override,
            customer_name,
            due_date,
            qty_pcs,
            net_yy,
            ppo_yy,
            marker_yy,
            required_qty,
            received_qty,
            system_allocate_qty,
            manual_allocate_qty,
            shortage_qty,
            allocate_pct,
            target_pct,
            target_qty,
            remark,
            etd_fabric,
            user_remark,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIME())
    """
    params = []
    for item in rows:
        meta = item.get("__storage") or {}
        etd_fabric = str(item.get(_COI_ETD_FABRIC_FIELD) or "").strip()
        user_remark = str(item.get(_COI_USER_REMARK_FIELD) or "").strip()
        params.append(
            (
                go,
                str(meta.get("ppo_no") or ""),
                _to_int(meta.get("lot_no")),
                str(meta.get("jo_no") or ""),
                str(meta.get("fabric_type") or ""),
                str(meta.get("color_code") or ""),
                str(meta.get("fabric_combo") or ""),
                str(meta.get("ppo_override") or ""),
                str(item.get("BRAND") or ""),
                _parse_due_date(item.get("BUYER_PO_DEL_DATE")),
                _to_float(item.get("Qty (pcs)")),
                _to_float(item.get("Net YY")),
                _to_float(item.get("PPO YY")),
                _to_float(item.get("Marker YY")),
                _to_float(item.get("Required Q'ty (Yds)")),
                _to_float(item.get("Rcv Q'ty (PPO)")),
                _to_float(item.get("Allocate Q'ty (Yds)")),
                None if str(item.get("AH Allocate Q'ty (yds)") or "").strip() == "" else _to_float(item.get("AH Allocate Q'ty (yds)")),
                _to_float(item.get("Shortage Q'ty (Yds)")),
                _to_float(item.get("Allocate %")),
                _to_float(meta.get("target_pct")),
                _to_float(meta.get("target_qty")),
                user_remark or etd_fabric,
                etd_fabric,
                user_remark,
            )
        )
    if params:
        cursor.fast_executemany = True
        cursor.executemany(insert_sql, params)


def _execute_sheet_edit_upsert(cursor, go_key: str, edit: dict) -> None:
    storage = edit["storage"] if isinstance(edit.get("storage"), dict) else {}
    key_params = (
        go_key,
        str(storage.get("ppo_no") or ""),
        _to_int(storage.get("lot_no")),
        str(storage.get("jo_no") or ""),
        str(storage.get("fabric_type") or ""),
        str(storage.get("color_code") or ""),
        str(storage.get("fabric_combo") or ""),
    )
    field = str(edit.get("field") or "").strip()
    if field == "AH Allocate Q'ty (yds)":
        column_name = "manual_allocate_qty"
        value = edit.get("parsed_manual_allocate")
    elif field == _COI_ETD_FABRIC_FIELD:
        column_name = "etd_fabric"
        value = str(edit.get("value") or "").strip()
    elif field == _COI_PPO_FIELD:
        column_name = "ppo_override"
        value = str(edit.get("value") or "").strip().upper()
    elif field in {_COI_USER_REMARK_FIELD, _COI_LEGACY_REMARK_FIELD}:
        column_name = "user_remark"
        value = str(edit.get("value") or "").strip()
    else:
        return

    cursor.execute(
        f"""
        IF EXISTS (
            SELECT 1
            FROM {_ALLOCATION_TABLE}
            WHERE go_no = ? AND ppo_no = ? AND lot_no = ? AND jo_no = ?
              AND fabric_type = ? AND color_code = ? AND fabric_combo = ?
        )
        BEGIN
            UPDATE {_ALLOCATION_TABLE}
            SET {column_name} = ?, updated_at = SYSDATETIME()
            WHERE go_no = ? AND ppo_no = ? AND lot_no = ? AND jo_no = ?
              AND fabric_type = ? AND color_code = ? AND fabric_combo = ?
        END
        ELSE
        BEGIN
            INSERT INTO {_ALLOCATION_TABLE} (
                go_no,
                ppo_no,
                lot_no,
                jo_no,
                fabric_type,
                color_code,
                fabric_combo,
                {column_name},
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIME())
        END
        """,
        *key_params,
        value,
        *key_params,
        *key_params,
        value,
    )


def save_live_sheet_edits(go: str, edits: list[dict], manual_allocation_mode: str | None = None) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")
    if not edits:
        return _error("No edits supplied", go=go_key)

    cleaned = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        storage = edit.get("storage") if isinstance(edit.get("storage"), dict) else {}
        field = str(edit.get("field") or "").strip()
        if field not in _SHEET_EDITABLE_KEYS:
            continue
        parsed_manual_allocate = None
        if field == "AH Allocate Q'ty (yds)":
            ok_value, parsed_value, parse_message = _parse_manual_allocate_input(edit.get("value"))
            if not ok_value:
                return _error(parse_message, go=go_key, field=field, value=edit.get("value"))
            parsed_manual_allocate = parsed_value
        row_key = _edit_storage_key(go_key, storage)
        cleaned.append(
            {
                "storage": storage,
                "row_key": row_key,
                "field": field,
                "value": edit.get("value"),
                "parsed_manual_allocate": parsed_manual_allocate,
            }
        )
    if not cleaned:
        return _error("No valid sheet edits", go=go_key)

    patch_only_fields = {_COI_USER_REMARK_FIELD, _COI_ETD_FABRIC_FIELD, _COI_LEGACY_REMARK_FIELD}
    remark_only = all(str(edit.get("field") or "").strip() in patch_only_fields for edit in cleaned)
    saved_mode = ""
    if manual_allocation_mode is not None and str(manual_allocation_mode or "").strip():
        saved_mode = _save_go_manual_allocation_mode(go_key, manual_allocation_mode)
    result = _save_local_sheet_edits(go_key, cleaned)
    if not result.get("ok"):
        return result
    if saved_mode:
        result["manual_allocation_mode"] = saved_mode

    if remark_only:
        _patch_sheet_snapshot_edits(go_key, cleaned)
        try:
            patch_live_sheet_payload_edits(go_key, cleaned)
        except Exception:
            pass
    else:
        _delete_sheet_snapshot(go_key)
        _queue_snapshot_priority(go_key)
    return result


def sql_live_status() -> dict:
    try:
        with _connect() as conn:
            cursor = conn.cursor()
        cursor.execute(
            "SELECT DB_NAME() AS dbname, SYSTEM_USER AS user_name, SYSDATETIME() AS server_time, @@VERSION AS sql_version"
        )
        row = cursor.fetchone()
        status_row = _rows_to_dicts(cursor, [row])[0] if row is not None else {}
    except Exception as exc:  # pragma: no cover - network errors
        classification = classify_source_error(exc)
        return _error(
            "Cannot connect SQL Server",
            detail=classification["message"],
            source_error_code=classification["code"],
            driver=sql_driver_configuration(),
            stock_connection={
                "host": STOCK_SQL_SERVER,
                "database": STOCK_SQL_DATABASE,
                "schema": STOCK_SQL_SCHEMA,
                "view": STOCK_SQL_VIEW,
                "driver": "pymssql",
                "configured": bool(STOCK_SQL_USER and STOCK_SQL_PASSWORD),
            },
        )

    return {
        "ok": True,
        "connected": True,
        "database": str(status_row.get("dbname") or ""),
        "user": str(status_row.get("user_name") or ""),
        "server_time": _to_jsonable(status_row.get("server_time")),
        "sql_version": str(status_row.get("sql_version") or "").splitlines()[0],
        "connection": {
            "host": SQL_SERVER_HOST,
            "database": SQL_SERVER_DATABASE,
            "driver": "pymssql",
            "timeout_sec": SQL_SERVER_TIMEOUT_SEC,
            "query_timeout_sec": SQL_SERVER_QUERY_TIMEOUT_SEC,
            "encrypted": SQL_SERVER_ENCRYPT,
            "trust_server_certificate": SQL_SERVER_TRUST_SERVER_CERTIFICATE,
            "encryption_required": SQL_SERVER_REQUIRE_ENCRYPTION,
            "transport_security": (
                "tls-unverified"
                if SQL_SERVER_ENCRYPT and SQL_SERVER_TRUST_SERVER_CERTIFICATE
                else "tls-verified"
                if SQL_SERVER_ENCRYPT
                else "plaintext"
            ),
            "user": SQL_SERVER_USER,
        },
        "stock_connection": {
            "host": STOCK_SQL_SERVER,
            "database": STOCK_SQL_DATABASE,
            "schema": STOCK_SQL_SCHEMA,
            "view": STOCK_SQL_VIEW,
            "driver": "pymssql",
            "configured": bool(STOCK_SQL_USER and STOCK_SQL_PASSWORD),
            "timeout_sec": STOCK_SQL_TIMEOUT_SEC,
            "query_timeout_sec": STOCK_SQL_QUERY_TIMEOUT_SEC,
            "encrypted": STOCK_SQL_ENCRYPT,
            "trust_server_certificate": STOCK_SQL_TRUST_SERVER_CERTIFICATE,
            "encryption_required": STOCK_SQL_REQUIRE_ENCRYPTION,
        },
        "driver": sql_driver_configuration(),
    }


def _normalize_coi_ready_filter(value: object) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in {"all", "ready", "not_ready"} else "all"


def _list_cached_go_rows(
    limit: int,
    search: str = "",
    since: str = "",
    factories: object = None,
    coi_ready: object = "all",
) -> list[dict]:
    top_n = _sanitize_limit(limit)
    search_text = str(search or "").strip()
    search_like = f"%{search_text}%"
    since_dt = _parse_iso_datetime(since)
    since_text = since_dt.isoformat(sep=" ", timespec="seconds") if since_dt is not None else ""
    factory_list = _normalize_factories(factories)
    if not factory_list:
        return []
    coi_ready_filter = _normalize_coi_ready_filter(coi_ready)
    _ensure_snapshot_tables()
    factory_placeholders = ",".join("?" for _ in factory_list)
    sql = f"""
        SELECT
            go_no,
            style_no,
            style_desc,
            factory_code,
            status,
            season,
            customer_code,
            create_date,
            modify_date,
            cache_state,
            cache_flags,
            cache_reason,
            snapshot_row_count,
            snapshot_updated_at,
            snapshot_built_from,
            next_refresh_at,
            last_build_error
        FROM go_feed
        WHERE factory_code IN ({factory_placeholders})
          AND (? = '' OR go_no LIKE ? OR style_no LIKE ? OR style_desc LIKE ? OR status LIKE ? OR season LIKE ? OR customer_code LIKE ? OR cache_state LIKE ?)
    """
    params: list[object] = [
        *factory_list,
        search_text,
        search_like,
        search_like,
        search_like,
        search_like,
        search_like,
        search_like,
        search_like,
    ]
    if since_text:
        sql += " AND COALESCE(modify_date, create_date) >= ?"
        params.append(since_text)
    if coi_ready_filter == "ready":
        sql += " AND snapshot_row_count > 0"
    elif coi_ready_filter == "not_ready":
        sql += " AND snapshot_row_count <= 0"
    sql += " ORDER BY COALESCE(modify_date, create_date) DESC, go_no DESC LIMIT ?"
    params.append(top_n)
    with _snapshot_connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "go_no": str(row["go_no"] or ""),
            "style_no": str(row["style_no"] or ""),
            "style_desc": str(row["style_desc"] or ""),
            "factory_code": str(row["factory_code"] or ""),
            "status": str(row["status"] or ""),
            "season": str(row["season"] or ""),
            "customer_code": str(row["customer_code"] or ""),
            "create_date": str(row["create_date"] or ""),
            "modify_date": str(row["modify_date"] or ""),
            "cache_state": str(row["cache_state"] or "UNSET").strip().upper() or "UNSET",
            "cache_flags": _split_cache_flags(row["cache_flags"]),
            "cache_reason": _public_source_reason(row["cache_reason"])[1],
            "source_reason_code": _public_source_reason(
                row["cache_reason"] or row["last_build_error"]
            )[0],
            "snapshot_row_count": int(row["snapshot_row_count"] or 0),
            "snapshot_updated_at": str(row["snapshot_updated_at"] or ""),
            "snapshot_built_from": str(row["snapshot_built_from"] or ""),
            "next_refresh_at": str(row["next_refresh_at"] or ""),
            "last_build_error": _public_source_reason(row["last_build_error"])[1],
            # SQL GO status (Submit/Unsubmit/etc.) is a workflow field.  COI
            # readiness is separate and comes from the locally verified sheet.
            "coi_ready": int(row["snapshot_row_count"] or 0) > 0,
            "coi_readiness": (
                "READY"
                if int(row["snapshot_row_count"] or 0) > 0
                else str(row["cache_state"] or "UNSET").strip().upper() or "UNSET"
            ),
            "coi_status": (
                "WAITING_SOURCE"
                if int(row["snapshot_row_count"] or 0) > 0
                and str(row["cache_state"] or "").strip().upper() == "WAIT_SOURCE"
                else "AVAILABLE"
                if int(row["snapshot_row_count"] or 0) > 0
                else "BLOCKED"
                if str(row["cache_state"] or "").strip().upper() == "ERROR"
                else "WAITING_SOURCE"
            ),
        }
        for row in rows
        if not _is_ignored_customer_code(row["customer_code"])
    ]


def list_live_go(
    limit: int = 220,
    search: str = "",
    since: str = "",
    factories: object = None,
    coi_ready: object = "all",
) -> dict:
    top_n = _sanitize_limit(limit)
    search_text = str(search or "").strip()
    search_like = f"%{search_text}%"
    since_dt = _parse_iso_datetime(since)
    factory_list = _normalize_factories(factories)
    if not factory_list:
        return _error("Factory filter invalid", allowed=list(_ALLOWED_FACTORIES))
    coi_ready_filter = _normalize_coi_ready_filter(coi_ready)

    factory_placeholders = ",".join("?" for _ in factory_list)
    rows = _list_cached_go_rows(
        top_n,
        search=search_text,
        since=since,
        factories=factory_list,
        coi_ready=coi_ready_filter,
    )
    select_sql = f"""
        SELECT TOP {top_n}
            [GO No] AS go_no,
            [Style No] AS style_no,
            [Style Desc] AS style_desc,
            [Factory Code] AS factory_code,
            [Status] AS status,
            [Season] AS season,
            [Customer Code] AS customer_code,
            [Create Date] AS create_date,
            [Last Modify Date] AS modify_date
        FROM dbo.V_GO_Head_Infor
    """

    try:
        if not rows and coi_ready_filter == "all":
            with _connect() as conn:
                cursor = conn.cursor()
                if search_text:
                    exact_sql = f"""
                        {select_sql}
                        WHERE [Factory Code] IN ({factory_placeholders})
                          AND ([GO No] = ? OR [Style No] = ?)
                        ORDER BY ISNULL([Last Modify Date], [Create Date]) DESC, [GO No] DESC
                    """
                    cursor.execute(exact_sql, [*factory_list, search_text, search_text])
                    rows = _rows_to_dicts(cursor, cursor.fetchall())
                if not rows:
                    sql = f"""
                        {select_sql}
                        WHERE
                            (? = '' OR [GO No] LIKE ? OR [Style No] LIKE ? OR [Style Desc] LIKE ?)
                            AND [Factory Code] IN ({factory_placeholders})
                    """
                    params: list[object] = [search_text, search_like, search_like, search_like, *factory_list]
                    if since_dt is not None:
                        sql += " AND (ISNULL([Last Modify Date], [Create Date]) >= ?)"
                        params.append(since_dt)
                    sql += " ORDER BY ISNULL([Last Modify Date], [Create Date]) DESC, [GO No] DESC"
                    cursor.execute(sql, params)
                    rows = _rows_to_dicts(cursor, cursor.fetchall())
    except Exception as exc:
        return _error("Cannot load GO list from SQL", detail=str(exc))

    rows = [row for row in rows if not _is_ignored_customer_code(row.get("customer_code"))]
    if search_text or since_dt is not None or not rows:
        _record_go_feed_rows(rows)
    if rows and "cache_state" not in rows[0]:
        cached_rows = _list_cached_go_rows(
            top_n,
            search=search_text,
            since=since,
            factories=factory_list,
            coi_ready=coi_ready_filter,
        )
        if cached_rows:
            rows = cached_rows
    if search_text:
        exact_key = str(search_text or "").strip().upper()
        for row in rows:
            if str(row.get("go_no") or "").strip().upper() == exact_key:
                _queue_snapshot_priority(exact_key)
                break

    latest_stamp = ""
    for row in rows:
        stamp = str(row.get("modify_date") or row.get("create_date") or "")
        if stamp and stamp > latest_stamp:
            latest_stamp = stamp

    return {
        "ok": True,
        "rows": rows,
        "total": len(rows),
        "latest_watermark": latest_stamp,
        "search": search_text,
        "coi_ready_filter": coi_ready_filter,
        "factories": factory_list,
        "snapshot": sql_snapshot_status(),
    }


def _load_go_head(cursor, go: str) -> dict | None:
    cursor.execute(
        """
        SELECT TOP 1
            [GO No] AS go_no,
            [Style No] AS style_no,
            [Style Desc] AS style_desc,
            [Season] AS season,
            [Factory Code] AS factory_code,
            [Status] AS status,
            [Customer Code] AS customer_code,
            [Create Date] AS create_date,
            [Last Modify Date] AS modify_date
        FROM dbo.V_GO_Head_Infor
        WHERE [GO No] = ?
        """,
        go,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _rows_to_dicts(cursor, [row])[0]


def _load_go_colors(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            [Color Code] AS color_code,
            [Color Desc] AS color_desc
        FROM dbo.V_GO_Color
        WHERE [GO NO] = ?
        ORDER BY [Color Code]
        """,
        go,
    )
    return _rows_to_dicts(cursor, cursor.fetchall())


def _load_go_lots(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            [LOT NO] AS lot_no,
            [JO No] AS jo_no,
            SUM([Qty]) AS qty,
            MAX([Buyer PO Del Date]) AS buyer_po_del_date,
            MAX([Buyer PO NO]) AS buyer_po_no,
            MAX([Percent Short Allowed]) AS short_pct,
            MAX([Percent Over Allowed]) AS over_pct
        FROM dbo.V_GO_BPO_HD_JO_ALL
        WHERE [GO NO] = ?
        GROUP BY [LOT NO], [JO No]
        ORDER BY [LOT NO], [JO No]
        """,
        go,
    )
    return _rows_to_dicts(cursor, cursor.fetchall())


def _load_go_jo_color_qty(cursor, go: str) -> list[dict]:
    try:
        cursor.execute(
            """
            SELECT
                GO_LOT_NO AS lot_no,
                JO_NO AS jo_no,
                COLOR_CODE AS color_code,
                COLOR_DESC AS color_desc,
                MAX(CUSTOMER_NAME) AS customer_name,
                MAX(BRAND_NAME) AS brand_name,
                MAX(BRAND_OWNER) AS brand_owner,
                MAX(CUSTOMER_LABEL) AS customer_label,
                MAX(BUYER_PO_DEL_DATE) AS buyer_po_del_date,
                SUM(Quantity) AS qty
            FROM dbo.V_ESCM_ORDER_COLORSIZE_SALES
            WHERE GO_NO = ?
            GROUP BY GO_LOT_NO, JO_NO, COLOR_CODE, COLOR_DESC
            ORDER BY GO_LOT_NO, JO_NO, COLOR_CODE
            """,
            go,
        )
        return _rows_to_dicts(cursor, cursor.fetchall())
    except Exception:
        return []


def _load_go_jo_color_size_qty_live(go: str) -> list[dict]:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return []
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    GO_LOT_NO AS lot_no,
                    JO_NO AS jo_no,
                    COLOR_CODE AS color_code,
                    COLOR_DESC AS color_desc,
                    SIZE_CODE1 AS size_code,
                    SUM(Quantity) AS qty
                FROM dbo.V_ESCM_ORDER_COLORSIZE_SALES
                WHERE GO_NO = ?
                GROUP BY GO_LOT_NO, JO_NO, COLOR_CODE, COLOR_DESC, SIZE_CODE1
                ORDER BY GO_LOT_NO, JO_NO, COLOR_CODE, SIZE_CODE1
                """,
                go_key,
            )
            return _rows_to_dicts(cursor, cursor.fetchall())
    except Exception:
        return []


def _load_go_ppo_mapping(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            [PPO NO] AS ppo_no,
            [Lot NO] AS lot_no
        FROM dbo.V_GO_PPO_Mapping
        WHERE [GO NO] = ?
        ORDER BY [PPO NO], [Lot NO]
        """,
        go,
    )
    return _rows_to_dicts(cursor, cursor.fetchall())


def _has_meaningful_fabric_row(item: dict) -> bool:
    ppo_no = str(item.get("ppo_no") or "").strip().upper()
    fabric_type = str(item.get("fabric_type") or "").strip().upper()
    color_code = str(item.get("color_code") or "").strip()
    combo_name = str(item.get("combo_name") or "").strip()
    related_jo = str(item.get("related_jo_list") or "").strip().upper()
    lot_no = _to_int(item.get("lot_no"))
    ppo_yy = _to_float(item.get("ppo_yy"))
    marker_yy = _to_float(item.get("marker_yy"))

    if ppo_no and (fabric_type or color_code or combo_name or lot_no > 0):
        return True
    if fabric_type and (color_code or combo_name or lot_no > 0 or related_jo):
        return True
    if related_jo:
        return True
    return ppo_yy > 0 or marker_yy > 0


def _load_go_fabric_rows(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            [Lot NO] AS lot_no,
            [PPO NO] AS ppo_no,
            [Fabric Type Code] AS fabric_type,
            [Color Code] AS color_code,
            [Combo Name] AS combo_name,
            [PPO YY] AS ppo_yy,
            [PPO Marker YY] AS marker_yy,
            [Related Jo List] AS related_jo_list,
            [Remarks] AS remarks
        FROM escmowner.V_GO_Fabric_Infor_ALL
        WHERE [GO NO] = ?
        ORDER BY [PPO NO], [Fabric Type Code], [Color Code], [Lot NO]
        """,
        go,
    )
    rows = _rows_to_dicts(cursor, cursor.fetchall())
    cleaned: list[dict] = []
    for item in rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        item["fabric_type"] = fabric_type
        color_code = str(item.get("color_code") or "").strip()
        combo_name = str(item.get("combo_name") or "").strip()
        if not ppo_no and not fabric_type and not color_code and not combo_name:
            continue
        if not _has_meaningful_fabric_row(item):
            continue
        cleaned.append(item)
    return cleaned


def _load_go_sql_bom_rows(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            [GO NO] AS go_no,
            [Style_color_code] AS style_color_code,
            [STYLE_COLOR_DESC] AS style_color_desc,
            [Fabric_type_cd] AS fabric_type_cd,
            [FABRIC_TYPE_DESC] AS fabric_type_desc,
            [COMBO_NAME] AS combo_name,
            [YY] AS yy,
            [Marker_YY] AS marker_yy
        FROM escmowner.V_GO_Fabric_BOM_Infor
        WHERE [GO NO] = ?
        ORDER BY [STYLE_COLOR_DESC], [Fabric_type_cd], [COMBO_NAME]
        """,
        go,
    )
    return _rows_to_dicts(cursor, cursor.fetchall())


def _load_jo_ppo_yy(cursor, go: str) -> list[dict]:
    cursor.execute(
        """
        SELECT
            lot_no,
            po_no AS jo_no,
            ppo_no,
            PPO_YY AS ppo_yy
        FROM dbo.V_JO_PPO_YY
        WHERE sc_no = ?
        ORDER BY ppo_no, lot_no, po_no, PPO_YY
        """,
        go,
    )
    return _rows_to_dicts(cursor, cursor.fetchall())


def _set_max_jo_ppo_yy(
    lookup: dict[tuple[str, str], float],
    ppo_no: object,
    jo_no: object,
    yy: object,
) -> None:
    ppo_key = str(ppo_no or "").strip().upper()
    jo_key = str(jo_no or "").strip().upper()
    yy_value = _to_float(yy)
    if not ppo_key or not jo_key:
        return
    key = (ppo_key, jo_key)
    current = _to_float(lookup.get(key))
    if key not in lookup or yy_value > current:
        lookup[key] = yy_value


def _build_jo_ppo_yy_value_lookup(jo_ppo_yy_rows: list[dict]) -> dict[tuple[str, str], list[float]]:
    lookup: dict[tuple[str, str], list[float]] = defaultdict(list)
    for item in jo_ppo_yy_rows or []:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        yy = _to_float(item.get("ppo_yy"))
        if ppo_no and jo_no and yy > 0:
            lookup[(ppo_no, jo_no)].append(yy)
    return {key: sorted(values) for key, values in lookup.items()}


def _load_received_foc_rows(
    cursor,
    factory_code: str,
    ppo_list: list[str],
    bypass_cache: bool = False,
) -> tuple[list[dict], str]:
    view_name = _FOC_VIEW_BY_FACTORY.get(str(factory_code or "").strip().upper(), "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV")
    if not ppo_list:
        return [], view_name

    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    cache_key = (view_name, tuple(clean_ppos))
    now_ts = time.time()
    if not bypass_cache:
        with _received_rows_cache_lock:
            cached = _received_rows_cache.get(cache_key)
            if cached and (now_ts - float(cached.get("ts") or 0.0)) <= float(_RECEIVED_ROWS_CACHE_TTL_SEC):
                return list(cached.get("rows") or []), view_name

    placeholders = ",".join("?" for _ in clean_ppos)
    sql = f"""
        SELECT
            po_no AS ppo_no,
            fabric_type,
            combo_name,
            COALESCE(size_code, '') AS size_code,
            SUM(received_qty) AS received_qty,
            SUM(foc_qty) AS foc_qty
        FROM {view_name}
        WHERE po_no IN ({placeholders})
        GROUP BY po_no, fabric_type, combo_name, COALESCE(size_code, '')
    """
    cursor.execute(sql, clean_ppos)
    rows = _rows_to_dicts(cursor, cursor.fetchall())
    with _received_rows_cache_lock:
        _received_rows_cache[cache_key] = {"ts": now_ts, "rows": rows}
    return rows, view_name


def _load_stock_balance_rows(
    cursor,
    factory_code: str,
    ppo_list: list[str],
    bypass_cache: bool = False,
) -> tuple[list[dict], str, str]:
    """Load current physical on-hand stock for PPOs without replaying ISS rows.

    The RDS inventory view is materially faster than the monthly balance report and
    includes transactions posted after the report's period date.  It is keyed
    by the same PPO/type/combo/size identity used by COI.  ``ON_HAND_QTY`` is
    intentionally used instead of ``AVAILABLE_QTY``: the latter also removes
    WMS reservations, while COI must only deduct fabric that has actually
    left stock (including stock/SR sample issues).
    """
    factory_key = str(factory_code or "").strip().upper()
    # Stock is sourced from the dedicated RDS inventory view.  The factory
    # argument is retained for the caller/cache contract, but no longer
    # selects the legacy ESQ_DATA view.
    view_name = f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}"
    fallback_view = _STOCK_BALANCE_FALLBACK_VIEW_BY_FACTORY.get(
        factory_key,
        "dbo.V_Fabric_Submat_Stock_Data_EGV_EAV",
    )
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not clean_ppos:
        return [], view_name, ""

    cache_key = (view_name, tuple(clean_ppos))
    now_ts = time.time()
    if not bypass_cache:
        with _stock_balance_rows_cache_lock:
            cached = _stock_balance_rows_cache.get(cache_key)
            if cached and (now_ts - float(cached.get("ts") or 0.0)) <= float(_STOCK_BALANCE_ROWS_CACHE_TTL_SEC):
                return (
                    list(cached.get("rows") or []),
                    str(cached.get("view_name") or view_name),
                    str(cached.get("error") or ""),
                )

    placeholders = ",".join("?" for _ in clean_ppos)
    primary_sql = f"""
        SELECT
            PO_NO AS ppo_no,
            Fabric_type AS fabric_type,
            Combo_Name AS combo_name,
            CAST('' AS varchar(30)) AS size_code,
            SUM(CAST(On_Hand_Qty AS float)) AS on_hand_qty,
            SUM(CAST(Allocated_Qty AS float)) AS allocated_qty,
            SUM(CAST(Reserved_Qty AS float)) AS reserved_qty
        FROM {view_name}
        WHERE PO_NO IN ({placeholders})
        GROUP BY PO_NO, Fabric_type, Combo_Name
        OPTION (RECOMPILE)
    """
    try:
        with _connect_stock() as stock_connection:
            stock_connection.timeout = max(
                int(getattr(stock_connection, "timeout", 0) or 0),
                int(STOCK_SQL_QUERY_TIMEOUT_SEC),
            )
            stock_cursor = stock_connection.cursor()
            stock_cursor.execute(primary_sql, clean_ppos)
            rows = _rows_to_dicts(stock_cursor, stock_cursor.fetchall())
        with _stock_balance_rows_cache_lock:
            _stock_balance_rows_cache[cache_key] = {
                "ts": now_ts,
                "rows": rows,
                "view_name": view_name,
                "error": "",
        }
        return rows, view_name, ""
    except Exception as primary_exc:
        # Retry once on a clean RDS connection. Never substitute the legacy
        # ESQ_DATA balance view: a different snapshot can make allocation look
        # verified when the authoritative stock source is unavailable.
        try:
            with _connect_stock() as retry_connection:
                retry_connection.timeout = max(
                    int(getattr(retry_connection, "timeout", 0) or 0),
                    int(STOCK_SQL_QUERY_TIMEOUT_SEC),
                )
                retry_cursor = retry_connection.cursor()
                retry_cursor.execute(primary_sql, clean_ppos)
                rows = _rows_to_dicts(retry_cursor, retry_cursor.fetchall())
            with _stock_balance_rows_cache_lock:
                _stock_balance_rows_cache[cache_key] = {
                    "ts": now_ts,
                    "rows": rows,
                    "view_name": view_name,
                    "error": "",
                }
            return rows, view_name, ""
        except Exception as retry_exc:
            primary_error = (
                f"{type(primary_exc).__name__}: {primary_exc}; "
                f"retry {type(retry_exc).__name__}: {retry_exc}"
            )
        # Keep the old period view available for diagnostics and migration
        # comparisons only. Its rows are returned with an error marker, so the
        # allocation layer still treats stock as unverified.
        try:
            # Preserve the legacy cursor failure boundary for callers that
            # already hold a main-SQL cursor (and for migration diagnostics).
            try:
                cursor.execute("SELECT 1")
            except Exception:
                pass
            fallback_sql = f"""
                SELECT [PO NO] AS ppo_no, [Fabric Type] AS fabric_type,
                       Combo_Name AS combo_name, COALESCE(Size_Code, '') AS size_code,
                       SUM(CAST(QTY AS float)) AS on_hand_qty,
                       CAST(0 AS float) AS allocated_qty, CAST(0 AS float) AS reserved_qty
                FROM {fallback_view}
                WHERE [PO NO] IN ({placeholders})
                GROUP BY [PO NO], [Fabric Type], Combo_Name, COALESCE(Size_Code, '')
            """
            cursor.execute(fallback_sql, clean_ppos)
            rows = _rows_to_dicts(cursor, cursor.fetchall())
            error = f"CURRENT_STOCK_UNAVAILABLE: {primary_error}; {fallback_view} is diagnostic-only"
            with _stock_balance_rows_cache_lock:
                _stock_balance_rows_cache[cache_key] = {"ts": now_ts, "rows": rows, "view_name": fallback_view, "error": error}
            return rows, fallback_view, error
        except Exception as fallback_exc:
            error = f"STOCK_SQL_UNAVAILABLE: {primary_error}; fallback {type(fallback_exc).__name__}: {fallback_exc}"
        with _stock_balance_rows_cache_lock:
            _stock_balance_rows_cache[cache_key] = {
                "ts": now_ts,
                "rows": [],
                "view_name": view_name,
                "error": error,
            }
        return [], view_name, error


def _received_fallback_merge_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get("ppo_no") or "").strip().upper(),
        _normalize_sql_fabric_type_code(item.get("fabric_type")),
        _normalize_combo_key(item.get("combo_name")),
    )


def _load_grn_received_fallback_rows(cursor, ppo_list: list[str]) -> list[dict]:
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not clean_ppos:
        return []

    original_timeout = getattr(cursor, "timeout", None)
    try:
        cursor.timeout = min(int(original_timeout or 30), 30) if original_timeout else 30
    except Exception:
        original_timeout = None

    type_by_code: dict[tuple[str, str], set[str]] = defaultdict(set)
    type_by_combo: dict[tuple[str, str], set[str]] = defaultdict(set)
    try:
        for start in range(0, len(clean_ppos), 200):
            batch = clean_ppos[start : start + 200]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"""
                SELECT
                    [PPO NO] AS ppo_no,
                    [Fabric Code] AS fabric_code,
                    [Fabric Part] AS fabric_part,
                    [Combo Name] AS combo_name
                FROM dbo.V_PPO_Summary_All_After_2015
                WHERE [PPO NO] IN ({placeholders})
                GROUP BY [PPO NO], [Fabric Code], [Fabric Part], [Combo Name]
                """,
                batch,
            )
            for item in _rows_to_dicts(cursor, cursor.fetchall()):
                ppo_no = str(item.get("ppo_no") or "").strip().upper()
                fabric_code = str(item.get("fabric_code") or "").strip().upper()
                combo_name = str(item.get("combo_name") or "").strip()
                fabric_type = _fabric_type_from_part(item.get("fabric_part"))
                if not ppo_no or not fabric_type:
                    continue
                if fabric_code:
                    type_by_code[(ppo_no, fabric_code)].add(fabric_type)
                    type_by_code[(ppo_no, f"{ppo_no}-{fabric_type}-{fabric_code}")].add(fabric_type)
                for combo_key in _combo_match_candidates(combo_name):
                    if combo_key:
                        type_by_combo[(ppo_no, combo_key)].add(fabric_type)
                for color_key in _color_lookup_keys_from_combo(combo_name):
                    if color_key:
                        type_by_combo[(ppo_no, color_key)].add(fabric_type)
    except Exception:
        if original_timeout is not None:
            try:
                cursor.timeout = original_timeout
            except Exception:
                pass
        return []

    output: list[dict] = []
    try:
        for start in range(0, len(clean_ppos), 200):
            batch = clean_ppos[start : start + 200]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"""
                SELECT
                    [PPO NO] AS ppo_no,
                    [Item code] AS item_code,
                    [Fabric Code] AS fabric_code,
                    [Combo Name] AS combo_name,
                    SUM(CASE WHEN ISNUMERIC(GRN_QTY) = 1 THEN CAST(GRN_QTY AS float) ELSE 0 END) AS received_qty
                FROM {_FABRIC_GRN_FALLBACK_VIEW}
                WHERE [PPO NO] IN ({placeholders})
                GROUP BY [PPO NO], [Item code], [Fabric Code], [Combo Name]
                """,
                batch,
            )
            for item in _rows_to_dicts(cursor, cursor.fetchall()):
                ppo_no = str(item.get("ppo_no") or "").strip().upper()
                item_code = str(item.get("item_code") or "").strip().upper()
                fabric_code = str(item.get("fabric_code") or "").strip().upper()
                combo_name = str(item.get("combo_name") or "").strip()
                received_qty = _to_float(item.get("received_qty"))
                if not ppo_no or not combo_name or received_qty <= 0:
                    continue

                type_candidates = set()
                grn_type = _fabric_type_from_grn_identity(item_code, ppo_no) or _fabric_type_from_grn_identity(fabric_code, ppo_no)
                if grn_type:
                    type_candidates.add(grn_type)
                if fabric_code:
                    type_candidates.update(type_by_code.get((ppo_no, fabric_code), set()))
                for combo_key in _combo_match_candidates(combo_name):
                    if combo_key:
                        type_candidates.update(type_by_combo.get((ppo_no, combo_key), set()))
                for color_key in _color_lookup_keys_from_combo(combo_name):
                    if color_key:
                        type_candidates.update(type_by_combo.get((ppo_no, color_key), set()))
                if len(type_candidates) != 1:
                    continue

                output.append(
                    {
                        "ppo_no": ppo_no,
                        "fabric_type": next(iter(type_candidates)),
                        "combo_name": combo_name,
                        "received_qty": received_qty,
                        "foc_qty": 0.0,
                        "source_view": _FABRIC_GRN_FALLBACK_VIEW,
                        "is_grn_fallback": 1,
                    }
                )
    except Exception:
        return []
    finally:
        if original_timeout is not None:
            try:
                cursor.timeout = original_timeout
            except Exception:
                pass
    return output


def _missing_received_fallback_ppos(
    ppo_list: list[str],
    fabric_rows: list[dict],
    received_rows: list[dict],
) -> list[str]:
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not clean_ppos:
        return []

    positive_received_ppos = {
        str(item.get("ppo_no") or "").strip().upper()
        for item in received_rows or []
        if str(item.get("ppo_no") or "").strip() and _display_received_qty(item) > 0
    }
    missing_ppos = set(clean_ppos) - positive_received_ppos

    # A PPO can contain several fabric types/combos while the primary FOC view
    # only contains one of them. Query the GRN fallback for that PPO as well;
    # the merge routine keeps authoritative positive FOC rows and only fills
    # missing identities.
    positive_keys = {
        _received_fallback_merge_key(item)
        for item in received_rows or []
        if _display_received_qty(item) > 0
    }
    for item in fabric_rows or []:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        if not ppo_no or ppo_no not in clean_ppos:
            continue
        probe = {
            "ppo_no": ppo_no,
            "fabric_type": item.get("fabric_type"),
            "combo_name": item.get("combo_name"),
        }
        if _received_fallback_merge_key(probe) not in positive_keys:
            missing_ppos.add(ppo_no)
    return sorted(missing_ppos)


def _merge_grn_received_fallback_rows(cursor, rows: list[dict], ppo_list: list[str]) -> list[dict]:
    # Compatibility shim only. The authoritative Received/FOC view is net of
    # returns; raw GRN cannot safely fill or overwrite it.
    return list(rows or [])


def _shipment_source_key(database: str, table_name: str) -> str:
    return f"{SHIPMENT_SQL_SERVER_HOST}:{database}:{table_name}"


def _latest_date_text(left: object, right: object) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    left_dt = _parse_due_date(left_text)
    right_dt = _parse_due_date(right_text)
    if left_dt and right_dt:
        return left_text if left_dt >= right_dt else right_text
    if left_dt:
        return left_text
    if right_dt:
        return right_text
    return left_text or right_text


def _ship_mode_eta_days(value: object) -> int:
    text = str(value or "").strip().upper()
    if "SEA" in text:
        return 14
    if "TRUCK" in text or "LAND" in text or "LORRY" in text or "BY ROAD" in text:
        return 7
    if "AIR" in text or "DHL" in text or "EXPRESS" in text:
        return 5
    return 0


def _shipment_eta_date(ship_type: object, ship_date: object, delivery_date: object) -> datetime | None:
    ship_dt = _parse_due_date(ship_date)
    delivery_dt = _parse_due_date(delivery_date)
    add_days = _ship_mode_eta_days(ship_type)
    if ship_dt is not None and add_days > 0:
        return ship_dt + timedelta(days=add_days)
    return delivery_dt or ship_dt


def _shipment_eta_rule_source_key(shipment_source_key: str, go_key: str) -> str:
    return f"SHIPMENT_ON_WAY_ETA_V{_SHIPMENT_ETA_RULE_VERSION}:{shipment_source_key}:{go_key}"


def _load_persisted_shipment_on_way_rows(source_key: str, ppo_list: list[str]) -> list[dict]:
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not source_key or not clean_ppos:
        return []
    try:
        _ensure_snapshot_tables()
        placeholders = ",".join("?" for _ in clean_ppos)
        with _snapshot_connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ppo_no, fabric_type, combo_name, shipment_qty, foc_qty,
                       eta_date, ship_type, source_table, synced_at
                FROM sql_shipment_on_way
                WHERE source_key = ? AND ppo_no IN ({placeholders})
                ORDER BY ppo_no, fabric_type, combo_name
                """,
                [source_key, *clean_ppos],
            ).fetchall()
        return [
            {
                "ppo_no": str(row["ppo_no"] or ""),
                "fabric_type": str(row["fabric_type"] or ""),
                "combo_name": str(row["combo_name"] or ""),
                "shipment_qty": _to_float(row["shipment_qty"]),
                "foc_qty": _to_float(row["foc_qty"]),
                "eta_date": str(row["eta_date"] or ""),
                "ship_type": str(row["ship_type"] or ""),
                "source_table": str(row["source_table"] or ""),
                "last_good_synced_at": str(row["synced_at"] or ""),
                "is_last_known_good": True,
            }
            for row in rows
        ]
    except Exception:
        return []


def _load_shipment_on_way_rows(
    factory_code: str,
    ppo_list: list[str],
    bypass_cache: bool = False,
) -> tuple[list[dict], str, str, str]:
    database, table_name, source_factory = _shipment_source_for_factory(factory_code)
    source_key = _shipment_source_key(database, table_name)
    source_table = f"{database}.{table_name}"
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not clean_ppos:
        return [], source_key, source_table, ""

    cache_key = (source_key, tuple(clean_ppos))
    now_ts = time.time()
    expired_last_good_rows: list[dict] = []
    if not bypass_cache:
        with _shipment_on_way_cache_lock:
            cached = _shipment_on_way_cache.get(cache_key)
            if cached and (now_ts - float(cached.get("ts") or 0.0)) <= float(_SHIPMENT_ON_WAY_CACHE_TTL_SEC):
                return list(cached.get("rows") or []), source_key, source_table, str(cached.get("error") or "")
            if cached and list(cached.get("rows") or []):
                expired_last_good_rows = list(cached.get("rows") or [])

    output_by_key: dict[tuple[str, str, str], dict] = {}
    try:
        with _connect_shipment(database) as shipment_conn:
            cursor = shipment_conn.cursor()
            for start in range(0, len(clean_ppos), 200):
                batch = clean_ppos[start : start + 200]
                placeholders = ",".join("?" for _ in batch)
                sql = f"""
                    SELECT
                        ppo_no,
                        [usage] AS fabric_type,
                        MAX([description]) AS fabric_part,
                        combo AS combo_name,
                        SUM(CASE WHEN ISNUMERIC(qty) = 1 THEN CAST(qty AS float) ELSE 0 END) AS shipment_qty,
                        SUM(CASE WHEN ISNUMERIC(foc_qty) = 1 THEN CAST(foc_qty AS float) ELSE 0 END) AS foc_qty,
                        CASE WHEN ISDATE(ship_date) = 1 THEN CAST(ship_date AS datetime) ELSE NULL END AS ship_date,
                        CASE WHEN ISDATE(delivery_date) = 1 THEN CAST(delivery_date AS datetime) ELSE NULL END AS delivery_date,
                        ship_type
                    FROM {table_name}
                    WHERE ppo_no IN ({placeholders})
                    GROUP BY ppo_no, [usage], combo, ship_type, ship_date, delivery_date
                """
                cursor.execute(sql, batch)
                for item in _rows_to_dicts(cursor, cursor.fetchall()):
                    ppo_key = str(item.get("ppo_no") or "").strip().upper()
                    fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type")) or _fabric_type_from_part(
                        item.get("fabric_part")
                    )
                    combo_name = str(item.get("combo_name") or "").strip()
                    combo_key = combo_name
                    if not ppo_key or not fabric_type or not combo_key:
                        continue
                    key = (ppo_key, fabric_type, combo_key)
                    current = output_by_key.setdefault(
                        key,
                        {
                            "ppo_no": ppo_key,
                            "fabric_type": fabric_type,
                            "combo_name": combo_name,
                            "shipment_qty": 0.0,
                            "foc_qty": 0.0,
                            "eta_date": "",
                            "ship_type": "",
                            "source_factory": source_factory,
                            "source_table": source_table,
                        },
                    )
                    current["shipment_qty"] = round(
                        _to_float(current.get("shipment_qty")) + _to_float(item.get("shipment_qty")),
                        3,
                    )
                    current["foc_qty"] = round(
                        _to_float(current.get("foc_qty")) + _to_float(item.get("foc_qty")),
                        3,
                    )
                    row_eta = _shipment_eta_date(item.get("ship_type"), item.get("ship_date"), item.get("delivery_date"))
                    current_eta = _parse_due_date(current.get("eta_date"))
                    if row_eta is not None and (current_eta is None or row_eta >= current_eta):
                        current["eta_date"] = row_eta.strftime("%Y-%m-%d %H:%M:%S")
                        current["ship_type"] = str(item.get("ship_type") or "").strip()
                    elif not current.get("ship_type"):
                        current["ship_type"] = str(item.get("ship_type") or "").strip()
        rows = list(output_by_key.values())
        with _shipment_on_way_cache_lock:
            _shipment_on_way_cache[cache_key] = {"ts": now_ts, "rows": rows, "error": ""}
        return rows, source_key, source_table, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        last_good_rows = expired_last_good_rows or _load_persisted_shipment_on_way_rows(source_key, clean_ppos)
        with _shipment_on_way_cache_lock:
            _shipment_on_way_cache[cache_key] = {"ts": now_ts, "rows": last_good_rows, "error": error}
        return last_good_rows, source_key, source_table, error


def _load_ppo_component_yy_rows_sql(cursor, ppo: str) -> list[dict]:
    ppo_key = str(ppo or "").strip().upper()
    if not ppo_key:
        return []

    queries = [
        """
        SELECT
            [PPO NO] AS ppo_no,
            [Fabric Type Code] AS fabric_type,
            [Fabric Part] AS fabric_part,
            [Combo Name] AS fabric_combo,
            CAST([YY] AS float) AS marker_yy,
            CAST([PPO YY] AS float) AS ppo_yy
        FROM dbo.V_Knit_PPO_Infor
        WHERE [PPO NO] = ?
          AND ([YY] IS NOT NULL OR [PPO YY] IS NOT NULL)
        """,
        """
        SELECT
            [PPO NO] AS ppo_no,
            [Fabric Type Code] AS fabric_type,
            [Fabric Type] AS fabric_part,
            [Combo Name] AS fabric_combo,
            CAST([Marker YY] AS float) AS marker_yy,
            CAST([Marker YY] AS float) AS ppo_yy
        FROM dbo.V_Woven_PPO_Infor
        WHERE [PPO NO] = ?
          AND [Marker YY] IS NOT NULL
        """,
    ]

    results: list[dict] = []
    for sql in queries:
        try:
            cursor.execute(sql, ppo_key)
            rows = _rows_to_dicts(cursor, cursor.fetchall())
        except Exception:
            continue
        for item in rows:
            fabric_part = str(item.get("fabric_part") or "").strip()
            fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type")) or _fabric_type_from_part(fabric_part)
            marker_yy = _to_float(item.get("marker_yy"))
            ppo_yy = _to_float(item.get("ppo_yy"))
            if not fabric_type or (marker_yy <= 0 and ppo_yy <= 0):
                continue
            results.append(
                {
                    "ppo": ppo_key,
                    "fabric_type": fabric_type,
                    "fabric_part": _format_fabric_part_with_type(fabric_part),
                    "color_code": "",
                    "fabric_combo": str(item.get("fabric_combo") or "").strip(),
                    "fabric_color": str(item.get("fabric_combo") or "").strip(),
                    "fabric_code": "",
                    "gmt_qty": 0.0,
                    "fabric_total_qty": 0.0,
                    "ppo_order_qty": 0.0,
                    "detail_marker_yy": marker_yy or ppo_yy,
                    "detail_ppo_yy": ppo_yy or marker_yy,
                }
            )
    return results


def _load_ppo_detail_rows_sql(cursor, ppo: str) -> list[dict]:
    ppo_key = str(ppo or "").strip().upper()
    if not ppo_key:
        return []
    cursor.execute(
        """
        SELECT
            [PPO NO] AS ppo_no,
            [Fabric Part] AS fabric_part,
            [Garment Color Code] AS color_code,
            [Combo Name] AS fabric_combo,
            [Fabric Code] AS fabric_code,
            SUM(CAST([Garment Qty] AS float)) AS gmt_qty,
            SUM(CAST([Order Qty] AS float)) AS ppo_order_qty
        FROM dbo.V_PPO_Summary_All_After_2015
        WHERE [PPO NO] = ?
        GROUP BY [PPO NO], [Fabric Part], [Garment Color Code], [Combo Name], [Fabric Code]
        ORDER BY [Fabric Part], [Garment Color Code], [Combo Name]
        """,
        ppo_key,
    )
    rows = _rows_to_dicts(cursor, cursor.fetchall())
    component_yy_rows = _load_ppo_component_yy_rows_sql(cursor, ppo_key)
    component_yy_lookup = _build_ppo_detail_yy_lookup({ppo_key: component_yy_rows})
    results: list[dict] = []
    for item in rows:
        fabric_part = str(item.get("fabric_part") or "").strip()
        fabric_type = _fabric_type_from_part(fabric_part)
        gmt_qty = _to_float(item.get("gmt_qty"))
        ppo_order_qty = _to_float(item.get("ppo_order_qty"))
        result = {
            "ppo": ppo_key,
            "fabric_type": fabric_type,
            "fabric_part": _format_fabric_part_with_type(fabric_part),
            "color_code": str(item.get("color_code") or "").strip(),
            "fabric_combo": str(item.get("fabric_combo") or "").strip(),
            "fabric_color": str(item.get("fabric_combo") or "").strip(),
            "fabric_code": str(item.get("fabric_code") or "").strip(),
            "gmt_qty": round(gmt_qty, 3),
            "fabric_total_qty": round(ppo_order_qty, 3),
            "ppo_order_qty": round(ppo_order_qty, 3),
        }
        detail_yy = _resolve_ppo_detail_yy_for_row(
            component_yy_lookup,
            ppo_key,
            fabric_type,
            result["color_code"],
            result["fabric_color"],
            result["fabric_combo"],
        )
        if detail_yy:
            result["detail_marker_yy"] = _to_float(detail_yy.get("marker_yy"))
            result["detail_ppo_yy"] = _to_float(detail_yy.get("ppo_yy"))
        results.append(result)

    existing_keys: set[tuple[str, str, str]] = set()
    for row in results:
        for color_key in _sheet_row_ppo_color_key_candidates(row.get("color_code"), row.get("fabric_color"), row.get("fabric_combo")):
            if color_key:
                existing_keys.add((ppo_key, str(row.get("fabric_type") or "").strip().upper(), color_key))

    for yy_row in component_yy_rows:
        row_keys = {
            (ppo_key, str(yy_row.get("fabric_type") or "").strip().upper(), color_key)
            for color_key in _sheet_row_ppo_color_key_candidates(
                yy_row.get("color_code"),
                yy_row.get("fabric_color"),
                yy_row.get("fabric_combo"),
            )
            if color_key
        }
        if row_keys and row_keys <= existing_keys:
            continue
        results.append(
            {
                **yy_row,
                "gmt_qty": round(_to_float(yy_row.get("gmt_qty")), 3),
                "fabric_total_qty": round(_to_float(yy_row.get("fabric_total_qty")), 3),
                "ppo_order_qty": round(_to_float(yy_row.get("ppo_order_qty")), 3),
            }
        )
    return results


def _load_ppo_order_totals_sql(
    cursor,
    ppo_list: list[str],
    errors: list[str] | None = None,
) -> dict[tuple[str, str, str], dict]:
    clean_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_list if str(ppo or "").strip()})
    if not clean_ppos:
        return {}
    totals: dict[tuple[str, str, str], dict] = {}
    for start in range(0, len(clean_ppos), _PPO_ENRICHMENT_BATCH_SIZE):
        batch = clean_ppos[start : start + _PPO_ENRICHMENT_BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        sql = f"""
        SELECT
            [PPO NO] AS ppo_no,
            [Fabric Part] AS fabric_part,
            [Garment Color Code] AS color_code,
            [Combo Name] AS fabric_combo,
            SUM(CAST([Order Qty] AS float)) AS ppo_order_qty
        FROM dbo.V_PPO_Summary_All_After_2015
        WHERE [PPO NO] IN ({placeholders})
        GROUP BY [PPO NO], [Fabric Part], [Garment Color Code], [Combo Name]
        """
        try:
            cursor.execute(sql, batch)
            rows = _rows_to_dicts(cursor, cursor.fetchall())
        except Exception as exc:
            if errors is None:
                raise
            classification = classify_source_error(exc)
            errors.append(
                f"{classification['code']} PPO batch {start // _PPO_ENRICHMENT_BATCH_SIZE + 1}"
            )
            # Successful earlier chunks remain usable. The failed chunk is
            # retried by the next source cycle with worker backoff.
            break
        for item in rows:
            ppo_key = str(item.get("ppo_no") or "").strip().upper()
            fabric_part = str(item.get("fabric_part") or "").strip()
            fabric_type = _fabric_type_from_part(fabric_part)
            if not ppo_key or not fabric_type:
                continue
            qty = _to_float(item.get("ppo_order_qty"))
            color_code = str(item.get("color_code") or "").strip()
            fabric_combo = str(item.get("fabric_combo") or "").strip()
            alias_keys = _sheet_row_ppo_color_key_candidates(color_code, "", fabric_combo)
            # Empty color key keeps a safe by-type fallback; row lookup always tries color aliases first.
            for color_key in ["", *alias_keys]:
                bucket = totals.setdefault(
                    (ppo_key, fabric_type, color_key),
                    {
                        "ppo_order_qty": 0.0,
                        "fabric_part": _format_fabric_part_with_type(fabric_part),
                        "color_code": color_code,
                        "fabric_combo": fabric_combo,
                    },
                )
                bucket["ppo_order_qty"] += qty
                if not bucket.get("fabric_part") and fabric_part:
                    bucket["fabric_part"] = _format_fabric_part_with_type(fabric_part)
    for value in totals.values():
        value["ppo_order_qty"] = round(_to_float(value.get("ppo_order_qty")), 3)
    return totals


def _source_cache_ppos(*row_groups: list[dict]) -> list[str]:
    ppos: set[str] = set()
    for rows in row_groups:
        for row in rows or []:
            for field in ("ppo_no", "PPO", "ppo"):
                value = str((row or {}).get(field) or "").strip().upper()
                if value:
                    ppos.add(value)
    return sorted(ppos)


def _mapped_ppo_set(ppo_mapping: list[dict]) -> set[str]:
    return {
        str((row or {}).get("ppo_no") or (row or {}).get("PPO") or (row or {}).get("ppo") or "").strip().upper()
        for row in (ppo_mapping or [])
        if str((row or {}).get("ppo_no") or (row or {}).get("PPO") or (row or {}).get("ppo") or "").strip()
    }


def _row_ppo_key(row: dict) -> str:
    for field in ("ppo_no", "PPO", "ppo"):
        value = str((row or {}).get(field) or "").strip().upper()
        if value:
            return value
    return ""


def _filter_rows_to_mapped_ppos(rows: list[dict], mapped_ppos: set[str]) -> list[dict]:
    if not mapped_ppos:
        return list(rows or [])
    filtered: list[dict] = []
    for row in rows or []:
        ppo_key = _row_ppo_key(row)
        if not ppo_key or ppo_key in mapped_ppos:
            filtered.append(row)
    return filtered


def _filter_detail_rows_to_mapped_ppos(rows_by_ppo: dict[str, list[dict]], mapped_ppos: set[str]) -> dict[str, list[dict]]:
    if not mapped_ppos:
        return dict(rows_by_ppo or {})
    return {
        str(ppo_no or "").strip().upper(): list(rows or [])
        for ppo_no, rows in (rows_by_ppo or {}).items()
        if str(ppo_no or "").strip().upper() in mapped_ppos
    }


def _source_cache_meta(
    conn: sqlite3.Connection,
    source_key: str,
    row_count: int,
    synced_at: str,
    error: str = "",
    *,
    content_changed: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO sql_source_sync (
            source_key, synced_at, last_checked_at, row_count, source_status, last_error
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            synced_at = CASE
                WHEN excluded.source_status = 'OK' AND ? = 1 THEN excluded.synced_at
                ELSE sql_source_sync.synced_at
            END,
            row_count = CASE
                WHEN excluded.source_status = 'OK' THEN excluded.row_count
                ELSE sql_source_sync.row_count
            END,
            last_checked_at = excluded.last_checked_at,
            source_status = excluded.source_status,
            last_error = excluded.last_error
        """,
        (
            str(source_key or "").strip().upper(),
            synced_at,
            synced_at,
            int(row_count or 0),
            "ERROR" if error else "OK",
            str(error or ""),
            1 if content_changed else 0,
        ),
    )


def _save_go_source_cache_bundle(go: str, bundle: dict) -> bool:
    go_key = str(go or "").strip().upper()
    if not go_key or not isinstance(bundle, dict) or not bundle.get("ok"):
        return False

    _ensure_snapshot_tables()
    synced_at = str(bundle.get("source_synced_at") or _snapshot_now())
    head = dict(bundle.get("head") or {})
    colors = list(bundle.get("colors") or [])
    lots = list(bundle.get("lots") or [])
    jo_color_qty_rows = list(bundle.get("jo_color_qty_rows") or [])
    ppo_mapping = list(bundle.get("ppo_mapping") or [])
    fabric_rows = list(bundle.get("fabric_rows") or [])
    sql_bom_rows = list(bundle.get("sql_bom_rows") or [])
    jo_ppo_yy_rows = list(bundle.get("jo_ppo_yy_rows") or [])
    received_rows = list(bundle.get("received_rows") or [])
    received_rows_aggregate = _aggregate_received_rows(received_rows)
    received_view = str(bundle.get("received_view") or "").strip()
    stock_balance_rows = list(bundle.get("stock_balance_rows") or [])
    stock_balance_view = str(bundle.get("stock_balance_view") or "").strip()
    stock_balance_error = str(bundle.get("stock_balance_error") or "").strip()
    shipment_on_way_rows = list(bundle.get("shipment_on_way_rows") or [])
    shipment_source_key = str(bundle.get("shipment_source_key") or "").strip()
    shipment_source_table = str(bundle.get("shipment_source_table") or "").strip()
    shipment_on_way_error = str(bundle.get("shipment_on_way_error") or "").strip()
    volatile_sources_refreshed = bool(bundle.get("volatile_sources_refreshed", True))
    ppo_order_totals = dict(bundle.get("ppo_order_totals") or {})
    ppo_order_totals_refreshed = bool(bundle.get("ppo_order_totals_refreshed", True))
    ppo_detail_rows_by_ppo = dict(bundle.get("ppo_detail_rows_by_ppo") or {})
    topology_error = ""

    with _snapshot_connect() as conn:
        feed_row = conn.execute(
            """
            SELECT COALESCE(modify_date, create_date, '') AS source_stamp
            FROM go_feed
            WHERE go_no = ?
            """,
            (go_key,),
        ).fetchone()
        feed_stamp = str(feed_row["source_stamp"] or "") if feed_row else ""
        incoming_stamp = str(head.get("modify_date") or head.get("create_date") or "")
        if feed_stamp and (not incoming_stamp or incoming_stamp < feed_stamp):
            return False

        # A live read can return the GO header while its PPO/fabric joins are
        # temporarily empty. Treating that partial response as authoritative
        # used to delete a valid staged topology and made the COI sheet appear
        # and disappear between refreshes. Keep the coherent last-known-good
        # structural rows, but mark the source incomplete so the worker retries.
        topology_components = {
            "colors": ("sql_go_colors", colors),
            "lots": ("sql_go_lots", lots),
            "jo_color_qty": ("sql_go_jo_color_qty", jo_color_qty_rows),
            "ppo_mapping": ("sql_go_ppo_mapping", ppo_mapping),
            "fabric": ("sql_go_fabric_rows", fabric_rows),
            "bom": ("sql_go_bom_rows", sql_bom_rows),
            "jo_ppo_yy": ("sql_go_jo_ppo_yy", jo_ppo_yy_rows),
        }
        missing_cached_components = [
            component
            for component, (table_name, incoming_rows) in topology_components.items()
            if not incoming_rows
            and conn.execute(
                f"SELECT 1 FROM {table_name} WHERE go_no = ? LIMIT 1",
                (go_key,),
            ).fetchone()
        ]
        if missing_cached_components:
            topology_error = (
                "SOURCE_INCOMPLETE: live GO topology omitted cached components "
                f"({', '.join(missing_cached_components)}); "
                "retained last-known-good cache"
            )

            def _cached_rows(table_name: str) -> list[dict]:
                rows = conn.execute(
                    f"SELECT * FROM {table_name} WHERE go_no = ? ORDER BY row_index",
                    (go_key,),
                ).fetchall()
                result: list[dict] = []
                for row in rows:
                    item = dict(row)
                    for field in ("go_no", "row_index", "synced_at"):
                        item.pop(field, None)
                    result.append(item)
                return result

            colors = _cached_rows("sql_go_colors")
            lots = _cached_rows("sql_go_lots")
            jo_color_qty_rows = _cached_rows("sql_go_jo_color_qty")
            ppo_mapping = _cached_rows("sql_go_ppo_mapping")
            fabric_rows = _cached_rows("sql_go_fabric_rows")
            sql_bom_rows = _cached_rows("sql_go_bom_rows")
            jo_ppo_yy_rows = _cached_rows("sql_go_jo_ppo_yy")
            volatile_sources_refreshed = False
            ppo_order_totals_refreshed = False
            bundle.update(
                {
                    "colors": colors,
                    "lots": lots,
                    "jo_color_qty_rows": jo_color_qty_rows,
                    "ppo_mapping": ppo_mapping,
                    "fabric_rows": fabric_rows,
                    "sql_bom_rows": sql_bom_rows,
                    "jo_ppo_yy_rows": jo_ppo_yy_rows,
                    "volatile_sources_refreshed": False,
                    "ppo_order_totals_refreshed": False,
                    "source_mode": "sqlite-source-cache",
                    "source_live_error": topology_error,
                }
            )

        ppo_list = _source_cache_ppos(
            ppo_mapping,
            fabric_rows,
            jo_ppo_yy_rows,
            received_rows,
            stock_balance_rows,
            shipment_on_way_rows,
        )
        head_cursor = conn.execute(
            """
            INSERT INTO sql_go_head (
                go_no, style_no, style_desc, season, factory_code, status,
                customer_code, create_date, modify_date, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(go_no) DO UPDATE SET
                style_no = excluded.style_no,
                style_desc = excluded.style_desc,
                season = excluded.season,
                factory_code = excluded.factory_code,
                status = excluded.status,
                customer_code = excluded.customer_code,
                create_date = excluded.create_date,
                modify_date = excluded.modify_date,
                synced_at = excluded.synced_at
            WHERE excluded.synced_at >= sql_go_head.synced_at
              AND (
                    COALESCE(sql_go_head.modify_date, sql_go_head.create_date, '') = ''
                 OR COALESCE(excluded.modify_date, excluded.create_date, '') >=
                    COALESCE(sql_go_head.modify_date, sql_go_head.create_date, '')
              )
            """,
            (
                go_key,
                str(head.get("style_no") or ""),
                str(head.get("style_desc") or ""),
                str(head.get("season") or ""),
                str(head.get("factory_code") or ""),
                str(head.get("status") or ""),
                str(head.get("customer_code") or ""),
                str(_to_jsonable(head.get("create_date")) or ""),
                str(_to_jsonable(head.get("modify_date")) or ""),
                synced_at,
            ),
        )
        if int(head_cursor.rowcount or 0) <= 0:
            return False

        for table_name in (
            "sql_go_colors",
            "sql_go_lots",
            "sql_go_jo_color_qty",
            "sql_go_ppo_mapping",
            "sql_go_fabric_rows",
            "sql_go_bom_rows",
            "sql_go_jo_ppo_yy",
        ):
            conn.execute(f"DELETE FROM {table_name} WHERE go_no = ?", (go_key,))

        conn.executemany(
            """
            INSERT INTO sql_go_colors (go_no, row_index, color_code, color_desc, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    str(row.get("color_code") or ""),
                    str(row.get("color_desc") or ""),
                    synced_at,
                )
                for index, row in enumerate(colors)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_lots (
                go_no, row_index, lot_no, jo_no, qty, buyer_po_del_date,
                buyer_po_no, short_pct, over_pct, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    _to_int(row.get("lot_no")),
                    str(row.get("jo_no") or ""),
                    _to_float(row.get("qty")),
                    str(_to_jsonable(row.get("buyer_po_del_date")) or ""),
                    str(row.get("buyer_po_no") or ""),
                    _to_float(row.get("short_pct")),
                    _to_float(row.get("over_pct")),
                    synced_at,
                )
                for index, row in enumerate(lots)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_jo_color_qty (
                go_no, row_index, lot_no, jo_no, color_code, color_desc,
                customer_name, brand_name, brand_owner, customer_label,
                buyer_po_del_date, qty, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    _to_int(row.get("lot_no")),
                    str(row.get("jo_no") or ""),
                    str(row.get("color_code") or ""),
                    str(row.get("color_desc") or ""),
                    str(row.get("customer_name") or ""),
                    str(row.get("brand_name") or ""),
                    str(row.get("brand_owner") or ""),
                    str(row.get("customer_label") or ""),
                    str(_to_jsonable(row.get("buyer_po_del_date")) or ""),
                    _to_float(row.get("qty")),
                    synced_at,
                )
                for index, row in enumerate(jo_color_qty_rows)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_ppo_mapping (go_no, row_index, ppo_no, lot_no, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    str(row.get("ppo_no") or ""),
                    _to_int(row.get("lot_no")),
                    synced_at,
                )
                for index, row in enumerate(ppo_mapping)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_fabric_rows (
                go_no, row_index, lot_no, ppo_no, fabric_type, color_code,
                combo_name, ppo_yy, marker_yy, related_jo_list, remarks, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    _to_int(row.get("lot_no")),
                    str(row.get("ppo_no") or ""),
                    str(row.get("fabric_type") or ""),
                    str(row.get("color_code") or ""),
                    str(row.get("combo_name") or ""),
                    _to_float(row.get("ppo_yy")),
                    _to_float(row.get("marker_yy")),
                    str(row.get("related_jo_list") or ""),
                    str(row.get("remarks") or ""),
                    synced_at,
                )
                for index, row in enumerate(fabric_rows)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_bom_rows (
                go_no, row_index, style_color_code, style_color_desc, fabric_type_cd,
                fabric_type_desc, combo_name, yy, marker_yy, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    str(row.get("style_color_code") or ""),
                    str(row.get("style_color_desc") or ""),
                    str(row.get("fabric_type_cd") or ""),
                    str(row.get("fabric_type_desc") or ""),
                    str(row.get("combo_name") or ""),
                    _to_float(row.get("yy")),
                    _to_float(row.get("marker_yy")),
                    synced_at,
                )
                for index, row in enumerate(sql_bom_rows)
            ],
        )
        conn.executemany(
            """
            INSERT INTO sql_go_jo_ppo_yy (go_no, row_index, lot_no, jo_no, ppo_no, ppo_yy, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    go_key,
                    index,
                    _to_int(row.get("lot_no")),
                    str(row.get("jo_no") or ""),
                    str(row.get("ppo_no") or ""),
                    _to_float(row.get("ppo_yy")),
                    synced_at,
                )
                for index, row in enumerate(jo_ppo_yy_rows)
            ],
        )

        if volatile_sources_refreshed and received_view and ppo_list:
            placeholders = ",".join("?" for _ in ppo_list)
            conn.execute(
                f"DELETE FROM sql_received_foc WHERE view_name = ? AND ppo_no IN ({placeholders})",
                [received_view, *ppo_list],
            )
            conn.execute(
                f"DELETE FROM sql_received_foc_by_size WHERE view_name = ? AND ppo_no IN ({placeholders})",
                [received_view, *ppo_list],
            )
        if volatile_sources_refreshed:
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_received_foc (
                    view_name, ppo_no, fabric_type, combo_name, received_qty, foc_qty, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        received_view,
                        str(row.get("ppo_no") or "").strip().upper(),
                        _normalize_sql_fabric_type_code(row.get("fabric_type")),
                        str(row.get("combo_name") or "").strip(),
                        _to_float(row.get("received_qty")),
                        _to_float(row.get("foc_qty")),
                        synced_at,
                    )
                    for row in received_rows_aggregate
                    if received_view and str(row.get("ppo_no") or "").strip()
                ],
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_received_foc_by_size (
                    view_name, ppo_no, fabric_type, combo_name, size_code,
                    received_qty, foc_qty, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        received_view,
                        str(row.get("ppo_no") or "").strip().upper(),
                        _normalize_sql_fabric_type_code(row.get("fabric_type")),
                        str(row.get("combo_name") or "").strip(),
                        str(row.get("size_code") or "").strip().upper(),
                        _to_float(row.get("received_qty")),
                        _to_float(row.get("foc_qty")),
                        synced_at,
                    )
                    for row in received_rows
                    if received_view and str(row.get("ppo_no") or "").strip()
                ],
            )

        # Net physical stock is the allocation source. Replace a PPO's rows
        # only after a successful stock query; an outage must not be mistaken
        # for zero available fabric.
        if volatile_sources_refreshed and ppo_list and not stock_balance_error:
            placeholders = ",".join("?" for _ in ppo_list)
            conn.execute(
                f"DELETE FROM sql_stock_balance WHERE ppo_no IN ({placeholders})",
                ppo_list,
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_stock_balance (
                    ppo_no, fabric_type, combo_name, size_code,
                    on_hand_qty, allocated_qty, reserved_qty,
                    source_view, source_as_of, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row.get("ppo_no") or "").strip().upper(),
                        _normalize_sql_fabric_type_code(row.get("fabric_type")),
                        str(row.get("combo_name") or "").strip(),
                        str(row.get("size_code") or "").strip().upper(),
                        _to_float(row.get("on_hand_qty")),
                        _to_float(row.get("allocated_qty")),
                        _to_float(row.get("reserved_qty")),
                        stock_balance_view,
                        str(_to_jsonable(row.get("source_as_of")) or ""),
                        synced_at,
                    )
                    for row in stock_balance_rows
                    if str(row.get("ppo_no") or "").strip()
                ],
            )
        # A transient shipment-server timeout is not evidence that every
        # shipment disappeared. Keep last-known-good rows and mark the source
        # stale/error in sql_source_sync; replace rows only after a successful
        # query (including a successful zero-row result).
        if (
            volatile_sources_refreshed
            and shipment_source_key
            and ppo_list
            and not shipment_on_way_error
        ):
            placeholders = ",".join("?" for _ in ppo_list)
            conn.execute(
                f"DELETE FROM sql_shipment_on_way WHERE source_key = ? AND ppo_no IN ({placeholders})",
                [shipment_source_key, *ppo_list],
            )
        if volatile_sources_refreshed and not shipment_on_way_error:
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_shipment_on_way (
                    source_key, ppo_no, fabric_type, combo_name, shipment_qty,
                    foc_qty, eta_date, ship_type, source_table, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        shipment_source_key,
                        str(row.get("ppo_no") or "").strip().upper(),
                        _normalize_sql_fabric_type_code(row.get("fabric_type")),
                        str(row.get("combo_name") or "").strip(),
                        _to_float(row.get("shipment_qty")),
                        _to_float(row.get("foc_qty")),
                        str(row.get("eta_date") or "").strip(),
                        str(row.get("ship_type") or "").strip(),
                        str(row.get("source_table") or shipment_source_table).strip(),
                        synced_at,
                    )
                    for row in shipment_on_way_rows
                    if shipment_source_key and str(row.get("ppo_no") or "").strip()
                ],
            )

        if ppo_order_totals_refreshed and ppo_list:
            placeholders = ",".join("?" for _ in ppo_list)
            conn.execute(
                f"DELETE FROM sql_ppo_order_totals WHERE ppo_no IN ({placeholders})",
                ppo_list,
            )
            conn.execute(
                f"DELETE FROM sql_ppo_order_totals_by_color WHERE ppo_no IN ({placeholders})",
                ppo_list,
            )
        aggregate_totals = {
            (ppo_no, fabric_type): payload
            for (ppo_no, fabric_type, color_key), payload in ppo_order_totals.items()
            if not str(color_key or "").strip()
        }
        if ppo_order_totals_refreshed:
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_ppo_order_totals (
                    ppo_no, fabric_type, fabric_part, ppo_order_qty, synced_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(ppo_no or "").strip().upper(),
                        str(fabric_type or "").strip().upper(),
                        str(payload.get("fabric_part") or ""),
                        _to_float(payload.get("ppo_order_qty")),
                        synced_at,
                    )
                    for (ppo_no, fabric_type), payload in aggregate_totals.items()
                    if str(ppo_no or "").strip() and str(fabric_type or "").strip()
                ],
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO sql_ppo_order_totals_by_color (
                    ppo_no, fabric_type, color_key, color_code, fabric_combo,
                    fabric_part, ppo_order_qty, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(ppo_no or "").strip().upper(),
                        str(fabric_type or "").strip().upper(),
                        str(color_key or "").strip().upper(),
                        str(payload.get("color_code") or ""),
                        str(payload.get("fabric_combo") or ""),
                        str(payload.get("fabric_part") or ""),
                        _to_float(payload.get("ppo_order_qty")),
                        synced_at,
                    )
                    for (ppo_no, fabric_type, color_key), payload in ppo_order_totals.items()
                    if str(ppo_no or "").strip() and str(fabric_type or "").strip()
                ],
            )

        detail_ppos = sorted({str(ppo or "").strip().upper() for ppo in ppo_detail_rows_by_ppo if str(ppo or "").strip()})
        if detail_ppos:
            placeholders = ",".join("?" for _ in detail_ppos)
            conn.execute(
                f"DELETE FROM sql_ppo_detail_rows WHERE ppo_no IN ({placeholders})",
                detail_ppos,
            )
        detail_params = []
        for ppo_no, detail_rows in ppo_detail_rows_by_ppo.items():
            ppo_key = str(ppo_no or "").strip().upper()
            if not ppo_key:
                continue
            for index, row in enumerate(detail_rows or []):
                detail_params.append(
                    (
                        ppo_key,
                        index,
                        str(row.get("fabric_type") or ""),
                        str(row.get("fabric_part") or ""),
                        str(row.get("color_code") or ""),
                        str(row.get("fabric_combo") or ""),
                        str(row.get("fabric_color") or ""),
                        str(row.get("fabric_code") or ""),
                        _to_float(row.get("gmt_qty")),
                        _to_float(row.get("fabric_total_qty")),
                        _to_float(row.get("ppo_order_qty")),
                        synced_at,
                    )
                )
        conn.executemany(
            """
            INSERT INTO sql_ppo_detail_rows (
                ppo_no, row_index, fabric_type, fabric_part, color_code,
                fabric_combo, fabric_color, fabric_code, gmt_qty,
                fabric_total_qty, ppo_order_qty, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            detail_params,
        )

        _source_cache_meta(
            conn,
            f"GO:{go_key}",
            len(fabric_rows),
            synced_at,
            topology_error,
            content_changed=not bool(topology_error),
        )
        if volatile_sources_refreshed and received_view:
            _source_cache_meta(conn, f"RECEIVED:{received_view}:{go_key}", len(received_rows), synced_at)
        if volatile_sources_refreshed and stock_balance_view:
            _source_cache_meta(
                conn,
                f"STOCK_BALANCE:{stock_balance_view}:{go_key}",
                len(stock_balance_rows),
                synced_at,
                stock_balance_error,
            )
            # A successful current-inventory query supersedes any older
            # diagnostic stock-source error for this GO. Leaving that stale
            # error behind makes a healthy cache look permanently invalid and
            # triggers needless rebuilds/zero-allocation screens.
            if not stock_balance_error:
                conn.execute(
                    """
                    DELETE FROM sql_source_sync
                    WHERE source_key LIKE ?
                      AND source_key <> ?
                    """,
                    (
                        f"STOCK_BALANCE:%:{go_key}",
                        f"STOCK_BALANCE:{stock_balance_view}:{go_key}".upper(),
                    ),
                )
        if volatile_sources_refreshed and shipment_source_key:
            _source_cache_meta(
                conn,
                f"SHIPMENT_ON_WAY:{shipment_source_key}:{go_key}",
                len(shipment_on_way_rows),
                synced_at,
                shipment_on_way_error,
            )
            _source_cache_meta(
                conn,
                _shipment_eta_rule_source_key(shipment_source_key, go_key),
                len(shipment_on_way_rows),
                synced_at,
                shipment_on_way_error,
            )
        if not volatile_sources_refreshed:
            # The PPO topology may have changed. Per-GO verification metadata
            # for the previous PPO set must not make the new topology appear
            # current; shared PPO quantity rows remain available until the
            # proactive volatile-source poll replaces/verifies them.
            conn.execute(
                """
                DELETE FROM sql_source_sync
                WHERE source_key LIKE ?
                   OR source_key LIKE ?
                   OR source_key LIKE ?
                   OR source_key LIKE ?
                """,
                (
                    f"RECEIVED:%:{go_key}",
                    f"STOCK_BALANCE:%:{go_key}",
                    f"SHIPMENT_ON_WAY:%:{go_key}",
                    f"SHIPMENT_ON_WAY_ETA_V%:%:{go_key}",
                ),
            )
        conn.commit()
    return True


def _load_cached_go_source_bundle(go: str, include_ppo_detail: bool = False) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        head_row = conn.execute(
            """
            SELECT go_no, style_no, style_desc, season, factory_code, status,
                   customer_code, create_date, modify_date, synced_at
            FROM sql_go_head
            WHERE go_no = ?
            """,
            (go_key,),
        ).fetchone()
        if not head_row:
            return _error("No staged SQL source cache for GO", go=go_key)
        feed_row = conn.execute(
            """
            SELECT COALESCE(modify_date, create_date, '') AS source_stamp
            FROM go_feed
            WHERE go_no = ?
            """,
            (go_key,),
        ).fetchone()
        staged_stamp = str(head_row["modify_date"] or head_row["create_date"] or "")
        feed_stamp = str(feed_row["source_stamp"] or "") if feed_row else ""
        if feed_stamp and (not staged_stamp or feed_stamp > staged_stamp):
            return _error(
                "Staged SQL GO topology is older than go_feed",
                go=go_key,
                topology_stale=True,
                staged_source_stamp=staged_stamp,
                feed_source_stamp=feed_stamp,
            )

        def _fetch_rows(table_name: str) -> list[sqlite3.Row]:
            return conn.execute(
                f"SELECT * FROM {table_name} WHERE go_no = ? ORDER BY row_index",
                (go_key,),
            ).fetchall()

        colors = [
            {"color_code": str(row["color_code"] or ""), "color_desc": str(row["color_desc"] or "")}
            for row in _fetch_rows("sql_go_colors")
        ]
        lots = [
            {
                "lot_no": int(row["lot_no"] or 0),
                "jo_no": str(row["jo_no"] or ""),
                "qty": _to_float(row["qty"]),
                "buyer_po_del_date": str(row["buyer_po_del_date"] or ""),
                "buyer_po_no": str(row["buyer_po_no"] or ""),
                "short_pct": _to_float(row["short_pct"]),
                "over_pct": _to_float(row["over_pct"]),
            }
            for row in _fetch_rows("sql_go_lots")
        ]
        jo_color_qty_rows = [
            {
                "lot_no": int(row["lot_no"] or 0),
                "jo_no": str(row["jo_no"] or ""),
                "color_code": str(row["color_code"] or ""),
                "color_desc": str(row["color_desc"] or ""),
                "customer_name": str(row["customer_name"] or ""),
                "brand_name": str(row["brand_name"] or ""),
                "brand_owner": str(row["brand_owner"] or ""),
                "customer_label": str(row["customer_label"] or ""),
                "buyer_po_del_date": str(row["buyer_po_del_date"] or ""),
                "qty": _to_float(row["qty"]),
            }
            for row in _fetch_rows("sql_go_jo_color_qty")
        ]
        ppo_mapping = [
            {"ppo_no": str(row["ppo_no"] or ""), "lot_no": int(row["lot_no"] or 0)}
            for row in _fetch_rows("sql_go_ppo_mapping")
        ]
        fabric_rows = [
            {
                "lot_no": int(row["lot_no"] or 0),
                "ppo_no": str(row["ppo_no"] or ""),
                "fabric_type": str(row["fabric_type"] or ""),
                "color_code": str(row["color_code"] or ""),
                "combo_name": str(row["combo_name"] or ""),
                "ppo_yy": _to_float(row["ppo_yy"]),
                "marker_yy": _to_float(row["marker_yy"]),
                "related_jo_list": str(row["related_jo_list"] or ""),
                "remarks": str(row["remarks"] or ""),
            }
            for row in _fetch_rows("sql_go_fabric_rows")
        ]
        sql_bom_rows = [
            {
                "style_color_code": str(row["style_color_code"] or ""),
                "style_color_desc": str(row["style_color_desc"] or ""),
                "fabric_type_cd": str(row["fabric_type_cd"] or ""),
                "fabric_type_desc": str(row["fabric_type_desc"] or ""),
                "combo_name": str(row["combo_name"] or ""),
                "yy": _to_float(row["yy"]),
                "marker_yy": _to_float(row["marker_yy"]),
            }
            for row in _fetch_rows("sql_go_bom_rows")
        ]
        jo_ppo_yy_rows = [
            {
                "lot_no": int(row["lot_no"] or 0),
                "jo_no": str(row["jo_no"] or ""),
                "ppo_no": str(row["ppo_no"] or ""),
                "ppo_yy": _to_float(row["ppo_yy"]),
            }
            for row in _fetch_rows("sql_go_jo_ppo_yy")
        ]

        mapped_ppos = _mapped_ppo_set(ppo_mapping)
        fabric_rows = _filter_rows_to_mapped_ppos(fabric_rows, mapped_ppos)
        jo_ppo_yy_rows = _filter_rows_to_mapped_ppos(jo_ppo_yy_rows, mapped_ppos)
        ppo_list = _source_cache_ppos(ppo_mapping, fabric_rows, jo_ppo_yy_rows)
        factory_code = str(head_row["factory_code"] or "").strip().upper()
        received_view = _FOC_VIEW_BY_FACTORY.get(factory_code, "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV")
        shipment_database, shipment_table_name, _shipment_factory = _shipment_source_for_factory(factory_code)
        shipment_source_key = _shipment_source_key(shipment_database, shipment_table_name)
        shipment_source_table = f"{shipment_database}.{shipment_table_name}"
        shipment_eta_cache_stale = False
        shipment_on_way_error = ""
        received_rows: list[dict] = []
        stock_balance_rows: list[dict] = []
        stock_balance_view = _STOCK_BALANCE_VIEW_BY_FACTORY.get(factory_code, f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}")
        stock_balance_error = ""
        stock_balance_refreshed = False
        shipment_on_way_rows: list[dict] = []
        ppo_order_totals: dict[tuple[str, str, str], dict] = {}
        ppo_detail_rows_by_ppo: dict[str, list[dict]] = {}
        if ppo_list:
            placeholders = ",".join("?" for _ in ppo_list)
            received_rows = [
                {
                    "ppo_no": str(row["ppo_no"] or ""),
                    "fabric_type": str(row["fabric_type"] or ""),
                    "combo_name": str(row["combo_name"] or ""),
                    "size_code": str(row["size_code"] or ""),
                    "received_qty": _to_float(row["received_qty"]),
                    "foc_qty": _to_float(row["foc_qty"]),
                }
                for row in conn.execute(
                    f"""
                    SELECT ppo_no, fabric_type, combo_name, size_code, received_qty, foc_qty
                    FROM sql_received_foc_by_size
                    WHERE view_name = ? AND ppo_no IN ({placeholders})
                    ORDER BY ppo_no, fabric_type, combo_name, size_code
                    """,
                    [received_view, *ppo_list],
                ).fetchall()
            ]
            if not received_rows:
                received_rows = [
                    {
                        "ppo_no": str(row["ppo_no"] or ""),
                        "fabric_type": str(row["fabric_type"] or ""),
                        "combo_name": str(row["combo_name"] or ""),
                        "size_code": "",
                        "received_qty": _to_float(row["received_qty"]),
                        "foc_qty": _to_float(row["foc_qty"]),
                    }
                    for row in conn.execute(
                        f"""
                        SELECT ppo_no, fabric_type, combo_name, received_qty, foc_qty
                        FROM sql_received_foc
                        WHERE view_name = ? AND ppo_no IN ({placeholders})
                        ORDER BY ppo_no, fabric_type, combo_name
                        """,
                    [received_view, *ppo_list],
                ).fetchall()
            ]
            stock_sync_meta = conn.execute(
                """
                SELECT source_status, last_error
                FROM sql_source_sync
                WHERE source_key LIKE ?
                ORDER BY last_checked_at DESC, synced_at DESC
                LIMIT 1
                """,
                (f"STOCK_BALANCE:%:{go_key}".upper(),),
            ).fetchone()
            if stock_sync_meta and str(stock_sync_meta["source_status"] or "").strip().upper() == "ERROR":
                stock_balance_error = str(stock_sync_meta["last_error"] or "").strip()
            stock_balance_refreshed = bool(
                stock_sync_meta
                and str(stock_sync_meta["source_status"] or "").strip().upper() == "OK"
            )
            stock_balance_rows = [
                {
                    "ppo_no": str(row["ppo_no"] or ""),
                    "fabric_type": str(row["fabric_type"] or ""),
                    "combo_name": str(row["combo_name"] or ""),
                    "size_code": str(row["size_code"] or ""),
                    "on_hand_qty": _to_float(row["on_hand_qty"]),
                    "allocated_qty": _to_float(row["allocated_qty"]),
                    "reserved_qty": _to_float(row["reserved_qty"]),
                    "source_as_of": str(row["source_as_of"] or ""),
                }
                for row in conn.execute(
                    f"""
                    SELECT ppo_no, fabric_type, combo_name, size_code,
                           on_hand_qty, allocated_qty, reserved_qty,
                           source_view, source_as_of
                    FROM sql_stock_balance
                    WHERE ppo_no IN ({placeholders})
                    ORDER BY ppo_no, fabric_type, combo_name, size_code
                    """,
                    ppo_list,
                ).fetchall()
            ]
            if stock_balance_rows:
                stock_balance_view = str(
                    conn.execute(
                        f"""
                        SELECT COALESCE(NULLIF(source_view, ''), ?) AS source_view
                        FROM sql_stock_balance
                        WHERE ppo_no IN ({placeholders})
                        ORDER BY synced_at DESC
                        LIMIT 1
                        """,
                        [stock_balance_view, *ppo_list],
                    ).fetchone()["source_view"]
                    or stock_balance_view
                )
            shipment_count_row = conn.execute(
                f"""
                SELECT COUNT(*) AS row_count
                FROM sql_shipment_on_way
                WHERE source_key = ? AND ppo_no IN ({placeholders})
                """,
                [shipment_source_key, *ppo_list],
            ).fetchone()
            shipment_eta_meta = conn.execute(
                """
                SELECT source_status
                FROM sql_source_sync
                WHERE source_key = ?
                """,
                (_shipment_eta_rule_source_key(shipment_source_key, go_key).upper(),),
            ).fetchone()
            shipment_sync_meta = conn.execute(
                """
                SELECT source_status, last_error
                FROM sql_source_sync
                WHERE source_key = ?
                """,
                (f"SHIPMENT_ON_WAY:{shipment_source_key}:{go_key}".upper(),),
            ).fetchone()
            if shipment_sync_meta and str(shipment_sync_meta["source_status"] or "").strip().upper() == "ERROR":
                shipment_on_way_error = str(shipment_sync_meta["last_error"] or "").strip()
            shipment_eta_cache_stale = bool(
                int((shipment_count_row["row_count"] if shipment_count_row else 0) or 0) > 0
                and not shipment_eta_meta
            )
            shipment_on_way_rows = [
                {
                    "ppo_no": str(row["ppo_no"] or ""),
                    "fabric_type": str(row["fabric_type"] or ""),
                    "combo_name": str(row["combo_name"] or ""),
                    "shipment_qty": _to_float(row["shipment_qty"]),
                    "foc_qty": _to_float(row["foc_qty"]),
                    "eta_date": str(row["eta_date"] or ""),
                    "ship_type": str(row["ship_type"] or ""),
                    "source_table": str(row["source_table"] or shipment_source_table),
                }
                for row in conn.execute(
                    f"""
                    SELECT ppo_no, fabric_type, combo_name, shipment_qty, foc_qty,
                           eta_date, ship_type, source_table
                    FROM sql_shipment_on_way
                    WHERE source_key = ? AND ppo_no IN ({placeholders})
                    ORDER BY ppo_no, fabric_type, combo_name
                    """,
                    [shipment_source_key, *ppo_list],
                ).fetchall()
            ]
            for row in conn.execute(
                f"""
                SELECT ppo_no, fabric_type, color_key, color_code, fabric_combo, fabric_part, ppo_order_qty
                FROM sql_ppo_order_totals_by_color
                WHERE ppo_no IN ({placeholders})
                """,
                ppo_list,
            ).fetchall():
                ppo_order_totals[(
                    str(row["ppo_no"] or ""),
                    str(row["fabric_type"] or ""),
                    str(row["color_key"] or ""),
                )] = {
                    "fabric_part": str(row["fabric_part"] or ""),
                    "color_code": str(row["color_code"] or ""),
                    "fabric_combo": str(row["fabric_combo"] or ""),
                    "ppo_order_qty": _to_float(row["ppo_order_qty"]),
                }
            for row in conn.execute(
                f"""
                SELECT ppo_no, fabric_type, fabric_part, ppo_order_qty
                FROM sql_ppo_order_totals
                WHERE ppo_no IN ({placeholders})
                """,
                ppo_list,
            ).fetchall():
                ppo_order_totals[(str(row["ppo_no"] or ""), str(row["fabric_type"] or ""), "")] = {
                    "fabric_part": str(row["fabric_part"] or ""),
                    "ppo_order_qty": _to_float(row["ppo_order_qty"]),
                }
            if include_ppo_detail:
                for row in conn.execute(
                    f"""
                    SELECT ppo_no, fabric_type, fabric_part, color_code, fabric_combo,
                           fabric_color, fabric_code, gmt_qty, fabric_total_qty, ppo_order_qty
                    FROM sql_ppo_detail_rows
                    WHERE ppo_no IN ({placeholders})
                    ORDER BY ppo_no, row_index
                    """,
                    ppo_list,
                ).fetchall():
                    ppo_key = str(row["ppo_no"] or "")
                    ppo_detail_rows_by_ppo.setdefault(ppo_key, []).append(
                        {
                            "ppo": ppo_key,
                            "fabric_type": str(row["fabric_type"] or ""),
                            "fabric_part": str(row["fabric_part"] or ""),
                            "color_code": str(row["color_code"] or ""),
                            "fabric_combo": str(row["fabric_combo"] or ""),
                            "fabric_color": str(row["fabric_color"] or ""),
                            "fabric_code": str(row["fabric_code"] or ""),
                            "gmt_qty": _to_float(row["gmt_qty"]),
                            "fabric_total_qty": _to_float(row["fabric_total_qty"]),
                            "ppo_order_qty": _to_float(row["ppo_order_qty"]),
                        }
                    )

        head = {
            "go_no": str(head_row["go_no"] or ""),
            "style_no": str(head_row["style_no"] or ""),
            "style_desc": str(head_row["style_desc"] or ""),
            "season": str(head_row["season"] or ""),
            "factory_code": str(head_row["factory_code"] or ""),
            "status": str(head_row["status"] or ""),
            "customer_code": str(head_row["customer_code"] or ""),
            "create_date": str(head_row["create_date"] or ""),
            "modify_date": str(head_row["modify_date"] or ""),
        }
        return {
            "ok": True,
            "go": go_key,
            "head": head,
            "colors": colors,
            "lots": lots,
            "jo_color_qty_rows": jo_color_qty_rows,
            "ppo_mapping": ppo_mapping,
            "fabric_rows": fabric_rows,
            "sql_bom_rows": sql_bom_rows,
            "jo_ppo_yy_rows": jo_ppo_yy_rows,
        "received_rows": received_rows,
        "received_view": received_view,
        "stock_balance_rows": stock_balance_rows,
        "stock_balance_view": stock_balance_view,
        "stock_balance_error": stock_balance_error,
        "stock_balance_refreshed": stock_balance_refreshed,
        "shipment_on_way_rows": shipment_on_way_rows,
            "shipment_source_key": shipment_source_key,
            "shipment_source_table": shipment_source_table,
            "shipment_on_way_error": shipment_on_way_error,
            "shipment_eta_cache_stale": shipment_eta_cache_stale,
            "ppo_order_totals": ppo_order_totals,
            "ppo_detail_rows_by_ppo": ppo_detail_rows_by_ppo,
            "source_mode": "sqlite-source-cache",
            "source_synced_at": str(head_row["synced_at"] or ""),
            "source_live_error": "",
        }


def _load_live_go_source_bundle(
    go: str,
    include_ppo_detail: bool = False,
    bypass_memory_cache: bool = False,
    include_order_totals: bool = True,
    include_volatile_sources: bool = True,
) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")
    with _connect() as conn:
        cursor = conn.cursor()
        go_head = _load_go_head(cursor, go_key)
        if not go_head:
            return _error("GO not found in SQL", go=go_key)
        colors = _load_go_colors(cursor, go_key)
        lots = _load_go_lots(cursor, go_key)
        jo_color_qty_rows = _load_go_jo_color_qty(cursor, go_key)
        ppo_mapping = _load_go_ppo_mapping(cursor, go_key)
        fabric_rows = _load_go_fabric_rows(cursor, go_key)
        sql_bom_rows = _load_go_sql_bom_rows(cursor, go_key)
        jo_ppo_yy_rows = _load_jo_ppo_yy(cursor, go_key)
        mapped_ppos = _mapped_ppo_set(ppo_mapping)
        fabric_rows = _filter_rows_to_mapped_ppos(fabric_rows, mapped_ppos)
        jo_ppo_yy_rows = _filter_rows_to_mapped_ppos(jo_ppo_yy_rows, mapped_ppos)
        ppo_list = _source_cache_ppos(ppo_mapping, fabric_rows, jo_ppo_yy_rows)
        factory_code = str(go_head.get("factory_code") or "").strip().upper()
        received_view = _FOC_VIEW_BY_FACTORY.get(
            factory_code,
            "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV",
        )
        shipment_database, shipment_table_name, _shipment_factory = _shipment_source_for_factory(
            factory_code
        )
        shipment_source_key = _shipment_source_key(shipment_database, shipment_table_name)
        shipment_source_table = f"{shipment_database}.{shipment_table_name}"
        shipment_on_way_error = ""
        received_rows: list[dict] = []
        stock_balance_rows: list[dict] = []
        stock_balance_view = _STOCK_BALANCE_VIEW_BY_FACTORY.get(factory_code, "dbo.V_INV_STOCK")
        stock_balance_error = ""
        stock_balance_refreshed = False
        shipment_on_way_rows: list[dict] = []
        if include_volatile_sources:
            received_rows, received_view = _load_received_foc_rows(
                cursor,
                factory_code,
                ppo_list,
                bypass_cache=bypass_memory_cache,
            )
            (
                stock_balance_rows,
                stock_balance_view,
                stock_balance_error,
            ) = _load_stock_balance_rows(
                cursor,
                factory_code,
                ppo_list,
                bypass_cache=bypass_memory_cache,
            )
            stock_balance_refreshed = not bool(stock_balance_error)
            # The factory Received/FOC view is the authoritative net warehouse
            # quantity (including supplier returns). Raw GRN transactions are for
            # diagnostics only and must never be merged into the displayed value.
            (
                shipment_on_way_rows,
                shipment_source_key,
                shipment_source_table,
                shipment_on_way_error,
            ) = _load_shipment_on_way_rows(
                factory_code,
                ppo_list,
                bypass_cache=bypass_memory_cache,
            )
        ppo_order_total_errors: list[str] = []
        ppo_order_totals = (
            _load_ppo_order_totals_sql(cursor, ppo_list, errors=ppo_order_total_errors)
            if include_order_totals
            else {}
        )
        if ppo_order_total_errors:
            cached_totals = dict(
                (_load_cached_go_source_bundle(go_key, include_ppo_detail=False) or {}).get(
                    "ppo_order_totals"
                )
                or {}
            )
            cached_totals.update(ppo_order_totals)
            ppo_order_totals = cached_totals
        ppo_detail_rows_by_ppo: dict[str, list[dict]] = {}
        if include_ppo_detail:
            for ppo_no in ppo_list:
                detail_rows = _load_ppo_detail_rows_sql(cursor, ppo_no)
                if detail_rows:
                    ppo_detail_rows_by_ppo[ppo_no] = detail_rows

    return {
        "ok": True,
        "go": go_key,
        "head": go_head,
        "colors": colors,
        "lots": lots,
        "jo_color_qty_rows": jo_color_qty_rows,
        "ppo_mapping": ppo_mapping,
        "fabric_rows": fabric_rows,
        "sql_bom_rows": sql_bom_rows,
        "jo_ppo_yy_rows": jo_ppo_yy_rows,
            "received_rows": received_rows,
            "received_view": received_view,
            "stock_balance_rows": stock_balance_rows,
        "stock_balance_view": stock_balance_view,
        "stock_balance_error": stock_balance_error,
        "stock_balance_refreshed": stock_balance_refreshed,
        "shipment_on_way_rows": shipment_on_way_rows,
        "shipment_source_key": shipment_source_key,
        "shipment_source_table": shipment_source_table,
        "shipment_on_way_error": shipment_on_way_error,
        "volatile_sources_refreshed": bool(include_volatile_sources),
        "ppo_order_totals": ppo_order_totals,
        "ppo_order_totals_refreshed": bool(include_order_totals and not ppo_order_total_errors),
        "ppo_detail_rows_by_ppo": ppo_detail_rows_by_ppo,
        "source_mode": "sql-live",
        "source_synced_at": _snapshot_now(),
        "source_live_error": "; ".join(ppo_order_total_errors),
    }


def _load_go_source_bundle(go: str, include_ppo_detail: bool = False) -> dict:
    go_key = str(go or "").strip().upper()
    try:
        live_bundle = _load_live_go_source_bundle(go_key, include_ppo_detail=include_ppo_detail)
        if not live_bundle.get("ok"):
            return live_bundle
        _save_go_source_cache_bundle(go_key, live_bundle)
        return live_bundle
    except Exception as exc:
        cached_bundle = _load_cached_go_source_bundle(go_key, include_ppo_detail=include_ppo_detail)
        if cached_bundle.get("ok"):
            cached_bundle["source_live_error"] = f"{type(exc).__name__}: {exc}"
            return cached_bundle
        return _error("Cannot load SQL source data", detail=str(exc), go=go_key)


def _refresh_go_topology_cache(go: str) -> dict:
    """Stage GO/PPO topology without blocking on the slow volatile views."""
    go_key = str(go or "").strip().upper()
    try:
        bundle = _load_live_go_source_bundle(
            go_key,
            include_ppo_detail=False,
            bypass_memory_cache=True,
            include_order_totals=False,
            include_volatile_sources=False,
        )
    except Exception as exc:
        return _error(
            "Cannot refresh staged GO topology",
            go=go_key,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not bundle.get("ok"):
        return bundle
    if not _save_go_source_cache_bundle(go_key, bundle):
        return _error(
            "GO topology changed again while it was being staged",
            go=go_key,
        )
    return {
        "ok": True,
        "go": go_key,
        "ppo_count": len(
            _source_cache_ppos(
                list(bundle.get("ppo_mapping") or []),
                list(bundle.get("fabric_rows") or []),
                list(bundle.get("jo_ppo_yy_rows") or []),
            )
        ),
        "source_synced_at": str(bundle.get("source_synced_at") or ""),
    }


def _mark_go_waiting_for_volatile_source(go: str, reason: str = "") -> None:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        row = conn.execute(
            "SELECT cache_flags, cache_reason FROM go_feed WHERE go_no = ?",
            (go_key,),
        ).fetchone()
        if not row:
            return
        flags = _split_cache_flags(row["cache_flags"])
        flags.extend(["TOPOLOGY_STAGED", "WAIT_SOURCE"])
        reason_text = "; ".join(
            bit
            for bit in (
                str(row["cache_reason"] or "").strip(),
                str(reason or "GO topology staged; warehouse/shipment verification pending").strip(),
            )
            if bit
        )
        conn.execute(
            """
            UPDATE go_feed
            SET cache_state = 'WAIT_SOURCE',
                cache_flags = ?,
                cache_reason = ?,
                next_refresh_at = ?,
                last_build_error = ''
            WHERE go_no = ?
            """,
            (
                _encode_cache_flags(flags),
                reason_text[:1000],
                _snapshot_now(),
                go_key,
            ),
        )
        conn.commit()


def _source_bundle_for_request(
    go: str,
    *,
    include_ppo_detail: bool = False,
    prefer_source_cache: bool = False,
    force_live_source_refresh: bool = False,
) -> dict:
    go_key = str(go or "").strip().upper()
    if force_live_source_refresh:
        return _load_go_source_bundle(go_key, include_ppo_detail=include_ppo_detail)
    if prefer_source_cache:
        cached = _load_cached_go_source_bundle(go_key, include_ppo_detail=include_ppo_detail)
        if cached.get("ok") and not cached.get("shipment_eta_cache_stale"):
            return cached
    return _load_go_source_bundle(go_key, include_ppo_detail=include_ppo_detail)


def _saved_ppo_override_values(saved_state: dict[str, dict]) -> list[str]:
    return sorted(
        {
            str((item or {}).get("ppo_override") or "").strip().upper()
            for item in (saved_state or {}).values()
            if str((item or {}).get("ppo_override") or "").strip()
        }
    )


def _merge_received_row_lists(base_rows: list[dict], extra_rows: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str, str], dict] = {}
    passthrough: list[dict] = []
    for item in [*(base_rows or []), *(extra_rows or [])]:
        key = _received_row_identity(item)
        if key[0] and key[1] and key[2]:
            merged[key] = item
        else:
            passthrough.append(item)
    return [*merged.values(), *passthrough]


def _merge_stock_balance_row_lists(base_rows: list[dict], extra_rows: list[dict]) -> list[dict]:
    """Replace stock identities with the fresh PPO override snapshot."""
    merged: dict[tuple[str, str, str, str], dict] = {}
    passthrough: list[dict] = []
    for item in [*(base_rows or []), *(extra_rows or [])]:
        key = _received_row_identity(item)
        if key[0] and key[1] and key[2]:
            merged[key] = dict(item)
        else:
            passthrough.append(item)
    return [*merged.values(), *passthrough]


def _augment_source_for_ppo_overrides(
    factory_code: str,
    override_ppos: list[str],
    received_rows: list[dict],
    stock_balance_rows: list[dict],
    shipment_on_way_rows: list[dict],
    ppo_order_totals: dict,
    ppo_detail_rows_by_ppo: dict[str, list[dict]],
    include_ppo_detail: bool = False,
) -> tuple[list[dict], list[dict], list[dict], dict, dict[str, list[dict]], str]:
    clean_ppos = sorted({str(item or "").strip().upper() for item in override_ppos if str(item or "").strip()})
    if not clean_ppos:
        return received_rows, stock_balance_rows, shipment_on_way_rows, ppo_order_totals, ppo_detail_rows_by_ppo, ""

    notes: list[str] = []
    next_received_rows = list(received_rows or [])
    next_stock_balance_rows = list(stock_balance_rows or [])
    next_shipment_rows = list(shipment_on_way_rows or [])
    next_order_totals = dict(ppo_order_totals or {})
    next_detail_rows = dict(ppo_detail_rows_by_ppo or {})
    try:
        with _connect() as conn:
            cursor = conn.cursor()
            extra_received_rows, _view = _load_received_foc_rows(
                cursor,
                factory_code,
                clean_ppos,
                bypass_cache=True,
            )
            if extra_received_rows:
                next_received_rows = _merge_received_row_lists(next_received_rows, extra_received_rows)
            extra_stock_rows, _stock_view, stock_error = _load_stock_balance_rows(
                cursor,
                factory_code,
                clean_ppos,
                bypass_cache=True,
            )
            if stock_error:
                notes.append(f"PPO override stock error: {stock_error}")
            else:
                next_stock_balance_rows = _merge_stock_balance_row_lists(
                    next_stock_balance_rows,
                    extra_stock_rows,
                )
            ppo_errors: list[str] = []
            extra_order_totals = _load_ppo_order_totals_sql(cursor, clean_ppos, errors=ppo_errors)
            if extra_order_totals:
                next_order_totals.update(extra_order_totals)
            if ppo_errors:
                notes.extend(ppo_errors)
            if include_ppo_detail:
                for ppo_no in clean_ppos:
                    if next_detail_rows.get(ppo_no):
                        continue
                    detail_rows = _load_ppo_detail_rows_sql(cursor, ppo_no)
                    if detail_rows:
                        next_detail_rows[ppo_no] = detail_rows
            notes.append(f"SQL PPO override: {', '.join(clean_ppos)}")
    except Exception as exc:
        notes.append(f"PPO override SQL error: {type(exc).__name__}: {exc}")

    try:
        extra_shipment_rows, _source_key, _source_table, shipment_error = _load_shipment_on_way_rows(
            factory_code,
            clean_ppos,
            bypass_cache=True,
        )
        if extra_shipment_rows:
            next_shipment_rows.extend(extra_shipment_rows)
        if shipment_error:
            notes.append(f"PPO override shipment error: {shipment_error}")
    except Exception as exc:
        notes.append(f"PPO override shipment error: {type(exc).__name__}: {exc}")

    return (
        next_received_rows,
        next_stock_balance_rows,
        next_shipment_rows,
        next_order_totals,
        next_detail_rows,
        "; ".join(notes),
    )


def _go_source_sync_lock(go_key: str) -> threading.Lock:
    with _go_source_sync_locks_guard:
        lock = _go_source_sync_locks.get(go_key)
        if lock is None:
            lock = threading.Lock()
            _go_source_sync_locks[go_key] = lock
        return lock


def _go_source_sync_meta(go: str) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"ok": False, "error": "GO number required", "go": ""}
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        rows = conn.execute(
            """
            SELECT source_key, synced_at, last_checked_at, row_count, source_status, last_error
            FROM sql_source_sync
            WHERE source_key = ?
               OR source_key LIKE ?
            ORDER BY synced_at DESC
            """,
            (f"GO:{go_key}", f"%:{go_key}"),
        ).fetchall()
        topology_row = conn.execute(
            """
            SELECT
                CASE WHEN h.go_no IS NULL THEN 0 ELSE 1 END AS has_staged_head,
                COALESCE(h.modify_date, h.create_date, '') AS staged_source_stamp,
                COALESCE(gf.modify_date, gf.create_date, '') AS feed_source_stamp
            FROM go_feed gf
            LEFT JOIN sql_go_head h ON h.go_no = gf.go_no
            WHERE gf.go_no = ?
            UNION ALL
            SELECT
                1 AS has_staged_head,
                COALESCE(h.modify_date, h.create_date, '') AS staged_source_stamp,
                '' AS feed_source_stamp
            FROM sql_go_head h
            WHERE h.go_no = ?
              AND NOT EXISTS (SELECT 1 FROM go_feed gf2 WHERE gf2.go_no = h.go_no)
            LIMIT 1
            """,
            (go_key, go_key),
        ).fetchone()
    sources = [
        {
            "source_key": str(row["source_key"] or ""),
            "synced_at": str(row["synced_at"] or ""),
            "last_checked_at": str(row["last_checked_at"] or row["synced_at"] or ""),
            "row_count": int(row["row_count"] or 0),
            "source_status": str(row["source_status"] or ""),
            "last_error": str(row["last_error"] or ""),
        }
        for row in rows
    ]
    source_keys = {str(item["source_key"] or "").upper() for item in sources}
    has_go = f"GO:{go_key}" in source_keys
    has_received = any(key.startswith("RECEIVED:") and key.endswith(f":{go_key}") for key in source_keys)
    has_stock_balance = any(key.startswith("STOCK_BALANCE:") and key.endswith(f":{go_key}") for key in source_keys)
    has_shipment = any(key.startswith("SHIPMENT_ON_WAY:") and key.endswith(f":{go_key}") for key in source_keys)
    has_eta = any(
        key.startswith(f"SHIPMENT_ON_WAY_ETA_V{_SHIPMENT_ETA_RULE_VERSION}:") and key.endswith(f":{go_key}")
        for key in source_keys
    )
    volatile_sources = [
        item
        for item in sources
        if str(item.get("source_key") or "").upper().startswith(
            ("RECEIVED:", "STOCK_BALANCE:", "SHIPMENT_ON_WAY:")
        )
    ]
    parsed_checked_dates = [
        dt
        for dt in (
            _parse_iso_datetime(item.get("last_checked_at") or item.get("synced_at"))
            for item in volatile_sources
        )
        if dt is not None
    ]
    parsed_content_dates = [
        dt
        for dt in (_parse_iso_datetime(item.get("synced_at")) for item in sources)
        if dt is not None
    ]
    oldest_dt = min(parsed_checked_dates) if parsed_checked_dates else None
    newest_checked_dt = max(parsed_checked_dates) if parsed_checked_dates else None
    newest_dt = max(parsed_content_dates) if parsed_content_dates else None
    now = datetime.now()
    age_sec = (now - oldest_dt).total_seconds() if oldest_dt is not None else None
    errors = [
        item
        for item in sources
        if str(item.get("source_status") or "").strip().upper() == "ERROR"
        or str(item.get("last_error") or "").strip()
    ]
    has_staged_head = bool(topology_row and int(topology_row["has_staged_head"] or 0))
    staged_source_stamp = str(topology_row["staged_source_stamp"] or "") if topology_row else ""
    feed_source_stamp = str(topology_row["feed_source_stamp"] or "") if topology_row else ""
    topology_current = bool(
        has_staged_head
        and (not feed_source_stamp or (staged_source_stamp and staged_source_stamp >= feed_source_stamp))
    )
    complete = bool(has_go and has_received and has_stock_balance and has_shipment and has_eta and topology_current)
    return {
        "ok": True,
        "go": go_key,
        "complete": complete,
        "has_go": has_go,
        "has_received": has_received,
        "has_stock_balance": has_stock_balance,
        "has_shipment": has_shipment,
        "has_shipment_eta_rule": has_eta,
        "has_staged_head": has_staged_head,
        "topology_current": topology_current,
        "staged_source_stamp": staged_source_stamp,
        "feed_source_stamp": feed_source_stamp,
        "has_error": bool(errors),
        "oldest_synced_at": oldest_dt.isoformat(sep=" ", timespec="seconds") if oldest_dt else "",
        "latest_checked_at": newest_checked_dt.isoformat(sep=" ", timespec="seconds") if newest_checked_dt else "",
        "latest_synced_at": newest_dt.isoformat(sep=" ", timespec="seconds") if newest_dt else "",
        "age_sec": None if age_sec is None else round(age_sec, 3),
        "sources": sources,
        "errors": errors[:3],
    }


def _flatknit_received_size_cache_contract(go: str) -> dict:
    """Check that cached O/F receipt totals still retain their size rows.

    Before the size-aware schema, ``sql_received_foc`` intentionally collapsed
    all sizes into one value.  A cache stamped as fresh by that older code must
    not be considered current for flatknit allocation.
    """
    go_key = str(go or "").strip().upper()
    if not go_key:
        return {"complete": True, "required": False, "reason": "no GO"}

    try:
        _ensure_snapshot_tables()
        with _snapshot_connect() as conn:
            head_row = conn.execute(
                "SELECT factory_code FROM sql_go_head WHERE go_no = ?",
                (go_key,),
            ).fetchone()
            if not head_row:
                return {"complete": False, "required": False, "reason": "no staged GO"}
            factory_code = str(head_row["factory_code"] or "").strip().upper()
            received_view = _FOC_VIEW_BY_FACTORY.get(factory_code, "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV")
            rows = conn.execute(
                """
                WITH go_ppos AS (
                    SELECT DISTINCT UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                    FROM sql_go_ppo_mapping
                    WHERE go_no = ?
                    UNION
                    SELECT DISTINCT UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                    FROM sql_go_fabric_rows
                    WHERE go_no = ?
                    UNION
                    SELECT DISTINCT UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                    FROM sql_go_jo_ppo_yy
                    WHERE go_no = ?
                ), detail AS (
                    SELECT
                        ppo_no,
                        fabric_type,
                        combo_name,
                        COUNT(*) AS detail_row_count,
                        SUM(CASE WHEN TRIM(COALESCE(size_code, '')) <> '' THEN 1 ELSE 0 END) AS sized_row_count,
                        SUM(received_qty) AS received_qty,
                        SUM(foc_qty) AS foc_qty
                    FROM sql_received_foc_by_size
                    WHERE view_name = ?
                    GROUP BY ppo_no, fabric_type, combo_name
                )
                SELECT
                    aggregate.ppo_no,
                    aggregate.fabric_type,
                    aggregate.combo_name,
                    aggregate.received_qty AS aggregate_received_qty,
                    aggregate.foc_qty AS aggregate_foc_qty,
                    COALESCE(detail.detail_row_count, 0) AS detail_row_count,
                    COALESCE(detail.sized_row_count, 0) AS sized_row_count,
                    COALESCE(detail.received_qty, 0) AS detail_received_qty,
                    COALESCE(detail.foc_qty, 0) AS detail_foc_qty
                FROM sql_received_foc AS aggregate
                INNER JOIN go_ppos ON go_ppos.ppo_no = aggregate.ppo_no
                LEFT JOIN detail
                    ON detail.ppo_no = aggregate.ppo_no
                   AND detail.fabric_type = aggregate.fabric_type
                   AND detail.combo_name = aggregate.combo_name
                WHERE aggregate.view_name = ?
                  AND UPPER(TRIM(aggregate.fabric_type)) IN ('O', 'F')
                """,
                (go_key, go_key, go_key, received_view, received_view),
            ).fetchall()
    except Exception as exc:
        return {
            "complete": False,
            "required": False,
            "reason": f"cache check error: {type(exc).__name__}: {exc}",
        }

    missing: list[str] = []
    for row in rows:
        received_matches = abs(_to_float(row["aggregate_received_qty"]) - _to_float(row["detail_received_qty"])) <= 0.001
        foc_matches = abs(_to_float(row["aggregate_foc_qty"]) - _to_float(row["detail_foc_qty"])) <= 0.001
        has_sized_detail = int(row["detail_row_count"] or 0) > 0 and int(row["sized_row_count"] or 0) > 0
        if not (has_sized_detail and received_matches and foc_matches):
            missing.append(
                "/".join(
                    [
                        str(row["ppo_no"] or "").strip(),
                        str(row["fabric_type"] or "").strip(),
                        str(row["combo_name"] or "").strip(),
                    ]
                )
            )
    return {
        "complete": not missing,
        "required": bool(rows),
        "aggregate_row_count": len(rows),
        "missing_size_detail_count": len(missing),
        "missing_size_detail": missing[:5],
    }


def _refresh_flatknit_received_size_cache(go: str) -> dict:
    """Refresh only the received/FOC rows needed to repair an old O/F cache."""
    go_key = str(go or "").strip().upper()
    cached_bundle = _load_cached_go_source_bundle(go_key, include_ppo_detail=False)
    if not cached_bundle.get("ok"):
        return {
            "ok": False,
            "go": go_key,
            "error": cached_bundle.get("error") or "No staged SQL source cache for flatknit refresh",
        }

    ppo_list = _source_cache_ppos(
        list(cached_bundle.get("ppo_mapping") or []),
        list(cached_bundle.get("fabric_rows") or []),
        list(cached_bundle.get("jo_ppo_yy_rows") or []),
    )
    if not ppo_list:
        return {"ok": False, "go": go_key, "error": "No PPO available for flatknit receipt refresh"}

    factory_code = str((cached_bundle.get("head") or {}).get("factory_code") or "").strip().upper()
    try:
        with _connect() as sql_conn:
            cursor = sql_conn.cursor()
            received_rows, received_view = _load_received_foc_rows(
                cursor,
                factory_code,
                ppo_list,
                bypass_cache=True,
            )
    except Exception as exc:
        return {
            "ok": False,
            "go": go_key,
            "error": f"Cannot refresh flatknit received sizes: {type(exc).__name__}: {exc}",
        }

    synced_at = _snapshot_now()
    received_rows_aggregate = _aggregate_received_rows(received_rows)
    with _snapshot_connect() as conn:
        placeholders = ",".join("?" for _ in ppo_list)
        conn.execute(
            f"DELETE FROM sql_received_foc WHERE view_name = ? AND ppo_no IN ({placeholders})",
            [received_view, *ppo_list],
        )
        conn.execute(
            f"DELETE FROM sql_received_foc_by_size WHERE view_name = ? AND ppo_no IN ({placeholders})",
            [received_view, *ppo_list],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO sql_received_foc (
                view_name, ppo_no, fabric_type, combo_name, received_qty, foc_qty, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    received_view,
                    str(row.get("ppo_no") or "").strip().upper(),
                    _normalize_sql_fabric_type_code(row.get("fabric_type")),
                    str(row.get("combo_name") or "").strip(),
                    _to_float(row.get("received_qty")),
                    _to_float(row.get("foc_qty")),
                    synced_at,
                )
                for row in received_rows_aggregate
                if str(row.get("ppo_no") or "").strip()
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO sql_received_foc_by_size (
                view_name, ppo_no, fabric_type, combo_name, size_code, received_qty, foc_qty, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    received_view,
                    str(row.get("ppo_no") or "").strip().upper(),
                    _normalize_sql_fabric_type_code(row.get("fabric_type")),
                    str(row.get("combo_name") or "").strip(),
                    str(row.get("size_code") or "").strip().upper(),
                    _to_float(row.get("received_qty")),
                    _to_float(row.get("foc_qty")),
                    synced_at,
                )
                for row in received_rows
                if str(row.get("ppo_no") or "").strip()
            ],
        )
        _source_cache_meta(
            conn,
            f"RECEIVED:{received_view}:{go_key}",
            len(received_rows),
            synced_at,
            content_changed=True,
        )

    with _received_rows_cache_lock:
        _received_rows_cache[(received_view, tuple(sorted(ppo_list)))] = {
            "ts": time.time(),
            "rows": list(received_rows),
        }
    return {
        "ok": True,
        "go": go_key,
        "received_view": received_view,
        "received_row_count": len(received_rows),
        "synced_at": synced_at,
    }


def _go_source_cache_is_current(go: str, max_age_sec: int | float | None = None) -> tuple[bool, dict]:
    try:
        max_age = float(_SQL_SOURCE_CACHE_MAX_AGE_SEC if max_age_sec is None else max_age_sec)
    except (TypeError, ValueError):
        max_age = float(_SQL_SOURCE_CACHE_MAX_AGE_SEC)
    max_age = max(0.0, max_age)
    meta = _go_source_sync_meta(go)
    age = meta.get("age_sec")
    source_current = bool(
        meta.get("ok")
        and meta.get("complete")
        and not meta.get("has_error")
        and age is not None
        and float(age) <= max_age
    )
    flatknit_received_size_contract = _flatknit_received_size_cache_contract(go)
    is_current = bool(source_current and flatknit_received_size_contract.get("complete"))
    meta["max_age_sec"] = max_age
    meta["source_current"] = source_current
    meta["flatknit_received_size_contract"] = flatknit_received_size_contract
    meta["current"] = is_current
    return is_current, meta


def _active_source_refresh_scope() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return the full active scope and the least-recently-checked batch."""
    _ensure_snapshot_tables()
    cutoff_text = (datetime.now() - timedelta(days=_SOURCE_REFRESH_LOOKBACK_DAYS)).isoformat(
        sep=" ",
        timespec="seconds",
    )
    ignored_customer_codes = sorted(_IGNORED_CUSTOMER_CODES) or [""]
    ignored_customer_placeholders = ",".join("?" for _ in ignored_customer_codes)
    with _snapshot_connect() as conn:
        rows = conn.execute(
            f"""
            WITH active_ppos AS (
                SELECT go_no, UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                FROM sql_go_ppo_mapping
                UNION
                SELECT go_no, UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                FROM sql_go_fabric_rows
                UNION
                SELECT go_no, UPPER(TRIM(COALESCE(ppo_no, ''))) AS ppo_no
                FROM sql_go_jo_ppo_yy
                UNION
                SELECT go_no, UPPER(TRIM(
                    CASE WHEN COALESCE(ppo_override, '') <> '' THEN ppo_override ELSE ppo_no END
                )) AS ppo_no
                FROM coi_ui_allocations
            )
            SELECT DISTINCT
                UPPER(TRIM(gf.go_no)) AS go_no,
                UPPER(TRIM(COALESCE(h.factory_code, gf.factory_code, ''))) AS factory_code,
                COALESCE(p.ppo_no, '') AS ppo_no,
                CASE WHEN h.go_no IS NULL THEN 0 ELSE 1 END AS has_staged_head,
                COALESCE(h.modify_date, h.create_date, '') AS staged_source_stamp,
                COALESCE(gf.modify_date, gf.create_date, '') AS feed_source_stamp
            FROM go_feed gf
            LEFT JOIN sql_go_head h ON h.go_no = gf.go_no
            LEFT JOIN active_ppos p ON p.go_no = gf.go_no
            WHERE COALESCE(gf.modify_date, gf.create_date, '') >= ?
              AND UPPER(COALESCE(gf.status, '')) <> 'CANCEL'
              AND UPPER(COALESCE(gf.factory_code, '')) IN (?, ?)
              AND UPPER(TRIM(COALESCE(gf.customer_code, ''))) NOT IN ({ignored_customer_placeholders})
            ORDER BY gf.go_no, p.ppo_no
            """,
            (cutoff_text, *_ALLOWED_FACTORIES, *ignored_customer_codes),
        ).fetchall()
        sync_rows = conn.execute(
            """
            SELECT source_key, last_checked_at, source_status
            FROM sql_source_sync
            WHERE source_key LIKE 'RECEIVED:%'
               OR source_key LIKE 'STOCK_BALANCE:%'
               OR source_key LIKE 'SHIPMENT_ON_WAY:%'
               OR source_key LIKE ?
            """
            ,
            (f"SHIPMENT_ON_WAY_ETA_V{_SHIPMENT_ETA_RULE_VERSION}:%",),
        ).fetchall()

    full_scope: dict[str, dict] = {}
    for row in rows:
        go_key = str(row["go_no"] or "").strip().upper()
        ppo_key = str(row["ppo_no"] or "").strip().upper()
        factory_code = str(row["factory_code"] or "").strip().upper()
        if not go_key or factory_code not in _ALLOWED_FACTORIES:
            continue
        staged_source_stamp = str(row["staged_source_stamp"] or "")
        feed_source_stamp = str(row["feed_source_stamp"] or "")
        has_staged_head = bool(int(row["has_staged_head"] or 0))
        bucket = full_scope.setdefault(
            go_key,
            {
                "go_no": go_key,
                "factory_code": factory_code,
                "ppos": set(),
                "has_staged_head": has_staged_head,
                "has_ppo": False,
                "topology_current": bool(
                    has_staged_head
                    and (not feed_source_stamp or staged_source_stamp >= feed_source_stamp)
                ),
                "staged_source_stamp": staged_source_stamp,
                "feed_source_stamp": feed_source_stamp,
                "last_checked_at": "",
                "has_error": False,
                "verification_complete": False,
                "_sync_by_kind": {},
            },
        )
        if ppo_key:
            bucket["ppos"].add(ppo_key)

    for row in sync_rows:
        source_key = str(row["source_key"] or "").strip().upper()
        go_key = source_key.rsplit(":", 1)[-1] if ":" in source_key else ""
        bucket = full_scope.get(go_key)
        if not bucket:
            continue
        if source_key.startswith("RECEIVED:"):
            source_kind = "received"
        elif source_key.startswith("STOCK_BALANCE:"):
            source_kind = "stock"
        elif source_key.startswith(f"SHIPMENT_ON_WAY_ETA_V{_SHIPMENT_ETA_RULE_VERSION}:"):
            source_kind = "eta"
        elif source_key.startswith("SHIPMENT_ON_WAY:"):
            source_kind = "shipment"
        else:
            continue
        checked_at = str(row["last_checked_at"] or "").strip()
        prior = (bucket.get("_sync_by_kind") or {}).get(source_kind) or {}
        if checked_at >= str(prior.get("checked_at") or ""):
            bucket["_sync_by_kind"][source_kind] = {
                "checked_at": checked_at,
                "status": str(row["source_status"] or "").strip().upper(),
            }

    for bucket in full_scope.values():
        sync_by_kind = dict(bucket.pop("_sync_by_kind", {}) or {})
        bucket["has_ppo"] = bool(bucket.get("ppos"))
        bucket["verification_complete"] = bool(
            bucket.get("has_staged_head")
            and bucket.get("topology_current")
            and bucket.get("has_ppo")
            and all(kind in sync_by_kind for kind in ("received", "stock", "shipment", "eta"))
        )
        if bucket["verification_complete"]:
            bucket["last_checked_at"] = min(
                str(sync_by_kind[kind].get("checked_at") or "")
                for kind in ("received", "stock", "shipment", "eta")
            )
        bucket["has_error"] = any(
            str(item.get("status") or "").upper() == "ERROR"
            for item in sync_by_kind.values()
        )

    ordered = sorted(
        full_scope.values(),
        key=lambda item: (
            0 if item.get("has_error") else 1,
            str(item.get("last_checked_at") or ""),
            str(item.get("go_no") or ""),
        ),
    )
    selected: dict[str, dict] = {}
    selected_ppos: set[tuple[str, str]] = set()
    selected_factory = ""
    batch_limit = max(1, int(_SOURCE_REFRESH_BATCH_SIZE))
    for item in ordered:
        if not (
            item.get("has_staged_head")
            and item.get("topology_current")
            and item.get("has_ppo")
        ):
            continue
        factory_code = str(item.get("factory_code") or "")
        if selected_factory and factory_code != selected_factory:
            continue
        item_ppos = {(factory_code, str(ppo or "")) for ppo in item.get("ppos") or set()}
        new_ppos = item_ppos - selected_ppos
        if selected and len(selected_ppos) + len(new_ppos) > batch_limit:
            continue
        selected[str(item["go_no"])] = {
            **item,
            "ppos": set(item.get("ppos") or set()),
        }
        selected_factory = selected_factory or factory_code
        selected_ppos.update(item_ppos)
        if len(selected_ppos) >= batch_limit:
            break
    return full_scope, selected


def _source_rows_fingerprint(rows: list[dict], source_kind: str) -> dict[str, tuple]:
    by_ppo: dict[str, list[tuple]] = defaultdict(list)
    for row in rows or []:
        ppo_key = str(row.get("ppo_no") or "").strip().upper()
        if not ppo_key:
            continue
        identity = (
            _normalize_sql_fabric_type_code(row.get("fabric_type")),
            str(row.get("combo_name") or "").strip().upper(),
        )
        if source_kind == "received":
            value = (
                *identity,
                _normalize_size_code(row.get("size_code")),
                round(_to_float(row.get("received_qty")), 3),
                round(_to_float(row.get("foc_qty")), 3),
            )
        elif source_kind == "stock":
            value = (
                *identity,
                _normalize_size_code(row.get("size_code")),
                round(_to_float(row.get("on_hand_qty")), 3),
                round(_to_float(row.get("allocated_qty")), 3),
                round(_to_float(row.get("reserved_qty")), 3),
            )
        else:
            value = (
                *identity,
                round(_to_float(row.get("shipment_qty")), 3),
                round(_to_float(row.get("foc_qty")), 3),
                str(row.get("eta_date") or "").strip(),
                str(row.get("ship_type") or "").strip().upper(),
            )
        by_ppo[ppo_key].append(value)
    return {ppo: tuple(sorted(values)) for ppo, values in by_ppo.items()}


def _changed_source_ppos(
    ppo_list: list[str],
    old_rows: list[dict],
    new_rows: list[dict],
    source_kind: str,
) -> set[str]:
    old_map = _source_rows_fingerprint(old_rows, source_kind)
    new_map = _source_rows_fingerprint(new_rows, source_kind)
    return {
        ppo
        for ppo in ppo_list
        if old_map.get(ppo, tuple()) != new_map.get(ppo, tuple())
    }


def _mark_source_changed_gos(conn: sqlite3.Connection, go_nos: set[str], checked_at: str) -> None:
    for go_key in sorted(go_nos):
        row = conn.execute(
            "SELECT cache_flags, cache_reason FROM go_feed WHERE go_no = ?",
            (go_key,),
        ).fetchone()
        if not row:
            continue
        flags = _split_cache_flags(row["cache_flags"])
        flags.extend(["SOURCE_DATA_CHANGED", "WAIT_SOURCE"])
        previous_reason = str(row["cache_reason"] or "").strip()
        reason = "; ".join(
            bit for bit in (previous_reason, "warehouse/shipment source changed") if bit
        )
        conn.execute(
            """
            UPDATE go_feed
            SET cache_state = 'WAIT_SOURCE',
                cache_flags = ?,
                cache_reason = ?,
                next_refresh_at = ?,
                last_build_error = ''
            WHERE go_no = ?
            """,
            (_encode_cache_flags(flags), reason[:1000], checked_at, go_key),
        )
        conn.execute(
            """
            INSERT INTO go_events (go_no, event_type, message, created_at)
            VALUES (?, 'SOURCE_CHANGED', 'Proactive source refresh detected new data', ?)
            """,
            (go_key, checked_at),
        )


def _refresh_active_source_cache_once() -> dict:
    full_scope, selected_scope = _active_source_refresh_scope()
    scope_ppo_count = len(
        {
            (str(item.get("factory_code") or ""), str(ppo or ""))
            for item in full_scope.values()
            for ppo in item.get("ppos") or set()
        }
    )
    if not selected_scope:
        return {
            "ok": True,
            "scope_go_count": len(full_scope),
            "scope_ppo_count": scope_ppo_count,
            "verified_go_count": 0,
            "changed_go_count": 0,
            "errors": [],
        }

    ppos_by_factory: dict[str, set[str]] = defaultdict(set)
    for item in selected_scope.values():
        ppos_by_factory[str(item.get("factory_code") or "")].update(item.get("ppos") or set())

    received_result: dict[str, dict] = {}
    stock_balance_result: dict[str, dict] = {}
    try:
        with _connect() as sql_conn:
            sql_conn.timeout = max(
                int(SQL_SERVER_QUERY_TIMEOUT_SEC),
                int(_SOURCE_REFRESH_QUERY_TIMEOUT_SEC),
            )
            cursor = sql_conn.cursor()
            for factory_code, ppo_set in ppos_by_factory.items():
                try:
                    rows, view_name = _load_received_foc_rows(
                        cursor,
                        factory_code,
                        sorted(ppo_set),
                        bypass_cache=True,
                    )
                    received_result[factory_code] = {"rows": rows, "view": view_name, "error": ""}
                except Exception as exc:
                    received_result[factory_code] = {
                        "rows": [],
                        "view": _FOC_VIEW_BY_FACTORY.get(factory_code, ""),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                try:
                    stock_rows, stock_view, stock_error = _load_stock_balance_rows(
                        cursor,
                        factory_code,
                        sorted(ppo_set),
                        bypass_cache=True,
                    )
                    stock_balance_result[factory_code] = {
                        "rows": stock_rows,
                        "view": stock_view,
                        "error": stock_error,
                    }
                except Exception as exc:
                    stock_balance_result[factory_code] = {
                        "rows": [],
                        "view": _STOCK_BALANCE_VIEW_BY_FACTORY.get(factory_code, f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}"),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
    except Exception as exc:
        for factory_code in ppos_by_factory:
            received_result[factory_code] = {
                "rows": [],
                "view": _FOC_VIEW_BY_FACTORY.get(factory_code, ""),
                "error": f"{type(exc).__name__}: {exc}",
            }
            stock_balance_result[factory_code] = {
                "rows": [],
                        "view": _STOCK_BALANCE_VIEW_BY_FACTORY.get(factory_code, f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}"),
                "error": f"{type(exc).__name__}: {exc}",
            }

    shipment_result: dict[str, dict] = {}
    for factory_code, ppo_set in ppos_by_factory.items():
        try:
            rows, source_key, source_table, error = _load_shipment_on_way_rows(
                factory_code,
                sorted(ppo_set),
                bypass_cache=True,
            )
            shipment_result[factory_code] = {
                "rows": rows,
                "source_key": source_key,
                "source_table": source_table,
                "error": error,
            }
        except Exception as exc:
            database, table_name, _source_factory = _shipment_source_for_factory(factory_code)
            shipment_result[factory_code] = {
                "rows": [],
                "source_key": _shipment_source_key(database, table_name),
                "source_table": f"{database}.{table_name}",
                "error": f"{type(exc).__name__}: {exc}",
            }

    checked_at = _snapshot_now()
    changed_by_factory: dict[str, set[str]] = defaultdict(set)
    errors: list[str] = []
    with _snapshot_connect() as conn:
        for factory_code, ppo_set in ppos_by_factory.items():
            ppo_list = sorted(ppo_set)
            placeholders = ",".join("?" for _ in ppo_list)
            received = received_result.get(factory_code) or {}
            received_view = str(received.get("view") or _FOC_VIEW_BY_FACTORY.get(factory_code, ""))
            received_error = str(received.get("error") or "")
            if received_error:
                errors.append(f"{factory_code} received: {received_error}")
                received_changed_ppos: set[str] = set()
            else:
                old_received_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT ppo_no, fabric_type, combo_name, size_code, received_qty, foc_qty
                        FROM sql_received_foc_by_size
                        WHERE view_name = ? AND ppo_no IN ({placeholders})
                        """,
                        [received_view, *ppo_list],
                    ).fetchall()
                ]
                new_received_rows = list(received.get("rows") or [])
                received_changed_ppos = _changed_source_ppos(
                    ppo_list,
                    old_received_rows,
                    new_received_rows,
                    "received",
                )
                conn.execute(
                    f"DELETE FROM sql_received_foc WHERE view_name = ? AND ppo_no IN ({placeholders})",
                    [received_view, *ppo_list],
                )
                conn.execute(
                    f"DELETE FROM sql_received_foc_by_size WHERE view_name = ? AND ppo_no IN ({placeholders})",
                    [received_view, *ppo_list],
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO sql_received_foc (
                        view_name, ppo_no, fabric_type, combo_name,
                        received_qty, foc_qty, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            received_view,
                            str(row.get("ppo_no") or "").strip().upper(),
                            _normalize_sql_fabric_type_code(row.get("fabric_type")),
                            str(row.get("combo_name") or "").strip(),
                            _to_float(row.get("received_qty")),
                            _to_float(row.get("foc_qty")),
                            checked_at,
                        )
                        for row in _aggregate_received_rows(new_received_rows)
                        if str(row.get("ppo_no") or "").strip()
                    ],
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO sql_received_foc_by_size (
                        view_name, ppo_no, fabric_type, combo_name, size_code,
                        received_qty, foc_qty, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            received_view,
                            str(row.get("ppo_no") or "").strip().upper(),
                            _normalize_sql_fabric_type_code(row.get("fabric_type")),
                            str(row.get("combo_name") or "").strip(),
                            str(row.get("size_code") or "").strip().upper(),
                            _to_float(row.get("received_qty")),
                            _to_float(row.get("foc_qty")),
                            checked_at,
                        )
                        for row in new_received_rows
                        if str(row.get("ppo_no") or "").strip()
                    ],
                )

            stock_balance = stock_balance_result.get(factory_code) or {}
            stock_balance_view = str(
                stock_balance.get("view")
                or _STOCK_BALANCE_VIEW_BY_FACTORY.get(factory_code, f"{STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}")
            )
            stock_balance_error = str(stock_balance.get("error") or "")
            if stock_balance_error:
                # Leave the last-good rows in place.  The sheet builder will
                # deliberately mark stock as unavailable and allocate zero
                # rather than treating receipt quantity as current on-hand.
                errors.append(f"{factory_code} stock balance: {stock_balance_error}")
                stock_balance_changed_ppos: set[str] = set()
            else:
                old_stock_balance_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT ppo_no, fabric_type, combo_name, size_code,
                               on_hand_qty, allocated_qty, reserved_qty
                        FROM sql_stock_balance
                        WHERE ppo_no IN ({placeholders})
                        """,
                        ppo_list,
                    ).fetchall()
                ]
                new_stock_balance_rows = list(stock_balance.get("rows") or [])
                stock_balance_changed_ppos = _changed_source_ppos(
                    ppo_list,
                    old_stock_balance_rows,
                    new_stock_balance_rows,
                    "stock",
                )
                # A successful query with no rows is authoritative zero: it
                # means the formerly received material has been fully issued
                # or is not on hand for this PPO/type/combo/size anymore.
                conn.execute(
                    f"DELETE FROM sql_stock_balance WHERE ppo_no IN ({placeholders})",
                    ppo_list,
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO sql_stock_balance (
                        ppo_no, fabric_type, combo_name, size_code,
                        on_hand_qty, allocated_qty, reserved_qty,
                        source_view, source_as_of, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(row.get("ppo_no") or "").strip().upper(),
                            _normalize_sql_fabric_type_code(row.get("fabric_type")),
                            str(row.get("combo_name") or "").strip(),
                            str(row.get("size_code") or "").strip().upper(),
                            _to_float(row.get("on_hand_qty")),
                            _to_float(row.get("allocated_qty")),
                            _to_float(row.get("reserved_qty")),
                            stock_balance_view,
                            str(row.get("source_as_of") or ""),
                            checked_at,
                        )
                        for row in new_stock_balance_rows
                        if str(row.get("ppo_no") or "").strip()
                    ],
                )

            shipment = shipment_result.get(factory_code) or {}
            shipment_source_key = str(shipment.get("source_key") or "")
            shipment_error = str(shipment.get("error") or "")
            if shipment_error:
                errors.append(f"{factory_code} shipment: {shipment_error}")
                shipment_changed_ppos: set[str] = set()
            else:
                old_shipment_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT ppo_no, fabric_type, combo_name, shipment_qty, foc_qty,
                               eta_date, ship_type
                        FROM sql_shipment_on_way
                        WHERE source_key = ? AND ppo_no IN ({placeholders})
                        """,
                        [shipment_source_key, *ppo_list],
                    ).fetchall()
                ]
                new_shipment_rows = list(shipment.get("rows") or [])
                shipment_changed_ppos = _changed_source_ppos(
                    ppo_list,
                    old_shipment_rows,
                    new_shipment_rows,
                    "shipment",
                )
                conn.execute(
                    f"DELETE FROM sql_shipment_on_way WHERE source_key = ? AND ppo_no IN ({placeholders})",
                    [shipment_source_key, *ppo_list],
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO sql_shipment_on_way (
                        source_key, ppo_no, fabric_type, combo_name, shipment_qty,
                        foc_qty, eta_date, ship_type, source_table, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            shipment_source_key,
                            str(row.get("ppo_no") or "").strip().upper(),
                            _normalize_sql_fabric_type_code(row.get("fabric_type")),
                            str(row.get("combo_name") or "").strip(),
                            _to_float(row.get("shipment_qty")),
                            _to_float(row.get("foc_qty")),
                            str(row.get("eta_date") or "").strip(),
                            str(row.get("ship_type") or "").strip(),
                            str(row.get("source_table") or shipment.get("source_table") or ""),
                            checked_at,
                        )
                        for row in new_shipment_rows
                        if str(row.get("ppo_no") or "").strip()
                    ],
                )

            changed_by_factory[factory_code].update(received_changed_ppos)
            changed_by_factory[factory_code].update(stock_balance_changed_ppos)
            changed_by_factory[factory_code].update(shipment_changed_ppos)

            for go_key, item in selected_scope.items():
                if str(item.get("factory_code") or "") != factory_code:
                    continue
                go_ppos = set(item.get("ppos") or set())
                received_count = sum(
                    1 for row in received.get("rows") or [] if str(row.get("ppo_no") or "").strip().upper() in go_ppos
                )
                shipment_count = sum(
                    1 for row in shipment.get("rows") or [] if str(row.get("ppo_no") or "").strip().upper() in go_ppos
                )
                stock_balance_count = sum(
                    1
                    for row in stock_balance.get("rows") or []
                    if str(row.get("ppo_no") or "").strip().upper() in go_ppos
                )
                received_changed = bool(go_ppos & received_changed_ppos)
                stock_balance_changed = bool(go_ppos & stock_balance_changed_ppos)
                shipment_changed = bool(go_ppos & shipment_changed_ppos)
                _source_cache_meta(
                    conn,
                    f"RECEIVED:{received_view}:{go_key}",
                    received_count,
                    checked_at,
                    received_error,
                    content_changed=received_changed,
                )
                _source_cache_meta(
                    conn,
                    f"STOCK_BALANCE:{stock_balance_view}:{go_key}",
                    stock_balance_count,
                    checked_at,
                    stock_balance_error,
                    content_changed=stock_balance_changed,
                )
                _source_cache_meta(
                    conn,
                    f"SHIPMENT_ON_WAY:{shipment_source_key}:{go_key}",
                    shipment_count,
                    checked_at,
                    shipment_error,
                    content_changed=shipment_changed,
                )
                _source_cache_meta(
                    conn,
                    _shipment_eta_rule_source_key(shipment_source_key, go_key),
                    shipment_count,
                    checked_at,
                    shipment_error,
                    content_changed=shipment_changed,
                )

        changed_gos = {
            go_key
            for go_key, item in full_scope.items()
            if set(item.get("ppos") or set()) & changed_by_factory.get(str(item.get("factory_code") or ""), set())
        }
        _mark_source_changed_gos(conn, changed_gos, checked_at)
        conn.commit()

    for go_key in changed_gos:
        _queue_snapshot_priority(go_key)
    return {
        "ok": not errors,
        "scope_go_count": len(full_scope),
        "scope_ppo_count": scope_ppo_count,
        "verified_go_count": len(selected_scope),
        "changed_go_count": len(changed_gos),
        "changed_go_nos": sorted(changed_gos)[:20],
        "errors": errors[:10],
    }


def _source_refresh_worker_loop() -> None:
    with _snapshot_worker_lock:
        _snapshot_worker_state["source_refresh_running"] = True
        _snapshot_worker_state["source_refresh_started_at"] = _snapshot_now()
    while True:
        try:
            result = _refresh_active_source_cache_once()
            now_text = _snapshot_now()
            with _snapshot_worker_lock:
                _snapshot_worker_state["source_refresh_last_cycle_at"] = now_text
                _snapshot_worker_state["source_refresh_scope_go_count"] = int(result.get("scope_go_count") or 0)
                _snapshot_worker_state["source_refresh_scope_ppo_count"] = int(result.get("scope_ppo_count") or 0)
                _snapshot_worker_state["source_refresh_verified_go_count"] = int(result.get("verified_go_count") or 0)
                _snapshot_worker_state["source_refresh_changed_go_count"] = int(result.get("changed_go_count") or 0)
                _snapshot_worker_state["source_refresh_last_error"] = "; ".join(result.get("errors") or [])
                if result.get("ok"):
                    _snapshot_worker_state["source_refresh_last_success_at"] = now_text
        except Exception as exc:
            with _snapshot_worker_lock:
                _snapshot_worker_state["source_refresh_last_cycle_at"] = _snapshot_now()
                _snapshot_worker_state["source_refresh_last_error"] = f"{type(exc).__name__}: {exc}"
        time.sleep(max(5, int(_SOURCE_REFRESH_INTERVAL_SEC)))


def ensure_sql_source_refresh_worker() -> None:
    _ensure_snapshot_tables()
    if not _acquire_worker_process_lease():
        return
    with _snapshot_worker_lock:
        thread = _snapshot_worker_state.get("source_refresh_thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
        worker = threading.Thread(target=_source_refresh_worker_loop, name="sql-source-refresh", daemon=True)
        _snapshot_worker_state["source_refresh_thread"] = worker
        worker.start()


def ensure_go_source_cache_current(
    go: str,
    max_age_sec: int | float | None = None,
    include_ppo_detail: bool = False,
    force: bool = False,
) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")
    is_current, meta = _go_source_cache_is_current(go_key, max_age_sec=max_age_sec)
    if is_current and not force:
        return {"ok": True, "go": go_key, "current": True, "refreshed": False, "source_cache": meta}

    lock = _go_source_sync_lock(go_key)
    with lock:
        is_current, meta = _go_source_cache_is_current(go_key, max_age_sec=max_age_sec)
        if is_current and not force:
            return {"ok": True, "go": go_key, "current": True, "refreshed": False, "source_cache": meta}
        flatknit_contract = dict(meta.get("flatknit_received_size_contract") or {})
        if not force and flatknit_contract.get("required") and not flatknit_contract.get("complete"):
            repair = _refresh_flatknit_received_size_cache(go_key)
            if not repair.get("ok"):
                return {
                    "ok": False,
                    "go": go_key,
                    "error": "Cannot refresh SQL flatknit received sizes",
                    "detail": repair.get("error") or "",
                    "source_cache": meta,
                }
            is_current, repaired_meta = _go_source_cache_is_current(go_key, max_age_sec=max_age_sec)
            if is_current:
                return {
                    "ok": True,
                    "go": go_key,
                    "current": True,
                    "refreshed": True,
                    "source_cache": repaired_meta,
                    "source_mode": "sql-live-flatknit-received",
                }
            meta = repaired_meta
        try:
            live_bundle = _load_live_go_source_bundle(
                go_key,
                include_ppo_detail=include_ppo_detail,
                bypass_memory_cache=True,
                include_order_totals=False,
            )
        except Exception as exc:
            return {
                "ok": False,
                "go": go_key,
                "error": "Cannot refresh SQL source cache for GO",
                "detail": f"{type(exc).__name__}: {exc}",
                "source_cache": meta,
            }
        if not live_bundle.get("ok"):
            return {
                "ok": False,
                "go": go_key,
                "error": "Cannot refresh SQL source cache for GO",
                "detail": live_bundle.get("detail") or live_bundle.get("error") or "",
                "source_cache": meta,
            }
        _save_go_source_cache_bundle(go_key, live_bundle)
        is_current, refreshed_meta = _go_source_cache_is_current(go_key, max_age_sec=max_age_sec)
        return {
            "ok": bool(is_current),
            "go": go_key,
            "current": bool(is_current),
            "refreshed": True,
            "source_cache": refreshed_meta,
            "source_mode": live_bundle.get("source_mode", "sql-live"),
        }


def _snapshot_source_cache_newer(go: str, cached_payload: dict | None) -> tuple[bool, dict]:
    if not isinstance(cached_payload, dict):
        return False, {}
    snapshot_meta = cached_payload.get("snapshot") if isinstance(cached_payload.get("snapshot"), dict) else {}
    snapshot_dt = _parse_iso_datetime(snapshot_meta.get("snapshot_updated_at"))
    if snapshot_dt is None:
        return False, {}
    meta = _go_source_sync_meta(go)
    source_dt = _parse_iso_datetime(meta.get("latest_synced_at"))
    if source_dt is None:
        return False, meta
    return bool(source_dt > snapshot_dt), meta


def sql_source_cache_status() -> dict:
    _ensure_snapshot_tables()
    with _snapshot_connect() as conn:
        table_counts = {}
        for table_name in (
            "coi_ui_allocations",
            "sql_go_head",
            "sql_go_lots",
            "sql_go_jo_color_qty",
            "sql_go_ppo_mapping",
            "sql_go_fabric_rows",
            "sql_go_bom_rows",
            "sql_go_jo_ppo_yy",
            "sql_received_foc",
            "sql_received_foc_by_size",
            "sql_stock_balance",
            "sql_shipment_on_way",
            "sql_ppo_order_totals",
            "sql_ppo_order_totals_by_color",
            "sql_ppo_detail_rows",
        ):
            table_counts[table_name] = int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
        latest_row = conn.execute("SELECT MAX(synced_at) FROM sql_source_sync").fetchone()
        latest_checked_row = conn.execute("SELECT MAX(last_checked_at) FROM sql_source_sync").fetchone()
        recent_rows = conn.execute(
            """
            SELECT source_key, synced_at, last_checked_at, row_count, source_status, last_error
            FROM sql_source_sync
            ORDER BY synced_at DESC
            LIMIT 10
            """
        ).fetchall()
    return {
        "source_db_file": str(_SNAPSHOT_DB),
        "table_counts": table_counts,
        "latest_synced_at": str((latest_row[0] if latest_row else "") or ""),
        "latest_checked_at": str((latest_checked_row[0] if latest_checked_row else "") or ""),
        "recent_sources": [
            {
                "source_key": str(row["source_key"] or ""),
                "synced_at": str(row["synced_at"] or ""),
                "last_checked_at": str(row["last_checked_at"] or row["synced_at"] or ""),
                "row_count": int(row["row_count"] or 0),
                "source_status": str(row["source_status"] or ""),
                "last_error": str(row["last_error"] or ""),
            }
            for row in recent_rows
        ],
    }


def _ppo_detail_color_keys(value: object) -> set[str]:
    text = str(value or "").strip().upper()
    keys = set()
    if text:
        keys.add(text)
        stripped = text.lstrip("0") or "0"
        keys.add(stripped)
        keys.add(_normalize_text(text))
    return {key for key in keys if key}


def _ppo_detail_color_key_candidates(value: object) -> list[str]:
    text = str(value or "").strip().upper()
    if not text:
        return []
    candidates = [text]
    stripped = text.lstrip("0") or "0"
    candidates.append(stripped)
    candidates.append(_normalize_text(text))
    output: list[str] = []
    seen: set[str] = set()
    for key in candidates:
        if key and key not in seen:
            seen.add(key)
            output.append(key)
    return output


def _sheet_row_ppo_color_key_candidates(color_code: object, color_desc: object, combo_name: object) -> list[str]:
    candidates = [
        color_code,
        _extract_color_token_from_combo(combo_name),
        color_desc,
        _extract_color_desc_from_combo(combo_name),
        combo_name,
    ]
    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for key in _ppo_detail_color_key_candidates(candidate):
            if key and key not in seen:
                seen.add(key)
                output.append(key)
    return output


def _sheet_row_ppo_color_keys(color_code: object, color_desc: object, combo_name: object) -> set[str]:
    return set(_sheet_row_ppo_color_key_candidates(color_code, color_desc, combo_name))


def _resolve_ppo_order_total_for_row(
    ppo_order_totals: dict,
    ppo_no: object,
    fabric_type: object,
    color_code: object,
    color_desc: object,
    combo_name: object,
) -> object:
    ppo_key = str(ppo_no or "").strip().upper()
    type_key = str(fabric_type or "").strip().upper()
    if not ppo_key or not type_key:
        return ""
    for lookup_type in _fabric_type_lookup_candidates(type_key):
        for color_key in _sheet_row_ppo_color_key_candidates(color_code, color_desc, combo_name):
            payload = ppo_order_totals.get((ppo_key, lookup_type, color_key))
            if payload:
                return payload.get("ppo_order_qty", "")
        fallback = ppo_order_totals.get((ppo_key, lookup_type, ""))
        if fallback:
            return fallback.get("ppo_order_qty", "")
        legacy = ppo_order_totals.get((ppo_key, lookup_type))
        if legacy:
            return legacy.get("ppo_order_qty", "")
    return ""


def _build_ppo_detail_assignment_maps(
    rows_by_ppo: dict[str, list[dict]],
) -> tuple[set[tuple[str, str, str]], dict[tuple[str, str, str], set[str]], set[tuple[str, str]]]:
    valid_keys: set[tuple[str, str, str]] = set()
    family_candidates: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    family_type_has_detail: set[tuple[str, str]] = set()

    for ppo_no, rows in (rows_by_ppo or {}).items():
        ppo_key = str(ppo_no or "").strip().upper()
        prefix = _ppo_family_prefix(ppo_key)
        if not ppo_key or not prefix:
            continue
        for row in rows or []:
            fabric_type = str(row.get("fabric_type") or "").strip().upper()
            if not fabric_type:
                continue
            family_type_has_detail.add((prefix, fabric_type))
            row_keys = _sheet_row_ppo_color_keys(
                row.get("color_code"),
                row.get("fabric_color"),
                row.get("fabric_combo"),
            )
            for color_key in row_keys:
                valid_keys.add((ppo_key, fabric_type, color_key))
                family_candidates[(prefix, fabric_type, color_key)].add(ppo_key)

    return valid_keys, family_candidates, family_type_has_detail


def _build_ppo_detail_yy_lookup(rows_by_ppo: dict[str, list[dict]]) -> dict[tuple[str, str, str], dict[str, float]]:
    totals: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: {"order": 0.0, "gmt": 0.0})
    lookup: dict[tuple[str, str, str], dict[str, float]] = {}

    for ppo_no, rows in (rows_by_ppo or {}).items():
        ppo_key = str(ppo_no or "").strip().upper()
        if not ppo_key:
            continue
        for row in rows or []:
            type_key = str(row.get("fabric_type") or "").strip().upper()
            explicit_ppo_yy = _to_float(row.get("detail_ppo_yy"))
            explicit_marker_yy = _to_float(row.get("detail_marker_yy"))
            order_qty = _to_float(row.get("ppo_order_qty") or row.get("fabric_total_qty"))
            gmt_qty = _to_float(row.get("gmt_qty"))
            if not type_key:
                continue
            for color_key in _sheet_row_ppo_color_key_candidates(
                row.get("color_code"),
                row.get("fabric_color"),
                row.get("fabric_combo"),
            ):
                if not color_key:
                    continue
                if explicit_ppo_yy > 0 or explicit_marker_yy > 0:
                    payload = lookup.setdefault((ppo_key, type_key, color_key), {"ppo_yy": 0.0, "marker_yy": 0.0})
                    payload["ppo_yy"] = max(payload.get("ppo_yy", 0.0), explicit_ppo_yy or explicit_marker_yy)
                    payload["marker_yy"] = max(payload.get("marker_yy", 0.0), explicit_marker_yy or explicit_ppo_yy)
                if order_qty > 0 and gmt_qty > 0:
                    bucket = totals[(ppo_key, type_key, color_key)]
                    bucket["order"] += order_qty
                    bucket["gmt"] += gmt_qty

    for key, bucket in totals.items():
        if key in lookup and (_to_float(lookup[key].get("ppo_yy")) > 0 or _to_float(lookup[key].get("marker_yy")) > 0):
            continue
        gmt_qty = _to_float(bucket.get("gmt"))
        order_qty = _to_float(bucket.get("order"))
        if gmt_qty > 0 and order_qty > 0:
            derived_yy = order_qty / gmt_qty
            lookup[key] = {"ppo_yy": derived_yy, "marker_yy": derived_yy}
    return lookup


def _resolve_ppo_detail_yy_for_row(
    lookup: dict[tuple[str, str, str], dict[str, float]],
    ppo_no: object,
    fabric_type: object,
    color_code: object,
    color_desc: object,
    combo_name: object,
) -> dict[str, float]:
    ppo_key = str(ppo_no or "").strip().upper()
    type_key = str(fabric_type or "").strip().upper()
    if not ppo_key or not type_key:
        return {}
    for lookup_type in _fabric_type_lookup_candidates(type_key):
        for color_key in _sheet_row_ppo_color_key_candidates(color_code, color_desc, combo_name):
            payload = lookup.get((ppo_key, lookup_type, color_key))
            if payload and (_to_float(payload.get("ppo_yy")) > 0 or _to_float(payload.get("marker_yy")) > 0):
                return payload
    return {}


def _resolve_sheet_effective_ppo(
    original_ppo: object,
    fabric_type: object,
    color_code: object,
    color_desc: object,
    combo_name: object,
    jo_numbers: list[str],
    allowed_ppos: set[str] | None,
    jo_ppo_yy_lookup: dict[tuple[str, str], float],
    valid_keys: set[tuple[str, str, str]],
    family_candidates: dict[tuple[str, str, str], set[str]],
    family_type_has_detail: set[tuple[str, str]],
) -> str:
    ppo_key = str(original_ppo or "").strip().upper()
    type_key = str(fabric_type or "").strip().upper()
    prefix = _ppo_family_prefix(ppo_key)
    if not ppo_key or not type_key or not prefix:
        return ppo_key

    row_keys = _sheet_row_ppo_color_keys(color_code, color_desc, combo_name)
    if not row_keys:
        return ppo_key

    allowed_set = {
        str(item or "").strip().upper()
        for item in (allowed_ppos or set())
        if str(item or "").strip()
    }
    if allowed_set:
        same_family_allowed = {item for item in allowed_set if _ppo_family_prefix(item) == prefix}
    else:
        same_family_allowed = set()

    original_supported = any((ppo_key, type_key, color_key) in valid_keys for color_key in row_keys)
    if original_supported and (not same_family_allowed or ppo_key in same_family_allowed):
        return ppo_key

    candidates: set[str] = set()
    for color_key in row_keys:
        candidates.update(family_candidates.get((prefix, type_key, color_key), set()))

    if same_family_allowed:
        candidates &= same_family_allowed
        if not candidates:
            for candidate_ppo in same_family_allowed:
                if any((candidate_ppo, type_key, color_key) in valid_keys for color_key in row_keys):
                    candidates.add(candidate_ppo)

    jo_list = [str(jo or "").strip().upper() for jo in jo_numbers if str(jo or "").strip()]

    if not candidates:
        if same_family_allowed and ppo_key not in same_family_allowed and (prefix, type_key) in family_type_has_detail:
            return ""
        if same_family_allowed and ppo_key in same_family_allowed:
            return ppo_key
        if any(_to_float(jo_ppo_yy_lookup.get((ppo_key, jo_no))) > 0 for jo_no in jo_list):
            return ppo_key
        if (prefix, type_key) in family_type_has_detail:
            return ""
        return ppo_key

    def _candidate_score(candidate_ppo: str) -> tuple[int, float, int, str]:
        positive_count = 0
        total_yy = 0.0
        for jo_no in jo_list:
            yy = _to_float(jo_ppo_yy_lookup.get((candidate_ppo, jo_no)))
            if yy > 0:
                positive_count += 1
                total_yy += yy
        return (
            positive_count,
            total_yy,
            1 if candidate_ppo == ppo_key else 0,
            candidate_ppo,
        )

    return max(sorted(candidates), key=_candidate_score)


def _harmonize_sheet_rows_by_lot_color(
    rows: list[dict],
    jo_ppo_yy_lookup: dict[tuple[str, str], float],
    valid_keys: set[tuple[str, str, str]],
    family_candidates: dict[tuple[str, str, str], set[str]],
) -> list[dict]:
    def _row_combo_identity(item: dict) -> str:
        combo = str(item.get("FABRIC_COMBO") or "").strip()
        combo_desc = _extract_color_desc_from_combo(combo) if "@" in combo else combo
        return _normalize_combo_key(combo_desc or combo)

    def _row_signature(item: dict) -> tuple[object, str, str, str, str, str]:
        return (
            _to_int(item.get("Lot")),
            str(item.get("JO") or "").strip().upper(),
            str(item.get("Type") or "").strip().upper(),
            str(_display_color_code(item) or item.get("COLOR_CODE") or "").strip().upper(),
            _row_combo_identity(item),
            # Different valid PPO branches for the same JO/lot/color are
            # separate allocation rows and must not be collapsed together.
            str(item.get("PPO") or "").strip().upper(),
        )

    def _row_dedupe_score(item: dict) -> tuple[float, float, int, int]:
        combo = str(item.get("FABRIC_COMBO") or "").strip()
        return (
            _to_float(item.get("PPO_YY")),
            _to_float(item.get("Marker_YY")),
            1 if "@" in combo else 0,
            len(combo),
        )

    def _dedupe_cluster_rows(items: list[dict]) -> list[dict]:
        dedup_rows: dict[tuple[object, str, str, str, str], dict] = {}
        for item in items:
            signature = _row_signature(item)
            existing = dedup_rows.get(signature)
            if existing is None or _row_dedupe_score(item) > _row_dedupe_score(existing):
                dedup_rows[signature] = item
        return list(dedup_rows.values())

    clusters: dict[tuple[object, str, str], list[dict]] = defaultdict(list)
    for row in rows or []:
        lot_no = _to_int(row.get("Lot"))
        jo_no = str(row.get("JO") or "").strip().upper()
        color_code = str(_display_color_code(row) or row.get("COLOR_CODE") or "").strip().upper()
        fabric_type = str(row.get("Type") or "").strip().upper()
        cluster_key = (lot_no if lot_no > 0 else jo_no, fabric_type, color_code)
        clusters[cluster_key].append(row)

    harmonized: list[dict] = []
    for cluster_rows in clusters.values():
        ppo_set = {str(row.get("PPO") or "").strip().upper() for row in cluster_rows if str(row.get("PPO") or "").strip()}
        if len(ppo_set) <= 1:
            harmonized.extend(_dedupe_cluster_rows(cluster_rows))
            continue

        # If SQL provides more than one PPO with a valid PPO-YY for the same
        # JO/lot/color, retain each branch.  They represent distinct fabric
        # purchase orders (for example DE and VE), not duplicate source rows.
        valid_ppo_branches = {
            ppo_no
            for ppo_no in ppo_set
            if any(
                _to_float(jo_ppo_yy_lookup.get((ppo_no, str(row.get("JO") or "").strip().upper()))) > 0
                for row in cluster_rows
            )
        }
        if len(valid_ppo_branches) > 1:
            harmonized.extend(_dedupe_cluster_rows(cluster_rows))
            continue

        # Keep PPO split when a branch only carries trim rows (F/O) without body rows (B).
        # Forcing one PPO here hides valid PPO variants like suffix C in some GOs.
        ppo_type_map: dict[str, set[str]] = defaultdict(set)
        for row in cluster_rows:
            row_ppo = str(row.get("PPO") or "").strip().upper()
            row_type = str(row.get("Type") or "").strip().upper()
            if row_ppo and row_type:
                ppo_type_map[row_ppo].add(row_type)
        if any(("B" not in type_set) and bool({"F", "O"} & type_set) for type_set in ppo_type_map.values()):
            harmonized.extend(_dedupe_cluster_rows(cluster_rows))
            continue

        prefixes = {_ppo_family_prefix(ppo_no) for ppo_no in ppo_set if _ppo_family_prefix(ppo_no)}
        if len(prefixes) != 1:
            harmonized.extend(_dedupe_cluster_rows(cluster_rows))
            continue
        prefix = next(iter(prefixes))

        candidate_ppos = set(ppo_set)
        row_key_map: dict[int, set[str]] = {}
        for idx, row in enumerate(cluster_rows):
            row_keys = _sheet_row_ppo_color_keys(
                row.get("COLOR_CODE"),
                row.get("COLOR_DESC"),
                row.get("FABRIC_COMBO"),
            )
            row_key_map[idx] = row_keys
            fabric_type = str(row.get("Type") or "").strip().upper()
            for color_key in row_keys:
                candidate_ppos.update(family_candidates.get((prefix, fabric_type, color_key), set()))

        def _supports(candidate_ppo: str, row_index: int) -> bool:
            row = cluster_rows[row_index]
            fabric_type = str(row.get("Type") or "").strip().upper()
            return any((candidate_ppo, fabric_type, color_key) in valid_keys for color_key in row_key_map.get(row_index, set()))

        def _score(candidate_ppo: str) -> tuple[int, int, int, int, float, int, str]:
            supported_rows = 0
            has_b = 0
            of_supported = 0
            jo_positive = 0
            jo_total = 0.0
            current_match = 0
            seen_jos: set[str] = set()
            for idx, row in enumerate(cluster_rows):
                row_ppo = str(row.get("PPO") or "").strip().upper()
                row_type = str(row.get("Type") or "").strip().upper()
                jo_no = str(row.get("JO") or "").strip().upper()
                if row_ppo == candidate_ppo:
                    current_match += 1
                if _supports(candidate_ppo, idx):
                    supported_rows += 1
                    if row_type == "B":
                        has_b = 1
                    if row_type in {"O", "F"}:
                        of_supported += 1
                if jo_no and jo_no not in seen_jos:
                    seen_jos.add(jo_no)
                    yy = _to_float(jo_ppo_yy_lookup.get((candidate_ppo, jo_no)))
                    if yy > 0:
                        jo_positive += 1
                        jo_total += yy
            return (has_b, supported_rows, of_supported, jo_positive, jo_total, current_match, candidate_ppo)

        chosen_ppo = max(sorted(candidate_ppos), key=_score)
        supported_count = sum(1 for idx in range(len(cluster_rows)) if _supports(chosen_ppo, idx))
        if supported_count != len(cluster_rows):
            harmonized.extend(_dedupe_cluster_rows(cluster_rows))
            continue

        dedup_rows: dict[tuple[object, str, str, str, str], dict] = {}
        for row in cluster_rows:
            adjusted = dict(row)
            adjusted["PPO"] = chosen_ppo
            signature = _row_signature(adjusted)
            existing = dedup_rows.get(signature)
            if existing is None or _row_dedupe_score(adjusted) > _row_dedupe_score(existing):
                dedup_rows[signature] = adjusted
        harmonized.extend(dedup_rows.values())

    return harmonized


def _split_flatknit_rows_by_size(rows: list[dict], size_qty_rows: list[dict]) -> list[dict]:
    if not rows or not size_qty_rows:
        return rows

    size_lookup: dict[tuple[int, str, str], list[dict]] = defaultdict(list)
    for item in size_qty_rows or []:
        lot_no = _to_int(item.get("lot_no"))
        jo_no = str(item.get("jo_no") or "").strip().upper()
        color_code = str(item.get("color_code") or "").strip().upper()
        size_code = str(item.get("size_code") or "").strip()
        qty = _to_float(item.get("qty"))
        if lot_no <= 0 or not jo_no or not color_code or not size_code or qty <= 0:
            continue
        size_lookup[(lot_no, jo_no, color_code)].append(
            {
                "size_code": size_code,
                "qty": qty,
                "color_desc": str(item.get("color_desc") or "").strip(),
            }
        )

    if not size_lookup:
        return rows

    output: list[dict] = []
    for row in rows:
        fabric_type = str(row.get("Type") or "").strip().upper()
        if fabric_type not in _FLATKNIT_SIZE_TYPES:
            output.append(row)
            continue
        key = (
            _to_int(row.get("Lot")),
            str(row.get("JO") or "").strip().upper(),
            str(_display_color_code(row) or row.get("COLOR_CODE") or "").strip().upper(),
        )
        matches = size_lookup.get(key)
        if not matches:
            output.append(row)
            continue
        for item in matches:
            cloned = dict(row)
            cloned["SIZE"] = str(item.get("size_code") or "").strip()
            cloned["Qty"] = round(_to_float(item.get("qty")), 3)
            if not str(cloned.get("COLOR_DESC") or "").strip() and str(item.get("color_desc") or "").strip():
                cloned["COLOR_DESC"] = str(item.get("color_desc") or "").strip()
            output.append(cloned)

    return output


def _pop_matching_yy_value(values: list[float], target: float, tolerance: float = 0.001) -> float:
    if target <= 0:
        return 0.0
    best_index = -1
    best_diff = tolerance
    for index, value in enumerate(values):
        diff = abs(_to_float(value) - target)
        if diff <= best_diff:
            best_index = index
            best_diff = diff
    if best_index < 0:
        return 0.0
    return values.pop(best_index)


def _infer_missing_jo_ppo_yy_for_rows(
    rows: list[dict],
    sql_bom_lookup: dict[tuple[str, str], dict],
    sql_fabric_yy_lookup: dict[tuple[str, str, str], dict],
    jo_ppo_yy_rows: list[dict],
) -> dict[int, dict]:
    """Fill zero typed YY rows from untyped JO/PPO YY values when SQL BOM is incomplete.

    V_JO_PPO_YY does not carry fabric type/color.  The same JO/PPO YY value can be
    shared by multiple garment colors, so treating each YY value as consumable once
    leaves valid M/M1/M2 rows at zero.  For missing M-family rows, prefer the large
    untyped YY value not already explained by the same display color; if none is
    left, reuse the largest large YY for that JO/PPO.
    """

    yy_values_by_jo_ppo = _build_jo_ppo_yy_value_lookup(jo_ppo_yy_rows)
    if not yy_values_by_jo_ppo:
        return {}

    grouped_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows or []:
        ppo_no = str(row.get("PPO") or "").strip().upper()
        jo_no = str(row.get("JO") or "").strip().upper()
        if ppo_no and jo_no:
            grouped_rows[(ppo_no, jo_no)].append(row)

    def _values_without_matches(values: list[float], used_values: list[float], tolerance: float = 0.001) -> list[float]:
        output = list(values or [])
        for used in used_values or []:
            _pop_matching_yy_value(output, _to_float(used), tolerance=tolerance)
        return output

    inferred: dict[int, dict] = {}
    for key, group_rows in grouped_rows.items():
        group_values = list(yy_values_by_jo_ppo.get(key) or [])
        if not group_values:
            continue

        missing_rows: list[dict] = []
        marker_ratios: list[float] = []
        marker_ratios_by_ppo_yy: dict[float, list[float]] = defaultdict(list)
        known_yy_values: list[float] = []
        known_yy_by_color: dict[str, list[float]] = defaultdict(list)
        for row in group_rows:
            display_color = _display_color_code(row)
            display_color_desc = str(row.get("COLOR_DESC") or "").strip() or _extract_color_desc_from_combo(row.get("FABRIC_COMBO"))
            probe = {
                "PPO": row.get("PPO"),
                "Type": row.get("Type"),
                "COLOR_CODE": display_color,
                "COLOR_DESC": display_color_desc,
                "FABRIC COLOR (For piecing only)": str(row.get("FABRIC_COMBO") or "").strip(),
            }
            sql_bom_row = _resolve_sql_bom_row(probe, sql_bom_lookup)
            sql_fabric_row = _resolve_sql_fabric_yy_row(probe, sql_fabric_yy_lookup)
            known_ppo_yy = (
                _to_float(sql_bom_row.get("yy"))
                or _to_float(sql_fabric_row.get("ppo_yy"))
                or _to_float(row.get("PPO_YY"))
            )
            known_marker_yy = (
                _to_float(sql_bom_row.get("marker_yy"))
                or _to_float(sql_fabric_row.get("marker_yy"))
                or _to_float(row.get("Marker_YY"))
            )
            if known_ppo_yy > 0:
                known_yy_values.append(known_ppo_yy)
                known_yy_by_color[_normalize_text(display_color)].append(known_ppo_yy)
                if known_marker_yy > 0:
                    ratio = known_marker_yy / known_ppo_yy
                    if 0.50 <= ratio <= 1.20:
                        marker_ratios.append(ratio)
                        marker_ratios_by_ppo_yy[round(known_ppo_yy, 4)].append(ratio)
            else:
                missing_rows.append(row)

        if not missing_rows:
            continue

        remaining_values = _values_without_matches(group_values, known_yy_values)
        large_group_values = [value for value in group_values if _to_float(value) >= 0.05]
        large_remaining_values = [value for value in remaining_values if _to_float(value) >= 0.05]
        small_group_values = [value for value in group_values if 0 < _to_float(value) < 0.05]

        for row in missing_rows:
            type_key = _normalize_sql_fabric_type_code(row.get("Type"))
            color_key = _normalize_text(_display_color_code(row))

            candidate_values: list[float] = []
            if type_key.startswith("M"):
                same_color_known = known_yy_by_color.get(color_key, [])
                candidate_values = [
                    value
                    for value in _values_without_matches(large_group_values, same_color_known)
                    if _to_float(value) >= 0.05
                ]
                if not candidate_values:
                    candidate_values = list(large_remaining_values or large_group_values)
            elif len(missing_rows) == 1 and len(remaining_values) == 1:
                candidate_values = remaining_values
            elif type_key in {"R", "I"} and small_group_values:
                candidate_values = small_group_values

            fallback_ppo_yy = _to_float(candidate_values[-1] if candidate_values else 0.0)
            if fallback_ppo_yy <= 0:
                continue
            matched_ratios = marker_ratios_by_ppo_yy.get(round(fallback_ppo_yy, 4), [])
            marker_ratio = (
                (sum(matched_ratios) / len(matched_ratios))
                if matched_ratios
                else ((sum(marker_ratios) / len(marker_ratios)) if marker_ratios else 1.0)
            )
            inferred[id(row)] = {
                "ppo_yy": fallback_ppo_yy,
                "marker_yy": fallback_ppo_yy * marker_ratio,
            }

    return inferred


def _allocation_pool_key_for_row(
    ppo_no: object,
    fabric_type: object,
    color_code: object,
    fabric_combo: object,
    size_code: object = "",
) -> tuple:
    # Received and shipment quantities are keyed by exact PPO in SQL.  Pooling
    # sibling PPO suffixes (for example ...1536C and ...1536F) double-counts
    # fabric that was purchased/received separately.
    ppo_key = str(ppo_no or "").strip().upper()
    type_key = str(fabric_type or "").strip().upper()
    color_key = str(color_code or "").strip().upper()
    combo_key = _normalize_combo_key(fabric_combo)
    size_key = _normalize_size_code(size_code)
    # A matched source combo is the physical warehouse identity.  Do not add
    # the UI garment color to that key: two display colors can legitimately
    # resolve to one PPO/type/combo, and adding both would allocate the same
    # on-hand balance twice.  When SQL has no combo identity, color remains
    # the conservative fallback partition.
    if type_key in _FLATKNIT_SIZE_TYPES and size_key:
        if combo_key:
            return ("POOL", ppo_key, type_key, combo_key, size_key)
        return ("POOL", ppo_key, type_key, color_key, size_key)
    if combo_key:
        return ("POOL", ppo_key, type_key, combo_key)
    return ("POOL", ppo_key, type_key, color_key)


def _allocation_source_identity_for_group(group_key: tuple) -> tuple:
    """Return the physical warehouse identity counted once per pool."""
    ppo_no, fabric_type, color_code, combo_key, size_code = group_key
    ppo_key = str(ppo_no or "").strip().upper()
    type_key = str(fabric_type or "").strip().upper()
    color_key = str(color_code or "").strip().upper()
    combo = _normalize_combo_key(combo_key)
    size_key = _normalize_size_code(size_code)
    if combo:
        return ("SOURCE", ppo_key, type_key, combo, size_key)
    return ("SOURCE", ppo_key, type_key, color_key, size_key)


def _spread_remaining_allocate(rows: list[dict], remaining: float) -> float:
    candidates = [item for item in rows or [] if isinstance(item, dict)]
    remainder = round(max(_to_float(remaining), 0.0), 3)
    if remainder <= 0 or not candidates:
        return remainder

    weights: list[float] = []
    total_weight = 0.0
    for item in candidates:
        weight = max(_to_float(item.get("Required Q'ty (Yds)")), 1.0)
        weights.append(weight)
        total_weight += weight

    distributed = 0.0
    for index, item in enumerate(candidates):
        current = _to_float(item.get("Allocate Q'ty (Yds)"))
        if index == len(candidates) - 1:
            extra = round(max(remainder - distributed, 0.0), 3)
        else:
            ratio = (weights[index] / total_weight) if total_weight > 0 else (1.0 / len(candidates))
            extra = round(remainder * ratio, 3)
            extra = min(extra, max(remainder - distributed, 0.0))
        item["Allocate Q'ty (Yds)"] = round(current + extra, 3)
        distributed += extra

    return round(max(remainder - distributed, 0.0), 3)


def _spread_remaining_allocate_map(rows: list[dict], allocations: dict[int, float], remaining: float) -> float:
    candidates = [item for item in rows or [] if isinstance(item, dict)]
    remainder = round(max(_to_float(remaining), 0.0), 3)
    if remainder <= 0 or not candidates:
        return remainder

    weights: list[float] = []
    total_weight = 0.0
    for item in candidates:
        weight = max(_to_float(item.get("Required Q'ty (Yds)")), 1.0)
        weights.append(weight)
        total_weight += weight

    distributed = 0.0
    for index, item in enumerate(candidates):
        current = _to_float(allocations.get(id(item), 0.0))
        if index == len(candidates) - 1:
            extra = round(max(remainder - distributed, 0.0), 3)
        else:
            ratio = (weights[index] / total_weight) if total_weight > 0 else (1.0 / len(candidates))
            extra = round(remainder * ratio, 3)
            extra = min(extra, max(remainder - distributed, 0.0))
        allocations[id(item)] = round(current + extra, 3)
        distributed += extra

    return round(max(remainder - distributed, 0.0), 3)


def _compute_pool_system_allocations(rows: list[dict], total_available: float, respect_manual: bool = True) -> dict[int, float]:
    allocations: dict[int, float] = {}
    locked_reserved = 0.0
    manual_reserved = 0.0
    for item in rows or []:
        allocations[id(item)] = 0.0
        locked_qty = max(_to_float(item.get("__issue_locked_qty")), 0.0)
        if locked_qty > 0:
            allocations[id(item)] = round(locked_qty, 3)
            locked_reserved += locked_qty
            continue
        if respect_manual and str(item.get("AH Allocate Q'ty (yds)") or "").strip() != "":
            manual_reserved += max(_to_float(item.get("AH Allocate Q'ty (yds)")), 0.0)

    remaining = max(_to_float(total_available) - locked_reserved - manual_reserved, 0.0)
    system_rows = [
        item
        for item in rows or []
        if _to_float(item.get("__issue_locked_qty")) <= 0
        and (not respect_manual or str(item.get("AH Allocate Q'ty (yds)") or "").strip() == "")
    ]

    system_rows.sort(
        key=lambda item: (
            _sheet_cutting_priority(item),
            item["__due_sort_key"],
            0 if _to_float(item.get("Required Q'ty (Yds)")) < 200.0 else 1,
            item["__storage"]["lot_no"],
            item["JOB ORDER NO"],
        )
    )

    for item in system_rows:
        need_100 = _to_float(item.get("Required Q'ty (Yds)"))
        alloc = min(remaining, max(need_100, 0.0))
        allocations[id(item)] = round(alloc, 3)
        remaining -= alloc

    system_rows.sort(
        key=lambda item: (
            _sheet_cutting_priority(item),
            item["__due_sort_key"],
            item["__storage"]["lot_no"],
            item["JOB ORDER NO"],
        )
    )
    for item in system_rows:
        if remaining <= 0:
            break
        current = _to_float(allocations.get(id(item), 0.0))
        target_qty = _to_float(item.get("__target_qty"))
        extra_need = max(target_qty - current, 0.0)
        extra = min(remaining, extra_need)
        allocations[id(item)] = round(current + extra, 3)
        remaining -= extra

    if remaining > 0 and system_rows:
        system_rows.sort(
            key=lambda item: (
                _sheet_cutting_priority(item),
                item["__due_sort_key"],
                item["__storage"]["lot_no"],
                item["JOB ORDER NO"],
            )
        )
        _spread_remaining_allocate_map(system_rows, allocations, remaining)

    return allocations


def _sheet_row_placeholder_signature(row: dict) -> tuple[str, str, str]:
    color_code = str(_display_color_code(row) or row.get("COLOR_CODE") or "").strip().upper()
    color_desc = _normalize_text(row.get("COLOR_DESC") or _extract_color_desc_from_combo(row.get("FABRIC_COMBO")))
    return (
        str(row.get("Type") or "").strip().upper(),
        color_code,
        color_desc,
    )


def _prune_unbound_placeholder_rows(rows: list[dict]) -> list[dict]:
    bound_signatures: set[tuple[str, str, str]] = set()
    for row in rows or []:
        jo_no = str(row.get("JO") or "").strip().upper()
        lot_no = _to_int(row.get("Lot"))
        qty = _to_float(row.get("Qty"))
        if jo_no or lot_no > 0 or qty > 0:
            bound_signatures.add(_sheet_row_placeholder_signature(row))

    filtered: list[dict] = []
    for row in rows or []:
        jo_no = str(row.get("JO") or "").strip().upper()
        lot_no = _to_int(row.get("Lot"))
        qty = _to_float(row.get("Qty"))
        if not jo_no and lot_no <= 0 and qty <= 0:
            if _sheet_row_placeholder_signature(row) in bound_signatures:
                continue
        filtered.append(row)
    return filtered


def load_ppo_order_detail(ppo: str) -> dict:
    ppo_key = str(ppo or "").strip().upper()
    if not ppo_key:
        return {"ok": False, "error": "PPO number required", "ppo": ""}

    try:
        with _connect() as conn:
            cursor = conn.cursor()
            sql_rows = _load_ppo_detail_rows_sql(cursor, ppo_key)
        if sql_rows:
            totals_by_type: dict[str, dict] = {}
            for item in sql_rows:
                fabric_type = str(item.get("fabric_type") or "").strip().upper()
                bucket = totals_by_type.setdefault(
                    fabric_type,
                    {
                        "fabric_type": fabric_type,
                        "fabric_part": str(item.get("fabric_part") or "").strip(),
                        "gmt_qty": 0.0,
                        "fabric_total_qty": 0.0,
                        "ppo_order_qty": 0.0,
                        "line_count": 0,
                    },
                )
                bucket["gmt_qty"] += _to_float(item.get("gmt_qty"))
                bucket["fabric_total_qty"] += _to_float(item.get("fabric_total_qty"))
                bucket["ppo_order_qty"] += _to_float(item.get("ppo_order_qty"))
                bucket["line_count"] += 1
            normalized_totals = {
                key: {
                    **value,
                    "gmt_qty": round(_to_float(value.get("gmt_qty")), 3),
                    "fabric_total_qty": round(_to_float(value.get("fabric_total_qty")), 3),
                    "ppo_order_qty": round(_to_float(value.get("ppo_order_qty")), 3),
                }
                for key, value in totals_by_type.items()
            }
            return {
                "ok": True,
                "ppo": ppo_key,
                "fetch_backend": "sql",
                "source_mode": "sql_view",
                "source_url": "dbo.V_PPO_Summary_All_After_2015",
                "rows": sql_rows,
                "row_count": len(sql_rows),
                "totals_by_type": normalized_totals,
            }
    except Exception:
        pass

    detail = fetch_ppo_fabric_combos(ppo_key, backend="auto")
    if not detail.get("ok"):
        return {
            "ok": False,
            "ppo": ppo_key,
            "error": detail.get("error", "Cannot fetch PPO detail"),
            "source_url": detail.get("source_url", ""),
            "rows": [],
            "totals_by_type": {},
        }

    rows = []
    totals_by_type: dict[str, dict] = {}
    for item in detail.get("fabric_lines") or []:
        fabric_type = str(item.get("fabric_type") or "").strip().upper()
        fabric_part = _format_fabric_part_with_type(item.get("fabric_part"))
        gmt_qty = _to_float(item.get("gmt_qty"))
        fabric_total_qty = _to_float(item.get("ppo_pur_qty") or item.get("order_qty"))
        ppo_pur_qty = _to_float(item.get("ppo_pur_qty") or item.get("order_qty"))
        row = {
            "ppo": ppo_key,
            "fabric_type": fabric_type,
            "fabric_part": fabric_part,
            "color_code": str(item.get("color_code") or "").strip(),
            "fabric_combo": str(item.get("fabric_combo") or "").strip(),
            "fabric_color": str(item.get("fabric_color") or "").strip(),
            "fabric_code": str(item.get("fabric_code") or "").strip(),
            "gmt_qty": round(gmt_qty, 3),
            "fabric_total_qty": round(fabric_total_qty, 3),
            "ppo_order_qty": round(ppo_pur_qty, 3),
        }
        rows.append(row)
        bucket = totals_by_type.setdefault(
            fabric_type,
            {
                "fabric_type": fabric_type,
                "fabric_part": fabric_part,
                "gmt_qty": 0.0,
                "fabric_total_qty": 0.0,
                "ppo_order_qty": 0.0,
                "line_count": 0,
            },
        )
        bucket["gmt_qty"] += gmt_qty
        bucket["fabric_total_qty"] += fabric_total_qty
        bucket["ppo_order_qty"] += ppo_pur_qty
        bucket["line_count"] += 1
    normalized_totals = {
        key: {
            **value,
            "gmt_qty": round(_to_float(value.get("gmt_qty")), 3),
            "fabric_total_qty": round(_to_float(value.get("fabric_total_qty")), 3),
            "ppo_order_qty": round(_to_float(value.get("ppo_order_qty")), 3),
        }
        for key, value in totals_by_type.items()
    }
    return {
        "ok": True,
        "ppo": ppo_key,
        "fetch_backend": detail.get("fetch_backend", ""),
        "source_mode": detail.get("source_mode", ""),
        "source_url": detail.get("source_url", ""),
        "rows": rows,
        "row_count": len(rows),
        "totals_by_type": normalized_totals,
    }


def _find_received_row(
    received_lookup: dict[tuple, dict],
    ppo: str,
    fabric_type: str,
    combo_name: str,
    color_code: str,
    size_code: object = "",
    *,
    prefer_color_identity: bool = False,
) -> dict | None:
    ppo_key = str(ppo or "").upper().strip()
    type_key = str(fabric_type or "").upper().strip()
    color_keys = _color_code_lookup_keys(color_code)
    size_key = _normalize_size_code(size_code)

    for lookup_type in _fabric_type_lookup_candidates(type_key):
        # Flatknit UI rows identify the garment color (the prefix before @ in
        # the warehouse combo).  The trailing fabric color can be shared by
        # several garment colors, e.g. 01@018 Navy / 018@018 Navy / 023@018
        # Navy.  Matching the trailing text first would merge all three.
        if prefer_color_identity:
            for color_key in color_keys:
                key = (ppo_key, lookup_type, color_key, size_key) if size_key else (ppo_key, lookup_type, color_key)
                by_color = received_lookup.get(key)
                if by_color:
                    return by_color
            continue
        for combo_key in _combo_match_candidates(combo_name):
            key = (ppo_key, lookup_type, combo_key, size_key) if size_key else (ppo_key, lookup_type, combo_key)
            direct = received_lookup.get(key)
            if direct:
                return direct
        for combo_key in _combo_match_candidates(f"{color_code}@{combo_name}"):
            key = (ppo_key, lookup_type, combo_key, size_key) if size_key else (ppo_key, lookup_type, combo_key)
            alt = received_lookup.get(key)
            if alt:
                return alt
        for color_key in color_keys:
            key = (ppo_key, lookup_type, color_key, size_key) if size_key else (ppo_key, lookup_type, color_key)
            by_color = received_lookup.get(key)
            if by_color:
                return by_color
    # None is materially different from an authoritative source row whose
    # quantity is zero (for example after a supplier return).
    return None


def _merge_received_lookup_payload(lookup: dict[tuple, dict], key: tuple, payload: dict) -> None:
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = dict(payload)
        return
    existing["received_qty"] = _to_float(existing.get("received_qty")) + _to_float(payload.get("received_qty"))
    existing["foc_qty"] = _to_float(existing.get("foc_qty")) + _to_float(payload.get("foc_qty"))


def _merge_stock_balance_lookup_payload(lookup: dict[tuple, dict], key: tuple, payload: dict) -> None:
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = dict(payload)
        return
    for quantity_key in ("on_hand_qty", "allocated_qty", "reserved_qty"):
        existing[quantity_key] = _to_float(existing.get(quantity_key)) + _to_float(payload.get(quantity_key))


def _find_stock_balance_row(
    stock_lookup: dict[tuple, dict],
    ppo: str,
    fabric_type: str,
    combo_name: str,
    color_code: str,
    size_code: object = "",
    *,
    prefer_color_identity: bool = False,
) -> dict | None:
    # Identity matching is deliberately identical to Received/FOC matching;
    # stock and receipt rows use the same PPO/type/combo/size vocabulary.
    return _find_received_row(
        stock_lookup,
        ppo,
        fabric_type,
        combo_name,
        color_code,
        size_code,
        prefer_color_identity=prefer_color_identity,
    )


def _matched_source_combo_key(match: dict | None, fallback_combo: object) -> str:
    payload = match or {}
    source_key = str(payload.get("source_combo_key") or "").strip().upper()
    if source_key:
        return source_key
    fallback_key = _normalize_combo_key(fallback_combo)
    return fallback_key or str(fallback_combo or "").strip().upper()


def _received_row_identity(item: dict) -> tuple[str, str, str, str]:
    ppo_key = str(item.get("ppo_no") or "").strip().upper()
    type_key = _normalize_sql_fabric_type_code(item.get("fabric_type"))
    combo_key = _normalize_combo_key(item.get("combo_name"))
    size_key = _normalize_size_code(item.get("size_code"))
    return (ppo_key, type_key, combo_key, size_key)


def _find_m_family_near_order_received_row(
    received_rows: list[dict],
    ppo: str,
    fabric_type: str,
    combo_name: str,
    color_code: str,
    ppo_order_totals: dict,
    target_order_qty: object,
    used_candidates: set[tuple[str, str, str]],
) -> tuple[dict, tuple[str, str, str] | None]:
    # Quantity proximity across different fabric types is not identity
    # evidence. Keep this compatibility function disabled so an authoritative
    # zero or an unmatched component is never replaced by another type.
    return {"received_qty": 0.0, "foc_qty": 0.0}, None

    # Legacy heuristic retained below for reference until callers are removed.
    ppo_key = str(ppo or "").strip().upper()
    type_key = _normalize_sql_fabric_type_code(fabric_type)
    if not ppo_key or not type_key.startswith("M"):
        return {"received_qty": 0.0, "foc_qty": 0.0}, None

    target_order = _to_float(target_order_qty)
    if target_order <= 0:
        return {"received_qty": 0.0, "foc_qty": 0.0}, None

    target_combo_keys = set(_combo_match_candidates(combo_name))
    for key in _combo_match_candidates(f"{color_code}@{combo_name}"):
        target_combo_keys.add(key)
    if not target_combo_keys:
        return {"received_qty": 0.0, "foc_qty": 0.0}, None

    max_delta = max(10.0, target_order * 0.25)
    candidates: list[tuple[float, float, dict, tuple[str, str, str]]] = []
    for item in received_rows or []:
        item_ppo = str(item.get("ppo_no") or "").strip().upper()
        item_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        if item_ppo != ppo_key or not item_type or item_type == type_key:
            continue
        if item_type in {"B", "R"}:
            continue

        item_combo = str(item.get("combo_name") or "").strip()
        item_combo_keys = set(_combo_match_candidates(item_combo))
        if not (target_combo_keys & item_combo_keys):
            continue

        candidate_key = _received_row_identity(item)
        if candidate_key in used_candidates:
            continue

        candidate_received = _display_received_qty(item)
        if candidate_received <= 0:
            continue

        item_color = _extract_color_code_from_combo(item_combo)
        item_desc = _extract_color_desc_from_combo(item_combo)
        candidate_order = _to_float(
            _resolve_ppo_order_total_for_row(
                ppo_order_totals,
                ppo_key,
                item_type,
                item_color,
                item_desc,
                item_combo,
            )
        )
        if candidate_order <= 0:
            continue

        delta = abs(candidate_order - target_order)
        if delta > max_delta:
            continue
        candidates.append((delta, abs(candidate_received - target_order), item, candidate_key))

    if not candidates:
        return {"received_qty": 0.0, "foc_qty": 0.0}, None

    candidates.sort(key=lambda value: (value[0], value[1], value[3]))
    _delta, _received_delta, item, candidate_key = candidates[0]
    return {
        "received_qty": _to_float(item.get("received_qty")),
        "foc_qty": _to_float(item.get("foc_qty")),
        "source_combo_key": _normalize_combo_key(item.get("combo_name")),
        "source_combo_name": str(item.get("combo_name") or "").strip(),
    }, candidate_key


def _find_shipment_on_way_row(
    shipment_lookup: dict[tuple[str, str, str], dict],
    ppo: str,
    fabric_type: str,
    combo_name: str,
    color_code: str,
) -> dict:
    ppo_key = str(ppo or "").upper().strip()
    type_key = str(fabric_type or "").upper().strip()
    color_keys = _color_code_lookup_keys(color_code)

    for lookup_type in _fabric_type_lookup_candidates(type_key):
        for combo_key in _combo_match_candidates(combo_name):
            direct = shipment_lookup.get((ppo_key, lookup_type, combo_key))
            if direct:
                return direct
        for combo_key in _combo_match_candidates(f"{color_code}@{combo_name}"):
            alt = shipment_lookup.get((ppo_key, lookup_type, combo_key))
            if alt:
                return alt
        for color_key in color_keys:
            by_color = shipment_lookup.get((ppo_key, lookup_type, color_key))
            if by_color:
                return by_color
    return {"shipment_qty": 0.0, "foc_qty": 0.0, "eta_date": "", "ship_type": "", "source_combo_key": ""}


def _format_short_date(value: object) -> str:
    parsed = _parse_due_date(value)
    if parsed is None:
        return str(value or "").strip()
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def _format_pct_piece(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text or "0"


def _looks_like_on_way_etd_segment(value: object) -> bool:
    text = str(value or "").upper()
    return "ON THE WAY" in text or "TRONG KHO" in text or "�� TRONG KHO" in text or "DA TRONG KHO" in text


def _remark_segment_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def _merge_on_way_etd_remark(existing: object, on_way_remark: str) -> str:
    on_way_text = str(on_way_remark or "").strip()
    segments = [
        str(segment or "").strip()
        for segment in re.split(r"\s*\|\s*", str(existing or ""))
        if str(segment or "").strip() and not _looks_like_on_way_etd_segment(segment)
    ]
    if on_way_text:
        segments.append(on_way_text)
    output: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        key = _remark_segment_key(segment)
        if key and key not in seen:
            seen.add(key)
            output.append(segment)
    return " | ".join(output)


def _build_on_way_etd_remark(received_qty: float, on_way_qty: float, order_qty: float, eta_date: object) -> str:
    received_qty = max(_to_float(received_qty), 0.0)
    on_way_qty = max(_to_float(on_way_qty), 0.0)
    if on_way_qty <= 0:
        return ""
    denominator = max(_to_float(order_qty), received_qty + on_way_qty)
    if denominator <= 0:
        return ""
    received_pct = min(max((received_qty / denominator) * 100.0, 0.0), 100.0)
    on_way_pct = min(max((on_way_qty / denominator) * 100.0, 0.0), 100.0)
    eta_text = _format_short_date(eta_date)
    eta_part = f" ETA {eta_text}" if eta_text else ""
    return f"{_format_pct_piece(received_pct)}% da trong kho, {_format_pct_piece(on_way_pct)}% on the way{eta_part}"


def _merge_sql_lots_with_go_report(sql_lots: list[dict], go_report: dict | None) -> list[dict]:
    merged: dict[tuple[int, str], dict] = {}
    for row in sql_lots or []:
        lot_no = _to_int(row.get("lot_no"))
        jo_no = str(row.get("jo_no") or "").strip().upper()
        if lot_no <= 0 and not jo_no:
            continue
        merged[(lot_no, jo_no)] = dict(row)

    for item in (go_report or {}).get("lot_rows") or []:
        lot_no = _to_int(item.get("lot"))
        jo_no = str(item.get("job_order_no") or "").strip().upper()
        if lot_no <= 0 and not jo_no:
            continue
        key = (lot_no, jo_no)
        existing = dict(merged.get(key) or {})
        existing["lot_no"] = lot_no
        existing["jo_no"] = jo_no
        existing["qty"] = _to_float(existing.get("qty")) or _to_float(item.get("qty"))
        existing["buyer_po_del_date"] = existing.get("buyer_po_del_date") or item.get("buyer_po_del_date")
        existing["buyer_po_no"] = existing.get("buyer_po_no") or item.get("buyer_po_no") or ""
        existing["short_pct"] = _resolve_allowance_pct(item.get("minus_pct"), existing.get("short_pct"))
        existing["over_pct"] = _resolve_allowance_pct(item.get("plus_pct"), existing.get("over_pct"))
        merged[key] = existing

    rows = list(merged.values())
    rows.sort(key=lambda row: (_to_int(row.get("lot_no")), str(row.get("jo_no") or "")))
    return rows


def _build_go_report_ppo_mapping(go_report: dict | None) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for item in (go_report or {}).get("ppo_mapping") or []:
        ppo_no = str(item.get("ppo") or "").strip().upper()
        lot_no = _to_int(item.get("lot"))
        if not ppo_no:
            continue
        key = (ppo_no, lot_no)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"ppo_no": ppo_no, "lot_no": lot_no})
    rows.sort(key=lambda row: (str(row.get("ppo_no") or ""), _to_int(row.get("lot_no"))))
    return rows


def _build_sql_bom_lookup(rows: list[dict]) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    for item in rows or []:
        type_key = str(item.get("fabric_type_cd") or "").strip().upper()
        if not type_key:
            continue
        candidates = [
            item.get("style_color_code"),
            item.get("style_color_desc"),
            item.get("combo_name"),
        ]
        for candidate in candidates:
            key = _normalize_text(candidate)
            if key:
                lookup[(type_key, key)] = item
    return lookup


def _resolve_sql_bom_row(row: dict, lookup: dict[tuple[str, str], dict]) -> dict:
    type_key = _normalize_sql_fabric_type_code(row.get("Type") or row.get("fabric_type"))
    if not type_key:
        return {}
    candidates = [
        _extract_color_desc_from_combo(row.get("FABRIC COLOR (For piecing only)")),
        row.get("COLOR_DESC"),
        row.get("FABRIC COLOR (For piecing only)"),
        row.get("COLOR_CODE"),
    ]
    for candidate in candidates:
        key = _normalize_text(candidate)
        if not key:
            continue
        for lookup_type in _fabric_type_lookup_candidates(type_key):
            item = lookup.get((lookup_type, key))
            if item:
                return item
    return {}


def _build_sql_fabric_yy_lookup(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    lookup: dict[tuple[str, str, str], dict] = {}
    for item in rows or []:
        ppo_key = str(item.get("ppo_no") or "").strip().upper()
        type_key = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        if not ppo_key or not type_key:
            continue
        candidates = [
            item.get("combo_name"),
            _extract_color_desc_from_combo(item.get("combo_name")),
            item.get("color_code"),
        ]
        for candidate in candidates:
            key = _normalize_text(candidate)
            if key:
                lookup[(ppo_key, type_key, key)] = item
    return lookup


def _canonical_combo_color_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        token = _extract_color_token_from_combo(text) or _extract_color_desc_from_combo(text)
    elif "-" in text:
        token = text.split("-", 1)[0].strip()
    else:
        token = text
    return _normalize_text(token)


def _canonical_fabric_color_key(item: dict) -> str:
    color_code = str(item.get("color_code") or "").strip().upper()
    if color_code:
        return _normalize_text(color_code)
    return _canonical_combo_color_key(item.get("combo_name"))


def _received_alias_keys(item: dict) -> set[str]:
    keys: set[str] = set()
    combo_name = str(item.get("combo_name") or "").strip()
    color_code = str(item.get("color_code") or "").strip()
    for candidate in (
        _normalize_combo_key(combo_name),
        _canonical_combo_color_key(combo_name),
        _normalize_text(_extract_color_desc_from_combo(combo_name)),
        _normalize_text(color_code),
        _canonical_fabric_color_key(item),
    ):
        if candidate:
            keys.add(candidate)
    return keys


def _build_received_type_alias_map(raw_rows: list[dict], aligned_rows: list[dict]) -> dict[tuple[str, str, str], set[str]]:
    alias_map: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    if not raw_rows or not aligned_rows:
        return alias_map

    raw_types_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    aligned_types_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in raw_rows or []:
        ppo_key = str(row.get("ppo_no") or "").strip().upper()
        type_key = _normalize_sql_fabric_type_code(row.get("fabric_type"))
        if not ppo_key or not type_key:
            continue
        for key in _received_alias_keys(row):
            raw_types_by_key[(ppo_key, key)].add(type_key)

    for row in aligned_rows or []:
        ppo_key = str(row.get("ppo_no") or "").strip().upper()
        type_key = _normalize_sql_fabric_type_code(row.get("fabric_type"))
        if not ppo_key or not type_key:
            continue
        for key in _received_alias_keys(row):
            aligned_types_by_key[(ppo_key, key)].add(type_key)

    for raw_row, aligned_row in zip(raw_rows or [], aligned_rows or []):
        ppo_key = str(aligned_row.get("ppo_no") or raw_row.get("ppo_no") or "").strip().upper()
        source_type = _normalize_sql_fabric_type_code(raw_row.get("fabric_type"))
        target_type = _normalize_sql_fabric_type_code(aligned_row.get("fabric_type"))
        if not ppo_key or not source_type or not target_type or source_type == target_type:
            continue
        alias_keys = _received_alias_keys(raw_row) | _received_alias_keys(aligned_row)
        for alias_key in alias_keys:
            raw_types = raw_types_by_key.get((ppo_key, alias_key), set())
            aligned_types = aligned_types_by_key.get((ppo_key, alias_key), set())
            if target_type in raw_types or source_type in aligned_types:
                continue
            alias_map[(ppo_key, source_type, alias_key)].add(target_type)

    return alias_map


def _received_alias_types(
    alias_map: dict[tuple[str, str, str], set[str]],
    ppo_no: object,
    fabric_type: object,
    combo_name: object,
    color_code: object,
) -> set[str]:
    ppo_key = str(ppo_no or "").strip().upper()
    type_key = _normalize_sql_fabric_type_code(fabric_type)
    if not ppo_key or not type_key:
        return set()
    keys = {
        key
        for key in (
            _normalize_combo_key(combo_name),
            _canonical_combo_color_key(combo_name),
            _normalize_text(_extract_color_desc_from_combo(combo_name)),
            _normalize_text(color_code),
        )
        if key
    }
    aliases: set[str] = set()
    for alias_key in keys:
        aliases.update(alias_map.get((ppo_key, type_key, alias_key), set()))
    return aliases


def _canonical_bom_color_key(item: dict) -> str:
    for candidate in (
        item.get("style_color_code"),
        item.get("style_color_desc"),
        _canonical_combo_color_key(item.get("combo_name")),
        item.get("combo_name"),
    ):
        key = _normalize_text(candidate)
        if key:
            return key
    return ""


def _augment_sql_bom_rows_with_go_report(sql_bom_rows: list[dict], knit_bom_rows: list[dict]) -> list[dict]:
    merged = [dict(item) for item in (sql_bom_rows or [])]
    if not knit_bom_rows:
        return merged

    existing_keys: set[tuple[str, str, str]] = set()
    for item in merged:
        type_key = _normalize_sql_fabric_type_code(item.get("fabric_type_cd"))
        color_key = _canonical_bom_color_key(item)
        combo_key = _normalize_combo_key(item.get("combo_name"))
        if type_key and color_key:
            existing_keys.add((type_key, color_key, combo_key))

    for item in knit_bom_rows or []:
        type_key = _normalize_sql_fabric_type_code(item.get("fabric_type_hint"))
        color_key = _normalize_text(item.get("color_code") or item.get("color_desc"))
        combo_key = _normalize_combo_key(item.get("combo_name"))
        if not type_key or not color_key:
            continue
        key = (type_key, color_key, combo_key)
        if key in existing_keys:
            continue
        merged.append(
            {
                "go_no": "",
                "style_color_code": str(item.get("color_code") or "").strip(),
                "style_color_desc": str(item.get("color_desc") or "").strip(),
                "fabric_type_cd": type_key,
                "fabric_type_desc": str(item.get("gmt_part") or "").strip(),
                "combo_name": str(item.get("combo_name") or "").strip(),
                "yy": _to_float(item.get("ppo_yy")),
                "marker_yy": _to_float(item.get("ppo_marker_yy")),
            }
        )
        existing_keys.add(key)
    return merged


def _build_ppo_detail_type_count_by_color(rows_by_ppo: dict[str, list[dict]]) -> dict[tuple[str, str], int]:
    buckets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ppo_no, rows in (rows_by_ppo or {}).items():
        ppo_key = str(ppo_no or "").strip().upper()
        if not ppo_key:
            continue
        for row in rows or []:
            fabric_type = _normalize_sql_fabric_type_code(row.get("fabric_type"))
            color_key = _canonical_fabric_color_key(
                {
                    "color_code": row.get("color_code"),
                    "combo_name": row.get("fabric_combo"),
                }
            )
            if fabric_type and color_key:
                buckets[(ppo_key, color_key)].add(fabric_type)
    return {key: len(types) for key, types in buckets.items()}


def _build_existing_fabric_type_count_by_color(rows: list[dict]) -> dict[tuple[str, str], int]:
    buckets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows or []:
        ppo_key = str(row.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(row.get("fabric_type"))
        color_key = _canonical_fabric_color_key(row)
        if ppo_key and fabric_type and color_key:
            buckets[(ppo_key, color_key)].add(fabric_type)
    return {key: len(types) for key, types in buckets.items()}


def _realign_fabric_rows_to_sql_bom(fabric_rows: list[dict], sql_bom_rows: list[dict]) -> list[dict]:
    if not fabric_rows or not sql_bom_rows:
        return list(fabric_rows or [])

    bom_by_color: dict[str, list[dict]] = defaultdict(list)
    for bom in sql_bom_rows or []:
        color_key = _canonical_bom_color_key(bom)
        if color_key:
            bom_by_color[color_key].append(bom)

    realigned: list[dict] = []
    for row in fabric_rows or []:
        adjusted = dict(row)
        adjusted["fabric_type"] = _normalize_sql_fabric_type_code(adjusted.get("fabric_type"))
        color_key = _canonical_fabric_color_key(adjusted)
        candidates = bom_by_color.get(color_key, [])
        if not candidates:
            realigned.append(adjusted)
            continue

        row_type = str(adjusted.get("fabric_type") or "").strip().upper()
        row_ppo_yy = _to_float(adjusted.get("ppo_yy"))
        row_marker_yy = _to_float(adjusted.get("marker_yy"))
        candidate_types = {
            _normalize_sql_fabric_type_code(item.get("fabric_type_cd"))
            for item in candidates
            if _normalize_sql_fabric_type_code(item.get("fabric_type_cd"))
        }
        remark_type = _fabric_type_hint_from_remark(adjusted.get("remarks"))
        if (
            row_type in {"M1", "M2", "M3", "R", "I"}
            and row_type not in candidate_types
            and remark_type in candidate_types
            and remark_type != row_type
        ):
            adjusted["fabric_type"] = remark_type
            realigned.append(adjusted)
            continue

        def _score(bom: dict) -> tuple[float, int, str]:
            bom_type = _normalize_sql_fabric_type_code(bom.get("fabric_type_cd"))
            diffs: list[float] = []
            bom_yy = _to_float(bom.get("yy"))
            bom_marker_yy = _to_float(bom.get("marker_yy"))
            if row_ppo_yy > 0 and bom_yy > 0:
                diffs.append(abs(row_ppo_yy - bom_yy))
            if row_marker_yy > 0 and bom_marker_yy > 0:
                diffs.append(abs(row_marker_yy - bom_marker_yy))
            diff = min(diffs) if diffs else 999.0
            return (diff, 0 if bom_type == row_type else 1, bom_type)

        best_bom = min(candidates, key=_score)
        best_type = _normalize_sql_fabric_type_code(best_bom.get("fabric_type_cd"))
        best_diff = _score(best_bom)[0]
        current_diff = min((_score(item)[0] for item in candidates if _normalize_sql_fabric_type_code(item.get("fabric_type_cd")) == row_type), default=999.0)
        if best_type and best_type != row_type and (row_type not in candidate_types or best_diff <= 0.03 or best_diff + 0.03 < current_diff):
            adjusted["fabric_type"] = best_type
        realigned.append(adjusted)
    return realigned


def _build_fabric_rows_from_sql_fallback(
    ppo_mapping: list[dict],
    sql_bom_rows: list[dict],
    jo_color_qty_rows: list[dict],
    jo_ppo_yy_rows: list[dict],
    ppo_with_detail_rows: set[str] | None = None,
    existing_fabric_rows: list[dict] | None = None,
    ppo_detail_valid_keys: set[tuple[str, str, str]] | None = None,
    ppo_detail_type_count_by_color: dict[tuple[str, str], int] | None = None,
) -> list[dict]:
    if not ppo_mapping or not sql_bom_rows or not jo_color_qty_rows:
        return []

    ppo_lots: dict[str, set[int]] = defaultdict(set)
    ppo_has_global = set()
    for item in ppo_mapping:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no"))
        if not ppo_no:
            continue
        if lot_no <= 0:
            ppo_has_global.add(ppo_no)
            continue
        ppo_lots[ppo_no].add(lot_no)

    color_rows: dict[str, list[dict]] = defaultdict(list)
    for item in jo_color_qty_rows:
        candidates = [
            item.get("color_code"),
            item.get("color_desc"),
        ]
        for candidate in candidates:
            key = _normalize_text(candidate)
            if key:
                color_rows[key].append(item)

    jo_ppo_yy_lookup: dict[tuple[str, str], float] = {}
    jo_has_positive_family_yy: set[tuple[str, str]] = set()
    for item in jo_ppo_yy_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        if not ppo_no or not jo_no:
            continue
        yy = _to_float(item.get("ppo_yy"))
        _set_max_jo_ppo_yy(jo_ppo_yy_lookup, ppo_no, jo_no, yy)
        if yy > 0:
            jo_has_positive_family_yy.add((_ppo_family_prefix(ppo_no), jo_no))

    existing_type_count_by_color = _build_existing_fabric_type_count_by_color(existing_fabric_rows or [])
    results: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    ppo_list = sorted({str(item.get("ppo_no") or "").strip().upper() for item in ppo_mapping if str(item.get("ppo_no") or "").strip()})
    for ppo_no in ppo_list:
        allowed_lots = ppo_lots.get(ppo_no, set())
        global_map = (ppo_no in ppo_has_global) and not allowed_lots
        for bom in sql_bom_rows:
            fabric_type = _normalize_sql_fabric_type_code(bom.get("fabric_type_cd"))
            style_color_code = str(bom.get("style_color_code") or "").strip()
            combo_name = str(bom.get("combo_name") or "").strip()
            color_key = _canonical_bom_color_key(bom)
            bom_row_keys = _sheet_row_ppo_color_keys(
                bom.get("style_color_code"),
                bom.get("style_color_desc"),
                bom.get("combo_name"),
            )
            if ppo_no in (ppo_with_detail_rows or set()):
                if (
                    fabric_type.startswith("M")
                    and ppo_detail_valid_keys
                    and not any((ppo_no, fabric_type, color_key) in ppo_detail_valid_keys for color_key in bom_row_keys)
                ):
                    continue
                # PPO detail can miss trim/reused fabric components that still exist
                # in GO BOM and JO/PPO YY. Do not let that source hide BOM rows.
                detail_type_count = int((ppo_detail_type_count_by_color or {}).get((ppo_no, color_key)) or 0)
                existing_type_count = int(existing_type_count_by_color.get((ppo_no, color_key)) or 0)
                if detail_type_count > 0 and existing_type_count >= detail_type_count:
                    continue

            color_candidates = [
                bom.get("style_color_code"),
                bom.get("style_color_desc"),
                bom.get("combo_name"),
            ]
            matched_rows: list[dict] = []
            for candidate in color_candidates:
                key = _normalize_text(candidate)
                if not key:
                    continue
                for row in color_rows.get(key, []):
                    lot_no = _to_int(row.get("lot_no"))
                    if global_map or not allowed_lots or lot_no in allowed_lots:
                        matched_rows.append(row)
                if matched_rows:
                    break
            if not matched_rows:
                continue

            raw_jo_list = sorted(
                {
                    str(item.get("jo_no") or "").strip().upper()
                    for item in matched_rows
                    if str(item.get("jo_no") or "").strip()
                }
            )
            jo_list = []
            for jo_no in raw_jo_list:
                key = (ppo_no, jo_no)
                family_key = (_ppo_family_prefix(ppo_no), jo_no)
                if key not in jo_ppo_yy_lookup and family_key in jo_has_positive_family_yy:
                    continue
                jo_list.append(jo_no)
            if not jo_list:
                continue

            row_key = (ppo_no, fabric_type, _normalize_text(combo_name or style_color_code))
            if row_key in seen:
                continue
            seen.add(row_key)
            results.append(
                {
                    "lot_no": 0,
                    "ppo_no": ppo_no,
                    "fabric_type": fabric_type,
                    "color_code": style_color_code,
                    "combo_name": combo_name,
                    "ppo_yy": _to_float(bom.get("yy")),
                    "marker_yy": _to_float(bom.get("marker_yy")),
                    "related_jo_list": ",".join(jo_list),
                    "remarks": "",
                }
            )
    results.sort(
        key=lambda item: (
            str(item.get("ppo_no") or ""),
            str(item.get("fabric_type") or ""),
            str(item.get("color_code") or ""),
        )
    )
    return results


def _merge_fabric_rows_with_sql_fallback(
    fabric_rows: list[dict],
    ppo_mapping: list[dict],
    sql_bom_rows: list[dict],
    jo_color_qty_rows: list[dict],
    jo_ppo_yy_rows: list[dict],
    ppo_with_detail_rows: set[str] | None = None,
    ppo_detail_valid_keys: set[tuple[str, str, str]] | None = None,
    ppo_detail_type_count_by_color: dict[tuple[str, str], int] | None = None,
) -> list[dict]:
    aligned_fabric_rows = _realign_fabric_rows_to_sql_bom(fabric_rows, sql_bom_rows)
    fallback_rows = _build_fabric_rows_from_sql_fallback(
        ppo_mapping,
        sql_bom_rows,
        jo_color_qty_rows,
        jo_ppo_yy_rows,
        ppo_with_detail_rows,
        existing_fabric_rows=aligned_fabric_rows,
        ppo_detail_valid_keys=ppo_detail_valid_keys,
        ppo_detail_type_count_by_color=ppo_detail_type_count_by_color,
    )
    if not aligned_fabric_rows:
        return fallback_rows
    if not fallback_rows:
        return _expand_multicolor_fabric_rows(list(aligned_fabric_rows))

    merged: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def _canonical_color_key(item: dict) -> str:
        return _canonical_fabric_color_key(item)

    def _row_key(item: dict) -> tuple[str, str, str]:
        return (
            str(item.get("ppo_no") or "").strip().upper(),
            str(item.get("fabric_type") or "").strip().upper(),
            _canonical_color_key(item),
        )

    for item in aligned_fabric_rows:
        key = _row_key(item)
        seen.add(key)
        merged.append(item)

    for item in fallback_rows:
        key = _row_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    merged.sort(
        key=lambda item: (
            str(item.get("ppo_no") or ""),
            str(item.get("fabric_type") or ""),
            str(item.get("color_code") or ""),
        )
    )
    return _expand_multicolor_fabric_rows(merged)


def _resolve_sql_fabric_yy_row(row: dict, lookup: dict[tuple[str, str, str], dict]) -> dict:
    ppo_key = str(row.get("PPO") or row.get("ppo_no") or "").strip().upper()
    type_key = str(row.get("Type") or row.get("fabric_type") or "").strip().upper()
    if not ppo_key or not type_key:
        return {}
    candidates = [
        row.get("FABRIC COLOR (For piecing only)"),
        _extract_color_desc_from_combo(row.get("FABRIC COLOR (For piecing only)")),
        row.get("COLOR_DESC"),
        row.get("COLOR_CODE"),
    ]
    for candidate in candidates:
        key = _normalize_text(candidate)
        if not key:
            continue
        for lookup_type in _fabric_type_lookup_candidates(type_key):
            item = lookup.get((ppo_key, lookup_type, key))
            if item:
                return item
    return {}


def _build_go_report_remark_lookup(rows: list[dict]) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for item in rows or []:
        type_key = str(item.get("fabric_type_hint") or "").strip().upper()
        remark = str(item.get("component_part") or "").strip()
        if not type_key or not remark:
            continue
        candidates = [
            item.get("combo_name"),
            item.get("color_desc"),
            item.get("color_code"),
        ]
        for candidate in candidates:
            key = _normalize_text(candidate)
            if key and (type_key, key) not in lookup:
                lookup[(type_key, key)] = remark
    return lookup


def _resolve_go_report_remark(row: dict, lookup: dict[tuple[str, str], str]) -> str:
    type_key = str(row.get("Type") or row.get("fabric_type") or "").strip().upper()
    if not type_key:
        return ""
    candidates = [
        _extract_color_desc_from_combo(row.get("FABRIC COLOR (For piecing only)")),
        row.get("COLOR_DESC"),
        row.get("FABRIC COLOR (For piecing only)"),
        row.get("COLOR_CODE"),
    ]
    for candidate in candidates:
        key = _normalize_text(candidate)
        if not key:
            continue
        remark = lookup.get((type_key, key))
        if remark:
            return remark
    return ""


def _sanitize_sheet_remark(saved_remark: object, auto_candidates: list[object] | None = None) -> str:
    remark = str(saved_remark or "").strip()
    if not remark:
        return ""
    normalized_remark = _normalize_text(remark)
    if not normalized_remark:
        return ""
    for candidate in auto_candidates or []:
        if normalized_remark == _normalize_text(candidate):
            return ""
    return remark


def _build_go_report_color_breakdown_lookup(
    rows: list[dict],
) -> tuple[dict[int, dict[str, dict[str, dict]]], dict[int, set[str]]]:
    lookup: dict[int, dict[str, dict[str, dict]]] = {}
    colors_by_lot: dict[int, set[str]] = defaultdict(set)
    for item in rows or []:
        lot_no = _to_int(item.get("lot"))
        if lot_no <= 0:
            continue
        bucket = lookup.setdefault(
            lot_no,
            {
                "color_code": {},
                "cust_color_code": {},
                "gmt_color_desc": {},
                "color_desc": {},
                "cust_color_desc": {},
            },
        )
        for code_key in _color_code_lookup_keys(item.get("gmt_color_code") or item.get("color_code")):
            bucket["color_code"].setdefault(code_key, item)
            colors_by_lot[lot_no].add(code_key)
        for code_key in _color_code_lookup_keys(item.get("cust_color_code")):
            bucket["cust_color_code"].setdefault(code_key, item)
        for field in ("gmt_color_desc", "color_desc", "cust_color_desc"):
            key = _normalize_text(item.get(field))
            if not key:
                continue
            bucket[field].setdefault(key, item)
            colors_by_lot[lot_no].add(key)
    return lookup, colors_by_lot


def _match_go_report_color_breakdown_row(
    lookup: dict[int, dict[str, dict[str, dict]]],
    lot_no: int,
    color_code: object,
    color_desc: object,
    combo_name: object,
) -> dict:
    if lot_no <= 0:
        return {}
    bucket = lookup.get(lot_no) or {}
    desc_candidates: list[str] = []
    for candidate in (_extract_color_desc_from_combo(combo_name), color_desc, combo_name):
        key = _normalize_text(candidate)
        if key and key not in desc_candidates:
            desc_candidates.append(key)
    code_candidates = _color_code_lookup_keys(color_code)

    def _desc_matches(item: dict) -> bool:
        if not desc_candidates:
            return True
        item_desc_keys = {
            _normalize_text(item.get("gmt_color_desc")),
            _normalize_text(item.get("color_desc")),
            _normalize_text(item.get("cust_color_desc")),
        }
        item_desc_keys.discard("")
        return bool(item_desc_keys.intersection(desc_candidates))

    if code_candidates:
        for code_key in code_candidates:
            item = (bucket.get("color_code") or {}).get(code_key)
            if item:
                return item

    for field in ("gmt_color_desc", "color_desc", "cust_color_desc"):
        for desc_key in desc_candidates:
            item = (bucket.get(field) or {}).get(desc_key)
            if item:
                return item

    if code_candidates and not desc_candidates:
        for code_key in code_candidates:
            item = (bucket.get("color_code") or {}).get(code_key)
            if item:
                return item

    if code_candidates:
        for code_key in code_candidates:
            item = (bucket.get("cust_color_code") or {}).get(code_key)
            if item:
                return item
    return {}


def _build_go_report_allowed_lot_nos(go_report: dict | None) -> set[int]:
    allowed: set[int] = set()
    report = go_report or {}
    for item in report.get("lot_rows") or []:
        lot_no = _to_int(item.get("lot"))
        if lot_no > 0:
            allowed.add(lot_no)
    for item in report.get("color_breakdown_rows") or []:
        lot_no = _to_int(item.get("lot"))
        if lot_no > 0:
            allowed.add(lot_no)
    return allowed


def _build_lot_color_fallback_rows(
    go_key: str,
    jo_color_qty_rows: list[dict],
    ppo_mapping: list[dict],
    go_report: dict | None,
    color_desc_map: dict[str, str],
    lot_rows: list[dict] | None = None,
) -> list[dict]:
    if not jo_color_qty_rows:
        return []

    report = go_report or {}
    report_ppo_by_jo: dict[str, dict] = {}
    report_ppo_by_lot: dict[int, dict] = {}
    lot_info_by_jo: dict[str, dict] = {}
    lot_info_by_lot: dict[int, dict] = {}
    sql_ppo_by_lot: dict[int, str] = {}
    sql_global_ppo = ""

    for item in lot_rows or []:
        lot_no = _to_int(item.get("lot_no") or item.get("lot"))
        jo_no = str(item.get("jo_no") or item.get("job_order_no") or "").strip().upper()
        if lot_no > 0:
            lot_info_by_lot[lot_no] = dict(item)
        if jo_no:
            lot_info_by_jo[jo_no] = dict(item)

    # New GOs often expose the lot/JO and shipment allowance before PPO
    # mapping exists. Preserve that data in fallback rows.
    for item in report.get("lot_rows") or []:
        lot_no = _to_int(item.get("lot"))
        jo_no = str(item.get("job_order_no") or "").strip().upper()
        normalized_item = {
            "lot_no": lot_no,
            "jo_no": jo_no,
            "qty": item.get("qty"),
            "buyer_po_del_date": item.get("buyer_po_del_date"),
            "short_pct": item.get("minus_pct"),
            "over_pct": item.get("plus_pct"),
        }
        if lot_no > 0:
            lot_info_by_lot[lot_no] = {**lot_info_by_lot.get(lot_no, {}), **normalized_item}
        if jo_no:
            lot_info_by_jo[jo_no] = {**lot_info_by_jo.get(jo_no, {}), **normalized_item}

    for item in ppo_mapping or []:
        ppo_no = str(item.get("ppo_no") or item.get("ppo") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no") or item.get("lot"))
        if not ppo_no:
            continue
        if lot_no > 0:
            sql_ppo_by_lot.setdefault(lot_no, ppo_no)
        elif not sql_global_ppo:
            sql_global_ppo = ppo_no

    for item in report.get("ppo_mapping") or []:
        jo_no = str(item.get("job_order_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot"))
        if jo_no and jo_no not in report_ppo_by_jo:
            report_ppo_by_jo[jo_no] = item
        if lot_no > 0 and lot_no not in report_ppo_by_lot:
            report_ppo_by_lot[lot_no] = item

    output_rows: list[dict] = []
    seen: set[tuple[int, str, str, str, str]] = set()
    for item in jo_color_qty_rows:
        lot_no = _to_int(item.get("lot_no"))
        jo_no = str(item.get("jo_no") or "").strip().upper()
        report_row = report_ppo_by_jo.get(jo_no) if jo_no else {}
        if not report_row:
            report_row = report_ppo_by_lot.get(lot_no) or {}
        lot_info = lot_info_by_jo.get(jo_no) if jo_no else {}
        if not lot_info:
            lot_info = lot_info_by_lot.get(lot_no) or {}
        if not jo_no:
            jo_no = str(
                report_row.get("job_order_no")
                or lot_info.get("jo_no")
                or lot_info.get("job_order_no")
                or ""
            ).strip().upper()
        # Color-size sales can contain an orphan LOT with no JO (for example a
        # stale/partial transaction).  It is not a valid GO shipment row and
        # must not be surfaced as a fake blank-JO order.
        if not jo_no:
            continue

        color_code_raw = str(item.get("color_code") or "").strip()
        color_code = color_code_raw.lstrip("0") or "0"
        color_desc = str(item.get("color_desc") or "").strip() or color_desc_map.get(color_code, "")
        ppo_no = (
            str(report_row.get("ppo") or "").strip().upper()
            or sql_ppo_by_lot.get(lot_no, "")
            or sql_global_ppo
        )
        row_key = (lot_no, jo_no, ppo_no, color_code_raw.upper(), color_desc.upper())
        if row_key in seen:
            continue
        seen.add(row_key)

        qty = _to_float(item.get("qty")) or _to_float(lot_info.get("qty"))
        short_pct = _resolve_allowance_pct(
            report_row.get("minus_pct"),
            lot_info.get("short_pct"),
            lot_info.get("minus_pct"),
        )
        over_pct = _resolve_allowance_pct(
            report_row.get("plus_pct"),
            lot_info.get("over_pct"),
            lot_info.get("plus_pct"),
        )
        buyer_po_del_date = (
            item.get("buyer_po_del_date")
            or report_row.get("buyer_po_del_date")
            or lot_info.get("buyer_po_del_date")
        )
        output_rows.append(
            {
                "GO": go_key,
                "PPO": ppo_no,
                "Lot": lot_no,
                "JO": jo_no,
                "Type": "B",
                "COLOR_CODE": color_code_raw,
                "COLOR_DESC": color_desc,
                "FABRIC_COMBO": color_desc,
                "Qty": qty,
                "Buyer_PO_Del_Date": buyer_po_del_date,
                "Marker_YY": 0.0,
                "PPO_YY": 0.0,
                "Require_Qty_Yds": 0.0,
                "Plan_Allocate_Qty_Yds": 0.0,
                "Rcv_Qty_PPO": 0.0,
                "FOC_Qty": 0.0,
                "On_Hand_Qty": 0.0,
                "On_Hand_Pct": 0.0,
                "Shortage_Qty": 0.0,
                "Surplus_Qty": 0.0,
                "Allow_Short_Pct": short_pct,
                "Allow_Over_Pct": over_pct,
                "Alert": "",
                "Automation_Action": "",
                "Remark": "",
            }
        )

    output_rows.sort(
        key=lambda row: (
            _to_int(row.get("Lot")),
            str(row.get("PPO") or ""),
            str(row.get("COLOR_CODE") or ""),
            str(row.get("JO") or ""),
        )
    )
    return output_rows


def build_live_coi(
    go: str,
    prefer_mes_cache: bool = True,
    allow_live_mes: bool = False,
    allow_live_go_report: bool = False,
    allow_slow_sql_enrichment: bool = False,
    prefer_source_cache: bool = False,
    source_bundle: dict | None = None,
) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")

    if not isinstance(source_bundle, dict):
        source_bundle = _source_bundle_for_request(
            go_key,
            include_ppo_detail=allow_slow_sql_enrichment,
            prefer_source_cache=prefer_source_cache,
        )
    if not source_bundle.get("ok"):
        source_error = source_bundle.get("error") or source_bundle.get("detail") or ""
        classification = classify_source_error(source_error)
        return _error(
            "Cannot build COI from required source data",
            detail=classification["message"],
            source_error_code=classification["code"],
            go=go_key,
        )

    go_head = dict(source_bundle.get("head") or {})
    if _is_ignored_customer_code(go_head.get("customer_code")):
        return _error("GO excluded by customer rule", go=go_key, customer_code=go_head.get("customer_code"))
    go_factory = str(go_head.get("factory_code") or "").strip().upper()
    if go_factory not in _ALLOWED_FACTORIES:
        return _error(
            "Factory not supported for live COI",
            go=go_key,
            factory_code=go_factory,
            allowed_factories=list(_ALLOWED_FACTORIES),
        )

    colors = list(source_bundle.get("colors") or [])
    lots = list(source_bundle.get("lots") or [])
    jo_color_qty_rows = list(source_bundle.get("jo_color_qty_rows") or [])
    ppo_mapping = list(source_bundle.get("ppo_mapping") or [])
    fabric_rows = list(source_bundle.get("fabric_rows") or [])
    raw_fabric_rows = list(fabric_rows)
    sql_bom_rows = list(source_bundle.get("sql_bom_rows") or [])
    jo_ppo_yy_rows = list(source_bundle.get("jo_ppo_yy_rows") or [])
    received_rows = list(source_bundle.get("received_rows") or [])
    received_view = str(source_bundle.get("received_view") or "")
    ppo_detail_rows_by_ppo = dict(source_bundle.get("ppo_detail_rows_by_ppo") or {})
    ppo_with_detail_rows = {
        str(ppo_no or "").strip().upper()
        for ppo_no, rows in ppo_detail_rows_by_ppo.items()
        if str(ppo_no or "").strip() and rows
    }

    go_report = _fetch_go_report_detail(go_key, allow_live_fetch=allow_live_go_report)
    if (
        not go_report.get("ok")
        and jo_color_qty_rows
        and (not ppo_mapping or not fabric_rows or not sql_bom_rows)
    ):
        go_report = _fetch_go_report_detail(go_key, allow_live_fetch=True)
    sql_bom_rows = _augment_sql_bom_rows_with_go_report(
        sql_bom_rows,
        (go_report.get("knit_bom_rows") or []) if go_report.get("ok") else [],
    )
    ppo_detail_valid_keys, _unused_family_candidates, _unused_family_types = _build_ppo_detail_assignment_maps(
        ppo_detail_rows_by_ppo
    )
    ppo_detail_yy_lookup = _build_ppo_detail_yy_lookup(ppo_detail_rows_by_ppo)
    ppo_detail_type_count_by_color = _build_ppo_detail_type_count_by_color(ppo_detail_rows_by_ppo)
    aligned_sql_fabric_rows = _realign_fabric_rows_to_sql_bom(raw_fabric_rows, sql_bom_rows)
    received_type_alias_map = _build_received_type_alias_map(raw_fabric_rows, aligned_sql_fabric_rows)
    fabric_rows = _merge_fabric_rows_with_sql_fallback(
        raw_fabric_rows,
        ppo_mapping,
        sql_bom_rows,
        jo_color_qty_rows,
        jo_ppo_yy_rows,
        ppo_with_detail_rows,
        ppo_detail_valid_keys,
        ppo_detail_type_count_by_color,
    )
    if go_report.get("ok"):
        lots = _merge_sql_lots_with_go_report(lots, go_report)
        if not any(_to_int(item.get("lot_no")) > 0 for item in ppo_mapping):
            ppo_mapping = _build_go_report_ppo_mapping(go_report)
        elif not lots:
            ppo_mapping = _build_go_report_ppo_mapping(go_report) or ppo_mapping

    cutting_payload = (
        _load_cutting_payload(go_key, prefer_cache=prefer_mes_cache, allow_live_query=allow_live_mes)
        if allow_live_mes
        else {"summary": [], "jo_details": [], "source_label": "SQL only", "error": ""}
    )

    color_desc_map = {
        str(item.get("color_code") or "").strip().lstrip("0") or "0": str(item.get("color_desc") or "").strip()
        for item in colors
    }
    go_report_color_lookup, go_report_colors_by_lot = _build_go_report_color_breakdown_lookup(
        (go_report.get("color_breakdown_rows") or []) if go_report.get("ok") else []
    )
    fallback_output_rows = _build_lot_color_fallback_rows(
        go_key,
        jo_color_qty_rows,
        ppo_mapping,
        go_report,
        color_desc_map,
        lots,
    )
    output_rows = []
    total_require = 0.0
    total_on_hand = 0.0
    total_shortage = 0.0
    total_surplus = 0.0
    short_count = 0
    over_count = 0

    if not fabric_rows and fallback_output_rows:
        output_rows = list(fallback_output_rows)
        return {
            "ok": True,
            "go": go_key,
            "head": go_head,
            "factory_code": go_factory,
            "rows": output_rows,
            "row_count": len(output_rows),
            "received_view": received_view,
            "jo_color_qty_rows": jo_color_qty_rows,
            "sql_source": {
                "foc_view": received_view,
                "fabric_view": "escmowner.V_GO_Fabric_Infor_ALL",
                "lot_view": "dbo.V_GO_BPO_HD_JO_ALL",
                "mapping_view": "dbo.V_GO_PPO_Mapping",
                "yy_view": "dbo.V_JO_PPO_YY",
                "mode": source_bundle.get("source_mode", ""),
                "synced_at": source_bundle.get("source_synced_at", ""),
                "live_error": source_bundle.get("source_live_error", ""),
            },
            "summary": {
                "total_require_qty": 0.0,
                "total_on_hand_qty": 0.0,
                "total_shortage_qty": 0.0,
                "total_surplus_qty": 0.0,
                "coverage_pct": 0.0,
                "short_alert_rows": 0,
                "over_alert_rows": 0,
                "ok_rows": len(output_rows),
            },
            "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "sql_enrichment_skipped": not allow_slow_sql_enrichment,
        }

    jo_color_qty_lookup: dict[tuple[int, str, str], dict] = {}
    jo_color_qty_allowed: dict[tuple[int, str], set[str]] = defaultdict(set)
    for item in jo_color_qty_rows:
        lot_no = _to_int(item.get("lot_no"))
        jo_no = str(item.get("jo_no") or "").strip().upper()
        if lot_no <= 0 or not jo_no:
            continue
        candidates = [*_color_code_lookup_keys(item.get("color_code")), _normalize_text(item.get("color_desc"))]
        for candidate in candidates:
            color_key = str(candidate or "").strip().upper()
            if not color_key:
                continue
            jo_color_qty_lookup.setdefault((lot_no, jo_no, color_key), item)
            jo_color_qty_allowed[(lot_no, jo_no)].add(color_key)

    lot_rows_by_lot: dict[int, list[dict]] = defaultdict(list)
    lot_rows_by_jo: dict[str, list[dict]] = defaultdict(list)
    for lot in lots:
        lot_no = _to_int(lot.get("lot_no"))
        jo_no = str(lot.get("jo_no") or "").strip().upper()
        lot_rows_by_lot[lot_no].append(lot)
        if jo_no:
            lot_rows_by_jo[jo_no].append(lot)

    jo_ppo_yy_lookup: dict[tuple[str, str], float] = {}
    jo_has_positive_family_yy: set[tuple[str, str]] = set()
    for item in jo_ppo_yy_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        yy = _to_float(item.get("ppo_yy"))
        _set_max_jo_ppo_yy(jo_ppo_yy_lookup, ppo_no, jo_no, yy)
        if ppo_no and jo_no and yy > 0:
            jo_has_positive_family_yy.add((_ppo_family_prefix(ppo_no), jo_no))

    cutting_color_to_jos: dict[str, set[str]] = defaultdict(set)
    for item in cutting_payload.get("jo_details") or []:
        jo_no = str(item.get("JO") or "").strip().upper()
        color_raw = str(item.get("Color") or "").strip()
        if not jo_no or not color_raw:
            continue
        if color_raw.isdigit():
            cutting_color_to_jos[color_raw.lstrip("0") or "0"].add(jo_no)
        else:
            cutting_color_to_jos[color_raw.upper()].add(jo_no)

    lot_row_lookup: dict[tuple[int, str], dict] = {}
    for lot in lots:
        jo_no = str(lot.get("jo_no") or "").strip().upper()
        lot_no = _to_int(lot.get("lot_no"))
        if lot_no <= 0 or not jo_no:
            continue
        lot_row_lookup[(lot_no, jo_no)] = lot

    ppo_to_jo_rows: dict[str, list[dict]] = defaultdict(list)
    precise_best_by_family: dict[tuple[str, int, str], dict] = {}
    for item in jo_ppo_yy_rows:
        ppo = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no"))
        ppo_yy = _to_float(item.get("ppo_yy"))
        if not ppo or lot_no <= 0 or not jo_no or ppo_yy <= 0:
            continue
        match = re.match(r"^[A-Z]+", ppo)
        prefix = match.group(0) if match else ppo[:4]
        family_key = (prefix, lot_no, jo_no)
        current_best = precise_best_by_family.get(family_key)
        if current_best is None or ppo_yy > _to_float(current_best.get("ppo_yy")) or (
            ppo_yy == _to_float(current_best.get("ppo_yy")) and ppo < str(current_best.get("ppo_no") or "")
        ):
            precise_best_by_family[family_key] = {
                "ppo_no": ppo,
                "lot_no": lot_no,
                "jo_no": jo_no,
                "ppo_yy": ppo_yy,
            }

    precise_ppo_rows_added: set[tuple[str, int, str]] = set()
    for item in precise_best_by_family.values():
        ppo = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no"))
        row = lot_row_lookup.get((lot_no, jo_no))
        if not row:
            continue
        key = (ppo, lot_no, jo_no)
        if key in precise_ppo_rows_added:
            continue
        precise_ppo_rows_added.add(key)
        ppo_to_jo_rows[ppo].append(row)

    ppos_with_precise_rows = set(ppo_to_jo_rows.keys())

    for item in ppo_mapping:
        ppo = str(item.get("ppo_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no"))
        if not ppo:
            continue
        has_precise_rows = ppo in ppos_with_precise_rows
        for row in lot_rows_by_lot.get(lot_no, []):
            jo_no = str(row.get("jo_no") or "").strip().upper()
            if not jo_no:
                continue
            family_key = (_ppo_family_prefix(ppo), jo_no)
            mapped_yy = _to_float(jo_ppo_yy_lookup.get((ppo, jo_no)))
            # Keep zero-YY JO/PPO mappings alive. They are still valid mappings; they
            # just don't provide authoritative YY and must not hide a whole LOT/color.
            if mapped_yy > 0 and has_precise_rows:
                continue
            if (ppo, jo_no) not in jo_ppo_yy_lookup and family_key in jo_has_positive_family_yy:
                continue
            if any(
                _to_int(existing.get("lot_no")) == lot_no
                and str(existing.get("jo_no") or "").strip().upper() == jo_no
                for existing in ppo_to_jo_rows.get(ppo, [])
            ):
                continue
            ppo_to_jo_rows[ppo].append(row)

    received_lookup: dict[tuple[str, str, str], dict] = {}
    for item in received_rows:
        ppo = str(item.get("ppo_no") or "").strip().upper()
        ftype = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        combo = str(item.get("combo_name") or "").strip()
        combo_key = _normalize_combo_key(combo)
        color_code = _extract_color_code_from_combo(combo)
        payload = {
            "received_qty": _to_float(item.get("received_qty")),
            "foc_qty": _to_float(item.get("foc_qty")),
            "source_combo_key": combo_key,
            "source_combo_name": combo,
        }
        target_types = {ftype} | _received_alias_types(received_type_alias_map, ppo, ftype, combo, color_code)
        for target_type in {item for item in target_types if item}:
            if ppo and target_type and combo_key:
                received_lookup[(ppo, target_type, combo_key)] = payload
            for color_key in _color_lookup_keys_from_combo(combo):
                if ppo and target_type and color_key:
                    received_lookup[(ppo, target_type, color_key)] = payload

    bom_combo_by_type: dict[str, str] = {}
    for bom in sql_bom_rows or []:
        bom_type = _normalize_sql_fabric_type_code(bom.get("fabric_type_cd"))
        combo = str(bom.get("combo_name") or "").strip()
        if not bom_type or not combo:
            continue
        for type_key in _fabric_type_lookup_candidates(bom_type):
            bom_combo_by_type.setdefault(type_key, combo)

    for fabric in fabric_rows:
        lot_no = _to_int(fabric.get("lot_no"))
        ppo = str(fabric.get("ppo_no") or "").strip().upper()
        fabric_type = str(fabric.get("fabric_type") or "").strip().upper()
        color_code_raw = str(fabric.get("color_code") or "").strip()
        color_code = color_code_raw.lstrip("0") or "0"
        color_desc = color_desc_map.get(color_code, "")
        raw_combo_name = str(fabric.get("combo_name") or "").strip()
        placeholder_global_fabric = lot_no <= 0 and not ppo and not color_code_raw and not raw_combo_name
        combo_name = raw_combo_name or bom_combo_by_type.get(fabric_type, "")
        related_jo = str(fabric.get("related_jo_list") or "").strip()
        remarks = str(fabric.get("remarks") or "").strip()
        marker_yy = _to_float(fabric.get("marker_yy"))
        ppo_yy_default = _to_float(fabric.get("ppo_yy"))
        if marker_yy <= 0 or ppo_yy_default <= 0:
            detail_yy = _resolve_ppo_detail_yy_for_row(
                ppo_detail_yy_lookup,
                ppo,
                fabric_type,
                color_code_raw,
                color_desc,
                combo_name,
            )
            detail_ppo_yy = _to_float(detail_yy.get("ppo_yy")) if detail_yy else 0.0
            detail_marker_yy = _to_float(detail_yy.get("marker_yy")) if detail_yy else 0.0
            if ppo_yy_default <= 0 and detail_ppo_yy > 0:
                ppo_yy_default = detail_ppo_yy
            if marker_yy <= 0 and detail_marker_yy > 0:
                marker_yy = detail_marker_yy
        if marker_yy <= 0 and ppo_yy_default > 0:
            marker_yy = ppo_yy_default

        jo_candidates = [row for row in lot_rows_by_lot.get(lot_no, []) if str(row.get("jo_no") or "").strip()]
        precise_global_candidates = []
        if lot_no <= 0:
            precise_global_candidates = list(ppo_to_jo_rows.get(ppo, []))
            jo_candidates.extend(precise_global_candidates)

        if related_jo:
            for token in re.split(r"[,\s;/]+", related_jo):
                jo_key = token.strip().upper()
                if not jo_key:
                    continue
                jo_candidates.extend(lot_rows_by_jo.get(jo_key, []))
                if not lot_rows_by_jo.get(jo_key):
                    for full_jo, rows in lot_rows_by_jo.items():
                        if full_jo.endswith(jo_key):
                            jo_candidates.extend(rows)

        inferred_color_tokens = _fabric_color_tokens(color_code_raw, combo_name)
        if inferred_color_tokens:
            for color_token in inferred_color_tokens:
                for jo_key in sorted(cutting_color_to_jos.get(color_token, set())):
                    jo_candidates.extend(lot_rows_by_jo.get(jo_key, []))

        if not jo_candidates:
            jo_candidates = list(ppo_to_jo_rows.get(ppo, []))

        if not jo_candidates and lot_no <= 0:
            global_color_keys: set[str] = set(_color_code_lookup_keys(color_code_raw))
            for candidate in (combo_name, color_desc):
                key = _normalize_text(candidate)
                if key:
                    global_color_keys.add(key)
            if global_color_keys:
                for color_row in jo_color_qty_rows:
                    jo_no = str(color_row.get("jo_no") or "").strip().upper()
                    color_lot_no = _to_int(color_row.get("lot_no"))
                    if color_lot_no <= 0 or not jo_no:
                        continue
                    candidate_keys: set[str] = set()
                    for field in ("color_code", "color_desc"):
                        candidate_keys.update(_color_code_lookup_keys(color_row.get(field)))
                        field_key = _normalize_text(color_row.get(field))
                        if field_key:
                            candidate_keys.add(field_key)
                    if not (candidate_keys & global_color_keys):
                        continue
                    mapped_row = lot_row_lookup.get((color_lot_no, jo_no))
                    if mapped_row:
                        jo_candidates.append(mapped_row)

        if jo_candidates:
            dedup: dict[str, dict] = {}
            for row in jo_candidates:
                key = f"{_to_int(row.get('lot_no'))}-{str(row.get('jo_no') or '').strip().upper()}"
                dedup[key] = row
            jo_candidates = list(dedup.values())

        if not jo_candidates and placeholder_global_fabric:
            jo_candidates = [
                row
                for row in jo_color_qty_rows
                if _to_int(row.get("lot_no")) > 0 and str(row.get("jo_no") or "").strip()
            ]

        if not jo_candidates and go_report.get("ok"):
            report_map_rows = []
            for item in (go_report.get("ppo_mapping") or []):
                report_ppo = str(item.get("ppo") or "").strip().upper()
                report_jo = str(item.get("job_order_no") or "").strip().upper()
                report_lot = _to_int(item.get("lot"))
                if report_ppo != ppo:
                    continue
                if report_lot > 0:
                    report_map_rows.extend(lot_rows_by_lot.get(report_lot, []))
                if report_jo:
                    report_map_rows.extend(lot_rows_by_jo.get(report_jo, []))
            if report_map_rows:
                jo_candidates = report_map_rows

        if not jo_candidates:
            jo_candidates = [
                {
                    "lot_no": lot_no,
                    "jo_no": "",
                    "qty": 0,
                    "buyer_po_del_date": None,
                    "buyer_po_no": "",
                    "short_pct": 0,
                    "over_pct": 0,
                }
            ]

        rcv = _find_received_row(received_lookup, ppo, fabric_type, combo_name, color_code)
        warehouse_received_total = _display_received_qty(rcv)
        foc_total = 0.0
        available_total = warehouse_received_total

        prepared_rows: list[dict] = []
        for jo in jo_candidates:
            jo_no = str(jo.get("jo_no") or "").strip().upper()
            jo_lot_no = _to_int(jo.get("lot_no"))
            if go_report_colors_by_lot and jo_lot_no not in go_report_colors_by_lot:
                continue
            color_probe = _normalize_text(color_code_raw or combo_name or color_desc or "")
            explicit_color_keys = _color_code_lookup_keys(color_code_raw)
            lot_breakdown_row = _match_go_report_color_breakdown_row(
                go_report_color_lookup,
                jo_lot_no,
                color_code_raw,
                color_desc,
                combo_name,
            )

            allowed_breakdown_colors = go_report_colors_by_lot.get(jo_lot_no, set())
            if allowed_breakdown_colors and not lot_breakdown_row:
                continue

            jo_color_row = None
            for color_key in explicit_color_keys:
                jo_color_row = jo_color_qty_lookup.get((jo_lot_no, jo_no, color_key))
                if jo_color_row:
                    break
            if not jo_color_row:
                jo_color_row = jo_color_qty_lookup.get((jo_lot_no, jo_no, color_probe))
            if not jo_color_row and not explicit_color_keys and combo_name:
                jo_color_row = jo_color_qty_lookup.get((jo_lot_no, jo_no, _normalize_text(combo_name)))
            if not jo_color_row and not explicit_color_keys and color_desc:
                jo_color_row = jo_color_qty_lookup.get((jo_lot_no, jo_no, _normalize_text(color_desc)))
            if (
                not jo_color_row
                and not explicit_color_keys
                and (placeholder_global_fabric or not color_probe)
                and (str(jo.get("color_code") or "").strip() or str(jo.get("color_desc") or "").strip())
            ):
                jo_color_row = jo

            allowed_sql_colors = jo_color_qty_allowed.get((jo_lot_no, jo_no), set())
            if allowed_sql_colors and not jo_color_row and not placeholder_global_fabric:
                continue

            qty = _to_float((lot_breakdown_row or {}).get("qty")) or _to_float((jo_color_row or {}).get("qty")) or _to_float(jo.get("qty"))
            buyer_po_del_date = (jo_color_row or {}).get("buyer_po_del_date") or jo.get("buyer_po_del_date")
            short_pct = _resolve_allowance_pct(jo.get("short_pct"))
            over_pct = _resolve_allowance_pct(jo.get("over_pct"))
            # V_JO_PPO_YY has no fabric type/color column. It is useful for PPO/JO
            # presence checks, but assigning it directly here can put a B/R/M average
            # onto every fabric row. Keep the type-specific YY from fabric/BOM rows;
            # the final sheet can infer one missing component only when unambiguous.
            ppo_yy = ppo_yy_default
            require_qty = _required_qty_from_ppo_yy(
                qty,
                ppo_yy,
                fallback_yy=marker_yy,
                allow_flatknit_fallback=fabric_type in _FLATKNIT_SIZE_TYPES,
            )
            resolved_color_code = (
                str((jo_color_row or {}).get("color_code") or "").strip()
                or str((lot_breakdown_row or {}).get("color_code") or "").strip()
                or str((lot_breakdown_row or {}).get("cust_color_code") or "").strip()
                or color_code_raw
            )
            resolved_color_desc = (
                str((jo_color_row or {}).get("color_desc") or "").strip()
                or str((lot_breakdown_row or {}).get("color_desc") or "").strip()
                or str((lot_breakdown_row or {}).get("cust_color_desc") or "").strip()
                or color_desc
            )
            prepared_rows.append(
                {
                    "jo": jo,
                    "jo_no": jo_no,
                    "qty": qty,
                    "buyer_po_del_date": buyer_po_del_date,
                    "short_pct": short_pct,
                    "over_pct": over_pct,
                    "ppo_yy": ppo_yy,
                    "require_qty": require_qty,
                    "planned_allocate": require_qty * (1.0 + over_pct / 100.0),
                    "distribution_basis": require_qty if require_qty > 0 else qty,
                    "color_code": resolved_color_code,
                    "color_desc": resolved_color_desc,
                }
            )

        if not prepared_rows:
            continue

        basis_total = sum(max(_to_float(item.get("distribution_basis")), 0.0) for item in prepared_rows)
        if basis_total <= 0 and prepared_rows:
            basis_total = float(len(prepared_rows))
            for item in prepared_rows:
                item["distribution_basis"] = 1.0

        available_remaining = available_total
        foc_remaining = 0.0

        for index, item in enumerate(prepared_rows):
            jo = item["jo"]
            jo_lot_no = _to_int(jo.get("lot_no"))
            jo_no = str(item.get("jo_no") or "")
            qty = _to_float(item.get("qty"))
            short_pct = _resolve_allowance_pct(item.get("short_pct"))
            over_pct = _resolve_allowance_pct(item.get("over_pct"))
            ppo_yy = _to_float(item.get("ppo_yy"))
            require_qty = _to_float(item.get("require_qty"))
            planned_allocate = _to_float(item.get("planned_allocate"))

            is_last = index == (len(prepared_rows) - 1)
            if is_last:
                received_qty = available_remaining
                foc_qty = foc_remaining
            else:
                share = (_to_float(item.get("distribution_basis")) / basis_total) if basis_total > 0 else 0.0
                received_qty = available_total * share
                foc_qty = 0.0
                available_remaining -= received_qty
                foc_remaining -= foc_qty

            on_hand_qty = max(received_qty, 0.0)
            on_hand_pct = (on_hand_qty / require_qty) if require_qty > 0 else 0.0
            shortage_qty = max(require_qty - on_hand_qty, 0.0)
            surplus_qty = max(on_hand_qty - require_qty, 0.0)

            if shortage_qty > 0.0001:
                alert = "SHORT"
                action = "Auto: open purchase / transfer request"
                short_count += 1
            elif surplus_qty > max(50.0, require_qty * 0.1):
                alert = "OVER"
                action = "Auto: candidate write-off / re-allocation"
                over_count += 1
            else:
                alert = "OK"
                action = "Auto: no action"

            total_require += require_qty
            total_on_hand += on_hand_qty
            total_shortage += shortage_qty
            total_surplus += surplus_qty

            output_rows.append(
                {
                    "GO": go_key,
                    "PPO": ppo,
                    "Lot": jo_lot_no if jo_lot_no > 0 else lot_no,
                    "JO": jo_no,
                    "Type": fabric_type,
                    "COLOR_CODE": str(item.get("color_code") or color_code_raw),
                    "COLOR_DESC": str(item.get("color_desc") or color_desc),
                    "FABRIC_COMBO": combo_name or str(item.get("color_desc") or color_desc or item.get("color_code") or ""),
                    "Qty": round(qty, 2),
                    "Buyer_PO_Del_Date": item.get("buyer_po_del_date"),
                    "Marker_YY": round(marker_yy, 6),
                    "PPO_YY": round(ppo_yy, 6),
                    "Require_Qty_Yds": round(require_qty, 3),
                    "Plan_Allocate_Qty_Yds": round(planned_allocate, 3),
                    "Rcv_Qty_PPO": round(received_qty, 3),
                    "FOC_Qty": round(foc_qty, 3),
                    "On_Hand_Qty": round(on_hand_qty, 3),
                    "On_Hand_Pct": round(on_hand_pct, 4),
                    "Shortage_Qty": round(shortage_qty, 3),
                    "Surplus_Qty": round(surplus_qty, 3),
                    "Allow_Short_Pct": short_pct,
                    "Allow_Over_Pct": over_pct,
                    "Alert": alert,
                    "Automation_Action": action,
                    "Remark": "",
                }
            )

    # Remove orphan fabric rows that have no JO and whose LOT is not present in
    # the authoritative GO lot header.  These are stale color-size records,
    # not shipment rows (e.g. the orphan LOT 25 seen for this GO).
    valid_lot_nos = {_to_int(item.get("lot_no")) for item in lots if _to_int(item.get("lot_no")) > 0}
    output_rows = [
        row
        for row in output_rows
        if str(row.get("JO") or "").strip() or _to_int(row.get("Lot")) in valid_lot_nos
    ]

    # Never drop a GO LOT/JO just because its PPO or fabric mapping is not
    # available yet.  The GO header is authoritative for the shipment lots;
    # retain unmatched color rows as WAIT-PPO rows so the UI can show the
    # missing mapping instead of silently truncating the order.
    output_identity = {
        (
            _to_int(row.get("Lot")),
            str(row.get("JO") or "").strip().upper(),
            str(row.get("COLOR_CODE") or "").strip().upper(),
        )
        for row in output_rows
    }
    for fallback_row in fallback_output_rows:
        identity = (
            _to_int(fallback_row.get("Lot")),
            str(fallback_row.get("JO") or "").strip().upper(),
            str(fallback_row.get("COLOR_CODE") or "").strip().upper(),
        )
        if identity not in output_identity:
            output_rows.append(fallback_row)
            output_identity.add(identity)

    # A LOT can exist in the GO header before any color/PPO detail is
    # published.  Add one explicit placeholder row in that case so it remains
    # visible and can be refreshed later when the mapping arrives.
    represented_lots = {
        (_to_int(row.get("Lot")), str(row.get("JO") or "").strip().upper())
        for row in output_rows
    }
    for lot in lots:
        lot_no = _to_int(lot.get("lot_no"))
        jo_no = str(lot.get("jo_no") or "").strip().upper()
        if lot_no <= 0 or not jo_no or (lot_no, jo_no) in represented_lots:
            continue
        output_rows.append(
            {
                "GO": go_key,
                "PPO": "",
                "Lot": lot_no,
                "JO": jo_no,
                "Type": "",
                "COLOR_CODE": "",
                "COLOR_DESC": "",
                "FABRIC_COMBO": "",
                "Qty": round(_to_float(lot.get("qty")), 2),
                "Buyer_PO_Del_Date": lot.get("buyer_po_del_date"),
                "Marker_YY": 0.0,
                "PPO_YY": 0.0,
                "Require_Qty_Yds": 0.0,
                "Plan_Allocate_Qty_Yds": 0.0,
                "Rcv_Qty_PPO": 0.0,
                "FOC_Qty": 0.0,
                "On_Hand_Qty": 0.0,
                "On_Hand_Pct": 0.0,
                "Shortage_Qty": 0.0,
                "Surplus_Qty": 0.0,
                "Allow_Short_Pct": _resolve_allowance_pct(lot.get("short_pct")),
                "Allow_Over_Pct": _resolve_allowance_pct(lot.get("over_pct")),
                "Alert": "WAIT PPO",
                "Automation_Action": "Waiting for PPO/fabric mapping",
                "Remark": "LOT/JO exists in GO report; PPO mapping is not available yet.",
            }
        )

    output_rows.sort(
        key=lambda row: (
            str(row.get("PPO") or ""),
            str(row.get("Type") or ""),
            str(row.get("COLOR_CODE") or ""),
            str(row.get("JO") or ""),
        )
    )

    coverage_pct = (total_on_hand / total_require) if total_require > 0 else 0.0
    return {
        "ok": True,
        "go": go_key,
        "head": go_head,
        "rows": output_rows,
        "row_count": len(output_rows),
        "factory_code": go_head.get("factory_code", ""),
        "jo_color_qty_rows": jo_color_qty_rows,
        "sql_source": {
            "foc_view": received_view,
            "fabric_view": "escmowner.V_GO_Fabric_Infor_ALL",
            "lot_view": "dbo.V_GO_BPO_HD_JO_ALL",
            "mapping_view": "dbo.V_GO_PPO_Mapping",
            "yy_view": "dbo.V_JO_PPO_YY",
            "mode": source_bundle.get("source_mode", ""),
            "synced_at": source_bundle.get("source_synced_at", ""),
            "live_error": source_bundle.get("source_live_error", ""),
        },
        "summary": {
            "total_require_qty": round(total_require, 3),
            "total_on_hand_qty": round(total_on_hand, 3),
            "total_shortage_qty": round(total_shortage, 3),
            "total_surplus_qty": round(total_surplus, 3),
            "coverage_pct": round(coverage_pct, 4),
            "short_alert_rows": short_count,
            "over_alert_rows": over_count,
            "ok_rows": len(output_rows) - short_count - over_count,
        },
        "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "sql_enrichment_skipped": not allow_slow_sql_enrichment,
    }


def _sample_status_keys_for_sheet_row(color_code: object, color_desc: object, fabric_color: object) -> list[str]:
    candidates = [
        color_desc,
        _extract_color_desc_from_combo(fabric_color),
        fabric_color,
        color_code,
    ]
    keys: list[str] = []
    for candidate in candidates:
        text = str(candidate or "").upper().replace("\xa0", " ").strip()
        if not text:
            continue
        if "@" in text:
            text = text.split("@", 1)[-1].strip()
        for item in (
            text,
            re.sub(r"^\s*0*\d+\s*[/@\\-]?\s*", "", text).strip(),
            re.sub(r"^\s*0*\d+\s+", "", text).strip(),
        ):
            key = re.sub(r"[^A-Z0-9]+", "", item)
            if key and key not in keys:
                keys.append(key)
    return keys


def _resolve_sample_status(lookup: dict, color_code: object, color_desc: object, fabric_color: object) -> str:
    if not isinstance(lookup, dict) or not lookup.get("ok"):
        return ""
    by_color = lookup.get("by_color") if isinstance(lookup.get("by_color"), dict) else {}
    for key in _sample_status_keys_for_sheet_row(color_code, color_desc, fabric_color):
        status = by_color.get(key)
        if isinstance(status, dict) and str(status.get("value") or "").strip():
            return str(status.get("value") or "").strip()
    default_status = lookup.get("default") if isinstance(lookup.get("default"), dict) else {}
    return str(default_status.get("value") or "").strip()


def _attach_sample_status_to_payload(
    payload: dict,
    go_key: str,
    sample_type: str = "PPS",
    *,
    allow_live: bool = True,
) -> dict:
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    columns = payload.get("columns") if isinstance(payload.get("columns"), list) else []
    if not any(str(item.get("key") if isinstance(item, dict) else "") == "SAMPLE STATUS" for item in columns):
        payload["columns"] = list(columns) + [
            {"letter": "X", "key": "SAMPLE STATUS", "label": "SAMPLE STATUS", "editable": False, "width": 148, "source": "MES Sample Tracking"}
        ]
    if any(str((row or {}).get("SAMPLE STATUS") or "").strip() for row in rows if isinstance(row, dict)):
        payload.setdefault("sources", {}).setdefault(
            "sample_status",
            f"MES / SampleReqTracking.asp -> {str(sample_type or 'PPS').strip().upper() or 'PPS'} (cached)",
        )
        return payload
    if not allow_live:
        payload.setdefault("sources", {}).setdefault("sample_status", "Deferred optional enrichment")
        return payload
    lookup = sample_status_lookup_for_go(go_key, sample_type or "PPS")
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["SAMPLE STATUS"] = _resolve_sample_status(
            lookup,
            row.get("COLOR_CODE"),
            row.get("COLOR_DESC"),
            row.get("FABRIC COLOR (For piecing only)"),
        )
    payload.setdefault("sources", {})["sample_status"] = f"MES / SampleReqTracking.asp -> {str(sample_type or 'PPS').strip().upper() or 'PPS'}"
    if lookup.get("error"):
        payload.setdefault("sources", {})["sample_status_error"] = lookup.get("error", "")
    return payload


def _build_live_coi_sheet_impl(
    go: str,
    prefer_mes_cache: bool = True,
    allow_live_mes: bool = False,
    allow_live_go_report: bool = False,
    allow_slow_sql_enrichment: bool = False,
    prefer_source_cache: bool = False,
    force_live_source_refresh: bool = False,
    sample_type: str = "PPS",
    allow_live_sample_status: bool = True,
    allow_live_size_breakdown: bool = True,
    manual_allocation_mode: str = _AH_ALLOCATE_MODE_REDISTRIBUTE,
) -> dict:
    source_bundle = _source_bundle_for_request(
        go,
        include_ppo_detail=allow_slow_sql_enrichment,
        prefer_source_cache=prefer_source_cache,
        force_live_source_refresh=force_live_source_refresh,
    )
    base_payload = build_live_coi(
        go,
        prefer_mes_cache=prefer_mes_cache,
        allow_live_mes=allow_live_mes,
        allow_live_go_report=allow_live_go_report,
        allow_slow_sql_enrichment=allow_slow_sql_enrichment,
        prefer_source_cache=prefer_source_cache and not force_live_source_refresh,
        source_bundle=source_bundle,
    )
    if not base_payload.get("ok"):
        return base_payload

    go_key = str(base_payload.get("go") or "").strip().upper()
    manual_allocation_mode = _normalize_manual_allocation_mode(manual_allocation_mode)
    head = dict(base_payload.get("head") or {})
    base_rows = list(base_payload.get("rows") or [])
    jo_color_qty_rows = list(base_payload.get("jo_color_qty_rows") or [])
    sql_bom_rows: list[dict] = []
    raw_sql_fabric_rows: list[dict] = []
    sql_fabric_rows: list[dict] = []
    jo_ppo_yy_rows: list[dict] = []
    received_rows: list[dict] = []
    stock_balance_rows: list[dict] = []
    shipment_on_way_rows: list[dict] = []
    received_view = ""
    stock_balance_view = ""
    stock_balance_error = ""
    stock_balance_refreshed = False
    shipment_source_key = ""
    shipment_source_table = ""
    shipment_on_way_error = ""
    ppo_detail_rows_by_ppo: dict[str, list[dict]] = {}
    ppo_order_totals: dict[tuple[str, str, str], dict] = {}
    source_ppo_mapping: list[dict] = []
    sql_enrichment_error = ""
    ppo_override_source_note = ""
    sql_source_mode = str((base_payload.get("sql_source") or {}).get("mode") or "").strip()
    sql_source_synced_at = str((base_payload.get("sql_source") or {}).get("synced_at") or "").strip()
    sql_source_live_error = str((base_payload.get("sql_source") or {}).get("live_error") or "").strip()
    persisted_state: dict[str, dict] = _load_local_saved_sheet_state(go_key)
    ppo_override_values = _saved_ppo_override_values(persisted_state)
    if source_bundle.get("ok"):
        sql_bom_rows = list(source_bundle.get("sql_bom_rows") or [])
        raw_sql_fabric_rows = list(source_bundle.get("fabric_rows") or [])
        sql_fabric_rows = _realign_fabric_rows_to_sql_bom(raw_sql_fabric_rows, sql_bom_rows)
        jo_ppo_yy_rows = list(source_bundle.get("jo_ppo_yy_rows") or [])
        received_rows = list(source_bundle.get("received_rows") or [])
        stock_balance_rows = list(source_bundle.get("stock_balance_rows") or [])
        shipment_on_way_rows = list(source_bundle.get("shipment_on_way_rows") or [])
        received_view = str(source_bundle.get("received_view") or "")
        stock_balance_view = str(source_bundle.get("stock_balance_view") or "")
        stock_balance_error = str(source_bundle.get("stock_balance_error") or "")
        stock_balance_refreshed = bool(source_bundle.get("stock_balance_refreshed"))
        shipment_source_key = str(source_bundle.get("shipment_source_key") or "")
        shipment_source_table = str(source_bundle.get("shipment_source_table") or "")
        shipment_on_way_error = str(source_bundle.get("shipment_on_way_error") or "")
        ppo_order_totals = dict(source_bundle.get("ppo_order_totals") or {})
        ppo_detail_rows_by_ppo = dict(source_bundle.get("ppo_detail_rows_by_ppo") or {})
        source_ppo_mapping = list(source_bundle.get("ppo_mapping") or [])
        sql_source_mode = str(source_bundle.get("source_mode") or sql_source_mode)
        sql_source_synced_at = str(source_bundle.get("source_synced_at") or sql_source_synced_at)
        sql_source_live_error = str(source_bundle.get("source_live_error") or sql_source_live_error)
        if ppo_override_values:
            (
                received_rows,
                stock_balance_rows,
                shipment_on_way_rows,
                ppo_order_totals,
                ppo_detail_rows_by_ppo,
                ppo_override_note,
            ) = _augment_source_for_ppo_overrides(
                str(head.get("factory_code") or ""),
                ppo_override_values,
                received_rows,
                stock_balance_rows,
                shipment_on_way_rows,
                ppo_order_totals,
                ppo_detail_rows_by_ppo,
                include_ppo_detail=allow_slow_sql_enrichment,
            )
            if ppo_override_note:
                ppo_override_source_note = ppo_override_note
                if "error:" in ppo_override_note.lower():
                    sql_source_live_error = "; ".join(
                        [item for item in [sql_source_live_error, ppo_override_note] if str(item or "").strip()]
                    )
        if sql_source_live_error:
            sql_enrichment_error = f"Using staged SQL source cache: {sql_source_live_error}"
    else:
        sql_enrichment_error = str(source_bundle.get("detail") or source_bundle.get("error") or "SQL source unavailable")

    go_report = _fetch_go_report_detail(go_key, allow_live_fetch=allow_live_go_report)
    sql_bom_rows = _augment_sql_bom_rows_with_go_report(
        sql_bom_rows,
        (go_report.get("knit_bom_rows") or []) if go_report.get("ok") else [],
    )
    if raw_sql_fabric_rows:
        sql_fabric_rows = _realign_fabric_rows_to_sql_bom(raw_sql_fabric_rows, sql_bom_rows)
    cutting_payload = (
        _load_cutting_payload(go_key, prefer_cache=prefer_mes_cache, allow_live_query=allow_live_mes)
        if allow_live_mes
        else {"summary": [], "jo_details": [], "source_label": "SQL only", "error": ""}
    )
    sample_status_lookup = (
        sample_status_lookup_for_go(go_key, sample_type or "PPS")
        if allow_live_sample_status
        else {"ok": False, "by_color": {}, "default": {}, "error": ""}
    )
    issue_state = get_go_issue_state(go_key)
    issue_lock_map = _load_go_issue_locks(go_key) if int(issue_state.get("issue_count") or 0) > 0 else {}
    customer_name = (
        _display_customer_name(go_report if go_report.get("ok") else None)
        or _display_customer_name_from_sql_rows(jo_color_qty_rows)
    )
    if not customer_name:
        # Cache-first requests should not open a new SQL Server connection just
        # to fill the display brand; doing so makes SQLite cache misses feel slow.
        if prefer_source_cache and sql_source_mode == "sqlite-source-cache":
            customer_name = str(head.get("customer_code") or "").strip()
        else:
            customer_name = _load_go_customer_name_from_sales(go_key)
    sql_bom_lookup = _build_sql_bom_lookup(sql_bom_rows)
    sql_fabric_yy_lookup = _build_sql_fabric_yy_lookup(sql_fabric_rows)
    received_type_alias_map = _build_received_type_alias_map(raw_sql_fabric_rows, sql_fabric_rows)
    go_report_remark_lookup = _build_go_report_remark_lookup(
        (go_report.get("knit_bom_rows") or []) if go_report.get("ok") else []
    )
    jo_ppo_yy_lookup: dict[tuple[str, str], float] = {}
    for item in jo_ppo_yy_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        jo_no = str(item.get("jo_no") or "").strip().upper()
        if not ppo_no or not jo_no:
            continue
        _set_max_jo_ppo_yy(jo_ppo_yy_lookup, ppo_no, jo_no, item.get("ppo_yy"))
    ppo_detail_valid_keys, ppo_detail_family_candidates, ppo_detail_family_types = _build_ppo_detail_assignment_maps(
        ppo_detail_rows_by_ppo
    )
    ppo_detail_yy_lookup = _build_ppo_detail_yy_lookup(ppo_detail_rows_by_ppo)
    received_lookup: dict[tuple[str, str, str], dict] = {}
    received_size_lookup: dict[tuple[str, str, str, str], dict] = {}
    for item in received_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        combo_name = str(item.get("combo_name") or "").strip()
        combo_key = _normalize_combo_key(combo_name)
        color_code = _extract_color_code_from_combo(combo_name)
        size_key = _normalize_size_code(item.get("size_code"))
        payload = {
            "received_qty": _to_float(item.get("received_qty")),
            "foc_qty": _to_float(item.get("foc_qty")),
            "source_combo_key": combo_key,
            "source_combo_name": combo_name,
        }
        target_types = {fabric_type} | _received_alias_types(received_type_alias_map, ppo_no, fabric_type, combo_name, color_code)
        for target_type in {item for item in target_types if item}:
            # For plain combo names (without a colour prefix), the generated
            # colour alias can be identical to the direct combo key. Insert it
            # once only; merging the same source row twice doubled Rcv Qty.
            inserted_identity_keys: set[str] = set()
            if ppo_no and target_type and combo_key:
                _merge_received_lookup_payload(received_lookup, (ppo_no, target_type, combo_key), payload)
                inserted_identity_keys.add(combo_key)
                if size_key:
                    _merge_received_lookup_payload(received_size_lookup, (ppo_no, target_type, combo_key, size_key), payload)
            for color_key in _color_lookup_keys_from_combo(combo_name):
                if color_key in inserted_identity_keys:
                    continue
                if ppo_no and target_type and color_key:
                    _merge_received_lookup_payload(received_lookup, (ppo_no, target_type, color_key), payload)
                    inserted_identity_keys.add(color_key)
                    if size_key:
                        _merge_received_lookup_payload(received_size_lookup, (ppo_no, target_type, color_key, size_key), payload)

    # Keep a second lookup for the physical on-hand balance. Its identity and
    # type-alias behaviour deliberately mirror receipt matching, while the
    # value is not mixed into the visible Received column.
    stock_balance_lookup: dict[tuple[str, str, str], dict] = {}
    stock_balance_size_lookup: dict[tuple[str, str, str, str], dict] = {}
    stock_balance_source_ready = bool(stock_balance_refreshed and not stock_balance_error)
    for item in stock_balance_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        combo_name = str(item.get("combo_name") or "").strip()
        combo_key = _normalize_combo_key(combo_name)
        color_code = _extract_color_code_from_combo(combo_name)
        size_key = _normalize_size_code(item.get("size_code"))
        payload = {
            "on_hand_qty": _to_float(item.get("on_hand_qty")),
            "allocated_qty": _to_float(item.get("allocated_qty")),
            "reserved_qty": _to_float(item.get("reserved_qty")),
            "source_combo_key": combo_key,
            "source_combo_name": combo_name,
        }
        target_types = {fabric_type} | _received_alias_types(
            received_type_alias_map,
            ppo_no,
            fabric_type,
            combo_name,
            color_code,
        )
        for target_type in {item for item in target_types if item}:
            # Same identity guard as Received/FOC. Without it, one physical
            # stock row can be added twice when its combo key is also the
            # colour alias, producing a false allocation surplus.
            inserted_identity_keys: set[str] = set()
            if ppo_no and target_type and combo_key:
                _merge_stock_balance_lookup_payload(
                    stock_balance_lookup,
                    (ppo_no, target_type, combo_key),
                    payload,
                )
                inserted_identity_keys.add(combo_key)
                if size_key:
                    _merge_stock_balance_lookup_payload(
                        stock_balance_size_lookup,
                        (ppo_no, target_type, combo_key, size_key),
                        payload,
                    )
            for color_key in _color_lookup_keys_from_combo(combo_name):
                if color_key in inserted_identity_keys:
                    continue
                if ppo_no and target_type and color_key:
                    _merge_stock_balance_lookup_payload(
                        stock_balance_lookup,
                        (ppo_no, target_type, color_key),
                        payload,
                    )
                    inserted_identity_keys.add(color_key)
                    if size_key:
                        _merge_stock_balance_lookup_payload(
                            stock_balance_size_lookup,
                            (ppo_no, target_type, color_key, size_key),
                            payload,
                        )

    shipment_lookup: dict[tuple[str, str, str], dict] = {}
    for item in shipment_on_way_rows:
        ppo_no = str(item.get("ppo_no") or "").strip().upper()
        fabric_type = _normalize_sql_fabric_type_code(item.get("fabric_type"))
        combo_name = str(item.get("combo_name") or "").strip()
        combo_key = _normalize_combo_key(combo_name)
        color_code = _extract_color_code_from_combo(combo_name)
        payload = {
            "shipment_qty": _to_float(item.get("shipment_qty")),
            "foc_qty": _to_float(item.get("foc_qty")),
            "eta_date": str(item.get("eta_date") or "").strip(),
            "ship_type": str(item.get("ship_type") or "").strip(),
            "source_combo_key": combo_key,
            "source_combo_name": combo_name,
        }
        target_types = {fabric_type} | _received_alias_types(received_type_alias_map, ppo_no, fabric_type, combo_name, color_code)
        for target_type in {item for item in target_types if item}:
            if ppo_no and target_type and combo_key:
                shipment_lookup[(ppo_no, target_type, combo_key)] = payload
            for color_key in _color_lookup_keys_from_combo(combo_name):
                if ppo_no and target_type and color_key:
                    shipment_lookup[(ppo_no, target_type, color_key)] = payload

    go_report_jo_map: dict[tuple[str, str], dict] = {}
    go_report_lot_map: dict[tuple[str, int], dict] = {}
    go_report_jo_fallback: dict[str, dict] = {}
    go_report_lot_fallback: dict[int, dict] = {}
    allowed_ppos_by_lot: dict[int, set[str]] = defaultdict(set)
    allowed_ppos_by_jo: dict[str, set[str]] = defaultdict(set)
    allowed_ppos_by_lot_jo: dict[tuple[int, str], set[str]] = defaultdict(set)
    for item in source_ppo_mapping:
        ppo_no = str(item.get("ppo_no") or item.get("ppo") or "").strip().upper()
        lot_no = _to_int(item.get("lot_no") or item.get("lot"))
        jo_no = str(item.get("jo_no") or item.get("job_order_no") or "").strip().upper()
        if not ppo_no:
            continue
        if lot_no > 0:
            allowed_ppos_by_lot[lot_no].add(ppo_no)
        if jo_no:
            allowed_ppos_by_jo[jo_no].add(ppo_no)
        if lot_no > 0 and jo_no:
            allowed_ppos_by_lot_jo[(lot_no, jo_no)].add(ppo_no)
    for item in (go_report.get("lot_rows") or []) if go_report.get("ok") else []:
        jo_no = str(item.get("job_order_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot"))
        normalized_item = {
            **item,
            "minus_pct": item.get("minus_pct"),
            "plus_pct": item.get("plus_pct"),
        }
        if jo_no:
            go_report_jo_fallback[jo_no] = normalized_item
        if lot_no > 0:
            go_report_lot_fallback[lot_no] = normalized_item
    for item in (go_report.get("ppo_mapping") or []) if go_report.get("ok") else []:
        ppo_no = str(item.get("ppo") or "").strip().upper()
        jo_no = str(item.get("job_order_no") or "").strip().upper()
        lot_no = _to_int(item.get("lot"))
        if ppo_no and jo_no:
            go_report_jo_map[(ppo_no, jo_no)] = item
        if jo_no:
            go_report_jo_fallback[jo_no] = {**go_report_jo_fallback.get(jo_no, {}), **item}
        if ppo_no and lot_no > 0:
            go_report_lot_map[(ppo_no, lot_no)] = item
            go_report_lot_fallback[lot_no] = {**go_report_lot_fallback.get(lot_no, {}), **item}
            allowed_ppos_by_lot[lot_no].add(ppo_no)
        if ppo_no and jo_no:
            allowed_ppos_by_jo[jo_no].add(ppo_no)
        if ppo_no and lot_no > 0 and jo_no:
            allowed_ppos_by_lot_jo[(lot_no, jo_no)].add(ppo_no)

    remapped_rows: list[dict] = []
    for row in base_rows:
        original_ppo = str(row.get("PPO") or "").strip().upper()
        fabric_type = str(row.get("Type") or "").strip().upper()
        jo_no = str(row.get("JO") or "").strip().upper()
        lot_no = _to_int(row.get("Lot"))
        allowed_ppos = set()
        if lot_no > 0 and jo_no:
            allowed_ppos.update(allowed_ppos_by_lot_jo.get((lot_no, jo_no), set()))
        if not allowed_ppos and lot_no > 0:
            allowed_ppos.update(allowed_ppos_by_lot.get(lot_no, set()))
        if not allowed_ppos and jo_no:
            allowed_ppos.update(allowed_ppos_by_jo.get(jo_no, set()))
        effective_ppo = _resolve_sheet_effective_ppo(
            original_ppo,
            fabric_type,
            row.get("COLOR_CODE"),
            row.get("COLOR_DESC"),
            row.get("FABRIC_COMBO"),
            [jo_no],
            allowed_ppos,
            jo_ppo_yy_lookup,
            ppo_detail_valid_keys,
            ppo_detail_family_candidates,
            ppo_detail_family_types,
        )
        if not effective_ppo and original_ppo:
            continue
        adjusted = dict(row)
        adjusted["PPO"] = effective_ppo
        if not effective_ppo and not str(adjusted.get("Remark") or "").strip():
            adjusted["Remark"] = "WAIT PPO mapping from GO report/SQL"
        remapped_rows.append(adjusted)

    authoritative_lot_nos = {
        _to_int(item.get("lot_no"))
        for item in (source_bundle.get("lots") or [])
        if _to_int(item.get("lot_no")) > 0
    }
    authoritative_lot_nos.update(
        _to_int(item.get("lot"))
        for item in (go_report.get("lot_rows") or [])
        if _to_int(item.get("lot")) > 0
    )
    # Do not let orphan color-size transactions with a blank JO re-enter the
    # sheet during remapping. They are not valid GO LOT rows.
    remapped_rows = [row for row in remapped_rows if str(row.get("JO") or "").strip()]

    remapped_rows = _harmonize_sheet_rows_by_lot_color(
        remapped_rows,
        jo_ppo_yy_lookup,
        ppo_detail_valid_keys,
        ppo_detail_family_candidates,
    )
    remapped_rows = _prune_unbound_placeholder_rows(remapped_rows)
    if any(
        str(row.get("Type") or "").strip().upper() in _FLATKNIT_SIZE_TYPES
        for row in remapped_rows
    ):
        # FK collar (O) and cuff (F) are issued and allocated by garment
        # size/color. Their size breakdown is mandatory even for a background
        # cache rebuild, otherwise an unsized snapshot can overwrite the UI.
        remapped_rows = _split_flatknit_rows_by_size(
            remapped_rows,
            _load_go_jo_color_size_qty_live(go_key),
        )

    all_source_ppos = {
        str(item.get("ppo_no") or item.get("ppo") or "").strip().upper()
        for item in source_ppo_mapping
        if str(item.get("ppo_no") or item.get("ppo") or "").strip()
    }
    for item in (go_report.get("ppo_mapping") or []) if go_report.get("ok") else []:
        ppo_no = str(item.get("ppo") or "").strip().upper()
        if ppo_no:
            all_source_ppos.add(ppo_no)
    all_source_ppos.update(
        str(row.get("PPO") or "").strip().upper()
        for row in remapped_rows
        if str(row.get("PPO") or "").strip()
    )
    all_source_ppos.update(
        str(row.get("ppo_no") or "").strip().upper()
        for row in received_rows
        if str(row.get("ppo_no") or "").strip()
    )
    all_source_ppos.update(
        str(row.get("ppo_no") or "").strip().upper()
        for row in stock_balance_rows
        if str(row.get("ppo_no") or "").strip()
    )

    for row in remapped_rows:
        auto_ppo = str(row.get("PPO") or "").strip().upper()
        row["__auto_ppo"] = auto_ppo
        base_storage = {
            "go_no": go_key,
            "ppo_no": auto_ppo,
            "lot_no": _to_int(row.get("Lot")),
            "jo_no": str(row.get("JO") or "").strip().upper(),
            "fabric_type": str(row.get("Type") or "").strip().upper(),
            "color_code": str(row.get("COLOR_CODE") or "").strip().upper(),
            "fabric_combo": str(row.get("FABRIC_COMBO") or "").strip(),
            "size_code": str(row.get("SIZE") or "").strip(),
        }
        saved_override = str(_saved_sheet_state_for_storage(persisted_state, base_storage).get("ppo_override") or "").strip().upper()
        if not saved_override or saved_override == auto_ppo:
            row["__ppo_override"] = ""
            continue
        lot_no = _to_int(row.get("Lot"))
        jo_no = str(row.get("JO") or "").strip().upper()
        allowed_for_row: set[str] = set()
        if lot_no > 0 and jo_no:
            allowed_for_row.update(allowed_ppos_by_lot_jo.get((lot_no, jo_no), set()))
        if lot_no > 0:
            allowed_for_row.update(allowed_ppos_by_lot.get(lot_no, set()))
        if jo_no:
            allowed_for_row.update(allowed_ppos_by_jo.get(jo_no, set()))
        if not allowed_for_row:
            allowed_for_row.update(all_source_ppos)
        if saved_override in allowed_for_row or saved_override in all_source_ppos:
            row["PPO"] = saved_override
            row["__ppo_override"] = saved_override
        else:
            row["__ppo_override"] = ""

    group_received_totals: dict[tuple[str, str, str, str, str], float] = {}
    group_stock_on_hand_totals: dict[tuple[str, str, str, str, str], float] = {}
    group_on_way_totals: dict[tuple[str, str, str, str, str], float] = {}
    group_on_way_eta: dict[tuple[str, str, str, str, str], str] = {}
    group_order_totals: dict[tuple[str, str, str, str, str], float] = {}
    group_qty_totals: dict[tuple[str, str, str, str, str], float] = defaultdict(float)
    flatknit_on_way_parent_totals: dict[tuple[str, str, str, str], float] = {}
    type_f_family_color_received_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    type_f_family_color_qty_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    allocation_pool_received_totals: dict[tuple, float] = defaultdict(float)
    allocation_pool_stock_on_hand_totals: dict[tuple, float] = defaultdict(float)
    allocation_pool_on_way_totals: dict[tuple, float] = defaultdict(float)
    allocation_pool_source_identities: dict[tuple, set[tuple]] = defaultdict(set)
    allocation_pool_received_complete: dict[tuple, bool] = {}
    allocation_pool_stock_complete: dict[tuple, bool] = {}
    canonical_group_key_by_row_id: dict[int, tuple[str, str, str, str, str]] = {}
    group_received_known: dict[tuple[str, str, str, str, str], bool] = {}
    group_stock_known: dict[tuple[str, str, str, str, str], bool] = {}
    received_mapping_diagnostics: list[dict] = []
    for row in remapped_rows:
        row_ppo = str(row.get("PPO") or "").strip().upper()
        row_type = str(row.get("Type") or "").strip().upper()
        row_color = str(row.get("COLOR_CODE") or "").strip().upper()
        row_combo = str(row.get("FABRIC_COMBO") or "").strip()
        row_size = _normalize_size_code(row.get("SIZE")) if row_type in _FLATKNIT_SIZE_TYPES else ""
        received_match = _find_received_row(
            received_size_lookup if row_size else received_lookup,
            row_ppo,
            row_type,
            row_combo,
            row_color,
            row_size,
            prefer_color_identity=bool(row_size and row_type in _FLATKNIT_SIZE_TYPES),
        )
        stock_match = _find_stock_balance_row(
            stock_balance_size_lookup if row_size else stock_balance_lookup,
            row_ppo,
            row_type,
            row_combo,
            row_color,
            row_size,
            prefer_color_identity=bool(row_size and row_type in _FLATKNIT_SIZE_TYPES),
        )
        canonical_combo_key = _matched_source_combo_key(received_match or stock_match, row_combo)
        group_key = (
            row_ppo,
            row_type,
            row_color,
            canonical_combo_key,
            row_size,
        )
        canonical_group_key_by_row_id[id(row)] = group_key
        group_qty_totals[group_key] += _to_float(row.get("Qty"))
        if group_key not in group_order_totals:
            row_display_color = _display_color_code(row)
            row_display_desc = str(row.get("COLOR_DESC") or "").strip() or _extract_color_desc_from_combo(row.get("FABRIC_COMBO"))
            group_order_totals[group_key] = _to_float(
                _resolve_ppo_order_total_for_row(
                    ppo_order_totals,
                    row.get("PPO"),
                    row.get("Type"),
                    row_display_color,
                    row_display_desc,
                    str(row.get("FABRIC_COMBO") or "").strip(),
                )
            )
        if group_key not in group_received_totals:
            group_received_known[group_key] = received_match is not None
            group_received_totals[group_key] = _display_received_qty(received_match)
            if received_match is None and len(received_mapping_diagnostics) < 200:
                candidates = [
                    item for item in received_rows
                    if str(item.get("ppo_no") or "").strip().upper() == row_ppo
                ]
                reason = "missing_ppo"
                if candidates:
                    type_candidates = {
                        _normalize_sql_fabric_type_code(item.get("fabric_type"))
                        for item in candidates
                    }
                    combo_candidates = {
                        _normalize_combo_key(item.get("combo_name"))
                        for item in candidates
                    }
                    reason = "fabric_type_mismatch" if row_type not in type_candidates else "combo_mismatch"
                    if row_size and any(
                        _normalize_sql_fabric_type_code(item.get("fabric_type")) in _fabric_type_lookup_candidates(row_type)
                        for item in candidates
                    ):
                        reason = "size_mismatch"
                    if _normalize_combo_key(row_combo) in combo_candidates and row_size:
                        reason = "size_mismatch"
                received_mapping_diagnostics.append(
                    {
                        "ppo_no": row_ppo,
                        "fabric_type": row_type,
                        "combo_name": row_combo,
                        "size_code": row_size,
                        "reason": reason,
                    }
                )
        if group_key not in group_stock_on_hand_totals:
            # A successfully queried stock source authoritatively reports zero
            # when a formerly received item has been fully issued. If the
            # source itself is unavailable, never reuse receipt as on-hand:
            # that would over-allocate fabric already consumed by stock/SR
            # issues. The allocation pool is explicitly held at zero below
            # until a current physical-stock query succeeds.
            group_stock_known[group_key] = stock_balance_source_ready
            group_stock_on_hand_totals[group_key] = (
                _display_stock_on_hand_qty(stock_match)
                if stock_balance_source_ready
                else 0.0
            )
        if group_key not in group_on_way_totals:
            shipment_match = _find_shipment_on_way_row(
                shipment_lookup,
                row.get("PPO"),
                row.get("Type"),
                row.get("FABRIC_COMBO"),
                row.get("COLOR_CODE"),
            )
            shipment_qty = _display_shipment_qty(shipment_match)
            # O/F receipts are exact per garment size but the shipment feed is
            # only at PPO/type/combo level.  Compare shipment against the
            # aggregate receipt first, then distribute any genuine balance over
            # sizes after the group quantities are known.  Using a size receipt
            # here would repeat the aggregate shipment total for every size.
            if row_type in _FLATKNIT_SIZE_TYPES and row_size:
                aggregate_received_match = _find_received_row(
                    received_lookup,
                    row_ppo,
                    row_type,
                    row_combo,
                    row_color,
                    prefer_color_identity=True,
                )
                received_qty = _display_received_qty(aggregate_received_match)
            else:
                received_qty = group_received_totals.get(group_key, 0.0)
            order_qty = group_order_totals.get(group_key, 0.0)
            shipment_balance = max(shipment_qty - received_qty, 0.0)
            if shipment_balance <= _SHIPMENT_ON_WAY_TOLERANCE_YDS:
                shipment_balance = 0.0
            if order_qty > 0:
                effective_on_way = min(shipment_balance, max(order_qty - received_qty, 0.0))
            else:
                effective_on_way = shipment_balance
            if effective_on_way <= _SHIPMENT_ON_WAY_TOLERANCE_YDS:
                effective_on_way = 0.0
            if row_type in _FLATKNIT_SIZE_TYPES and row_size:
                flatknit_on_way_parent_totals[group_key[:4]] = round(effective_on_way, 3)
                group_on_way_totals[group_key] = 0.0
            else:
                group_on_way_totals[group_key] = round(effective_on_way, 3)
            group_on_way_eta[group_key] = str(shipment_match.get("eta_date") or "").strip()

    for parent_key, total_on_way in flatknit_on_way_parent_totals.items():
        size_group_qty = {
            group_key: qty
            for group_key, qty in group_qty_totals.items()
            if group_key[:4] == parent_key and str(group_key[4] or "").strip()
        }
        group_on_way_totals.update(
            _distribute_flatknit_total_by_size(total_on_way, size_group_qty)
        )
    for group_key, received_qty in group_received_totals.items():
        ppo_no, fabric_type, color_code, _combo, size_code = group_key
        pool_key = _allocation_pool_key_for_row(ppo_no, fabric_type, color_code, _combo, size_code)
        allocation_pool_received_complete[pool_key] = bool(
            allocation_pool_received_complete.get(pool_key, True)
            and group_received_known.get(group_key, False)
        )
        allocation_pool_stock_complete[pool_key] = bool(
            allocation_pool_stock_complete.get(pool_key, True)
            and group_stock_known.get(group_key, False)
        )
        source_identity = _allocation_source_identity_for_group(group_key)
        if source_identity in allocation_pool_source_identities[pool_key]:
            continue
        allocation_pool_source_identities[pool_key].add(source_identity)
        allocation_pool_received_totals[pool_key] += received_qty
        allocation_pool_stock_on_hand_totals[pool_key] += group_stock_on_hand_totals.get(group_key, 0.0)
        allocation_pool_on_way_totals[pool_key] += group_on_way_totals.get(group_key, 0.0)
        if fabric_type == "F":
            type_f_family_color_received_totals[(_ppo_family_prefix(ppo_no), "F", color_code)] += received_qty
    for group_key, qty in group_qty_totals.items():
        ppo_no, fabric_type, color_code, _combo, _size_code = group_key
        if fabric_type == "F":
            type_f_family_color_qty_totals[(_ppo_family_prefix(ppo_no), "F", color_code)] += qty

    edit_persistence = "sqlite"

    prepared_rows: list[dict] = []
    allocation_pool_rows: dict[tuple, list[dict]] = defaultdict(list)
    missing_mes_rows = 0
    inferred_missing_jo_ppo_yy = _infer_missing_jo_ppo_yy_for_rows(
        remapped_rows,
        sql_bom_lookup,
        sql_fabric_yy_lookup,
        jo_ppo_yy_rows,
    )

    for row in remapped_rows:
        display_color = _display_color_code(row)
        display_color_desc = str(row.get("COLOR_DESC") or "").strip() or _extract_color_desc_from_combo(row.get("FABRIC_COMBO"))
        effective_ppo_no = str(row.get("PPO") or "").strip().upper()
        auto_ppo_no = str(row.get("__auto_ppo") or effective_ppo_no).strip().upper()
        ppo_override_no = str(row.get("__ppo_override") or "").strip().upper()
        storage = {
            "go_no": go_key,
            "ppo_no": auto_ppo_no,
            "effective_ppo_no": effective_ppo_no,
            "ppo_override": ppo_override_no,
            "lot_no": _to_int(row.get("Lot")),
            "jo_no": str(row.get("JO") or "").strip().upper(),
            "fabric_type": str(row.get("Type") or "").strip().upper(),
            "color_code": str(row.get("COLOR_CODE") or "").strip().upper(),
            "fabric_combo": str(row.get("FABRIC_COMBO") or "").strip(),
            "size_code": str(row.get("SIZE") or "").strip(),
        }
        storage_key = _row_storage_key(storage)
        group_key = canonical_group_key_by_row_id.get(id(row)) or (
            effective_ppo_no,
            storage["fabric_type"],
            storage["color_code"],
            _normalize_combo_key(storage["fabric_combo"]) or str(storage["fabric_combo"]).strip().upper(),
            _normalize_size_code(storage["size_code"]) if storage["fabric_type"] in _FLATKNIT_SIZE_TYPES else "",
        )
        allocation_pool_key = _allocation_pool_key_for_row(*group_key)
        report_row = (
            go_report_jo_map.get((effective_ppo_no, storage["jo_no"]))
            or go_report_lot_map.get((effective_ppo_no, storage["lot_no"]))
            or go_report_jo_fallback.get(storage["jo_no"])
            or go_report_lot_fallback.get(storage["lot_no"])
            or {}
        )
        sql_bom_row = _resolve_sql_bom_row(
            {
                "Type": storage["fabric_type"],
                "COLOR_CODE": display_color,
                "COLOR_DESC": display_color_desc,
                "FABRIC COLOR (For piecing only)": str(row.get("FABRIC_COMBO") or "").strip(),
            },
            sql_bom_lookup,
        )
        sql_fabric_row = _resolve_sql_fabric_yy_row(
            {
                "PPO": effective_ppo_no,
                "Type": storage["fabric_type"],
                "COLOR_CODE": display_color,
                "COLOR_DESC": display_color_desc,
                "FABRIC COLOR (For piecing only)": str(row.get("FABRIC_COMBO") or "").strip(),
            },
            sql_fabric_yy_lookup,
        )

        sql_marker_yy = _to_float(sql_bom_row.get("marker_yy")) or _to_float(sql_fabric_row.get("marker_yy"))
        sql_ppo_yy = _to_float(sql_bom_row.get("yy")) or _to_float(sql_fabric_row.get("ppo_yy"))
        net_yy = sql_marker_yy
        base_ppo_yy = _to_float(row.get("PPO_YY"))
        base_marker_yy = _to_float(row.get("Marker_YY"))
        inferred_yy = inferred_missing_jo_ppo_yy.get(id(row), {})
        if sql_ppo_yy <= 0 and base_ppo_yy <= 0:
            base_ppo_yy = _to_float(inferred_yy.get("ppo_yy"))
        if sql_marker_yy <= 0 and base_marker_yy <= 0:
            base_marker_yy = _to_float(inferred_yy.get("marker_yy"))
        if sql_ppo_yy <= 0 and base_ppo_yy <= 0:
            detail_yy = _resolve_ppo_detail_yy_for_row(
                ppo_detail_yy_lookup,
                effective_ppo_no,
                storage["fabric_type"],
                display_color,
                display_color_desc,
                str(row.get("FABRIC_COMBO") or "").strip(),
            )
            detail_ppo_yy = _to_float(detail_yy.get("ppo_yy")) if detail_yy else 0.0
            detail_marker_yy = _to_float(detail_yy.get("marker_yy")) if detail_yy else 0.0
            if detail_ppo_yy > 0:
                base_ppo_yy = detail_ppo_yy
                if sql_marker_yy <= 0 and base_marker_yy <= 0 and detail_marker_yy > 0:
                    base_marker_yy = detail_marker_yy
        ppo_yy = sql_ppo_yy or base_ppo_yy
        marker_yy = sql_marker_yy or base_marker_yy
        total_received_for_group = group_received_totals.get(group_key, 0.0)
        total_stock_on_hand_for_group = group_stock_on_hand_totals.get(group_key, 0.0)
        total_on_way_for_group = group_on_way_totals.get(group_key, 0.0)
        total_qty_for_group = group_qty_totals.get(group_key, 0.0)
        if storage["fabric_type"] in {"O", "L"}:
            marker_yy = 1.0
        elif storage["fabric_type"] in {"F", "U"}:
            color_group_key = (
                _ppo_family_prefix(effective_ppo_no),
                "F" if storage["fabric_type"] == "U" else storage["fabric_type"],
                storage["color_code"],
            )
            total_received_for_color = type_f_family_color_received_totals.get(color_group_key, total_received_for_group)
            total_qty_for_color = type_f_family_color_qty_totals.get(color_group_key, total_qty_for_group)
            coverage_to_total_qty = (total_received_for_color / total_qty_for_color) if total_qty_for_color > 0 else 0.0
            marker_yy = 2.0 if coverage_to_total_qty > 1.50 else 1.0
        net_yy = net_yy or marker_yy
        if net_yy <= 0:
            missing_mes_rows += 1

        base_qty = _to_float(row.get("Qty"))
        qty = base_qty
        minus_pct = _resolve_allowance_pct(report_row.get("minus_pct"), row.get("Allow_Short_Pct"))
        plus_pct = _resolve_allowance_pct(report_row.get("plus_pct"), row.get("Allow_Over_Pct"))
        # PPC fabric requirement is PPO YY � garment quantity.  Net/Marker YY
        # remain visible for comparison only and must not drive COI demand.
        required_qty = _required_qty_from_ppo_yy(
            qty,
            ppo_yy,
            fallback_yy=marker_yy,
            allow_flatknit_fallback=storage["fabric_type"] in _FLATKNIT_SIZE_TYPES,
        )
        target_pct = 1.0 + max(plus_pct, 0.0) / 100.0
        target_qty = required_qty * target_pct if required_qty > 0 else 0.0
        row_due = report_row.get("buyer_po_del_date") or row.get("Buyer_PO_Del_Date")
        auto_remark = _resolve_go_report_remark(
            {
                "Type": storage["fabric_type"],
                "COLOR_CODE": display_color,
                "COLOR_DESC": display_color_desc,
                "FABRIC COLOR (For piecing only)": str(row.get("FABRIC_COMBO") or "").strip(),
            },
            go_report_remark_lookup,
        )
        sample_status = _resolve_sample_status(
            sample_status_lookup,
            display_color,
            display_color_desc,
            str(row.get("FABRIC_COMBO") or "").strip(),
        )
        saved = _saved_sheet_state_for_storage(persisted_state, storage)
        user_remark_value = _sanitize_sheet_remark(
            saved.get("user_remark"),
            auto_candidates=[auto_remark, row.get("Remark")],
        )
        ppo_order_total_value = _resolve_ppo_order_total_for_row(
            ppo_order_totals,
            effective_ppo_no,
            storage["fabric_type"],
            display_color,
            display_color_desc,
            str(row.get("FABRIC_COMBO") or "").strip(),
        )
        on_way_etd_remark = _build_on_way_etd_remark(
            total_received_for_group,
            total_on_way_for_group,
            _to_float(ppo_order_total_value),
            group_on_way_eta.get(group_key, ""),
        )
        etd_fabric_value = _merge_on_way_etd_remark(str(saved.get("etd_fabric") or "").strip(), on_way_etd_remark)

        prepared = {
            "__row_key": storage_key,
            "__storage": storage,
            "__group_key": group_key,
            "__allocation_pool_key": allocation_pool_key,
            "__due_sort_key": _due_date_sort_key(row_due),
            "__display_color_sort_key": _display_color_sort_key(display_color),
            "__type_sort_key": _type_sort_key(storage["fabric_type"]),
            "__base_received_qty": _to_float(row.get("Rcv_Qty_PPO")),
            "__stock_on_hand_qty": round(total_stock_on_hand_for_group, 3),
            "__stock_data_status": "VERIFIED" if group_stock_known.get(group_key, False) else "UNAVAILABLE",
            "__issue_locked_qty": _to_float(issue_lock_map.get(storage_key)),
            "BRAND": customer_name,
            "GO#": go_key,
            "PPO": effective_ppo_no,
            "Type": storage["fabric_type"],
            "COLOR_CODE": display_color,
            "COLOR_DESC": display_color_desc,
            "FABRIC COLOR (For piecing only)": str(row.get("FABRIC_COMBO") or "").strip(),
            "JOB ORDER NO": storage["jo_no"],
            "LOT": storage["lot_no"] if storage["lot_no"] > 0 else "",
            "SIZE": storage["size_code"],
            "- %": minus_pct,
            "+%": plus_pct,
            "Qty (pcs)": round(qty, 3),
            "BUYER_PO_DEL_DATE": row_due,
            "Net YY": round(net_yy, 6),
            "PPO YY": round(ppo_yy, 6),
            "Marker YY": round(marker_yy, 6),
            "Required Q'ty (Yds)": round(required_qty, 3),
            "Rcv Q'ty (PPO)": 0.0,
            "On The Way Q'ty (Yds)": 0.0,
            "Allocate Q'ty (Yds)": 0.0,
            "Shortage Q'ty (Yds)": 0.0,
            "AH Allocate Q'ty (yds)": "" if saved.get("manual_allocate_qty") is None else round(_to_float(saved.get("manual_allocate_qty")), 3),
            "Allocate %": 0.0,
            "ETD Fabric": etd_fabric_value,
            "User Remark": user_remark_value,
            "PPO Order Total (Yds)": ppo_order_total_value,
            "SAMPLE STATUS": sample_status,
            "__target_qty": target_qty,
            "__target_pct": target_pct,
        }
        prepared_rows.append(prepared)
        allocation_pool_rows[allocation_pool_key].append(prepared)

    for pool_key, rows in allocation_pool_rows.items():
        total_received = allocation_pool_received_totals.get(pool_key, 0.0)
        total_stock_on_hand = allocation_pool_stock_on_hand_totals.get(pool_key, 0.0)
        total_on_way = allocation_pool_on_way_totals.get(pool_key, 0.0)
        received_complete = allocation_pool_received_complete.get(pool_key, False)
        stock_complete = allocation_pool_stock_complete.get(pool_key, False)
        # Do not silently allocate from receipt/on-way while the physical
        # stock source is unavailable. A user can still see the received and
        # on-way values, but system allocation remains conservative until the
        # net balance can be verified.
        total_available = _system_allocation_available_qty(
            total_stock_on_hand,
            total_on_way,
            stock_complete,
        )
        preserve_manual_gap = manual_allocation_mode == _AH_ALLOCATE_MODE_PRESERVE
        allocations = _compute_pool_system_allocations(
            rows,
            total_available,
            respect_manual=not preserve_manual_gap,
        )

        for item in rows:
            locked_qty = max(_to_float(item.get("__issue_locked_qty")), 0.0)
            manual_raw = item.get("AH Allocate Q'ty (yds)")
            has_manual = str(manual_raw or "").strip() != ""
            if locked_qty > 0:
                item["Allocate Q'ty (Yds)"] = round(locked_qty, 3)
            elif has_manual:
                item["Allocate Q'ty (Yds)"] = 0.0
            else:
                item["Allocate Q'ty (Yds)"] = round(_to_float(allocations.get(id(item), 0.0)), 3)

        for item in rows:
            system_allocate = _to_float(item.get("Allocate Q'ty (Yds)"))
            locked_qty = max(_to_float(item.get("__issue_locked_qty")), 0.0)
            manual_raw = item.get("AH Allocate Q'ty (yds)")
            manual_allocate = _to_float(manual_raw) if str(manual_raw or "").strip() != "" else None
            effective_allocate = locked_qty if locked_qty > 0 else (manual_allocate if manual_allocate is not None else system_allocate)
            required_qty = _to_float(item.get("Required Q'ty (Yds)"))
            shortage_qty = max(required_qty - effective_allocate, 0.0)
            allocate_pct = (effective_allocate / required_qty) if required_qty > 0 else 0.0

            # Missing identity is not the same as an authoritative zero.
            item["Rcv Q'ty (PPO)"] = round(total_received, 3) if received_complete else ""
            item["Rcv Data Status"] = "VERIFIED" if received_complete else "NOT_FOUND"
            item["Stock Data Status"] = "VERIFIED" if stock_complete else "UNAVAILABLE"
            item["On The Way Q'ty (Yds)"] = round(total_on_way, 3)
            item["Shortage Q'ty (Yds)"] = round(shortage_qty, 3)
            item["Allocate %"] = round(allocate_pct, 4)

    prepared_rows.sort(
        key=lambda item: (
            item.get("__display_color_sort_key", (2, 999999, "")),
            item.get("__type_sort_key", (99, "")),
            item.get("__due_sort_key", (9999, 12, 31, 23, 59, 59)),
            str(item.get("JOB ORDER NO") or ""),
            str(item.get("PPO") or ""),
        )
    )

    total_required = sum(_to_float(item.get("Required Q'ty (Yds)")) for item in prepared_rows)
    total_received = sum(allocation_pool_received_totals.values())
    total_stock_on_hand = sum(allocation_pool_stock_on_hand_totals.values())
    total_on_way = sum(allocation_pool_on_way_totals.values())
    total_system_allocate = sum(_to_float(item.get("Allocate Q'ty (Yds)")) for item in prepared_rows)
    total_effective_allocate = 0.0
    total_shortage = 0.0
    total_manual_allocate = 0.0
    manual_rows = 0
    received_by_type: dict[str, float] = defaultdict(float)
    stock_on_hand_by_type: dict[str, float] = defaultdict(float)
    on_way_by_type: dict[str, float] = defaultdict(float)
    system_allocate_by_type: dict[str, float] = defaultdict(float)
    effective_allocate_by_type: dict[str, float] = defaultdict(float)
    manual_allocate_by_type: dict[str, float] = defaultdict(float)

    for pool_key, received_qty in allocation_pool_received_totals.items():
        type_key = str(pool_key[2] if len(pool_key) > 2 else "").strip().upper()
        if type_key:
            received_by_type[type_key] += _to_float(received_qty)
    for pool_key, stock_qty in allocation_pool_stock_on_hand_totals.items():
        type_key = str(pool_key[2] if len(pool_key) > 2 else "").strip().upper()
        if type_key:
            stock_on_hand_by_type[type_key] += _to_float(stock_qty)
    for pool_key, on_way_qty in allocation_pool_on_way_totals.items():
        type_key = str(pool_key[2] if len(pool_key) > 2 else "").strip().upper()
        if type_key:
            on_way_by_type[type_key] += _to_float(on_way_qty)

    for item in prepared_rows:
        type_key = str(item.get("Type") or "").strip().upper()
        system_allocate = _to_float(item.get("Allocate Q'ty (Yds)"))
        if type_key:
            system_allocate_by_type[type_key] += system_allocate
        manual_raw = item.get("AH Allocate Q'ty (yds)")
        if str(manual_raw or "").strip() != "":
            manual_rows += 1
            manual_allocate = _to_float(manual_raw)
            total_manual_allocate += manual_allocate
            total_effective_allocate += manual_allocate
            if type_key:
                manual_allocate_by_type[type_key] += manual_allocate
                effective_allocate_by_type[type_key] += manual_allocate
        else:
            total_effective_allocate += system_allocate
            if type_key:
                effective_allocate_by_type[type_key] += system_allocate
        total_shortage += _to_float(item.get("Shortage Q'ty (Yds)"))

    snapshot_rows = []
    for item in prepared_rows:
        snapshot_rows.append(
            {
                **item,
                "__storage": item["__storage"],
            }
        )

    # UI edit persistence is SQLite-only. Avoid writing generated COI rows back
    # to SQL Server on every build; that path was slow and permission-sensitive.

    public_rows = []
    for item in prepared_rows:
        public = {key: value for key, value in item.items() if not key.startswith("__")}
        if str(public.get("AH Allocate Q'ty (yds)") or "").strip() != "":
            public["Allocate Q'ty (Yds)"] = ""
        public["_row_key"] = item.get("__row_key", "")
        public["_storage"] = dict(item.get("__storage") or {})
        public_rows.append(public)

    missing_received_group_count = sum(1 for known in group_received_known.values() if not known)
    missing_stock_group_count = sum(1 for known in group_stock_known.values() if not known)
    consumed_from_received_qty = sum(
        max(
            _to_float(allocation_pool_received_totals.get(pool_key))
            - _to_float(allocation_pool_stock_on_hand_totals.get(pool_key)),
            0.0,
        )
        for pool_key in allocation_pool_received_totals
        if allocation_pool_stock_complete.get(pool_key, False)
    )
    total_allocatable_available = sum(
        _system_allocation_available_qty(
            allocation_pool_stock_on_hand_totals.get(pool_key),
            allocation_pool_on_way_totals.get(pool_key),
            allocation_pool_stock_complete.get(pool_key, False),
        )
        for pool_key in allocation_pool_rows
    )
    summary = {
        "rows": len(public_rows),
        "total_required_qty": round(total_required, 3),
        "total_received_qty": round(total_received, 3),
        "received_data_complete": missing_received_group_count == 0,
        "missing_received_group_count": missing_received_group_count,
        "received_mapping_diagnostics": received_mapping_diagnostics,
        "total_stock_on_hand_qty": round(total_stock_on_hand, 3),
        "stock_balance_data_complete": missing_stock_group_count == 0,
        "missing_stock_balance_group_count": missing_stock_group_count,
        "stock_issue_or_sample_qty": round(consumed_from_received_qty, 3),
        "total_on_way_qty": round(total_on_way, 3),
        "total_available_qty": round(total_allocatable_available, 3),
        "manual_allocation_mode": manual_allocation_mode,
        "received_qty_by_type": _sorted_type_totals(received_by_type),
        "stock_on_hand_qty_by_type": _sorted_type_totals(stock_on_hand_by_type),
        "on_way_qty_by_type": _sorted_type_totals(on_way_by_type),
        "received_type_count": len(received_by_type),
        "total_manual_allocate_qty": round(total_manual_allocate, 3),
        "total_system_allocate_qty": round(total_system_allocate, 3),
        "system_allocate_qty_by_type": _sorted_type_totals(system_allocate_by_type),
        "total_effective_allocate_qty": round(total_effective_allocate, 3),
        "effective_allocate_qty_by_type": _sorted_type_totals(effective_allocate_by_type),
        "manual_allocate_qty_by_type": _sorted_type_totals(manual_allocate_by_type),
        "total_shortage_qty": round(total_shortage, 3),
        "coverage_pct": (
            round((total_received / total_required), 4)
            if total_required > 0 and missing_received_group_count == 0
            else None
        ),
        "available_coverage_pct": round((total_allocatable_available / total_required), 4) if total_required > 0 else 0.0,
        "allocate_pct": round((total_effective_allocate / total_required), 4) if total_required > 0 else 0.0,
        "manual_override_rows": manual_rows,
        "missing_mes_rows": missing_mes_rows,
        "issue_count": int(issue_state.get("issue_count") or 0),
    }
    cache_profile = _summarize_sheet_cache_profile(
        public_rows,
        summary,
        jo_color_qty_rows,
    )
    summary_total_required = _to_float(summary.get("total_required_qty"))
    summary_total_received = _to_float(summary.get("total_received_qty"))

    if missing_received_group_count > 0:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["RECEIVED_NOT_FOUND", "WAIT_SOURCE"])
        )
        reason_bits = [
            str(cache_profile.get("reason") or "").strip(),
            f"warehouse identity not found {missing_received_group_count}",
        ]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _snapshot_now()

    if missing_stock_group_count > 0:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["STOCK_BALANCE_UNAVAILABLE", "WAIT_SOURCE"])
        )
        reason_bits = [
            str(cache_profile.get("reason") or "").strip(),
            f"stock balance source unavailable for {missing_stock_group_count} group(s)",
        ]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _snapshot_now()

    if shipment_on_way_error:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["SHIPMENT_SOURCE_STALE", "WAIT_SOURCE"])
        )
        reason_bits = [str(cache_profile.get("reason") or "").strip(), "shipment source error; using last-known-good"]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _snapshot_now()

    if sql_enrichment_error:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["SQL_ENRICH_ERROR", "WAIT_SQL_ENRICH"])
        )
        reason_bits = [str(cache_profile.get("reason") or "").strip(), "sql enrichment error"]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _snapshot_now()

    if len(received_rows) > 0 and summary_total_required > 0 and summary_total_received <= 0:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["SOURCE_MISMATCH_RECEIVED"])
        )
        reason_bits = [str(cache_profile.get("reason") or "").strip(), "sql received > 0 but sheet received = 0"]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _snapshot_now()

    if summary["rows"] <= 0 and not source_ppo_mapping and not raw_sql_fabric_rows:
        cache_profile["flags"] = sorted(
            set(list(cache_profile.get("flags") or []) + ["WAIT_PPO", "MISSING_PPO_SOURCE"])
        )
        reason_bits = [
            str(cache_profile.get("reason") or "").strip(),
            "no PPO/fabric rows in SQL source yet",
        ]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        cache_profile["state"] = "WAIT_PPO"
        cache_profile["next_refresh_at"] = _next_refresh_at_for_cache_state("WAIT_PPO")

    if base_payload.get("sql_enrichment_skipped") and summary["rows"] > 0:
        cache_profile["flags"] = sorted(set(list(cache_profile.get("flags") or []) + ["WAIT_SQL_ENRICH"]))
        reason_bits = [str(cache_profile.get("reason") or "").strip(), "fast sql snapshot"]
        cache_profile["reason"] = "; ".join([bit for bit in reason_bits if bit][:6])
        if str(cache_profile.get("state") or "").strip().upper() == _CACHE_READY_STATE:
            cache_profile["state"] = "WAIT_SOURCE"
        cache_profile["next_refresh_at"] = _next_refresh_at_for_cache_state(cache_profile.get("state"))

    if not allow_slow_sql_enrichment:
        current_mismatch_count = int(cache_profile.get("sql_jo_color_mismatch_count") or 0) + int(cache_profile.get("sql_lot_color_mismatch_count") or 0)
        if current_mismatch_count > 0:
            retry_payload = _build_live_coi_sheet_impl(
                go_key,
                prefer_mes_cache=prefer_mes_cache,
                allow_live_mes=allow_live_mes,
                allow_live_go_report=allow_live_go_report,
                allow_slow_sql_enrichment=True,
                prefer_source_cache=prefer_source_cache,
                force_live_source_refresh=force_live_source_refresh,
                sample_type=sample_type,
                allow_live_sample_status=allow_live_sample_status,
                allow_live_size_breakdown=allow_live_size_breakdown,
                manual_allocation_mode=manual_allocation_mode,
            )
            if retry_payload.get("ok"):
                retry_profile = dict(retry_payload.get("cache_profile") or {})
                retry_mismatch_count = int(retry_profile.get("sql_jo_color_mismatch_count") or 0) + int(retry_profile.get("sql_lot_color_mismatch_count") or 0)
                retry_received_total = _to_float((retry_payload.get("summary") or {}).get("total_received_qty"))
                current_received_total = _to_float(summary.get("total_received_qty"))
                if (
                    retry_mismatch_count < current_mismatch_count
                    or (retry_mismatch_count == 0 and int(retry_payload.get("row_count") or 0) <= len(public_rows))
                ):
                    retry_payload.setdefault("sources", {})["sql_enrichment_escalated"] = "auto-retry"
                    return retry_payload

    return {
        "ok": True,
        "go": go_key,
        "head": head,
        "factory_code": base_payload.get("factory_code", ""),
        "style_no": head.get("style_no", ""),
        "style_desc": head.get("style_desc", ""),
        "brand": customer_name,
        "sheet": {
            "name": "FORMAT COI REQUEST",
            "header_row": 8,
            "first_data_row": 9,
            "template": "FORMAT COI REQUEST.xlsx",
        },
        "columns": list(_FORMAT_COI_COLUMNS),
        "rows": public_rows,
        "row_count": len(public_rows),
        "summary": summary,
        "sql_enrichment_error": sql_enrichment_error,
        "cache_profile": cache_profile,
        "sources": {
            "go_list": "SQL Server / dbo.V_GO_Head_Infor",
            "customer_name": "GO report -> Customer Name(Code)",
            "fabric_color": "PPO combo name",
            "qty_pcs": "SQL / dbo.V_ESCM_ORDER_COLORSIZE_SALES",
            "warehouse_received": base_payload.get("sql_source", {}).get("foc_view", ""),
            "warehouse_stock_on_hand": stock_balance_view or f"SQL / {STOCK_SQL_SCHEMA}.{STOCK_SQL_VIEW}",
            "warehouse_stock_identity": "PPO + fabric_type + combo; source view has no Size_Code",
            "warehouse_stock_on_hand_error": stock_balance_error,
            "allocation_source": "Net physical stock on hand + verified shipment balance",
            "fabric_on_way": shipment_source_table or "Shipment SQL / GAK_ShipmentDetail",
            "fabric_on_way_error": shipment_on_way_error,
            "sql_source_mode": sql_source_mode,
            "sql_source_synced_at": sql_source_synced_at,
            "sql_source_live_error": sql_source_live_error,
            "ppo_override_source": ppo_override_source_note,
            "mes": "SQL / escmowner.V_GO_Fabric_BOM_Infor + escmowner.V_GO_Fabric_Infor_ALL",
            "sample_status": f"MES / SampleReqTracking.asp -> {str(sample_type or 'PPS').strip().upper() or 'PPS'}",
            "ppo_order_total": "SQL / dbo.V_PPO_Summary_All_After_2015",
            "mes_error": cutting_payload.get("error", ""),
            "sample_status_error": sample_status_lookup.get("error", ""),
            "sql_enrichment_error": sql_enrichment_error,
            "remark": "Manual entry only (blank by default)",
            "edit_persistence": edit_persistence,
            "manual_allocation_mode": manual_allocation_mode,
        },
        "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }


@_serialize_sheet_build
def build_live_coi_sheet(
    go: str,
    prefer_mes_cache: bool = True,
    allow_live_mes: bool = False,
    allow_live_go_report: bool = False,
    allow_slow_sql_enrichment: bool = False,
    prefer_source_cache: bool = False,
    use_snapshot: bool = False,
    persist_snapshot: bool = True,
    allow_inline_build: bool = True,
    snapshot_built_from: str = "ui-live",
    sample_type: str = "PPS",
    allow_live_sample_status: bool = True,
    allow_live_size_breakdown: bool = True,
    require_current_source: bool = False,
    source_max_age_sec: int | float | None = None,
    manual_allocation_mode: str | None = None,
) -> dict:
    go_key = str(go or "").strip().upper()
    if not go_key:
        return _error("GO number required")

    manual_allocation_mode = _resolve_go_manual_allocation_mode(go_key, manual_allocation_mode)
    ensure_sql_snapshot_worker()
    _queue_snapshot_priority(go_key)
    force_live_source_refresh = bool((not use_snapshot) and not prefer_source_cache)

    head_preview = _load_go_head_fast(go_key, allow_live=(allow_inline_build or not use_snapshot))
    if isinstance(head_preview, dict) and _is_ignored_customer_code(head_preview.get("customer_code")):
        return _error("GO excluded by customer rule", go=go_key, customer_code=head_preview.get("customer_code"))

    source_readiness: dict = {}
    source_refreshed_for_request = False
    if require_current_source:
        source_readiness = ensure_go_source_cache_current(
            go_key,
            max_age_sec=source_max_age_sec,
            # Readiness is about live fabric quantities. Pulling PPO detail here
            # makes large GO requests slow; cached detail is still used by the
            # sheet rebuild, while received/on-way/order totals are refreshed.
            include_ppo_detail=False,
            force=False,
        )
        if not source_readiness.get("ok") or not source_readiness.get("current"):
            return _error(
                "SQL source cache is not current; refusing to serve stale fabric quantities",
                go=go_key,
                detail=source_readiness.get("detail") or source_readiness.get("error") or "",
                source_cache=source_readiness.get("source_cache") or {},
            )
        source_refreshed_for_request = bool(source_readiness.get("refreshed"))
        prefer_source_cache = True
        force_live_source_refresh = False

    if use_snapshot:
        cached = _load_persisted_sheet_payload(go_key)
        cache_profile = _load_go_cache_profile(go_key)
        if isinstance(cached, dict) and cached.get("ok") and _snapshot_matches_head(cached, head_preview):
            cached_mode = _normalize_manual_allocation_mode((cached.get("summary") or {}).get("manual_allocation_mode"))
            # The UI must not block on the slow received/FOC SQL view merely to
            # display an already versioned sheet.  Source polling and the
            # priority queue continue in the background, while a PPO override
            # takes the targeted live-SQL path from the edit endpoint.
            if not require_current_source and cached_mode == manual_allocation_mode:
                cached.setdefault("snapshot", {})["fast_cache_served"] = True
                cached.setdefault("snapshot", {})["background_refresh_queued"] = True
                return _attach_sample_status_to_payload(
                    cached,
                    go_key,
                    sample_type=sample_type,
                    # PPS is a small direct lookup and must not remain blank
                    # simply because the rest of the sheet is served fast
                    # from SQLite.
                    allow_live=allow_live_sample_status,
                )
            if cached_mode != manual_allocation_mode:
                if _can_build_sheet_from_sqlite_source_cache(go_key):
                    prefer_source_cache = True
                    allow_inline_build = True
                    cached.setdefault("snapshot", {})["manual_allocation_mode_changed"] = {
                        "cached": cached_mode,
                        "requested": manual_allocation_mode,
                    }
                elif not allow_inline_build:
                    _start_inline_snapshot_build(go_key)
                    return _build_pending_sheet_payload(go_key, head_preview, cached)
            if cache_profile:
                cached["cache_profile"] = cache_profile
            profile_flags = {
                str(item or "").strip().upper()
                for item in (cache_profile or {}).get("flags", [])
                if str(item or "").strip()
            }
            profile_received_mismatch = "SOURCE_MISMATCH_RECEIVED" in profile_flags
            profile_refresh_needed = _cache_profile_requires_source_refresh(cache_profile)
            active_ready_refresh_needed = _active_ready_snapshot_refresh_due(cache_profile)
            has_received_gap = _snapshot_has_received_gap(cached)
            source_cache_newer, source_cache_newer_info = _snapshot_source_cache_newer(go_key, cached)
            source_received_changed, source_received_info = _snapshot_source_cache_received_changed(go_key, cached)
            live_received_changed = (
                _snapshot_sql_received_positive(go_key, head_preview, cached)
                if has_received_gap and not source_received_changed
                else False
            )
            source_live_changed = False
            source_live_info: dict = {}
            cached_summary = cached.get("summary") if isinstance(cached.get("summary"), dict) else {}
            cached_received_total = _to_float(cached_summary.get("total_received_qty"))
            cached_required_total = _to_float(cached_summary.get("total_required_qty"))
            if (
                not source_received_changed
                and not live_received_changed
                and (
                    profile_refresh_needed
                    or has_received_gap
                    or str((cache_profile or {}).get("state") or "").strip().upper() != _CACHE_READY_STATE
                    or (cached_required_total > 0 and cached_received_total < cached_required_total)
                )
            ):
                source_live_changed, source_live_info = _source_cache_live_received_changed(go_key)
            received_refresh_needed = source_received_changed or live_received_changed
            refresh_needed = (
                _is_cache_refresh_due(cache_profile)
                or profile_refresh_needed
                or received_refresh_needed
                or source_live_changed
                or source_cache_newer
                or source_refreshed_for_request
                or active_ready_refresh_needed
            )
            if refresh_needed:
                _start_inline_snapshot_build(go_key)
                cached.setdefault("snapshot", {})["background_refresh_queued"] = True
            if (profile_refresh_needed or has_received_gap) and not allow_inline_build:
                if profile_received_mismatch:
                    prefer_source_cache = False
                    force_live_source_refresh = True
                    allow_inline_build = True
                    cached.setdefault("snapshot", {})["live_source_rebuild"] = True
                elif _can_build_sheet_from_sqlite_source_cache(go_key):
                    prefer_source_cache = True
                    allow_inline_build = True
                    cached.setdefault("snapshot", {})["sqlite_source_rebuild"] = True
            if active_ready_refresh_needed:
                cached.setdefault("snapshot", {})["active_refresh_needed"] = True
            if source_cache_newer or source_refreshed_for_request:
                snapshot_meta = cached.setdefault("snapshot", {})
                snapshot_meta["source_refresh_needed"] = True
                snapshot_meta["source_cache_newer"] = source_cache_newer_info
                snapshot_meta["source_verified"] = source_readiness.get("source_cache") or {}
                # Source tables can change without GO header timestamps changing.
                # Rebuild from verified SQLite source cache before showing fabric qty.
                prefer_source_cache = True
                allow_inline_build = True
            if source_received_changed:
                snapshot_meta = cached.setdefault("snapshot", {})
                snapshot_meta["source_refresh_needed"] = True
                snapshot_meta["received_source_cache_changed"] = source_received_info
                # SQLite already has newer SQL received rows. Rebuild synchronously from
                # source cache so the UI does not display stale warehouse quantities.
                prefer_source_cache = True
                allow_inline_build = True
            elif source_live_changed:
                snapshot_meta = cached.setdefault("snapshot", {})
                snapshot_meta["source_refresh_needed"] = True
                snapshot_meta["received_live_sql_changed"] = source_live_info
                # Source cache itself is stale. Rebuild synchronously from SQL Server,
                # then persist the fresh received rows back to both SQLite stores.
                prefer_source_cache = False
                force_live_source_refresh = True
                allow_inline_build = True
            elif live_received_changed:
                cached.setdefault("snapshot", {})["source_refresh_needed"] = True
                # Corrupted snapshot (received=0 while SQL has stock): force a one-shot
                # inline rebuild so UI does not keep showing fake "no fabric" values.
                prefer_source_cache = False
                force_live_source_refresh = True
                allow_inline_build = True
            elif has_received_gap and not allow_inline_build:
                cached.setdefault("snapshot", {})["source_refresh_needed"] = True
                _start_inline_snapshot_build(go_key)
            elif profile_refresh_needed and not allow_inline_build:
                pending = _build_pending_sheet_payload(go_key, head_preview, cached)
                pending.setdefault("snapshot", {})["source_refresh_needed"] = True
                return pending
            if not allow_inline_build:
                if source_readiness:
                    cached.setdefault("snapshot", {})["source_verified"] = source_readiness.get("source_cache") or {}
                return _attach_sample_status_to_payload(
                    cached,
                    go_key,
                    sample_type,
                    allow_live=allow_live_sample_status,
                )
        if not allow_inline_build:
            source_live_changed, source_live_info = _source_cache_live_received_changed(go_key)
            if source_live_changed:
                prefer_source_cache = False
                force_live_source_refresh = True
                allow_inline_build = True
                if isinstance(cached, dict):
                    cached.setdefault("snapshot", {})["received_source_cache_changed"] = source_live_info
            elif _can_build_sheet_from_sqlite_source_cache(go_key):
                prefer_source_cache = True
                allow_inline_build = True
            else:
                _start_inline_snapshot_build(go_key)
                return _build_pending_sheet_payload(go_key, head_preview, cached)

    payload = _build_live_coi_sheet_impl(
        go_key,
        prefer_mes_cache=prefer_mes_cache,
        allow_live_mes=allow_live_mes,
        allow_live_go_report=allow_live_go_report,
        allow_slow_sql_enrichment=allow_slow_sql_enrichment,
        prefer_source_cache=prefer_source_cache,
        force_live_source_refresh=force_live_source_refresh,
        sample_type=sample_type,
        allow_live_sample_status=allow_live_sample_status,
        allow_live_size_breakdown=allow_live_size_breakdown,
        manual_allocation_mode=manual_allocation_mode,
    )
    if payload.get("ok"):
        if source_readiness:
            payload.setdefault("snapshot", {})["source_verified"] = source_readiness.get("source_cache") or {}
        if persist_snapshot:
            _save_sheet_snapshot(go_key, payload, built_from=snapshot_built_from)
    elif persist_snapshot:
        _mark_go_cache_error(go_key, str(payload.get("error") or "Unknown build error"), built_from=snapshot_built_from)
    return _attach_sample_status_to_payload(
        payload,
        go_key,
        sample_type,
        allow_live=allow_live_sample_status,
    )





















