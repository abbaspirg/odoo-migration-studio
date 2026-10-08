"""Step 8: migration-notes/<module>.md and the MIGRATION_SUMMARY.md log."""
from __future__ import annotations

import fcntl
import time
from pathlib import Path

from . import config

SUMMARY_HEADER = "\n\n## Migration Studio runs\n\n" \
                 "_Appended automatically by Odoo Migration Studio (one line per module run)._\n\n"


def _fmt_duration(sec) -> str:
    if sec is None:
        return "—"
    sec = float(sec)
    return f"{sec:.0f}s" if sec < 90 else f"{sec / 60:.1f}min"


def _rel(p) -> str:
    try:
        return str(Path(p).resolve().relative_to(config.WORKSPACE))
    except (ValueError, TypeError):
        return str(p)


def _tests_text(verdicts: dict) -> str:
    t = verdicts.get("test")
    if not t:
        return "not run"
    if "tests_total" in t:
        return f"{t['tests_total'] - t['tests_failed'] - t['tests_errors']}/{t['tests_total']} passed"
    return "passed" if t["ok"] else "failed"


def write_module_report(*, job, module, status, error, steps, analysis, claude_reports, verdicts,
                        changes, attempts, dbname, keep_db, log_dir: Path, session_id) -> Path:
    config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.NOTES_DIR / f"{module}.md"
    lines = [
        f"# `{module}` — Odoo {job['source_version']} → {job['target_version']}",
        "",
        f"- **Result:** {status.upper()}" + (f" — {error}" if error else ""),
        f"- **Date:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"- **Job:** `{job['id']}` (Odoo Migration Studio)",
        f"- **Source:** `{_rel(Path(job['source_dir']) / module)}` (unchanged)",
        f"- **Output:** `{_rel(Path(job['output_dir']) / module)}`",
        f"- **Auto-fix attempts:** {attempts}",
        f"- **Tests:** {_tests_text(verdicts)}",
        f"- **Test database:** " + (f"`{dbname}` (kept for inspection)" if keep_db and dbname
                                     else "dropped" if dbname else "none"),
        f"- **Claude session:** `{session_id or '—'}` (resume with `claude --resume <id>` from the output folder)",
        f"- **Logs:** `{_rel(log_dir)}`",
        "",
        "## Pipeline",
        "",
        "| Step | Status | Duration | Notes |",
        "|---|---|---|---|",
    ]
    for s in steps:
        if s["id"] == "report":
            continue
        note = (s.get("summary") or "").replace("|", "\\|").replace("\n", " ")[:200]
        lines.append(f"| {s['label']} | {s['status']} | {_fmt_duration(s.get('duration'))} | {note} |")

    lines += ["", "## Changes made (Claude's report)", ""]
    if claude_reports:
        lines.append(claude_reports[-1].strip())
        if len(claude_reports) > 1:
            lines += ["", "<details><summary>Earlier reports (initial migration and previous fix "
                          "attempts)</summary>", ""]
            for i, text in enumerate(claude_reports[:-1]):
                lines += [f"### Run {i}", "", text.strip(), ""]
            lines.append("</details>")
    else:
        lines.append("_No report from Claude._")

    lines += ["", "## Files changed", ""]
    if changes:
        lines += ["| File | Change | +/- |", "|---|---|---|"]
        for c in changes:
            delta = "binary" if c["binary"] else f"+{c['added']} / -{c['removed']}"
            lines.append(f"| `{c['path']}` | {c['status']} | {delta} |")
    else:
        lines.append("_No differences from the source module._")

    lines += ["", "## Pre-scan findings (source module)", ""]
    if analysis.get("rules"):
        lines += ["| Rule | Severity | Hits | Fix |", "|---|---|---|---|"]
        for r in analysis["rules"]:
            lines.append(f"| {r['message']} | {r['severity']} | {r['count']} | {r['hint']} |")
    else:
        lines.append("_None._")

    failing = [(k, v) for k, v in verdicts.items() if not v.get("ok")]
    if failing:
        lines += ["", "## Last errors", ""]
        for k, v in failing:
            lines += [f"### {k}", "", "```", (v.get("error_excerpt") or "")[-4000:], "```", ""]

    lines += ["", "## Remaining manual checks", "",
              "- Review Claude's *Remaining TODOs / Needs review* list above.",
              "- Fill the Odoo 20 migration checklist (MIGRATION_RULES.md → Reusable Checklist).",
              "- Render website/portal/report pages: `t-esc`, `request.website` and Binary issues only show at render time."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_summary(*, job, module, status, attempts, verdicts, report_path: Path) -> None:
    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} — `{module}` {job['source_version']} → "
            f"{job['target_version']}: **{status}** (tests: {_tests_text(verdicts)}, "
            f"auto-fix attempts: {attempts}, job `{job['id']}`) — report: `{_rel(report_path)}`\n")
    with open(config.SUMMARY_FILE, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        if "## Migration Studio runs" not in fh.read():
            fh.write(SUMMARY_HEADER)
        fh.write(line)
        fcntl.flock(fh, fcntl.LOCK_UN)


def append_manual_fix(*, job, module, rnd, status, notes, claude_report, steps, error) -> Path:
    """Add a "Manual fix round N" section to migration-notes/<module>.md and a summary line."""
    path = config.NOTES_DIR / f"{module}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else f"# `{module}`\n"
    checks = [f"- {s['label']}: **{s['status']}**" + (f" — {s['summary']}" if s.get("summary") else "")
              for s in steps if s["id"] in ("static", "install", "test", "fix")]
    section = [f"## Manual fix round {rnd}", "",
               f"- **Result:** {status.upper()}" + (f" — {error}" if error else ""),
               f"- **Date:** {time.strftime('%Y-%m-%d %H:%M')}", "",
               "### What the tester reported", "", notes.strip() or "_No notes._", "",
               "### Claude's report", "", (claude_report or "_No report._").strip(), "",
               "### Checks after the fix", "", *checks, ""]
    path.write_text(text.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")
    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} — `{module}` {job['source_version']} → "
            f"{job['target_version']}: manual fix round {rnd} **{status}** (job `{job['id']}`) — "
            f"report: `{_rel(path)}`\n")
    with open(config.SUMMARY_FILE, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        if "## Migration Studio runs" not in fh.read():
            fh.write(SUMMARY_HEADER)
        fh.write(line)
        fcntl.flock(fh, fcntl.LOCK_UN)
    return path


LESSONS_HEADER = "## Lessons from manual tests"


def add_rule(text: str, module: str) -> Path:
    """Append a rule learned from a manual test to the user's migration rules file.

    The bundled rules are never edited: if they are the active file, they are copied to
    <workspace>/MIGRATION_RULES.md first (which then takes over, as documented)."""
    rule = " ".join(text.split())
    if not rule:
        raise ValueError("The rule is empty")
    target = config.RULES_FILE
    if target.resolve() == config.BUNDLED_RULES.resolve():
        target = config.WORKSPACE / "MIGRATION_RULES.md"
        if not target.exists():
            target.write_text(config.BUNDLED_RULES.read_text(encoding="utf-8"), encoding="utf-8")
        config.RULES_FILE = target
    with open(target, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        body = fh.read()
        if LESSONS_HEADER not in body:
            fh.write(("\n" if body.endswith("\n") else "\n\n") + LESSONS_HEADER + "\n\n"
                     "_Added from Odoo Migration Studio manual tests._\n\n")
        fh.write(f"- {rule} _(from `{module}`, {time.strftime('%Y-%m-%d')})_\n")
        fcntl.flock(fh, fcntl.LOCK_UN)
    return target
