import React, { useEffect, useState } from "react";
import { api } from "../api.js";

const ST = { added: "text-emerald-400", removed: "text-rose-400", modified: "text-amber-300" };
const BG = { insert: ["", "bg-emerald-500/15"], delete: ["bg-rose-500/15", ""], replace: ["bg-rose-500/15", "bg-emerald-500/15"], equal: ["", ""] };

export default function DiffView({ jobId, module, refreshKey }) {
  const [files, setFiles] = useState(null);
  const [sel, setSel] = useState(null);
  const [diff, setDiff] = useState(null);
  useEffect(() => {
    api(`/api/jobs/${jobId}/modules/${module}/files`).then((f) => { setFiles(f); if (!sel && f.length) setSel(f.find((x) => !x.binary)?.path); }).catch(() => setFiles([]));
  }, [jobId, module, refreshKey]);
  useEffect(() => {
    if (sel) api(`/api/jobs/${jobId}/modules/${module}/diff?path=${encodeURIComponent(sel)}`).then(setDiff).catch(() => setDiff(null));
  }, [sel, jobId, module, refreshKey]);
  if (!files) return <div className="p-6 text-sm text-zinc-500">Loading…</div>;
  if (!files.length) return <div className="p-6 text-center text-sm text-zinc-500">No differences between source and migrated module yet.</div>;
  return (
    <div className="flex h-full min-h-0">
      <ul className="w-60 shrink-0 overflow-auto border-r border-zinc-800 py-1 text-xs">
        {files.map((f) => (
          <li key={f.path}>
            <button onClick={() => setSel(f.path)} className={`flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-zinc-800/50 ${sel === f.path ? "bg-zinc-800" : ""}`}>
              <span className={`w-3 font-bold ${ST[f.status]}`}>{f.status[0].toUpperCase()}</span>
              <span className="flex-1 truncate font-mono" title={f.path}>{f.path}</span>
              {!f.binary && <span className="text-[10px] text-zinc-500">+{f.added}/-{f.removed}</span>}
            </button>
          </li>
        ))}
      </ul>
      <div className="min-w-0 flex-1 overflow-auto">
        {diff && (
          <table className="w-full table-fixed font-mono text-[11px]">
            <thead className="sticky top-0 bg-zinc-900 text-left text-zinc-500">
              <tr><th className="w-10" /><th className="px-2 py-1">source</th><th className="w-10" /><th className="px-2 py-1">migrated</th></tr>
            </thead>
            <tbody>
              {diff.rows.map((r, i) => r.k === "skip" ? (
                <tr key={i}><td colSpan={4} className="bg-zinc-900/80 py-0.5 text-center text-zinc-600">⋯ {r.n} unchanged lines ⋯</td></tr>
              ) : (
                <tr key={i} className="align-top">
                  <td className="select-none pr-1 text-right text-zinc-600">{r.ln}</td>
                  <td className={`whitespace-pre-wrap break-all px-2 ${r.l != null ? BG[r.k][0] : "bg-zinc-900/60"}`}>{r.l}</td>
                  <td className="select-none border-l border-zinc-800 pr-1 text-right text-zinc-600">{r.rn}</td>
                  <td className={`whitespace-pre-wrap break-all px-2 ${r.r != null ? BG[r.k][1] : "bg-zinc-900/60"}`}>{r.r}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
