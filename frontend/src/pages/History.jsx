import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDuration, fmtTime, useEvents, useFetch } from "../api.js";
import { Badge, Button, Card, Empty, ErrorBox } from "../components/ui.jsx";

export default function History() {
  const nav = useNavigate();
  const [jobs, reload] = useFetch("/api/jobs");
  const [dbs, reloadDbs] = useFetch("/api/databases");
  const [err, setErr] = useState(null);
  useEvents((ev) => { if (ev.kind === "job" || ev.kind === "module") reload(); });
  const rerun = (id) => api(`/api/jobs/${id}/rerun-failed`, { method: "POST" }).then((j) => nav(`/jobs/${j.id}`)).catch((e) => setErr(e.message));
  const dropDb = (name) => api(`/api/databases/${name}`, { method: "DELETE" }).then(reloadDbs).catch((e) => setErr(e.message));
  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <h1 className="text-xl font-semibold">Jobs</h1>
      <ErrorBox>{err}</ErrorBox>
      <Card>
        {!jobs?.length ? <Empty>No jobs yet. Start one from the Migration page.</Empty> : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-500">
              <tr className="border-b border-zinc-800"><th className="py-2">Job</th><th>Versions</th><th>Status</th><th>Modules</th><th>Started</th><th>Duration</th><th /></tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const failed = Object.entries(j.counts).filter(([s]) => s !== "passed").reduce((a, [, n]) => a + n, 0);
                return (
                  <tr key={j.id} className="border-b border-zinc-800/70 hover:bg-zinc-800/30">
                    <td className="py-2"><Link className="font-mono text-violet-300 hover:underline" to={`/jobs/${j.id}`}>{j.id}</Link>
                      {j.parent_job && <div className="text-[11px] text-zinc-500">re-run of {j.parent_job}</div>}</td>
                    <td>{j.options?.kind === "create" ? `new module · ${j.target_version}` : `${j.source_version} → ${j.target_version}`}</td>
                    <td><Badge status={j.status} /></td>
                    <td className="space-x-1">{Object.entries(j.counts).map(([s, n]) => <Badge key={s} status={s}>{n} {s}</Badge>)}</td>
                    <td className="text-xs text-zinc-400">{fmtTime(j.created_at)}</td>
                    <td className="text-xs text-zinc-400">{j.finished_at ? fmtDuration(j.finished_at - j.created_at) : "…"}</td>
                    <td className="text-right">
                      {j.options?.kind !== "create" && j.status !== "running" && j.status !== "queued" && failed > 0 && <Button className="text-xs" onClick={() => rerun(j.id)}>Re-run failed</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Kept test databases (mig_*)">
        {!dbs?.length ? <div className="text-sm text-zinc-500">None.</div> : (
          <ul className="space-y-1 text-sm">
            {dbs.map((d) => (
              <li key={d} className="flex items-center gap-3"><span className="font-mono">{d}</span>
                <Button variant="ghost" className="text-xs text-rose-300" onClick={() => dropDb(d)}>Drop</Button></li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
