from __future__ import annotations

import unittest

from backend.engine import sql_live_engine as engine


class FlatknitSizeSplitTests(unittest.TestCase):
    def test_collar_and_cuff_rows_are_split_by_color_and_size(self) -> None:
        rows = [
            {"Type": "O", "Lot": 1, "JO": "JO-001", "COLOR_CODE": "NAVY", "Qty": 100},
            {"Type": "F", "Lot": 1, "JO": "JO-001", "COLOR_CODE": "NAVY", "Qty": 100},
            {"Type": "B", "Lot": 1, "JO": "JO-001", "COLOR_CODE": "NAVY", "Qty": 100},
        ]
        size_rows = [
            {"lot_no": 1, "jo_no": "JO-001", "color_code": "NAVY", "size_code": "S", "qty": 40},
            {"lot_no": 1, "jo_no": "JO-001", "color_code": "NAVY", "size_code": "M", "qty": 60},
        ]

        result = engine._split_flatknit_rows_by_size(rows, size_rows)

        self.assertEqual(len(result), 5)
        self.assertEqual(
            [(row["Type"], row.get("SIZE"), row["Qty"]) for row in result],
            [("O", "S", 40.0), ("O", "M", 60.0), ("F", "S", 40.0), ("F", "M", 60.0), ("B", None, 100)],
        )

    def test_snapshot_gap_detection_only_targets_unsized_flatknit_rows(self) -> None:
        self.assertTrue(
            engine._snapshot_payload_has_unsplit_flatknit_rows(
                {"rows": [{"Type": "O", "SIZE": ""}, {"Type": "F", "SIZE": "M"}]}
            )
        )
        self.assertFalse(
            engine._snapshot_payload_has_unsplit_flatknit_rows(
                {"rows": [{"Type": "O", "SIZE": "S"}, {"Type": "F", "SIZE": "M"}]}
            )
        )

    def test_received_rows_and_allocation_pool_are_size_specific_for_flatknit(self) -> None:
        received_rows = [
            {"ppo_no": "PPO-001", "fabric_type": "O", "combo_name": "NAVY", "size_code": "S", "received_qty": 10, "foc_qty": 0},
            {"ppo_no": "PPO-001", "fabric_type": "O", "combo_name": "NAVY", "size_code": "M", "received_qty": 20, "foc_qty": 0},
        ]
        aggregate = engine._aggregate_received_rows(received_rows)
        self.assertEqual(len(aggregate), 1)
        self.assertEqual(aggregate[0]["received_qty"], 30.0)

        by_size = {
            ("PPO-001", "O", "NAVY", "S"): {"received_qty": 10, "foc_qty": 0},
            ("PPO-001", "O", "NAVY", "M"): {"received_qty": 20, "foc_qty": 0},
        }
        self.assertEqual(engine._find_received_row(by_size, "PPO-001", "O", "NAVY", "", "S")["received_qty"], 10)
        self.assertEqual(engine._find_received_row(by_size, "PPO-001", "O", "NAVY", "", "M")["received_qty"], 20)
        self.assertIsNone(engine._find_received_row(by_size, "PPO-001", "O", "NAVY", "", "L"))
        self.assertNotEqual(
            engine._allocation_pool_key_for_row("PPO-001", "O", "NAVY", "NAVY", "S"),
            engine._allocation_pool_key_for_row("PPO-001", "O", "NAVY", "NAVY", "M"),
        )

    def test_aggregate_shipment_is_distributed_not_repeated_for_each_size(self) -> None:
        by_size = {
            ("PPO-001", "O", "NAVY", "NAVY", "S"): 40,
            ("PPO-001", "O", "NAVY", "NAVY", "M"): 60,
        }

        result = engine._distribute_flatknit_total_by_size(25, by_size)

        self.assertEqual(result[("PPO-001", "O", "NAVY", "NAVY", "S")], 10.0)
        self.assertEqual(result[("PPO-001", "O", "NAVY", "NAVY", "M")], 15.0)
        self.assertEqual(sum(result.values()), 25.0)

    def test_flatknit_receipt_prefers_garment_color_over_shared_fabric_color(self) -> None:
        lookup = {
            # 018 Navy is the fabric color shared by three garment colors.
            ("PPO-001", "O", "018NAVY", "S"): {"received_qty": 300, "foc_qty": 0},
            ("PPO-001", "O", "018", "S"): {"received_qty": 61, "foc_qty": 0},
        }

        result = engine._find_received_row(
            lookup,
            "PPO-001",
            "O",
            "018 Navy",
            "018",
            "S",
            prefer_color_identity=True,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["received_qty"], 61)
