# Paper evidence layer · 2026-10-30

## Outcome

This local iteration establishes a versioned, deterministic evidence contract
between a source PDF and later extraction, compilation and retrieval stages.
It is intentionally not wired into application startup or the production paper
pipeline yet. Existing papers, databases, Wiki files, indexes and APIs retain
their current behavior.

The contract records:

- source file hash and size from the same immutable in-memory bytes snapshot
  supplied to the parser, parser identity and optional document-open failure;
- one explicit status for every source page: `ok`, `empty` or `error`;
- full extracted-page character count/hash and page-local evidence spans;
- bounded heading-derived section kind, heading and provenance;
- content-addressed chunk and manifest identities;
- attempted, text, empty and failed page coverage, extracted characters,
  `page_iteration_complete` and `has_usable_text` rather than treating a fully
  iterated image-only PDF as usable textual evidence.

Only exception classes are retained for failures. Exception messages are not
persisted because they may contain local paths or private content.

## Isolation and delivery gate

- Branch: `codex/paper-evidence-layer-20261030`
- Worktree: `/private/tmp/knowra-paper-evidence-layer-20261030`
- Base: `origin/main` at `918614aed15a7e83dfe678099518252fd8e1f409`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: 24.14.1 required for the unified check
- Test material: fabricated in-memory page text and temporary synthetic files
- External model/API calls: none
- App/API/worker startup: none
- Real databases, papers, Wiki, indexes, configuration and running services:
  not read, written, migrated, recompiled or restarted

Per the current delivery rule, this round remains a local dependency commit.
No PR is created or published unless every later system-optimization round and
the final end-to-end acceptance gate pass. A failed final gate means none of
this iteration's PRs are published.

## Definition of Done

- [x] Every known source page has an explicit record; missing pages fail
  validation rather than becoming silent coverage loss.
- [x] Evidence chunks carry paper, page, page-local offsets, section metadata,
  text hash and a stable content-addressed identifier.
- [x] Repeated builds from identical inputs are byte-equivalent at the object
  level; source/content changes alter the relevant identities.
- [x] Empty pages, page access/extraction failures and document-open failures
  remain distinguishable without persisting exception messages; later pages
  continue after a page-local failure.
- [x] Manifest loading fails closed on altered text, invalid bounds, inconsistent
  page/chunk references, invalid chunking metadata or coverage mismatches.
- [x] Persistence requires an explicit caller path and uses a temporary file
  plus atomic replace; a failed replace preserves the previous artifact.
- [x] The phase-0 evaluator exposes source, evidence, extraction and Wiki claim
  upper bounds without breaking fixtures that omit the new evidence layer.
- [x] The bounded backend check includes the evidence service tests.
- [x] Three independent review passes have no unresolved blocking finding.
- [x] The repository unified check passes in the pinned native environment.
- [x] No production pipeline, schema, API, data or service is changed.

## Synthetic verification

Targeted command:

```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m unittest \
  backend.tests.test_paper_evidence_service \
  backend.tests.test_ask_quality_eval -v
```

Result: 25/25 passed. The cases cover multi-page section inheritance, complete
last-page extraction, empty/error pages, document-open failure, deterministic
identities, Unicode surrogate sanitization, fail-closed loading, atomic write
recovery and evidence-oracle compatibility.

In-memory cost measurements used 5,000 fabricated characters per page and
default 2,400-character chunks with 240-character overlap:

| Synthetic size | Input characters | Chunks | Compact JSON | Build + validation | JSON/input |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 48 pages | 240,027 | 144 | 334,231 bytes | 11.360 ms | 1.392× |
| 200 pages | 1,000,027 | 600 | 1,391,262 bytes | 47.264 ms | 1.391× |

These are single-process diagnostic measurements on synthetic text, not stable
performance targets. They exclude PDF decoding time, filesystem persistence,
indexing, embeddings and model calls.

Unified repository check:

```bash
NODE_BIN=/Users/vince/.local/lib/node-v24.14.1-darwin-arm64/bin/node \
  ./scripts/check.sh
```

- Native startup fixtures: 29/29 passed.
- Bounded isolated backend: 130/130 passed.
- Frontend unit tests, TypeScript and Vite build: passed.
- Full ESLint: 0 errors, 0 warnings.
- Mock browser lifecycle regressions: 7/7 passed.

The check used disposable source/data snapshots, blocked external providers and
did not start the real application. `--real-start` was not run because this
round does not alter startup or service wiring.

## Compatibility, rollback and limitations

- Compatibility is additive: no Paper model, database schema, Wiki format,
  endpoint or background task imports this module.
- Rollback is removal of the local dependency commit. No data cleanup or schema
  rollback is needed because no real artifact is generated.
- `pypdf` text extraction preserves a page locator, not layout coordinates or
  OCR fidelity. Scanned/image-only pages remain `empty` until a separately
  evaluated OCR path exists.
- Snapshotting the PDF bytes closes hash/parse time-of-check/time-of-use drift,
  but temporarily retains one source-file-sized byte buffer in memory. This
  cost must be measured on the actual library size distribution before any
  production pipeline integration.
- Heading detection is a bounded heuristic. `section_source` explicitly says
  whether a section was detected, inherited or unknown; it is not presented as
  ground truth.
- Character offsets address the extracted page string, not PDF byte offsets or
  rendered bounding boxes.
- This round does not yet map structured claims to supporting chunk IDs, compile
  evidence into Wiki, index chunks, change Ask retrieval or prove answer quality
  on private papers. Those remain later dependent rounds.
- Any real evidence generation, shadow recompilation or migration remains
  blocked on the documented cross-asset backup and restore rehearsal plus
  explicit user approval.
