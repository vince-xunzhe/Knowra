# Ask quality evaluation foundation · 2026-10-30

## Outcome

This iteration establishes a layered, privacy-safe baseline before changing
retrieval or recompiling research data. It exercises the production
`wiki_search` implementation against a disposable synthetic Wiki/FTS index and
separates:

- source → extraction claim retention;
- extraction → compiled Wiki claim retention;
- document recall and ranking;
- retrieved claim coverage and no-answer behavior.

It intentionally does not improve production retrieval yet. The baseline must
remain able to show a failure before a later implementation can claim an
improvement.

## Environment and isolation

- Branch: `codex/retrieval-eval-foundation-20261030`
- Worktree: `/private/tmp/knowra-retrieval-eval-foundation-20261030`
- Base: `origin/main` at `8f100da9186d383ecb601c84bbaed06bd5035518`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: 24.14.1, worktree-local `frontend/node_modules`
- Data: four synthetic Markdown documents and a temporary FTS5 database
- External calls: none
- App/API/worker startup: none outside the repository's isolated test harness
- Real databases, papers, Wiki, configuration and running services: not read or
  modified

The fixture uses fabricated titles, statements and research entities. Its two
Chinese questions preserve only the structural shape of a long evolution query;
no user question, paper title, body, record, database row or model response is
included.

## Definition of Done

- [x] A versioned fixture distinguishes source, extraction and Wiki claim IDs.
- [x] Invalid/missing document references and unsupported derived claims fail
  validation instead of inflating scores.
- [x] The evaluator builds and queries the real production FTS implementation
  in a disposable directory and restores module paths after each run.
- [x] Reports include Recall@k, Precision@k, MRR, nDCG@k, Wiki claim coverage,
  no-answer accuracy and latency.
- [x] Oracle upper bounds make pre-retrieval information loss visible.
- [x] Two fictional long-question shapes, exact-title control, concept-loss
  control and no-answer control are covered.
- [x] The bounded offline backend check includes this evaluator and its JSON
  fixture.
- [x] A staged system plan covers extraction fidelity, Wiki retention, hybrid
  retrieval, ranking, claim-level citation, cost, rollback and the backup gate
  for any future real recompile.
- [x] No production search behavior, evidence threshold, data or service was
  changed.

## Reproducible baseline

Run:

```bash
backend/.venv/bin/python backend/scripts/ask_quality_eval.py \
  --output .cache/ask-quality-eval.json
```

The stable, latency-free summary is in
[`baseline-summary.json`](baseline-summary.json). Key observations:

| Synthetic diagnostic | Source oracle | Extraction oracle | Wiki oracle | Production Recall@k |
| --- | ---: | ---: | ---: | ---: |
| Two-entity evolution question | 1.00 | 1.00 | 1.00 | 0.00 |
| One-entity evolution question | 1.00 | 1.00 | 1.00 | 0.00 |
| Concept retention control | 1.00 | 1.00 | 0.00 | 1.00 |

This demonstrates two different failure classes without conflating them:

- a relevant paper Wiki can contain every required synthetic claim but still be
  missed by production retrieval;
- a concept document can rank first while containing none of the required
  synthetic claims, so better ranking alone cannot repair compilation loss.

Aggregate production metrics are Recall@k 0.50, Precision@k 0.166666, MRR 0.50,
nDCG@k 0.50, retrieved Wiki claim coverage 0.25 and no-answer accuracy 1.00.
They are diagnostic fixture scores, not a product-quality target.

## Verification

### Targeted and related regression

```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m unittest \
  backend.tests.test_ask_quality_eval \
  backend.tests.test_wiki_search_fallback \
  backend.tests.test_ask_retrieval_orchestration \
  backend.tests.test_ask_evidence_gate -v
```

- New evaluator: 10/10 passed after independent-review hardening.
- Combined evaluator and related Ask/Wiki regression: 46/46 passed, including
  10 evaluator cases for unsupported/source-oracle labels, metric definitions
  and global cache restoration.

### Unified repository check

```bash
NODE_BIN=/Users/vince/.local/lib/node-v24.14.1-darwin-arm64/bin/node \
  ./scripts/check.sh
```

- Native startup: 29/29 passed.
- Bounded isolated backend: 115/115 passed, including 10 evaluator tests.
- Frontend unit, TypeScript and Vite build: passed.
- Full ESLint: 0 errors, 0 warnings.
- Mock browser regression: 7/7 passed.

The first invocation without `NODE_BIN` correctly rejected globally selected
Node 26. Two direct sandboxed `scripts/check_backend.py` diagnostic runs had
four pre-existing subprocess-worker cases remain queued past their 10-second
poll window. The supported unified check, run with the required Node version
and its normal isolated subprocess permissions, passed all 115 backend cases.
No product code was changed to hide those diagnostic failures.

## Limitations and next gate

- No live LLM answer, semantic retrieval, reranker or private corpus is scored.
- Claim labels are annotations; this round does not automatically prove textual
  entailment.
- PR #42 is not in the base. If it merges, this evaluator should measure its
  effect rather than preserve the current failing score as an assertion.
- A private 30–50 question judged set and oracle comparisons are still required
  before claiming real Ask success.
- Any real Wiki/paper recompilation remains blocked on the documented backup,
  manifest and restore rehearsal, plus explicit user approval of the data
  operation.
