"""Running odoo-bin for install/test steps, plus test-database housekeeping."""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import socket
import time
from pathlib import Path

import psycopg2
from psycopg2 import sql

from . import events, sources
from .procs import CancelToken, run_streaming

LEVEL_RE = re.compile(r"^\S+ \S+ \d+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
RESULT_RE = re.compile(r"(\d+) failed, (\d+) error\(s\) of (\d+) tests")


def _pg_connect(settings: dict, dbname: str = "postgres"):
    return psycopg2.connect(host=settings["db_host"] or None, port=int(settings["db_port"]),
                            user=settings["db_user"], password=settings["db_password"] or None,
                            dbname=dbname, connect_timeout=5)


def check_postgres(settings: dict) -> dict:
    try:
        with _pg_connect(settings) as conn, conn.cursor() as cr:
            cr.execute("SELECT rolcreatedb, rolsuper, version() FROM pg_roles, "
                       "(SELECT 1) x WHERE rolname = current_user")
            createdb, superuser, version = cr.fetchone()
        conn.close()
        error = None
        if superuser and settings["db_user"] == "postgres":
            error = "Odoo refuses to run as the 'postgres' user; create a dedicated role."
        elif not createdb:
            error = f"Role {settings['db_user']!r} lacks CREATEDB."
        return {"ok": error is None, "createdb": createdb, "version": version.split(",")[0],
                "error": error}
    except Exception as exc:          # noqa: BLE001
        return {"ok": False, "error": str(exc).strip()}


def module_state(settings: dict, dbname: str, module: str) -> str | None:
    try:
        conn = _pg_connect(settings, dbname)
        with conn.cursor() as cr:
            cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (module,))
            row = cr.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:                 # noqa: BLE001
        return None


def drop_database(settings: dict, dbname: str) -> str | None:
    if not dbname.startswith("mig_"):
        return f"Refusing to drop non-studio database {dbname!r}"
    try:
        conn = _pg_connect(settings)
        conn.autocommit = True
        with conn.cursor() as cr:
            cr.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))
        conn.close()
        return None
    except Exception as exc:          # noqa: BLE001
        return str(exc).strip()


def list_studio_databases(settings: dict) -> list[str]:
    try:
        conn = _pg_connect(settings)
        with conn.cursor() as cr:
            cr.execute("SELECT datname FROM pg_database WHERE datname LIKE 'mig\\_%' ORDER BY 1")
            names = [r[0] for r in cr.fetchall()]
        conn.close()
        return names
    except Exception:                 # noqa: BLE001
        return []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def addons_path(target: str, output_dir: Path) -> str:
    paths = [str(p) for p in sources.community_addons_paths(target)
             if not p.as_posix().endswith("odoo/addons")]
    ent = sources.enterprise_path(target)
    if ent:
        paths.append(str(ent))
    paths.append(str(output_dir))
    return ",".join(paths)


def build_command(target: str, output_dir: Path, dbname: str, module: str, settings: dict,
                  mode: str, http_port: int | None = None) -> list[str]:
    """mode: install | test | manual (serve, module already installed) | manual_install."""
    root = sources.community_path(target)
    py = sources.venv_python(target)
    if not root or not py:
        raise RuntimeError(f"Odoo {target} source or venv missing (see Versions & Sources)")
    cmd = [str(py), str(root / "odoo-bin"), "-d", dbname,
           f"--addons-path={addons_path(target, output_dir)}",
           f"--db_host={settings['db_host']}", f"--db_port={settings['db_port']}",
           f"--db_user={settings['db_user']}",
           f"--http-port={http_port or free_port()}", "--http-interface=127.0.0.1"]
    if settings.get("db_password"):
        cmd.append(f"--db_password={settings['db_password']}")
    if mode == "install":
        cmd += ["--stop-after-init", "-i", module, "--log-level=warn"]
    elif mode == "test":
        # tests run on update so at_install tests execute again; `test` level logs the result line
        cmd += ["--stop-after-init", "-u", module, "--test-enable", f"--test-tags=/{module}",
                "--log-level=test"]
    else:
        # a long-running server restricted to this one database, for manual testing
        cmd += [f"--db-filter=^{re.escape(dbname)}$", "--log-level=info"]
        if mode == "manual_install":
            cmd += ["-i", module]
    cmd += shlex.split(settings.get("odoo_extra_args") or "")
    return cmd


def database_exists(settings: dict, dbname: str) -> bool:
    try:
        conn = _pg_connect(settings)
        with conn.cursor() as cr:
            cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            found = cr.fetchone() is not None
        conn.close()
        return found
    except Exception:                 # noqa: BLE001
        return False


def free_port_near(start: int = 8069, end: int = 8099) -> int:
    """A free port in Odoo's usual range (nicer URLs), else any free port."""
    for port in range(start, end + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return free_port()


class OdooRun:
    def __init__(self, job_id: str, module: str, step: str, attempt: int, log_dir: Path):
        self.job_id, self.module, self.step, self.attempt = job_id, module, step, attempt
        self.log_file = log_dir / f"odoo_{step}_{attempt}.log"
        self.errors: list[str] = []
        self.result_line: str | None = None
        self._buffer: list[dict] = []
        self._last_flush = 0.0
        self._in_error = False
        self._error_block: list[str] = []

    def _flush(self, force: bool = False) -> None:
        if self._buffer and (force or time.time() - self._last_flush > 0.3):
            events.publish("odoo_log", {"step": self.step, "attempt": self.attempt,
                                        "lines": self._buffer}, job_id=self.job_id, module=self.module)
            self._buffer = []
            self._last_flush = time.time()

    def _close_error_block(self) -> None:
        if self._error_block:
            self.errors.append("\n".join(self._error_block[:80]))
            self._error_block = []

    def _on_line(self, fh, line: str) -> None:
        fh.write(line + "\n")
        m = LEVEL_RE.match(line)
        level = m.group(1) if m else None
        if level:
            self._close_error_block()
            self._in_error = level in ("ERROR", "CRITICAL")
            if self._in_error:
                self._error_block.append(line)
        elif self._in_error:                   # traceback continuation lines
            self._error_block.append(line)
        if RESULT_RE.search(line):
            self.result_line = RESULT_RE.search(line).group(0)
        shown_level = level or ("ERROR" if self._in_error else None)
        self._buffer.append({"t": line[:2000], "l": shown_level})
        self._flush()

    async def run(self, cmd: list[str], token: CancelToken, timeout: float | None) -> int:
        env = dict(os.environ)
        env.pop("PGPASSWORD", None)
        safe_cmd = [c if not c.startswith("--db_password=") else "--db_password=***" for c in cmd]
        events.publish("odoo_start", {"step": self.step, "attempt": self.attempt,
                                      "command": " ".join(safe_cmd), "log_file": str(self.log_file)},
                       job_id=self.job_id, module=self.module)
        with open(self.log_file, "w") as fh:
            fh.write("$ " + " ".join(safe_cmd) + "\n")
            try:
                rc, _ = await run_streaming(cmd, cwd=Path(cmd[1]).parent, env=env, token=token,
                                            on_line=lambda line: self._on_line(fh, line),
                                            timeout=timeout)
            finally:
                self._close_error_block()
                self._flush(force=True)
        return rc

    def error_excerpt(self, limit: int = 12000) -> str:
        text = "\n\n".join(self.errors)
        if not text and self.log_file.exists():
            text = "\n".join(self.log_file.read_text(errors="replace").splitlines()[-120:])
        return text[-limit:]


async def run_step(job_id, module, step, attempt, log_dir, target, output_dir, dbname,
                   settings, token) -> dict:
    """Install or test ``module``; returns a verdict dict."""
    run = OdooRun(job_id, module, step, attempt, log_dir)
    cmd = build_command(target, output_dir, dbname, module, settings, step)
    timeout = float(settings.get("install_timeout" if step == "install" else "test_timeout") or 1800)
    rc = await run.run(cmd, token, timeout)
    state = await asyncio.to_thread(module_state, settings, dbname, module)
    verdict = {"returncode": rc, "module_state": state, "errors": len(run.errors),
               "result_line": run.result_line, "log_file": str(run.log_file)}
    problems = []
    if rc != 0:
        problems.append(f"odoo-bin exited with code {rc}")
    if state != "installed":
        problems.append(f"module state is {state!r}, expected 'installed'")
    if run.errors:
        problems.append(f"{len(run.errors)} ERROR/CRITICAL log entries")
    if step == "test":
        m = RESULT_RE.search(run.result_line or "")
        if m:
            failed, errored, total = map(int, m.groups())
            verdict.update(tests_failed=failed, tests_errors=errored, tests_total=total)
            if failed or errored:
                problems.append(run.result_line)
    verdict["ok"] = not problems
    verdict["problems"] = problems
    verdict["error_excerpt"] = run.error_excerpt() if problems else ""
    return verdict
