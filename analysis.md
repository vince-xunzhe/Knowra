# VIN-10 Analysis

## Changed Files
- `backend/services/wiki_index.py`
- `backend/routers/wiki.py`
- `backend/routers/papers.py`
- `backend/routers/graph.py`
- `backend/routers/promotion.py`
- `backend/routers/ask.py`
- `backend/tests/test_wiki_index_incremental.py`

## What Changed
- Added fast incremental `index.md` refresh (`wiki_index.refresh_index`) that builds index content from existing wiki pages without LLM calls.
- Added digest-based dirty tracking (`source_digest`) in `index.md` frontmatter and `index_summary`, so index staleness can be detected even when page counts do not change.
- Kept full LLM rebuild path (`/api/wiki/index/rebuild`) for explicit/manual full rewrite, now with digest metadata.
- Wired incremental index refresh into all relevant wiki mutation paths:
  - full paper/concept recompile background jobs
  - single paper/concept recompile endpoints
  - paper processing auto-compile pipeline
  - graph/promotion reconciliation
  - Ask synthesis concept writeback
- Added wiki compile failure recording (`compile_state.failed_items`) with item id/kind/error timestamp.
- Added single-item retry endpoint: `POST /api/wiki/retry_failed_item` (`kind=paper|concept`, `item_id=int`).
- Single-item recompile now also refreshes `index.md` + rebuilds wiki FTS index, ensuring Ask can see latest content in one task.

## Commands Run
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-10-kt2/backend && python3 -m compileall . && python3 -m pytest -q || true`
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-10-kt2/backend && /Users/vince/Documents/knowledge-tree-v2/backend/.venv/bin/python -m unittest discover -s tests -p 'test_wiki_index_incremental.py' -v`
- `cd /Users/vince/Documents/vince-studio-v2/workspaces/VIN-10-kt2/backend && /Users/vince/Documents/knowledge-tree-v2/backend/.venv/bin/python -m unittest discover -s tests -p 'test_wiki_compiler_reconcile.py' -v`

## Test / Eval Result
- `compileall`: pass.
- `python3 -m pytest -q`: not runnable in this environment (`No module named pytest`).
- Targeted unittest suites with project venv Python:
  - `test_wiki_index_incremental.py`: 2 passed.
  - `test_wiki_compiler_reconcile.py`: 3 passed.

## Metric Delta (Primary Goal)
- Before: incremental page compile did not guarantee `index.md` freshness; Ask could miss newest page title/summary unless manual full index rebuild.
- After: incremental page/concept updates now auto-refresh `index.md` locally (no LLM call) and refresh search index in the same flow.
- Expected impact: per-paper update path avoids full LLM index rebuild and reduces end-to-end “new page visible to Ask” latency to local file rewrite + FTS rebuild.

## Risks / Follow-up
- Incremental index body is deterministic template-based text (not LLM quality prose). Manual full rebuild endpoint remains available for richer editorial index content.
- `failed_items` is in-memory runtime state; backend restart clears history.
- Recommended follow-up: add API integration tests for `/api/wiki/papers/{id}/recompile` and `/api/wiki/retry_failed_item`.
