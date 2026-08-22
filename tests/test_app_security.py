from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

import backend.app as app_module
from backend.auth import authenticate, clear_sessions_for_tests, create_session


class AppSecurityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sessions_for_tests()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        token, _expires_at = create_session(authenticate("AH", "1234"))
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"

    def tearDown(self) -> None:
        clear_sessions_for_tests()

    def test_static_response_has_security_headers(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        response.close()

    def test_spa_deep_links_serve_frontend(self) -> None:
        for path in ("/login", "/coi?go=S26V00001"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.content_type)
                response.close()

    def test_unknown_api_route_returns_json_404_not_spa_html(self) -> None:
        response = self.client.get("/api/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"ok": False, "error": "Not found"})
        self.assertIn("application/json", response.content_type)

    def test_frontend_does_not_generate_csp_blocked_inline_styles(self) -> None:
        frontend_index = Path(app_module.FRONTEND_DIR) / "index.html"
        if not frontend_index.exists():
            self.skipTest("React frontend — inline style check skipped")
        frontend_html = frontend_index.read_text(encoding="utf-8")
        self.assertNotIn("style=", frontend_html)

    def test_cross_site_mutation_is_rejected_before_services_start(self) -> None:
        with mock.patch.object(app_module, "start_background_services") as start:
            response = self.client.post(
                "/api/go/summary",
                json={"go": "S26V00001"},
                headers={"Sec-Fetch-Site": "cross-site"},
            )
        self.assertEqual(response.status_code, 403)
        start.assert_not_called()

    def test_vite_dev_origin_is_allowed_for_mutations(self) -> None:
        with (
            mock.patch.object(app_module, "start_background_services") as start,
            mock.patch.object(app_module, "clear_cutting_forecast_cache", return_value={"cached_go_count": 0}),
        ):
            response = self.client.post(
                "/api/mes/cutting/cache/clear",
                json={},
                headers={"Origin": "http://localhost:5173"},
            )
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(wait_for_startup=False)

    def test_unconfigured_origin_is_rejected_before_services_start(self) -> None:
        with mock.patch.object(app_module, "start_background_services") as start:
            response = self.client.post(
                "/api/material-status/apply-coi-rules",
                json={"rows": []},
                headers={"Origin": "http://malicious.example:5173"},
            )
        self.assertEqual(response.status_code, 403)
        start.assert_not_called()

    def test_path_outside_allowlist_is_rejected(self) -> None:
        with mock.patch.object(app_module, "start_background_services") as start:
            response = self.client.post(
                "/api/excel/audit",
                json={"workbook_path": r"C:\Windows\System32\config\SAM"},
            )
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()

    def test_get_workbook_path_outside_allowlist_is_rejected(self) -> None:
        with (
            mock.patch.object(app_module, "start_background_services") as start,
            mock.patch.object(app_module, "get_workbook_overview") as overview,
        ):
            response = self.client.get(
                "/api/excel/workbook",
                query_string={"path": r"C:\Windows\System32\config\SAM"},
            )
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()
        overview.assert_not_called()

    def test_get_audit_remote_path_is_rejected(self) -> None:
        with (
            mock.patch.object(app_module, "start_background_services") as start,
            mock.patch.object(app_module, "audit_workbook") as audit,
        ):
            response = self.client.get(
                "/api/excel/audit",
                query_string={"path": "https://example.com/private.xlsx"},
            )
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()
        audit.assert_not_called()

    def test_internal_api_requires_login(self) -> None:
        anonymous_client = app_module.app.test_client()
        with mock.patch.object(app_module, "start_background_services"):
            response = anonymous_client.get("/api/sql/status")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json().get("error"), "Authentication required")

    def test_shared_bind_does_not_require_api_token(self) -> None:
        app_module.validate_bind_security("127.0.0.1")
        app_module.validate_bind_security("::1")
        app_module.validate_bind_security("0.0.0.0")

    def test_private_lan_api_works_without_token(self) -> None:
        with mock.patch.object(
            app_module,
            "sql_live_status",
            return_value={"ok": True, "database": "TEST"},
        ):
            response = self.client.get(
                "/api/sql/status",
                environ_overrides={"REMOTE_ADDR": "10.20.30.40"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json().get("ok"))

    def test_preload_status_redacts_internal_diagnostics(self) -> None:
        status = {
            "warmup_complete": False,
            "cached_go_count": 120,
            "source_current_go_count": 80,
            "thread_alive": True,
            "db_file": r"C:\private\snapshot.db",
            "current_go": "S26V00001",
            "recent_events": [{"go_no": "S26V00001", "message": "private"}],
            "last_error": "sensitive database error",
        }
        with (
            mock.patch.object(app_module, "_DIAGNOSTICS_DETAIL", False),
            mock.patch.object(app_module, "sql_snapshot_status", return_value=status),
            mock.patch.object(app_module, "start_background_services"),
        ):
            response = self.client.get("/api/sql/preload/status")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["cached_go_count"], 120)
        self.assertNotIn("db_file", payload)
        self.assertNotIn("current_go", payload)
        self.assertNotIn("recent_events", payload)
        self.assertNotIn("last_error", payload)

    def test_source_cache_status_redacts_keys_paths_and_errors(self) -> None:
        status = {
            "source_db_file": r"C:\private\snapshot.db",
            "table_counts": {"sql_go_head": 123},
            "latest_synced_at": "2026-07-27 09:00:00",
            "latest_checked_at": "2026-07-27 09:01:00",
            "recent_sources": [
                {
                    "source_key": "RECEIVED:secret-view:S26V00001",
                    "source_status": "ERROR",
                    "last_error": "database topology",
                }
            ],
        }
        with (
            mock.patch.object(app_module, "_DIAGNOSTICS_DETAIL", False),
            mock.patch.object(app_module, "sql_source_cache_status", return_value=status),
            mock.patch.object(app_module, "start_background_services"),
        ):
            response = self.client.get("/api/sql/source-cache/status")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["recent_source_count"], 1)
        self.assertEqual(payload["recent_error_count"], 1)
        self.assertNotIn("source_db_file", payload)
        self.assertNotIn("table_counts", payload)
        self.assertNotIn("recent_sources", payload)

    def test_api_status_exposes_only_safe_aggregates_without_diagnostics(self) -> None:
        with (
            mock.patch.object(app_module, "_DIAGNOSTICS_DETAIL", False),
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(
                app_module,
                "get_fabric_stock_meta",
                return_value={
                    "filename": "SECRET_FABRIC_FILE.xlsx",
                    "source_path": r"C:\SECRET_FABRIC_PATH\source.xlsx",
                    "loaded_at": "2026-07-27 09:00:00",
                    "total_groups": 12,
                },
            ),
            mock.patch.object(
                app_module,
                "get_cutting_cache_status",
                return_value={
                    "cache_file": r"C:\SECRET_MES_PATH\cache.json",
                    "cached_go_count": 3,
                    "cached_gos": ["SECRET_GO_MES"],
                    "exists": True,
                },
            ),
            mock.patch.object(
                app_module,
                "sql_snapshot_status",
                return_value={
                    "cached_go_count": 8,
                    "db_file": r"C:\SECRET_SNAPSHOT_PATH\cache.db",
                    "recent_events": [{"go_no": "SECRET_GO_SNAPSHOT"}],
                },
            ),
            mock.patch.object(
                app_module,
                "sql_source_cache_status",
                return_value={
                    "source_db_file": r"C:\SECRET_SOURCE_PATH\cache.db",
                    "latest_synced_at": "2026-07-27 09:00:00",
                    "recent_sources": [
                        {
                            "source_key": "SECRET_SOURCE_KEY",
                            "source_status": "ERROR",
                            "last_error": "SECRET_SOURCE_ERROR",
                        }
                    ],
                },
            ),
            mock.patch.object(
                app_module,
                "color_audit_status",
                return_value={
                    "running": True,
                    "thread_alive": True,
                    "last_error": "SECRET_AUDIT_ERROR",
                    "summary_json": r"C:\SECRET_AUDIT_PATH\summary.json",
                    "findings_csv": r"C:\SECRET_AUDIT_PATH\findings.csv",
                    "last_summary": {
                        "scanned_go": 50,
                        "finding_row_count": 4,
                        "findings_csv": r"C:\SECRET_AUDIT_PATH\findings.csv",
                        "go_sample": ["SECRET_GO_AUDIT"],
                    },
                },
            ),
        ):
            response = self.client.get("/api/status")
        payload = response.get_json()
        serialized = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["fabric_stock"]["total_groups"], 12)
        self.assertEqual(payload["mes_cache"]["cached_go_count"], 3)
        self.assertEqual(payload["color_audit"]["summary"]["finding_row_count"], 4)
        self.assertNotIn("external_eta_sources", payload)
        self.assertNotIn("weekly_remark", payload)
        for secret in (
            "SECRET_FABRIC",
            "SECRET_MES",
            "SECRET_GO",
            "SECRET_SNAPSHOT",
            "SECRET_SOURCE",
            "SECRET_AUDIT",
            "SECRET_ETA",
            "SECRET_WEEKLY",
            "SECRET_LINE",
        ):
            self.assertNotIn(secret, serialized)

    def test_sql_status_preserves_only_safe_transport_fields(self) -> None:
        sql_status = {
            "ok": True,
            "connected": True,
            "database": "ESQ_DATA",
            "user": "SECRET_SQL_USER",
            "server_time": "2026-07-27",
            "sql_version": "SECRET_SQL_VERSION",
            "connection": {
                "host": "SECRET_SQL_HOST",
                "driver": "SECRET_SQL_DRIVER",
                "user": "SECRET_SQL_USER",
                "encrypted": True,
                "transport_security": "tls-verified",
                "encryption_required": True,
            },
        }
        with (
            mock.patch.object(app_module, "_DIAGNOSTICS_DETAIL", False),
            mock.patch.object(app_module, "sql_live_status", return_value=sql_status),
            mock.patch.object(app_module, "start_background_services"),
        ):
            response = self.client.get("/api/sql/status")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            payload["connection"],
            {
                "database": "ESQ_DATA",
                "encrypted": True,
                "transport_security": "tls-verified",
                "encryption_required": True,
            },
        )
        serialized = response.get_data(as_text=True)
        self.assertNotIn("SECRET_SQL_HOST", serialized)
        self.assertNotIn("SECRET_SQL_DRIVER", serialized)
        self.assertNotIn("SECRET_SQL_USER", serialized)
        self.assertNotIn("SECRET_SQL_VERSION", serialized)

    def test_direct_cache_status_routes_do_not_expose_paths_or_go_samples(self) -> None:
        with (
            mock.patch.object(app_module, "_DIAGNOSTICS_DETAIL", False),
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(
                app_module,
                "color_audit_status",
                return_value={
                    "last_error": "SECRET_AUDIT_ERROR",
                    "findings_csv": r"C:\SECRET_AUDIT\findings.csv",
                },
            ),
            mock.patch.object(
                app_module,
                "get_cutting_cache_status",
                return_value={
                    "cached_go_count": 4,
                    "exists": True,
                    "cache_file": r"C:\SECRET_MES\cache.json",
                    "cached_gos": ["SECRET_MES_GO"],
                },
            ),
        ):
            for route in (
                "/api/sql/audit/status",
                "/api/mes/cutting/cache/status",
            ):
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn("SECRET_", response.get_data(as_text=True))

    def test_removed_material_and_actual_cutting_routes_are_not_available(self) -> None:
        with mock.patch.object(app_module, "start_background_services"):
            for route in (
                "/api/material-status/weekly-remark/status",
                "/api/mes/actual-cutting/cache/status",
            ):
                with self.subTest(route=route):
                    self.assertEqual(self.client.get(route).status_code, 404)

    def test_cutting_coi_feed_is_read_only_and_can_opt_in_to_cors(self) -> None:
        feed = {
            "ok": True,
            "feed": "cutting-coi-latest",
            "rows": [{"GO#": "S26V00001"}],
            "pagination": {"returned": 1},
        }
        with (
            mock.patch.object(app_module, "_CUTTING_COI_API_ALLOWED_ORIGINS", {"*"}),
            mock.patch.object(app_module, "get_latest_issued_coi_feed", return_value=feed) as get_feed,
            mock.patch.object(app_module, "start_background_services") as start,
        ):
            response = self.client.get(
                "/api/cutting/coi/latest?go=S26V00001&limit=50",
                headers={"Origin": "http://192.168.161.99:8080"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(response.get_json()["rows"][0]["GO#"], "S26V00001")
        get_feed.assert_called_once_with(
            go="S26V00001",
            ppo=None,
            jo=None,
            color_code=None,
            limit="50",
            offset=0,
        )
        start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
