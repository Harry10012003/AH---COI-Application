from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import threading
from typing import Any, Callable
from uuid import uuid4

from .exceptions import GetYYError


JobRunner = Callable[[Path, Callable[[str], None]], Path]


@dataclass
class PreCoiJob:
    job_id: str
    owner: str
    action: str
    job_dir: Path
    created_at: datetime
    state: str = "RUNNING"
    logs: list[str] = field(default_factory=list)
    error: str = ""
    artifact_path: Path | None = None
    draft_records: list[Any] | None = None
    draft_revision: int = 0


class PreCoiJobStore:
    """Owns short-lived Pre-COI workbooks without exposing local paths."""

    def __init__(self, root_dir: Path, *, retention_seconds: int = 1800) -> None:
        self.root_dir = root_dir.resolve()
        self.retention = timedelta(seconds=max(60, retention_seconds))
        self._jobs: dict[str, PreCoiJob] = {}
        self._lock = threading.RLock()

    def start(self, *, owner: str, action: str, runner: JobRunner) -> PreCoiJob:
        self._cleanup_expired()
        job_id = uuid4().hex
        job_dir = self.root_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        job = PreCoiJob(
            job_id=job_id,
            owner=str(owner or "").strip().casefold(),
            action=str(action or "").strip().lower(),
            job_dir=job_dir,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._jobs[job_id] = job

        worker = threading.Thread(target=self._run, args=(job, runner), daemon=True, name=f"precoi-{job_id[:8]}")
        worker.start()
        return job

    def snapshot(self, job_id: str, owner: str) -> dict | None:
        job = self._owned_job(job_id, owner)
        if job is None:
            return None
        with self._lock:
            return {
                "job_id": job.job_id,
                "action": job.action,
                "state": job.state,
                "logs": list(job.logs),
                "error": job.error,
                "artifact_name": job.artifact_path.name if job.artifact_path else "",
            }

    def artifact_for(self, job_id: str, owner: str) -> Path | None:
        job = self._owned_job(job_id, owner)
        if job is None:
            return None
        with self._lock:
            artifact = job.artifact_path
            if job.state != "DONE" or artifact is None or not artifact.is_file():
                return None
            return artifact

    def set_draft_records(self, job_id: str, owner: str, records: list[Any]) -> int | None:
        job = self._owned_job(job_id, owner)
        if job is None:
            return None
        with self._lock:
            if job.state != "DONE":
                return None
            job.draft_records = list(records)
            job.draft_revision += 1
            return job.draft_revision

    def draft_records_for(self, job_id: str, owner: str) -> tuple[list[Any], int] | None:
        job = self._owned_job(job_id, owner)
        if job is None:
            return None
        with self._lock:
            if job.state != "DONE" or job.draft_records is None:
                return None
            return list(job.draft_records), job.draft_revision

    def replace_draft_records(
        self,
        job_id: str,
        owner: str,
        records: list[Any],
        *,
        expected_revision: int,
    ) -> int | None:
        job = self._owned_job(job_id, owner)
        if job is None:
            return None
        with self._lock:
            if job.state != "DONE" or job.draft_records is None or job.draft_revision != expected_revision:
                return None
            job.draft_records = list(records)
            job.draft_revision += 1
            return job.draft_revision

    def _run(self, job: PreCoiJob, runner: JobRunner) -> None:
        try:
            artifact = runner(job.job_dir, lambda message: self._append_log(job, message))
            artifact_path = Path(artifact).resolve()
            if not artifact_path.is_relative_to(job.job_dir) or not artifact_path.is_file():
                raise ValueError("Pre-COI job did not create a workbook.")
            with self._lock:
                job.artifact_path = artifact_path
                job.state = "DONE"
        except GetYYError as exc:
            self._fail(job, str(exc))
        except ValueError as exc:
            self._fail(job, str(exc))
        except Exception:
            self._fail(job, "Processing failed. Check source access and try again.")

    def _append_log(self, job: PreCoiJob, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        with self._lock:
            job.logs.append(text[:1000])
            if len(job.logs) > 300:
                del job.logs[:-300]

    def _fail(self, job: PreCoiJob, message: str) -> None:
        with self._lock:
            job.error = str(message or "Processing failed.")[:1000]
            job.state = "ERROR"

    def _owned_job(self, job_id: str, owner: str) -> PreCoiJob | None:
        normalized_owner = str(owner or "").strip().casefold()
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            return job if job and job.owner == normalized_owner else None

    def _cleanup_expired(self) -> None:
        cutoff = datetime.now(UTC) - self.retention
        with self._lock:
            expired = [job for job in self._jobs.values() if job.created_at < cutoff]
            for job in expired:
                self._jobs.pop(job.job_id, None)
        for job in expired:
            shutil.rmtree(job.job_dir, ignore_errors=True)
