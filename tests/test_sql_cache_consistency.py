from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from backend.engine import live_sheet_store
from backend.engine import sql_live_engine as engine


class SqlCacheConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_snapshot_db = engine._SNAPSHOT_DB
        self.original_schema_ready = engine._snapshot_schema_ready
        self.original_store_db = live_sheet_store.LIVE_SHEET_STORE_DB
        self.original_full_sync_at = engine._snapshot_worker_state.get("last_full_go_feed_sync_at")

        engine._SNAPSHOT_DB = Path(self.temp_dir.name) / "snapshot.db"
        engine._snapshot_schema_ready = False
        live_sheet_store.LIVE_SHEET_STORE_DB = Path(self.temp_dir.name) / "sheet-store.db"
        engine._ensure_snapshot_tables()

    def tearDown(self) -> None:
        engine._SNAPSHOT_DB = self.original_snapshot_db
        engine._snapshot_schema_ready = self.original_schema_ready
        live_sheet_store.LIVE_SHEET_STORE_DB = self.original_store_db
        with engine._snapshot_worker_lock:
            engine._snapshot_worker_state["last_full_go_feed_sync_at"] = self.original_full_sync_at
        self.temp_dir.cleanup()

    def _insert_feed_and_staged_head(
        self,
        *,
        go: str = "GO-001",
        feed_stamp: str,
        staged_stamp: str,
        with_ppo: bool = False,
    ) -> None:
        now_text = engine._snapshot_now()
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, style_no, status, create_date,
                    modify_date, last_seen_at
                )
                VALUES (?, 'EGV', 'STYLE-FEED', 'OPEN', ?, ?, ?)
                """,
                (go, feed_stamp, feed_stamp, now_text),
            )
            conn.execute(
                """
                INSERT INTO sql_go_head (
                    go_no, factory_code, style_no, status, create_date,
                    modify_date, synced_at
                )
                VALUES (?, 'EGV', 'STYLE-STAGED', 'OPEN', ?, ?, ?)
                """,
                (go, staged_stamp, staged_stamp, now_text),
            )
            if with_ppo:
                conn.execute(
                    """
                    INSERT INTO sql_go_ppo_mapping (
                        go_no, row_index, ppo_no, lot_no, synced_at
                    )
                    VALUES (?, 0, 'PPO-001', 1, ?)
                    """,
                    (go, now_text),
                )
            conn.commit()

    @staticmethod
    def _payload(
        *,
        marker: str,
        source_stamp: str,
        build_started_ns: int,
        received_qty: float = 100.0,
    ) -> dict:
        return {
            "ok": True,
            "go": "GO-001",
            "factory_code": "EGV",
            "style_no": "STYLE-1",
            "style_desc": "",
            "head": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "create_date": source_stamp,
                "modify_date": source_stamp,
            },
            "rows": [{"_row_key": "ROW-1", "marker": marker}],
            "row_count": 1,
            "summary": {
                "rows": 1,
                "total_required_qty": 120.0,
                "total_received_qty": received_qty,
            },
            "cache_profile": {
                "state": "READY",
                "flags": [],
                "reason": "",
            },
            "snapshot": {
                "version": engine._SNAPSHOT_PAYLOAD_VERSION,
                "build_started_ns": build_started_ns,
            },
        }

    def test_staged_topology_older_than_feed_is_not_served_as_current(self) -> None:
        self._insert_feed_and_staged_head(
            feed_stamp="2026-07-27 09:05:00",
            staged_stamp="2026-07-27 09:00:00",
            with_ppo=True,
        )
        checked_at = engine._snapshot_now()
        with engine._snapshot_connect() as conn:
            conn.executemany(
                """
                INSERT INTO sql_source_sync (
                    source_key, synced_at, last_checked_at,
                    row_count, source_status, last_error
                )
                VALUES (?, ?, ?, 1, 'OK', '')
                """,
                [
                    ("GO:GO-001", checked_at, checked_at),
                    ("RECEIVED:VIEW:GO-001", checked_at, checked_at),
                    ("SHIPMENT_ON_WAY:SHIP:GO-001", checked_at, checked_at),
                    (
                        f"SHIPMENT_ON_WAY_ETA_V{engine._SHIPMENT_ETA_RULE_VERSION}:SHIP:GO-001",
                        checked_at,
                        checked_at,
                    ),
                ],
            )
            conn.commit()

        head = engine._load_go_head_fast("GO-001", allow_live=False)
        staged = engine._load_cached_go_source_bundle("GO-001")
        source_current, source_meta = engine._go_source_cache_is_current(
            "GO-001",
            max_age_sec=3600,
        )
        scope, selected = engine._active_source_refresh_scope()

        self.assertEqual(head["style_no"], "STYLE-FEED")
        self.assertFalse(staged["ok"])
        self.assertTrue(staged["topology_stale"])
        self.assertFalse(source_current)
        self.assertFalse(source_meta["topology_current"])
        self.assertFalse(source_meta["complete"])
        self.assertFalse(scope["GO-001"]["topology_current"])
        self.assertFalse(scope["GO-001"]["verification_complete"])
        self.assertNotIn("GO-001", selected)

    def test_older_build_cannot_overwrite_newer_snapshot_or_sheet_store(self) -> None:
        newer = self._payload(
            marker="newer",
            source_stamp="2026-07-27 09:05:00",
            build_started_ns=200,
            received_qty=125.0,
        )
        older = self._payload(
            marker="older",
            source_stamp="2026-07-27 09:00:00",
            build_started_ns=100,
            received_qty=200.0,
        )
        correction = self._payload(
            marker="correction",
            source_stamp="2026-07-27 09:05:00",
            build_started_ns=300,
            received_qty=80.0,
        )

        self.assertTrue(engine._save_sheet_snapshot("GO-001", newer, "newer-build"))
        self.assertFalse(engine._save_sheet_snapshot("GO-001", older, "late-old-build"))
        snapshot = engine._load_sheet_snapshot("GO-001")
        sheet_store_payload = live_sheet_store.load_live_sheet_payload("GO-001")
        self.assertEqual(snapshot["rows"][0]["marker"], "newer")
        self.assertEqual(sheet_store_payload["rows"][0]["marker"], "newer")

        # A later authoritative warehouse correction may decrease quantities.
        self.assertTrue(engine._save_sheet_snapshot("GO-001", correction, "correction"))
        corrected = live_sheet_store.load_live_sheet_payload("GO-001")
        self.assertEqual(corrected["summary"]["total_received_qty"], 80.0)

    def test_pending_payload_preserves_cache_reason(self) -> None:
        now_text = engine._snapshot_now()
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, status, create_date, modify_date,
                    last_seen_at, cache_state, cache_reason
                )
                VALUES ('GO-PENDING', 'EGV', 'NEW', ?, ?, ?, 'WAIT_PPO', ?)
                """,
                (now_text, now_text, now_text, "no PPO/fabric rows in SQL source yet"),
            )
            conn.commit()

        payload = engine._build_pending_sheet_payload("GO-PENDING", {"factory_code": "EGV"})

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["cache_profile"]["state"], "WAIT_PPO")
        self.assertEqual(payload["cache_profile"]["reason"], "no PPO/fabric rows in SQL source yet")

    def test_source_errors_are_classified_without_exposing_sql_detail(self) -> None:
        collation = engine.classify_source_error(
            "ProgrammingError: Cannot resolve the collation conflict in dbo.V_ESCM_ORDER_COLORSIZE_SALES"
        )
        unavailable = engine.classify_source_error("DBPROCESS is dead or not enabled")
        self.assertEqual(collation["code"], "COLLATION_CONFLICT")
        self.assertNotIn("ProgrammingError", collation["message"])
        self.assertEqual(unavailable["code"], "SQL_UNAVAILABLE")

    def test_priority_seed_uses_color_audit_candidates_without_name_error(self) -> None:
        with (
            mock.patch.object(engine, "color_audit_priority_go_nos", return_value=["GO-AUDIT"]),
            mock.patch.object(engine, "_preload_lookback_cutoff", return_value=engine.datetime(2026, 1, 1)),
        ):
            seeded = engine._seed_snapshot_priorities(force=True)
        self.assertIn("GO-AUDIT", seeded)

    def test_priority_seed_continues_when_color_audit_is_unavailable(self) -> None:
        with mock.patch.object(engine, "color_audit_priority_go_nos", side_effect=RuntimeError("audit unavailable")):
            seeded = engine._seed_snapshot_priorities(force=True)
        self.assertEqual(seeded, [])

    def test_compatible_legacy_snapshot_is_copied_without_overwriting_current_cache(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "live_sheet_snapshot_v55.db"
        payload = self._payload(
            marker="legacy",
            source_stamp="2026-07-27 09:05:00",
            build_started_ns=10,
        )
        payload["snapshot"]["stock_balance_contract"] = engine._STOCK_BALANCE_CONTRACT_VERSION
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            """
            CREATE TABLE sheet_snapshots (
                go_no TEXT PRIMARY KEY, factory_code TEXT, style_no TEXT, style_desc TEXT,
                source_modify_date TEXT, row_count INTEGER, payload_version INTEGER,
                payload_json TEXT, updated_at TEXT, built_from TEXT, build_started_ns INTEGER
            )
            """
        )
        legacy.execute(
            "INSERT INTO sheet_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "GO-LEGACY", "EGV", "STYLE-1", "", "2026-07-27 09:05:00", 1,
                engine._SNAPSHOT_PAYLOAD_VERSION, json.dumps(payload), "2026-07-27 09:05:01",
                "legacy", 10,
            ),
        )
        legacy.commit()
        legacy.close()

        with engine._snapshot_connect() as conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (engine._SNAPSHOT_LEGACY_MIGRATION_KEY,))
            copied = engine._migrate_compatible_legacy_snapshots(conn)
            row = conn.execute("SELECT built_from FROM sheet_snapshots WHERE go_no = 'GO-LEGACY'").fetchone()

        self.assertEqual(copied, 1)
        self.assertEqual(row["built_from"], "legacy-migration:live_sheet_snapshot_v55.db")

    def test_same_go_build_decorator_serializes_threads(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        active = 0
        max_active = 0
        guard = threading.Lock()

        @engine._serialize_sheet_build
        def _work(go: str, label: str) -> None:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            if label == "first":
                first_entered.set()
                release_first.wait(2)
            else:
                second_entered.set()
            with guard:
                active -= 1

        first = threading.Thread(target=_work, args=("GO-001", "first"))
        second = threading.Thread(target=_work, args=("GO-001", "second"))
        first.start()
        self.assertTrue(first_entered.wait(1))
        second.start()
        time.sleep(0.05)
        self.assertFalse(second_entered.is_set())
        release_first.set()
        first.join(2)
        second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(max_active, 1)

    def test_changed_topology_precedes_cold_snapshot_backlog(self) -> None:
        self._insert_feed_and_staged_head(
            go="GO-COLD",
            feed_stamp="2026-07-27 09:05:00",
            staged_stamp="2026-07-27 09:05:00",
        )
        self._insert_feed_and_staged_head(
            go="GO-CHANGED",
            feed_stamp="2026-07-27 09:10:00",
            staged_stamp="2026-07-27 09:00:00",
        )
        changed_payload = self._payload(
            marker="old-topology",
            source_stamp="2026-07-27 09:00:00",
            build_started_ns=100,
        )
        changed_payload["go"] = "GO-CHANGED"
        changed_payload["head"]["go_no"] = "GO-CHANGED"
        self.assertTrue(
            engine._save_sheet_snapshot(
                "GO-CHANGED",
                changed_payload,
                "old-topology",
            )
        )
        feed_rows = [
            {
                "go_no": "GO-COLD",
                "create_date": "2026-07-27 09:05:00",
                "modify_date": "2026-07-27 09:05:00",
            },
            {
                "go_no": "GO-CHANGED",
                "create_date": "2026-07-27 09:10:00",
                "modify_date": "2026-07-27 09:10:00",
            },
        ]

        selected, stale_count = engine._select_stale_go_rows(
            feed_rows,
            batch_size=1,
            priority_go_nos=["GO-COLD"],
        )

        self.assertEqual(stale_count, 2)
        self.assertEqual(selected[0]["go_no"], "GO-CHANGED")

    def test_missing_topology_precedes_cold_snapshot_backlog(self) -> None:
        self._insert_feed_and_staged_head(
            go="GO-COLD",
            feed_stamp="2026-07-27 09:05:00",
            staged_stamp="2026-07-27 09:05:00",
        )
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, style_no, status,
                    create_date, modify_date, last_seen_at
                )
                VALUES (
                    'GO-MISSING', 'EGV', 'STYLE-MISSING', 'OPEN',
                    '2026-07-27 09:06:00', '2026-07-27 09:06:00', ?
                )
                """,
                (engine._snapshot_now(),),
            )
            conn.commit()
        feed_rows = [
            {
                "go_no": "GO-COLD",
                "create_date": "2026-07-27 09:05:00",
                "modify_date": "2026-07-27 09:05:00",
            },
            {
                "go_no": "GO-MISSING",
                "create_date": "2026-07-27 09:06:00",
                "modify_date": "2026-07-27 09:06:00",
            },
        ]

        selected, stale_count = engine._select_stale_go_rows(
            feed_rows,
            batch_size=1,
            priority_go_nos=["GO-COLD"],
        )

        self.assertEqual(stale_count, 2)
        self.assertEqual(selected[0]["go_no"], "GO-MISSING")

    def test_topology_only_stage_preserves_rows_and_invalidates_verification(self) -> None:
        stamp = "2026-07-27 09:05:00"
        self._insert_feed_and_staged_head(
            feed_stamp=stamp,
            staged_stamp=stamp,
            with_ppo=True,
        )
        checked_at = engine._snapshot_now()
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_received_foc (
                    view_name, ppo_no, fabric_type, combo_name,
                    received_qty, foc_qty, synced_at
                )
                VALUES ('VIEW', 'PPO-001', 'M2', 'NAVY', 125, 0, ?)
                """,
                (checked_at,),
            )
            conn.executemany(
                """
                INSERT INTO sql_source_sync (
                    source_key, synced_at, last_checked_at,
                    row_count, source_status, last_error
                )
                VALUES (?, ?, ?, 1, 'OK', '')
                """,
                [
                    ("RECEIVED:VIEW:GO-001", checked_at, checked_at),
                    ("SHIPMENT_ON_WAY:SHIP:GO-001", checked_at, checked_at),
                    (
                        f"SHIPMENT_ON_WAY_ETA_V{engine._SHIPMENT_ETA_RULE_VERSION}:SHIP:GO-001",
                        checked_at,
                        checked_at,
                    ),
                ],
            )
            conn.commit()
        bundle = {
            "ok": True,
            "head": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "style_no": "STYLE-NEW",
                "status": "OPEN",
                "create_date": stamp,
                "modify_date": stamp,
            },
            "colors": [],
            "lots": [],
            "jo_color_qty_rows": [],
            "ppo_mapping": [{"ppo_no": "PPO-001", "lot_no": 1}],
            "fabric_rows": [],
            "sql_bom_rows": [],
            "jo_ppo_yy_rows": [],
            "received_rows": [],
            "received_view": "VIEW",
            "shipment_on_way_rows": [],
            "shipment_source_key": "SHIP",
            "shipment_source_table": "DB.TABLE",
            "shipment_on_way_error": "",
            "volatile_sources_refreshed": False,
            "ppo_order_totals": {},
            "ppo_order_totals_refreshed": False,
            "ppo_detail_rows_by_ppo": {},
            "source_synced_at": checked_at,
        }

        self.assertTrue(engine._save_go_source_cache_bundle("GO-001", bundle))

        with engine._snapshot_connect() as conn:
            received = conn.execute(
                """
                SELECT received_qty
                FROM sql_received_foc
                WHERE view_name = 'VIEW' AND ppo_no = 'PPO-001'
                """
            ).fetchone()
            volatile_meta_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM sql_source_sync
                WHERE source_key LIKE 'RECEIVED:%:GO-001'
                   OR source_key LIKE 'SHIPMENT_ON_WAY:%:GO-001'
                   OR source_key LIKE 'SHIPMENT_ON_WAY_ETA_V%:%:GO-001'
                """
            ).fetchone()[0]
            go_meta = conn.execute(
                "SELECT source_status FROM sql_source_sync WHERE source_key = 'GO:GO-001'"
            ).fetchone()
        self.assertEqual(received["received_qty"], 125.0)
        self.assertEqual(volatile_meta_count, 0)
        self.assertEqual(go_meta["source_status"], "OK")

    def test_empty_live_topology_retains_last_known_good_rows(self) -> None:
        stamp = "2026-07-27 09:05:00"
        self._insert_feed_and_staged_head(
            feed_stamp=stamp,
            staged_stamp=stamp,
            with_ppo=True,
        )
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_go_fabric_rows (
                    go_no, row_index, lot_no, ppo_no, fabric_type,
                    combo_name, synced_at
                )
                VALUES ('GO-001', 0, 1, 'PPO-001', 'B', 'NAVY', ?)
                """,
                (engine._snapshot_now(),),
            )
            conn.commit()
        bundle = {
            "ok": True,
            "head": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "style_no": "STYLE-NEW",
                "status": "OPEN",
                "create_date": stamp,
                "modify_date": stamp,
            },
            "ppo_mapping": [],
            "fabric_rows": [],
            "sql_bom_rows": [],
            "jo_ppo_yy_rows": [],
            "volatile_sources_refreshed": True,
            "ppo_order_totals_refreshed": True,
            "source_synced_at": engine._snapshot_now(),
        }

        self.assertTrue(engine._save_go_source_cache_bundle("GO-001", bundle))

        with engine._snapshot_connect() as conn:
            ppo_rows = conn.execute(
                "SELECT ppo_no FROM sql_go_ppo_mapping WHERE go_no = 'GO-001'"
            ).fetchall()
            fabric_rows = conn.execute(
                "SELECT ppo_no, combo_name FROM sql_go_fabric_rows WHERE go_no = 'GO-001'"
            ).fetchall()
            go_meta = conn.execute(
                """
                SELECT source_status, last_error
                FROM sql_source_sync
                WHERE source_key = 'GO:GO-001'
                """
            ).fetchone()

        self.assertEqual([row["ppo_no"] for row in ppo_rows], ["PPO-001"])
        self.assertEqual(
            [(row["ppo_no"], row["combo_name"]) for row in fabric_rows],
            [("PPO-001", "NAVY")],
        )
        self.assertEqual(bundle["source_mode"], "sqlite-source-cache")
        self.assertFalse(bundle["volatile_sources_refreshed"])
        self.assertEqual(go_meta["source_status"], "ERROR")
        self.assertIn("SOURCE_INCOMPLETE", go_meta["last_error"])

    def test_complete_live_topology_replaces_cached_rows(self) -> None:
        stamp = "2026-07-27 09:05:00"
        self._insert_feed_and_staged_head(
            feed_stamp=stamp,
            staged_stamp=stamp,
            with_ppo=True,
        )
        bundle = {
            "ok": True,
            "head": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "style_no": "STYLE-NEW",
                "status": "OPEN",
                "create_date": stamp,
                "modify_date": stamp,
            },
            "ppo_mapping": [{"ppo_no": "PPO-NEW", "lot_no": 2}],
            "fabric_rows": [
                {
                    "lot_no": 2,
                    "ppo_no": "PPO-NEW",
                    "fabric_type": "M",
                    "combo_name": "WHITE",
                }
            ],
            "sql_bom_rows": [],
            "jo_ppo_yy_rows": [],
            "volatile_sources_refreshed": False,
            "ppo_order_totals_refreshed": False,
            "source_synced_at": engine._snapshot_now(),
        }

        self.assertTrue(engine._save_go_source_cache_bundle("GO-001", bundle))

        with engine._snapshot_connect() as conn:
            ppo_rows = conn.execute(
                "SELECT ppo_no FROM sql_go_ppo_mapping WHERE go_no = 'GO-001'"
            ).fetchall()
            go_meta = conn.execute(
                "SELECT source_status FROM sql_source_sync WHERE source_key = 'GO:GO-001'"
            ).fetchone()
        self.assertEqual([row["ppo_no"] for row in ppo_rows], ["PPO-NEW"])
        self.assertEqual(go_meta["source_status"], "OK")

    def test_partial_live_topology_cannot_drop_only_ppo_mapping(self) -> None:
        stamp = "2026-07-27 09:05:00"
        self._insert_feed_and_staged_head(
            feed_stamp=stamp,
            staged_stamp=stamp,
            with_ppo=True,
        )
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_go_fabric_rows (
                    go_no, row_index, lot_no, ppo_no, fabric_type,
                    combo_name, synced_at
                )
                VALUES ('GO-001', 0, 1, 'PPO-001', 'B', 'NAVY', ?)
                """,
                (engine._snapshot_now(),),
            )
            conn.commit()
        bundle = {
            "ok": True,
            "head": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "style_no": "STYLE-NEW",
                "status": "OPEN",
                "create_date": stamp,
                "modify_date": stamp,
            },
            "ppo_mapping": [],
            "fabric_rows": [
                {
                    "lot_no": 2,
                    "ppo_no": "PPO-PARTIAL",
                    "fabric_type": "M",
                    "combo_name": "WHITE",
                }
            ],
            "sql_bom_rows": [],
            "jo_ppo_yy_rows": [],
            "volatile_sources_refreshed": False,
            "ppo_order_totals_refreshed": False,
            "source_synced_at": engine._snapshot_now(),
        }

        self.assertTrue(engine._save_go_source_cache_bundle("GO-001", bundle))

        with engine._snapshot_connect() as conn:
            ppo_rows = conn.execute(
                "SELECT ppo_no FROM sql_go_ppo_mapping WHERE go_no = 'GO-001'"
            ).fetchall()
            fabric_rows = conn.execute(
                "SELECT ppo_no FROM sql_go_fabric_rows WHERE go_no = 'GO-001'"
            ).fetchall()
        self.assertEqual([row["ppo_no"] for row in ppo_rows], ["PPO-001"])
        self.assertEqual([row["ppo_no"] for row in fabric_rows], ["PPO-001"])
        self.assertIn("ppo_mapping", bundle["source_live_error"])

    def test_topology_only_load_skips_slow_received_and_shipment_queries(self) -> None:
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = mock.MagicMock()
        with (
            mock.patch.object(engine, "_connect", return_value=connection),
            mock.patch.object(
                engine,
                "_load_go_head",
                return_value={"go_no": "GO-001", "factory_code": "EGV"},
            ),
            mock.patch.object(engine, "_load_go_colors", return_value=[]),
            mock.patch.object(engine, "_load_go_lots", return_value=[]),
            mock.patch.object(engine, "_load_go_jo_color_qty", return_value=[]),
            mock.patch.object(
                engine,
                "_load_go_ppo_mapping",
                return_value=[{"ppo_no": "PPO-001", "lot_no": 1}],
            ),
            mock.patch.object(engine, "_load_go_fabric_rows", return_value=[]),
            mock.patch.object(engine, "_load_go_sql_bom_rows", return_value=[]),
            mock.patch.object(engine, "_load_jo_ppo_yy", return_value=[]),
            mock.patch.object(
                engine,
                "_load_received_foc_rows",
                side_effect=AssertionError("slow received query must be skipped"),
            ) as received,
            mock.patch.object(
                engine,
                "_load_shipment_on_way_rows",
                side_effect=AssertionError("shipment query must be skipped"),
            ) as shipment,
        ):
            result = engine._load_live_go_source_bundle(
                "GO-001",
                include_order_totals=False,
                include_volatile_sources=False,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["volatile_sources_refreshed"])
        received.assert_not_called()
        shipment.assert_not_called()

    def test_standby_monitor_retries_until_process_lease_is_available(self) -> None:
        acquired = threading.Event()
        calls = 0

        def _retry() -> bool:
            nonlocal calls
            calls += 1
            if calls >= 2:
                acquired.set()
                return True
            return False

        with engine._snapshot_worker_lock:
            engine._snapshot_worker_state["lease_monitor_thread"] = None
            engine._snapshot_worker_state["lease_monitor_running"] = False
        with (
            mock.patch.object(engine, "_retry_worker_process_lease_once", side_effect=_retry),
            mock.patch.object(engine.time, "sleep", return_value=None),
        ):
            engine._ensure_worker_lease_monitor()
            self.assertTrue(acquired.wait(1))
            with engine._snapshot_worker_lock:
                monitor = engine._snapshot_worker_state["lease_monitor_thread"]
            monitor.join(1)

        self.assertGreaterEqual(calls, 2)
        self.assertFalse(monitor.is_alive())

    def test_warmup_reports_active_go_with_missing_ppo_as_incomplete(self) -> None:
        now_text = engine._snapshot_now()
        self._insert_feed_and_staged_head(
            feed_stamp=now_text,
            staged_stamp=now_text,
            with_ppo=False,
        )
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, style_no, status,
                    create_date, modify_date, last_seen_at
                )
                VALUES ('GO-OTHER', 'OTHER', 'STYLE-X', 'OPEN', ?, ?, ?)
                """,
                (now_text, now_text, now_text),
            )
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, style_no, status, customer_code,
                    create_date, modify_date, last_seen_at
                )
                VALUES ('GO-IGNORED', 'EGV', 'STYLE-Y', 'OPEN', '36086', ?, ?, ?)
                """,
                (now_text, now_text, now_text),
            )
            conn.commit()
        with engine._snapshot_worker_lock:
            engine._snapshot_worker_state["last_full_go_feed_sync_at"] = now_text

        scope, selected = engine._active_source_refresh_scope()
        status = engine.sql_snapshot_status()

        self.assertIn("GO-001", scope)
        self.assertFalse(scope["GO-001"]["has_ppo"])
        self.assertFalse(scope["GO-001"]["verification_complete"])
        self.assertNotIn("GO-001", selected)
        self.assertEqual(status["source_missing_ppo_count"], 1)
        self.assertEqual(status["source_uncurrent_go_count"], 1)
        self.assertEqual(status["preload_feed_count"], 1)
        self.assertEqual(status["preload_staged_missing_count"], 0)
        self.assertFalse(status["warmup_complete"])
        self.assertEqual(
            status["warmup_scope"]["volatile_source_lookback_days"],
            engine._SOURCE_REFRESH_LOOKBACK_DAYS,
        )


if __name__ == "__main__":
    unittest.main()
