"""Cancellable streaming subprocesses (each in its own process group)."""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Awaitable, Callable


class Cancelled(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self.cancelled = False
        self.procs: set[asyncio.subprocess.Process] = set()

    def cancel(self) -> None:
        self.cancelled = True
        for proc in list(self.procs):
            kill_group(proc)

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()


def kill_group(proc: asyncio.subprocess.Process, grace: float = 5.0) -> None:
    """SIGTERM the whole group, SIGKILL it if still alive after ``grace`` seconds."""
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return

    async def escalate():
        await asyncio.sleep(grace)
        if proc.returncode is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    asyncio.get_running_loop().create_task(escalate())   # callers must be on the event loop


async def run_streaming(cmd: list[str], *, cwd: Path | None, env: dict | None,
                        on_line: Callable[[str], Awaitable[None] | None],
                        token: CancelToken, timeout: float | None = None,
                        stderr_to_stdout: bool = True) -> tuple[int, str]:
    """Run ``cmd``; call ``on_line`` per stdout line. Returns (returncode, stderr text)."""
    token.check()
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd) if cwd else None, env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if stderr_to_stdout else asyncio.subprocess.PIPE,
        start_new_session=True, limit=64 * 1024 * 1024)
    token.procs.add(proc)
    stderr_chunks: list[bytes] = []

    async def pump_stdout():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            res = on_line(line.decode(errors="replace").rstrip("\n"))
            if asyncio.iscoroutine(res):
                await res

    async def pump_stderr():
        if proc.stderr:
            stderr_chunks.append(await proc.stderr.read())

    try:
        await asyncio.wait_for(asyncio.gather(pump_stdout(), pump_stderr(), proc.wait()),
                               timeout=timeout)
    except asyncio.TimeoutError:
        kill_group(proc, grace=2)
        await proc.wait()
        raise TimeoutError(f"Timed out after {timeout:.0f}s: {cmd[0]}")
    finally:
        token.procs.discard(proc)
    if token.cancelled:
        raise Cancelled()
    return proc.returncode, b"".join(stderr_chunks).decode(errors="replace")
