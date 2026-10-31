# Claim-level support and shadow answer · 2026-10-30

## Outcome

This local-only iteration adds an offline, fail-closed contract between retained
extraction claims, exact page evidence spans and answer citations. It is based
on local dependency commit `68e5a34` in worktree
`/private/tmp/knowra-claim-support-answer-20261030` on branch
`codex/claim-support-answer-20261030`.

The implementation is deliberately not wired into production Ask. Ordinary
content hashes prove deterministic identity, not reviewer authority. Both the
CLI and API production adapters therefore refuse to emit shadow answer text
until a separately designed trusted-review receipt exists.

## Definition of Done

- [x] Each `supports` assessment binds one retained claim to an exact validated
  evidence manifest, source-file hash, chunk, normalized-text character span
  and page locator.
- [x] Candidate compiler links remain distinct from adjudicated support.
- [x] Comparison/evolution plans require multiple papers and distinct source
  files; evolution additionally requires chronological year claims.
- [x] Missing retrieval provenance, missing support, unlisted conflicts,
  duplicated adjudications and altered source/catalog payloads fail closed.
- [x] Cross-source contradictions may target a stage claim, but can never be
  used as affirmative support and must be disclosed.
- [x] Shadow Markdown escapes claim, label, locator and quote content; raw
  citation data stays structured.
- [x] Synthetic and caller-declared manual assessments both remain
  `production_eligible=false`; production CLI/API wrappers return one
  deterministic refusal and no citations.
- [x] Work is isolated, bounded and covered by the maintained backend check.
- [x] No real paper, Wiki, database, index, config, provider or running service
  was read, written, migrated, started or stopped.

## Validation

Environment: Python 3.12.13, Node 24.14.1; worktree-local managed environments.

Focused command:

```text
backend/.venv/bin/python -m unittest tests.test_claim_support_answer -v
```

Result: 13/13 passed. The cases include exact citation locations, shadow-only
manual declarations, production adapter refusal, missing retrieval, copied-PDF
source diversity, chronological ordering, targeted conflicts, source-version
binding, rehashed tampering, validator replay, adjudicated chronology,
Markdown/HTML/reference injection and budgets.

Dependency regression: 49/49 paper-evidence, claim compiler, hybrid retrieval
and claim-support tests passed. Unified synthetic check passed: 29 native
startup tests, 167 backend tests, frontend unit/type/build/lint with zero
warnings, lifecycle type/build, and 7 mocked browser regressions. The unified
check used no real startup because this round changes neither startup nor
production wiring.

The dependency regression command initially named a nonexistent
`tests.test_wiki_shadow_compiler` module and failed only at import; the actual
maintained module list contains `test_wiki_claims` and
`test_hybrid_claim_retrieval`. The corrected dependency and unified check
results are recorded after final execution.

## Limitations and rollback

- Assessments are explicit inputs; this iteration does not infer entailment or
  define an authenticated reviewer/approval system.
- No real private benchmark, provider model, latency distribution or citation
  quality has been measured.
- No production Ask endpoint imports this module. Rollback is removal of the
  new service, test registration and acceptance entry, with no data migration.
- Final publication remains blocked on the subsequent integrated synthetic and
  approved private shadow validation. Per the user delivery rule, no branch or
  PR from this optimization chain is published unless that final target passes.
