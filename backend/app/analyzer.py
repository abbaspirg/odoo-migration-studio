"""Static deprecation scan of a module (step 1: Analyze).

Each rule applies when the *target* version is >= ``since``: old constructs can survive
several releases in custom code (MIGRATION_RULES.md §5), so the source version is not used
to filter. Rules marked [rules] are also described in rules/MIGRATION_RULES.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .modules import read_manifest


@dataclass(frozen=True)
class Rule:
    id: str
    since: int                 # target major version from which the pattern is broken/deprecated
    kinds: tuple               # file kinds: py, xml, js, scss, csv
    pattern: str
    severity: str              # error | warning | info
    message: str
    hint: str
    server_only: bool = False  # skip static/src (client-side OWL templates)


RULES: list[Rule] = [
    # ---- Python
    Rule("sql-constraints", 19, ("py",), r"_sql_constraints\s*=", "error",
         "_sql_constraints removed", "Use models.Constraint('sql', 'message') class attributes [rules]"),
    Rule("env-private-props", 19, ("py",), r"\bself\._(context|uid|cr)\b", "warning",
         "self._context/_uid/_cr deprecated", "Use self.env.context / self.env.uid / self.env.cr [rules]"),
    Rule("read-group-fields", 20, ("py",), r"\.read_group\(", "warning",
         "read_group(domain, fields, groupby) removed", "Use _read_group(domain, groupby, aggregates) [rules]"),
    Rule("request-website", 20, ("py", "xml"), r"\brequest\.website\b", "error",
         "request.website no longer exists", "Use request.env.website [rules]"),
    Rule("quantity-done", 17, ("py", "xml"), r"\bquantity_done\b", "error",
         "stock.move.quantity_done removed", "Renamed to quantity [rules]"),
    Rule("return-picking-wizard", 20, ("py", "xml"), r"stock\.return\.picking", "error",
         "stock.return.picking wizard removed", "Use picking._create_return() and adjust moves [rules]"),
    Rule("portal-home-values", 20, ("py",), r"_prepare_home_portal_values", "error",
         "_prepare_home_portal_values removed", "Use _prepare_portal_counter_values + portal.entry record [rules]"),
    Rule("payment-post-processing", 20, ("py",), r"PaymentPostProcessing|poll_status", "error",
         "Payment post-processing controller removed", "Override payment.transaction._post_process() [rules]"),
    Rule("video-embed", 20, ("py",), r"get_video_embed_code", "error",
         "html_editor.tools.get_video_embed_code removed", "Reimplement locally [rules]"),
    Rule("attachment-datas", 20, ("py",), r"['\"]datas['\"]", "error",
         "ir.attachment 'datas' silently ignored", "Use raw= with bytes [rules]"),
    Rule("http-helpers", 20, ("py",), r"from\s+odoo\.http\s+import[^\n]*(content_disposition|serialize_exception)",
         "error", "content_disposition/serialize_exception not exported from odoo.http",
         "Import from odoo.http.stream / odoo.http.dispatcher [rules]"),
    Rule("route-json", 19, ("py",), r"type\s*=\s*['\"]json['\"]", "warning",
         "type='json' routes renamed", "Use type='jsonrpc'"),
    Rule("name-get", 17, ("py",), r"def\s+name_get\s*\(", "warning",
         "name_get deprecated", "Use _compute_display_name"),
    Rule("api-multi-one", 13, ("py",), r"@api\.(multi|one|returns)\b", "error",
         "@api.multi/@api.one removed", "Remove the decorator"),
    Rule("payment-posted", 20, ("py", "xml"), r"state\s*[!=]=\s*['\"]posted['\"]", "info",
         "Possible account.payment 'posted' state", "account.payment states are draft/paid/reconciled/canceled/rejected [rules]"),
    Rule("binary-field", 20, ("py",), r"fields\.Binary\(", "info",
         "Binary field: BinaryValue on read, base64 str on write", "Render with .to_base64(); write b64 str [rules]"),
    Rule("groups-id", 19, ("py", "xml"), r"\bgroups_id\b", "warning",
         "groups_id renamed on res.users / views", "Check for group_ids in the target source"),
    # ---- XML
    Rule("tree-view", 18, ("xml",), r"<tree\b|view_mode[^<]*\btree\b", "error",
         "<tree> views removed", "Use <list> and view_mode 'list' [rules]"),
    Rule("attrs-states", 17, ("xml",), r"\s(attrs|states)\s*=\s*\"", "error",
         "attrs=/states= removed", "Use invisible/readonly/required expressions [rules]", True),
    Rule("t-esc", 20, ("xml",), r"\bt-(esc|raw)\s*=", "error",
         "t-esc/t-raw render empty in server QWeb", "Replace with t-out [rules]", True),
    Rule("t-key-server", 20, ("xml",), r"\bt-key\s*=", "info",
         "t-key is OWL-only; warns on server templates", "Remove from server-rendered t-foreach [rules]", True),
    Rule("ir-rule", 20, ("xml",), r"model\s*=\s*['\"]ir\.(rule|model\.access)['\"]", "error",
         "ir.rule / ir.model.access removed", "Convert to ir.access records [rules]"),
    Rule("report-file", 20, ("xml",), r"name\s*=\s*['\"]report_file['\"]", "error",
         "ir.actions.report.report_file removed", "Keep report_name only [rules]"),
    Rule("portal-my-home", 20, ("xml",), r"portal\.portal_my_home|portal_docs_entry", "warning",
         "portal home card contract changed", "Create a portal.entry data record [rules]"),
    Rule("base64-binary-qweb", 20, ("xml",), r"base64,(#\{|\{\{)", "error",
         "Binary formatted in QWeb raises UnicodeDecodeError", "Use #{rec.field.to_base64()} [rules]"),
    Rule("cron-numbercall", 18, ("xml", "py"), r"\b(numbercall|doall)\b", "error",
         "ir.cron numbercall/doall removed", "Remove the fields [rules]"),
    Rule("kanban-box", 18, ("xml",), r"kanban-box", "warning",
         "kanban-box template renamed", "Use t-name=\"card\""),
    Rule("bs4-classes", 16, ("xml", "js"), r"class=\"[^\"]*\b(ml|mr|pl|pr)-\d|float-(left|right)\b", "info",
         "Bootstrap 4 utility classes", "Use Bootstrap 5 ms-/me-/ps-/pe-/float-start/end"),
    # ---- JS / OWL
    Rule("public-widget", 20, ("js",), r"publicWidget|@web/legacy/js/public/public_widget", "error",
         "publicWidget framework removed", "Port to the Interaction framework (@web/public/interaction) [rules]"),
    Rule("odoo-define", 16, ("js",), r"odoo\.define\s*\(", "error",
         "Legacy odoo.define module", "Use ES modules with /** @odoo-module */ imports"),
    Rule("owl-global", 18, ("js",), r"=\s*owl\s*;", "warning",
         "Global owl object", "import { … } from \"@odoo/owl\" [rules]"),
    Rule("owl3-refname", 20, ("js",), r"\brefName\b|\buseRef\(", "warning",
         "OWL 3: refs changed", "Use signal.ref() / useInputField({ref}) [rules]"),
    # ---- SCSS
    Rule("scss-import", 17, ("scss",), r"^\s*@import\b", "info",
         "SCSS @import", "Assets bundles are declared in the manifest; check the import still resolves"),
]

KIND_BY_SUFFIX = {".py": "py", ".xml": "xml", ".js": "js", ".scss": "scss", ".css": "scss",
                  ".csv": "csv"}


def analyze(module_dir: Path, source_version: str, target_version: str,
            required_keys: list[str] | None = None) -> dict:
    target_major = int(target_version.split(".")[0])
    active = [r for r in RULES if target_major >= r.since]
    compiled = [(r, re.compile(r.pattern, re.MULTILINE)) for r in active]
    findings: list[dict] = []
    files_by_kind: dict[str, int] = {}

    for path in sorted(module_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(module_dir).as_posix()
        if rel.startswith("static/description/"):
            continue
        kind = KIND_BY_SUFFIX.get(path.suffix)
        if not kind:
            continue
        files_by_kind[kind] = files_by_kind.get(kind, 0) + 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        client_side = rel.startswith("static/src/")
        lines = text.splitlines()
        for rule, rx in compiled:
            if kind not in rule.kinds or (rule.server_only and client_side):
                continue
            for m in rx.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                findings.append({"rule": rule.id, "severity": rule.severity,
                                 "message": rule.message, "hint": rule.hint, "file": rel,
                                 "line": line_no, "text": lines[line_no - 1].strip()[:200]})

    # ---- file-level / manifest checks
    if target_major >= 20 and (module_dir / "security" / "ir.model.access.csv").exists():
        rows = max(0, len((module_dir / "security" / "ir.model.access.csv")
                          .read_text().strip().splitlines()) - 1)
        findings.append({"rule": "ir-model-access-csv", "severity": "error",
                         "message": f"security/ir.model.access.csv ({rows} rows): model removed",
                         "hint": "Rename to security/ir.access.csv with id,name,model_id,group_id/id,"
                                 "operation,domain [rules]",
                         "file": "security/ir.model.access.csv", "line": 1, "text": ""})
    manifest: dict = {}
    try:
        manifest = read_manifest(module_dir)
    except Exception as exc:          # noqa: BLE001
        findings.append({"rule": "manifest", "severity": "error", "message": f"Unreadable manifest: {exc}",
                         "hint": "", "file": "__manifest__.py", "line": 1, "text": ""})
    if manifest:
        version = str(manifest.get("version", ""))
        if not version.startswith(target_version + "."):
            findings.append({"rule": "manifest-version", "severity": "error",
                             "message": f"Manifest version {version!r} is not {target_version}.x.y.z",
                             "hint": "Otherwise the module is silently uninstallable [rules]",
                             "file": "__manifest__.py", "line": 1, "text": ""})
        if not manifest.get("license"):
            findings.append({"rule": "manifest-license", "severity": "warning",
                             "message": "No license key (Odoo assumes LGPL-3)",
                             "hint": "Set the intended license explicitly",
                             "file": "__manifest__.py", "line": 1, "text": ""})
        missing = [k for k in (required_keys or []) if k not in manifest]
        if missing:
            findings.append({"rule": "manifest-keys", "severity": "warning",
                             "message": "Missing manifest keys: " + ", ".join(missing),
                             "hint": "Required by your settings (required_manifest_keys)",
                             "file": "__manifest__.py", "line": 1, "text": ""})

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    by_rule: dict[str, dict] = {}
    for f in findings:
        entry = by_rule.setdefault(f["rule"], {"rule": f["rule"], "severity": f["severity"],
                                                "message": f["message"], "hint": f["hint"], "count": 0})
        entry["count"] += 1
    return {"source_version": source_version, "target_version": target_version,
            "files": files_by_kind, "counts": counts, "rules": list(by_rule.values()),
            "findings": findings}


def prompt_summary(result: dict, limit: int = 60) -> str:
    """Compact text listing of findings for the Claude task prompt."""
    lines = []
    for f in result["findings"][:limit]:
        lines.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['message']} → {f['hint']}")
    extra = len(result["findings"]) - limit
    if extra > 0:
        lines.append(f"- … and {extra} more findings of the same kinds")
    return "\n".join(lines) or "- (no known deprecated patterns found by the pre-scan)"
