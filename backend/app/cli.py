"""Run a migration job from the terminal (same pipeline as the web UI).

    cd migration-studio/backend
    ../.venv/bin/python -m app.cli --from 19.0 --to 20.0 my_module other_module
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time

from . import claude_runner, config, db, events, odoo_runner, pipeline, sources

COLORS = {"passed": "\033[32m", "failed": "\033[31m", "running": "\033[36m",
          "skipped": "\033[90m", "cancelled": "\033[33m"}
RESET = "\033[0m"


def show(ev: dict, verbose: bool) -> None:
    k, d, mod = ev["kind"], ev["data"], ev.get("module") or ""
    ts = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
    if k == "step":
        s = d["step"]
        c = COLORS.get(s["status"], "")
        dur = f" ({s['duration']}s)" if s.get("duration") else ""
        print(f"{ts} [{mod}] {c}{s['label']:<17} {s['status']:<8}{RESET}{dur} {s.get('summary') or ''}")
    elif k == "claude_tool":
        target = d.get("path") or d.get("command") or d.get("pattern") or ""
        print(f"{ts} [{mod}]   ↳ {d['name']} {str(target)[:110]}")
    elif k == "claude_text" and verbose:
        print(f"{ts} [{mod}]   💬 {d['text'][:300]}")
    elif k == "claude_result":
        print(f"{ts} [{mod}]   ✔ claude {d['subtype']} turns={d['num_turns']} "
              f"cost=${(d.get('cost_usd') or 0):.2f}")
    elif k == "odoo_start":
        print(f"{ts} [{mod}]   $ odoo {d['step']} → {d['log_file']}")
    elif k == "odoo_log":
        for line in d["lines"]:
            if line["l"] in ("ERROR", "CRITICAL") or (verbose and line["l"] == "WARNING"):
                print(f"{ts} [{mod}]   ! {line['t'][:220]}")
    elif k in ("module", "job"):
        print(f"{ts} [{mod or 'job'}] ==> {d['status'].upper()}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="19.0")
    ap.add_argument("--to", dest="tgt", default="20.0")
    ap.add_argument("--source-dir", default=str(config.DEFAULT_CUSTOM_DIR))
    ap.add_argument("--keep-db", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="back up and replace an existing output")
    ap.add_argument("--max-fix-attempts", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("modules", nargs="+")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    sources.init()
    db.conn()
    claude = await claude_runner.cli_status()
    if claude["error"]:
        print("Claude Code:", claude["error"])
        return 2
    print(f"Claude Code {claude['version']} ({claude['auth_method']}, logged in)")
    pg = odoo_runner.check_postgres(db.get_settings())
    if not pg["ok"]:
        print("PostgreSQL:", pg["error"], "\n→ set db_host/db_port/db_user/db_password in Settings")
        return 2
    if not sources.community_path(args.tgt) or not sources.venv_python(args.tgt):
        print(f"Odoo {args.tgt} source/venv missing")
        return 2

    options = {"keep_db": args.keep_db, "overwrite": args.overwrite}
    if args.max_fix_attempts is not None:
        options["max_fix_attempts"] = args.max_fix_attempts
    job_id = pipeline.create_job(args.src, args.tgt, args.source_dir, args.modules, options)
    print(f"Job {job_id}: {' → '.join(m['module'] for m in db.get_job(job_id)['modules'])}")
    q = events.subscribe()
    task = pipeline.start_job(job_id)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # graceful: kills the running subprocess, then report + cleanup still run
        loop.add_signal_handler(sig, lambda: (print("\nCancelling…"), pipeline.cancel_job(job_id)))

    async def printer():
        while True:
            show(await q.get(), args.verbose)

    ptask = asyncio.create_task(printer())
    status = await task
    await asyncio.sleep(0.2)
    ptask.cancel()
    print(f"\nJob {job_id} finished: {status}. Logs: {config.LOGS_DIR / job_id}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
