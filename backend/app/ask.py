"""Ask Odoo: functional questions answered by Claude from the Odoo source code, read-only."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from . import claude_runner, config, db, events, modules, sources
from .procs import CancelToken, Cancelled

SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_threads (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    scopes TEXT NOT NULL,
    session_id TEXT,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS ask_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
"""

RUNNING: dict[str, CancelToken] = {}          # thread id -> token of its running question
EVENT_JOB = "ask"                              # events go to logs/ask/<thread>/events.jsonl


def init() -> None:
    with db._lock:
        db.conn().executescript(SCHEMA)
        db.conn().execute("UPDATE ask_threads SET status = 'interrupted', "
                          "error = 'The studio restarted while Claude was answering' "
                          "WHERE status = 'running'")
        db.conn().commit()


# ---------------------------------------------------------------- what can be searched
def _custom_modules(version: str) -> list[Path]:
    """Custom modules for this Odoo version (by manifest version), from the custom-modules
    folder and the migrated output for that version."""
    found: dict[str, Path] = {}
    for root in (config.DEFAULT_CUSTOM_DIR, config.output_dir_for(version)):
        if not root.is_dir():
            continue
        for d in modules.find_module_dirs(root):
            ver = str(modules.read_manifest(d).get("version", ""))
            if ver.startswith(version + ".") or ver == version:
                found[d.name] = d             # the migrated copy wins over an older source
    return sorted(found.values(), key=lambda p: p.name)


def options() -> list[dict]:
    out = []
    for v in config.VERSIONS:
        community = sources.community_path(v)
        enterprise = sources.enterprise_path(v)
        if not community:
            continue
        out.append({"version": v, "community": str(community),
                    "enterprise": str(enterprise) if enterprise else None,
                    "custom": [p.name for p in _custom_modules(v)]})
    return out


def _scope_dirs(version: str, scopes: dict) -> list[tuple[str, Path]]:
    root = sources.community_path(version)
    # only addons/ and odoo/: a community checkout can hold other folders (e.g. enterprise-addons)
    dirs = [("Odoo community addons", root / "addons"), ("Odoo framework and base module", root / "odoo")]
    if scopes.get("enterprise") and (ent := sources.enterprise_path(version)):
        dirs.append(("Odoo enterprise", ent))
    if scopes.get("custom"):
        dirs += [(f"custom module `{p.name}`", p) for p in _custom_modules(version)]
    return [(label, p.resolve()) for label, p in dirs if p and p.is_dir()]


def allowed_roots(thread: dict) -> list[Path]:
    return [p for _, p in _scope_dirs(thread["version"], thread["scopes"])]


# ---------------------------------------------------------------- prompts
def system_prompt(version: str, scopes: dict) -> str:
    dirs = "\n".join(f"- {label}: `{p}`" for label, p in _scope_dirs(version, scopes))
    enterprise = ("Enterprise code is included." if scopes.get("enterprise") and sources.enterprise_path(version)
                  else "Enterprise code is NOT available here: if the answer may depend on an enterprise "
                       "module, say so instead of guessing what it does.")
    return f"""You are an Odoo {version} functional expert inside Odoo Migration Studio. A functional
consultant or developer asks how Odoo behaves. Answer ONLY from the source code in these folders:

{dirs}

{enterprise}

How to work:
- Find the models, fields, views, menus, actions, security rules (ir.model.access.csv, record rules,
  groups), settings (res.config.settings), data files and JS that decide the behaviour. Read them;
  don't rely on what you remember from other Odoo versions.
- Use the labels a user sees (menu names, field strings, button labels, setting names from the
  view XML), not only technical names.
- Name the module behind each behaviour, and say whether it is community or enterprise.
- Every factual claim needs a source reference written as inline code with the absolute path and
  line, e.g. `{(sources.community_path(version) / "addons").resolve()}/sale/models/sale_order.py:120`.
- If the code doesn't settle a point (it depends on data, configuration or runtime JS you couldn't
  trace), say so plainly. Never fill a gap with a guess.

Reply in this format:
## Answer
<the direct answer in functional terms: what the user does and sees, in a few short paragraphs or steps>
## How it works in the code
- <one line per mechanism, each with its source reference>
## Depends on
- <settings, installed modules, user groups or data that change the behaviour; or "Nothing found">
## Not verified
- <what you could not confirm from the code; or "None">"""


# ---------------------------------------------------------------- storage
def _decode(row: dict) -> dict:
    row["scopes"] = json.loads(row["scopes"] or "{}")
    row["running"] = row["id"] in RUNNING
    return row


def list_threads(limit: int = 200) -> list[dict]:
    return [_decode(r) for r in db._all(
        "SELECT * FROM ask_threads ORDER BY updated_at DESC LIMIT ?", (limit,))]


def get_thread(thread_id: str) -> dict | None:
    rows = db._all("SELECT * FROM ask_threads WHERE id = ?", (thread_id,))
    if not rows:
        return None
    t = _decode(rows[0])
    t["messages"] = [{**m, "meta": json.loads(m["meta"] or "{}")} for m in db._all(
        "SELECT * FROM ask_messages WHERE thread_id = ? ORDER BY id", (thread_id,))]
    return t


def _update(thread_id: str, **fields) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db._exec(f"UPDATE ask_threads SET {cols} WHERE id = ?", (*fields.values(), thread_id))


def _add_message(thread_id: str, role: str, text: str, meta: dict | None = None) -> dict:
    now = time.time()
    cur = db._exec("INSERT INTO ask_messages(thread_id, created_at, role, text, meta) VALUES (?,?,?,?,?)",
                   (thread_id, now, role, text, json.dumps(meta or {})))
    msg = {"id": cur.lastrowid, "thread_id": thread_id, "created_at": now, "role": role,
           "text": text, "meta": meta or {}}
    events.publish("ask_message", msg, job_id=EVENT_JOB, module=thread_id, persist=False)
    return msg


def delete_thread(thread_id: str) -> None:
    if thread_id in RUNNING:
        raise ValueError("Stop the running question first")
    db._exec("DELETE FROM ask_messages WHERE thread_id = ?", (thread_id,))
    db._exec("DELETE FROM ask_threads WHERE id = ?", (thread_id,))


# ---------------------------------------------------------------- asking
def create_thread(version: str, scopes: dict, question: str) -> dict:
    if not sources.community_path(version):
        raise ValueError(f"Odoo {version} sources are not cloned (see Versions & Sources)")
    if scopes.get("enterprise") and not sources.enterprise_path(version):
        raise ValueError(f"No enterprise code for Odoo {version} (upload it in Versions & Sources)")
    thread_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    title = " ".join(question.split())[:120]
    now = time.time()
    db._exec("INSERT INTO ask_threads(id, created_at, updated_at, title, version, scopes, status) "
             "VALUES (?,?,?,?,?,?,?)",
             (thread_id, now, now, title, version, json.dumps(
                 {"enterprise": bool(scopes.get("enterprise")), "custom": bool(scopes.get("custom"))}),
              "idle"))
    ask(thread_id, question)
    return get_thread(thread_id)


def ask(thread_id: str, question: str) -> None:
    thread = get_thread(thread_id)
    if not thread:
        raise ValueError("Unknown question thread")
    if thread_id in RUNNING:
        raise ValueError("Claude is still answering the previous question")
    question = question.strip()
    if not question:
        raise ValueError("Write a question first")
    n = sum(1 for m in thread["messages"] if m["role"] == "user") + 1
    _add_message(thread_id, "user", question)
    token = CancelToken()
    RUNNING[thread_id] = token
    _update(thread_id, status="running", error=None)
    events.publish("ask_status", {"status": "running"}, job_id=EVENT_JOB, module=thread_id, persist=False)
    asyncio.create_task(_run(thread, question, n, token))


async def _run(thread: dict, question: str, n: int, token: CancelToken) -> None:
    thread_id = thread["id"]
    settings = db.get_settings()
    log_dir = events.module_log_dir(EVENT_JOB, thread_id)
    cwd = config.DATA_DIR / "ask" / thread_id            # empty, so no project CLAUDE.md is picked up
    cwd.mkdir(parents=True, exist_ok=True)
    read_only = {"system_prompt": system_prompt(thread["version"], thread["scopes"]),
                 "dirs": allowed_roots(thread), "max_turns": int(settings.get("ask_max_turns") or 30)}
    status, error = "failed", None
    try:
        run = claude_runner.ClaudeRun(EVENT_JOB, thread_id, f"q{n}", log_dir)
        res = await run.run(question, cwd, settings, token, resume=thread.get("session_id"),
                            read_only=read_only)
        if res["session_id"]:
            _update(thread_id, session_id=res["session_id"])
        text = (res["result_text"] or "").strip()
        meta = {"turns": res["num_turns"], "cost_usd": res["cost_usd"], "subtype": res["subtype"]}
        if res["subtype"] == "error_max_turns":
            text = (text + "\n\n" if text else "") + (
                f"_Claude used all {read_only['max_turns']} turns before finishing. Ask a narrower "
                "question, or ask it to continue._")
        if text:
            _add_message(thread_id, "assistant", text, meta)
        if res["ok"] or text:
            status = "idle"
        else:
            error = f"Claude run failed (exit {res['returncode']}, {res['subtype'] or 'no result'})"
    except Cancelled:
        status, error = "idle", "Stopped"
    except Exception as exc:          # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        RUNNING.pop(thread_id, None)
    _update(thread_id, status=status, error=error)
    events.publish("ask_status", {"status": status, "error": error}, job_id=EVENT_JOB,
                   module=thread_id, persist=False)


def cancel(thread_id: str) -> bool:
    token = RUNNING.get(thread_id)
    if not token:
        return False
    token.cancel()
    return True


# ---------------------------------------------------------------- cited source
def source_snippet(thread: dict, path: str, line: int, context: int = 25) -> dict:
    """Lines around a cited path:line, only from the folders this thread may search."""
    p = Path(path).resolve()
    if not any(p == r or r in p.parents for r in allowed_roots(thread)):
        raise ValueError("That file is outside the folders this question searched")
    if not p.is_file():
        raise ValueError("File not found")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - context)
    end = min(len(lines), max(line, 1) + context)
    rel = p.relative_to(config.WORKSPACE).as_posix() if config.WORKSPACE in p.parents else str(p)
    return {"path": str(p), "display": rel, "line": line, "start": start,
            "lines": lines[start - 1:end], "total": len(lines)}
