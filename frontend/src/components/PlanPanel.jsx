import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, fmtTime } from "../api.js";
import { Badge, Button, ErrorBox } from "./ui.jsx";

/* A new module's plan: Claude drafts it, the user edits, revises or approves it. */
export default function PlanPanel({ jobId, mod, busy: jobBusy, onDone }) {
  const plan = mod.plan || {};
  const [text, setText] = useState(plan.text || "");
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { setText(plan.text || ""); setEditing(false); }, [plan.text, plan.round]);

  const post = (path, body) => async () => {
    setBusy(true); setErr(null);
    try { await api(`/api/jobs/${jobId}/modules/${mod.module}/plan/${path}`, { method: "POST", body }); setFeedback(""); onDone?.(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const planning = mod.status === "planning";
  const locked = jobBusy || planning || busy;
  const built = plan.status === "approved";

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-zinc-100">Plan for <span className="font-mono">{mod.module}</span></h2>
          {plan.round > 0 && <Badge>round {plan.round}</Badge>}
          {planning && <Badge status="planning">Claude is {plan.text ? "revising" : "drafting"}</Badge>}
          {!planning && plan.text && !built && <Badge status="plan_ready">waiting for your approval</Badge>}
          {built && <Badge status="passed">approved {plan.approved_at ? fmtTime(plan.approved_at) : ""}</Badge>}
          {plan.text && !locked && (
            <Button variant="ghost" className="ml-auto text-xs" onClick={() => setEditing(!editing)}>{editing ? "Preview" : "Edit plan"}</Button>
          )}
        </div>

        {!plan.text && (
          <div className="rounded-lg border border-zinc-800 p-4 text-sm text-zinc-400">
            {planning ? "Claude is reading the Odoo source and drafting the plan. Follow it in Claude activity; it usually takes a few minutes."
              : mod.error || "No plan yet."}
          </div>
        )}

        {plan.text && (editing ? (
          <textarea id="plan-text" value={text} onChange={(e) => setText(e.target.value)} rows={28}
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-3 font-mono text-xs leading-5 text-zinc-100 focus:border-violet-500 focus:outline-none" />
        ) : (
          <div className="md rounded-lg border border-zinc-800 bg-zinc-900/60 px-5 py-4 text-sm text-zinc-200">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        ))}

        {plan.text && (
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-3">
              <div className="text-sm font-medium text-zinc-100">{built ? "Rebuild from this plan" : "Approve and build"}</div>
              <p className="mt-1 text-xs text-zinc-400">
                Claude writes the module {text !== plan.text ? "from your edited plan " : ""}with tests, then the studio checks, installs and tests it.
                {built ? " The current version is backed up first." : ""}
              </p>
              <Button variant="primary" className="mt-2 w-full" disabled={locked || !text.trim()} onClick={post("approve", { text })}>
                {built ? "Rebuild" : "Approve and build"}
              </Button>
            </div>
            <div className="rounded-lg border border-zinc-800 p-3">
              <div className="text-sm font-medium text-zinc-100">Ask Claude to revise</div>
              <textarea id="plan-feedback" value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3}
                placeholder="e.g. Use the existing Delivery Method instead of a new model; answer to question 2: yes"
                className="mt-1 w-full rounded-md border border-zinc-700 bg-zinc-950 p-2 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
              <Button className="mt-1 w-full" disabled={locked || !feedback.trim()} onClick={post("revise", { feedback })}>Revise the plan</Button>
            </div>
          </div>
        )}

        {err && <ErrorBox>{err}</ErrorBox>}
        {plan.history?.length > 0 && (
          <div className="space-y-1 text-xs text-zinc-500">
            <div className="uppercase tracking-wide">Your revision requests</div>
            {plan.history.map((h) => <div key={h.round}>Round {h.round}: {h.feedback}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}
