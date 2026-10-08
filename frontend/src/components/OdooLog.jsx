import React, { useEffect, useMemo, useRef, useState } from "react";
import { Select } from "./ui.jsx";

const COLOR = { ERROR: "text-rose-400", CRITICAL: "text-rose-400 font-semibold", WARNING: "text-amber-300", INFO: "text-zinc-400", DEBUG: "text-zinc-600" };

export default function OdooLog({ events }) {
  const runs = useMemo(() => {
    const r = [];
    events.forEach((e) => {
      if (e.kind === "odoo_start") r.push({ key: `${e.data.step}-${e.data.attempt}`, ...e.data, lines: [] });
      else if (e.kind === "odoo_log") {
        const run = [...r].reverse().find((x) => x.step === e.data.step && x.attempt === e.data.attempt);
        if (run) {
          run.lines.push(...e.data.lines);
          if (run.lines.length > 5000) run.lines.splice(0, run.lines.length - 5000);   // long-running manual server
        }
      }
    });
    return r;
  }, [events]);
  const [sel, setSel] = useState(null);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const box = useRef();
  const run = runs.find((r) => r.key === sel) || runs[runs.length - 1];
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [run?.lines.length, errorsOnly]);
  if (!runs.length) return <div className="p-6 text-center text-sm text-zinc-500">Odoo has not run yet.</div>;
  const lines = errorsOnly ? run.lines.filter((l) => l.l === "ERROR" || l.l === "CRITICAL" || l.l === "WARNING") : run.lines;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-zinc-800 p-2 text-xs">
        <Select value={run.key} onChange={(e) => setSel(e.target.value)}>
          {runs.map((r) => <option key={r.key} value={r.key}>{r.step === "manual" ? `manual test server · ${new Date(r.attempt * 1000).toLocaleTimeString()}` : `${r.step} · attempt ${r.attempt}`}</option>)}
        </Select>
        <label className="flex items-center gap-1 text-zinc-400"><input type="checkbox" className="accent-violet-500" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> warnings & errors only</label>
        <span className="ml-auto text-zinc-500">{run.lines.filter((l) => l.l === "ERROR" || l.l === "CRITICAL").length} error lines</span>
      </div>
      <div className="border-b border-zinc-800 bg-zinc-950 px-3 py-1.5 font-mono text-[10.5px] text-zinc-500 break-all">{run.command}</div>
      <pre ref={box} className="min-h-0 flex-1 overflow-auto bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed">
        {lines.map((l, i) => <div key={i} className={`${COLOR[l.l] || "text-zinc-400"} ${l.l === "ERROR" || l.l === "CRITICAL" ? "bg-rose-500/10" : ""}`}>{l.t}</div>)}
      </pre>
    </div>
  );
}
