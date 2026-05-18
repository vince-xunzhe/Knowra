# VIN-8 Analysis

## Changed files
- `backend/models.py`
- `backend/database.py`
- `backend/config.py`
- `backend/routers/papers.py`
- `backend/services/scanner_service.py`
- `backend/services/paper_record_service.py`
- `backend/services/paper_pipeline_service.py` (new)
- `backend/tests/test_paper_pipeline_service.py` (new)

## What changed
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

## Commands run
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-8/target/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-8/target/knowledge-tree-v2/backend && python3 tests/test_paper_pipeline_service.py`
- Also executed issue-provided command path for parity check:
  - `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`

## Validation result
- `compileall`: passed.
- `pytest`: blocked by environment (`No module named pytest`).
- Additional unit validation: `tests/test_paper_pipeline_service.py` passed (`Ran 4 tests`).
- Existing broader tests in this environment are additionally blocked by missing runtime dependency `pypdf` when importing PDF service.

## Metric delta (qualitative)
- Before: extraction chain failure handling was mostly terminal per paper with coarse `processed/error` signals.
- After: recoverable failures now retry with exponential backoff; each paper records stage + failure reason + recoverability; batch status surfaces failed-paper list and success/failure counters while continuing remaining papers.

## Risks and follow-ups
- Current recoverable classification is keyword/status-code based; tune with production error telemetry to reduce false positives/negatives.
- Stage commits increase DB writes slightly during processing.
- Recommend adding API-level tests for `/api/status`, `/api/process`, and `/api/papers/retry_failed` payload shape and pipeline state transitions.
