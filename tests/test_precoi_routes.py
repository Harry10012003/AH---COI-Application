from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from types import SimpleNamespace

from flask import g
from openpyxl import Workbook

from backend.app import app
from backend.auth import ROLE_EDITOR
from backend.precoi.excel_exporter import ALL_HEADERS, COLLAR_ALL_HEADERS, COLLAR_SHEET_NAME
from backend.precoi import routes
from backend.precoi.jobs import PreCoiJobStore


class FakePreCoiService:
    def create_output(self, *, go_input, username, password, output_dir, log):
        self.last_request = (go_input, username, password)
        log("Created workbook for GO")
        workbook = Path(output_dir) / "COI Master.xlsx"
        excel = Workbook()
        main_sheet = excel.active
        main_sheet.title = "COI"
        main_sheet.append(ALL_HEADERS)
        main_values = {
            "GO": "S26V00001",
            "YY Req No": "YF2600001",
            "Marker YY": "0.4",
            "PPO YY": "0.5",
            "Gmt Color": "Off",
            "Fabric Part": "MAIN BODY1",
            "COLOR_CODE": "01",
            "COLOR_DESC": "OFF",
            "JOB ORDER NO": "26V00001GB01",
            "Qty": "100",
            "PPO": "",
            "PPO Q'ty": "",
            "Flow": "KNIT",
            "Combo Name": "Main Combo",
            "Block Index": "1",
            "Section Order": "1",
            "Part Order": "1",
            "Aggregate Key": "S26V00001|MAIN",
            "Is Separator": "N",
            "Sheet Kind": "COI",
        }
        main_sheet.append([main_values.get(header, "") for header in ALL_HEADERS])
        collar_sheet = excel.create_sheet(COLLAR_SHEET_NAME)
        collar_sheet.append(COLLAR_ALL_HEADERS)
        collar_values = {
            "GO": "S26V00001",
            "Gmt Color": "Off",
            "Fabric Part": "FK COLLAR1",
            "COLOR_CODE": "01",
            "COLOR_DESC": "OFF",
            "Size": "M",
            "Qty": "20",
            "PPO": "",
            "PPO Q'ty": "",
            "Flow": "KNIT",
            "Combo Name": "Collar Combo",
            "Block Index": "1",
            "Section Order": "1",
            "Part Order": "1",
            "Aggregate Key": "S26V00001|COLLAR",
            "Is Separator": "N",
            "Sheet Kind": COLLAR_SHEET_NAME,
        }
        collar_sheet.append([collar_values.get(header, "") for header in COLLAR_ALL_HEADERS])
        excel.save(workbook)
        return workbook


class PreCoiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.previous_store = routes._jobs
        self.previous_factory = routes._service_factory
        routes._jobs = PreCoiJobStore(Path(self.temp_dir.name), retention_seconds=60)
        routes._service_factory = FakePreCoiService
        app.config.update(TESTING=True)
        self.client = app.test_client()
        login = self.client.post("/api/auth/login", json={"username": "AH", "password": "1234"})
        self.headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}

    def tearDown(self) -> None:
        routes._jobs = self.previous_store
        routes._service_factory = self.previous_factory
        self.temp_dir.cleanup()

    def test_create_job_returns_owner_scoped_status_and_download(self) -> None:
        response = self.client.post(
            "/api/precoi/jobs/create",
            data={"go_text": "S26V00001", "ypd_username": "domain\\user", "ypd_password": "secret"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["job_id"]

        payload = {}
        for _ in range(50):
            status = self.client.get(f"/api/precoi/jobs/{job_id}", headers=self.headers)
            payload = status.get_json()
            if payload["state"] != "RUNNING":
                break
            time.sleep(0.01)

        self.assertEqual(payload["state"], "DONE")
        self.assertEqual(payload["artifact_name"], "COI Master.xlsx")
        self.assertNotIn("artifact_path", payload)
        download = self.client.get(f"/api/precoi/jobs/{job_id}/download", headers=self.headers, buffered=True)
        self.assertEqual(download.status_code, 200)
        self.assertGreater(len(download.data), 100)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertIn("Pre-COI S26V00001.xlsx", download.headers["Content-Disposition"])

    def test_draft_can_save_only_ppo_and_yy_cells(self) -> None:
        response = self.client.post(
            "/api/precoi/jobs/create",
            data={"go_text": "S26V00001", "ypd_username": "domain\\user", "ypd_password": "secret"},
            headers=self.headers,
        )
        job_id = response.get_json()["job_id"]
        for _ in range(50):
            status = self.client.get(f"/api/precoi/jobs/{job_id}", headers=self.headers).get_json()
            if status["state"] != "RUNNING":
                break
            time.sleep(0.01)

        draft = self.client.get(f"/api/precoi/jobs/{job_id}/draft", headers=self.headers)
        self.assertEqual(draft.status_code, 200)
        payload = draft.get_json()
        self.assertEqual([sheet["key"] for sheet in payload["sheets"]], ["COI", COLLAR_SHEET_NAME])
        row_id = payload["sheets"][0]["rows"][0]["row_id"]

        saved = self.client.post(
            f"/api/precoi/jobs/{job_id}/draft",
            json={"revision": payload["revision"], "edits": [{"row_id": row_id, "field": "ppo_no", "value": "PPO1,,PPO3"}]},
            headers=self.headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["sheets"][0]["rows"][0]["ppo_no"], "PPO1,,PPO3")

    def test_viewer_cannot_start_precoi_job(self) -> None:
        login = self.client.post("/api/auth/login", json={"username": "Viewer", "password": "1234"})
        headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}
        response = self.client.post(
            "/api/precoi/jobs/cm",
            data={"go_text": "S26V00001"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_other_editor_is_denied_precoi_access(self) -> None:
        with app.test_request_context("/api/precoi/jobs/create", method="POST"):
            g.auth_user = {"username": "AnotherEditor", "role": ROLE_EDITOR}
            denied = routes._require_precoi_user()

        self.assertIsNotNone(denied)
        response, status_code = denied
        self.assertEqual(status_code, 403)
        self.assertEqual(response.get_json()["error"], "Pre-COI access is restricted")

    def test_download_name_uses_ordered_go_numbers(self) -> None:
        filename = routes._download_name_for_records(
            [
                SimpleNamespace(go="S26V00001"),
                SimpleNamespace(go="S26V00002"),
                SimpleNamespace(go="S26V00001"),
            ]
        )

        self.assertEqual(filename, "Pre-COI S26V00001-S26V00002.xlsx")


if __name__ == "__main__":
    unittest.main()
