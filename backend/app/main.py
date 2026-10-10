"""Odoo Migration Studio — FastAPI entry point (bind to 127.0.0.1 only)."""
from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (ask, claude_runner, config, db, diffs, events, manual, modules, odoo_runner,
               pipeline, reports, sources)

CLAUDE_STATUS: dict = {}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


async def refresh_claude_status() -> dict:
    CLAUDE_STATUS.clear()
    CLAUDE_STATUS.update(await claude_runner.cli_status(), checked_at=time.time())
    return CLAUDE_STATUS


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sources.init()
    db.conn()
    ask.init()
    pipeline.recover_interrupted()
    await refresh_claude_status()
    yield
    manual.stop_all()
    for thread_id in list(ask.RUNNING):
        ask.cancel(thread_id)
    for job_id in list(pipeline.RUNNING):
        pipeline.cancel_job(job_id)


app = FastAPI(title="Odoo Migration Studio", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Reject non-local Host headers (DNS-rebinding guard; the server binds 127.0.0.1)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    return await call_next(request)


def _bad(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail=msg)


# ---------------------------------------------------------------- status & settings
@app.get("/api/status")
async def status(refresh: bool = False):
    claude = await refresh_claude_status() if refresh or not CLAUDE_STATUS else CLAUDE_STATUS
    settings = db.get_settings()
    pg = await asyncio.to_thread(odoo_runner.check_postgres, settings)
    return {"claude": claude, "postgres": pg, "workspace": str(config.WORKSPACE),
            "rules_file": config.RULES_FILE.exists(), "rules_path": str(config.RULES_FILE),
            "rules_bundled": config.RULES_FILE == config.BUNDLED_RULES.resolve(),
            "reference_module": str(config.REFERENCE_MODULE)
            if (config.REFERENCE_MODULE / "__manifest__.py").is_file() else None,
            "versions": config.VERSIONS, "default_custom_dir": str(config.DEFAULT_CUSTOM_DIR),
            # never the key itself, only whether one is there
            "api_key_allowed": config.ALLOW_API_KEY,
            "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(values: dict):
    return db.update_settings(values)


# ---------------------------------------------------------------- versions & sources
@app.get("/api/versions")
def versions():
    return [{"version": v, "community": sources.community_status(v),
             "enterprise": sources.enterprise_status(v), "venv": sources.venv_status(v)}
            for v in config.VERSIONS]


@app.post("/api/versions/{version}/clone")
async def clone(version: str):
    try:
        return await sources.clone_community(version)
    except ValueError as exc:
        _bad(str(exc))


@app.post("/api/versions/{version}/pull")
async def pull(version: str):
    try:
        return await sources.pull_community(version)
    except ValueError as exc:
        _bad(str(exc))


class VenvRequest(BaseModel):
    python_bin: str = "python3"


@app.post("/api/versions/{version}/venv")
async def venv(version: str, body: VenvRequest):
    try:
        return await sources.create_venv(version, body.python_bin)
    except ValueError as exc:
        _bad(str(exc))


async def _save_upload(upload: UploadFile) -> Path:
    if not (upload.filename or "").lower().endswith(".zip"):
        _bad("Please upload a .zip file")
    dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex}.zip"
    with open(dest, "wb") as fh:
        while chunk := await upload.read(1 << 20):
            fh.write(chunk)
    if not zipfile.is_zipfile(dest):
        dest.unlink()
        _bad("Not a valid zip archive")
    return dest


@app.post("/api/versions/{version}/enterprise")
async def enterprise_upload(version: str, file: UploadFile = File(...)):
    if version not in config.VERSIONS:
        _bad("Unknown version")
    return await sources.install_enterprise_zip(version, await _save_upload(file))


@app.get("/api/tasks")
def tasks():
    return [sources._task_view(t) for t in sorted(sources.TASKS.values(),
                                                  key=lambda t: -t["started_at"])]


# ---------------------------------------------------------------- custom modules
@app.get("/api/modules/scan")
def scan(path: str, source_version: str = "", target_version: str = ""):
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = config.WORKSPACE / root
    if not root.is_dir():
        _bad(f"Folder not found: {root}")
    return {"path": str(root.resolve()),
            "modules": modules.scan(root, [v for v in (target_version, source_version) if v],
                                    config.output_dir_for(target_version) if target_version else None)}


@app.post("/api/modules/upload")
async def upload_modules(file: UploadFile = File(...)):
    zpath = await _save_upload(file)
    dest = config.UPLOADS_DIR / f"{Path(file.filename).stem}-{time.strftime('%Y%m%d%H%M%S')}"
    try:
        with zipfile.ZipFile(zpath) as zf:
            sources._safe_extract(zf, dest)
    except ValueError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        _bad(str(exc))
    finally:
        zpath.unlink(missing_ok=True)
    found = modules.find_module_dirs(dest)
    if not found:
        shutil.rmtree(dest, ignore_errors=True)
        _bad("No Odoo module (__manifest__.py) in the zip")
    # the folder that directly contains the modules is the source dir
    return {"path": str(found[0].parent)}


# ---------------------------------------------------------------- jobs
class JobRequest(BaseModel):
    source_version: str
    target_version: str
    source_dir: str
    modules: list[str] = Field(min_length=1)
    options: dict = {}


def _validate_job(req: JobRequest):
    if req.source_version not in config.VERSIONS or req.target_version not in config.VERSIONS:
        _bad("Unknown version")
    if float(req.target_version) <= float(req.source_version):
        _bad("Target version must be newer than the source version")
    if not CLAUDE_STATUS.get("logged_in"):
        _bad("Claude Code is not available or not logged in (see banner)")
    if not sources.community_path(req.target_version):
        _bad(f"Odoo {req.target_version} community source is missing (Versions & Sources)")
    if not sources.venv_python(req.target_version):
        _bad(f"No Python venv for Odoo {req.target_version} (Versions & Sources)")
    src = Path(req.source_dir)
    for m in req.modules:
        if not (src / m / "__manifest__.py").is_file():
            _bad(f"Module {m} not found in {src}")
    out = config.output_dir_for(req.target_version).resolve()
    if src.resolve() == out:
        _bad("Source folder cannot be the output folder")


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    _validate_job(req)
    job_id = pipeline.create_job(req.source_version, req.target_version, req.source_dir,
                                 req.modules, req.options)
    pipeline.start_job(job_id)
    return db.get_job(job_id)


@app.get("/api/jobs")
def list_jobs():
    return db.list_jobs()


def _job_or_404(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        _bad("Job not found", 404)
    job["running"] = job_id in pipeline.RUNNING
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job_or_404(job_id)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    if not pipeline.cancel_job(job_id):
        _bad("Job is not running")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/modules/{module}/cancel")
async def cancel_module(job_id: str, module: str):
    if not pipeline.cancel_job(job_id, module):
        _bad("Job is not running")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/rerun-failed")
async def rerun_failed(job_id: str):
    job = _job_or_404(job_id)
    if job["options"].get("kind") == "create":
        _bad("A new module is rebuilt from its plan: use Approve and build in the Plan tab")
    failed = [m["module"] for m in job["modules"] if m["status"] != "passed"]
    if not failed:
        _bad("No failed modules in this job")
    req = JobRequest(source_version=job["source_version"], target_version=job["target_version"],
                     source_dir=job["source_dir"], modules=failed, options=job["options"])
    _validate_job(req)
    new_id = pipeline.create_job(req.source_version, req.target_version, req.source_dir,
                                 failed, req.options, parent_job=job_id)
    pipeline.start_job(new_id)
    return db.get_job(new_id)


def _module_or_404(job_id: str, module: str) -> tuple[dict, dict]:
    job = _job_or_404(job_id)
    mod = next((m for m in job["modules"] if m["module"] == module), None)
    if not mod:
        _bad("Module not in job", 404)
    return job, mod


@app.get("/api/jobs/{job_id}/modules/{module}/events")
def module_events(job_id: str, module: str, kinds: str = ""):
    _module_or_404(job_id, module)
    return events.replay(job_id, module, set(kinds.split(",")) if kinds else None)


@app.get("/api/jobs/{job_id}/modules/{module}/files")
def module_files(job_id: str, module: str):
    job, _ = _module_or_404(job_id, module)
    return diffs.changed_files(Path(job["source_dir"]) / module, Path(job["output_dir"]) / module)


@app.get("/api/jobs/{job_id}/modules/{module}/diff")
def module_diff(job_id: str, module: str, path: str):
    job, _ = _module_or_404(job_id, module)
    try:
        return diffs.side_by_side(Path(job["source_dir"]) / module,
                                  Path(job["output_dir"]) / module, path)
    except ValueError as exc:
        _bad(str(exc))


@app.get("/api/jobs/{job_id}/modules/{module}/report", response_class=PlainTextResponse)
def module_report(job_id: str, module: str):
    _, mod = _module_or_404(job_id, module)
    report = next((s["details"].get("path") for s in mod["steps"] if s["id"] == "report"), None) \
        or str(config.NOTES_DIR / f"{module}.md")
    if not report or not Path(report).exists():
        return ""
    return Path(report).read_text()


@app.get("/api/jobs/{job_id}/modules/{module}/logs/{name}", response_class=PlainTextResponse)
def module_log(job_id: str, module: str, name: str):
    _module_or_404(job_id, module)
    log_dir = (config.LOGS_DIR / job_id / module).resolve()
    path = (log_dir / name).resolve()
    if path.parent != log_dir or not path.is_file():
        _bad("Log not found", 404)
    return path.read_text(errors="replace")[-2_000_000:]


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = _job_or_404(job_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in job["modules"]:
            out = Path(job["output_dir"]) / m["module"]
            if out.is_dir():
                for p in out.rglob("*"):
                    if p.is_file() and "__pycache__" not in p.parts:
                        zf.write(p, f"modules/{m['module']}/{p.relative_to(out)}")
            note = config.NOTES_DIR / f"{m['module']}.md"
            if note.exists():
                zf.write(note, f"reports/{m['module']}.md")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="migration-{job_id}.zip"'})


# ---------------------------------------------------------------- manual testing
class ManualStart(BaseModel):
    fresh: bool = False
    demo: bool = False


class ManualResult(BaseModel):
    result: str
    notes: str = ""


@app.get("/api/manual")
def manual_servers():
    return manual.list_servers()


@app.post("/api/jobs/{job_id}/modules/{module}/manual")
async def manual_start(job_id: str, module: str, body: ManualStart):
    job, mod = _module_or_404(job_id, module)
    if mod["status"] in ("running", "queued"):
        _bad("Wait until the pipeline has finished this module")
    try:
        return await manual.start(job, module, fresh=body.fresh, demo=body.demo)
    except (ValueError, RuntimeError) as exc:
        _bad(str(exc))


@app.delete("/api/jobs/{job_id}/modules/{module}/manual")
async def manual_stop(job_id: str, module: str):
    if not manual.stop(job_id, module):
        _bad("No running manual test server for this module")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/modules/{module}/manual-result")
def manual_result(job_id: str, module: str, body: ManualResult):
    job, _ = _module_or_404(job_id, module)
    try:
        return manual.record_result(job, module, body.result, body.notes)
    except ValueError as exc:
        _bad(str(exc))


class ManualFix(BaseModel):
    notes: str


@app.post("/api/jobs/{job_id}/modules/{module}/manual-fix")
async def manual_fix(job_id: str, module: str, body: ManualFix):
    job, mod = _module_or_404(job_id, module)
    if not body.notes.strip():
        _bad("Describe what is wrong first: Claude works from your notes")
    if job_id in pipeline.RUNNING or mod["status"] not in pipeline.TERMINAL:
        _bad("Wait until this job has finished running")
    if not (Path(job["output_dir"]) / module / "__manifest__.py").is_file():
        _bad("No migrated output for this module")
    if not CLAUDE_STATUS.get("logged_in"):
        _bad("Claude Code is not available or not logged in (see banner)")
    manual.record_result(job, module, "failed", body.notes)
    pipeline.start_manual_fix(job, module, body.notes)
    return {"ok": True}


class RuleIn(BaseModel):
    text: str
    module: str = ""


@app.post("/api/rules/lessons")
def add_rule(body: RuleIn):
    try:
        path = reports.add_rule(body.text, body.module)
    except ValueError as exc:
        _bad(str(exc))
    return {"ok": True, "path": str(path)}


# ---------------------------------------------------------------- new modules
class NewModule(BaseModel):
    module: str
    version: str
    description: str
    depends_hint: str = ""


class PlanRevise(BaseModel):
    feedback: str


class PlanApprove(BaseModel):
    text: str


@app.get("/api/create/options")
def create_options():
    return [{"version": v, "output_dir": str(config.new_module_dir_for(v)),
             "enterprise": bool(sources.enterprise_path(v))}
            for v in config.VERSIONS if sources.community_path(v) and sources.venv_python(v)]


@app.post("/api/create")
async def create_module(body: NewModule):
    name, version = body.module.strip(), body.version
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name):
        _bad("Technical name: lowercase letters, digits and _ only, starting with a letter")
    if not body.description.strip():
        _bad("Describe what the module should do")
    _claude_ready()
    if not sources.community_path(version) or not sources.venv_python(version):
        _bad(f"Odoo {version} needs its source and venv first (Versions & Sources)")
    taken = pipeline.known_modules(version, config.output_dir_for(version)) - {
        p.parent.name for p in config.new_module_dir_for(version).glob("*/__manifest__.py")}
    if name in taken or (config.DEFAULT_CUSTOM_DIR / name).exists():
        _bad(f"`{name}` is already an Odoo, enterprise, custom or migrated module: pick another name")
    out = config.new_module_dir_for(version) / name
    if out.exists() and not db.output_owned_by_studio(str(out.resolve())):
        _bad(f"{out} already exists and wasn't created by the studio")
    job_id = pipeline.create_module_job(name, version, body.description, body.depends_hint)
    return db.get_job(job_id)


def _plan_module(job_id: str, module: str) -> tuple[dict, dict]:
    job, mod = _module_or_404(job_id, module)
    if job["options"].get("kind") != "create":
        _bad("Only new-module jobs have a plan")
    if job_id in pipeline.RUNNING:
        _bad("Wait until Claude has finished")
    return job, mod


@app.post("/api/jobs/{job_id}/modules/{module}/plan/revise")
async def plan_revise(job_id: str, module: str, body: PlanRevise):
    job, mod = _plan_module(job_id, module)
    if not body.feedback.strip():
        _bad("Write what should change in the plan")
    _claude_ready()
    pipeline.start_plan(job, module, body.feedback)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/modules/{module}/plan/approve")
async def plan_approve(job_id: str, module: str, body: PlanApprove):
    job, mod = _plan_module(job_id, module)
    if not body.text.strip():
        _bad("The plan is empty")
    if not (mod.get("plan") or {}).get("text"):
        _bad("There is no plan to approve yet")
    _claude_ready()
    pipeline.start_build(job, module, body.text)
    return {"ok": True}


# ---------------------------------------------------------------- Ask Odoo
class AskNew(BaseModel):
    version: str
    question: str
    enterprise: bool = False
    custom: bool = False


class AskFollowUp(BaseModel):
    question: str


def _thread_or_404(thread_id: str) -> dict:
    thread = ask.get_thread(thread_id)
    if not thread:
        _bad("Question not found", 404)
    return thread


def _claude_ready() -> None:
    if not CLAUDE_STATUS.get("logged_in"):
        _bad("Claude Code is not available or not logged in (see banner)")


@app.get("/api/ask/options")
def ask_options():
    return ask.options()


@app.get("/api/ask/threads")
def ask_threads():
    return ask.list_threads()


@app.post("/api/ask/threads")
async def ask_new(body: AskNew):
    _claude_ready()
    if not body.question.strip():
        _bad("Write a question first")
    try:
        return ask.create_thread(body.version, {"enterprise": body.enterprise, "custom": body.custom},
                                 body.question)
    except ValueError as exc:
        _bad(str(exc))


@app.get("/api/ask/threads/{thread_id}")
def ask_thread(thread_id: str):
    return _thread_or_404(thread_id)


@app.post("/api/ask/threads/{thread_id}/messages")
async def ask_follow_up(thread_id: str, body: AskFollowUp):
    _thread_or_404(thread_id)
    _claude_ready()
    try:
        ask.ask(thread_id, body.question)
    except ValueError as exc:
        _bad(str(exc))
    return ask.get_thread(thread_id)


@app.post("/api/ask/threads/{thread_id}/cancel")
def ask_cancel(thread_id: str):
    if not ask.cancel(thread_id):
        _bad("Claude isn't answering anything in this thread")
    return {"ok": True}


@app.delete("/api/ask/threads/{thread_id}")
def ask_delete(thread_id: str):
    _thread_or_404(thread_id)
    try:
        ask.delete_thread(thread_id)
    except ValueError as exc:
        _bad(str(exc))
    return {"ok": True}


@app.get("/api/ask/threads/{thread_id}/events")
def ask_events(thread_id: str):
    _thread_or_404(thread_id)
    return events.replay(ask.EVENT_JOB, thread_id)


@app.get("/api/ask/threads/{thread_id}/source")
def ask_source(thread_id: str, path: str, line: int = 1):
    try:
        return ask.source_snippet(_thread_or_404(thread_id), path, line)
    except ValueError as exc:
        _bad(str(exc))


# ---------------------------------------------------------------- test databases
@app.get("/api/databases")
def databases():
    return odoo_runner.list_studio_databases(db.get_settings())


@app.delete("/api/databases/{name}")
def drop_db(name: str):
    err = odoo_runner.drop_database(db.get_settings(), name)
    if err:
        _bad(err)
    return {"ok": True}


# ---------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    host = (websocket.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = events.subscribe()
    try:
        while True:
            await websocket.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(q)


# ---------------------------------------------------------------- built frontend (optional)
DIST = config.STUDIO_DIR / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(DIST / "index.html")
