"""In-process pub/sub that feeds the WebSocket and persists job events for replay."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import config

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def module_log_dir(job_id: str, module: str | None = None) -> Path:
    d = config.LOGS_DIR / job_id
    if module:
        d = d / module
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish(kind: str, data: dict | None = None, *, job_id: str | None = None,
            module: str | None = None, persist: bool = True) -> dict:
    event = {"kind": kind, "ts": time.time(), "job_id": job_id, "module": module,
             "data": data or {}}
    if persist and job_id and module:
        with open(module_log_dir(job_id, module) / "events.jsonl", "a") as fh:
            fh.write(config.dumps(event) + "\n")
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:          # slow client: drop rather than block the pipeline
            pass
    return event


def replay(job_id: str, module: str, kinds: set[str] | None = None,
           limit: int = 5000) -> list[dict]:
    path = config.LOGS_DIR / job_id / module / "events.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if kinds is None or ev["kind"] in kinds:
                out.append(ev)
    return out[-limit:]
