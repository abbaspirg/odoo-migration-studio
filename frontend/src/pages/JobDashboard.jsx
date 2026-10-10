import React, { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtTime, useEvents, useFetch } from "../api.js";
import { Badge, Button, ErrorBox, Progress } from "../components/ui.jsx";
import StepTimeline from "../components/StepTimeline.jsx";
import ClaudeActivity from "../components/ClaudeActivity.jsx";
import OdooLog from "../components/OdooLog.jsx";
import DiffView from "../components/DiffView.jsx";
import ReportView from "../components/ReportView.jsx";
import ManualTest from "../components/ManualTest.jsx";
import PlanPanel from "../components/PlanPanel.jsx";

const STEP_IDS = ["analyze", "copy", "claude", "static", "install", "test", "fix", "report", "cleanup"];
const DONE = new Set(["passed", "failed", "skipped", "cancelled"]);

function moduleProgress(m) {
  if (["passed", "failed", "skipped", "cancelled", "interrupted"].includes(m.status)) return 100;
  if (!m.steps?.length) return 0;
  return Math.round((m.steps.filter((s) => DONE.has(s.status)).length / STEP_IDS.length) * 100);
}

export default function JobDashboard() {
  const { jobId } = useParams();
  const nav = useNavigate();
  const [job, reload, error, setJob] = useFetch(`/api/jobs/${jobId}`);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("claude");
  const [events, setEvents] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [err, setErr] = useState(null);
  const created = job?.options?.kind === "create";

  useEffect(() => {
    if (job && !selected) {
      const running = job.modules.find((m) => m.status === "running");
      setSelected((running || job.modules[0])?.module);
      if (job.options?.kind === "create" && ["planning", "plan_ready"].includes(job.modules[0]?.status)) setTab("plan");
    }
  }, [job]);

  const loadEvents = useCallback(() => {
    if (!selected) return;
    api(`/api/jobs/${jobId}/modules/${selected}/events?kinds=claude_start,claude_init,claude_text,claude_tool,claude_tool_result,claude_result,claude_session,odoo_start,odoo_log`)
      .then(setEvents).catch(() => setEvents([]));
  }, [jobId, selected]);
  useEffect(() => { setEvents([]); loadEvents(); }, [loadEvents]);

  useEvents((ev) => {
    if (ev.job_id !== jobId) return;
    if (ev.kind === "step") {
      setJob((j) => j && { ...j, modules: j.modules.map((m) => m.module !== ev.module ? m : {
        ...m, steps: (m.steps.length ? m.steps : []).some((s) => s.id === ev.data.step.id)
          ? m.steps.map((s) => (s.id === ev.data.step.id ? ev.data.step : s)) : [...m.steps, ev.data.step] }) });
      if (ev.module === selected && DONE.has(ev.data.step.status)) setRefreshKey((k) => k + 1);
    } else if (ev.kind === "module") {
      setJob((j) => j && { ...j, modules: j.modules.map((m) => (m.module === ev.module ? { ...m, status: ev.data.status, error: ev.data.error ?? m.error } : m)) });
      if (!selected) setSelected(ev.module);
    } else if (ev.kind === "manual_result") {
      setJob((j) => j && { ...j, modules: j.modules.map((m) => (m.module === ev.module ? { ...m, manual_test: ev.data } : m)) });
      if (ev.module === selected) setRefreshKey((k) => k + 1);
    } else if (ev.kind === "manual_fix") {
      setJob((j) => j && { ...j, modules: j.modules.map((m) => (m.module === ev.module ? { ...m, manual_fix: ev.data } : m)) });
      if (ev.module === selected) setRefreshKey((k) => k + 1);
    } else if (ev.kind === "job") {
      reload();
    } else if (ev.module === selected && (ev.kind.startsWith("claude_") || ev.kind.startsWith("odoo_"))) {
      setEvents((e) => [...e, ev]);
    }
  });

  if (error) return <div className="p-6"><ErrorBox>{error}</ErrorBox></div>;
  if (!job) return <div className="p-6 text-zinc-500">Loading…</div>;
  const mod = job.modules.find((m) => m.module === selected);
  const total = job.modules.reduce((a, m) => a + moduleProgress(m), 0) / (job.modules.length || 1);
  const steps = mod?.steps?.length ? mod.steps : STEP_IDS.map((id) => ({ id, label: id, status: "pending" }));
  const act = (path) => api(path, { method: "POST" }).then((r) => { reload(); return r; }).catch((e) => setErr(e.message));
  const failedCount = job.modules.filter((m) => m.status !== "passed").length;
  const active = job.running || ["running", "queued", "planning"].includes(job.status);

  const claudeEvents = events.filter((e) => e.kind.startsWith("claude_"));
  const odooEvents = events.filter((e) => e.kind.startsWith("odoo_"));

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-zinc-800 px-5 py-3">
        <Link to="/history" className="text-sm text-zinc-500 hover:text-zinc-300">Jobs /</Link>
        <span className="font-mono text-sm">{job.id}</span>
        <Badge status={job.status} />
        <span className="text-sm text-zinc-400">{created ? `New module · Odoo ${job.target_version}` : `Odoo ${job.source_version} → ${job.target_version}`}</span>
        <span className="text-xs text-zinc-500">{fmtTime(job.created_at)}</span>
        <div className="ml-auto flex gap-2">
          {job.running && <Button variant="danger" onClick={() => act(`/api/jobs/${jobId}/cancel`)}>Cancel job</Button>}
          {!created && !active && failedCount > 0 && (
            <Button onClick={() => act(`/api/jobs/${jobId}/rerun-failed`).then((j) => j && nav(`/jobs/${j.id}`))}>Re-run failed ({failedCount})</Button>
          )}
          <a href={`/api/jobs/${jobId}/download`}><Button variant="primary">Download zip</Button></a>
        </div>
      </div>
      {err && <div className="px-5 pt-2"><ErrorBox>{err}</ErrorBox></div>}

      <div className="grid min-h-0 flex-1 grid-cols-[16rem_22rem_minmax(0,1fr)]">
        {/* left: modules */}
        <aside className="overflow-auto border-r border-zinc-800 p-3">
          <div className="mb-3">
            <div className="mb-1 flex justify-between text-xs text-zinc-500"><span>Job progress</span><span>{Math.round(total)}%</span></div>
            <Progress value={total} />
          </div>
          <ul className="space-y-1">
            {job.modules.map((m) => (
              <li key={m.module}>
                <button onClick={() => setSelected(m.module)} className={`w-full rounded-md px-2.5 py-2 text-left ${selected === m.module ? "bg-zinc-800" : "hover:bg-zinc-800/50"}`}>
                  <div className="flex items-center gap-2">
                    <span className="flex-1 truncate font-mono text-sm">{m.module}</span>
                    <Badge status={m.status} />
                  </div>
                  <Progress value={moduleProgress(m)} className="mt-2" />
                  {m.error && <div className="mt-1 line-clamp-2 text-[11px] text-rose-300">{m.error}</div>}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* center: timeline */}
        <section className="overflow-auto border-r border-zinc-800 p-3">
          {mod && (
            <>
              <div className="mb-3 flex items-center gap-2 px-2">
                <h2 className="font-mono text-sm font-semibold">{mod.module}</h2>
                {mod.status === "running" && (
                  <Button variant="ghost" className="ml-auto px-2 py-0.5 text-xs" onClick={() => act(`/api/jobs/${jobId}/modules/${mod.module}/cancel`)}>Cancel</Button>
                )}
              </div>
              <StepTimeline steps={steps} />
              <div className="mt-4 space-y-1 px-2 text-[11px] text-zinc-500">
                {mod.output_path && <div>output <span className="font-mono">{mod.output_path}</span></div>}
                {mod.db_name && <div>database <span className="font-mono">{mod.db_name}</span></div>}
                {mod.session_id && <div>session <span className="font-mono">{mod.session_id}</span></div>}
                <div>logs <span className="font-mono">logs/{job.id}/{mod.module}/</span></div>
              </div>
              {!["planning", "plan_ready"].includes(mod.status) && mod.output_path && (
                <ManualTest jobId={jobId} mod={mod} onShowLog={() => setTab("odoo")} />
              )}
            </>
          )}
        </section>

        {/* right: tabs */}
        <section className="flex min-h-0 min-w-0 flex-col">
          <div className="flex gap-1 border-b border-zinc-800 px-2 pt-2">
            {[...(created ? [["plan", "Plan"]] : []), ["claude", `Claude activity (${claudeEvents.filter((e) => e.kind === "claude_tool").length})`], ["odoo", "Odoo log"], ["diff", "Diff"], ["report", "Report"]].map(([id, label]) => (
              <button key={id} onClick={() => setTab(id)}
                className={`rounded-t-md px-3 py-1.5 text-sm ${tab === id ? "bg-zinc-900 text-white ring-1 ring-zinc-800" : "text-zinc-400 hover:text-zinc-200"}`}>{label}</button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-hidden bg-zinc-900/40">
            {mod && tab === "plan" && <PlanPanel jobId={jobId} mod={mod} busy={job.running} onDone={reload} />}
            {mod && tab === "claude" && <ClaudeActivity events={claudeEvents} created={created} />}
            {mod && tab === "odoo" && <OdooLog events={odooEvents} />}
            {mod && tab === "diff" && <DiffView jobId={jobId} module={mod.module} refreshKey={refreshKey} />}
            {mod && tab === "report" && <ReportView jobId={jobId} module={mod.module} refreshKey={refreshKey} />}
          </div>
        </section>
      </div>
    </div>
  );
}
