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
PYTHON_BIN=python3.12 ./scripts/install-native.sh
```

The installer creates this worktree's `backend/.venv`, installs hash-checked official PyPI wheels from `backend/requirements-dev.lock`, and runs `npm ci --ignore-scripts` against the official npm registry. Tool caches and empty npm config files stay in `.cache/`. It does not start services, migrate a database or change system permissions/signatures. No existing environment is replaced with an incompatible Python version.

## Validate before starting

```bash
./scripts/check.sh
./start.sh --check
```

The first command runs the isolated backend subset, polling unit tests, TypeScript/Vite build, full ESLint (zero warnings) and mock browser regressions against real React components. It requires an installed Chrome/Chromium; it never downloads a browser. Every failure or missing prerequisite returns nonzero. The browser uses a temporary profile and a random loopback port, with mocked APIs and external requests blocked. PostgreSQL, real providers and the existing application E2E suite are not run. The second command is a read-only launch preflight. See [Development](docs/DEVELOPMENT.md) for scope and browser configuration.

## Start this worktree

```bash
BACKEND_PORT=8103 FRONTEND_PORT=5183 ./start.sh
```

Choose distinct free ports for each concurrent worktree. Startup uses installed dependencies only; it no longer installs packages, selects Docker automatically, starts Colima, re-signs modules or opens a browser. `KNOWLEDGE_WIKI_MODE=native` is optional; old `auto`/`docker` values are rejected with an explanation. Existing Docker files are unchanged, and container operations remain a separate explicit workflow.

The API may initialize/migrate **this worktree's** `data/` and launch the existing independent worker. Existing SQLite files require explicit `KNOWRA_ALLOW_EXISTING_DATA=1` after reviewing backup/migration implications. Shared data symlinks and external task-queue paths are rejected. Stopping API/Web does not stop an independent worker; read [worker operations](docs/TASK-WORKERS.md).

Credentials may be configured later in the application's Settings, within an authorized running session. The launcher does not load root `.env` by default. To use this worktree's local file explicitly, set `KNOWRA_LOAD_ENV=1`; Uvicorn parses it as environment data, not shell code. Never commit real credentials. Backend reload remains opt-in with `BACKEND_RELOAD=1`.

## Backup and delivery

[Development](docs/DEVELOPMENT.md) records the backup decisions still required from the user. No automatic backup, database copy or two-machine synchronization is included. After implementation, validation, review fixes and conflict checks, deliver a non-Draft PR only with that round's publication authorization; the user merges.
