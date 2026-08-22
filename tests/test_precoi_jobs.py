from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from backend.precoi.jobs import PreCoiJobStore


class PreCoiJobStoreTests(unittest.TestCase):
    def test_job_keeps_logs_and_artifact_private_to_owner(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = PreCoiJobStore(Path(temp_dir), retention_seconds=60)

            def runner(job_dir: Path, log):
                log("Loading GO report")
                artifact = job_dir / "COI Master.xlsx"
                artifact.write_bytes(b"workbook")
                return artifact

            job = store.start(owner="ah", action="create", runner=runner)
            for _ in range(20):
                snapshot = store.snapshot(job.job_id, "ah")
                if snapshot and snapshot["state"] != "RUNNING":
                    break
                time.sleep(0.01)

            snapshot = store.snapshot(job.job_id, "ah")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["state"], "DONE")
            self.assertEqual(snapshot["logs"], ["Loading GO report"])
            self.assertEqual(snapshot["artifact_name"], "COI Master.xlsx")
            self.assertNotIn("artifact_path", snapshot)
            self.assertEqual(store.artifact_for(job.job_id, "ah").read_bytes(), b"workbook")
            self.assertIsNone(store.snapshot(job.job_id, "viewer"))
            self.assertIsNone(store.artifact_for(job.job_id, "viewer"))

    def test_job_reports_safe_error_without_artifact(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = PreCoiJobStore(Path(temp_dir), retention_seconds=60)

            def runner(_job_dir: Path, _log):
                raise ValueError("Invalid workbook")

            job = store.start(owner="ah", action="ppo", runner=runner)
            for _ in range(20):
                snapshot = store.snapshot(job.job_id, "ah")
                if snapshot and snapshot["state"] != "RUNNING":
                    break
                time.sleep(0.01)

            snapshot = store.snapshot(job.job_id, "ah")
            self.assertEqual(snapshot["state"], "ERROR")
            self.assertEqual(snapshot["error"], "Invalid workbook")
            self.assertIsNone(store.artifact_for(job.job_id, "ah"))

    def test_draft_records_are_owner_scoped_and_revisioned(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = PreCoiJobStore(Path(temp_dir), retention_seconds=60)

            def runner(job_dir: Path, _log):
                artifact = job_dir / "COI Master.xlsx"
                artifact.write_bytes(b"workbook")
                return artifact

            job = store.start(owner="ah", action="create", runner=runner)
            for _ in range(20):
                snapshot = store.snapshot(job.job_id, "ah")
                if snapshot and snapshot["state"] != "RUNNING":
                    break
                time.sleep(0.01)

            self.assertEqual(store.set_draft_records(job.job_id, "ah", ["row-1"]), 1)
            self.assertEqual(store.draft_records_for(job.job_id, "ah"), (["row-1"], 1))
            self.assertIsNone(store.draft_records_for(job.job_id, "viewer"))
            self.assertEqual(store.replace_draft_records(job.job_id, "ah", ["row-2"], expected_revision=1), 2)
            self.assertIsNone(store.replace_draft_records(job.job_id, "ah", ["stale"], expected_revision=1))


if __name__ == "__main__":
    unittest.main()
