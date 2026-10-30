# Wiki index status and safe refresh · 2026-10-30

## Scope and behavior

This fourth Ask iteration makes the two existing local indexes explicit without
changing retrieval semantics:

- **Wiki overview** is `data/wiki/index.md`, the orienting overview that Ask can
  read before searching. Its status reports readability, staleness, bytes,
  approximate tokens, source counts, and measured read latency.
- **Full-text search** is `data/wiki_search.sqlite`, the SQLite FTS5 corpus used
  by `search_wiki`. Its separate status reports health, staleness, document
  counts, bytes, approximate tokens, and measured status latency. The same
  response reports the compiled paper/concept directory's size and scan time.

The Ask drawer displays the two statuses and refresh actions on separate rows.
A full-text refresh builds an adjacent shadow database and atomically replaces
the active database only after the shadow build closes successfully. Overview
writes likewise use an adjacent temporary file and atomic replacement. A source
read failure aborts the build rather than installing a partial index. When a
refresh fails, the API and UI say whether a previously healthy index remains
usable. Merely reading FTS status opens an existing database in SQLite read-only
mode and does not create a missing database.

Successful refresh responses include the status snapshot from that build. The
UI adopts it directly, avoiding a second GET that could otherwise make a
successful refresh look like a failure.

This iteration does not add embeddings, semantic retrieval, contradiction
checking, question/answer deposition, a schema migration, or a new dependency.

## Measured evidence and decision

`scripts/measure_wiki_indexes.py` generated disposable Markdown and SQLite
fixtures; it did not read a user Wiki, database, paper, configuration, or call a
provider. Times are one local run and support an implementation decision, not a
cross-machine performance guarantee.

| Synthetic corpus | Wiki directory | Deterministic overview | Full LLM overview prompt estimate | FTS5 database | FTS rebuild | FTS status | FTS query p50 / max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 documents | 201.0 KB / ~63.2k tokens / 11 ms scan | 10.0 KB / ~2.7k tokens / 30.100 ms refresh | ~21.4k tokens | 440.0 KB | 6 ms | 20 ms | 0.632 / 0.785 ms |
| 500 documents | 1005.5 KB / ~316.1k tokens / 59 ms scan | 48.7 KB / ~13.3k tokens / 145.760 ms refresh | ~106.8k tokens | 2.0 MB | 25 ms | 102 ms | 1.436 / 1.528 ms |

The overview refresh timing includes pre/post source-read safety scans. Token
figures use the UI's CJK-aware heuristic
`CJK characters + non-CJK characters / 3.6`, not a provider tokenizer. Exact
JSON is in [measurements.json](measurements.json).

**Decision:** keep the current complete FTS rebuild. At the largest measured
fixture it rebuilt in 25 ms and queried in 1.4 ms median, so this evidence does
not justify incremental indexing, embeddings, or another search dependency.
Status collection remains a linear diagnostic scan (102 ms at 500 documents).
The ~106.8k estimated manual LLM-overview prompt at 500 documents is a scaling
warning; this synthetic point alone does not establish a safe chunking or
summarization redesign, so no speculative refactor is included.

## Definition of Done

- [x] Ask separately labels Wiki overview and full-text search state and gives
  each its own refresh action.
- [x] Status responses include size, approximate token count, latency, health,
  staleness, and paper/concept or document counts.
- [x] Full-text status is read-only and does not create a missing SQLite file.
- [x] Overview and FTS refreshes atomically replace only a fully built result.
- [x] Synthetic failures during source reading, text replacement, and SQLite
  replacement preserve prior bytes and leave the old FTS queryable.
- [x] Configuration failure and build failure messages distinguish “old index
  remains usable” from “no usable old index.”
- [x] Successful refreshes return and adopt one status snapshot without a
  fallible follow-up GET; later failed refreshes retain the displayed snapshot.
- [x] Maintained backend and mocked-browser regressions cover status separation,
  stale detection, atomic failure, and retained UI state.
- [x] A disposable benchmark records directory/index size, approximate tokens,
  rebuild/status/search latency, and the resulting keep/refactor decision.
- [x] No real research data, database, running service, provider, configuration,
  migration, deployment, or merge is used.

## UI evidence

Both captures use the real React component, a disposable Chrome profile, and
fully mocked local API responses; all external requests and unmocked API routes
fail the run.

Before — one ambiguous `index.md` row:

![Before: one index status](before-index-status.png)

After — distinct overview, FTS5, and Wiki-directory measurements:

![After: separate overview and full-text statuses](after-index-statuses.png)

[browser-results.json](browser-results.json) records the requests, visible text,
zero browser violations, and the failed full-text refresh assertion that the
old FTS row remained visible and usable.

## Verification

Environment:

- Base: `origin/main` at `7780a9df09ff10e2305e6d9ef48ee40a81edca6c`
  (merged PR #40)
- Tested implementation commit: `767b1aa2ebdfcec1581480647b447e8d6b5ecca1`;
  the following delivery commit only records the PR and tested SHA
- Branch: `codex/wiki-index-status-20261030`
- Worktree: `/private/tmp/knowra-wiki-index-status-20261030`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: 24.14.1; Vite 8.0.10; TypeScript 6.0.3
- Browser: installed Google Chrome with a disposable profile
- Data: generated Markdown/SQLite and mocked API responses only

Targeted regression after independent-review fixes:

```text
35 tests passed
tests.test_wiki_index_status
tests.test_wiki_search_fallback
tests.test_wiki_index_incremental
tests.test_ask_router
tests.test_wiki_router_incremental
tests.test_ask_synthesis
```

Final unified check after review fixes:

```text
29 native startup boundary tests passed
105 isolated backend tests passed
frontend unit/types/build/lint passed (0 errors, 0 warnings)
frontend lifecycle types/build passed
7 mocked browser lifecycle tests passed with 0 violations
```

The targeted 35 tests and the complete unified check were both rerun after the
final invalid-UTF-8 status fix. Independent re-review reported no blockers.

`./start.sh --check` passed on explicit ports 8135/5215 and confirmed that it
opened no database and started no service. `./scripts/check.sh --real-start`
was not run because no native startup path changed and this iteration is
restricted to synthetic checks without starting services. PostgreSQL RLS,
unrestricted E2E, real providers, mobile, deployment, and merge were omitted.

Delivery: [PR #41](https://github.com/vince-xunzhe/Knowra/pull/41), opened
non-Draft for user review. GitHub status and checks are verified after the
delivery commit is pushed; no merge or deployment is performed.

## Independent review

The review found one blocking partial-index risk and two status-reporting
issues. All were fixed before delivery:

- unreadable source Markdown now aborts both overview and FTS replacement, and
  status keeps index health separate from source-directory health;
- configuration/model selection is inside the overview failure-preservation
  boundary;
- both successful refresh endpoints return the new status snapshot, so the UI
  does not depend on a second status request.

The reviewer found no real-data, privacy, dependency, or API-compatibility
boundary violation. The known mtime race below remains documented rather than
expanding this iteration into digest-based or incremental indexing.

## Limits and non-claims

- Token counts are approximate and can differ materially from the selected
  model/provider tokenizer.
- Latencies are local synthetic observations, not production SLOs; status reads
  source Markdown and indexed title/body text, so its cost grows linearly.
- FTS staleness uses source counts, unreadable-file state, and source-vs-database
  modification times. A source changing during the narrow read/build window can
  evade this diagnostic signal; it is not a cryptographic content proof or a
  concurrency lock.
- Atomic replacement prevents a failed build from truncating the old index. It
  does not make simultaneous administrative refresh requests mutually exclusive;
  the Ask UI disables its two refresh buttons while either request is active.
- A healthy index means the file/database can be read with the expected table;
  it does not prove retrieval completeness or answer correctness.
- The benchmark does not establish that a 500-document full LLM overview will
  fit every provider context window. No real model was called.
