import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const ICON = { Read: "📄", Edit: "✏️", MultiEdit: "✏️", Write: "📝", Grep: "🔎", Glob: "🗂️", Bash: "⌘" };

function EditDiff({ oldText, newText }) {
  return (
    <div className="mt-1 overflow-hidden rounded border border-zinc-800 font-mono text-[11px]">
      {oldText && <pre className="whitespace-pre-wrap bg-rose-500/10 px-2 py-1 text-rose-200">{oldText.split("\n").map((l) => "- " + l).join("\n")}</pre>}
      {newText && <pre className="whitespace-pre-wrap bg-emerald-500/10 px-2 py-1 text-emerald-200">{newText.split("\n").map((l) => "+ " + l).join("\n")}</pre>}
    </div>
  );
}

function Tool({ ev, result }) {
  const d = ev.data;
  const editable = ["Edit", "MultiEdit", "Write"].includes(d.name);
  const [open, setOpen] = useState(editable);
  const target = d.path || d.command || d.pattern || d.input || "";
  return (
    <div className={`rounded-md border px-2.5 py-1.5 text-xs ${result?.is_error ? "border-rose-500/40 bg-rose-500/5" : editable ? "border-emerald-500/30 bg-emerald-500/5" : "border-zinc-800 bg-zinc-900/60"}`}>
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(!open)}>
        <span>{ICON[d.name] || "🔧"}</span>
        <span className="font-semibold text-zinc-200">{d.name}</span>
        <span className="truncate font-mono text-zinc-400">{String(target)}</span>
        {result?.is_error && <span className="ml-auto text-rose-400">error</span>}
        {!result && <span className="ml-auto h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />}
      </button>
      {open && (
        <div className="mt-1">
          {d.name === "Edit" && <EditDiff oldText={d.old} newText={d.new} />}
          {d.name === "MultiEdit" && d.edits?.map((e, i) => <EditDiff key={i} oldText={e.old} newText={e.new} />)}
          {d.name === "Write" && <EditDiff newText={d.content} />}
          {result && (
            <pre className={`mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 p-2 font-mono text-[11px] ${result.is_error ? "text-rose-300" : "text-zinc-400"}`}>{result.content}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function ClaudeActivity({ events }) {
  const box = useRef();
  const [follow, setFollow] = useState(true);
  const results = {};
  events.forEach((e) => { if (e.kind === "claude_tool_result") results[e.data.tool_use_id] = e.data; });
  // scroll our own container only (scrollIntoView would scroll the whole page)
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [events.length, follow]);
  const items = events.filter((e) => e.kind !== "claude_tool_result" && e.kind !== "claude_session");
  if (!items.length) return <div className="p-6 text-center text-sm text-zinc-500">No Claude activity yet.</div>;
  return (
    <div ref={box} className="h-full space-y-2 overflow-auto p-3" onWheel={(e) => e.deltaY < 0 && setFollow(false)}>
      {items.map((e, i) => {
        const d = e.data;
        if (e.kind === "claude_start") {
          return (
            <div key={i} className="flex items-center gap-2 pt-2 text-[11px] uppercase tracking-wide text-violet-300">
              <span className="h-px flex-1 bg-violet-500/30" />
              {/^q\d+$/.test(String(d.attempt)) ? `Question ${String(d.attempt).slice(1)}`
                : d.attempt === 0 ? "Migration run" : d.attempt === "0c" ? "Migration run (continued after max turns)"
                : /^m\d+$/.test(String(d.attempt)) ? `Manual fix round ${String(d.attempt).slice(1)}`
                : /^m\d+\.\d+$/.test(String(d.attempt)) ? `Manual fix round ${String(d.attempt).slice(1).split(".")[0]}, auto-fix attempt ${String(d.attempt).split(".")[1]}`
                : `Auto-fix attempt ${d.attempt}`}{d.resume ? " (resumed session)" : ""}
              <span className="h-px flex-1 bg-violet-500/30" />
            </div>
          );
        }
        if (e.kind === "claude_init") return <div key={i} className="text-[11px] text-zinc-500">model {d.model} · {d.permission_mode} · cwd {d.cwd}</div>;
        if (e.kind === "claude_text") {
          return (
            <div key={i} className={`md rounded-lg px-3 py-2 text-sm ${d.role === "stderr" ? "bg-rose-500/10 text-rose-200" : "bg-zinc-800/60 text-zinc-200"}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{d.text}</ReactMarkdown>
            </div>
          );
        }
        if (e.kind === "claude_tool") return <Tool key={i} ev={e} result={results[d.id]} />;
        if (e.kind === "claude_result") {
          return (
            <div key={i} className={`rounded-lg border px-3 py-2 text-xs ${d.is_error ? "border-rose-500/40 text-rose-200" : "border-violet-500/30 text-zinc-300"}`}>
              Finished: {d.subtype} · {d.num_turns} turns · {(d.duration_ms / 1000).toFixed(0)}s{d.cost_usd != null ? ` · $${d.cost_usd.toFixed(2)}` : ""}
            </div>
          );
        }
        return null;
      })}
      {!follow && <button className="sticky bottom-2 ml-auto block rounded-full bg-violet-600 px-3 py-1 text-xs text-white" onClick={() => setFollow(true)}>Follow ↓</button>}
    </div>
  );
}
