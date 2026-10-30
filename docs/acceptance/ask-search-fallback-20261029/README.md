# Ask strict-first search fallback · 2026-10-29

## Scope

This iteration changes only Ask's local Wiki retrieval. Existing strict FTS5
search remains the first attempt and keeps its hit order. A zero-hit Ask query
may then resolve explicit compiled-Wiki titles, slugs, or aliases present in
the question, search their canonical titles with AND, and fill remaining slots
with de-duplicated OR hits. No model call, embedding, dependency, database
schema, or public `/wiki/search` behavior was added or changed.

The original Chinese regression question is represented with synthetic pages;
no user paper title, Wiki body, database, index, configuration, or provider was
used in the committed evidence.

## Definition of Done

- [x] Strict queries with hits preserve the existing order and scores.
- [x] The synthetic long Chinese question misses strict phrase search and is
  recovered through explicit title/alias entity AND then OR fallback.
- [x] Exact titles, English aliases, multiple concepts, unknown questions,
  punctuation-only input, ASCII alias boundaries, de-duplication, and stable
  AND-first ranking have regression coverage.
- [x] Codex CLI retrieval reads the recovered synthetic Wiki pages without an
  additional model call.
- [x] The Responses API tool loop receives the same structured fallback result.
- [x] Ask trace summaries expose the selected mode and executed fallback query.
- [x] The maintained offline backend check includes the new synthetic suite.
- [x] No evidence-enforcement/refusal policy from the next planned iteration is
  included here.

## Verification

Environment:

- Base: `origin/main` at `ead177956815cf1d0da4f54119e1932e22b68fe6`
- Branch: `codex/ask-search-fallback-20261029`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: official 24.14.1 arm64 archive, SHA-256 verified, worktree cache only
- Data: temporary synthetic Markdown and SQLite FTS5 databases only

Targeted regression:

```text
21 tests passed
tests.test_wiki_search_fallback
tests.test_ask_agent
tests.test_ask_router
tests.test_wiki_router_incremental
tests.test_knowledge_graph_baseline
```

Unified check:

```text
29 native startup tests passed
68 offline backend tests passed
frontend unit/types/build/lint passed (0 errors, 0 warnings)
frontend lifecycle types/build passed
6 mocked browser lifecycle tests passed
```

The first sandboxed unified run could not bind its synthetic loopback ports and
the ChatGPT-bundled Node binary could not load npm's Rolldown native module due
to macOS Team ID enforcement. No product code was changed for either issue.
The final run used the SHA-verified official Node runtime and the suite's
approved isolated loopback/browser environment.

## Interaction and visual evidence

There is no visual layout change. The existing expandable Ask trace renders the
backend `result_summary`; the synthetic Codex CLI test asserts that the summary
contains both `entity_and_or` and `executed_queries`. Screenshot evidence is
therefore not applicable to this backend-only iteration.

## Limitations

- This is deterministic explicit-entity matching, not synonym or semantic
  retrieval. Questions without a known title, slug, or alias still stop after
  strict search.
- Strict search is not broadened when it already has at least one hit.
- Up to six non-overlapping canonical entities are used; longest mentions win.
- Entity metadata is published as one immutable cache snapshot and invalidated
  after a synthetic FTS rebuild; the regression suite covers alias changes.
- Evidence sufficiency, refusal behavior, and stricter citation semantics remain
  for the next separately authorized iteration.
- No real provider, daily service, user database, user Wiki, migration,
  deployment, or unrestricted browser suite was run.
