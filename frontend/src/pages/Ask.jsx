import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, fmtTime, useEvents, useFetch } from "../api.js";
import { Badge, Button, ErrorBox, Select } from "../components/ui.jsx";
import ClaudeActivity from "../components/ClaudeActivity.jsx";
import { AttachBar, AttachmentList, useAttach } from "../components/Attachments.jsx";

const EXAMPLES = [
  "When is the invoiced quantity of a sale order line updated for a service product?",
  "What exactly does the lock date on posted journal entries block, and who can bypass it?",
  "How does a delivery's backorder get created, and which setting decides whether to ask?",
];

const CITE = /^(\/[^\s`]+?\.[A-Za-z0-9]+):(\d+)(?:-\d+)?$/;

// a cited absolute path, shortened to what a reader recognises (module/…/file.py)
function shortPath(path, workspace) {
  let p = workspace && path.startsWith(workspace + "/") ? path.slice(workspace.length + 1) : path;
  const i = p.lastIndexOf("addons/");
  if (i >= 0) return p.slice(i + 7);
  return p.split("/").slice(-4).join("/");
}

function Answer({ text, workspace, onCite }) {
  return (
    <div className="md text-sm text-zinc-200">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        code({ inline, className, children, ...props }) {
          const raw = String(children).trim();
          const m = !className && raw.match(CITE);
          if (m) {
            return (
              <button type="button" onClick={() => onCite(m[1], Number(m[2]))} title={raw}
                className="rounded bg-violet-500/15 px-1.5 py-0.5 font-mono text-[12px] text-violet-200 ring-1 ring-violet-500/30 hover:bg-violet-500/25">
                {shortPath(m[1], workspace)}:{m[2]}
              </button>
            );
          }
          return <code className={className} {...props}>{children}</code>;
        },
      }}>{text}</ReactMarkdown>
    </div>
  );
}

function SourcePanel({ threadId, cite, onClose }) {
  const [snip, setSnip] = useState(null);
  const [err, setErr] = useState(null);
  const hit = useRef();
  useEffect(() => {
    setSnip(null); setErr(null);
    api(`/api/ask/threads/${threadId}/source?path=${encodeURIComponent(cite.path)}&line=${cite.line}`)
      .then(setSnip).catch((e) => setErr(e.message));
  }, [threadId, cite.path, cite.line]);
  useEffect(() => { hit.current?.scrollIntoView({ block: "center" }); }, [snip]);
  return (
    <aside className="flex min-h-0 w-[44%] min-w-[340px] flex-col border-l border-zinc-800 bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-zinc-800 px-3 py-2">
        <span className="min-w-0 truncate font-mono text-xs text-zinc-300" title={snip?.path}>{snip?.display || cite.path}</span>
        <Button variant="ghost" className="ml-auto px-2 py-0.5 text-xs" onClick={onClose}>Close</Button>
      </div>
      {err && <div className="p-3 text-xs text-rose-300">{err}</div>}
      {snip && (
        <pre className="min-h-0 flex-1 overflow-auto py-2 font-mono text-[12px] leading-5">
          {snip.lines.map((l, i) => {
            const n = snip.start + i;
            const on = n === snip.line;
            return (
              <div key={n} ref={on ? hit : undefined} className={on ? "bg-violet-500/20" : ""}>
                <span className="inline-block w-12 select-none pr-3 text-right text-zinc-600">{n}</span>
                <span className={on ? "text-zinc-50" : "text-zinc-300"}>{l || " "}</span>
              </div>
            );
          })}
        </pre>
      )}
    </aside>
  );
}

function NewQuestion({ options, onCreated }) {
  const [version, setVersion] = useState("");
  const [enterprise, setEnterprise] = useState(true);
  const [custom, setCustom] = useState(true);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const att = useAttach();
  useEffect(() => { if (options?.length && !version) setVersion(options[options.length - 1].version); }, [options]);
  const opt = options?.find((o) => o.version === version);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const t = await api("/api/ask/threads", { method: "POST", body: {
        version, question, enterprise: enterprise && !!opt?.enterprise, custom: custom && !!opt?.custom.length, attachments: att.ids } });
      onCreated(t);
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };
  if (options && !options.length) {
    return <div className="p-8 text-sm text-zinc-400">No Odoo sources yet. Clone a version in <b>Versions &amp; Sources</b> first.</div>;
  }
  return (
    <form onSubmit={submit} className="mx-auto max-w-3xl space-y-4 p-8">
      <div>
        <h1 className="text-lg font-semibold text-zinc-100">Ask Odoo</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Ask how Odoo behaves. Claude answers only from the Odoo source code below, read-only, and cites the
          file and line behind each point so you can check it.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-zinc-300">
        <label className="flex items-center gap-2">Odoo
          <Select id="ask-version" value={version} onChange={(e) => setVersion(e.target.value)}>
            {options?.map((o) => <option key={o.version} value={o.version}>{o.version}</option>)}
          </Select>
        </label>
        <span className="text-zinc-500">community, always searched</span>
        <label className={`flex items-center gap-2 ${opt?.enterprise ? "" : "opacity-40"}`} title={opt?.enterprise ? "" : "No enterprise code for this version (Versions & Sources)"}>
          <input id="ask-enterprise" type="checkbox" className="accent-violet-500" disabled={!opt?.enterprise}
            checked={enterprise && !!opt?.enterprise} onChange={(e) => setEnterprise(e.target.checked)} />
          enterprise addons
        </label>
        <label className={`flex items-center gap-2 ${opt?.custom.length ? "" : "opacity-40"}`}
          title={opt?.custom.length ? opt.custom.join(", ") : `No custom modules with a ${version} manifest`}>
          <input id="ask-custom" type="checkbox" className="accent-violet-500" disabled={!opt?.custom.length}
            checked={custom && !!opt?.custom.length} onChange={(e) => setCustom(e.target.checked)} />
          your custom modules{opt?.custom.length ? ` (${opt.custom.length})` : ""}
        </label>
      </div>
      <div>
      <textarea id="ask-question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={5} {...att.textareaProps}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e); }}
        placeholder="e.g. Which setting makes Odoo ask for a backorder when validating a partial delivery?"
        className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
      <AttachBar att={att} />
      </div>
      <div className="flex items-center gap-3">
        <Button variant="primary" type="submit" disabled={busy || att.uploading || !question.trim() || !version}>Ask Claude</Button>
        <span className="text-xs text-zinc-500">Ctrl+Enter · runs on your Claude account, read-only</span>
      </div>
      {err && <ErrorBox>{err}</ErrorBox>}
      <div className="space-y-1.5 pt-2">
        <div className="text-xs uppercase tracking-wide text-zinc-500">Examples</div>
        {EXAMPLES.map((q) => (
          <button key={q} type="button" onClick={() => setQuestion(q)}
            className="block w-full rounded-md border border-zinc-800 px-3 py-2 text-left text-sm text-zinc-300 hover:border-zinc-600">{q}</button>
        ))}
      </div>
    </form>
  );
}

function Thread({ threadId, workspace, onChanged }) {
  const [thread, setThread] = useState(null);
  const [evs, setEvs] = useState([]);
  const [showWork, setShowWork] = useState(false);
  const [cite, setCite] = useState(null);
  const [follow, setFollow] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const navigate = useNavigate();
  const bottom = useRef();
  const att = useAttach();

  const load = () => api(`/api/ask/threads/${threadId}`).then(setThread).catch((e) => setErr(e.message));
  useEffect(() => {
    setThread(null); setEvs([]); setCite(null); setErr(null); setShowWork(false);
    load();
    api(`/api/ask/threads/${threadId}/events`).then(setEvs).catch(() => {});
  }, [threadId]);

  useEvents((ev) => {
    if (ev.job_id !== "ask" || ev.module !== threadId) return;
    if (ev.kind.startsWith("claude_")) setEvs((l) => [...l, ev]);
    if (ev.kind === "ask_message") setThread((t) => t && ({ ...t, messages: [...t.messages.filter((m) => m.id !== ev.data.id), ev.data] }));
    if (ev.kind === "ask_status") { load(); onChanged(); }
  });

  const running = thread?.running || thread?.status === "running";
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [thread?.messages.length, running]);

  const send = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { setThread(await api(`/api/ask/threads/${threadId}/messages`, { method: "POST", body: { question: follow, attachments: att.ids } })); setFollow(""); att.clear(); onChanged(); }
    catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };
  const stop = () => api(`/api/ask/threads/${threadId}/cancel`, { method: "POST" }).catch((e) => setErr(e.message));
  const remove = async () => {
    try { await api(`/api/ask/threads/${threadId}`, { method: "DELETE" }); onChanged(); navigate("/ask"); }
    catch (e) { setErr(e.message); }
  };

  // Claude's work for the question being answered now: events since the last claude_start
  const liveEvents = useMemo(() => {
    const i = evs.map((e) => e.kind).lastIndexOf("claude_start");
    return i < 0 ? [] : evs.slice(i);
  }, [evs]);

  if (!thread) return <div className="p-8 text-sm text-zinc-500">{err || "Loading…"}</div>;
  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-5 py-2.5">
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-100" title={thread.title}>{thread.title}</span>
          <Badge>Odoo {thread.version}</Badge>
          <Badge>community{thread.scopes.enterprise ? " + enterprise" : ""}{thread.scopes.custom ? " + custom" : ""}</Badge>
          {running && <Badge status="running">answering</Badge>}
          {thread.status === "failed" && <Badge status="failed">failed</Badge>}
          <Button variant="ghost" className="text-xs" onClick={() => setShowWork(!showWork)}>{showWork ? "Hide" : "Show"} Claude's search</Button>
          {running ? <Button variant="danger" className="text-xs" onClick={stop}>Stop</Button>
            : <Button variant="ghost" className="text-xs text-zinc-500" onClick={remove}>Delete</Button>}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl space-y-4 px-5 py-5">
            {thread.messages.map((m) => m.role === "user" ? (
              <div key={m.id} className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-lg bg-violet-600/20 px-3 py-2 text-sm text-zinc-100 ring-1 ring-violet-500/30">{m.text}<AttachmentList files={m.meta?.attachments} /></div>
            ) : (
              <div key={m.id} className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-3">
                <Answer text={m.text} workspace={workspace} onCite={(path, line) => setCite({ path, line })} />
                <div className="mt-2 text-[11px] text-zinc-500">
                  {fmtTime(m.created_at)}{m.meta?.turns ? ` · ${m.meta.turns} turns` : ""}
                  {m.meta?.cost_usd != null ? ` · API-price estimate $${m.meta.cost_usd.toFixed(2)}` : ""}
                </div>
              </div>
            ))}
            {running && (
              <div className="rounded-lg border border-sky-500/30 bg-sky-500/5">
                <div className="px-3 pt-2 text-xs text-sky-300">Claude is reading the Odoo source…</div>
                <div className="h-80"><ClaudeActivity events={liveEvents} /></div>
              </div>
            )}
            {!running && showWork && (
              <div className="h-[32rem] rounded-lg border border-zinc-800"><ClaudeActivity events={evs} /></div>
            )}
            {thread.error && !running && <div className="text-xs text-rose-300">{thread.error}</div>}
            <div ref={bottom} />
          </div>
        </div>
        <form onSubmit={send} className="border-t border-zinc-800 px-5 py-3">
          <div className="mx-auto flex max-w-3xl gap-2">
            <div className="min-w-0 flex-1">
            <textarea id="ask-follow-up" value={follow} onChange={(e) => setFollow(e.target.value)} rows={2} {...att.textareaProps}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(e); }}
              placeholder={running ? "Wait for the answer…" : "Ask a follow-up (Claude keeps the context)…"}
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
            <AttachBar att={att} disabled={running} />
            </div>
            <Button variant="primary" type="submit" className="self-start" disabled={busy || running || att.uploading || !follow.trim()}>Ask</Button>
          </div>
          {err && <div className="mx-auto mt-1 max-w-3xl text-xs text-rose-300">{err}</div>}
        </form>
      </div>
      {cite && <SourcePanel threadId={threadId} cite={cite} onClose={() => setCite(null)} />}
    </div>
  );
}

export default function Ask({ status }) {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const [threads, reload] = useFetch("/api/ask/threads");
  const [options] = useFetch("/api/ask/options");
  useEvents((ev) => { if (ev.job_id === "ask" && ev.kind === "ask_status") reload(); });
  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800">
        <div className="p-3">
          <Button variant="primary" className="w-full" onClick={() => navigate("/ask")}>New question</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
          {threads?.length === 0 && <div className="px-2 text-xs text-zinc-500">Your questions appear here.</div>}
          {threads?.map((t) => (
            <button key={t.id} onClick={() => navigate(`/ask/${t.id}`)}
              className={`mb-1 block w-full rounded-md px-2.5 py-2 text-left ${t.id === threadId ? "bg-zinc-800" : "hover:bg-zinc-900"}`}>
              <div className="line-clamp-2 text-sm text-zinc-200">{t.title}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
                {t.running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />}
                {t.version} · {fmtTime(t.updated_at)}
              </div>
            </button>
          ))}
        </div>
      </aside>
      {threadId
        ? <Thread key={threadId} threadId={threadId} workspace={status?.workspace} onChanged={reload} />
        : <div className="min-h-0 flex-1 overflow-auto"><NewQuestion options={options} onCreated={(t) => { reload(); navigate(`/ask/${t.id}`); }} /></div>}
    </div>
  );
}
