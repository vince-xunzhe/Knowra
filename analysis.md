# VIN-10 Analysis

## Changed files
- `backend/routers/wiki.py`
- `backend/tests/test_wiki_router_incremental.py`

## What changed
- Added explicit ID-based incremental entrypoint: `POST /api/wiki/recompile/by_ids`.
- Refactored incremental compile into shared `_run_incremental_recompile(...)` used by both `recompile/dirty` and `recompile/by_ids`.
- Integrated dirty incremental runs with `compile_state` lifecycle (`_try_acquire` / `_set_current` / `_tick` / `_finish`) to avoid parallel compile races and to keep failure state consistent.
- Kept per-item fault tolerance: any paper/concept compile error is recorded and does not block other targets.
- Preserved single-task freshness guarantee: after incremental compile, the flow still runs `wiki_index.refresh_index()` and `wiki_search.rebuild_index()`.
- Extended router tests with `test_recompile_by_ids_targets_only_explicit_ids` to validate explicit-ID mode and post-compile refresh hooks.

## Commands run
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m compileall .`
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m pytest -q`
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m pytest -q tests/test_wiki_router_incremental.py tests/test_wiki_index_incremental.py`
- `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`

## Test / eval result
- Workspace full tests: pass (`70 passed, 1 warning`)
- Issue-specified eval command path: pass (`61 passed, 1 warning`)

## Metric delta (qualitative)
- Before: dirty incremental runs did not occupy `compile_state`, so concurrent compile requests could overlap and explicit-ID batch entrypoint was missing.
- After: incremental runs are serialized with compile-state locking, explicit `paper_ids`/`concept_ids` batch compile is supported, and index/search refresh remain in the same request.
- Expected impact: lower and more predictable latency for single/batch page updates, with Ask-visible freshness in one task.

## Risks / follow-ups
- `failed_items` remains in-memory only and clears on process restart.
- `recompile/by_ids` currently returns skipped/not-found items in response but does not persist per-item history beyond runtime state.
