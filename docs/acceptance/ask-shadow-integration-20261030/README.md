# Default-off Ask shadow integration · 2026-10-30

## Outcome

This local-only iteration is based on dependency commit `0f9ebf1` in worktree
`/private/tmp/knowra-ask-shadow-integration-20261030`, branch
`codex/ask-shadow-integration-20261030`.

Production Ask now has a deliberately observation-only bridge to one immutable
shadow generation. It is off by default and cannot change the visible answer,
citations or evidence gate. The HTTP/frontend citation contract is extended
with optional exact page/span fields while retaining every old field.

No branch is published because the real backup, restore, private generation
and private benchmark are still awaiting explicit user approval.

## Definition of Done

- [x] With no explicit mode, Ask does not resolve or read a generation path and
  produces exactly the pre-existing trace/answer/citations.
- [x] The only accepted non-off mode is `observe`; no `enforce` or production
  eligibility setting exists.
- [x] An observe root must be absolute, physical, outside and not an ancestor
  of live `data/`; manifest/index traversal and symlinks fail closed.
- [x] A bounded generation manifest must bind the index source digest, dense
  digest, model revision, actual file SHA-256 and byte size, and declare
  `production_eligible=false`.
- [x] The index is opened through the existing SQLite immutable read path;
  observation creates no files and calls no model or embedding provider.
- [x] Pre/post file identity checks and retrieval share a five-second observer
  deadline; trace budgets expose actual integrity I/O and total observer time.
- [x] Malformed or re-signed SQLite records, vectors and JSON fail closed and
  cannot escape into or replace the existing Ask result.
- [x] CLI/API adapters share candidate ordering, source locators, coverage and
  hard search/result/serialization/latency budgets.
- [x] Trace telemetry contains only status, counts, coverage and budgets: no
  candidate text, quote, prompt or question.
- [x] The old answer and evidence citations remain unchanged; candidate records
  are never promoted to answer citations.
- [x] Router/frontend citation fields support optional page, section, exact
  normalized-text quote, page/chunk spans, claim/support/evidence/manifest IDs,
  source hash, relationship and assessor revision from the real renderer shape.
- [x] Quotes are rendered as React text, not HTML, and backend field sizes are
  bounded.
- [x] All checks use synthetic temporary data; live papers, Wiki, databases,
  configuration and running services are untouched.

## Configuration contract

Default:

```text
KNOWRA_ASK_SHADOW_MODE=off
```

Explicit observation after a separately approved generation exists:

```text
KNOWRA_ASK_SHADOW_MODE=observe
KNOWRA_ASK_SHADOW_ROOT=/absolute/path/to/one/immutable/generation
```

The root must contain `generation.json` plus its bound index. These variables
are intentionally not added to `.env`, startup defaults or user configuration.
Invalid/missing/tampered generations emit compact `unavailable` telemetry and
cannot break or replace the existing answer.

`generation.json` binds `index_sha256` and `index_bytes` in addition to corpus,
dense-index and model metadata. This detects content drift within a selected
generation; it is deliberately not an external signature or publisher trust
anchor and cannot make a shadow artifact production-eligible.

## Validation and limitations

Focused results after independent-review fixes:

- 10 shadow-integration + 2 router tests passed;
- 68 Ask/router/evidence/orchestration/retrieval/claim tests passed;
- frontend production build and full ESLint passed with zero warnings.

Final unified validation passed on Python 3.12.13 and Node 24.14.1:

- 29 native-startup fixture checks;
- 180 isolated backend tests;
- frontend unit tests, TypeScript, production build and full ESLint with zero
  warnings;
- lifecycle TypeScript/build and 8 isolated browser regressions, including the
  exact-citation UI, with no external requests or policy violations.

The first unified attempt was blocked by the execution sandbox's loopback
socket policy. The same check then passed in the approved isolated loopback
environment. Independent review reproduced the tamper/symlink/failure cases
and finished with no blocking findings.

Remaining limits are intentional and material:

- no real generation builder or private generation has run;
- production observation currently performs lexical retrieval unless a caller
  explicitly supplies a version-matched local query vector; no runtime local
  embedding provider is installed or called;
- observations do not become synthesis context or answer citations;
- the claim-support adapter still requires a future trusted production
  adjudication receipt and therefore refuses production output;
- exact citation UI is exercised with synthetic API data, not a live answer;
- the 5-second observer budget is a safety ceiling, not a demonstrated private
  p95; real index size and latency remain unmeasured until private shadow work;
- no visual screenshot is published while the all-or-none PR gate remains
  blocked; the real publication round must capture one after private E2E passes.

Rollback is removal of the bridge import/hook and optional citation fields.
Leaving the two environment variables unset is already a zero-storage-read,
zero-behavior-change rollback.
