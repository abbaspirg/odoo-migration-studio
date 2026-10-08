import { useEffect, useRef, useState, useCallback } from "react";

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}

/* One shared WebSocket for the whole app, with auto-reconnect. */
const listeners = new Set();
let socket = null;
let connected = false;
const statusListeners = new Set();

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.onopen = () => { connected = true; statusListeners.forEach((f) => f(true)); };
  socket.onclose = () => {
    connected = false;
    statusListeners.forEach((f) => f(false));
    setTimeout(connect, 1500);
  };
  socket.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch { return; }
    listeners.forEach((f) => f(ev));
  };
}

export function useEvents(handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!socket) connect();
    const f = (ev) => ref.current(ev);
    listeners.add(f);
    return () => listeners.delete(f);
  }, []);
}

export function useSocketStatus() {
  const [ok, setOk] = useState(connected);
  useEffect(() => {
    if (!socket) connect();
    statusListeners.add(setOk);
    return () => statusListeners.delete(setOk);
  }, []);
  return ok;
}

export function useFetch(path, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const reload = useCallback(() => {
    if (!path) return Promise.resolve();
    return api(path).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [path]);
  useEffect(() => { reload(); }, [reload, ...deps]);
  return [data, reload, error, setData];
}

export const fmtDuration = (s) => {
  if (s == null) return "";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
};

export const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "");
