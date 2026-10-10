import React, { useEffect, useState } from "react";
import { api, fmtTime, useEvents } from "../api.js";
import { Badge, Button, EditionSelect, ErrorBox } from "./ui.jsx";
import { AttachBar, AttachmentList, useAttach } from "./Attachments.jsx";

const STATUS = { starting: "running", ready: "passed", stopping: "cancelled", stopped: "skipped", crashed: "failed" };

export default function ManualTest({ jobId, mod, onShowLog }) {
  const module = mod.module;
  const [server, setServer] = useState(null);
  const [fresh, setFresh] = useState(false);
  const [demo, setDemo] = useState(false);
  const [edition, setEdition] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [notes, setNotes] = useState("");
  const [saved, setSaved] = useState(mod.manual_test);
  const [rule, setRule] = useState("");
  const [ruleSaved, setRuleSaved] = useState(null);
  const fix = mod.manual_fix;

  useEffect(() => {
    setServer(null); setErr(null); setSaved(mod.manual_test); setNotes(mod.manual_test?.notes || ""); setRuleSaved(null);
    api("/api/manual").then((list) => setServer(list.find((s) => s.job_id === jobId && s.module === module) || null)).catch(() => {});
  }, [jobId, module]);

  useEvents((ev) => {
    if (ev.job_id !== jobId || ev.module !== module) return;
    if (ev.kind === "manual") setServer(ev.data);
    if (ev.kind === "manual_result") setSaved(ev.data);
  });

  const run = (fn) => async () => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const start = run(async () => { setServer(await api(`/api/jobs/${jobId}/modules/${module}/manual`, { method: "POST", body: { fresh, demo, edition } })); onShowLog?.(); });
  const stop = run(() => api(`/api/jobs/${jobId}/modules/${module}/manual`, { method: "DELETE" }));
  const record = (result) => run(async () => setSaved(await api(`/api/jobs/${jobId}/modules/${module}/manual-result`, { method: "POST", body: { result, notes } })))();

  useEffect(() => { setRule(fix?.rule_suggestion || ""); setRuleSaved(null); }, [fix?.round, fix?.rule_suggestion]);
  const att = useAttach();
  const sendFix = run(async () => {
    await api(`/api/jobs/${jobId}/modules/${module}/manual-fix`, { method: "POST", body: { notes, attachments: att.ids } });
    att.clear();
  });
  const saveRule = run(async () => setRuleSaved((await api("/api/rules/lessons", { method: "POST", body: { text: rule, module } })).path));
  const fixing = fix?.status === "running";

  const active = server && (server.status === "starting" || server.status === "ready" || server.status === "stopping");
  const pipelineBusy = mod.status === "running" || mod.status === "queued";

  return (
    <div className="mt-5 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold">Manual test</h3>
        {server && <Badge status={STATUS[server.status]}>{server.status}</Badge>}
        {saved && <Badge status={saved.result} className="ml-auto">manual: {saved.result}</Badge>}
      </div>

      {!active && (
        <div className="space-y-2 text-xs text-zinc-400">
          <p>Start a real Odoo {""}server with the migrated module installed, then log in and click through its screens.</p>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="accent-violet-500" checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
            fresh database (otherwise reuse a kept one if it has the module installed)
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="accent-violet-500" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo data (only reuses a database that also has demo data, else creates one)
          </label>
          <EditionSelect id="manual-edition" compact value={edition} onChange={setEdition} />
          <Button variant="primary" className="w-full" disabled={busy || pipelineBusy} onClick={start}>
            {pipelineBusy ? "Available when the pipeline finishes" : "Start Odoo for manual test"}
          </Button>
          {server?.status === "crashed" && <div className="text-rose-300">{server.error}</div>}
        </div>
      )}

      {active && (
        <div className="space-y-2 text-xs">
          {server.status === "starting" && (
            <div className="text-sky-300">{server.installing ? "Creating the database and installing the module…" : "Starting Odoo…"} (live output in the Odoo log tab)</div>
          )}
          {server.status === "ready" && (
            <a href={server.url} target="_blank" rel="noreferrer"
              className="block rounded-md bg-emerald-600 px-3 py-2 text-center text-sm font-medium text-white hover:bg-emerald-500">
              Open Odoo ↗ <span className="font-mono text-xs opacity-80">{server.url}</span>
            </a>
          )}
          <div className="grid grid-cols-[5rem_1fr] gap-y-0.5 text-zinc-400">
            <span>login</span><span className="font-mono text-zinc-200">{server.login} / {server.password}</span>
            <span>database</span><span className="break-all font-mono">{server.db}</span>
            <span>demo data</span><span>{server.demo ? "yes" : "no"}</span>
            {server.edition && <><span>edition</span><span>{server.edition}{server.edition_choice === "auto" ? " (auto)" : ""}</span></>}
            <span>started</span><span>{fmtTime(server.started_at)}</span>
          </div>
          <Button variant="danger" className="w-full" disabled={busy || server.status === "stopping"} onClick={stop}>Stop server</Button>
          <div className="text-[11px] text-zinc-500">The database is kept after stopping (Jobs page → drop it when done).</div>
        </div>
      )}

      <div className="mt-3 border-t border-zinc-800 pt-3">
        <div className="mb-1 text-xs text-zinc-400">Your verdict (added to the module report)</div>
        <textarea id="manual-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} {...att.textareaProps}
          placeholder="What you checked, what broke…"
          className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-2 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
        <AttachBar att={att} />
        <div className="mt-1 flex gap-2">
          <Button className="flex-1 text-xs" disabled={busy} onClick={() => record("passed")}>✓ Mark passed</Button>
          <Button className="flex-1 text-xs" disabled={busy} onClick={() => record("failed")}>✕ Mark failed</Button>
        </div>
        {saved && <div className="mt-1 text-[11px] text-zinc-500">Recorded {saved.result} · {fmtTime(saved.at)}</div>}
      </div>

      <div className="mt-3 border-t border-zinc-800 pt-3 text-xs">
        <div className="mb-1 flex items-center gap-2">
          <span className="font-semibold text-zinc-200">Fix with Claude</span>
          {fix && <Badge status={fix.status === "running" ? "running" : fix.status}>round {fix.round}: {fix.status}</Badge>}
        </div>
        {fixing ? (
          <>
            <p className="text-sky-300">Claude is fixing what you reported (round {fix.round}). Follow it in the Claude tab; the checks, install and tests re-run after. If they pass, Odoo restarts here on a fresh database.</p>
            <AttachmentList files={fix.attachments} />
          </>
        ) : (
          <>
            <p className="mb-2 text-zinc-400">Describe what's wrong in the box above: the page, what you did, what you expected. Paste browser console errors too, and attach screenshots. Claude also gets the errors from this Odoo's log.</p>
            <Button variant="primary" className="w-full" disabled={busy || pipelineBusy || !notes.trim()} onClick={sendFix}>
              Send to Claude to fix
            </Button>
          </>
        )}
        {fix && !fixing && fix.status !== "passed" && fix.error && <div className="mt-2 text-rose-300">{fix.error}</div>}
        {fix && !fixing && fix.status === "passed" && <div className="mt-2 text-emerald-300">Fixed and re-checked. Test it again in the restarted Odoo above.</div>}
        {fix?.restart_error && <div className="mt-2 text-amber-300">Could not restart Odoo: {fix.restart_error}</div>}

        {fix && !fixing && (
          <div className="mt-3 rounded-md border border-zinc-800 bg-zinc-950/60 p-2">
            <div className="mb-1 text-zinc-400">Save the lesson for future migrations</div>
            {!fix.rule_suggestion && <p className="mb-1 text-zinc-500">Claude found nothing general to learn here. You can still write a rule yourself.</p>}
            <textarea value={rule} onChange={(e) => { setRule(e.target.value); setRuleSaved(null); }} rows={3}
              placeholder="e.g. Website controllers must …"
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-2 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
            <Button className="mt-1 w-full text-xs" disabled={busy || !rule.trim() || !!ruleSaved} onClick={saveRule}>
              {ruleSaved ? "Added to migration rules" : "Add to migration rules"}
            </Button>
            {ruleSaved && <div className="mt-1 break-all text-[11px] text-zinc-500">Saved in {ruleSaved}. Every later migration gets it.</div>}
          </div>
        )}
      </div>
      <ErrorBox>{err}</ErrorBox>
    </div>
  );
}
