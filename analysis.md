# Analyses

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
