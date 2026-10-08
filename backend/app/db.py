"""SQLite persistence: settings, jobs and per-module state."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    finished_at REAL,
    source_version TEXT NOT NULL,
    target_version TEXT NOT NULL,
    source_dir TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '{}',
    parent_job TEXT
);
CREATE TABLE IF NOT EXISTS job_modules (
    job_id TEXT NOT NULL,
    module TEXT NOT NULL,
    position INTEGER NOT NULL,
    depends TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    session_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    db_name TEXT,
    output_path TEXT,
    started_at REAL,
    finished_at REAL,
    summary TEXT,
    PRIMARY KEY (job_id, module)
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(jobs)")}
        if "owner_pid" not in cols:
            _conn.execute("ALTER TABLE jobs ADD COLUMN owner_pid INTEGER")
        mcols = {r[1] for r in _conn.execute("PRAGMA table_info(job_modules)")}
        if "manual_test" not in mcols:
            _conn.execute("ALTER TABLE job_modules ADD COLUMN manual_test TEXT")
        _conn.commit()
    return _conn


def _exec(sql: str, params=()) -> sqlite3.Cursor:
    with _lock:
        cur = conn().execute(sql, params)
        conn().commit()
        return cur


def _all(sql: str, params=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


# ---------------------------------------------------------------- settings
def get_settings() -> dict:
    out = dict(config.DEFAULT_SETTINGS)
    for row in _all("SELECT key, value FROM settings"):
        out[row["key"]] = json.loads(row["value"])
    return out


def update_settings(values: dict) -> dict:
    for key, value in values.items():
        if key not in config.DEFAULT_SETTINGS:
            continue
        _exec("INSERT INTO settings(key, value) VALUES (?, ?) "
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
              (key, json.dumps(value)))
    return get_settings()


# ---------------------------------------------------------------- jobs
def _decode_job(row: dict) -> dict:
    row["options"] = json.loads(row["options"] or "{}")
    return row


def _decode_module(row: dict) -> dict:
    row["depends"] = json.loads(row["depends"] or "[]")
    row["steps"] = json.loads(row["steps"] or "[]")
    row["manual_test"] = json.loads(row["manual_test"]) if row.get("manual_test") else None
    return row


def create_job(source_version, target_version, source_dir, output_dir, modules,
               options, parent_job=None) -> str:
    """modules: list of (name, depends) already in dependency order."""
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    _exec("INSERT INTO jobs(id, created_at, source_version, target_version, source_dir, "
          "output_dir, status, options, parent_job, owner_pid) VALUES (?,?,?,?,?,?,?,?,?,?)",
          (job_id, time.time(), source_version, target_version, str(source_dir),
           str(output_dir), "queued", json.dumps(options), parent_job, os.getpid()))
    for pos, (name, depends) in enumerate(modules):
        _exec("INSERT INTO job_modules(job_id, module, position, depends, status) "
              "VALUES (?,?,?,?,?)", (job_id, name, pos, json.dumps(depends), "queued"))
    return job_id


def get_job(job_id: str) -> dict | None:
    rows = _all("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not rows:
        return None
    job = _decode_job(rows[0])
    job["modules"] = list_modules(job_id)
    return job


def list_jobs(limit: int = 100) -> list[dict]:
    jobs = [_decode_job(r) for r in _all(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]
    for job in jobs:
        counts = _all("SELECT status, COUNT(*) AS n FROM job_modules WHERE job_id = ? "
                      "GROUP BY status", (job["id"],))
        job["counts"] = {c["status"]: c["n"] for c in counts}
    return jobs


def update_job(job_id: str, **fields) -> None:
    if "options" in fields:
        fields["options"] = json.dumps(fields["options"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def list_modules(job_id: str) -> list[dict]:
    return [_decode_module(r) for r in _all(
        "SELECT * FROM job_modules WHERE job_id = ? ORDER BY position", (job_id,))]


def get_module(job_id: str, module: str) -> dict | None:
    rows = _all("SELECT * FROM job_modules WHERE job_id = ? AND module = ?", (job_id, module))
    return _decode_module(rows[0]) if rows else None


def update_module(job_id: str, module: str, **fields) -> None:
    if "steps" in fields:
        fields["steps"] = json.dumps(fields["steps"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE job_modules SET {cols} WHERE job_id = ? AND module = ?",
          (*fields.values(), job_id, module))


def output_owned_by_studio(output_path: str) -> bool:
    """True when a previous studio job created this output folder."""
    return bool(_all("SELECT 1 FROM job_modules WHERE output_path = ? LIMIT 1", (output_path,)))


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def interrupted_jobs() -> list[str]:
    """Unfinished jobs whose owning process (server or CLI) is gone."""
    rows = _all("SELECT id, owner_pid FROM jobs WHERE status IN ('queued', 'running')")
    return [r["id"] for r in rows if r["owner_pid"] != os.getpid() and not _alive(r["owner_pid"])]
