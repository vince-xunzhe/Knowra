# VIN-10 Analysis

## Changed files
- `backend/routers/papers.py`
- `backend/routers/wiki.py`
- `backend/services/wiki_index.py`
- `backend/tests/test_wiki_index_incremental.py`

## What changed
- Added deterministic incremental index refresh (`wiki_index.refresh_index`) so `index.md` can be updated without full LLM rebuild after wiki page changes.
- Added source digest tracking in `index.md` frontmatter and `index_summary` to mark stale index content robustly.
- Added single-item wiki recompile entry points and retry support:
  - `POST /api/wiki/papers/{paper_id}/recompile`
  - `POST /api/wiki/concepts/{concept_id}/recompile`
  - `POST /api/wiki/retry_failed_item` (`kind=paper|concept`)
- Added compile failure recording (`compile_state.failed_items`) so one page failure can be retried independently without stopping other outputs.
- Wired post-compile incremental refresh + search reindex so Ask can see latest titles/summaries in one task cycle.

## Commands run
- `cd backend && python3 -m compileall .`
- `cd backend && python3 -m pytest -q`
- `cd backend && python3 -m compileall . && python3 -m pytest -q || true`

## Test / eval result
- `cd backend && python3 -m compileall .`: pass
- `cd backend && python3 -m pytest -q`: pass (`67 passed, 1 warning`)
- Issue eval shape (`cd backend && python3 -m compileall . && python3 -m pytest -q || true`): pass

## Metric delta (qualitative)
- Before: new/updated wiki pages could require full index rebuild before Ask reliably saw latest entries.
- After: per-item or batch compile paths refresh `index.md` incrementally and rebuild search index immediately.
- Expected impact: lower end-to-end time from page update to Ask-visible retrieval.

## Risks / follow-ups
- `compile_state.failed_items` is in-memory and resets on backend restart.
- Incremental index body favors deterministic freshness over LLM editorial quality; full rebuild endpoint remains available.
