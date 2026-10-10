"""Files a user attaches for Claude (screenshots, mockups, logs, specs).

Each upload gets its own folder under data/attachments/, never inside a module, so nothing
attached can end up shipped with the module or in a download zip."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from . import config

ROOT = config.DATA_DIR / "attachments"
MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 10
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT = {".txt", ".log", ".md", ".csv", ".json", ".xml", ".py", ".js", ".css", ".scss", ".html",
        ".po", ".pot", ".rst", ".yml", ".yaml", ".sql", ".diff", ".patch"}
ALLOWED = IMAGE | TEXT | {".pdf"}


def kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    return "image" if ext in IMAGE else "pdf" if ext == ".pdf" else "text"


def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    return stem[:80] + ext


def check_name(name: str) -> str:
    """The stored file name, or ValueError if the type isn't accepted."""
    safe = _safe_name(name)
    if Path(safe).suffix.lower() not in ALLOWED:
        raise ValueError(f"{name}: only images (PNG, JPG, GIF, WebP), PDFs and text files "
                         "(logs, code, CSV, XML…) can be attached")
    return safe


def new_path(name: str) -> tuple[str, Path]:
    safe = check_name(name)
    batch = uuid.uuid4().hex[:12]
    folder = ROOT / batch
    folder.mkdir(parents=True, exist_ok=True)
    return f"{batch}/{safe}", folder / safe


def describe(att_id: str) -> dict:
    p = resolve(att_id)
    return {"id": att_id, "name": p.name, "size": p.stat().st_size, "kind": kind(p.name)}


def resolve(att_id: str) -> Path:
    """The file for an attachment id; ValueError for anything outside data/attachments/."""
    p = (ROOT / str(att_id)).resolve()
    if ROOT.resolve() not in p.parents or not p.is_file():
        raise ValueError(f"Attachment not found: {att_id}")
    return p


def resolve_all(ids: list[str] | None) -> list[dict]:
    ids = list(dict.fromkeys(ids or []))
    if len(ids) > MAX_FILES:
        raise ValueError(f"Attach at most {MAX_FILES} files at a time")
    return [{**describe(i), "path": str(resolve(i))} for i in ids]


def dirs(files: list[dict]) -> list[Path]:
    """Folders Claude needs read access to for these attachments."""
    return sorted({Path(f["path"]).parent for f in files})


def prompt_block(files: list[dict]) -> str:
    if not files:
        return ""
    lines = "\n".join(f"- `{f['path']}` ({f['kind']}, {f['name']})" for f in files)
    return ("\n\n## Files the user attached\nOpen every one with the Read tool before you start; "
            "images are screenshots or mockups the user wants you to look at.\n" + lines + "\n")


def report_lines(files: list[dict]) -> list[str]:
    return [f"- `{_rel(f['path'])}` ({f['kind']})" for f in files]


def _rel(p: str) -> str:
    try:
        return Path(p).relative_to(config.STUDIO_DIR).as_posix()
    except ValueError:
        return p
