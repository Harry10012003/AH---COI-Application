from __future__ import annotations

import unittest
from unittest import mock

import backend.app as app_module
from backend.auth import ROLE_EDITOR, ROLE_VIEWER, authenticate, clear_sessions_for_tests


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sessions_for_tests()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        clear_sessions_for_tests()

    def _login(self, username: str, password: str = "1234") -> tuple[dict, dict]:
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        payload = response.get_json()
        headers = {"Authorization": f"Bearer {payload.get('access_token', '')}"}
        return payload, headers

    def test_hardcoded_accounts_have_expected_roles(self) -> None:
        self.assertEqual(authenticate("AH", "1234")["role"], ROLE_EDITOR)
        self.assertEqual(authenticate("viewer", "1234")["role"], ROLE_VIEWER)
        self.assertIsNone(authenticate("AH", "wrong"))
        self.assertIsNone(authenticate("unknown", "1234"))

    def test_login_me_and_logout_lifecycle(self) -> None:
        payload, headers = self._login("AH")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user"], {"username": "AH", "role": ROLE_EDITOR})

        me_response = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.get_json()["user"]["username"], "AH")

        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)

    def test_login_rejects_invalid_credentials(self) -> None:
        with mock.patch.object(app_module, "start_background_services"):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "AH", "password": "wrong"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.get_json())

    def test_viewer_can_search_and_open_snapshot(self) -> None:
        _payload, headers = self._login("Viewer")
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(
                app_module,
                "list_live_go",
                return_value={"ok": True, "rows": [{"go_no": "S26V00001"}]},
            ),
        ):
            list_response = self.client.get("/api/sql/go/list?search=S26", headers=headers)
        self.assertEqual(list_response.status_code, 200)

        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(
                app_module,
                "build_live_coi_sheet",
                return_value={"ok": True, "rows": []},
            ) as build_sheet,
        ):
            sheet_response = self.client.get(
                "/api/sql/go/S26V00001/sheet?use_snapshot=false&allow_inline_build=true&require_current_source=true",
                headers=headers,
            )
        self.assertEqual(sheet_response.status_code, 200)
        kwargs = build_sheet.call_args.kwargs
        self.assertTrue(kwargs["use_snapshot"])
        self.assertFalse(kwargs["persist_snapshot"])
        self.assertFalse(kwargs["allow_inline_build"])
        self.assertFalse(kwargs["require_current_source"])

    def test_viewer_cannot_edit_refresh_export_or_issue(self) -> None:
        _payload, headers = self._login("Viewer")
        protected_requests = (
            ("post", "/api/sql/go/S26V00001/sheet/edits"),
            ("post", "/api/sql/go/S26V00001/refresh-ppo"),
            ("post", "/api/sql/go/S26V00001/sheet/export"),
            ("post", "/api/sql/go/S26V00001/issue"),
        )
        with mock.patch.object(app_module, "start_background_services"):
            for method, path in protected_requests:
                with self.subTest(path=path):
                    response = getattr(self.client, method)(path, json={}, headers=headers)
                    self.assertEqual(response.status_code, 403)

    def test_editor_can_reach_edit_endpoint(self) -> None:
        _payload, headers = self._login("AH")
        with (
            mock.patch.object(app_module, "start_background_services"),
            mock.patch.object(app_module, "save_live_sheet_edits", return_value={"ok": True}) as save_edits,
        ):
            response = self.client.post(
                "/api/sql/go/S26V00001/sheet/edits",
                json={"edits": []},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        save_edits.assert_called_once()


if __name__ == "__main__":
    unittest.main()
