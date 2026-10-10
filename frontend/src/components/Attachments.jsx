import React, { useRef, useState } from "react";
import { api } from "../api.js";

const ACCEPT = "image/png,image/jpeg,image/gif,image/webp,application/pdf,.txt,.log,.md,.csv,.json,.xml,.py,.js,.css,.scss,.html,.po,.pot,.rst,.yml,.yaml,.sql,.diff,.patch";
const fileUrl = (f) => `/api/attachments/${f.id}`;
const fmtSize = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

/* Files attached to one text box: upload on pick, paste or drop; `ids` go with the request. */
export function useAttach() {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const upload = async (list) => {
    const picked = [...(list || [])];
    if (!picked.length) return;
    const form = new FormData();
    picked.forEach((f, i) => form.append("files", f, f.name || `pasted-${Date.now()}-${i}.png`));
    setUploading(true); setError(null);
    try {
      const added = await api("/api/attachments", { method: "POST", form });
      setFiles((prev) => [...prev, ...added]);
    } catch (e) { setError(e.message); } finally { setUploading(false); }
  };

  const textareaProps = {
    onPaste: (e) => {
      const pasted = [...(e.clipboardData?.files || [])];
      if (pasted.length) { e.preventDefault(); upload(pasted); }
    },
    onDrop: (e) => {
      const dropped = [...(e.dataTransfer?.files || [])];
      if (dropped.length) { e.preventDefault(); upload(dropped); }
    },
    onDragOver: (e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); },
  };

  return { files, ids: files.map((f) => f.id), upload, uploading, error, textareaProps,
    remove: (id) => setFiles((l) => l.filter((f) => f.id !== id)), clear: () => { setFiles([]); setError(null); } };
}

function Chip({ f, onRemove }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 py-0.5 pl-0.5 pr-1.5 text-[11px] text-zinc-300">
      {f.kind === "image"
        ? <img src={fileUrl(f)} alt="" className="h-6 w-6 rounded object-cover" />
        : <span className="flex h-6 w-6 items-center justify-center rounded bg-zinc-800 text-[9px] uppercase text-zinc-400">{f.kind === "pdf" ? "pdf" : "txt"}</span>}
      <a href={fileUrl(f)} target="_blank" rel="noreferrer" className="min-w-0 truncate hover:underline" title={f.name}>{f.name}</a>
      {f.size != null && <span className="text-zinc-500">{fmtSize(f.size)}</span>}
      {onRemove && <button type="button" onClick={onRemove} className="ml-0.5 text-zinc-500 hover:text-rose-300" aria-label={`Remove ${f.name}`}>×</button>}
    </span>
  );
}

/* Attach button + chips; put it right under the text box that uses `att.textareaProps`. */
export function AttachBar({ att, disabled = false }) {
  const input = useRef();
  return (
    <div className="mt-1 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" disabled={disabled || att.uploading} onClick={() => input.current?.click()}
          className="rounded-md px-2 py-1 text-[11px] text-zinc-300 ring-1 ring-zinc-700 hover:bg-zinc-800 disabled:opacity-40">
          {att.uploading ? "Uploading…" : "Attach files"}
        </button>
        {!att.files.length && <span className="text-[11px] text-zinc-500">or paste a screenshot / drop files on the box · images, PDFs, logs, code</span>}
        {att.files.map((f) => <Chip key={f.id} f={f} onRemove={disabled ? null : () => att.remove(f.id)} />)}
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden"
          onChange={(e) => { att.upload(e.target.files); e.target.value = ""; }} />
      </div>
      {att.error && <div className="text-[11px] text-rose-300">{att.error}</div>}
    </div>
  );
}

/* Read-only list for files already sent (history). */
export function AttachmentList({ files }) {
  if (!files?.length) return null;
  return <div className="mt-1 flex flex-wrap gap-1.5">{files.map((f) => <Chip key={f.id} f={f} />)}</div>;
}
