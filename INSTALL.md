# Install (native Web development)

[中文](INSTALL.zh.md) | [English](INSTALL.md)

Use a fresh worktree and branch for each iteration; follow [Development](docs/DEVELOPMENT.md). Never reuse another checkout's virtual environment, node_modules or research data.

## Requirements

- Python 3.12.13 or later **within 3.12.x** (`.python-version`).
- Node 24.14.1 or later **within 24.x**, with npm (`.node-version`).
- `uv` available on PATH; the checked-in Python lock was generated with uv 0.11.14.

These are the supported native Web baseline, not mobile/container requirements. An API key is not needed to install or run the offline checks.

## Install explicitly

```bash
./scripts/install-native.sh
```

The installer uses an existing successful installation record, creates `backend/.venv` in a fresh checkout, or creates `backend/.venv-native-py312` beside an unrecorded/legacy environment. It never deletes or moves the old environment or reuses an unrecorded `backend/.venv-py312`. It installs hash-checked official PyPI wheels from `backend/requirements-dev.lock`, and runs `npm ci --ignore-scripts` against the official npm registry. Tool caches and empty npm config files stay in `.cache/`. It does not start services, migrate a database or change system permissions/signatures. The installer finds an already installed pinned Python through `python3.12` or `uv`; it never downloads a runtime. Set `PYTHON_BIN=/absolute/path/to/python3.12` to choose an existing supported interpreter. The system bootstrap Python may be older; the application still requires Python 3.12.13+ within 3.12.x. If PATH selects another Node major, prefix each command with `NODE_BIN=/absolute/path/to/node24/bin/node`; no global setting is changed.

A failed install remains marked `installing`, and startup refuses it. Rerunning the installer repairs its own incomplete environment and finishes the locked installation. An unrelated existing native target is never overwritten.

## Validate before starting

```bash
./scripts/check.sh
./start.sh --check
```

The first command runs the startup-entry regression fixtures, isolated backend subset, polling unit tests, TypeScript/Vite build, full ESLint (zero warnings) and mock browser regressions against real React components. It requires an installed Chrome/Chromium; it never downloads a browser. Every failure or missing prerequisite returns nonzero. The browser uses a temporary profile and a random loopback port, with mocked APIs and external requests blocked. PostgreSQL, real providers and the existing application E2E suite are not run. The second command is a read-only launch preflight. See [Development](docs/DEVELOPMENT.md) for scope and browser configuration.

## Start this worktree

```bash
BACKEND_PORT=8103 FRONTEND_PORT=5183 ./start.sh
```

Choose distinct free ports for each concurrent worktree. Startup uses installed dependencies only; it no longer installs packages, selects Docker automatically, starts Colima, re-signs modules or opens a browser. `KNOWLEDGE_WIKI_MODE=native` is optional; old `auto`/`docker` values are rejected with an explanation. Existing Docker files are unchanged, and container operations remain a separate explicit workflow.

For daily use, run `./start.sh` normally in your terminal. With existing SQLite files, it displays this checkout's data directory and startup effects, then asks you to type `START`. Enter, any other answer, EOF or interruption cancels without opening the database or starting services. The confirmation is for this launch only; no environment switch or saved permission is needed. Non-interactive launches fail closed unless you explicitly provide `KNOWRA_ALLOW_EXISTING_DATA=1` for an already approved operational run; piping `START` is not accepted.

Starting the API reads its local application configuration and can update schemas/backfills, remove legacy finding nodes, merge/repair records and edges, update paper records, rebuild the wiki index, and resume background/recurring tasks that may call external services. An initialized database is not proof that startup is read-only. Shared data symlinks and external task-queue paths are rejected. Stopping API/Web does not stop independent workers; read [worker operations](docs/TASK-WORKERS.md).

If you need a backup before accepting, cancel first. Choose a protected destination outside the repository; no destination is preselected and the launcher does not make a backup. Follow the [backup checklist](docs/DEVELOPMENT.md#数据备份待用户决定不自动执行), covering the knowledge/task/recommendation databases, paper records and wiki content. Coordinate API and independent-worker shutdown or a consistent SQLite backup; do not copy an active `.db` alone and lose its WAL. Schema compatibility and maintenance effects should be checked on a consistent disposable backup before approving changes to valuable existing data; `--check` does not inspect database schemas or certify that no migration is needed.

Credentials may be configured later in the application's Settings, within an authorized running session. The launcher does not load root `.env` by default. To use this worktree's local file explicitly, set `KNOWRA_LOAD_ENV=1`; Uvicorn parses it as environment data, not shell code. Never commit real credentials. Backend reload remains opt-in with `BACKEND_RELOAD=1`.

## Backup and delivery

[Development](docs/DEVELOPMENT.md) records the backup decisions still required from the user. No automatic backup, database copy or two-machine synchronization is included. After implementation, validation, review fixes and conflict checks, deliver a non-Draft PR only with that round's publication authorization; the user merges.

## Existing main checkout after upgrading

Merging this fix changes code, not ignored local environments. In your daily checkout, run `./scripts/install-native.sh` once and then `./start.sh --check`. With a legacy Python 3.9 `backend/.venv`, installation preserves it and records a separate Python 3.12 environment; subsequent `./start.sh` uses that record without activation or automatic installation. No worktree environment is linked into main.

If preflight reports existing databases, it has only checked their presence. `./start.sh --check` still succeeds when the environment, paths and ports are valid: it does not open databases or start API/Web/worker processes, and needs no existing-data permission. To start, run `./start.sh` in your terminal and review its confirmation. This operational permission is separate from preparing Python or checking the environment. Credentials and research data are not touched by installation. A passing repair-worktree check does not update your daily checkout: after the corresponding PR is merged, update its main code before using the repaired launcher behavior.
