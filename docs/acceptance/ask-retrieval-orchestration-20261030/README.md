# Ask retrieval orchestration · 2026-10-30

## Scope and behavior

This third Ask iteration coordinates the retrieval primitives from the prior
two merged iterations without adding embeddings, a planning model, a new
dependency, or a database change.

- API providers keep the existing Responses/Chat tool loop. The system prompt
  directs the model to refine a zero-hit query with another explicit entity or
  title, read candidates after a hit, and continue only while evidence remains
  insufficient. This is model-driven adaptation, not a deterministic planner.
- Codex CLI providers use bounded local orchestration: one primary Wiki search,
  optional searches for explicit terms returned by the existing fallback,
  round-robin candidate de-duplication, up to five read attempts targeting four
  successful sources, and at most one final synthesis call.
- Both paths feed the same source-level evidence gate and system-built citation
  list. Index and search snippets locate material but never count as content
  evidence.

Per-request in-memory limits are 9 model calls, 16 tool calls, 4 Wiki searches,
5 Wiki reads, 6 graph calls, 60,000 successfully read Wiki characters, a
16,000-character index, and 180 seconds wall-clock time. The remaining wall
time is passed to every API and CLI model call; OpenAI client retries are
disabled for this loop. The trace exposes actual use, measured model/tool time,
limits, blocked calls, and their reasons.

## Definition of Done

- [x] API zero-hit synthetic flow can refine its query, read the recovered
  candidate, and produce only evidence-gated citations.
- [x] API search, read, graph, tool, model, character, and elapsed-time budgets
  block before additional dispatch; Responses, Chat, and CLI calls receive a
  remaining-time timeout.
- [x] Codex CLI executes bounded primary/entity routes, balances and de-duplicates
  candidates, tries the fifth candidate after a failed read, and synthesizes at
  most once.
- [x] Duplicate protection normalizes whitespace/case, singular/plural Wiki
  kinds, unordered graph node IDs, and ignores non-dispatch metadata.
- [x] Wiki body truncation includes its suffix inside the character cap; index
  content is independently capped and measured before entering model context.
- [x] The compact public budget trace stays within the existing 240-character
  summary while the raw internal trace retains the structured snapshot.
- [x] Both provider paths retain the merged evidence/refusal and citation
  semantics from PR #39.
- [x] Maintained backend checks include the new synthetic regression suite; no
  real provider, user Wiki, paper, database, configuration, or running service
  is used.

## Verification

Environment:

- Base: `origin/main` at `1676a6adfcefd2b798e5ee68d2495e3460dbd964`
  (merged PR #39)
- Branch: `codex/ask-retrieval-orchestration-20261030`
- Worktree: `/private/tmp/knowra-ask-retrieval-orchestration-20261030`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: official 24.14.1 arm64 archive, checksum verified, worktree cache only
- Data: in-memory mocks and disposable synthetic Markdown/SQLite fixtures only

Targeted regression after independent-review fixes:

```text
55 tests passed
tests.test_ask_retrieval_orchestration
tests.test_ask_evidence_gate
tests.test_ask_agent
tests.test_ask_router
tests.test_ask_synthesis
tests.test_wiki_search_fallback
tests.test_wiki_router_incremental
tests.test_knowledge_graph_baseline
```

Unified check:

```text
29 native startup tests passed
94 isolated backend tests passed
frontend unit/types/build/lint passed (0 errors, 0 warnings)
frontend lifecycle types/build passed
6 mocked browser lifecycle tests passed
```

The first sandboxed unified run could not bind synthetic loopback ports; its
disposable worker children likewise could not claim test jobs. The same suite
was rerun with permission for isolated loopback and test child processes and
passed in full. No product code was changed for that environment restriction.
There is no visual layout change; screenshot evidence is not applicable to
this backend-only iteration.

## Limits and non-claims

- API adaptation depends on the selected model following the tool prompt. The
  synthetic tests verify the protocol and budgets, not real-provider planning
  quality or compliance.
- Codex CLI entity expansion happens only when the primary search payload
  contains explicit fallback terms. A strict primary hit remains a single
  route; this iteration does not add synonym or semantic retrieval.
- One `search_wiki` tool call can internally execute strict, AND, and OR FTS
  attempts from the merged fallback. The budget counts public tool calls, while
  the search trace retains the executed internal queries.
- Character limits are Python string characters, not tokenizer tokens or
  encoded bytes. A local filesystem operation already in progress is not
  interruptible by the orchestration timer; model calls are bounded by the
  remaining timeout.
- The evidence gate remains source-level rather than claim-level entailment.
  Two readable sources do not prove that every generated sentence is correct.
- No real model/provider, daily service, user research data, migration,
  unrestricted E2E, PostgreSQL RLS, mobile build, deployment, or merge was run.

