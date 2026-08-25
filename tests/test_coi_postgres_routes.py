from __future__ import annotations

import unittest
from unittest import mock

import backend.app as app_module
from backend.auth import clear_sessions_for_tests


class CoiPostgresRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sessions_for_tests()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        login = self.client.post("/api/auth/login", json={"username": "AH", "password": "1234"}).get_json()
        self.headers = {"Authorization": f"Bearer {login['access_token']}"}

    def tearDown(self) -> None:
        clear_sessions_for_tests()

    def test_issued_go_sheet_comes_from_postgres_before_live_cache(self) -> None:
        current = {
            "ok": True,
            "go": "S26V00001",
            "storage_source": "postgres",
            "columns": [],
            "rows": [{"GO#": "S26V00001", "PPO": "PPO-PG"}],
        }
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(app_module, "load_current_issue", return_value=current),
            mock.patch.object(app_module, "build_live_coi_sheet") as live,
        ):
            response = self.client.get("/api/sql/go/S26V00001/sheet", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["storage_source"], "postgres")
        live.assert_not_called()

    def test_non_ppo_edit_on_issued_go_syncs_postgres_directly(self) -> None:
        current = {
            "ok": True,
            "go": "S26V00001",
            "rows": [{"_row_key": "8f31a758-323e-4b28-8cdf-d57f01766e1e", "User Remark": "old"}],
        }
        edits = [
            {
                "row_key": "8f31a758-323e-4b28-8cdf-d57f01766e1e",
                "field": "User Remark",
                "value": "new",
            }
        ]
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(app_module, "load_current_issue", side_effect=[current, current]),
            mock.patch.object(
                app_module,
                "apply_current_edits",
                return_value={"ok": True, "action": "AUTO_SYNC_EDIT", "sync_revision": 2},
            ) as sync,
            mock.patch.object(app_module, "save_live_sheet_edits") as local_save,
        ):
            response = self.client.post(
                "/api/sql/go/S26V00001/sheet/edits",
                json={"edits": edits},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        sync.assert_called_once_with("S26V00001", edits, actor="AH")
        local_save.assert_not_called()

    def test_issue_is_synchronous_postgres_write(self) -> None:
        result = {
            "ok": True,
            "go": "S26V00001",
            "issue_revision": 1,
            "storage_source": "postgres",
        }
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(app_module, "issue_coi_to_postgres", return_value=result) as issue,
        ):
            response = self.client.post(
                "/api/sql/go/S26V00001/issue",
                json={"go": "S26V00001"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        issue.assert_called_once_with("S26V00001", actor="AH")

    def test_refresh_apply_conflict_returns_409(self) -> None:
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(
                app_module,
                "apply_refresh_preview",
                return_value={"ok": False, "conflict": True, "error": "stale"},
            ),
        ):
            response = self.client.post(
                "/api/sql/go/S26V00001/refresh-ppo/apply",
                json={"preview_id": "expired"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
