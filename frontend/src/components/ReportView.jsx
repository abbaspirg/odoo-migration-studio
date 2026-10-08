import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api.js";

export default function ReportView({ jobId, module, refreshKey }) {
  const [text, setText] = useState(null);
  useEffect(() => { api(`/api/jobs/${jobId}/modules/${module}/report`).then(setText).catch(() => setText("")); }, [jobId, module, refreshKey]);
  if (text === null) return <div className="p-6 text-sm text-zinc-500">Loading…</div>;
  if (!text) return <div className="p-6 text-center text-sm text-zinc-500">The report is written when the module finishes.</div>;
  return <div className="md h-full overflow-auto p-5 text-sm text-zinc-300"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}
