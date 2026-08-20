from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.engine import sql_live_engine as engine
from backend.engine.refresh_scheduler import InteractiveGoQueue
from backend.engine.sql_sources.metrics import QueryMetricRegistry
from backend.engine.sql_sources.read_only import ReadOnlySqlViolation, assert_read_only_sql


class ReadOnlySqlTests(unittest.TestCase):
    def test_select_and_cte_are_allowed(self) -> None:
        assert_read_only_sql("SELECT TOP 1 * FROM dbo.Source")
        assert_read_only_sql("SELECT [Create Date], [Update Flag] FROM dbo.Source")
        assert_read_only_sql("-- bounded read\nWITH rows AS (SELECT 1 AS value) SELECT * FROM rows")

    def test_source_mutations_are_rejected(self) -> None:
        for statement in (
            "UPDATE dbo.Source SET value = 1",
            "DELETE FROM dbo.Source",
            "ALTER VIEW dbo.Source AS SELECT 1 AS value",
            "EXEC dbo.RefreshSource",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(ReadOnlySqlViolation):
                    assert_read_only_sql(statement)

    def test_second_statement_is_rejected(self) -> None:
        with self.assertRaises(ReadOnlySqlViolation):
            assert_read_only_sql("SELECT 1; DELETE FROM dbo.Source")

    def test_mutating_cte_and_select_into_are_rejected(self) -> None:
        for statement in (
            "WITH rows AS (SELECT 1 AS value) DELETE FROM dbo.Source",
            "SELECT * INTO dbo.CopyOfSource FROM dbo.Source",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(ReadOnlySqlViolation):
                    assert_read_only_sql(statement)


class InteractiveQueueTests(unittest.TestCase):
    def test_queue_deduplicates_and_promotes_recent_request(self) -> None:
        queue = InteractiveGoQueue(capacity=3)
        queue.promote(["GO-1", "GO-2", "GO-1"])
        queue.promote(["go-2", "GO-3"])
        self.assertEqual(queue.take(3), ["GO-2", "GO-3", "GO-1"])

    def test_queue_capacity_drops_oldest_background_request(self) -> None:
        queue = InteractiveGoQueue(capacity=2)
        queue.promote(["GO-1", "GO-2", "GO-3"])
        self.assertEqual(queue.take(5), ["GO-1", "GO-2"])

    def test_interactive_go_precedes_missing_topology_backlog(self) -> None:
        with TemporaryDirectory() as temp_dir, mock.patch.object(
            engine, "_SNAPSHOT_DB", Path(temp_dir) / "queue.db"
        ), mock.patch.object(engine, "_snapshot_schema_ready", False):
            rows = [
                {"go_no": "GO-BACKGROUND", "modify_date": "2026-08-20 10:00:00"},
                {"go_no": "GO-INTERACTIVE", "modify_date": "2026-08-20 10:00:01"},
            ]
            selected, stale_count = engine._select_stale_go_rows(
                rows,
                batch_size=1,
                interactive_go_nos=["GO-INTERACTIVE"],
            )
        self.assertEqual(stale_count, 2)
        self.assertEqual(selected[0]["go_no"], "GO-INTERACTIVE")


class QueryMetricsTests(unittest.TestCase):
    def test_metrics_report_safe_rolling_percentiles(self) -> None:
        metrics = QueryMetricRegistry(window_size=20)
        metrics.record("stock-rds", 0.1, "ok")
        metrics.record("stock-rds", 0.3, "timeout")
        snapshot = metrics.snapshot()["stock-rds"]
        self.assertEqual(snapshot["sample_count"], 2)
        self.assertEqual(snapshot["p50_ms"], 100.0)
        self.assertEqual(snapshot["p95_ms"], 300.0)
        self.assertEqual(snapshot["outcomes"], {"ok": 1, "timeout": 1})


class PpoBatchIsolationTests(unittest.TestCase):
    def test_failed_ppo_batch_does_not_discard_successful_batch(self) -> None:
        cursor = mock.MagicMock()
        cursor.description = [
            ("ppo_no",),
            ("fabric_part",),
            ("color_code",),
            ("fabric_combo",),
            ("ppo_order_qty",),
        ]
        cursor.execute.side_effect = [None, TimeoutError("query timed out")]
        cursor.fetchall.return_value = [("PPO-1", "Main Body", "01", "01@NAVY", 125.0)]
        errors: list[str] = []
        with mock.patch.object(engine, "_PPO_ENRICHMENT_BATCH_SIZE", 1):
            totals = engine._load_ppo_order_totals_sql(
                cursor,
                ["PPO-1", "PPO-2"],
                errors=errors,
            )
        self.assertTrue(any(key[0] == "PPO-1" for key in totals))
        self.assertEqual(errors, ["SQL_UNAVAILABLE PPO batch 2"])


if __name__ == "__main__":
    unittest.main()
