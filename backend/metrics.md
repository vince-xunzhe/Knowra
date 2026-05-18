# VIN-9 Metrics

## Changed files
- `backend/services/graph_service.py`
- `backend/routers/graph.py`
- `backend/tests/test_graph_curation.py`

## What changed
- Refactored similar-edge rebuild into `rebuild_similarity_edges(db, threshold)` with safe scope (delete/rebuild only `relation_type="similar"`).
- Added explainability logs:
  - `node_merge_resolved` for node merge/upsert resolution.
  - `similar_edge_created` with source/target, threshold, similarity, and context (`incremental_build` / `rebuild`).
  - `similar_rebuild_summary` with aggregate rebuild metrics.
- Rebuild API now returns summary fields:
  - `threshold`, `total_nodes`, `embedding_nodes`, `candidate_edges`, `final_edges`, `removed_similar_edges`, `total_edges`.
- Kept compatibility by preserving existing response fields (`threshold`, `total_edges`) and existing graph query fields.
- Added edge `created_at` to graph payload/detail payload so each similar edge can be traced to generation time.
- Added tests for:
  - safe rebuild behavior (manual node + non-similar edge preservation)
  - rebuild summary counts
  - edge payload `created_at`

## Commands run
1. `python3 -m compileall .`
2. `python3 -m pytest -q tests/test_graph_curation.py`
3. `./.venv/bin/python -m unittest tests.test_graph_curation -v`
4. `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true`

## Test / eval result
- `python3 -m compileall .`: pass
- `python3 -m pytest -q ...`: failed in this environment with `No module named pytest`
- `./.venv/bin/python -m unittest tests.test_graph_curation -v`: pass (14/14)
- Issue eval command: compile step pass; pytest step reports missing `pytest` module and is tolerated by `|| true`

## Metric delta (primary metric)
- Repeatable similar-edge rebuild now emits deterministic summary stats (`candidate_edges`, `final_edges`) at a fixed threshold.
- Each newly created similar edge now has explainable provenance in logs (source nodes + threshold + similarity) and retains score/time (`weight`, `created_at`) in graph payload.

## Risks / follow-ups
- Current environment lacks `pytest`; human review should run `python3 -m pip install -r requirements.txt` (or install `pytest`) then re-run full pytest suite.
- Similar-edge explain logs are per-edge; on very large graphs log volume may grow and may need rate limiting/sampling later.
