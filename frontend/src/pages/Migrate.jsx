import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, useFetch } from "../api.js";
import { Button, Card, ErrorBox, Input, Select } from "../components/ui.jsx";
import { DepChips } from "../components/ModuleTable.jsx";

function topoOrder(selected, modules) {
  const deps = Object.fromEntries(modules.map((m) => [m.name, m.depends.map((d) => d.name)]));
  const remaining = new Map(selected.map((m) => [m, new Set((deps[m] || []).filter((d) => selected.includes(d)))]));
  const order = [];
  while (remaining.size) {
    let ready = [...remaining].filter(([, d]) => !d.size).map(([m]) => m).sort();
    if (!ready.length) ready = [[...remaining.keys()].sort()[0]];
    ready.forEach((m) => { order.push(m); remaining.delete(m); });
    remaining.forEach((d) => ready.forEach((r) => d.delete(r)));
  }
  return order;
}

export default function Migrate({ status }) {
  const nav = useNavigate();
  const [settings] = useFetch("/api/settings");
  const versions = status?.versions || ["16.0", "17.0", "18.0", "19.0", "20.0"];
  const [src, setSrc] = useState("19.0");
  const [tgt, setTgt] = useState("20.0");
  const [folder, setFolder] = useState("");
  const [scan, setScan] = useState(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [opts, setOpts] = useState({ keep_db: false, overwrite: false, max_fix_attempts: null });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (status && !folder) setFolder(status.default_custom_dir); }, [status]);
  const doScan = () => folder && api(`/api/modules/scan?path=${encodeURIComponent(folder)}&source_version=${src}&target_version=${tgt}`)
    .then((d) => { setScan(d); setErr(null); setSelected(new Set()); }).catch((e) => setErr(e.message));
  useEffect(() => { doScan(); }, [folder && status, src, tgt]);

  const modules = scan?.modules || [];
  const filtered = modules.filter((m) => (m.name + " " + m.title).toLowerCase().includes(q.toLowerCase()));
  const order = useMemo(() => topoOrder([...selected], modules), [selected, modules]);
  const toggle = (name) => setSelected((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const allFiltered = filtered.length > 0 && filtered.every((m) => selected.has(m.name));
  const existing = modules.filter((m) => selected.has(m.name) && m.output_exists);
  const versionError = parseFloat(tgt) <= parseFloat(src) ? "Target must be newer than source." : null;

  const start = async () => {
    setBusy(true); setErr(null);
    const options = { keep_db: opts.keep_db, overwrite: opts.overwrite };
    if (opts.max_fix_attempts != null && opts.max_fix_attempts !== "") options.max_fix_attempts = Number(opts.max_fix_attempts);
    try {
      const job = await api("/api/jobs", { method: "POST", body: { source_version: src, target_version: tgt, source_dir: scan.path, modules: order, options } });
      nav(`/jobs/${job.id}`);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <h1 className="text-xl font-semibold">New migration</h1>
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-xs text-zinc-400">Source version<br />
            <Select value={src} onChange={(e) => setSrc(e.target.value)}>{versions.map((v) => <option key={v}>{v}</option>)}</Select>
          </label>
          <span className="pb-2 text-zinc-500">→</span>
          <label className="text-xs text-zinc-400">Target version<br />
            <Select value={tgt} onChange={(e) => setTgt(e.target.value)}>{versions.map((v) => <option key={v}>{v}</option>)}</Select>
          </label>
          <label className="flex-1 text-xs text-zinc-400">Custom modules folder<br />
            <div className="flex gap-2">
              <Input className="flex-1 font-mono" value={folder} onChange={(e) => setFolder(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doScan()} />
              <Button onClick={doScan}>Scan</Button>
            </div>
          </label>
        </div>
        {versionError && <div className="mt-2 text-sm text-rose-400">{versionError}</div>}
        <div className="mt-2 text-xs text-zinc-500">Output: <span className="font-mono">v{tgt.split(".")[0]}-migrated/&lt;module&gt;</span> — the source folder is never modified.</div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <Card title={`Modules (${modules.length})`} actions={
          <>
            <Input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} className="w-48" />
            <Button variant="ghost" onClick={() => setSelected((s) => {
              const n = new Set(s); filtered.forEach((m) => (allFiltered ? n.delete(m.name) : n.add(m.name))); return n;
            })}>{allFiltered ? "Clear" : "Select all"}</Button>
          </>
        }>
          {filtered.map((m) => (
            <label key={m.name} className="flex cursor-pointer items-start gap-3 border-b border-zinc-800/70 py-2.5 last:border-0 hover:bg-zinc-800/30">
              <input type="checkbox" checked={selected.has(m.name)} onChange={() => toggle(m.name)} className="mt-1 accent-violet-500" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm text-zinc-100">{m.name}</span>
                  <span className="font-mono text-xs text-zinc-500">{m.version}</span>
                  {m.output_exists && <span className="rounded bg-amber-500/10 px-1.5 text-[11px] text-amber-300 ring-1 ring-amber-500/30">output exists</span>}
                </div>
                <div className="text-xs text-zinc-500">{m.title}</div>
                <div className="mt-1"><DepChips deps={m.depends} /></div>
              </div>
            </label>
          ))}
          {!filtered.length && <div className="py-6 text-center text-sm text-zinc-500">No modules found.</div>}
        </Card>

        <div className="space-y-5">
          <Card title="Run order">
            {order.length ? (
              <ol className="space-y-1 text-sm">
                {order.map((m, i) => <li key={m} className="font-mono"><span className="mr-2 text-zinc-600">{i + 1}.</span>{m}</li>)}
              </ol>
            ) : <div className="text-sm text-zinc-500">Select modules; dependencies run first.</div>}
          </Card>
          <Card title="Options">
            <div className="space-y-3 text-sm">
              <label className="flex items-center gap-2"><input type="checkbox" className="accent-violet-500" checked={opts.keep_db} onChange={(e) => setOpts({ ...opts, keep_db: e.target.checked })} /> Keep test databases</label>
              <label className="flex items-start gap-2"><input type="checkbox" className="mt-1 accent-violet-500" checked={opts.overwrite} onChange={(e) => setOpts({ ...opts, overwrite: e.target.checked })} />
                <span>Overwrite existing output<br /><span className="text-xs text-zinc-500">Existing folders are moved to migration-studio/data/backups first.</span></span></label>
              <label className="flex items-center gap-2">Max auto-fix attempts
                <Input type="number" min="0" max="10" className="w-16" placeholder={settings?.max_fix_attempts ?? 3}
                  value={opts.max_fix_attempts ?? ""} onChange={(e) => setOpts({ ...opts, max_fix_attempts: e.target.value })} /></label>
            </div>
          </Card>
          {existing.length > 0 && !opts.overwrite && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              {existing.map((m) => m.name).join(", ")} already exist in the output folder. Those modules will fail at the copy step unless the studio created them or you enable Overwrite.
            </div>
          )}
          <ErrorBox>{err}</ErrorBox>
          {status?.claude?.logged_in && status.claude.auth_method === "api_key" && (
            <div className="text-center text-xs text-amber-300/80">
              Runs on your Anthropic API key (ANTHROPIC_API_KEY): every run is billed per token.
            </div>
          )}
          {status?.claude?.logged_in && status.claude.auth_method !== "api_key" && (
            <div className="text-center text-xs text-zinc-500">
              Runs on your Claude account <span className="text-zinc-300">{status.claude.email}</span>
              {status.claude.subscription ? ` (${status.claude.subscription})` : ""} — usage counts against its plan.
            </div>
          )}
          <Button variant="primary" className="w-full py-2.5" disabled={!order.length || busy || !!versionError || !status?.claude?.logged_in} onClick={start}>
            {busy ? "Starting…" : `Start migration (${order.length})`}
          </Button>
        </div>
      </div>
    </div>
  );
}
