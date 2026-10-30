# Integrated Ask shadow E2E · 2026-10-30

## Scope and outcome

This final local synthetic round is based on dependency commit `8feea63` in
worktree `/private/tmp/knowra-integrated-shadow-e2e-20261030`, branch
`codex/integrated-shadow-e2e-20261030`.

The fixed fixture exercises one uninterrupted offline path:

```text
normalized page text
  -> immutable evidence manifests
  -> claim-attributed paper compile plans
  -> isolated FTS + caller-supplied dense shadow index
  -> bounded CLI/API retrieval plan
  -> explicit claim/span adjudication ledger
  -> evidence-gated answer plan
  -> exact shadow citations or deterministic refusal
```

No model/provider, application server, production Ask route, user data or real
Wiki/index participates in this result.

## Definition of Done

- [x] The long Chinese evolution question has lexical Recall@4 `0.00` and
  hybrid Recall@4 `1.00` / Precision@4 `1.00` against a four-record synthetic
  oracle (two stage claims and their two source chunks); the overlapping
  manufacturing negative is excluded from the positive top four.
- [x] The successful shadow answer cites both stage claims and both adjudicated
  year claims with exact normalized-text spans and page/source identities.
- [x] CLI and API retrieval produce the same ordered IDs; CLI and API
  production answer adapters return the same no-citation refusal.
- [x] A random no-answer query traverses CLI/API retrieval plan, answer plan,
  shadow renderer and production adapters, returning no candidates/citations.
- [x] A manufacturing negative deliberately overlaps `world`, `action`,
  `model` and `dynamics` and has a non-orthogonal vector; it can be retrieved
  for its own mixed query but cannot cross the world-model evidence gate.
- [x] An unlisted cross-source contradiction blocks an otherwise complete
  shadow answer.
- [x] The production-root sentinel remains empty; all index/data writes are in
  a temporary synthetic directory.
- [x] The integrated regression is part of the maintained backend check.
- [x] Independent review and the unified synthetic repository check complete
  before the local commit.

## Validation evidence

Focused command:

```text
backend/.venv/bin/python -m unittest tests.test_shadow_ask_e2e -v
```

Final results in Python 3.12.13 / Node 24.14.1:

- integrated E2E: 3/3 passed;
- evidence/claim/retrieval/support/E2E dependency set: 52/52 passed;
- unified synthetic check: 29 native startup and 170 backend tests passed;
- frontend unit, type, build and lint passed with zero warnings;
- lifecycle type/build and 7 mocked-browser regressions passed.

Independent review first rejected an orthogonal, unrelated negative and a
partial no-answer path. The final fixture uses overlapping terminology and
non-orthogonal vectors, measures both Recall@4 and Precision@4, excludes the
negative from the positive top four, and carries no-answer through both
retrieval and answer adapters. Final re-review reported no blockers.

The fixture is intentionally transparent: its dense vectors, stage selection
and adjudications are explicit oracle inputs. Therefore it proves contract composition,
provenance preservation, gate behavior and deterministic metrics; it does not
prove that a real embedding model or adjudicator will achieve these scores.

## Final release gate and rollback

This synthetic E2E is necessary but not sufficient for the user's actual
private library. Production Ask remains unchanged and the real corpus has not
been shadow-recompiled into the new evidence/claim/index formats. Consequently
this chain is not eligible for publication under the user's all-or-none rule.

The next step requires explicit approval for the backup and private shadow
validation gate in `docs/ASK-QUALITY-OPTIMIZATION-PLAN.md`: choose a backup
target/encryption/retention/maintenance window, create and restore-verify one
cross-asset generation, then recompile only into a new isolated shadow target.
No cutover follows automatically; the old generation remains the rollback
point. Until that approval and real benchmark pass, no branch is pushed and no
PR is opened.
