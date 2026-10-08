import React, { useEffect, useState } from "react";
import { fmtDuration } from "../api.js";
import { StatusDot } from "./ui.jsx";

function Elapsed({ since }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  return <span>{fmtDuration(now - since)}</span>;
}

function Details({ step }) {
  const d = step.details || {};
  if (step.id === "analyze" && d.rules?.length) {
    return (
      <ul className="mt-2 space-y-1 text-xs">
        {d.rules.map((r) => (
          <li key={r.rule} className="flex gap-2">
            <span className={`w-12 shrink-0 font-mono ${r.severity === "error" ? "text-rose-400" : r.severity === "warning" ? "text-amber-400" : "text-zinc-500"}`}>{r.severity}</span>
            <span className="text-zinc-300">{r.message} <span className="text-zinc-500">×{r.count}</span><br /><span className="text-zinc-500">{r.hint}</span></span>
          </li>
        ))}
      </ul>
    );
  }
  if (step.id === "static" && d.problems?.length) {
    return <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 p-2 font-mono text-[11px] text-rose-300">{d.problems.join("\n")}</pre>;
  }
  if (step.id === "claude" && d.files_touched) {
    return (
      <div className="mt-2 text-xs text-zinc-400">
        {d.cost_usd != null && <span className="mr-3">cost ${d.cost_usd.toFixed(2)}</span>}
        {d.tool_calls != null && <span className="mr-3">{d.tool_calls} tool calls</span>}
        {d.session_id && <span className="font-mono text-zinc-500">session {d.session_id.slice(0, 8)}</span>}
        <div className="mt-1 flex flex-wrap gap-1">
          {d.files_touched.map((f) => <span key={f} className="rounded bg-zinc-800 px-1.5 font-mono text-[11px]">{f}</span>)}
        </div>
      </div>
    );
  }
  if ((step.id === "install" || step.id === "test") && d.log_file) {
    return (
      <div className="mt-1 text-xs text-zinc-500">
        {d.attempt != null && <span className="mr-3">attempt {d.attempt}</span>}
        {d.module_state && <span className="mr-3">state: {d.module_state}</span>}
        {d.tests_total != null && <span className="mr-3">{d.tests_total} tests</span>}
      </div>
    );
  }
  return null;
}

export default function StepTimeline({ steps }) {
  const [open, setOpen] = useState({});
  return (
    <ol className="relative space-y-1">
      {steps.map((s, i) => (
        <li key={s.id} className="relative pl-8">
          {i < steps.length - 1 && <span className="absolute left-[9px] top-6 h-[calc(100%-12px)] w-px bg-zinc-800" />}
          <span className="absolute left-0 top-2"><StatusDot status={s.status} /></span>
          <button className="w-full rounded-md px-2 py-1.5 text-left hover:bg-zinc-800/40" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>
            <div className="flex items-center gap-2">
              <span className={`text-sm font-medium ${s.status === "pending" || s.status === "skipped" ? "text-zinc-500" : "text-zinc-100"}`}>
                {i + 1}. {s.label}
              </span>
              <span className="ml-auto text-xs tabular-nums text-zinc-500">
                {s.status === "running" && s.started_at ? <Elapsed since={s.started_at} /> : fmtDuration(s.duration)}
              </span>
            </div>
            {s.summary && <div className={`mt-0.5 text-xs ${s.status === "failed" ? "text-rose-300" : "text-zinc-400"} ${open[s.id] ? "" : "line-clamp-2"}`}>{s.summary}</div>}
            {open[s.id] && <Details step={s} />}
          </button>
        </li>
      ))}
    </ol>
  );
}
