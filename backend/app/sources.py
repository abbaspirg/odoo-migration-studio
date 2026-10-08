"""Odoo community/enterprise source trees and per-version Python venvs."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

from . import config, db, events

# ---------------------------------------------------------------- background tasks
TASKS: dict[str, dict] = {}


def _new_task(kind: str, title: str, version: str) -> dict:
    task = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "version": version,
            "status": "running", "started_at": time.time(), "finished_at": None,
            "log": [], "progress": None}
    TASKS[task["id"]] = task
    events.publish("task", _task_view(task), persist=False)
    return task


def _task_view(task: dict) -> dict:
    return {**task, "log": task["log"][-200:]}


def _task_log(task: dict, line: str, progress: float | None = None) -> None:
    line = line.rstrip()
    if not line:
        return
    task["log"].append(line)
    del task["log"][:-2000]
    if progress is not None:
        task["progress"] = progress
    events.publish("task_log", {"id": task["id"], "line": line, "progress": task["progress"]},
                   persist=False)


def _task_done(task: dict, ok: bool, message: str = "") -> None:
    if message:
        _task_log(task, message)
    task["status"] = "done" if ok else "failed"
    task["finished_at"] = time.time()
    events.publish("task", _task_view(task), persist=False)


def running_task(kind: str, version: str) -> dict | None:
    return next((t for t in TASKS.values() if t["kind"] == kind and t["version"] == version
                 and t["status"] == "running"), None)


async def _stream_process(task: dict, cmd: list[str], cwd: Path | None = None,
                          env: dict | None = None) -> int:
    """Run cmd, feeding stdout/stderr (split on \\r and \\n, for git progress) to the task log."""
    _task_log(task, "$ " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd) if cwd else None, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    buf = b""
    pct_re = re.compile(rb"(Receiving objects|Resolving deltas|Updating files):\s+(\d+)%")
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for part in parts:
            m = pct_re.search(part)
            progress = None
            if m:
                phase = {b"Receiving objects": 0, b"Resolving deltas": 1, b"Updating files": 2}
                progress = round((phase[m.group(1)] * 100 + int(m.group(2))) / 3, 1)
                # only keep the last progress line of each phase in the log
                if task["log"] and pct_re.search(task["log"][-1].encode()):
                    task["log"].pop()
            _task_log(task, part.decode(errors="replace"), progress)
    if buf:
        _task_log(task, buf.decode(errors="replace"))
    return await proc.wait()


# ---------------------------------------------------------------- community
def _link_existing_workspace_trees() -> None:
    """Expose pre-existing ./odoo-<ver> trees (and their enterprise-addons) as sources."""
    for ver in config.VERSIONS:
        tree = config.WORKSPACE / f"odoo-{ver}"
        if not (tree / "odoo-bin").is_file():
            continue
        link = config.COMMUNITY_DIR / ver
        if not link.exists() and not link.is_symlink():
            link.symlink_to(tree)
        ent = tree / "enterprise-addons"
        elink = config.ENTERPRISE_DIR / ver
        if ent.is_dir() and not elink.exists() and not elink.is_symlink():
            elink.symlink_to(ent)


def community_path(version: str) -> Path | None:
    p = config.COMMUNITY_DIR / version
    return p if (p / "odoo-bin").is_file() else None


def community_addons_paths(version: str) -> list[Path]:
    root = community_path(version)
    if not root:
        return []
    return [p for p in (root / "addons", root / "odoo" / "addons") if p.is_dir()]


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def community_status(version: str) -> dict:
    root = config.COMMUNITY_DIR / version
    path = community_path(version)
    info = {"version": version, "present": bool(path), "path": str(root),
            "linked_to": str(root.resolve()) if root.is_symlink() else None,
            "is_git": False, "commit": None, "commit_date": None,
            "task": running_task("clone", version) or running_task("pull", version)}
    if path and (path / ".git").exists():
        info["is_git"] = True
        info["commit"] = _git(path, "rev-parse", "--short", "HEAD")
        info["commit_date"] = _git(path, "log", "-1", "--format=%ci")
    if info["task"]:
        info["task"] = _task_view(info["task"])
    return info


async def clone_community(version: str) -> dict:
    if version not in config.VERSIONS:
        raise ValueError(f"Unsupported version {version}")
    if community_path(version):
        raise ValueError(f"Community {version} is already present")
    if running_task("clone", version):
        raise ValueError("A clone of this version is already running")
    task = _new_task("clone", f"Clone Odoo community {version}", version)

    async def run():
        dest = config.COMMUNITY_DIR / version
        tmp = config.COMMUNITY_DIR / f".{version}.partial"
        if tmp.exists():
            shutil.rmtree(tmp)
        try:
            rc = await _stream_process(task, ["git", "clone", "--depth", "1", "--progress",
                                              "-b", version, config.ODOO_GIT_URL, str(tmp)])
            if rc == 0:
                tmp.rename(dest)
                _task_done(task, True, f"Cloned into {dest}")
            else:
                shutil.rmtree(tmp, ignore_errors=True)
                _task_done(task, False, f"git clone failed (exit {rc})")
        except Exception as exc:          # noqa: BLE001
            shutil.rmtree(tmp, ignore_errors=True)
            _task_done(task, False, f"Error: {exc}")

    asyncio.create_task(run())
    return _task_view(task)


async def pull_community(version: str) -> dict:
    path = community_path(version)
    if not path or not (path / ".git").exists():
        raise ValueError("Only git clones can be updated")
    if running_task("pull", version):
        raise ValueError("An update is already running")
    task = _new_task("pull", f"Update Odoo community {version}", version)

    async def run():
        rc = await _stream_process(task, ["git", "-C", str(path), "pull", "--ff-only",
                                          "--depth", "1", "--progress"])
        _task_done(task, rc == 0, "Up to date" if rc == 0 else f"git pull failed (exit {rc})")

    asyncio.create_task(run())
    return _task_view(task)


# ---------------------------------------------------------------- enterprise
def _best_addons_dir(root: Path) -> Path | None:
    """The folder (root or up to 2 levels below) holding the most addons."""
    best, best_n = None, 0
    candidates = [root] + [p for p in root.glob("*") if p.is_dir()] + \
                 [p for p in root.glob("*/*") if p.is_dir()]
    for c in candidates:
        n = sum(1 for _ in c.glob("*/__manifest__.py"))
        if n > best_n:
            best, best_n = c, n
    return best


def enterprise_path(version: str) -> Path | None:
    root = config.ENTERPRISE_DIR / version
    return _best_addons_dir(root) if root.exists() else None


def enterprise_status(version: str) -> dict:
    root = config.ENTERPRISE_DIR / version
    path = enterprise_path(version)
    count = sum(1 for _ in path.glob("*/__manifest__.py")) if path else 0
    return {"version": version, "present": bool(path), "path": str(path) if path else None,
            "linked_to": str(root.resolve()) if root.is_symlink() else None,
            "module_count": count, "task": _task_view(t) if (t := running_task(
                "enterprise", version)) else None}


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if dest != target and dest not in target.parents:
            raise ValueError(f"Unsafe path in zip: {member.filename}")
    zf.extractall(dest)


async def install_enterprise_zip(version: str, zip_path: Path) -> dict:
    task = _new_task("enterprise", f"Extract enterprise {version}", version)

    async def run():
        dest = config.ENTERPRISE_DIR / version
        tmp = config.ENTERPRISE_DIR / f".{version}.partial"
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            _task_log(task, f"Extracting {zip_path.name} …")
            await asyncio.to_thread(lambda: _safe_extract(zipfile.ZipFile(zip_path), tmp))
            found = _best_addons_dir(tmp)
            n = sum(1 for _ in found.glob("*/__manifest__.py")) if found else 0
            if not n:
                raise ValueError("No Odoo addons (__manifest__.py) found in the zip")
            if dest.is_symlink():
                dest.unlink()           # only the link: never touch what it points to
            elif dest.exists():
                shutil.rmtree(dest)
            tmp.rename(dest)
            _task_done(task, True, f"{n} enterprise addons ready in {enterprise_path(version)}")
        except Exception as exc:      # noqa: BLE001
            shutil.rmtree(tmp, ignore_errors=True)
            _task_done(task, False, f"Error: {exc}")
        finally:
            zip_path.unlink(missing_ok=True)

    asyncio.create_task(run())
    return _task_view(task)


# ---------------------------------------------------------------- venvs
def _venv_candidates(version: str) -> list[Path]:
    configured = db.get_settings().get("venvs", {}).get(version)
    cands = [Path(configured)] if configured else []
    cands.append(config.VENVS_DIR / version)
    root = config.COMMUNITY_DIR / version
    # the workspace's own ./venv belongs to the workspace's ./odoo-<ver> tree
    if root.is_symlink() and root.resolve().parent == config.WORKSPACE:
        cands.append(config.WORKSPACE / "venv")
    return cands


def venv_python(version: str) -> Path | None:
    for c in _venv_candidates(version):
        py = c / "bin" / "python"
        if py.exists():
            return py
    return None


def venv_status(version: str) -> dict:
    py = venv_python(version)
    info = {"version": version, "present": bool(py), "path": str(py.parent.parent) if py else None,
            "python": None, "deps_ok": False, "missing": [],
            "task": _task_view(t) if (t := running_task("venv", version)) else None}
    if py:
        probe = ("import importlib.util as u, sys; mods=['psycopg2','werkzeug','lxml','babel',"
                 "'PIL','dateutil','passlib','reportlab']; "
                 "print(sys.version.split()[0]); print(','.join(m for m in mods if not u.find_spec(m)))")
        try:
            out = subprocess.run([str(py), "-c", probe], capture_output=True, text=True,
                                 timeout=20).stdout.splitlines()
            info["python"] = out[0] if out else None
            info["missing"] = [m for m in (out[1].split(",") if len(out) > 1 else []) if m]
            info["deps_ok"] = bool(out) and not info["missing"]
        except (OSError, subprocess.SubprocessError):
            pass
    return info


async def create_venv(version: str, python_bin: str = "python3") -> dict:
    root = community_path(version)
    if not root:
        raise ValueError(f"Community {version} source is required first")
    if running_task("venv", version):
        raise ValueError("A venv task is already running for this version")
    existing = venv_python(version)
    target = existing.parent.parent if existing else config.VENVS_DIR / version
    task = _new_task("venv", f"Python venv for {version}", version)

    async def run():
        try:
            if not existing:
                rc = await _stream_process(task, [python_bin, "-m", "venv", str(target)])
                if rc:
                    return _task_done(task, False, "venv creation failed")
            pip = str(target / "bin" / "pip")
            rc = await _stream_process(task, [pip, "install", "-r", str(root / "requirements.txt")],
                                       env={**os.environ, "PIP_PROGRESS_BAR": "off"})
            _task_done(task, rc == 0, "Requirements installed" if rc == 0
                       else f"pip install failed (exit {rc}) — see log")
        except Exception as exc:      # noqa: BLE001
            _task_done(task, False, f"Error: {exc}")

    asyncio.create_task(run())
    return _task_view(task)


def init() -> None:
    config.ensure_dirs()
    _link_existing_workspace_trees()
