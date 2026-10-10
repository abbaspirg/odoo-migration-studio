"""Claude Code headless (`claude -p … --output-format stream-json`) integration."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from . import config, events
from .procs import CancelToken, run_streaming

MAX_FIELD = 4000        # truncate large payloads sent to the UI


def _cut(text, n: int = MAX_FIELD) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    return text if len(text) <= n else text[:n] + f"\n… [{len(text) - n} more chars]"


def claude_env() -> dict:
    """Environment for every `claude` call. ANTHROPIC_API_KEY passes through only with MS_ALLOW_API_KEY."""
    env = dict(os.environ)
    if not config.ALLOW_API_KEY:
        env.pop("ANTHROPIC_API_KEY", None)
    return env


async def cli_status() -> dict:
    """`claude --version` + `claude auth status` for the startup banner."""
    exe = shutil.which("claude")
    info = {"installed": bool(exe), "path": exe, "version": None, "logged_in": False,
            "auth_method": None, "subscription": None, "email": None, "org_name": None,
            "error": None}
    if not exe:
        info["error"] = "Claude Code CLI (`claude`) not found on PATH. Install it and run `claude` once to log in."
        return info
    try:
        proc = await asyncio.create_subprocess_exec(exe, "--version", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, env=claude_env())
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        info["version"] = out.decode().strip()
        proc = await asyncio.create_subprocess_exec(exe, "auth", "status", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=claude_env())
        out, err = await asyncio.wait_for(proc.communicate(), 30)
        try:
            status = json.loads(out.decode() or "{}")
        except ValueError:
            status = {}
        info["logged_in"] = bool(status.get("loggedIn"))
        info["auth_method"] = status.get("authMethod")
        info["subscription"] = status.get("subscriptionType")
        info["email"] = status.get("email")
        info["org_name"] = status.get("orgName")
        if not info["logged_in"]:
            info["error"] = ("Claude Code is installed but not logged in. Run `claude auth login` in a terminal"
                             + (" or set ANTHROPIC_API_KEY." if config.ALLOW_API_KEY else "."))
    except (OSError, asyncio.TimeoutError) as exc:
        info["error"] = f"Could not run claude: {exc}"
    return info


def build_command(prompt: str, settings: dict, resume: str | None = None,
                  read_only: dict | None = None, extra_dirs: list | None = None) -> list[str]:
    """read_only: {"system_prompt", "dirs", "max_turns"} runs Claude with only Read/Grep/Glob,
    confined to the working directory and `dirs`, instead of the migration setup."""
    if read_only:
        tools = "Read,Grep,Glob"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--tools", tools, "--allowedTools", tools, "--permission-mode", "dontAsk",
               "--strict-mcp-config", "--max-turns", str(read_only["max_turns"]),
               "--append-system-prompt", read_only["system_prompt"]]
        for d in read_only["dirs"]:
            cmd += ["--add-dir", str(d)]
        if settings.get("claude_model"):
            cmd += ["--model", settings["claude_model"]]
        if resume:
            cmd += ["--resume", resume]
        return cmd
    rules = config.RULES_FILE.read_text(encoding="utf-8") if config.RULES_FILE.exists() else ""
    cmd = ["claude", "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits",
           "--allowedTools", settings.get("claude_allowed_tools") or config.DEFAULT_ALLOWED_TOOLS,
           "--max-turns", str(settings.get("claude_max_turns") or 40)]
    if rules:
        cmd += ["--append-system-prompt", rules]
    if settings.get("claude_model"):
        cmd += ["--model", settings["claude_model"]]
    for d in extra_dirs or []:                  # e.g. the user's attachments
        cmd += ["--add-dir", str(d)]
    if resume:
        cmd += ["--resume", resume]
    return cmd


class ClaudeRun:
    """One `claude -p` invocation, streaming parsed events to the UI."""

    def __init__(self, job_id: str, module: str, attempt: int, log_dir: Path):
        self.job_id, self.module, self.attempt = job_id, module, attempt
        self.raw_log = log_dir / f"claude_{attempt}.jsonl"
        self.session_id: str | None = None
        self.result: dict | None = None
        self.files_touched: set[str] = set()
        self.tool_calls = 0

    def _emit(self, kind: str, data: dict) -> None:
        events.publish(kind, {"attempt": self.attempt, **data}, job_id=self.job_id, module=self.module)

    def _handle(self, line: str, module_dir: Path) -> None:
        with open(self.raw_log, "a") as fh:
            fh.write(line + "\n")
        try:
            ev = json.loads(line)
        except ValueError:
            if line.strip():
                self._emit("claude_text", {"text": line, "role": "stderr"})
            return
        etype = ev.get("type")
        if ev.get("session_id") and not self.session_id:
            self.session_id = ev["session_id"]
            self._emit("claude_session", {"session_id": self.session_id})
        if etype == "system" and ev.get("subtype") == "init":
            self._emit("claude_init", {"model": ev.get("model"), "cwd": ev.get("cwd"),
                                       "permission_mode": ev.get("permissionMode")})
        elif etype == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    self._emit("claude_text", {"text": _cut(block["text"]), "role": "assistant"})
                elif block.get("type") == "tool_use":
                    self.tool_calls += 1
                    self._emit("claude_tool", self._describe_tool(block, module_dir))
        elif etype == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                    self._emit("claude_tool_result", {"tool_use_id": block.get("tool_use_id"),
                                                      "is_error": bool(block.get("is_error")),
                                                      "content": _cut(body or "", 1500)})
        elif etype == "result":
            self.result = ev
            self._emit("claude_result", {
                "subtype": ev.get("subtype"), "is_error": ev.get("is_error"),
                "num_turns": ev.get("num_turns"), "duration_ms": ev.get("duration_ms"),
                "cost_usd": ev.get("total_cost_usd"), "text": _cut(ev.get("result") or "", 8000)})

    def _describe_tool(self, block: dict, module_dir: Path) -> dict:
        name, inp = block.get("name"), block.get("input", {}) or {}
        path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
        rel = None
        if path:
            try:
                rel = Path(path).resolve().relative_to(module_dir.resolve()).as_posix()
            except ValueError:
                try:
                    rel = Path(path).resolve().relative_to(config.WORKSPACE.resolve()).as_posix()
                except ValueError:
                    rel = path
        data = {"id": block.get("id"), "name": name, "path": rel}
        if name in ("Edit", "MultiEdit", "Write") and rel:
            self.files_touched.add(rel)
        if name == "Edit":
            data["old"] = _cut(inp.get("old_string", ""), 3000)
            data["new"] = _cut(inp.get("new_string", ""), 3000)
        elif name == "MultiEdit":
            data["edits"] = [{"old": _cut(e.get("old_string", ""), 1500),
                              "new": _cut(e.get("new_string", ""), 1500)} for e in inp.get("edits", [])]
        elif name == "Write":
            data["content"] = _cut(inp.get("content", ""), 3000)
        elif name == "Bash":
            data["command"] = _cut(inp.get("command", ""), 1000)
        elif name in ("Grep", "Glob"):
            data["pattern"] = inp.get("pattern")
            data["path"] = inp.get("path") or rel
        else:
            data["input"] = _cut(inp, 800)
        return data

    async def run(self, prompt: str, module_dir: Path, settings: dict, token: CancelToken,
                  env_path_prefix: str | None = None, resume: str | None = None,
                  read_only: dict | None = None, extra_dirs: list | None = None) -> dict:
        cmd = build_command(prompt, settings, resume, read_only, extra_dirs)
        env = claude_env()
        if env_path_prefix:                     # `python` → the target Odoo venv
            env["PATH"] = env_path_prefix + os.pathsep + env.get("PATH", "")
        shown = [c if len(c) < 200 else c[:60] + "…" for c in cmd]
        self._emit("claude_start", {"command": shown, "resume": resume})
        rc, _ = await run_streaming(cmd, cwd=module_dir, env=env, token=token,
                                    on_line=lambda line: self._handle(line, module_dir),
                                    timeout=float(settings.get("claude_timeout") or 3600))
        ok = rc == 0 and self.result is not None and not self.result.get("is_error")
        return {"ok": ok, "returncode": rc, "session_id": self.session_id,
                "result_text": (self.result or {}).get("result", ""),
                "subtype": (self.result or {}).get("subtype"),
                "num_turns": (self.result or {}).get("num_turns"),
                "cost_usd": (self.result or {}).get("total_cost_usd"),
                "files_touched": sorted(self.files_touched), "tool_calls": self.tool_calls}
