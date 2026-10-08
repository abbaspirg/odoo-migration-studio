import React from "react";

const KIND = {
  community: "text-sky-300 bg-sky-500/10 ring-sky-500/30",
  enterprise: "text-fuchsia-300 bg-fuchsia-500/10 ring-fuchsia-500/30",
  custom: "text-amber-300 bg-amber-500/10 ring-amber-500/30",
  unknown: "text-rose-300 bg-rose-500/10 ring-rose-500/30",
};

export function DepChips({ deps }) {
  return (
    <div className="flex flex-wrap gap-1">
      {deps.map((d) => (
        <span key={d.name} title={d.kind} className={`rounded px-1.5 py-0.5 text-[11px] ring-1 ring-inset ${KIND[d.kind]}`}>{d.name}</span>
      ))}
    </div>
  );
}

export default function ModuleTable({ modules, selected, onToggle }) {
  const selectable = !!onToggle;
  return (
    <div>
      <div className="mb-2 flex gap-3 text-[11px] text-zinc-500">
        {Object.keys(KIND).map((k) => <span key={k} className={`rounded px-1.5 ring-1 ring-inset ${KIND[k]}`}>{k}</span>)}
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-zinc-500">
          <tr className="border-b border-zinc-800">
            {selectable && <th className="w-8 py-2" />}
            <th className="py-2 pr-3">Module</th>
            <th className="py-2 pr-3">Version</th>
            <th className="py-2">Depends</th>
          </tr>
        </thead>
        <tbody>
          {modules.map((m) => (
            <tr key={m.name} className={`border-b border-zinc-800/70 ${selectable ? "cursor-pointer hover:bg-zinc-800/40" : ""}`}
              onClick={() => selectable && onToggle(m.name)}>
              {selectable && (
                <td className="py-2"><input type="checkbox" readOnly checked={selected.has(m.name)} className="accent-violet-500" /></td>
              )}
              <td className="py-2 pr-3">
                <div className="font-mono text-zinc-100">{m.name}</div>
                <div className="text-xs text-zinc-500">{m.title}</div>
                {m.error && <div className="text-xs text-rose-400">{m.error}</div>}
              </td>
              <td className="py-2 pr-3 font-mono text-xs text-zinc-400">{m.version}</td>
              <td className="py-2"><DepChips deps={m.depends} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
