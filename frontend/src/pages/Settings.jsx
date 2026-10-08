import React, { useEffect, useState } from "react";
import { api, useFetch } from "../api.js";
import { Button, Card, ErrorBox, Input } from "../components/ui.jsx";

const FIELDS = [
  ["Pipeline", [
    ["max_concurrency", "Modules migrated in parallel", "number"],
    ["max_fix_attempts", "Max auto-fix attempts", "number"],
    ["keep_db", "Keep test databases by default", "checkbox"],
  ]],
  ["Claude Code", [
    ["claude_max_turns", "--max-turns", "number"],
    ["claude_allowed_tools", "--allowedTools", "text"],
    ["claude_model", "--model (blank = account default)", "text"],
    ["claude_timeout", "Timeout per run (s)", "number"],
  ]],
  ["PostgreSQL (test databases)", [
    ["db_host", "Host", "text"], ["db_port", "Port", "number"],
    ["db_user", "User (needs CREATEDB, not postgres)", "text"], ["db_password", "Password", "password"],
  ]],
  ["Analyzer", [
    ["required_manifest_keys", "Required manifest keys (comma-separated)", "list"],
  ]],
  ["Odoo runs", [
    ["odoo_extra_args", "Extra odoo-bin arguments", "text"],
    ["install_timeout", "Install timeout (s)", "number"], ["test_timeout", "Test timeout (s)", "number"],
  ]],
];

function WorkspaceInfo({ onSaved }) {
  const [st, , , setSt] = useFetch("/api/status");
  const [checking, setChecking] = useState(false);
  const recheck = async () => {
    setChecking(true);
    try { setSt(await api("/api/status?refresh=true")); onSaved?.(); } finally { setChecking(false); }
  };
  if (!st) return null;
  const rows = [
    ["Workspace", st.workspace, "MS_WORKSPACE"],
    ["Custom modules", st.default_custom_dir, "MS_CUSTOM_DIR"],
    ["Migration rules", `${st.rules_path}${st.rules_bundled ? "  (bundled generic rules)" : ""}`, "MS_RULES_FILE"],
    ["Reference module", st.reference_module || "none", "MS_REFERENCE_MODULE"],
  ];
  return (
    <Card title="Workspace (set in .env, restart to apply)">
      <div className="grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1.5 text-sm">
        {rows.map(([label, value, env]) => (
          <React.Fragment key={env}>
            <span className="text-zinc-400">{label}</span>
            <span><span className="break-all font-mono text-zinc-200">{value}</span> <span className="text-xs text-zinc-600">{env}</span></span>
          </React.Fragment>
        ))}
        <span className="text-zinc-400">Claude account</span>
        <span className="text-zinc-200">{st.claude?.logged_in
          ? <>{st.claude.email} <span className="text-zinc-500">· {st.claude.subscription || st.claude.auth_method}{st.claude.org_name ? ` · ${st.claude.org_name}` : ""} · switch with <code className="font-mono">claude auth login</code></span></>
          : "not logged in — run claude auth login"}
          <Button variant="ghost" className="ml-2 px-2 py-0.5 text-xs" disabled={checking} onClick={recheck}>
            {checking ? "Checking…" : "Re-check"}</Button></span>
      </div>
    </Card>
  );
}

export default function Settings({ onSaved }) {
  const [data] = useFetch("/api/settings");
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { if (data) setForm(data); }, [data]);
  if (!form) return null;
  const save = async () => {
    try {
      const clean = { ...form };
      FIELDS.flatMap(([, f]) => f).forEach(([k, , t]) => {
        if (t === "number") clean[k] = Number(clean[k]);
        if (t === "list" && typeof clean[k] === "string") clean[k] = clean[k].split(",").map((x) => x.trim()).filter(Boolean);
      });
      setForm(await api("/api/settings", { method: "PUT", body: clean }));
      setMsg("Saved."); setErr(null); onSaved?.();
      setTimeout(() => setMsg(null), 2000);
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <h1 className="text-xl font-semibold">Settings</h1>
      <WorkspaceInfo onSaved={onSaved} />
      {FIELDS.map(([title, fields]) => (
        <Card key={title} title={title}>
          <div className="grid grid-cols-[16rem_1fr] items-center gap-3 text-sm">
            {fields.map(([k, label, type]) => (
              <React.Fragment key={k}>
                <label className="text-zinc-400" htmlFor={k}>{label}</label>
                {type === "checkbox"
                  ? <input id={k} type="checkbox" className="h-4 w-4 accent-violet-500" checked={!!form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.checked })} />
                  : <Input id={k} type={type === "list" ? "text" : type} className="font-mono"
                      value={Array.isArray(form[k]) ? form[k].join(", ") : form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />}
              </React.Fragment>
            ))}
          </div>
        </Card>
      ))}
      <ErrorBox>{err}</ErrorBox>
      <div className="flex items-center gap-3"><Button variant="primary" onClick={save}>Save settings</Button>{msg && <span className="text-sm text-emerald-400">{msg}</span>}</div>
    </div>
  );
}
