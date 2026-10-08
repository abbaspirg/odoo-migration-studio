import React from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useFetch, useSocketStatus } from "./api.js";
import Sources from "./pages/Sources.jsx";
import Migrate from "./pages/Migrate.jsx";
import JobDashboard from "./pages/JobDashboard.jsx";
import History from "./pages/History.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  ["/sources", "Versions & Sources"],
  ["/migrate", "Migration"],
  ["/history", "Jobs"],
  ["/settings", "Settings"],
];

function Banners({ status, reload }) {
  if (!status) return null;
  const { claude, postgres } = status;
  const items = [];
  if (claude?.error) items.push(["Claude Code", claude.error]);
  if (postgres && !postgres.ok) items.push(["PostgreSQL", `${postgres.error} — fix it in Settings.`]);
  if (!status.rules_file) items.push(["Rules", "MIGRATION_RULES.md not found in the workspace."]);
  if (!items.length) return null;
  return (
    <div className="border-b border-rose-500/40 bg-rose-950/60 px-5 py-2 text-sm text-rose-100">
      {items.map(([k, v]) => (
        <div key={k} className="flex items-center gap-2">
          <span className="font-semibold">{k}:</span> <span>{v}</span>
        </div>
      ))}
      <button className="mt-1 text-xs underline text-rose-300" onClick={() => reload()}>Re-check</button>
    </div>
  );
}

export default function App() {
  const [status, reloadStatus] = useFetch("/api/status");
  const wsOk = useSocketStatus();
  const recheck = () => fetch("/api/status?refresh=true").then(() => reloadStatus());
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-6 border-b border-zinc-800 bg-zinc-950 px-5 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-600 text-sm font-bold text-white">O</div>
          <span className="font-semibold text-zinc-100">Odoo Migration Studio</span>
        </div>
        <nav className="flex gap-1">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to}
              className={({ isActive }) => `rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-zinc-200"}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-4 text-xs text-zinc-500">
          {status?.claude && (
            <NavLink to="/settings" title={`Claude Code ${status.claude.version || ""} — migrations run on this account. Switch with: claude auth login`}
              className={`flex items-center gap-2 rounded-full px-3 py-1 ring-1 ${status.claude.logged_in ? "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30 hover:bg-emerald-500/20" : "bg-rose-500/10 text-rose-200 ring-rose-500/40"}`}>
              <span className="text-[10px] uppercase tracking-wide text-zinc-400">Claude</span>
              <span className="font-medium">{status.claude.logged_in ? (status.claude.email || "logged in") : "not logged in"}</span>
              {status.claude.subscription && <span className="rounded bg-zinc-800 px-1.5 text-[10px] uppercase text-zinc-300">{status.claude.subscription}</span>}
            </NavLink>
          )}
          {status?.postgres && (
            <span><span className={status.postgres.ok ? "text-emerald-400" : "text-rose-400"}>●</span> Postgres</span>
          )}
          <span><span className={wsOk ? "text-emerald-400" : "text-amber-400"}>●</span> live</span>
        </div>
      </header>
      <Banners status={status} reload={recheck} />
      <main className="min-h-0 flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/migrate" replace />} />
          <Route path="/sources" element={<Sources status={status} />} />
          <Route path="/migrate" element={<Migrate status={status} />} />
          <Route path="/jobs/:jobId" element={<JobDashboard />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings onSaved={recheck} />} />
        </Routes>
      </main>
    </div>
  );
}
