#!/usr/bin/env bash
# Odoo Migration Studio: backend (FastAPI :8765) + frontend (Vite :5173), both on 127.0.0.1 only.
#   ./start.sh            dev mode: backend + Vite dev server (hot reload)
#   ./start.sh --prod     build the frontend once and serve everything from the backend on :8765
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export MS_BACKEND_PORT="${MS_BACKEND_PORT:-8765}"
export MS_FRONTEND_PORT="${MS_FRONTEND_PORT:-5173}"

if [ ! -x .venv/bin/python ]; then
  echo "→ creating backend venv"
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, multipart, psycopg2, lxml" 2>/dev/null; then
  echo "→ installing backend requirements"
  .venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  echo "→ installing frontend packages"
  (cd frontend && npm install --no-audit --no-fund)
fi
command -v claude >/dev/null || echo "WARNING: 'claude' CLI not found on PATH — migrations will be disabled."

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null; }
trap cleanup EXIT INT TERM

if [ "${1:-}" = "--prod" ]; then
  (cd frontend && npx vite build)
  echo "→ Odoo Migration Studio: http://127.0.0.1:${MS_BACKEND_PORT}"
  cd backend && exec ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$MS_BACKEND_PORT"
fi

(cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$MS_BACKEND_PORT") &
pids+=($!)
(cd frontend && npx vite --host 127.0.0.1 --port "$MS_FRONTEND_PORT" --strictPort) &
pids+=($!)
echo "→ Odoo Migration Studio: http://127.0.0.1:${MS_FRONTEND_PORT}  (API on :${MS_BACKEND_PORT})"
wait -n
