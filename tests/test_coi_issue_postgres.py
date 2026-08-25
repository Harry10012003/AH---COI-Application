from __future__ import annotations

import unittest
from unittest import mock

import backend.engine.coi_issue_engine as issue_engine
from backend.engine.coi_postgres import normalize_sheet_rows


class CoiPostgresIssueFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        issue_engine.clear_refresh_previews_for_tests()

    def tearDown(self) -> None:
        issue_engine.clear_refresh_previews_for_tests()

    def test_refresh_preview_is_read_only_until_apply_and_preserves_manual_fields(self) -> None:
        candidate = {
            "ok": True,
            "go": "S26V00001",
            "rows": [
                {
                    "_row_key": "source-row-a",
                    "GO#": "S26V00001",
                    "PPO": "PPO-NEW",
                    "AH Allocate Q'ty (yds)": None,
                    "User Remark": "source remark",
                }
            ],
        }
        row_id = str(normalize_sheet_rows(candidate)[0]["internal_row_id"])
        current = {
            "ok": True,
            "go": "S26V00001",
            "issue": {"updated_at": "2026-08-25T10:00:00+00:00"},
            "rows": [
                {
                    "_row_key": row_id,
                    "GO#": "S26V00001",
                    "PPO": "PPO-OLD",
                    "AH Allocate Q'ty (yds)": 12.5,
                    "User Remark": "AH remark",
                }
            ],
        }
        with (
            mock.patch.object(issue_engine, "load_current_issue", return_value=current),
            mock.patch.object(issue_engine, "sync_current_sheet") as sync,
        ):
            preview = issue_engine.create_refresh_preview("S26V00001", candidate, actor="AH")
            sync.assert_not_called()
            sync.return_value = {
                "ok": True,
                "action": "SOURCE_REFRESH_APPLIED",
                "issue_revision": 1,
                "sync_revision": 1,
            }
            applied = issue_engine.apply_refresh_preview(
                "S26V00001",
                preview["preview_id"],
                actor="AH",
            )

        self.assertTrue(preview["preview_required"])
        self.assertEqual(preview["diff"]["changed_row_count"], 1)
        self.assertTrue(applied["ok"])
        synced_payload = sync.call_args.args[0]
        self.assertEqual(synced_payload["rows"][0]["AH Allocate Q'ty (yds)"], 12.5)
        self.assertEqual(synced_payload["rows"][0]["User Remark"], "AH remark")
        self.assertEqual(sync.call_args.kwargs["expected_updated_at"], "2026-08-25T10:00:00+00:00")

    def test_unissued_refresh_returns_live_sheet_without_preview(self) -> None:
        candidate = {"ok": True, "go": "S26V00001", "rows": [{"_row_key": "row-a"}]}
        with mock.patch.object(issue_engine, "load_current_issue", return_value=None):
            result = issue_engine.create_refresh_preview("S26V00001", candidate, actor="AH")
        self.assertFalse(result["preview_required"])
        self.assertIs(result["sheet"], candidate)


if __name__ == "__main__":
    unittest.main()
