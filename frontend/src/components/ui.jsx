import React from "react";

export const STATUS_STYLES = {
  pending: "bg-zinc-800 text-zinc-400 ring-zinc-700",
  queued: "bg-zinc-800 text-zinc-400 ring-zinc-700",
  running: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  passed: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  failed: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
  skipped: "bg-zinc-800 text-zinc-500 ring-zinc-700",
  cancelled: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  interrupted: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  done: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  planning: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  plan_ready: "bg-amber-500/15 text-amber-200 ring-amber-500/40",
  waiting: "bg-amber-500/15 text-amber-200 ring-amber-500/40",
};

export function Badge({ status, children, className = "" }) {
  const s = STATUS_STYLES[status] || "bg-zinc-800 text-zinc-300 ring-zinc-700";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${s} ${className}`}>
      {(status === "running" || status === "planning") && <span className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-pulse" />}
      {children || (status === "plan_ready" ? "plan ready" : status)}
    </span>
  );
}

export function StatusDot({ status }) {
  const map = {
    passed: "✓", failed: "✕", running: "", skipped: "–", cancelled: "!", pending: "", queued: "", interrupted: "!", waiting: "?",
  };
  const color = {
    passed: "bg-emerald-500 text-emerald-950", failed: "bg-rose-500 text-rose-950",
    running: "bg-sky-500 animate-pulse", skipped: "bg-zinc-700 text-zinc-300",
    cancelled: "bg-amber-500 text-amber-950", interrupted: "bg-amber-500 text-amber-950",
    waiting: "bg-amber-400 text-amber-950",
    pending: "bg-zinc-800 ring-1 ring-zinc-600", queued: "bg-zinc-800 ring-1 ring-zinc-600",
  }[status] || "bg-zinc-700";
  return (
    <span className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${color}`}>
      {map[status]}
    </span>
  );
}

export function Button({ variant = "default", className = "", ...props }) {
  const v = {
    default: "bg-zinc-800 hover:bg-zinc-700 text-zinc-100 ring-1 ring-zinc-700",
    primary: "bg-violet-600 hover:bg-violet-500 text-white",
    danger: "bg-rose-600/90 hover:bg-rose-500 text-white",
    ghost: "hover:bg-zinc-800 text-zinc-300",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-40 disabled:cursor-not-allowed ${v} ${className}`}
    />
  );
}

export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`rounded-xl border border-zinc-800 bg-zinc-900/60 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-zinc-200">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Progress({ value, className = "" }) {
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-zinc-800 ${className}`}>
      <div className="h-full rounded-full bg-violet-500 transition-all" style={{ width: `${Math.min(100, value || 0)}%` }} />
    </div>
  );
}

export function Input(props) {
  return (
    <input
      {...props}
      className={`rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none ${props.className || ""}`}
    />
  );
}

export function Select({ children, ...props }) {
  return (
    <select
      {...props}
      className={`rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-sm text-zinc-100 focus:border-violet-500 focus:outline-none ${props.className || ""}`}
    >
      {children}
    </select>
  );
}

export function ErrorBox({ children }) {
  if (!children) return null;
  return <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{children}</div>;
}

export function Empty({ children }) {
  return <div className="py-10 text-center text-sm text-zinc-500">{children}</div>;
}
