# Ask source-level evidence gate · 2026-10-30

## Scope

This is the second Ask iteration after the strict-first search fallback merged
in PR #38. It adds a deterministic, source-level gate after retrieval and
before a response is returned. Search results and index titles remain routing
signals; paper or concept content claims require a successful `read_wiki` in
the current run.

The HTTP response fields are unchanged. The existing trace gains one final
`evidence_gate` step, and the returned answer/citations are normalized from
verified tool results. No model, dependency, database schema, public search
endpoint, or frontend layout was added or changed.

## Evidence states

- `supported`: at least two successfully read Wiki files and the required
  source count has been met.
- `limited`: one successfully read Wiki file for a single-source question;
  the answer carries a visible boundary that it is not a cross-paper result.
- `structure_only`: a structural question has a positive bounded graph query,
  but Wiki content does not meet the content-source threshold. The answer is
  explicitly limited to graph structure.
- `catalog_only`: an explicit knowledge-library/catalog request has a
  successfully read `index.md`. Generic content questions mentioning indexing
  or papers do not qualify.
- `insufficient`: no usable content evidence, or a cross-source question has
  fewer than two successfully read Wiki files. The model answer is discarded
  and replaced with a deterministic insufficiency response.

A question requires two content sources only when it contains an explicit
comparison instruction or the merged search fallback reports two or more
explicit known entities. Broad words such as “change”, “integration”, `变化`,
or `结合` do not independently raise the threshold.

## Definition of Done

- [x] Responses API, Chat Completions, and Codex CLI all pass through the same
  final evidence and citation normalization.
- [x] Search snippets, index titles, failed/missing reads, duplicate-call
  sentinels, tool-limit sentinels, and model-written `[[paper:id]]` markers do
  not count as Wiki content evidence.
- [x] `paper` / `concept` tool arguments are normalized to the canonical
  `papers` / `concepts` citation paths after successful reads.
- [x] Cross-source questions with one successful read return a deterministic
  insufficiency response; two successful reads pass.
- [x] The first-iteration synthetic long Chinese question recovers multiple
  files and reaches `supported` through the real search/read path.
- [x] A final canonical source section is built from verified tool results;
  a model-generated standard source section and unverified paper markers are
  removed.
- [x] Positive structural queries retain graph node/edge citations, including
  the singular `edge` returned by `explain_edge`.
- [x] Catalog exceptions require explicit “知识库 / library / knowledge base”
  context. “How does the indexing method work?” and “有哪些论文支持 Alpha 的结论？”
  remain subject to the content-evidence gate.
- [x] The existing response schema remains compatible; the new suite is part
  of the maintained offline backend check.
- [x] Independent review completed with all reported blockers fixed and
  re-reviewed.

## Verification

Environment:

- Base: merged `origin/main` at `39acfb52bc85fcd2365f944c2d1da3971abe8889`
- Branch: `codex/ask-evidence-gate-20261030`
- Worktree: `/private/tmp/knowra-ask-evidence-gate-20261030`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: official 24.14.1 arm64 archive, SHA-256 verified, worktree cache only
- Data: synthetic mocks and temporary test fixtures only

Related Ask/Wiki regression:

```text
43 tests passed
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
82 offline backend tests passed
frontend unit/types/build/lint passed (0 errors, 0 warnings)
frontend lifecycle types/build passed
6 mocked browser lifecycle tests passed
```

## Interaction and visual evidence

There is no layout change. Users see the normalized answer boundary and source
section in the existing Markdown surface; the existing expandable trace shows
the final `evidence_gate` status and counts. These behaviors are exercised by
the synthetic backend tests, so screenshot evidence is not applicable.

## Limits and remaining work

- This is document/source-level gating, not claim-level semantic verification.
  A successful read proves that a source was opened, not that every generated
  sentence is entailed by it.
- Counting two files does not prove semantic relevance, source independence,
  or full coverage. A concept page and a paper page may overlap.
- Standard trailing source sections and unverified `[[paper:id]]` markers are
  normalized. Non-standard inline filenames, Markdown links, or prose such as
  “参考资料” cannot be reliably removed without claim-level validation.
- `limited`, `structure_only`, and `catalog_only` retain the model narrative
  with an explicit boundary. Prompt compliance is not a formal claim verifier.
- On Responses/Chat providers, a model call may already have occurred before
  a terminal answer is rejected. The deterministic Codex CLI path skips its
  answer-generation call when retrieval already proves evidence insufficient.
- Citation-to-claim alignment, quoted evidence spans, and semantic sufficiency
  evaluation remain candidates for later, separately authorized iterations.
- No real provider, user database, user Wiki, paper collection, migration,
  running service, unrestricted browser suite, deployment, or merge was used.
