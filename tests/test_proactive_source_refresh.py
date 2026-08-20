from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from backend.engine import sql_live_engine as engine


class ProactiveSourceRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = engine._SNAPSHOT_DB
        self.original_schema_ready = engine._snapshot_schema_ready
        engine._SNAPSHOT_DB = Path(self.temp_dir.name) / "source-refresh.db"
        engine._snapshot_schema_ready = False
        engine._ensure_snapshot_tables()
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO go_feed (
                    go_no, factory_code, create_date, modify_date, last_seen_at,
                    cache_state, cache_flags, cache_reason
                )
                VALUES ('GO-001', 'EGV', '2026-07-01', '2026-07-01', '2026-07-01',
                        'READY', '[]', '')
                """
            )
            conn.execute(
                """
                INSERT INTO sql_received_foc (
                    view_name, ppo_no, fabric_type, combo_name,
                    received_qty, foc_qty, synced_at
                )
                VALUES ('dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV', 'PPO-001', 'M2',
                        '01@NAVY', 125, 0, '2026-07-01 00:00:00')
                """
            )
            conn.commit()

    def tearDown(self) -> None:
        engine._SNAPSHOT_DB = self.original_db
        engine._snapshot_schema_ready = self.original_schema_ready
        self.temp_dir.cleanup()

    def test_downward_received_correction_is_persisted_and_go_is_queued(self) -> None:
        scope = {
            "GO-001": {
                "go_no": "GO-001",
                "factory_code": "EGV",
                "ppos": {"PPO-001"},
                "last_checked_at": "",
                "has_error": False,
            }
        }
        received_rows = [
            {
                "ppo_no": "PPO-001",
                "fabric_type": "M2",
                "combo_name": "01@NAVY",
                "received_qty": 80.0,
                "foc_qty": 0.0,
            }
        ]
        sql_connection = mock.MagicMock()
        sql_connection.__enter__.return_value = sql_connection
        queued: list[str] = []

        with (
            mock.patch.object(engine, "_active_source_refresh_scope", return_value=(scope, scope)),
            mock.patch.object(engine, "_connect", return_value=sql_connection),
            mock.patch.object(
                engine,
                "_load_received_foc_rows",
                return_value=(received_rows, "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV"),
            ),
            mock.patch.object(
                engine,
                "_load_stock_balance_rows",
                return_value=(
                    [
                        {
                            "ppo_no": "PPO-001",
                            "fabric_type": "M2",
                            "combo_name": "01@NAVY",
                            "size_code": "",
                            "on_hand_qty": 80.0,
                            "allocated_qty": 0.0,
                            "reserved_qty": 0.0,
                        }
                    ],
                    "dbo.V_INV_STOCK",
                    "",
                ),
            ),
            mock.patch.object(
                engine,
                "_load_shipment_on_way_rows",
                return_value=([], "SHIP:EGV", "DB.TABLE", ""),
            ),
            mock.patch.object(engine, "_queue_snapshot_priority", side_effect=queued.append),
        ):
            result = engine._refresh_active_source_cache_once()

        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_go_count"], 1)
        self.assertEqual(queued, ["GO-001"])
        with engine._snapshot_connect() as conn:
            received = conn.execute(
                """
                SELECT received_qty
                FROM sql_received_foc
                WHERE view_name = 'dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV'
                  AND ppo_no = 'PPO-001'
                """
            ).fetchone()
            feed = conn.execute(
                "SELECT cache_state, cache_flags FROM go_feed WHERE go_no = 'GO-001'"
            ).fetchone()
        self.assertEqual(received["received_qty"], 80.0)
        self.assertEqual(feed["cache_state"], "WAIT_SOURCE")
        self.assertIn("SOURCE_DATA_CHANGED", engine._split_cache_flags(feed["cache_flags"]))

    def test_active_scope_counts_current_eta_rule_as_verified(self) -> None:
        checked_at = engine._snapshot_now()
        with engine._snapshot_connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_go_head (
                    go_no, factory_code, create_date, modify_date, synced_at
                )
                VALUES ('GO-001', 'EGV', ?, ?, ?)
                """,
                (checked_at, checked_at, checked_at),
            )
            conn.execute(
                """
                INSERT INTO sql_go_ppo_mapping (
                    go_no, row_index, ppo_no, lot_no, synced_at
                )
                VALUES ('GO-001', 1, 'PPO-001', 1, ?)
                """,
                (checked_at,),
            )
            conn.executemany(
                """
                INSERT INTO sql_source_sync (
                    source_key, synced_at, last_checked_at,
                    row_count, source_status, last_error
                )
                VALUES (?, ?, ?, 0, 'OK', '')
                """,
                [
                    ("RECEIVED:VIEW:GO-001", checked_at, checked_at),
                    ("STOCK_BALANCE:dbo.V_INV_STOCK:GO-001", checked_at, checked_at),
                    ("SHIPMENT_ON_WAY:SHIP:EGV:GO-001", checked_at, checked_at),
                    (
                        f"SHIPMENT_ON_WAY_ETA_V{engine._SHIPMENT_ETA_RULE_VERSION}:SHIP:EGV:GO-001",
                        checked_at,
                        checked_at,
                    ),
                ],
            )
            conn.commit()

        scope, _selected = engine._active_source_refresh_scope()

        self.assertTrue(scope["GO-001"]["verification_complete"])
        self.assertFalse(scope["GO-001"]["has_error"])
        self.assertEqual(scope["GO-001"]["last_checked_at"], checked_at)


if __name__ == "__main__":
    unittest.main()
