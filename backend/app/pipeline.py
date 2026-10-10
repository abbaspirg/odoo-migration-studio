"""Per-module migration pipeline and the job queue."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import traceback
from pathlib import Path

from . import (analyzer, claude_runner, config, db, diffs, events, manual, odoo_runner, reports,
               sources, static_checks)
from .procs import CancelToken, Cancelled

STEPS = [
    ("analyze", "Analyze"),
    ("copy", "Copy to output"),
    ("claude", "Claude migration"),
    ("static", "Static checks"),
    ("install", "Install test"),
    ("test", "Unit tests"),
    ("fix", "Auto-fix loop"),
    ("report", "Report"),
    ("cleanup", "Cleanup"),
]

TERMINAL = {"passed", "failed", "cancelled", "skipped", "interrupted"}


# ---------------------------------------------------------------- concurrency limiter
class Limiter:
    """Like a semaphore whose size is re-read from settings on every acquire."""

    def __init__(self):
        self.active = 0
        self.cond = asyncio.Condition()

    async def acquire(self, token: CancelToken):
        async with self.cond:
            while self.active >= max(1, int(db.get_settings()["max_concurrency"])):
                token.check()
                try:
                    await asyncio.wait_for(self.cond.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
            token.check()
            self.active += 1

    async def release(self):
        async with self.cond:
            self.active -= 1
            self.cond.notify_all()


LIMITER: Limiter | None = None
RUNNING: dict[str, dict] = {}        # job_id -> {"tokens": {module: CancelToken}, "task": Task}


def limiter() -> Limiter:
    global LIMITER
    if LIMITER is None:
        LIMITER = Limiter()
    return LIMITER


# ---------------------------------------------------------------- prompts
def claude_md(module, src_ver, tgt_ver, source_module: Path, out: Path) -> str:
    src_tree = sources.community_path(src_ver)
    tgt_tree = sources.community_path(tgt_ver)
    src_ent = sources.enterprise_path(src_ver)
    tgt_ent = sources.enterprise_path(tgt_ver)

    def fmt(p):
        return f"`{p.resolve()}`" if p else "_not available locally_"

    skills = tgt_tree / "skills" if tgt_tree and (tgt_tree / "skills").is_dir() else None
    ref = config.REFERENCE_MODULE if (config.REFERENCE_MODULE / "__manifest__.py").is_file() else None
    style = (f"- Already-migrated reference module to copy conventions from: `{ref}` (manifest keys,\n"
             f"  README/doc layout, license header).\n"
             f"- Its `static/description/index.html` shows the current app-page design. Restyle this\n"
             f"  module's index.html to it ONLY if the migration rules ask for that (then keep all of the\n"
             f"  module's own content, screenshots and links, and copy any icons it uses from the\n"
             f"  reference's `static/description/assets/`). Otherwise keep the existing index layout and\n"
             f"  only update versions, dates and version-specific links.\n") if ref else ""
    return f"""# Migration context for `{module}` (written by Odoo Migration Studio)

- Source version: **Odoo {src_ver}**  →  Target version: **Odoo {tgt_ver}**
- This folder (the ONLY place you may edit): `{out}`
- Original module (read-only, for comparison): `{source_module}`

## Odoo source trees (read-only — grep them to verify every API you use)
- Odoo {src_ver} community: {fmt(src_tree)}
- Odoo {tgt_ver} community: {fmt(tgt_tree)}  (core ORM in `odoo/`, apps in `addons/`, base in `odoo/addons/base`)
- Odoo {src_ver} enterprise: {fmt(src_ent)}
- Odoo {tgt_ver} enterprise: {fmt(tgt_ent)}
{f"- Odoo {tgt_ver} coding guidelines: `{skills.resolve()}`" if skills else ""}

## Conventions
{style}- Migration rules: `{config.RULES_FILE}` (already in your system prompt).

## Ground rules
- Never modify anything outside this folder.
- Do not run `odoo-bin` or create databases; the studio backend installs the module and runs
  its tests after you finish and sends you the errors.
- Useful searches: `grep -rn "<name>" {tgt_tree.resolve() if tgt_tree else '<target tree>'}/addons --include=*.py`
"""


def task_prompt(module, src_ver, tgt_ver, findings_text: str) -> str:
    return f"""Migrate the Odoo module `{module}` in the current directory from Odoo {src_ver} to Odoo {tgt_ver}.

The current directory is a copy of the module; edit files here only. Read ./CLAUDE.md first: it
gives the paths of the Odoo {src_ver} and {tgt_ver} source trees (and any reference module). Verify every
model, field, method, decorator, XML ID, inherit_id, xpath and JS import against the Odoo {tgt_ver}
source with Grep/Read before relying on it — do not guess names.

Pre-scan findings (regex scan: a starting point, not exhaustive, may contain false positives):
{findings_text}

Requirements:
- Apply every rule from the migration rules in your system prompt.
- Set the manifest version to {tgt_ver}.x.y.z.
- Preserve business logic, field names and XML IDs unless Odoo {tgt_ver} forces a change.
- Keep and port the module's tests to the new APIs; do not delete tests to make them pass.
- Do not run odoo-bin: the backend installs the module in a fresh database and runs its tests
  after you finish, then sends you any errors.

Tool limits: use the Read, Glob and Grep tools to inspect files (Grep/Read work on the source
trees outside this folder too). Bash only accepts single `python …` or `grep …` commands — `cat`,
`ls`, `find`, `cd`, `git`, pipes and `&&` chains are refused, so don't spend turns on them.

When done, reply with a short report in exactly this format:
## Changes made
- <one line per change, with file names>
## Remaining TODOs / Needs review
- <anything you could not verify or finish, or "None">
"""


CONTINUE_PROMPT = """You ran out of turns before finishing. Continue the migration where you stopped:
finish any remaining required changes (skip nice-to-haves), then reply with the report in the format
## Changes made / ## Remaining TODOs / Needs review, covering the whole migration."""


def fix_prompt(tgt_ver, attempt, max_attempts, failures: list[tuple[str, str]]) -> str:
    parts = "\n\n".join(f"### {title}\n```\n{body.strip()[-6000:]}\n```" for title, body in failures)
    return f"""The studio backend checked your migrated module and it FAILED (auto-fix attempt {attempt} of {max_attempts}).

{parts}

Find and fix the root cause in the module (current directory). Grep the Odoo {tgt_ver} source to
confirm the correct API. Do not run odoo-bin; the backend will re-run the checks when you finish.
Reply with the same report format (## Changes made / ## Remaining TODOs / Needs review), covering
the whole migration so far."""


def manual_fix_prompt(tgt_ver, notes: str, server_log: str, todos: list[str]) -> str:
    log = server_log.strip() or "(no ERROR lines in the server log: the problem only shows in the browser)"
    todo_block = "\n".join(f"[T{i}] {t}" for i, t in enumerate(todos, 1)) or "(none)"
    return f"""A person installed your migrated module on a real Odoo {tgt_ver} server and clicked through it.
The automatic checks had passed, but they found problems.

## What the tester reported
{notes.strip()}

## Errors from that Odoo server's log
```
{log}
```

Find and fix the root cause in the module (current directory). Grep the Odoo {tgt_ver} source to
confirm the correct API, templates, assets and JS imports, and compare with the original module
(path in CLAUDE.md) where behaviour changed. Fix what was reported and anything with the same cause;
leave unrelated code alone. Do not run odoo-bin: the backend re-runs static checks, install and
tests when you finish, and the tester will check again.

## Open TODOs from earlier reports
{todo_block}

Reply in exactly this format:
## Changes made
- <one line per change, with file names>
## Remaining TODOs / Needs review
- <anything you could not verify or finish, or "None">
## Resolved TODOs
- <the IDs from "Open TODOs from earlier reports" that the module now fully satisfies, e.g. "T1, T3";
  only items you fixed and checked, not ones that still need review; or "None">
## Rule suggestion
- <one general rule for future migrations that would have prevented this mistake, written for any
  module and verified against the Odoo {tgt_ver} source; or "None" if it was specific to this module>
"""


def rule_suggestion(report: str) -> str:
    m = re.search(r"##\s*Rule suggestion\s*\n(.*?)(?=\n##\s|\Z)", report or "", re.S | re.I)
    if not m:
        return ""
    text = re.sub(r"^\s*[-*]\s*", "", m.group(1).strip()).strip()
    return "" if text.lower().strip(" ._*\"'") in ("none", "n/a", "") else text


def resolved_todos(report: str, todos: list[str]) -> list[str]:
    m = re.search(r"##\s*Resolved TODOs\s*\n(.*?)(?=\n##\s|\Z)", report or "", re.S | re.I)
    ids = {int(n) for n in re.findall(r"\bT(\d+)\b", m.group(1))} if m else set()
    return [t for i, t in enumerate(todos, 1) if i in ids]


def manual_round(log_dir: Path) -> int:
    rounds = [int(m.group(1)) for f in log_dir.glob("prompt_m*.txt")
              if (m := re.fullmatch(r"prompt_m(\d+)\.txt", f.name))]
    return max(rounds, default=0) + 1


# ---------------------------------------------------------------- module pipeline
class ModulePipeline:
    def __init__(self, job: dict, module: str, token: CancelToken):
        self.job, self.module, self.token = job, module, token
        self.job_id = job["id"]
        self.src_ver, self.tgt_ver = job["source_version"], job["target_version"]
        self.source_module = Path(job["source_dir"]) / module
        self.out = Path(job["output_dir"]) / module
        self.log_dir = events.module_log_dir(self.job_id, module)
        self.settings = db.get_settings()
        self.options = job["options"]
        self.max_attempts = int(self.options.get("max_fix_attempts",
                                                 self.settings["max_fix_attempts"]))
        self.steps = [{"id": s, "label": label, "status": "pending", "started_at": None,
                       "finished_at": None, "duration": None, "summary": "", "details": {}}
                      for s, label in STEPS]
        self.session_id: str | None = None
        self.claude_reports: list[str] = []
        self.attempts = 0
        self.dbname: str | None = None
        self.analysis: dict = {}
        self.verdicts: dict = {}
        self.source_fp: str | None = None

    # -- step bookkeeping
    def step(self, sid: str) -> dict:
        return next(s for s in self.steps if s["id"] == sid)

    def _save(self, **extra):
        db.update_module(self.job_id, self.module, steps=self.steps, **extra)

    def set_step(self, sid: str, status: str, summary: str | None = None, **details):
        s = self.step(sid)
        now = time.time()
        if status == "running" and s["status"] != "running":
            s["started_at"], s["finished_at"], s["duration"] = now, None, None
        elif status in TERMINAL and s["started_at"]:
            s["finished_at"] = now
            s["duration"] = round(now - s["started_at"], 1)
        s["status"] = status
        if summary is not None:
            s["summary"] = summary
        s["details"].update(details)
        self._save()
        events.publish("step", {"step": s}, job_id=self.job_id, module=self.module)

    def set_module_status(self, status: str, **extra):
        db.update_module(self.job_id, self.module, status=status, **extra)
        events.publish("module", {"status": status, **extra}, job_id=self.job_id, module=self.module)

    # -- steps
    async def do_analyze(self):
        self.set_step("analyze", "running")
        if not (self.source_module / "__manifest__.py").is_file():
            raise StepFailed("analyze", f"No module at {self.source_module}")
        self.analysis = await asyncio.to_thread(analyzer.analyze, self.source_module,
                                                self.src_ver, self.tgt_ver,
                                                self.settings.get("required_manifest_keys"))
        (self.log_dir / "analysis.json").write_text(json.dumps(self.analysis, indent=1))
        c = self.analysis["counts"]
        self.set_step("analyze", "passed",
                      f"{c.get('error', 0)} errors, {c.get('warning', 0)} warnings, "
                      f"{c.get('info', 0)} info in {sum(self.analysis['files'].values())} files",
                      rules=self.analysis["rules"], files=self.analysis["files"])

    async def do_copy(self):
        self.set_step("copy", "running")
        out = self.out.resolve()
        if config.is_protected(out) or out == self.source_module.resolve() \
                or self.source_module.resolve() in out.parents:
            raise StepFailed("copy", f"Refusing to write into a protected folder: {out}")
        backup = None
        if out.exists():
            owned = db.output_owned_by_studio(str(out))
            if not (owned or self.options.get("overwrite")):
                raise StepFailed("copy", f"{out} already exists and was not created by the studio. "
                                         "Enable 'Overwrite existing output' to back it up and replace it.")
            backup = config.BACKUPS_DIR / self.job_id / self.module
            backup.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(out), str(backup))
        self.source_fp = await asyncio.to_thread(static_checks.fingerprint, self.source_module)
        await asyncio.to_thread(shutil.copytree, self.source_module, out,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        (out / "CLAUDE.md").write_text(claude_md(self.module, self.src_ver, self.tgt_ver,
                                                 self.source_module, out))
        db.update_module(self.job_id, self.module, output_path=str(out))
        self.set_step("copy", "passed", f"Copied to {out}" + (f" (previous output backed up to {backup})"
                                                             if backup else ""),
                      output=str(out), backup=str(backup) if backup else None)

    def _check_source_untouched(self):
        if self.source_fp and static_checks.fingerprint(self.source_module) != self.source_fp:
            raise StepFailed("claude", "SAFETY: the source module changed during the run!")

    async def run_claude(self, prompt: str, attempt: int) -> dict:
        run = claude_runner.ClaudeRun(self.job_id, self.module, attempt, self.log_dir)
        py = sources.venv_python(self.tgt_ver)
        res = await run.run(prompt, self.out, self.settings, self.token,
                            env_path_prefix=str(py.parent) if py else None, resume=self.session_id)
        if res["session_id"]:
            self.session_id = res["session_id"]
            db.update_module(self.job_id, self.module, session_id=self.session_id)
        if res["result_text"]:
            self.claude_reports.append(res["result_text"])
        await asyncio.to_thread(self._check_source_untouched)
        return res

    async def do_claude(self):
        self.set_step("claude", "running")
        prompt = task_prompt(self.module, self.src_ver, self.tgt_ver,
                             analyzer.prompt_summary(self.analysis))
        (self.log_dir / "prompt_0.txt").write_text(prompt)
        res = await self.run_claude(prompt, 0)
        details = {k: res[k] for k in ("session_id", "num_turns", "cost_usd", "files_touched",
                                       "tool_calls", "subtype")}
        if res["session_id"] and res["subtype"] == "error_max_turns":
            # one continuation so the migration (and its report) can be finished
            self.set_step("claude", "running", f"Hit max turns ({res['num_turns']}); continuing the session")
            touched = set(res["files_touched"])
            res = await self.run_claude(CONTINUE_PROMPT, "0c")
            touched |= set(res["files_touched"])
            details.update(files_touched=sorted(touched), continued=True,
                           cost_usd=(details["cost_usd"] or 0) + (res["cost_usd"] or 0))
            res["files_touched"] = sorted(touched)
        if res["ok"]:
            self.set_step("claude", "passed", f"{len(res['files_touched'])} files edited"
                                              + (" (continued once after max turns)"
                                                 if details.get("continued") else ""), **details)
        elif res["session_id"] and res["subtype"] == "error_max_turns":
            self.set_step("claude", "passed", "Stopped at max turns again; continuing with checks",
                          **details)
        else:
            raise StepFailed("claude", f"Claude run failed (exit {res['returncode']}, "
                                       f"{res['subtype'] or 'no result'})")

    async def do_static(self, attempt: int) -> dict:
        self.set_step("static", "running")
        res = await static_checks.run(self.out, self.tgt_ver, sources.venv_python(self.tgt_ver))
        self.set_step("static", "passed" if res["ok"] else "failed",
                      f"{res['python_files']} .py, {res['xml_files']} .xml checked" if res["ok"]
                      else f"{len(res['problems'])} problem(s)", attempt=attempt, problems=res["problems"])
        return res

    async def new_db(self):
        if self.dbname:
            await asyncio.to_thread(odoo_runner.drop_database, self.settings, self.dbname)
        self.dbname = f"mig_{self.module}_{time.strftime('%Y%m%d%H%M%S')}"[:63].lower()
        db.update_module(self.job_id, self.module, db_name=self.dbname)

    async def do_odoo(self, sid: str, attempt: int) -> dict:
        self.set_step(sid, "running", "", attempt=attempt)
        v = await odoo_runner.run_step(self.job_id, self.module, sid, attempt, self.log_dir,
                                       self.tgt_ver, Path(self.job["output_dir"]), self.dbname,
                                       self.settings, self.token)
        if v["ok"]:
            if sid == "test":
                summary = v.get("result_line") or "passed"
            else:
                summary = f"Installed in {self.dbname}"
        else:
            summary = "; ".join(v["problems"])[:300]
        self.set_step(sid, "passed" if v["ok"] else "failed", summary,
                      **{k: v[k] for k in v if k != "error_excerpt"})
        self.verdicts[sid] = v
        return v

    async def check_round(self, attempt: int) -> list[tuple[str, str]]:
        """static → install → test; returns failures [(title, excerpt)]."""
        failures = []
        st = await self.do_static(attempt)
        if not st["ok"]:
            self.set_step("install", "skipped", "static checks failed")
            self.set_step("test", "skipped", "static checks failed")
            return [("Static checks", "\n".join(st["problems"]))]
        await self.new_db()
        inst = await self.do_odoo("install", attempt)
        if not inst["ok"]:
            self.set_step("test", "skipped", "install failed")
            return [("Install test: odoo-bin -i " + self.module + " — " + "; ".join(inst["problems"]),
                     inst["error_excerpt"])]
        if not (self.out / "tests").is_dir():
            self.set_step("test", "skipped", "module has no tests/ folder")
            return failures
        tst = await self.do_odoo("test", attempt)
        if not tst["ok"]:
            failures.append(("Unit tests: --test-tags /" + self.module + " — " + "; ".join(tst["problems"]),
                             tst["error_excerpt"]))
        return failures

    async def do_checks_and_fix(self, label=lambda a: a, intro: str = "") -> bool:
        failures = await self.check_round(label(0))
        if not failures:
            if intro:
                self.set_step("fix", "passed", f"{intro}: checks passed")
            else:
                self.set_step("fix", "skipped", "not needed")
            return True
        if self.max_attempts <= 0:
            self.set_step("fix", "skipped", "auto-fix disabled")
            return False
        self.set_step("fix", "running", f"attempt 1/{self.max_attempts}")
        for attempt in range(1, self.max_attempts + 1):
            self.token.check()
            self.attempts = attempt
            db.update_module(self.job_id, self.module, attempts=attempt)
            self.set_step("fix", "running", f"{intro + ', ' if intro else ''}attempt {attempt}/"
                                            f"{self.max_attempts}: Claude fixing {len(failures)} failure(s)")
            prompt = fix_prompt(self.tgt_ver, attempt, self.max_attempts, failures)
            (self.log_dir / f"prompt_{label(attempt)}.txt").write_text(prompt)
            res = await self.run_claude(prompt, label(attempt))
            if not res["session_id"]:
                self.set_step("fix", "failed", f"Claude fix run {attempt} failed")
                return False
            self.set_step("fix", "running", f"{intro + ', ' if intro else ''}attempt {attempt}/"
                                            f"{self.max_attempts}: re-checking")
            failures = await self.check_round(label(attempt))
            if not failures:
                self.set_step("fix", "passed", f"{intro + ': ' if intro else ''}fixed after {attempt} "
                                               f"attempt(s)", attempts=attempt)
                return True
        self.set_step("fix", "failed", f"{intro + ': ' if intro else ''}still failing after "
                                       f"{self.max_attempts} attempt(s)", attempts=self.max_attempts)
        return False

    async def write_report(self, final_status: str, error: str | None) -> Path:
        changes = await asyncio.to_thread(diffs.changed_files, self.source_module, self.out) \
            if self.out.exists() else []
        return await asyncio.to_thread(
            reports.write_module_report, job=self.job, module=self.module,
            status=final_status, error=error, steps=self.steps, analysis=self.analysis,
            claude_reports=self.claude_reports, verdicts=self.verdicts, changes=changes,
            attempts=self.attempts, dbname=self.dbname,
            keep_db=self.keep_db(), log_dir=self.log_dir, session_id=self.session_id)

    async def do_report(self, final_status: str, error: str | None):
        self.set_step("report", "running")
        try:
            # CLAUDE.md is only for the migration session: keep it with the logs, not in the module
            if (self.out / "CLAUDE.md").exists():
                shutil.move(str(self.out / "CLAUDE.md"), str(self.log_dir / "CLAUDE.md"))
            path = await self.write_report(final_status, error)
            await asyncio.to_thread(reports.append_summary, job=self.job, module=self.module,
                                    status=final_status, attempts=self.attempts,
                                    verdicts=self.verdicts, report_path=path)
            db.update_module(self.job_id, self.module, summary=self.claude_reports[-1]
                             if self.claude_reports else error)
            self.set_step("report", "passed", str(path.relative_to(config.WORKSPACE)), path=str(path))
        except Exception as exc:          # noqa: BLE001
            self.set_step("report", "failed", f"Report failed: {exc}")

    def keep_db(self) -> bool:
        return bool(self.options.get("keep_db", self.settings["keep_db"]))

    async def do_cleanup(self):
        self.set_step("cleanup", "running")
        if self.out.is_dir() and db.output_owned_by_studio(str(self.out.resolve())):
            for cache in list(self.out.rglob("__pycache__")):   # left by the Odoo runs
                shutil.rmtree(cache, ignore_errors=True)
        if not self.dbname:
            return self.set_step("cleanup", "skipped", "no test database was created")
        if self.keep_db():
            return self.set_step("cleanup", "skipped", f"kept database {self.dbname} for inspection")
        err = await asyncio.to_thread(odoo_runner.drop_database, self.settings, self.dbname)
        self.set_step("cleanup", "failed" if err else "passed",
                      err or f"dropped {self.dbname}")

    # -- driver
    async def run(self) -> str:
        db.update_module(self.job_id, self.module, started_at=time.time(), steps=self.steps,
                         attempts=0, error=None)
        self.set_module_status("running")
        status, error = "failed", None
        try:
            await self.do_analyze()
            await self.do_copy()
            await self.do_claude()
            status = "passed" if await self.do_checks_and_fix() else "failed"
            if status == "failed":
                error = "; ".join(s["summary"] for s in self.steps
                                  if s["status"] == "failed" and s["id"] != "fix")[:500]
        except Cancelled:
            status, error = "cancelled", "Cancelled by user"
        except StepFailed as exc:
            status, error = "failed", exc.message
            self.set_step(exc.step, "failed", exc.message)
        except Exception as exc:          # noqa: BLE001
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            (self.log_dir / "crash.txt").write_text(traceback.format_exc())
            running = next((s for s in self.steps if s["status"] == "running"), None)
            if running:
                self.set_step(running["id"], "failed", error)
        # mark anything left unfinished before reporting
        for s in self.steps:
            if s["id"] in ("report", "cleanup"):
                continue
            if s["status"] == "running":
                self.set_step(s["id"], "cancelled" if status == "cancelled" else "failed")
            elif s["status"] == "pending":
                self.set_step(s["id"], "skipped")
        self.token.cancelled = False           # let report/cleanup run after a cancel
        await self.do_report(status, error)
        await self.do_cleanup()
        if self.step("report")["status"] == "passed":     # refresh so the report shows cleanup too
            await self.write_report(status, error)
        self.set_module_status(status, error=error, finished_at=time.time())
        return status


    # -- a fix round driven by what the human tester found
    async def run_manual_fix(self, notes: str, server_log: str) -> dict:
        mod = db.get_module(self.job_id, self.module) or {}
        if mod.get("steps"):
            self.steps = mod["steps"]
        self.session_id = mod.get("session_id")
        rnd = manual_round(self.log_dir)
        label, intro = f"m{rnd}", f"manual fix round {rnd}"
        info = {"round": rnd, "status": "running", "notes": notes.strip(), "report": "",
                "rule_suggestion": "", "started_at": time.time(), "finished_at": None}
        db.update_module(self.job_id, self.module, manual_fix=info, error=None)
        events.publish("manual_fix", info, job_id=self.job_id, module=self.module)
        self.set_module_status("running")
        status, error, resolved = "failed", None, []
        claude_md_log = self.log_dir / "CLAUDE.md"           # session context, moved aside by the report
        try:
            if claude_md_log.exists():
                shutil.copy(claude_md_log, self.out / "CLAUDE.md")
            self.source_fp = await asyncio.to_thread(static_checks.fingerprint, self.source_module)
            self.set_step("fix", "running", f"{intro}: Claude fixing what you reported")
            todos = await asyncio.to_thread(reports.open_todos, self.module)
            prompt = manual_fix_prompt(self.tgt_ver, notes, server_log, todos)
            (self.log_dir / f"prompt_{label}.txt").write_text(prompt)
            res = await self.run_claude(prompt, label)
            info["report"] = res["result_text"] or ""
            info["rule_suggestion"] = rule_suggestion(info["report"])
            resolved = resolved_todos(info["report"], todos)
            if not res["ok"] and not res["session_id"]:
                raise StepFailed("fix", f"Claude run failed (exit {res['returncode']}, "
                                        f"{res['subtype'] or 'no result'})")
            ok = await self.do_checks_and_fix(lambda a: label if a == 0 else f"{label}.{a}", intro)
            status = "passed" if ok else "failed"
            if not ok:
                error = "; ".join(s["summary"] for s in self.steps
                                  if s["status"] == "failed" and s["id"] != "fix")[:500]
        except Cancelled:
            status, error = "cancelled", "Cancelled by user"
            self.set_step("fix", "cancelled", f"{intro}: cancelled")
        except StepFailed as exc:
            status, error = "failed", exc.message
            self.set_step(exc.step, "failed", exc.message)
        except Exception as exc:          # noqa: BLE001
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            (self.log_dir / f"crash_{label}.txt").write_text(traceback.format_exc())
            self.set_step("fix", "failed", error)
        finally:
            (self.out / "CLAUDE.md").unlink(missing_ok=True)
        for st in self.steps:                  # nothing may stay "running" after the round
            if st["id"] in ("static", "install", "test", "fix") and st["status"] == "running":
                self.set_step(st["id"], "cancelled" if status == "cancelled" else "failed")
            elif st["id"] in ("static", "install", "test") and st["status"] == "pending":
                self.set_step(st["id"], "skipped")
        self.token.cancelled = False
        try:
            await asyncio.to_thread(reports.append_manual_fix, job=self.job, module=self.module,
                                    rnd=rnd, status=status, notes=notes, claude_report=info["report"],
                                    steps=self.steps, error=error, resolved=resolved)
        except Exception as exc:          # noqa: BLE001 - the round's result matters more than the note
            (self.log_dir / f"report_{label}_error.txt").write_text(f"{exc}\n")
        await self.do_cleanup()
        info.update(status=status, error=error, finished_at=time.time())
        db.update_module(self.job_id, self.module, manual_fix=info)
        events.publish("manual_fix", info, job_id=self.job_id, module=self.module)
        self.set_module_status(status, error=error)
        return info


class StepFailed(Exception):
    def __init__(self, step: str, message: str):
        super().__init__(message)
        self.step, self.message = step, message


# ---------------------------------------------------------------- jobs
async def _run_module_when_ready(job: dict, mod: dict, done: dict[str, asyncio.Event],
                                 results: dict[str, str]):
    name = mod["module"]
    token = RUNNING[job["id"]]["tokens"][name]
    try:
        for dep in mod["depends"]:
            if dep in done:
                await done[dep].wait()
        if token.cancelled:
            raise Cancelled()
        failed_deps = [d for d in mod["depends"] if d in results and results[d] != "passed"]
        if failed_deps:
            results[name] = "skipped"
            db.update_module(job["id"], name, status="skipped",
                             error=f"dependency not migrated: {', '.join(failed_deps)}")
            events.publish("module", {"status": "skipped"}, job_id=job["id"], module=name)
            return
        await limiter().acquire(token)
        try:
            results[name] = await ModulePipeline(job, name, token).run()
        finally:
            await limiter().release()
    except Cancelled:
        results[name] = "cancelled"
        db.update_module(job["id"], name, status="cancelled", error="Cancelled before start")
        events.publish("module", {"status": "cancelled"}, job_id=job["id"], module=name)
    finally:
        done[name].set()


async def run_job(job_id: str):
    job = db.get_job(job_id)
    db.update_job(job_id, status="running")
    events.publish("job", {"status": "running"}, job_id=job_id)
    done = {m["module"]: asyncio.Event() for m in job["modules"]}
    results: dict[str, str] = {}
    await asyncio.gather(*(_run_module_when_ready(job, m, done, results) for m in job["modules"]))
    if RUNNING.get(job_id, {}).get("cancelled"):
        status = "cancelled"
    elif all(r == "passed" for r in results.values()):
        status = "passed"
    else:
        status = "failed"
    db.update_job(job_id, status=status, finished_at=time.time())
    events.publish("job", {"status": status}, job_id=job_id)
    RUNNING.pop(job_id, None)
    return status


def start_job(job_id: str) -> asyncio.Task:
    job = db.get_job(job_id)
    RUNNING[job_id] = {"tokens": {m["module"]: CancelToken() for m in job["modules"]},
                       "cancelled": False}
    task = asyncio.create_task(run_job(job_id))
    RUNNING[job_id]["task"] = task
    return task


def cancel_job(job_id: str, module: str | None = None) -> bool:
    entry = RUNNING.get(job_id)
    if not entry:
        return False
    if module is None:
        entry["cancelled"] = True
    for name, token in entry["tokens"].items():
        if module is None or name == module:
            token.cancel()
    return True


def start_manual_fix(job: dict, module: str, notes: str) -> None:
    """Send the tester's notes and the manual server's errors to Claude, re-check, restart Odoo."""
    job_id = job["id"]
    server_log = manual.server_errors(job_id, module)
    manual.stop(job_id, module)               # it serves the old code; restarted fresh on success
    token = CancelToken()
    RUNNING[job_id] = {"tokens": {module: token}, "cancelled": False}
    db.update_job(job_id, status="running")
    events.publish("job", {"status": "running"}, job_id=job_id)

    async def go():
        info = {"status": "failed"}
        try:
            await limiter().acquire(token)
            try:
                info = await ModulePipeline(job, module, token).run_manual_fix(notes, server_log)
            finally:
                await limiter().release()
        except Cancelled:
            db.update_module(job_id, module, status="cancelled", error="Cancelled before start")
            events.publish("module", {"status": "cancelled"}, job_id=job_id, module=module)
        finally:
            RUNNING.pop(job_id, None)
            mods = db.list_modules(job_id)
            status = "passed" if all(m["status"] == "passed" for m in mods) else "failed"
            db.update_job(job_id, status=status)
            events.publish("job", {"status": status}, job_id=job_id)
        if info.get("status") == "passed":
            # wait for the old server to release before starting a fresh one
            for _ in range(40):
                s = manual.get_server(job_id, module)
                if not s or s["status"] in ("stopped", "crashed"):
                    break
                await asyncio.sleep(0.5)
            try:
                await manual.start(db.get_job(job_id), module, fresh=True)
            except (ValueError, RuntimeError) as exc:
                events.publish("manual_fix", {**info, "restart_error": str(exc)}, job_id=job_id, module=module)

    RUNNING[job_id]["task"] = asyncio.create_task(go())


def recover_interrupted() -> None:
    """Jobs left running by a server restart can't be resumed: mark them interrupted."""
    for job_id in db.interrupted_jobs():
        for m in db.list_modules(job_id):
            if m["status"] not in TERMINAL:
                db.update_module(job_id, m["module"], status="interrupted",
                                 error="Server restarted while this module was running")
        db.update_job(job_id, status="interrupted", finished_at=time.time())


def create_job(source_version: str, target_version: str, source_dir: str, modules: list[str],
               options: dict, parent_job: str | None = None) -> str:
    from .modules import read_manifest, topo_order
    src = Path(source_dir).resolve()
    deps = {}
    for m in modules:
        try:
            deps[m] = list(read_manifest(src / m).get("depends", []))
        except Exception:                 # noqa: BLE001 - the analyze step reports it
            deps[m] = []
    ordered = topo_order(modules, deps)
    selected = set(modules)
    out_dir = config.output_dir_for(target_version)
    out_dir.mkdir(parents=True, exist_ok=True)
    return db.create_job(source_version, target_version, src, out_dir,
                         [(m, [d for d in deps[m] if d in selected]) for m in ordered],
                         options, parent_job)
