"""Background job runner: spawns the CLI as a subprocess and streams its log."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import uuid
from collections import deque
from pathlib import Path
from queue import Empty, Queue
from typing import Optional

from ..agent.config import load_dotenv
from ..models import utc_now_iso
from . import store as store_module

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = PROJECT_ROOT / "main.py"
LOG_BUFFER_LINES = 2000

_SAVED_RE = re.compile(
    r"Saved (\d+) listings \((\d+) new, (\d+) updated, (\d+) price changes\)"
)
_AGENT_RUN_RE = re.compile(r"Run #(\d+):")
_AGENT_SEEN_RE = re.compile(r"\[agent\] Run #\d+: (\d+) listing")
_AGENT_SCORED_RE = re.compile(r"\[agent\] \d+/\d+ listing \d+: ")


class JobBusyError(RuntimeError):
    """Raised when a job is already running."""


class Job:
    def __init__(self, job_id: str, kind: str, run_id: int, argv: list[str]):
        self.job_id = job_id
        self.kind = kind
        self.run_id = run_id
        self.argv = argv
        self.started_at = utc_now_iso()
        self.finished_at: Optional[str] = None
        self.status = "running"
        self.exit_code: Optional[int] = None
        self.lines: deque[str] = deque(maxlen=LOG_BUFFER_LINES)
        self.subscribers: set[Queue] = set()
        self.process: Optional[subprocess.Popen] = None
        self.counters: dict = {}

    def snapshot(self, include_log: bool = False) -> dict:
        data = {
            "job_id": self.job_id,
            "kind": self.kind,
            "run_id": self.run_id,
            "command": " ".join(self.argv),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "exit_code": self.exit_code,
            "counters": self.counters,
        }
        if include_log:
            data["log"] = "\n".join(self.lines)
        return data

    def extract_counters(self) -> dict:
        """Pull result counters out of the CLI's human-readable output."""
        counters: dict = {}
        for line in self.lines:
            match = _SAVED_RE.search(line)
            if match:
                counters["inserted"] = int(match.group(2))
                counters["updated"] = int(match.group(3))
                counters["price_changes"] = int(match.group(4))
                continue
            seen = _AGENT_SEEN_RE.search(line)
            if seen:
                counters["listings_seen"] = int(seen.group(1))
            if _AGENT_SCORED_RE.search(line):
                counters["listings_scored"] = counters.get("listings_scored", 0) + 1
            run = _AGENT_RUN_RE.search(line)
            if run:
                counters["agent_run_id"] = int(run.group(1))
        return counters


def child_env() -> dict:
    """Environment for the subprocess, with .env merged in."""
    env = dict(os.environ)
    load_dotenv(PROJECT_ROOT / ".env")
    env.update(
        {
            key: value
            for key, value in os.environ.items()
            if key in ("PROPERTYBOT_DB", "MALL_DB_PATH", "AGENT_REPORTS_DIR",
                       "AGENT_TOP_N", "OPENROUTER_API_KEY", "OPENROUTER_MODEL")
        }
    )
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kill_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True, check=False,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


class JobManager:
    """One job at a time — Playwright and Cloudflare must not be driven twice."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: Optional[Job] = None

    def active(self) -> Optional[Job]:
        with self._lock:
            return self._active

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
        return [job.snapshot() for job in jobs[:limit]]

    def start(self, kind: str, argv: list[str], run_id: int) -> Job:
        with self._lock:
            if self._active is not None and self._active.status == "running":
                raise JobBusyError(
                    f"A {self._active.kind} job is already running "
                    f"(run #{self._active.run_id})."
                )
            job = Job(uuid.uuid4().hex, kind, run_id, argv)
            self._jobs[job.job_id] = job
            self._active = job

        job.process = self._spawn(job)
        thread = threading.Thread(target=self._pump, args=(job,), daemon=True)
        thread.start()
        return job

    def _spawn(self, job: Job) -> subprocess.Popen:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(
            job.argv,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env(),
            **kwargs,
        )

    def _pump(self, job: Job) -> None:
        process = job.process
        try:
            if process is not None and process.stdout is not None:
                for line in process.stdout:
                    self._emit(job, line.rstrip("\r\n"))
        finally:
            if process is not None:
                job.exit_code = process.wait()
                try:
                    if process.stdout is not None:
                        process.stdout.close()
                except OSError:
                    pass
            self._complete(job)

    def _emit(self, job: Job, line: str) -> None:
        job.lines.append(line)
        for queue in list(job.subscribers):
            try:
                queue.put_nowait(line)
            except Exception:
                job.subscribers.discard(queue)

    def _complete(self, job: Job) -> None:
        job.counters = job.extract_counters()
        job.finished_at = utc_now_iso()
        job.status = "done" if job.exit_code == 0 else "failed"
        for queue in list(job.subscribers):
            queue.put_nowait(None)
        self._persist(job)
        with self._lock:
            if self._active is job:
                self._active = None

    def _persist(self, job: Job) -> None:
        from .. import db as db_module

        conn = db_module.connect()
        try:
            store_module.ensure_schema(conn)
            store_module.finish_run(
                conn,
                job.run_id,
                finished_at=job.finished_at or utc_now_iso(),
                status=job.status,
                exit_code=job.exit_code,
                counters=job.counters,
                log="\n".join(job.lines),
            )
        finally:
            conn.close()

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status != "running" or job.process is None:
            return False
        _kill_tree(job.process)
        return True

    def subscribe(self, job: Job) -> Queue:
        """A fresh queue pre-seeded with the buffered log, then fed live."""
        queue: Queue = Queue()
        with self._lock:
            for line in job.lines:
                queue.put_nowait(line)
            if job.status != "running":
                queue.put_nowait(None)
            else:
                job.subscribers.add(queue)
        return queue

    def unsubscribe(self, job: Job, queue: Queue) -> None:
        with self._lock:
            job.subscribers.discard(queue)

    def close(self) -> None:
        job = self.active()
        if job is not None:
            self.cancel(job.job_id)


manager = JobManager()
