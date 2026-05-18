# Analyses

## VIN-10 Incremental Wiki Compile/Index Pipeline

### Changed files
- `backend/routers/wiki.py`
- `backend/tests/test_wiki_router_incremental.py`

### What changed
- Added explicit ID-based incremental entrypoint: `POST /api/wiki/recompile/by_ids`.
- Refactored incremental compile into shared `_run_incremental_recompile(...)` used by both `recompile/dirty` and `recompile/by_ids`.
- Integrated dirty incremental runs with `compile_state` lifecycle (`_try_acquire` / `_set_current` / `_tick` / `_finish`) to avoid parallel compile races and to keep failure state consistent.
- Kept per-item fault tolerance: any paper/concept compile error is recorded and does not block other targets.
- Preserved single-task freshness guarantee: after incremental compile, the flow still runs `wiki_index.refresh_index()` and `wiki_search.rebuild_index()`.
- Extended router tests with `test_recompile_by_ids_targets_only_explicit_ids` to validate explicit-ID mode and post-compile refresh hooks.

### Commands run
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m compileall .`
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m pytest -q`
- `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`

### Test / eval result
- Workspace backend tests: pass (`72 passed, 1 warning`)
- Issue-specified eval command path: pass (`61 passed, 1 warning`)

### Metric delta (qualitative)
- Before: dirty incremental runs did not occupy `compile_state`, so concurrent compile requests could overlap and explicit-ID batch entrypoint was missing.
- After: incremental runs are serialized with compile-state locking, explicit `paper_ids`/`concept_ids` batch compile is supported, and index/search refresh remain in the same request.
- Expected impact: lower and more predictable latency for single/batch page updates, with Ask-visible freshness in one task.

### Risks / follow-ups
- `failed_items` remains in-memory only and clears on process restart.
- `recompile/by_ids` currently returns skipped/not-found items in response but does not persist per-item history beyond runtime state.

## VIN-12 Frontend Observability Improvements

### Changed files
- frontend/src/components/TaskNotice.tsx
- frontend/src/components/PaperProcessBadge.tsx
- frontend/src/pages/GraphPage.tsx
- frontend/src/pages/PapersPage.tsx
- frontend/src/pages/ReviewPage.tsx
- frontend/src/pages/SettingsPage.tsx
- frontend/src/components/AskDrawer.tsx
- artifacts/reviews/VIN-12/impact_report.json
- artifacts/reviews/VIN-12/impact_report.md
- artifacts/reviews/VIN-12/build_output.txt

### Commands run
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-12/frontend && npm --version && npm run build`

### Test / eval result
- Build command passed in issue worktree frontend path: `/Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-12/frontend`
- Result: `tsc -b && vite build` success.
- Notes: Vite emitted existing chunk-size warning (>500kB), no compilation/type errors.

### Metric delta (qualitative)
- Papers/Review now expose structured paper lifecycle stages (`待处理 / 处理中 / 已处理 / 失败`) with explicit stage summaries.
- Papers and Review surface recent failure summaries in page context, reducing first-pass troubleshooting cost.
- Graph/Settings/Ask key operations now use a unified non-blocking notice pattern with success/failure detail and retry CTA.

### Risks and follow-up
- `status.current === filename` is still used for per-paper `处理中` inference; if backend status wording changes, running-stage mapping may drift.
- Current metric delta is qualitative only; recommend adding telemetry counters (`retry` click-through, failure recovery time) to quantify reduction in manual troubleshooting time.

## VIN-8 Backend Pipeline Observability

### Changed files
- `backend/models.py`
- `backend/database.py`
- `backend/config.py`
- `backend/routers/papers.py`
- `backend/services/scanner_service.py`
- `backend/services/paper_record_service.py`
- `backend/services/paper_pipeline_service.py` (new)
- `backend/tests/test_paper_pipeline_service.py` (new)

### What changed
- Added pipeline status persistence on `Paper`:
  - `processing_status` (`scanning/extracting/parsing/graphing/failed/done`)
  - `retry_count`
  - `last_error_stage`
  - `last_error_reason`
  - `last_error_recoverable`
- Added SQLite migration/backfill for new columns (idempotent).
- Implemented recoverable vs non-recoverable error classification and exponential backoff retry helper.
- Refactored paper processing main chain to:
  - stage transitions with DB commits for observability
  - bounded retry with exponential backoff for recoverable failures
  - final failure classification + reason/stage persistence
  - keep per-paper isolation so single-paper failure does not stop batch loop
- Extended batch runtime status payload with:
  - `succeeded`
  - `failed_papers` list
  - `max_retries`
- Extended batch/retry API responses to include runtime stats payload and failed-paper list.
- Synced new fields into markdown paper records.
- Added unit tests for retry/backoff/error-classification helpers.

### Commands run
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-8/target/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-8/target/knowledge-tree-v2/backend && python3 tests/test_paper_pipeline_service.py`
- Also executed issue-provided command path for parity check:
  - `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`

### Validation result
- `compileall`: passed.
- `pytest`: blocked by environment (`No module named pytest`).
- Additional unit validation: `tests/test_paper_pipeline_service.py` passed (`Ran 4 tests`).
- Existing broader tests in this environment are additionally blocked by missing runtime dependency `pypdf` when importing PDF service.

### Metric delta (qualitative)
- Before: extraction chain failure handling was mostly terminal per paper with coarse `processed/error` signals.
- After: recoverable failures now retry with exponential backoff; each paper records stage + failure reason + recoverability; batch status surfaces failed-paper list and success/failure counters while continuing remaining papers.

### Risks and follow-ups
- Current recoverable classification is keyword/status-code based; tune with production error telemetry to reduce false positives/negatives.
- Stage commits increase DB writes slightly during processing.
- Recommend adding API-level tests for `/api/status`, `/api/process`, and `/api/papers/retry_failed` payload shape and pipeline state transitions.

## VIN-11 Analysis

### Scope
- Implementation target is this `knowledge-tree-v2` workspace on branch `agent/VIN-11`.

### Changed Files
- `backend/services/ask_agent.py`
- `backend/routers/ask.py`
- `backend/tests/test_ask_agent.py`
- `backend/tests/test_ask_router.py`
- `backend/tests/test_ask_synthesis.py`
- `frontend/src/api/client.ts`
- `frontend/src/components/AskDrawer.tsx`

### What Changed
- Added structured Ask citations (`citations`) while preserving existing `cited_files` contract.
- Added Ask session passthrough (`session_id`) in request/response for trace continuity.
- Exposed citations in Ask UI as explicit source list (not only count).
- Extended synthesis payload with traceability fields:
  - `source_session_id`, `source_session_title`, `source_turn_indexes`, `source_cited_files`
- Persisted those traceability fields into concept page frontmatter.
- Strengthened synthesis dedupe by including model aliases in deterministic duplicate checks; still supports `force_create` override.

### Commands Run
- `python3 -m pytest -q backend/tests/test_ask_agent.py backend/tests/test_ask_router.py backend/tests/test_ask_synthesis.py`
- `cd backend && python3 -m compileall . && python3 -m pytest -q || true`
- `cd frontend && npm ci`
- `cd frontend && npm run build`

### Validation Result
- Backend targeted tests: pass (`11 passed`).
- Backend full suite via issue eval command form: pass (`58 passed`).
- Frontend build: pass (`tsc -b` + `vite build`).

### Metric Delta (Qualitative)
- Duplicate concept creation risk reduced via alias-aware deterministic dedupe plus existing model duplicate judgement.
- Concept pages now include session/turn/source citation metadata, improving post-hoc provenance and backfill traceability.
- Ask responses now present concrete citation sources in UI, improving source traceability for users before synthesis.

### Risks / Follow-up
- `session_id` is currently client-provided and local-session based; if server-side session storage is introduced later, map this ID to persisted session records.
- Citation granularity is currently file-level provenance (plus paper refs), not paragraph offsets; add paragraph anchors if stricter auditability is needed.
