from __future__ import annotations

import unittest

from backend.engine import sql_live_engine as engine


class CoiRequiredQuantityTests(unittest.TestCase):
    def test_required_quantity_uses_ppo_yy_not_net_or_marker_yy(self) -> None:
        # This mirrors the user-reported COI case: 400 × PPO YY 1.3295,
        # rather than 400 × Net YY / Marker YY 1.3122.
        self.assertEqual(engine._required_qty_from_ppo_yy(400, 1.3295, 1.3122), 531.8)

    def test_flatknit_can_use_its_neutral_marker_when_ppo_yy_is_blank(self) -> None:
        self.assertEqual(
            engine._required_qty_from_ppo_yy(
                53,
                0,
                1,
                allow_flatknit_fallback=True,
            ),
            53.0,
        )

    def test_body_fabric_with_missing_ppo_yy_is_not_replaced_by_marker_yy(self) -> None:
        self.assertEqual(engine._required_qty_from_ppo_yy(400, 0, 1.3122), 0.0)

    def test_required_quantity_never_uses_a_negative_value(self) -> None:
        self.assertEqual(engine._required_qty_from_ppo_yy(-10, 1.2), 0.0)
        self.assertEqual(engine._required_qty_from_ppo_yy(10, -1), 0.0)
