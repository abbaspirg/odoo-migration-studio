# Odoo Migration Studio

A local web app that migrates Odoo custom modules between versions (16→17 … 19→20). It uses
[Claude Code](https://claude.com/claude-code) in headless mode (`claude -p`) as the migration
engine, and then **installs and tests** the result in a real Odoo before you trust it.

- **Your own Claude account.** The studio drives the `claude` CLI installed on your machine, so
  every migration runs on the Claude account *you* are logged into. Usage counts against your
  plan. The studio never asks for, stores or sees credentials, and `ANTHROPIC_API_KEY` is removed
  from the subprocess environment so a stray key is never billed by accident. To use an API key
  instead, opt in with `MS_ALLOW_API_KEY=1` (see [Using an API key](#using-an-api-key)).
- **Runs locally.** Everything binds to `127.0.0.1`. Your modules, Odoo sources and test
  databases stay on your machine. The one exception: Claude Code sends the code it reads to
  Anthropic under your account, just as when you use Claude Code by hand.
- **Verifies, not just rewrites.** Each module is installed into a fresh database and its tests
  are run. Failures go back to Claude to fix, up to 3 attempts by default. Then you can click
  through it yourself on a real Odoo server.

## How it works

For each module the backend runs a 9-step pipeline, streamed live to the browser:

| # | Step | What happens |
|---|------|--------------|
| 1 | Analyze | Regex scan of Python/XML/JS/SCSS/manifest for known deprecations (`backend/app/analyzer.py`) |
| 2 | Copy | `<modules folder>/<m>` → `<workspace>/v<target>-migrated/<m>`. The source is never modified, and its content hash is checked after every Claude run. A `CLAUDE.md` with the paths of the Odoo source trees is added for the session |
| 3 | Claude migration | `claude -p … --output-format stream-json --permission-mode acceptEdits --allowedTools "Read,Edit,Write,Glob,Grep,Bash(python:*),Bash(grep:*)" --append-system-prompt <rules> --max-turns 40`, in the module copy. Claude greps the real Odoo sources to confirm APIs |
| 4 | Static checks | Python compile (with the target Odoo venv's interpreter), XML well-formedness, manifest version is `<target>.x.y.z`, manifest data files exist |
| 5 | Install test | `odoo-bin -d mig_<module>_<ts> -i <module> --stop-after-init`, then checks the module state is `installed` and there are no ERROR lines |
| 6 | Unit tests | `odoo-bin -u <module> --test-enable --test-tags /<module>` |
| 7 | Auto-fix loop | If 4, 5 or 6 fails, the error log goes back to Claude (`--resume <session>`), and 4→6 are re-run on a fresh DB |
| 8 | Report | `<workspace>/migration-notes/<module>.md`: Claude's change list and TODOs, files changed, test results. Plus one line in `<workspace>/MIGRATION_SUMMARY.md` |
| 9 | Cleanup | Drops the test database, unless you keep it |

Odoo commands are run by the backend, never by Claude.

## Requirements

| Component | Notes |
|---|---|
| Claude Code | Install from https://claude.com/claude-code and sign in once with `claude` (or `claude auth login`). Any plan that includes Claude Code works. `claude auth status` must show `"loggedIn": true`. Or use an [API key](#using-an-api-key) |
| Python | 3.10+ for the studio (tested on 3.12). Odoo needs its own: 3.12+ for 19/20, 3.10–3.12 for 17/18, 3.8–3.11 for 16 |
| Node.js | 18+ |
| PostgreSQL | 13+, with a role that has `CREATEDB` and is **not** `postgres` (Odoo refuses to run as `postgres`) |
| git | To clone Odoo community sources |
| wkhtmltopdf | Optional, only if your module's tests render PDF reports (0.12.5/0.12.6 with patched Qt) |

On Ubuntu/Debian, this installs everything except Claude Code. The `-dev` libraries are needed
when the studio builds Odoo's Python requirements:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-dev build-essential \
     postgresql nodejs npm libpq-dev libldap2-dev libsasl2-dev libxml2-dev libxslt1-dev
python3 --version   # 3.12+ for Odoo 19/20
node -v             # 18+; older Ubuntu releases ship an older Node, so use nodesource or nvm
```

### Windows (WSL2)

The studio needs Linux: it uses Linux file locking and process groups, and `start.sh` is a
bash script. On Windows, run it inside WSL2:

1. In PowerShell as administrator: `wsl --install` (installs Ubuntu; restart if asked).
2. Open the **Ubuntu** app and follow the steps above and below as written. Install Claude Code
   inside WSL, not on the Windows side.
3. Open http://127.0.0.1:5173 in your normal Windows browser. WSL2 forwards the port.

Keep the studio, the Odoo sources and your modules in the Linux file system (for example
`~/odoo-migration-studio`), not under `/mnt/c/...`: Odoo and git are much slower on Windows drives.

## Quick start

```bash
git clone https://github.com/abbaspirg/odoo-migration-studio.git && cd odoo-migration-studio

# 1. a PostgreSQL role for the test databases (the studio only creates/drops databases named mig_*)
sudo -u postgres psql -c "CREATE ROLE odoo_studio LOGIN CREATEDB PASSWORD 'odoo_studio'"

# 2. sign in to Claude Code with your own account (once, in a normal terminal: it opens the browser)
#    or skip this and use an API key instead (see "Using an API key")
claude auth login
claude auth status    # must show "loggedIn": true

# 3. optional: point the studio at an existing folder (see "Workspace")
cp .env.example .env

# 4. start (creates .venv and installs npm packages on first run)
./start.sh            # → http://127.0.0.1:5173   (use ./start.sh --prod for a single port, :8765)
```

Then in the UI:

1. **Versions & Sources**:
   - Clone the Odoo community version(s) you need (`git clone --depth 1`, about 1.5 GB each).
   - Click **Create venv + install** to install Odoo's Python requirements.
   - Optionally drop an **Odoo Enterprise** zip onto the version's slot. You need your own
     Enterprise license; enterprise code is extracted locally and never published or served.
2. Put your modules in `<workspace>/custom-modules/`, or scan any folder, or upload a zip.
3. **Migration**: pick the source and target versions, tick the modules (dependencies are ordered
   automatically), then **Start migration**.
4. **Dashboard**: watch the step timeline, Claude's messages and edits, the Odoo log, a
   side-by-side diff and the report. When a module is done, use **Manual test** to start a real
   Odoo with it installed (login `admin` / `admin`) and record your verdict.
   Found a problem? Describe it in the verdict box and click **Send to Claude to fix**. Claude
   resumes the migration session with your notes and the errors from that Odoo's log, the studio
   re-runs the checks, install and tests (with auto-fix), and if they pass, Odoo restarts on a
   fresh database so you can test again. Each round is added to the module's report.
   Claude also suggests a general rule. Edit it if needed and click **Add to migration rules**:
   it goes into `<workspace>/MIGRATION_RULES.md`, so later migrations avoid the same mistake.
5. **Download zip** gets the migrated modules and their reports. **Jobs** lists history and can
   re-run failed modules.

Check Settings first: Postgres credentials (default `odoo_studio` / `odoo_studio` on
`127.0.0.1:5432`), concurrency, fix attempts, and Claude options (max turns, allowed tools,
model).

### Stopping and updating

- **Stop:** Ctrl+C in the terminal running `./start.sh`.
- **Update:** `git pull` in the studio folder, then `./start.sh` again. If an update adds
  dependencies, install them with `.venv/bin/pip install -r backend/requirements.txt` and
  `(cd frontend && npm install)`.

## Workspace

Everything the studio reads and writes lives in one folder, `MS_WORKSPACE` (default
`./workspace`, git-ignored):

```
<workspace>/
  custom-modules/          modules to migrate (MS_CUSTOM_DIR), read-only for the studio
  v20-migrated/            output, one folder per target major version
  migration-notes/         per-module reports
  MIGRATION_SUMMARY.md     one line per module run
  logs/<job>/<module>/     Claude stream-json, prompts, odoo logs, event log
  sources/community/<ver>  cloned Odoo trees (or symlinks to existing ./odoo-<ver> trees)
  sources/enterprise/<ver> your extracted Enterprise addons
  sources/venvs/<ver>      Python venvs for each Odoo version
  MIGRATION_RULES.md       optional: your own rules (replaces the bundled ones)
  reference_module/        optional: an already-migrated module whose style Claude should copy
```

Already have Odoo checked out? A tree at `<workspace>/odoo-20.0/` (with `odoo-bin`) is picked up
automatically as community 20.0. An `enterprise-addons/` folder inside it is used as enterprise,
and `<workspace>/venv/` as its Python venv.

All variables are listed in `.env.example`.

### Using an API key

No Claude plan with Claude Code, or the account can't log in on this machine? Use an Anthropic API
key from https://console.anthropic.com instead. Usage is billed per token to that key's Console
account. Add both lines to `.env` (or export them) and restart the studio:

```bash
MS_ALLOW_API_KEY=1
ANTHROPIC_API_KEY=sk-ant-...
```

Claude Code uses the key even when an account is also logged in. The header badge then shows
"API key", and the dashboard shows the cost of each Claude run. Remove `MS_ALLOW_API_KEY` to go
back to the logged-in account. `.env` is git-ignored, so the key never ends up in the repo.

## Migration rules

`rules/MIGRATION_RULES.md` is appended to Claude's system prompt. It contains general migration
practice and a catalogue of API changes up to Odoo 20, each verified against the Odoo 20.0
source: `ir.access`, `t-esc`→`t-out`, the Interaction framework, OWL 3, and more. Add your house
rules (licensing, manifest style, per-module notes) as `<workspace>/MIGRATION_RULES.md`, and the
studio uses that file instead. Contributions of verified rules for other versions are welcome.

### Your house style: reference module and rules

Make every migration follow your own templates with two things in the workspace:

- **`<workspace>/MIGRATION_RULES.md`:** your rules. It *replaces* the bundled file, so start from
  a copy of `rules/MIGRATION_RULES.md` and add your own rules (licensing, manifest style, what to
  do with the app page), so you keep the verified API catalogue.
- **`<workspace>/reference_module/`:** one module already migrated the way you want (or set
  `MS_REFERENCE_MODULE` in `.env`). Claude copies its manifest keys, README and doc layout and
  license header.

Settings → Workspace shows which rules file and reference module are active. Both are read again
for every migration, so when your templates change, replace the folder or edit the file: the next
migration uses them, with no restart.

**The app page (`static/description/index.html`).** By default the studio keeps each module's
existing index layout and only updates versions, dates and version links. To restyle every index
to your reference module's design instead, add a rule like this to your rules file:

```markdown
- Restyle `static/description/index.html` to match `reference_module/static/description/index.html`
  (layout, fonts, inline styles). Keep all of the module's own content, screenshots and links.
  Copy any icons it uses from the reference's `static/description/assets/`.
```

**Modules you've already migrated** don't need a new migration. Open the module on the
dashboard, write what to change in the Manual test box (for example *"Restyle index.html to the
current reference_module layout, keep all content"*) and click **Send to Claude to fix**.

## Terminal mode

```bash
cd backend
../.venv/bin/python -m app.cli --from 19.0 --to 20.0 my_module other_module [--keep-db] [--overwrite] [-v]
```

Ctrl-C cancels cleanly. The report and cleanup still run.

## Safety

- **Local only.**
  - Servers bind to `127.0.0.1`, and requests with a non-local `Host` header get a 403.
  - **Do not expose the studio to a network.** It has no user accounts, it runs Claude with
    write access, and it executes the uploaded modules' Python through Odoo.
- **Read-only sources.**
  - The studio never writes into the modules folder, the Odoo trees or the reference module.
  - An existing output folder it didn't create is never replaced unless you tick *Overwrite*. The
    old folder is then moved to `data/backups/`, not deleted.
- **What Claude may do.** Only the allowed tools run without a prompt. Headless mode can't ask
  for approval, so anything else is refused. Two things are worth knowing:
  - `acceptEdits` also auto-approves simple file commands such as `rm` and `mv` inside the module
    copy.
  - `Bash(python:*)` lets Claude run arbitrary Python. Remove it in Settings if you prefer.
- **Your data.**
  - The job history is in `data/studio.db`.
  - Logs, reports and outputs are in the workspace.
  - Both are git-ignored, together with `.env`, `sources/` and zips.

## Layout

```
backend/app/   main.py (API + WebSocket), pipeline.py, claude_runner.py, odoo_runner.py, manual.py,
               analyzer.py, static_checks.py, sources.py, modules.py, reports.py, diffs.py, db.py,
               events.py, procs.py, config.py, cli.py
frontend/src/  React + Vite + Tailwind single-page app
rules/         bundled migration rules
start.sh       starts backend + frontend
```

## Limitations

- A migration is a strong first pass, not a guarantee. Review the diff and Claude's "Needs review"
  list, and use Manual test. Render-time breakages (QWeb pages, website routes) only show when
  exercised.
- Claude usage counts against your own plan. A small module takes a few minutes.
- Odoo 16–18 targets use the generic rules; the verified API catalogue currently covers changes up
  to 20.

## License

MIT; see `LICENSE`. This is an independent project, not affiliated with or endorsed by Odoo S.A.
or Anthropic. Odoo and Claude are trademarks of their respective owners.
