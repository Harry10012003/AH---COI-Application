from __future__ import annotations

from decimal import Decimal
import os
import unittest
from unittest import mock
import uuid

import backend.engine.coi_postgres as postgres_module
from backend.config.postgres import PostgresConfig, PostgresConfigError
from backend.engine.coi_postgres import (
    CoiPostgresError,
    CoiPostgresUnavailable,
    FIELD_SPECS,
    diff_rows,
    normalize_sheet_rows,
)


def _row(row_key: str = "stable-source-row", **overrides) -> dict:
    row = {spec.ui_key: None for spec in FIELD_SPECS}
    row.update(
        {
            "_row_key": row_key,
            "GO#": "S26V00001",
            "PPO": "PPO-1",
            "Type": "B",
            "COLOR_CODE": "WHITE",
            "Required Q'ty (Yds)": "0",
        }
    )
    row.update(overrides)
    return row


class PostgresConfigTests(unittest.TestCase):
    def test_valid_config_has_safe_non_secret_summary(self) -> None:
        env = {
            "COI_PG_HOST": "postgres.internal",
            "COI_PG_PORT": "5432",
            "COI_PG_DATABASE": "TGVLocalApp",
            "COI_PG_SCHEMA": "ah_app",
            "COI_PG_USER": "coi-user",
            "COI_PG_PASSWORD": "do-not-expose",
            "COI_PG_SSLMODE": "prefer",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = PostgresConfig.from_env()
        summary = config.safe_summary()
        self.assertTrue(summary["password_configured"])
        self.assertNotIn("do-not-expose", str(summary))

    def test_missing_or_invalid_config_is_actionable(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"COI_PG_HOST": "", "COI_PG_DATABASE": "", "COI_PG_USER": "", "COI_PG_PASSWORD": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(PostgresConfigError, "COI_PG_HOST"):
                PostgresConfig.from_env()
        with mock.patch.dict(
            os.environ,
            {
                "COI_PG_HOST": "host",
                "COI_PG_DATABASE": "db",
                "COI_PG_USER": "user",
                "COI_PG_PASSWORD": "secret",
                "COI_PG_SCHEMA": "bad-schema",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(PostgresConfigError, "identifier"):
                PostgresConfig.from_env()

    def test_pool_timeout_is_classified_without_exposing_driver_detail(self) -> None:
        class ExhaustedPool:
            def connection(self):
                raise TimeoutError("secret driver detail")

        with mock.patch.object(postgres_module, "_pool", return_value=ExhaustedPool()):
            with self.assertRaisesRegex(CoiPostgresUnavailable, "temporarily unavailable") as raised:
                with postgres_module._connection():
                    pass
        self.assertNotIn("secret driver detail", str(raised.exception))


class PostgresRowModelTests(unittest.TestCase):
    def test_row_uuid_is_deterministic_and_null_is_distinct_from_zero(self) -> None:
        first = normalize_sheet_rows({"go": "S26V00001", "rows": [_row()]})[0]
        second = normalize_sheet_rows({"go": "S26V00001", "rows": [_row()]})[0]
        self.assertIsInstance(first["internal_row_id"], uuid.UUID)
        self.assertEqual(first["internal_row_id"], second["internal_row_id"])
        self.assertEqual(first["required_qty_yds"], Decimal("0"))
        self.assertIsNone(first["received_qty_ppo"])

    def test_missing_or_duplicate_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(CoiPostgresError, "stable _row_key"):
            normalize_sheet_rows({"go": "S26V00001", "rows": [_row("")]})
        duplicate = _row("same")
        with self.assertRaisesRegex(CoiPostgresError, "Duplicate"):
            normalize_sheet_rows({"go": "S26V00001", "rows": [duplicate, dict(duplicate)]})

    def test_diff_ignores_row_order_but_records_exact_old_new(self) -> None:
        existing = normalize_sheet_rows(
            {"go": "S26V00001", "rows": [_row("a", PPO="PPO-A"), _row("b", PPO="PPO-B")]}
        )
        incoming = normalize_sheet_rows(
            {"go": "S26V00001", "rows": [_row("b", PPO="PPO-B"), _row("a", PPO="PPO-A")]}
        )
        self.assertFalse(diff_rows(existing, incoming)["has_changes"])

        changed = normalize_sheet_rows(
            {"go": "S26V00001", "rows": [_row("a", PPO="PPO-NEW"), _row("b", PPO="PPO-B")]}
        )
        result = diff_rows(existing, changed)
        self.assertTrue(result["has_changes"])
        self.assertEqual(result["changed_row_count"], 1)
        ppo_change = next(item for item in result["changes"] if item["field"] == "ppo_no")
        self.assertEqual(ppo_change["old_value"], "PPO-A")
        self.assertEqual(ppo_change["new_value"], "PPO-NEW")


if __name__ == "__main__":
    unittest.main()
