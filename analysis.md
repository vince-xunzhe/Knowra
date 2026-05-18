# VIN-10 Analysis

## Changed files
- `backend/routers/wiki.py`
- `backend/tests/test_wiki_router_incremental.py`

## What changed
- Added `POST /api/wiki/recompile/dirty` incremental entrypoint.
- `recompile/dirty` accepts freshness-based targeting (`include_missing` / `include_stale`) plus explicit `paper_ids` / `concept_ids`.
- Incremental compile loop is per-item tolerant: one paper/concept failure is recorded to `failed.items` and does not block other outputs.
- Incremental run returns `freshness_before` and `freshness_after`, then refreshes `index.md` and rebuilds wiki search index in the same task.
- Added router tests covering:
  - successful dirty compile path
  - partial failure + continue behavior

## Commands run
- `cd backend && python3 -m compileall . && python3 -m pytest -q || true`

## Test / eval result
- Issue eval command: pass (`69 passed, 1 warning`)

## Metric delta (qualitative)
- Before: incremental jobs relied on coarse full flows; freshness-driven targeted recompilation was missing.
- After: one request can recompile only dirty pages, update `index.md`, and rebuild search index without blocking on all pages.
- Expected impact: lower update latency from single paper/concept change to Ask-visible retrieval.

## Risks / follow-ups
- The endpoint currently checks `compile_state.running` but does not expose per-item progress in `/status`; follow-up can add explicit state transitions for dirty runs.
- `failed_items` remains in-memory only and clears on process restart.
