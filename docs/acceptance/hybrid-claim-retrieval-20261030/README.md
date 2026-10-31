# Shadow claim retrieval acceptance

## Scope and safety

This iteration adds an offline, shadow-only retrieval experiment. It does not
replace `wiki_search`, change an Ask response, start the application, call an
embedding/model provider, or read/rebuild the daily library.

- Worktree: new isolated temporary checkout (local path intentionally omitted)
- Branch: `codex/hybrid-claim-retrieval-20261030`
- Base: local dependency commit `c95a10a` (which depends on `fc9eaaf`)
- Data: generated manifests, compile plans, SQLite indexes and vectors in
  disposable test directories only
- Publication: local dependent commit only; no branch push or PR before the
  final multi-round acceptance gate

## What is implemented

1. Shadow compile plans carry a bounded claim-text ledger inside their signed
   payload. Paper evidence links can be supplied only with a validated manifest
   whose source hash matches the paper.
2. The index accepts only validated compile plans and evidence manifests. It
   stores canonical claims, validated source chunks, all paper/concept plan
   locations, source status and locators in a separate SQLite database below an
   explicit shadow root.
3. FTS/BM25 and optional caller-supplied dense vectors are fused by RRF. Dense
   vectors are bound to an explicit `model@revision`, dimension, vector hashes,
   corpus signature and policy digest. No provider is called implicitly.
4. A trusted-process retrieval plan allows at most four primary routes and one
   gap route. CLI/API shadow adapters call the same executor. Duplicate query
   variants cannot multiply a record's score.
5. The executor caps query/vector/filter/index/result sizes, per-search and
   plan wall time, compact trace size and final text characters. It exposes
   unmet paper/source coverage after final selection.
6. `unavailable`, `candidate_evidence` and raw `source_text` remain distinct.
   Retrieval never upgrades any of them to entailment. Consequently this
   shadow executor always reports `answerable=false`; a later claim-support
   validator is required before activation.

`plan_id` is a canonical content identifier and catches accidental in-process
mutation. It is not an authentication signature and must not be accepted from
an untrusted client; the current module is not exposed through an API.

## Synthetic result

The fixed cross-paper evolution fixture contains two relevant modeling claims,
one relevant source chunk and one semantically unrelated boundary-model hard
negative.

| Strategy / control | Recall@4 for required records | Result |
| --- | ---: | --- |
| Lexical-only, long Chinese question | 0.00 | expected baseline miss |
| Hybrid with precomputed fixture vector | 1.00 | all 3 required records |
| Random absent query | n/a | zero candidates, coverage unmet |
| Boundary-model hard negative | n/a | retrievable only for its own facet; never answerable |

These are synthetic protocol results, not real-library quality. The vector is a
fixture, not a selected production embedding model, so the result proves the
fusion/provenance contract rather than semantic generalization.

## Definition of Done

- [x] Claim text comes from a validated, signed compile-plan ledger; Markdown
  is never reverse-parsed as provenance.
- [x] Ledger status/field/paper/candidate IDs must match signed source metadata.
- [x] The same stable claim from paper and concept plans is indexed once while
  retaining both locations.
- [x] Validated evidence chunks retain page/offset/hash and are labelled raw
  `source_text`, not claim entailment.
- [x] Exact lexical retrieval, dense-only recovery, RRF, filters, stable tie
  breaks, hard negative and no-answer controls are covered.
- [x] CLI/API shadow wrappers use one bounded plan executor with at most one gap
  route; final top-N coverage, not the unbounded aggregate, controls the gap.
- [x] Dense model mismatch, stale corpus/dense signatures, dimension/filter/
  corpus/text budgets, deadline, altered plans/ledgers and unsafe paths fail
  closed.
- [x] Existing v1 Wiki compiler, production Wiki index, Ask gate and provider
  loops are unchanged.
- [ ] Real private corpus Recall/MRR/no-answer calibration: not run and not
  authorized.
- [ ] A local embedding model/revision and its quality/cost: not selected.
- [ ] Claim-to-source entailment and answer activation: deferred to the next
  dependent round.

## Validation record

- 36 evidence／claim／hybrid targeted tests passed.
- Final `NODE_BIN=<Node-24.14.1-path> ./scripts/check.sh` passed in the
  repository's isolated fixtures: 29 startup, 154 backend, frontend unit/types/
  build/lint with zero warnings, and 7 mock-browser lifecycle checks.
- The first restricted-sandbox attempt could not bind loopback ports and was
  invalid; the same suite passed when temporary loopback binding was permitted.
- `git diff --check`
- independent retrieval and orchestration reviews; all reported blockers are
  addressed, reproduced as regressions, and final re-review found no blocker.

The full suite uses Python 3.12.13 and Node 24.14.1 from this worktree's managed
environment. It did not start the daily application or read real research data.

## Remaining activation blockers

1. The dense provider/model and thresholds need a larger public/private labelled
   set; a fixed similarity threshold is not an answer-confidence score.
2. Raw source chunks do not prove that a claim is entailed. The next round must
   validate explicit claim-to-span support and keep conflicts/negation visible.
3. Production CLI/API Ask still use the existing paths. These wrappers establish
   a shared shadow contract only; activation requires integration tests through
   the actual provider entry points.
4. Real shadow compilation/retrieval requires a consistent approved snapshot.
   Recompilation, switching or backup copying remains prohibited until the user
   approves the separate backup/restore plan.
