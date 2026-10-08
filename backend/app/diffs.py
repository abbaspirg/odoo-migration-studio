"""Source-vs-migrated file comparison for the Diff tab and reports."""
from __future__ import annotations

import difflib
from pathlib import Path

IGNORED_PARTS = {"__pycache__", ".git"}
MAX_BYTES = 1_500_000


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and not IGNORED_PARTS & set(p.parts) and p.suffix != ".pyc"}


def _is_text(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return b"\0" not in fh.read(8192)
    except OSError:
        return False


def changed_files(src: Path, out: Path) -> list[dict]:
    a, b = _files(src), _files(out)
    rows = []
    for rel in sorted(set(a) | set(b)):
        if rel not in b:
            status = "removed"
        elif rel not in a:
            status = "added"
        elif a[rel].read_bytes() == b[rel].read_bytes():
            continue
        else:
            status = "modified"
        added = removed = 0
        path = b.get(rel) or a.get(rel)
        text = _is_text(path)
        if text and status == "modified":
            for line in difflib.unified_diff(_read(a[rel]), _read(b[rel]), lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
        elif text and status == "added":
            added = len(_read(b[rel]))
        elif text and status == "removed":
            removed = len(_read(a[rel]))
        rows.append({"path": rel, "status": status, "added": added, "removed": removed,
                     "binary": not text})
    return rows


def _read(p: Path | None) -> list[str]:
    if not p or not p.is_file() or p.stat().st_size > MAX_BYTES:
        return []
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def side_by_side(src: Path, out: Path, rel: str, context: int = 4) -> dict:
    """Rows of {l, r, ln, rn, k} where k is equal/insert/delete/replace/skip."""
    a_path, b_path = (src / rel).resolve(), (out / rel).resolve()
    for base, p in ((src.resolve(), a_path), (out.resolve(), b_path)):
        if base != p and base not in p.parents:
            raise ValueError("Invalid path")
    a, b = _read(a_path), _read(b_path)
    rows = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if n > context * 2 + 1:
                idx = list(range(context)) + [None] + list(range(n - context, n))
            else:
                idx = list(range(n))
            for k in idx:
                if k is None:
                    rows.append({"k": "skip", "n": n - context * 2})
                else:
                    rows.append({"k": "equal", "l": a[i1 + k], "r": b[j1 + k],
                                 "ln": i1 + k + 1, "rn": j1 + k + 1})
        else:
            for k in range(max(i2 - i1, j2 - j1)):
                li, rj = i1 + k, j1 + k
                rows.append({"k": tag,
                             "l": a[li] if li < i2 else None, "ln": li + 1 if li < i2 else None,
                             "r": b[rj] if rj < j2 else None, "rn": rj + 1 if rj < j2 else None})
    return {"path": rel, "rows": rows, "left_exists": a_path.is_file(), "right_exists": b_path.is_file()}
