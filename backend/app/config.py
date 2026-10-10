"""Paths and user-editable settings for Odoo Migration Studio."""
from __future__ import annotations

import json
import os
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parents[2]          # repository root


def _load_env_file() -> None:
    """Read KEY=VALUE lines from <repo>/.env (does not override the real environment)."""
    env_file = STUDIO_DIR / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def _path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    if not value:
        return default.resolve()
    p = Path(value).expanduser()
    return (p if p.is_absolute() else WORKSPACE / p).resolve()


# Everything the studio reads and writes lives in the workspace (see README → Workspace).
WORKSPACE = Path(os.environ.get("MS_WORKSPACE") or STUDIO_DIR / "workspace").expanduser().resolve()

DATA_DIR = STUDIO_DIR / "data"
DB_PATH = DATA_DIR / "studio.db"
UPLOADS_DIR = DATA_DIR / "uploads"
BACKUPS_DIR = DATA_DIR / "backups"
SOURCES_DIR = WORKSPACE / "sources"
COMMUNITY_DIR = SOURCES_DIR / "community"
ENTERPRISE_DIR = SOURCES_DIR / "enterprise"
VENVS_DIR = SOURCES_DIR / "venvs"
LOGS_DIR = WORKSPACE / "logs"
NOTES_DIR = WORKSPACE / "migration-notes"
SUMMARY_FILE = WORKSPACE / "MIGRATION_SUMMARY.md"
BUNDLED_RULES = STUDIO_DIR / "rules" / "MIGRATION_RULES.md"
# your own rules (workspace/MIGRATION_RULES.md) take precedence over the bundled generic ones
RULES_FILE = _path("MS_RULES_FILE", WORKSPACE / "MIGRATION_RULES.md"
                   if (WORKSPACE / "MIGRATION_RULES.md").is_file() else BUNDLED_RULES)
# optional: an already-migrated module Claude should copy conventions from
REFERENCE_MODULE = _path("MS_REFERENCE_MODULE", WORKSPACE / "reference_module")
DEFAULT_CUSTOM_DIR = _path("MS_CUSTOM_DIR", WORKSPACE / "custom-modules")
# opt-in: let `claude` use ANTHROPIC_API_KEY (billed per token) instead of only the logged-in account
ALLOW_API_KEY = os.environ.get("MS_ALLOW_API_KEY", "").strip().lower() in ("1", "true", "yes")

VERSIONS = ["16.0", "17.0", "18.0", "19.0", "20.0"]
ODOO_GIT_URL = "https://github.com/odoo/odoo.git"

DEFAULT_ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python:*),Bash(grep:*)"

DEFAULT_SETTINGS: dict = {
    "max_concurrency": 1,
    "max_fix_attempts": 3,
    "keep_db": False,
    "claude_max_turns": 40,
    "claude_allowed_tools": DEFAULT_ALLOWED_TOOLS,
    "claude_model": "",
    "db_host": "127.0.0.1",
    "db_port": 5432,
    "db_user": "odoo_studio",
    "db_password": "odoo_studio",
    "odoo_extra_args": "",
    # manifest keys the analyzer asks for (your house style)
    "required_manifest_keys": ["summary", "license", "author", "website"],
    "install_timeout": 1800,
    "test_timeout": 3600,
    "claude_timeout": 3600,
    "ask_max_turns": 30,
    # version -> absolute path; empty means auto-detect
    "venvs": {},
}


def major(version: str) -> str:
    return version.split(".")[0]


def output_dir_for(target: str) -> Path:
    return WORKSPACE / f"v{major(target)}-migrated"


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOADS_DIR, BACKUPS_DIR, COMMUNITY_DIR, ENTERPRISE_DIR,
              VENVS_DIR, LOGS_DIR, NOTES_DIR, DEFAULT_CUSTOM_DIR):
        d.mkdir(parents=True, exist_ok=True)


def protected_roots() -> list[Path]:
    """Folders the studio must never write into."""
    roots = [COMMUNITY_DIR, ENTERPRISE_DIR, REFERENCE_MODULE, DEFAULT_CUSTOM_DIR]
    roots += [p for p in WORKSPACE.glob("odoo-*") if p.is_dir()]
    return [r.resolve() for r in roots if r.exists()]


def is_protected(path: Path) -> bool:
    p = path.resolve()
    return any(p == r or r in p.parents for r in protected_roots())


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
