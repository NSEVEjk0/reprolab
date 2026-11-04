from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator


class AdmissionDenied(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path, *, daily_limit: int = 8, pending_limit: int = 4):
        if not 1 <= daily_limit <= 30 or not 1 <= pending_limit <= 8:
            raise ValueError("Job budgets are outside their allowed bounds")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path, self.daily_limit, self.pending_limit = path, daily_limit, pending_limit
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL, fixture_id TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, evidence TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS budgets (day TEXT PRIMARY KEY, used INTEGER NOT NULL)")
        path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, fixture_id: str) -> dict[str, str]:
        now = datetime.now(timezone.utc).isoformat()
        day = now[:10]
        job_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','reproducing','planning','retrying','testing')").fetchone()[0]
            if pending >= self.pending_limit:
                raise AdmissionDenied("The repair queue is full. Try after the current jobs finish")
            db.execute("INSERT INTO budgets(day,used) VALUES (?,0) ON CONFLICT(day) DO NOTHING", (day,))
            changed = db.execute("UPDATE budgets SET used=used+1 WHERE day=? AND used<?", (day, self.daily_limit))
            if changed.rowcount != 1:
                raise AdmissionDenied("Today's sponsored repair allowance has been used")
            db.execute("INSERT INTO jobs(id,token_sha256,fixture_id,status,message,created_at,updated_at) VALUES (?,?,?,'queued',?,?,?)",
                       (job_id, hashlib.sha256(token.encode()).hexdigest(), fixture_id, "Waiting for the isolated repair worker", now, now))
        return {"id": job_id, "token": token, "status": "queued"}

    def update(self, job_id: str, status: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        allowed = {"queued", "reproducing", "planning", "retrying", "testing", "verified", "failed", "unavailable", "interrupted"}
        if status not in allowed:
            raise ValueError("Unknown job state")
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,message=?,updated_at=?,evidence=COALESCE(?,evidence) WHERE id=?",
                       (status, message[:500], datetime.now(timezone.utc).isoformat(), json.dumps(evidence) if evidence is not None else None, job_id))

    def read(self, job_id: str, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["token_sha256"], hashlib.sha256(token.encode()).hexdigest()):
            return None
        return {"id": row["id"], "fixture_id": row["fixture_id"], "status": row["status"], "message": row["message"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "evidence": json.loads(row["evidence"]) if row["evidence"] is not None else None}

    def recover(self) -> None:
        """No automatic paid retries after a controller restart."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',message='The controller restarted. No paid retry was started.' WHERE status IN ('queued','reproducing','planning','retrying','testing')")
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            db.execute("DELETE FROM jobs WHERE created_at<?", (cutoff,))
            db.execute("DELETE FROM budgets WHERE day<?", (cutoff[:10],))
