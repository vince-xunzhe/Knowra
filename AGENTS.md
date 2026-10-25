# AGENTS.md

## Project and scope

Knowra is a local-first paper research workspace: a Python/FastAPI backend, a React/TypeScript/Vite frontend, a shared `model_gateway/`, and an optional Expo mobile app. Research data lives under `data/`; the versioned roadmap skill lives under `skills/shitu-roadmap/`.

Follow the user's task scope. A read-only task does not authorize starting services, installing dependencies, migrations, model calls, Git commits, pushes, issue creation, or pull requests. Documentation edits do not authorize service or data operations; deliver authorized changes using the workflow below.

## Protect local state

- Do not read or change `.env*`, credentials, auth files, historical agent sessions, or `data/config.json` unless the task explicitly authorizes that access. Never include their values in output or commits.
- Preserve research databases, PDFs, wiki content, generated assets and backups. Do not change deployment configuration unless requested.
- Check Git status before edits and preserve unrelated changes. Read-only Git checks should use `GIT_OPTIONAL_LOCKS=0` and disable `core.fsmonitor`.
- Use an isolated checkout/worktree and separate local data for executable checks. Do not point a development worktree at a live research database. Keep machine-specific environments and credentials out of Git.

## Runtime and startup

See `INSTALL.md` / `INSTALL.zh.md` and `docs/DEVELOPMENT.md` for existing commands. Runtime dependencies are in `backend/requirements.txt`; there is no root `requirements.txt`. Preserve the existing npm lockfiles.

The installation guides and `start.sh` currently accept Node 20 too broadly: locked Vite requires `^20.19.0 || >=22.12.0`, and locked mobile React Native requires `>=20.19.4`. These are dependency constraints, not a verified complete runtime matrix. The container files declare Python 3.12 and Node 24; native version pinning and stricter startup checks remain to be added.

`./start.sh` is an operational command: it reads local configuration, installs dependencies, and may build containers or start Colima. Native macOS startup may adjust attributes/signatures on native Node modules. API startup can apply database schema changes/backfills and start a task worker. Do not use startup as a documentation check.

The independent worker can survive API shutdown/restart. Stopping the API does not stop all work. Follow `docs/TASK-WORKERS.md` for worker shutdown, upgrades and recovery. Back up a live SQLite database with the SQLite backup API, not by copying its main file alone.

## Existing validation entrypoints

These are declared entrypoints, not a claim that checks pass in the current environment. Use them only within an authorized task and an isolated environment.

| Working directory | Command | Purpose |
| --- | --- | --- |
| `frontend/` | `npm run build` | TypeScript application check and Vite build |
| `frontend/` | `npm run lint` | ESLint |
| `frontend/` | `node --test tests/polling.test.mjs` | Polling unit tests; verify direct TypeScript import support in the chosen Node runtime |
| `frontend/` | `npm run test:e2e` | Playwright; unit/E2E collection boundaries and service isolation still need tightening |
| `mobile/` | `npm run tsc` | Mobile typecheck |

The documented backend fault-test subset, from the repository root, is:

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_task_queue.py backend/tests/test_task_executor.py backend/tests/test_paper_background_dispatch.py
```

`docs/TASK-WORKERS.md` describes temporary queues and fake handlers for this subset. A complete development/test dependency set and a unified full-suite offline command have not yet been established. Do not infer working pytest, Ruff or mypy setup from the old template.

PostgreSQL RLS tests are separate: they skip unless `KNOWRA_TEST_POSTGRES_URL` and the driver are available. When enabled, they reset schemas and tables. Use only a disposable test database; target validation and restricted-role assertions still need improvement.

## Handoff

Report changed files, the checks actually performed, checks not run, and remaining uncertainties. For documentation-only work, review text, references and the diff; state that builds, tests and services were not run. Do not treat a documented command or existing configuration as proof of successful execution.

## Change delivery

For each authorized development round, use a dedicated branch, review the upload scope, and open a draft pull request. Review the complete diff, fix issues related to that round, and resolve merge conflicts before asking the user to merge. Read-only checks do not automatically publish changes. Stay within the approved repositories and content scope; do not push directly to the main branch, force-push, or merge the PR yourself. Keep secrets, real configuration, user data, virtual environments, caches and build artifacts out of commits; leave suspicious private content local and report it.
