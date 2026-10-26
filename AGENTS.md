# AGENTS.md

Knowra is a local-first paper research workspace: Python/FastAPI, React/TypeScript/Vite, shared `model_gateway/`, and an optional mobile app. Follow the current user's scope; a read-only task does not authorize changes, installs, execution or publication.

## Worktree and data boundaries

- Every development iteration starts from freshly fetched `origin/main` in a **new worktree and branch**. Check status first; preserve the original checkout, previous branches/worktrees and all user edits. Never reset, stash, clean or overwrite them to make a task easier. Register and report the worktree path and base SHA.
- Work only inside the assigned worktree. Each has its own managed Python environment (`backend/.venv` or `backend/.venv-native-py312`), `frontend/node_modules`, `.cache/`, `data/` and explicit API/Web ports. Never link or copy a live research database, paper/photo collection, another venv or another worktree's data into a development task.
- Read-only Git commands use `GIT_OPTIONAL_LOCKS=0` and `-c core.fsmonitor=false`. Redact credentials from remote URLs.
- Do not read secrets, real `.env*`, `data/config.json`, auth files or historical agent sessions. Keep databases, PDFs, wiki data, generated assets, credentials and environments out of commits. Public-repository candidates need a scoped privacy review.

## Native environment and execution

Use Python 3.12.13+ within 3.12.x and Node 24.14.1+ within 24.x; pinned baselines are in `.python-version` / `.node-version`. Current Web locks use Vite 8.0.9 and TypeScript 6.0.3. This does not change the optional mobile/container environment.

- Legacy Python environments are never deleted, moved or overwritten. Installation records bind the selected environment and lock hashes; a legacy/unmanaged canonical environment gets a separate `backend/.venv-native-py312`. Installation and startup use the same selection, including on a daily `main` checkout.
- `./scripts/install-native.sh` explicitly installs this worktree's wheel-only, hashed Python lock and official npm lock with lifecycle scripts disabled. It clears inherited package-manager configuration and keeps caches local. Review dependency sources/scripts before changing this policy; do not modify global config, native signatures or quarantine settings.
- `./start.sh --check` checks installed versions, lock markers, data boundaries and ports without loading credentials or importing the app. Existing databases produce a notice, not a request for write permission; the existing-data opt-in applies only to actually starting services.
- `./start.sh` is an operational action: it starts installed native services, defaults to API 8103 / Web 5183, and never auto-installs packages or starts Docker/Colima. Assign another pair for concurrent worktrees. Root `.env` loading requires `KNOWRA_LOAD_ENV=1`; existing databases require `KNOWRA_ALLOW_EXISTING_DATA=1` after reviewing backup/migration implications.
- API startup can apply database schema changes/backfills and start an independent worker. Existing worker semantics are preserved. Stopping API/Web does **not** stop an independent worker; follow `docs/TASK-WORKERS.md`. Do not launch the app to verify documentation.

## Validation

`./scripts/check.sh` first runs `scripts/test_native_startup.py` against disposable main-like fixtures (real shell entrypoints, mock services), then runs the maintained isolated backend subset, polling unit tests, TypeScript/Vite build, full ESLint with zero warnings allowed, and mock browser regressions against real React components. Any failure or missing browser returns nonzero; a skipped check is not a pass. Python and Web checks operate on source snapshots under this worktree's `.cache/`, with credentials/data excluded and network guards. The browser harness uses a temporary profile and a random loopback port, with all API responses mocked and external requests blocked; it does not start the application backend. See `docs/DEVELOPMENT.md` for the exact scope; this is not a full application test suite.

For native startup changes, explicitly run `./scripts/check.sh --real-start` to add real API/Vite/browser verification after the ordinary checks pass. It uses a disposable source/data snapshot, blocks external providers and keeps task execution disabled; it must verify normal process/port cleanup. This does not authorize using daily research data or executing queued work.

Never automatically run unrestricted backend discovery, existing browser E2E, real provider/LLM/market calls, migrations or deployment. PostgreSQL RLS tests are destructive and require an additional explicit opt-in for a disposable loopback test instance; setting a database URL alone is insufficient. The default check does not execute them. Report old lint/test debt separately; do not broaden infrastructure work into business rewrites merely to get green checks.

## Delivery and backup

Before each iteration's commit, update `DEVELOPMENT_LOG.md` with one concise newest-first entry: date/branch, problem/result, PR link (or not yet created), and last verified status. Keep detailed validation and failure-resolution evidence in the PR. At the next iteration's start, verify the previous merge and refresh its short SHA/date in that iteration's normal PR, never by editing main or creating status-only PR loops. GitHub links remain the live source of truth.

Finish implementation, relevant validation, complete diff review, related fixes and conflict checks before delivery. Publication requires authorization for that iteration. Publish a dedicated branch and deliver a **non-Draft, ready-for-review PR**; if validation is incomplete, report it as blocked rather than calling it merge-ready. The user merges. No direct main push, force push, automatic merge or unapproved deployment.

Report changed files, environment paths/versions, checks actually run, failures, omitted checks and remaining decisions. Backup planning is separate from Git: list data, exclusions and restore verification, but wait for the user to choose a target and approve actual copying/synchronization. See `docs/DEVELOPMENT.md`. CI, deployment, mobile builds and old-worktree cleanup are outside the current native baseline.
