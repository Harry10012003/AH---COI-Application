from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.config import credentials
from backend import sources
from backend.engine import sql_live_engine as engine


class CredentialResolutionTests(unittest.TestCase):
    def test_partial_environment_override_never_mixes_credentials(self) -> None:
        stored = credentials.ResolvedCredential(
            username="stored-user",
            password="stored-password",
            source="windows-credential-manager",
            target="TEST",
        )
        with (
            mock.patch.dict(
                os.environ,
                {"UNIT_SQL_USER": "other-user", "UNIT_SQL_PASSWORD": ""},
                clear=False,
            ),
            mock.patch.object(credentials, "read_windows_credential", return_value=stored),
        ):
            result = credentials.resolve_credential(
                user_env="UNIT_SQL_USER",
                password_env="UNIT_SQL_PASSWORD",
                target_env="UNIT_SQL_TARGET",
                default_target="TEST",
                default_user="default-user",
            )
        self.assertEqual((result.username, result.password), ("stored-user", "stored-password"))
        self.assertEqual(result.source, "windows-credential-manager")

    def test_complete_environment_pair_takes_precedence(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"UNIT_SQL_USER": "env-user", "UNIT_SQL_PASSWORD": "env-password"},
                clear=False,
            ),
            mock.patch.object(
                credentials,
                "read_windows_credential",
                return_value=credentials.ResolvedCredential(),
            ),
        ):
            result = credentials.resolve_credential(
                user_env="UNIT_SQL_USER",
                password_env="UNIT_SQL_PASSWORD",
                target_env="UNIT_SQL_TARGET",
                default_target="TEST",
            )
        self.assertEqual((result.username, result.password), ("env-user", "env-password"))
        self.assertEqual(result.source, "environment")

    def test_pymssql_connect_rejects_missing_credentials(self) -> None:
        with (
            mock.patch.object(engine, "SQL_SERVER_HOST", ""),
            mock.patch.object(engine, "SQL_SERVER_DATABASE", "TEST_DB"),
            mock.patch.object(engine, "SQL_SERVER_USER", "test-user"),
            mock.patch.object(engine, "SQL_SERVER_PASSWORD", "test-password"),
        ):
            with self.assertRaisesRegex(RuntimeError, "SQL connection settings are missing"):
                engine._connect()

    def test_pymssql_connect_rejects_encryption_mismatch(self) -> None:
        with (
            mock.patch.object(engine, "SQL_SERVER_HOST", "db.example.test"),
            mock.patch.object(engine, "SQL_SERVER_DATABASE", "TEST_DB"),
            mock.patch.object(engine, "SQL_SERVER_USER", "test-user"),
            mock.patch.object(engine, "SQL_SERVER_PASSWORD", "test-password"),
            mock.patch.object(engine, "SQL_SERVER_ENCRYPT", False),
            mock.patch.object(engine, "SQL_SERVER_REQUIRE_ENCRYPTION", True),
        ):
            with self.assertRaisesRegex(RuntimeError, "encryption is required"):
                engine._connect()

    def test_driver_configuration_reports_non_pymssql_value(self) -> None:
        with (
            mock.patch.object(sources, "SQL_SERVER_DRIVER", "ODBC Driver 18 for SQL Server"),
            mock.patch.object(sources, "SHIPMENT_SQL_SERVER_DRIVER", "pymssql"),
        ):
            result = sources.sql_driver_configuration()
        self.assertEqual(result["runtime_driver"], "pymssql")
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["warnings"]), 1)

    def test_sql_status_supports_tuple_rows_from_pymssql(self) -> None:
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = ("ESQ_DATA", "sql-user", "2026-08-19", "SQL Server test")
        cursor.description = [
            ("dbname",), ("user_name",), ("server_time",), ("sql_version",),
        ]
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        with mock.patch.object(engine, "_connect", return_value=connection):
            result = engine.sql_live_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["database"], "ESQ_DATA")
        self.assertEqual(result["sql_version"], "SQL Server test")


if __name__ == "__main__":
    unittest.main()
