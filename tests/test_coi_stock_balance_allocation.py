from __future__ import annotations

import unittest
from unittest import mock

from backend.engine import sql_live_engine as engine


class CoiStockBalanceAllocationTests(unittest.TestCase):
    def test_on_hand_is_the_net_physical_balance_not_wms_reservations(self) -> None:
        # Allocation must deduct actual stock/SR sample issues. V_INV_STOCK's
        # ON_HAND_QTY already does that; WMS Allocated/Reserved are planning
        # fields and must not be deducted a second time.
        self.assertEqual(
            engine._display_stock_on_hand_qty(
                {"on_hand_qty": 84, "allocated_qty": 20, "reserved_qty": 12}
            ),
            84.0,
        )
        self.assertEqual(engine._display_stock_on_hand_qty({"on_hand_qty": -3}), 0.0)

    def test_flatknit_stock_balance_match_remains_size_specific(self) -> None:
        lookup = {
            ("PPO-001", "O", "018", "S"): {"on_hand_qty": 8},
            ("PPO-001", "O", "018", "M"): {"on_hand_qty": 14},
            ("PPO-001", "F", "018", "S"): {"on_hand_qty": 5},
        }

        collar_s = engine._find_stock_balance_row(
            lookup,
            "PPO-001",
            "O",
            "018 Navy",
            "018",
            "S",
            prefer_color_identity=True,
        )
        collar_m = engine._find_stock_balance_row(
            lookup,
            "PPO-001",
            "O",
            "018 Navy",
            "018",
            "M",
            prefer_color_identity=True,
        )
        cuff_s = engine._find_stock_balance_row(
            lookup,
            "PPO-001",
            "F",
            "018 Navy",
            "018",
            "S",
            prefer_color_identity=True,
        )

        self.assertEqual(collar_s["on_hand_qty"], 8)
        self.assertEqual(collar_m["on_hand_qty"], 14)
        self.assertEqual(cuff_s["on_hand_qty"], 5)
        self.assertIsNone(
            engine._find_stock_balance_row(
                lookup,
                "PPO-001",
                "O",
                "018 Navy",
                "018",
                "L",
                prefer_color_identity=True,
            )
        )

    def test_unavailable_stock_balance_never_falls_back_to_receipt_for_allocation(self) -> None:
        self.assertEqual(
            engine._system_allocation_available_qty(
                stock_on_hand_qty=150,
                on_way_qty=30,
                stock_balance_complete=False,
            ),
            0.0,
        )
        self.assertEqual(
            engine._system_allocation_available_qty(
                stock_on_hand_qty=150,
                on_way_qty=30,
                stock_balance_complete=True,
            ),
            180.0,
        )

    def test_legacy_stock_fallback_is_diagnostic_only_when_current_view_fails(self) -> None:
        cursor = mock.Mock()
        cursor.description = [
            ("ppo_no",),
            ("fabric_type",),
            ("combo_name",),
            ("size_code",),
            ("on_hand_qty",),
            ("allocated_qty",),
            ("reserved_qty",),
        ]
        cursor.execute.side_effect = [RuntimeError("current stock timeout"), None]
        cursor.fetchall.return_value = [
            ("PPO-001", "M2", "01@NAVY", "", 99.0, 0.0, 0.0)
        ]

        with engine._stock_balance_rows_cache_lock:
            original_cache = dict(engine._stock_balance_rows_cache)
            engine._stock_balance_rows_cache.clear()
        try:
            # A unit test must not open a real SQL connection when it verifies
            # the diagnostic fallback path.  The fresh-connection retry is a
            # production resilience feature, so make it fail deterministically
            # here and assert the legacy view remains diagnostic-only.
            with mock.patch.object(engine, "_connect", side_effect=RuntimeError("retry unavailable")):
                rows, view_name, error = engine._load_stock_balance_rows(
                    cursor,
                    "EGV",
                    ["PPO-001"],
                    bypass_cache=True,
                )
        finally:
            with engine._stock_balance_rows_cache_lock:
                engine._stock_balance_rows_cache.clear()
                engine._stock_balance_rows_cache.update(original_cache)

        self.assertEqual(rows[0]["on_hand_qty"], 99.0)
        self.assertEqual(view_name, engine._STOCK_BALANCE_FALLBACK_VIEW_BY_FACTORY["EGV"])
        self.assertIn("CURRENT_STOCK_UNAVAILABLE", error)
        self.assertIn("diagnostic-only", error)
        self.assertEqual(
            engine._system_allocation_available_qty(99.0, 10.0, stock_balance_complete=not bool(error)),
            0.0,
        )

    def test_stock_change_invalidates_affected_ppo_only(self) -> None:
        old_rows = [
            {
                "ppo_no": "PPO-001",
                "fabric_type": "O",
                "combo_name": "018 Navy",
                "size_code": "S",
                "on_hand_qty": 12,
                "allocated_qty": 0,
                "reserved_qty": 0,
            }
        ]
        new_rows = [{**old_rows[0], "on_hand_qty": 8}]

        self.assertEqual(
            engine._changed_source_ppos(
                ["PPO-001", "PPO-002"], old_rows, new_rows, "stock"
            ),
            {"PPO-001"},
        )

    def test_one_physical_combo_is_one_allocation_pool_across_display_colors(self) -> None:
        # A warehouse combo can feed more than one UI display color. Its
        # on-hand balance must be allocated once, not once per display color.
        first = engine._allocation_pool_key_for_row(
            "PPO-001", "B", "001", "001@018 Navy"
        )
        second = engine._allocation_pool_key_for_row(
            "PPO-001", "B", "002", "001@018 Navy"
        )
        self.assertEqual(first, second)
        self.assertEqual(
            engine._allocation_source_identity_for_group(
                ("PPO-001", "B", "001", "001@018 Navy", "")
            ),
            engine._allocation_source_identity_for_group(
                ("PPO-001", "B", "002", "001@018 Navy", "")
            ),
        )

    def test_pool_allocation_ignores_legacy_cutting_status(self) -> None:
        cutted = {
            "CUTTING STATUS": "CUTTED",
            "__issue_locked_qty": 0,
            "AH Allocate Q'ty (yds)": "",
            "__due_sort_key": (2026, 2, 1, 0, 0, 0),
            "__storage": {"lot_no": "2"},
            "JOB ORDER NO": "JO-CUTTED",
            "Required Q'ty (Yds)": 50,
            "__target_qty": 50,
        }
        pending = {
            "CUTTING STATUS": "PENDING",
            "__issue_locked_qty": 0,
            "AH Allocate Q'ty (yds)": "",
            "__due_sort_key": (2026, 1, 1, 0, 0, 0),
            "__storage": {"lot_no": "1"},
            "JOB ORDER NO": "JO-PENDING",
            "Required Q'ty (Yds)": 50,
            "__target_qty": 50,
        }

        allocations = engine._compute_pool_system_allocations([pending, cutted], total_available=50)

        self.assertEqual(allocations[id(cutted)], 0.0)
        self.assertEqual(allocations[id(pending)], 50.0)


if __name__ == "__main__":
    unittest.main()
