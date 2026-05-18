# VIN-11 Analysis

## Scope
- Implementation target is this `knowledge-tree-v2` workspace on branch `agent/VIN-11`.

## Changed Files
- `backend/services/ask_agent.py`
- `backend/routers/ask.py`
- `backend/tests/test_ask_agent.py`
- `backend/tests/test_ask_router.py`
- `backend/tests/test_ask_synthesis.py`
- `frontend/src/api/client.ts`
- `frontend/src/components/AskDrawer.tsx`

## What Changed
- Added structured Ask citations (`citations`) while preserving existing `cited_files` contract.
- Added Ask session passthrough (`session_id`) in request/response for trace continuity.
- Exposed citations in Ask UI as explicit source list (not only count).
- Extended synthesis payload with traceability fields:
  - `source_session_id`, `source_session_title`, `source_turn_indexes`, `source_cited_files`
- Persisted those traceability fields into concept page frontmatter.
- Strengthened synthesis dedupe by including model aliases in deterministic duplicate checks; still supports `force_create` override.

## Commands Run
- `python3 -m pytest -q backend/tests/test_ask_agent.py backend/tests/test_ask_router.py backend/tests/test_ask_synthesis.py`
- `cd backend && python3 -m compileall . && python3 -m pytest -q || true`
- `cd frontend && npm ci`
- `cd frontend && npm run build`

## Validation Result
- Backend targeted tests: pass (`11 passed`).
- Backend full suite via issue eval command form: pass (`58 passed`).
- Frontend build: pass (`tsc -b` + `vite build`).

## Metric Delta (Qualitative)
- Duplicate concept creation risk reduced via alias-aware deterministic dedupe plus existing model duplicate judgement.
- Concept pages now include session/turn/source citation metadata, improving post-hoc provenance and backfill traceability.
- Ask responses now present concrete citation sources in UI, improving source traceability for users before synthesis.

## Risks / Follow-up
- `session_id` is currently client-provided and local-session based; if server-side session storage is introduced later, map this ID to persisted session records.
- Citation granularity is currently file-level provenance (plus paper refs), not paragraph offsets; add paragraph anchors if stricter auditability is needed.
