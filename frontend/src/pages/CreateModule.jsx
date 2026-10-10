import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, useFetch } from "../api.js";
import { Button, Card, ErrorBox, Input, Select } from "../components/ui.jsx";
import { AttachBar, useAttach } from "../components/Attachments.jsx";

const STEPS = [
  ["Plan", "Claude reads the Odoo source for that version and drafts a plan: dependencies, models, views, security and tests."],
  ["You review", "Edit the plan, or ask Claude to revise it. Nothing is written until you approve."],
  ["Build and verify", "Claude writes the module with tests, then the studio installs it in a fresh database, runs the tests and auto-fixes failures."],
];

export default function CreateModule() {
  const nav = useNavigate();
  const [options] = useFetch("/api/create/options");
  const [version, setVersion] = useState("");
  const [module, setModule] = useState("");
  const [description, setDescription] = useState("");
  const [depends, setDepends] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const att = useAttach();
  useEffect(() => { if (options?.length && !version) setVersion(options[options.length - 1].version); }, [options]);
  const opt = options?.find((o) => o.version === version);
  const nameOk = /^[a-z][a-z0-9_]{1,62}$/.test(module);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const job = await api("/api/create", { method: "POST", body: { module, version, description, depends_hint: depends, attachments: att.ids } });
      nav(`/jobs/${job.id}`);
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };

  if (options && !options.length) {
    return <div className="p-8 text-sm text-zinc-400">No Odoo version is ready. In <b>Versions &amp; Sources</b>, clone a version and create its venv first.</div>;
  }
  return (
    <form onSubmit={submit} className="mx-auto max-w-3xl space-y-5 p-6">
      <div>
        <h1 className="text-lg font-semibold text-zinc-100">New module</h1>
        <p className="mt-1 text-sm text-zinc-400">Describe a custom module and pick the Odoo version. Claude plans it from that version's source, you approve the plan, then it is built and tested like a migration.</p>
      </div>
      <ol className="grid gap-2 sm:grid-cols-3">
        {STEPS.map(([title, text], i) => (
          <li key={title} className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 text-xs text-zinc-400">
            <div className="mb-1 text-sm font-medium text-zinc-200"><span className="mr-1.5 text-violet-300">{i + 1}</span>{title}</div>{text}
          </li>
        ))}
      </ol>
      <Card title="Module">
        <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
          <label className="space-y-1 text-sm text-zinc-300">
            <div>Technical name</div>
            <Input id="new-module-name" className="w-full font-mono" value={module} placeholder="e.g. sale_delivery_slots"
              onChange={(e) => setModule(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} />
            {module && !nameOk && <div className="text-xs text-rose-300">Start with a letter; lowercase letters, digits and _ only.</div>}
          </label>
          <label className="space-y-1 text-sm text-zinc-300">
            <div>Odoo version</div>
            <Select id="new-module-version" className="w-full" value={version} onChange={(e) => setVersion(e.target.value)}>
              {options?.map((o) => <option key={o.version} value={o.version}>{o.version}</option>)}
            </Select>
          </label>
        </div>
        <label className="mt-4 block space-y-1 text-sm text-zinc-300">
          <div>What should it do?</div>
          <textarea id="new-module-description" rows={8} value={description} onChange={(e) => setDescription(e.target.value)} {...att.textareaProps}
            placeholder={"Who uses it, the screens and fields they need, the rules it enforces.\n\ne.g. Let salespeople book a delivery time slot on a quotation. Slots are defined per warehouse with a capacity. A slot is reserved when the order is confirmed and appears on the delivery order. Show a calendar of booked slots to inventory users."}
            className="w-full rounded-md border border-zinc-700 bg-zinc-950 p-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-violet-500 focus:outline-none" />
        </label>
        <AttachBar att={att} />
        <label className="mt-4 block space-y-1 text-sm text-zinc-300">
          <div>Dependencies you expect <span className="text-zinc-500">(optional)</span></div>
          <Input id="new-module-depends" className="w-full font-mono" value={depends} onChange={(e) => setDepends(e.target.value)} placeholder="e.g. sale_stock, website_sale" />
          <div className="text-xs text-zinc-500">Claude adds or drops dependencies to match the features{opt?.enterprise ? ", including enterprise modules when a feature needs them" : ""}. Each one is checked against Odoo {version || "…"}.</div>
        </label>
        <div className="mt-4 text-xs text-zinc-500">Output: <span className="font-mono">{opt?.output_dir}/{module || "<name>"}</span></div>
      </Card>
      {err && <ErrorBox>{err}</ErrorBox>}
      <div className="flex items-center gap-3">
        <Button variant="primary" type="submit" disabled={busy || att.uploading || !nameOk || !description.trim() || !version}>Draft the plan</Button>
        <span className="text-xs text-zinc-500">Planning is read-only and runs on your Claude account.</span>
      </div>
    </form>
  );
}
