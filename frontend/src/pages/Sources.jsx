import React, { useEffect, useRef, useState } from "react";
import { api, useEvents, useFetch } from "../api.js";
import { Badge, Button, Card, ErrorBox, Input, Progress, Select } from "../components/ui.jsx";
import ModuleTable from "../components/ModuleTable.jsx";

function TaskLog({ task }) {
  if (!task) return null;
  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <Badge status={task.status === "done" ? "passed" : task.status === "failed" ? "failed" : "running"}>{task.title}</Badge>
        {task.progress != null && <span>{task.progress}%</span>}
      </div>
      {task.progress != null && task.status === "running" && <Progress value={task.progress} />}
      <pre className="max-h-40 overflow-auto rounded bg-zinc-950 p-2 font-mono text-[11px] text-zinc-400">
        {task.log.slice(-40).join("\n")}
      </pre>
    </div>
  );
}

function EnterpriseDrop({ version, onDone }) {
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const input = useRef();
  const upload = async (file) => {
    if (!file) return;
    setErr(null); setBusy(true);
    const form = new FormData();
    form.append("file", file);
    try { await api(`/api/versions/${version}/enterprise`, { method: "POST", form }); onDone(); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}
        onClick={() => input.current.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-3 py-3 text-center text-xs transition ${drag ? "border-violet-500 bg-violet-500/10 text-violet-200" : "border-zinc-700 text-zinc-500 hover:border-zinc-500"}`}>
        {busy ? "Uploading…" : "Drop enterprise .zip here or click"}
        <input ref={input} type="file" accept=".zip" hidden onChange={(e) => upload(e.target.files[0])} />
      </div>
      <ErrorBox>{err}</ErrorBox>
    </div>
  );
}

function VersionRow({ v, tasks, reload }) {
  const [err, setErr] = useState(null);
  const act = (path, body) => api(path, { method: "POST", body }).then(reload).catch((e) => setErr(e.message));
  const c = v.community, e = v.enterprise, venv = v.venv;
  const task = Object.values(tasks).filter((t) => t.version === v.version)
    .sort((a, b) => b.started_at - a.started_at)[0];
  return (
    <Card title={`Odoo ${v.version}`} actions={c.present ? <Badge status="passed">ready</Badge> : <Badge status="pending">not installed</Badge>}>
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Community</div>
          {c.present ? (
            <div className="space-y-1 text-xs text-zinc-400">
              <div className="font-mono break-all">{c.linked_to || c.path}</div>
              {c.linked_to && <div className="text-zinc-500">linked from the existing workspace tree</div>}
              {c.commit && <div>commit <span className="font-mono">{c.commit}</span> · {c.commit_date}</div>}
              {c.is_git && <Button className="mt-1" onClick={() => act(`/api/versions/${v.version}/pull`)} disabled={!!c.task}>Update (git pull)</Button>}
            </div>
          ) : (
            <Button variant="primary" disabled={!!c.task} onClick={() => act(`/api/versions/${v.version}/clone`)}>
              Clone {v.version} (--depth 1)
            </Button>
          )}
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Enterprise</div>
          {e.present && (
            <div className="mb-2 text-xs text-zinc-400">
              {e.module_count} addons · <span className="font-mono break-all">{e.path}</span>
            </div>
          )}
          <EnterpriseDrop version={v.version} onDone={reload} />
          <div className="mt-1 text-[11px] text-zinc-600">Extracted locally to sources/enterprise/{v.version}; never committed or served.</div>
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Python venv</div>
          {venv.present ? (
            <div className="space-y-1 text-xs text-zinc-400">
              <div className="font-mono break-all">{venv.path}</div>
              <div>Python {venv.python} · {venv.deps_ok
                ? <span className="text-emerald-400">requirements OK</span>
                : <span className="text-amber-400">missing: {venv.missing.join(", ") || "?"}</span>}</div>
            </div>
          ) : <div className="text-xs text-zinc-500">none detected</div>}
          <Button className="mt-2" disabled={!c.present || !!venv.task}
            onClick={() => act(`/api/versions/${v.version}/venv`, { python_bin: "python3" })}>
            {venv.present ? "pip install -r requirements.txt" : "Create venv + install"}
          </Button>
        </div>
      </div>
      <ErrorBox>{err}</ErrorBox>
      <TaskLog task={task} />
    </Card>
  );
}

export default function Sources({ status }) {
  const [versions, reload] = useFetch("/api/versions");
  const [tasks, setTasks] = useState({});
  const [shown, setShown] = useState("20.0");
  useEvents((ev) => {
    if (ev.kind === "task") {
      setTasks((t) => ({ ...t, [ev.data.id]: ev.data }));
      if (ev.data.status !== "running") reload();
    } else if (ev.kind === "task_log") {
      setTasks((t) => {
        const cur = t[ev.data.id];
        if (!cur) return t;
        return { ...t, [ev.data.id]: { ...cur, progress: ev.data.progress, log: [...cur.log.slice(-300), ev.data.line] } };
      });
    }
  });
  const list = versions || [];
  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Versions & Sources</h1>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-zinc-500">Show</span>
          <Select value={shown} onChange={(e) => setShown(e.target.value)}>
            <option value="all">all versions</option>
            {list.map((v) => <option key={v.version} value={v.version}>{v.version}</option>)}
          </Select>
        </div>
      </div>
      {list.filter((v) => shown === "all" || v.version === shown).map((v) => (
        <VersionRow key={v.version} v={v} tasks={tasks} reload={reload} />
      ))}
      <CustomModules status={status} />
    </div>
  );
}

function CustomModules({ status }) {
  const [path, setPath] = useState("");
  useEffect(() => { if (status && !path) setPath(status.default_custom_dir); }, [status]);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const known = (status?.versions || []).slice(-2);   // classify deps against the two newest versions
  const scan = (p = path) => api(`/api/modules/scan?path=${encodeURIComponent(p)}&target_version=${known[1] || ""}&source_version=${known[0] || ""}`)
    .then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message));
  const upload = async (file) => {
    const form = new FormData(); form.append("file", file);
    try { const r = await api("/api/modules/upload", { method: "POST", form }); setPath(r.path); scan(r.path); }
    catch (e) { setErr(e.message); }
  };
  return (
    <Card title="Custom modules">
      <div className="flex flex-wrap items-center gap-2">
        <Input className="min-w-[28rem] flex-1 font-mono" value={path} onChange={(e) => setPath(e.target.value)} />
        <Button variant="primary" onClick={() => scan()}>Scan folder</Button>
        <label className="cursor-pointer rounded-md bg-zinc-800 px-3 py-1.5 text-sm ring-1 ring-zinc-700 hover:bg-zinc-700">
          Upload .zip<input type="file" accept=".zip" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
        </label>
      </div>
      <ErrorBox>{err}</ErrorBox>
      {data && <div className="mt-4"><ModuleTable modules={data.modules} /></div>}
    </Card>
  );
}
