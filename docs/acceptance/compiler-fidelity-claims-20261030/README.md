# Compiler fidelity and claim attribution · 2026-10-30

## Outcome

This local round introduces a v2 shadow compiler without changing the existing
Wiki compiler, Wiki files, FTS index, APIs or freshness behavior. It separates
three concepts that the current free-form Markdown path conflates:

1. a structured extraction claim and its stable identity;
2. a generated paragraph's attribution to one or more extraction claims;
3. page-level PDF evidence, which remains unavailable or candidate-only until
   a later stage verifies an exact supporting span.

The shadow path builds one immutable `CompilePlan`. The exact bounded payload,
system/user prompts, template/budget/renderer versions, model, locale and
generation parameters used by the plan are also the inputs to its SHA-256
signature. The model-facing output contract is structured paragraphs, and the
renderer rejects empty or unknown claim attribution before producing Markdown
and a lineage sidecar.

## Isolation and compatibility

- Branch: `codex/compiler-fidelity-claims-20261030`
- Worktree: `/private/tmp/knowra-compiler-fidelity-claims-20261030`
- Dependency base: local evidence-layer commit `fc9eaaf`
- Python: 3.12.13, worktree-local `backend/.venv`
- Node: 24.14.1, worktree-local `frontend/node_modules`
- Inputs and outputs used in tests: fabricated objects and temporary folders
- External model/API calls: none
- Real application/API/worker startup: none
- Real database, PDFs, Wiki, FTS, configuration and services: not read or
  modified

The production `wiki_compiler.py` is unchanged. Shadow writing requires both
an explicit `output_root` and `production_wiki_root`, rejects equal, parent or
child overlap after path resolution, and never invokes cleanup, reconciliation,
index refresh, pipeline manifests or database operations.

Per the current delivery gate, this round remains a local dependency commit.
No branch is pushed and no PR is created unless all later rounds and the final
end-to-end acceptance gate pass.

## Definition of Done

- [x] Paper extraction is serialized by versioned per-field budgets rather
  than cutting a pretty-printed JSON prefix.
- [x] Innovations, experimental gains, historical position, limitations,
  techniques, datasets, baselines and findings have explicit independent
  retention budgets.
- [x] Concept compilation uses a fair per-paper claim capsule under a bounded
  total claim budget; every paper still retains identity even when claim quota
  is exhausted.
- [x] Coverage reports list retained, truncated, dropped and ignored fields and
  included/omitted claim IDs.
- [x] Claim IDs bind paper, field path and full source-value hash.
- [x] Structured generated paragraphs must reference known extraction claims;
  empty or unknown attribution fails closed.
- [x] User notes remain `user_note` origin and are not labeled paper evidence.
- [x] Missing evidence is `unavailable`; validated chunk references are only
  `candidate_evidence`, not verified support.
- [x] Signature v2 binds the actual bounded payload and every material compiler
  contract/generation parameter.
- [x] Shadow artifacts are explicit, atomic and non-overlapping with the
  production Wiki root.
- [x] Existing v1 compiler, FTS, API and freshness contracts remain unchanged.
- [x] Independent review has no unresolved blocking finding.
- [x] The repository unified check passes in the pinned environment.

## Synthetic before/after diagnostic

A fabricated extraction placed a 12,000-character abstract before four tail
sentinels: innovation, experimental gain, historical position and limitation.

| Compiler input path | Innovation | Experiment | History | Limitation |
| --- | ---: | ---: | ---: | ---: |
| Existing paper prompt | 0 | 0 | 0 | 0 |
| Existing concept snippet | 0 | 0 | 0 | 0 |
| v2 shadow paper plan | 1 | 1 | 1 | 1 |

The v2 paper plan was 5,205 prompt characters and built in 0.424 ms. A 50-paper
concept plan produced a 50,201-character prompt, retained at least one claim in
every capsule and built in 11.790 ms. These are single-run synthetic diagnostics,
not stable production benchmarks and not measurements of model answer quality.

Targeted verification:

```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m unittest \
  backend.tests.test_wiki_claims \
  backend.tests.test_paper_evidence_service \
  backend.tests.test_ask_quality_eval -v
```

Result: 34/34 passed. Tests cover field-tail retention, stable signatures,
candidate evidence boundaries, tamper rejection, 20-paper fairness, unknown and
empty attribution rejection, explicit shadow writes, production-root overlap
rejection and the unchanged v1 prompt contract.

Unified repository check:

```bash
NODE_BIN=/Users/vince/.local/lib/node-v24.14.1-darwin-arm64/bin/node \
  ./scripts/check.sh
```

- Native startup fixtures: 29/29 passed.
- Bounded isolated backend: 139/139 passed.
- Frontend unit tests, TypeScript and Vite build: passed.
- Full ESLint: 0 errors, 0 warnings.
- Mock browser lifecycle regressions: 7/7 passed.

The final run followed fixes for omitted-claim allowlisting, nested symlink
escape, cross-paper candidate evidence, critical-field fairness and compile-plan
signature/path traversal. It used only disposable source/data snapshots and did
not run the real application.

## Limitations and next gate

- Field retention does not prove the extraction is correct.
- Paragraph lineage proves attribution to structured extraction claims, not
  entailment by a PDF page.
- The shadow compiler accepts already-produced structured synthesis; it does
  not call a live model in this round.
- Candidate evidence IDs are existence-checked against a fully validated
  manifest, but remain candidates because no quote/span entailment is checked.
- The current fair budget bounds claim text, while paper IDs/titles and prompt
  framing add small overhead beyond the 48,000-character claim budget.
- No real shadow compilation or private-human judgment has been performed.
- Production adoption must wait for later retrieval/citation evaluation and a
  final switch decision. Any real recompilation remains blocked on the approved
  cross-asset backup and restore rehearsal.
