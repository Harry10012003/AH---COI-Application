from __future__ import annotations

import time
import unittest
from unittest import mock

from backend.engine import sql_live_engine as engine


class AuthoritativeReceivedContractTests(unittest.TestCase):
    def test_authoritative_zero_is_not_replaced_by_positive_raw_grn(self) -> None:
        authoritative_zero = {
            "ppo_no": "PPO-001",
            "fabric_type": "M2",
            "combo_name": "01@NAVY",
            "received_qty": 0.0,
            "foc_qty": 0.0,
            "source_view": "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV",
        }
        raw_grn = {
            "ppo_no": "PPO-001",
            "fabric_type": "M2",
            "combo_name": "01@NAVY",
            "received_qty": 125.0,
            "foc_qty": 0.0,
            "source_view": engine._FABRIC_GRN_FALLBACK_VIEW,
            "is_grn_fallback": 1,
        }

        with mock.patch.object(
            engine,
            "_load_grn_received_fallback_rows",
            return_value=[raw_grn],
        ):
            merged = engine._merge_grn_received_fallback_rows(
                mock.Mock(),
                [authoritative_zero.copy()],
                ["PPO-001"],
            )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["received_qty"], 0.0)
        self.assertEqual(merged[0]["foc_qty"], 0.0)
        self.assertEqual(
            merged[0]["source_view"],
            "dbo.V_F_RCV_FOC_QTY_FOR_WO_EGV",
        )
        self.assertNotIn("is_grn_fallback", merged[0])

    def test_find_received_row_distinguishes_miss_from_confirmed_zero(self) -> None:
        authoritative_zero = {
            "ppo_no": "PPO-001",
            "fabric_type": "M2",
            "combo_name": "01@NAVY",
            "received_qty": 0.0,
            "foc_qty": 0.0,
            "source_combo_key": "01NAVY",
        }
        lookup = {
            ("PPO-001", "M2", "01NAVY"): authoritative_zero,
        }

        matched = engine._find_received_row(
            lookup,
            "PPO-001",
            "M2",
            "01@NAVY",
            "01",
        )
        missing = engine._find_received_row(
            lookup,
            "PPO-001",
            "M3",
            "02@RED",
            "02",
        )

        self.assertIs(matched, authoritative_zero)
        self.assertEqual(engine._display_received_qty(matched), 0.0)
        self.assertIsNone(missing)

    def test_confirmed_zero_is_not_replaced_from_another_m_family_type(self) -> None:
        received_rows = [
            {
                "ppo_no": "PPO-001",
                "fabric_type": "M2",
                "combo_name": "01@NAVY",
                "received_qty": 0.0,
                "foc_qty": 0.0,
            },
            {
                "ppo_no": "PPO-001",
                "fabric_type": "M3",
                "combo_name": "01@NAVY",
                "received_qty": 100.0,
                "foc_qty": 0.0,
            },
        ]
        order_totals = {
            ("PPO-001", "M3", ""): {
                "ppo_order_qty": 100.0,
            },
        }

        fallback, fallback_key = engine._find_m_family_near_order_received_row(
            received_rows,
            "PPO-001",
            "M2",
            "01@NAVY",
            "01",
            order_totals,
            100.0,
            set(),
        )

        self.assertIsNone(fallback_key)
        self.assertEqual(engine._display_received_qty(fallback), 0.0)


class ShipmentLastKnownGoodContractTests(unittest.TestCase):
    def setUp(self) -> None:
        with engine._shipment_on_way_cache_lock:
            self._original_cache = dict(engine._shipment_on_way_cache)
            engine._shipment_on_way_cache.clear()

    def tearDown(self) -> None:
        with engine._shipment_on_way_cache_lock:
            engine._shipment_on_way_cache.clear()
            engine._shipment_on_way_cache.update(self._original_cache)

    def test_query_error_returns_expired_last_known_good_rows(self) -> None:
        database, table_name, _factory = engine._shipment_source_for_factory("EGV")
        source_key = engine._shipment_source_key(database, table_name)
        cache_key = (source_key, ("PPO-001",))
        last_known_good = [
            {
                "ppo_no": "PPO-001",
                "fabric_type": "M2",
                "combo_name": "01@NAVY",
                "shipment_qty": 80.0,
                "foc_qty": 0.0,
                "eta_date": "2026-07-30 00:00:00",
                "ship_type": "AIR",
                "source_factory": "EGV",
                "source_table": f"{database}.{table_name}",
            }
        ]
        with engine._shipment_on_way_cache_lock:
            engine._shipment_on_way_cache[cache_key] = {
                "ts": time.time() - engine._SHIPMENT_ON_WAY_CACHE_TTL_SEC - 1,
                "rows": last_known_good,
                "error": "",
            }

        with mock.patch.object(
            engine,
            "_connect_shipment",
            side_effect=RuntimeError("temporary shipment timeout"),
        ):
            rows, actual_source_key, _source_table, error = (
                engine._load_shipment_on_way_rows("EGV", ["PPO-001"])
            )

        self.assertEqual(actual_source_key, source_key)
        self.assertEqual(rows, last_known_good)
        self.assertIn("RuntimeError", error)
        with engine._shipment_on_way_cache_lock:
            self.assertEqual(
                engine._shipment_on_way_cache[cache_key]["rows"],
                last_known_good,
            )

    def test_successful_empty_query_replaces_last_known_good_rows(self) -> None:
        database, table_name, _factory = engine._shipment_source_for_factory("EGV")
        source_key = engine._shipment_source_key(database, table_name)
        cache_key = (source_key, ("PPO-001",))
        with engine._shipment_on_way_cache_lock:
            engine._shipment_on_way_cache[cache_key] = {
                "ts": time.time() - engine._SHIPMENT_ON_WAY_CACHE_TTL_SEC - 1,
                "rows": [{"ppo_no": "PPO-001", "shipment_qty": 80.0}],
                "error": "",
            }

        cursor = mock.Mock()
        cursor.description = []
        cursor.fetchall.return_value = []
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor

        with mock.patch.object(
            engine,
            "_connect_shipment",
            return_value=connection,
        ):
            rows, _actual_source_key, _source_table, error = (
                engine._load_shipment_on_way_rows("EGV", ["PPO-001"])
            )

        self.assertEqual(rows, [])
        self.assertEqual(error, "")
        with engine._shipment_on_way_cache_lock:
            self.assertEqual(engine._shipment_on_way_cache[cache_key]["rows"], [])
            self.assertEqual(engine._shipment_on_way_cache[cache_key]["error"], "")


if __name__ == "__main__":
    unittest.main()
