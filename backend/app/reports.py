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
