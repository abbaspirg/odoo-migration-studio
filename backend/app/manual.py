"""Manual testing: run a real Odoo server with the migrated module for a human to click through."""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path

from . import config, db, events, odoo_runner
from .procs import CancelToken, Cancelled

SERVERS: dict[str, dict] = {}        # "<job_id>/<module>" -> server info


def _key(job_id: str, module: str) -> str:
    return f"{job_id}/{module}"


def _view(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


def _publish(s: dict) -> None:
    events.publish("manual", _view(s), job_id=s["job_id"], module=s["module"])


def list_servers() -> list[dict]:
    return [_view(s) for s in SERVERS.values()]


def get_server(job_id: str, module: str) -> dict | None:
    s = SERVERS.get(_key(job_id, module))
    return _view(s) if s else None


async def _wait_ready(s: dict) -> None:
    url = f"http://127.0.0.1:{s['port']}/web/login"

    def probe() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.status == 200
        except Exception:             # noqa: BLE001 - not up yet
            return False

    while s["status"] == "starting":
        if await asyncio.to_thread(probe):
            s["status"] = "ready"
            s["ready_at"] = time.time()
            _publish(s)
            return
        await asyncio.sleep(1.5)


async def start(job: dict, module: str, fresh: bool = False, demo: bool = False) -> dict:
    key = _key(job["id"], module)
    if key in SERVERS and SERVERS[key]["status"] in ("starting", "ready"):
        raise ValueError("A manual test server is already running for this module")
    out_dir = Path(job["output_dir"])
    if not (out_dir / module / "__manifest__.py").is_file():
        raise ValueError("No migrated output for this module")
    settings = db.get_settings()
    mod = db.get_module(job["id"], module) or {}

    # reuse a database that already has the module installed (e.g. "keep DB" or a
    # previous manual session) and the requested demo data setting, unless a fresh one is requested
    dbname, install = None, True
    # oldest → newest; earlier manual databases are found in Postgres so they survive a restart
    earlier = sorted(d for d in await asyncio.to_thread(odoo_runner.list_studio_databases, settings)
                     if d.startswith(f"mig_{module}_manual_".lower()))
    candidates = [mod.get("db_name")] + earlier + [s["db"] for s in SERVERS.values()
                                                   if s["job_id"] == job["id"] and s["module"] == module]
    if not fresh:
        for cand in filter(None, reversed(candidates)):
            if await asyncio.to_thread(odoo_runner.database_exists, settings, cand):
                state = await asyncio.to_thread(odoo_runner.module_state, settings, cand, module)
                has_demo = await asyncio.to_thread(odoo_runner.demo_loaded, settings, cand)
                if state == "installed" and has_demo == demo:
                    dbname, install = cand, False
                    break
    if not dbname:
        dbname = f"mig_{module}_manual_{time.strftime('%Y%m%d%H%M%S')}"[:63].lower()

    port = odoo_runner.free_port_near()
    n = int(time.time())
    log_dir = events.module_log_dir(job["id"], module)
    cmd = odoo_runner.build_command(job["target_version"], out_dir, dbname, module, settings,
                                    "manual_install" if install else "manual", http_port=port,
                                    with_demo=demo and install)
    token = CancelToken()
    s = {"job_id": job["id"], "module": module, "db": dbname, "port": port,
         "url": f"http://127.0.0.1:{port}/odoo", "login": "admin", "password": "admin",
         "installing": install, "demo": demo, "status": "starting", "started_at": time.time(),
         "ready_at": None, "stopped_at": None, "exit_code": None, "error": None,
         "log_file": str(log_dir / f"odoo_manual_{n}.log"), "_token": token}
    SERVERS[key] = s
    _publish(s)
    run = odoo_runner.OdooRun(job["id"], module, "manual", n, log_dir)

    async def serve():
        try:
            rc = await run.run(cmd, token, None)
            s["exit_code"] = rc
            if s["status"] != "stopping":
                s["error"] = f"Odoo exited with code {rc} — see the Odoo log tab"
            s["status"] = "stopped" if s["status"] == "stopping" else "crashed"
        except Cancelled:
            s["status"] = "stopped"
        except Exception as exc:      # noqa: BLE001
            s["status"], s["error"] = "crashed", str(exc)
        s["stopped_at"] = time.time()
        _publish(s)

    asyncio.create_task(serve())
    asyncio.create_task(_wait_ready(s))
    return _view(s)


def stop(job_id: str, module: str) -> bool:
    s = SERVERS.get(_key(job_id, module))
    if not s or s["status"] not in ("starting", "ready"):
        return False
    s["status"] = "stopping"
    _publish(s)
    s["_token"].cancel()
    return True


def stop_all() -> None:
    for s in SERVERS.values():
        if s["status"] in ("starting", "ready"):
            s["status"] = "stopping"
            s["_token"].cancel()


# ---------------------------------------------------------------- errors seen while testing
LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \d+ (\w+) ")
HTTP_500 = re.compile(r'" 50\d ')


def server_errors(job_id: str, module: str, limit: int = 8000) -> str:
    """ERROR/CRITICAL blocks (with tracebacks) and HTTP 5xx lines from the latest manual server log."""
    s = SERVERS.get(_key(job_id, module))
    path = Path(s["log_file"]) if s else None
    if not path or not path.exists():
        logs = sorted(events.module_log_dir(job_id, module).glob("odoo_manual_*.log"))
        path = logs[-1] if logs else None
    if not path or not path.exists():
        return ""
    blocks, current = [], None
    for line in path.read_text(errors="replace").splitlines():
        m = LOG_LINE.match(line)
        if m:
            current = None
            if m.group(1) in ("ERROR", "CRITICAL"):
                current = [line]
                blocks.append(current)
            elif HTTP_500.search(line):
                blocks.append([line])
        elif current is not None:
            current.append(line)          # traceback lines belong to the ERROR above them
    return "\n\n".join("\n".join(b) for b in blocks)[-limit:]


# ---------------------------------------------------------------- recording the verdict
MANUAL_HEADER = "## Manual test"


def record_result(job: dict, module: str, result: str, notes: str) -> dict:
    if result not in ("passed", "failed"):
        raise ValueError("result must be 'passed' or 'failed'")
    entry = {"result": result, "notes": notes.strip(), "at": time.time(),
             "db": (SERVERS.get(_key(job["id"], module)) or {}).get("db")}
    db.update_module(job["id"], module, manual_test=json.dumps(entry))

    # add/replace the "Manual test" section of migration-notes/<module>.md
    path = config.NOTES_DIR / f"{module}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else f"# `{module}`\n"
    text = re.sub(rf"\n{MANUAL_HEADER}\n.*?(?=\n## |\Z)", "\n", text, flags=re.S).rstrip() + "\n"
    section = [f"\n{MANUAL_HEADER}\n",
               f"- **Result:** {result.upper()}",
               f"- **Date:** {time.strftime('%Y-%m-%d %H:%M')}",
               f"- **Database:** `{entry['db'] or '—'}`",
               "", notes.strip() or "_No notes._", ""]
    path.write_text(text + "\n".join(section), encoding="utf-8")
    events.publish("manual_result", entry, job_id=job["id"], module=module)
    return entry
