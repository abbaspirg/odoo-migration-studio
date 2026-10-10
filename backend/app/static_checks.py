"""Step 4: py_compile, XML well-formedness, manifest sanity."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from lxml import etree

from .modules import read_manifest


def fingerprint(folder: Path) -> str:
    """Content hash of a folder, used to prove the source module was never modified."""
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(folder).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


async def run(module_dir: Path, target: str, python: Path | None,
              known_modules: set[str] | None = None) -> dict:
    """known_modules: when given, every manifest dependency must be one of them."""
    problems: list[str] = []
    py_files = [p for p in module_dir.rglob("*.py") if "__pycache__" not in p.parts]
    xml_files = [p for p in module_dir.rglob("*.xml")]

    # --- python: compile with the target Odoo venv's interpreter (its grammar is what matters)
    if py_files:
        interp = str(python) if python else "python3"
        # compile() in memory: checks the syntax without writing any .pyc
        code = ("import sys\n"
                "bad = 0\n"
                "for f in sys.argv[1:]:\n"
                "    try: compile(open(f, 'rb').read(), f, 'exec')\n"
                "    except SyntaxError as e: bad += 1; print(f'{e.filename}:{e.lineno}: {e.msg}')\n"
                "sys.exit(1 if bad else 0)\n")
        proc = await asyncio.create_subprocess_exec(
            interp, "-c", code, *map(str, py_files),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode:
            problems.append("Python compile errors:\n" + out.decode(errors="replace").strip())

    # --- xml well-formedness
    for p in xml_files:
        try:
            etree.parse(str(p), etree.XMLParser(resolve_entities=False, no_network=True))
        except etree.XMLSyntaxError as exc:
            problems.append(f"XML not well-formed: {p.relative_to(module_dir)}: {exc}")

    # --- manifest
    manifest_info: dict = {}
    try:
        manifest = read_manifest(module_dir)
        version = str(manifest.get("version", ""))
        manifest_info = {"version": version, "license": manifest.get("license")}
        if not re.fullmatch(re.escape(target) + r"\.\d+\.\d+\.\d+", version):
            problems.append(f"Manifest version {version!r} must be {target}.x.y.z")
        if known_modules is not None:
            missing = [d for d in manifest.get("depends", []) if d not in known_modules]
            if missing:
                problems.append(f"Manifest depends on modules that don't exist for Odoo {target}: "
                                + ", ".join(missing))
        for key in ("data", "demo"):
            for rel in manifest.get(key, []):
                if not (module_dir / rel).is_file():
                    problems.append(f"Manifest {key!r} lists missing file: {rel}")
    except Exception as exc:          # noqa: BLE001
        problems.append(f"Manifest unreadable: {exc}")

    return {"ok": not problems, "problems": problems, "python_files": len(py_files),
            "xml_files": len(xml_files), "manifest": manifest_info}
