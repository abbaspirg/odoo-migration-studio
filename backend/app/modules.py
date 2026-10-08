"""Custom-module discovery, dependency classification and topological ordering."""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from . import sources

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def read_manifest(module_dir: Path) -> dict:
    manifest = module_dir / "__manifest__.py"
    return ast.literal_eval(manifest.read_text(encoding="utf-8"))


def find_module_dirs(root: Path, max_depth: int = 3) -> list[Path]:
    """Folders containing __manifest__.py, not descending into a module once found."""
    found: list[Path] = []

    def walk(d: Path, depth: int):
        if (d / "__manifest__.py").is_file():
            found.append(d)
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name not in SKIP_DIRS and not child.name.startswith("."):
                walk(child, depth + 1)

    if root.is_dir():
        walk(root, 0)
    return found


@lru_cache(maxsize=32)
def _addon_names(path: str) -> frozenset[str]:
    return frozenset(p.parent.name for p in Path(path).glob("*/__manifest__.py"))


def core_module_names(version: str) -> tuple[frozenset, frozenset]:
    community: set[str] = set()
    for p in sources.community_addons_paths(version):
        community |= _addon_names(str(p))
    ent = sources.enterprise_path(version)
    enterprise = _addon_names(str(ent)) if ent else frozenset()
    return frozenset(community), frozenset(enterprise)


def scan(root: Path, versions: list[str], output_dir: Path | None = None) -> list[dict]:
    dirs = find_module_dirs(root)
    custom = {d.name for d in dirs}
    known = [(v, *core_module_names(v)) for v in versions if v]
    result = []
    for d in dirs:
        try:
            manifest = read_manifest(d)
            error = None
        except Exception as exc:          # noqa: BLE001 - report bad manifests, keep scanning
            manifest, error = {}, f"Unreadable manifest: {exc}"
        deps = []
        for dep in manifest.get("depends", []):
            if dep in custom:
                kind = "custom"
            elif any(dep in c for _, c, _ in known):
                kind = "community"
            elif any(dep in e for _, _, e in known):
                kind = "enterprise"
            else:
                kind = "unknown"
            deps.append({"name": dep, "kind": kind})
        result.append({
            "name": d.name,
            "path": str(d),
            "title": manifest.get("name", d.name),
            "version": manifest.get("version", ""),
            "license": manifest.get("license", ""),
            "depends": deps,
            "error": error,
            "output_exists": bool(output_dir and (output_dir / d.name).exists()),
        })
    return result


def topo_order(selected: list[str], depends: dict[str, list[str]]) -> list[str]:
    """Dependencies first; ties keep alphabetical order. Cycles are appended as-is."""
    selected_set = set(selected)
    remaining = {m: {d for d in depends.get(m, []) if d in selected_set} for m in selected}
    order: list[str] = []
    while remaining:
        ready = sorted(m for m, deps in remaining.items() if not deps)
        if not ready:                      # cycle: break it deterministically
            ready = [sorted(remaining)[0]]
        for m in ready:
            order.append(m)
            remaining.pop(m)
        for deps in remaining.values():
            deps.difference_update(ready)
    return order
