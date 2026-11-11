# Full-paper compile, lightweight iteration — 2026-11-10

## Scope and outcome

This local, unpublished iteration reuses the prior full-page extraction work
and connects a deliberately smaller source-complete path to the production
paper Wiki compiler. It does not restore the expensive claim extraction,
review, split, selected-only review, and render pipeline.

- Codex CLI extraction has no 54,000-character or four-batch prefix cap.
- A successful extraction persists a private, source-bound page manifest.
- A real paper Wiki is compiled in page order from every locally extracted
  source batch. There is no final model merge that could silently discard the
  tail again. Exact user notes are appended without the old 4,000-character
  cut.
- Compile checkpoints reuse completed source batches after an interruption.
- The Review page exposes `原文对照 → 逐页核对原文`; it loads one exact local
  text page at a time and links the same page in the PDF.
- Wiki frontmatter reports source pages, characters, chunks, batches, text
  coverage, semantic-completeness status, and visual-coverage status.

## What “complete” means here

`all_locally_extracted_chunks_presented` is structural text coverage. It does
not prove that the model retained every semantic claim. Figures, visual table
layout, diagrams, and formula layout are explicitly `unsupported`; the UI asks
the user to check those in the PDF. Text-extracted table headers and rows are
kept in the same bounded source stream and the compile prompt tells the model
to preserve their relation.

## Focused verification

- Worktree: `/tmp/knowra-full-paper-compile-fast-20261110`
- Branch: `codex/full-paper-compile-fast-20261110`
- Base: `origin/main@69b2abb1af5b66086e9cd20552cb61f55a535e17`
- Python: independent worktree environment, CPython 3.12.13.
- Backend: 22 focused tests passed for full-source planning, >4 batches, tail
  marker, table header/row input, checkpoint resume, private manifest identity,
  page-only API output, production Wiki wiring, legacy skip behavior, and
  extraction validation.
- Frontend: changed-file ESLint passed with zero warnings; Node 24.14.1
  TypeScript/Vite production build passed.
- `git diff --check` passed.

The full repository check, service startup, browser automation, provider calls,
and real paper processing were intentionally not run in this fast iteration.

## UI availability

The candidate API and Web UI were started on the isolated worktree and the new
interface was browser-verified against the compiled public-paper result. The
release-user path is:

1. start this worktree on an explicit unused port pair, for example
   `BACKEND_PORT=8113 FRONTEND_PORT=5193 ./start.sh`;
2. process the public paper in this worktree's isolated `data/` directory;
3. open `http://127.0.0.1:5193`, choose **回顾**, select the paper, then open
   **原文对照 → 逐页核对原文**;
4. compare the located text and generated Wiki with the linked PDF page.

The live candidate URL is `http://127.0.0.1:5193` while the retained isolated
launcher is running. The public PDF, checkpoints, ledger, database, source
manifest, and generated Wiki remain inside this worktree's ignored `data/`
directory; none is part of the Git commit.

## Public sample and zero-call planner

The old pruned worktrees still contain sanitized acceptance documents and
checkpoint hashes for public *Attention Is All You Need*, but no readable PDF,
manifest, Wiki, or provider-response checkpoint remains there. Those historical
responses are not reusable.

The public arXiv v7 PDF was downloaded from
`https://arxiv.org/pdf/1706.03762` solely into ignored temporary storage. Its
SHA-256 is `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`.
The local parser and current 12,000-character planner reported:

- 15 source pages, 15 successfully parsed, 0 empty, 0 failed;
- 39,587 locally extracted characters and 28 source chunks;
- exactly 5 extraction map batches: pages 1–3, 4–6, 7–9, 10–12, and 13–15;
- exactly 5 paper-Wiki batches with the same page grouping;
- text containing table labels on pages 6, 8, 9, and 10, distributed across
  batches 2–4; the final batch retains pages 13–15.

The deterministic base call count is therefore 5 extraction map calls + 1
final structured extraction call + 5 Wiki calls = 11. Extraction reduction
calls cannot be known before the five map outputs exist: they are zero only if
their rendered notes fit the 32,000-character final-notes bound. Retries and
the final extraction's timeout fallbacks are also data-dependent.

The isolated acceptance run now has an optional durable cross-stage guard. It
is disabled for normal product use and activates only when ledger path, run id,
and limit are supplied together. It reserves before each identified extraction
map/reduce/final/fallback and source-Wiki request, records success or unknown,
blocks replay of every reserved operation, and rejects request 16 before
dispatch. Missing operation ids fail closed while the guard is active. Four
focused guard tests plus the related extraction, Wiki, and gateway tests cover
pre-dispatch persistence, unknown-result replay blocking, and the 15-attempt
limit.

The proposed provider remains the previously approved public-paper path,
`codex-cli/gpt-6-astra`; no embedding call is needed for isolated paper-Wiki
acceptance. This iteration made zero business-model calls and did not read or
change Knowra provider configuration.

## Remaining gate

The user approved this exact public paper, `codex-cli/gpt-6-astra`, and the
executable 15-attempt cap. The remaining acceptance compares the Wiki tail and
table sections against `原文对照` and the PDF. No private library scan or
full-corpus compile is part of that acceptance.

## Completed public-paper run

The isolated run completed within the hard cap. The durable ledger records 11
attempts, all `succeeded`:

- extraction map: 5;
- extraction reduce: 0;
- final structured extraction: 1;
- final fallbacks: 0;
- paper-Wiki batches: 5;
- failures, unknown outcomes, and retries: 0;
- embedding calls: 0.

The resulting paper Wiki is 32,263 bytes and its frontmatter reports 15 source
pages, 39,587 extracted characters, 28 chunks, 5 compile batches,
`all_locally_extracted_chunks_presented`, semantic completeness not claimed,
and visual coverage unsupported.

The isolated release UI was started with Python 3.12.13, Node 24.14.1, API port
8113, and Web port 5193. Real Chrome verification covered:

- the Review page reports 15/15 pages, 28 located chunks, 0 empty and 0 failed;
- page 1 exposes the Transformer method and abstract source text;
- page 8 exposes Table 2's header/rows, including 28.4 and 41.8, plus the
  surrounding result text and the 41.0 prose discrepancy;
- page 13 exposes the attention-visualization caption and raw extracted text;
- Knowledge full-text search for `Transformer big 28.4` returns the generated
  paper Wiki.

This proves local text input coverage and a user-visible comparison path. It
does not prove that every semantic statement survived generation, nor that
figures, table geometry, or formula layout were visually understood.

## PDF preview regression follow-up

The paper follow-up and PDF entry were still implemented, but the layout hid
the whole block below the `2xl` breakpoint and also omitted it whenever
`has_first_page_image` was false. The public acceptance paper has a readable
PDF but no separately rendered first-page PNG, so both conditions applied in
the ordinary candidate window.

The follow-up, PDF entry, and notes now share the ordinary-width side column.
When a first-page PNG is absent, the UI renders an explicit local-PDF placeholder
instead of removing the reader entry. Focused verification in the real React
UI covered the visible placeholder, loading all 15 pages in `PdfSidePanel`,
close, reopen from the placeholder, and Escape-to-close. Single-file ESLint and
`git diff --check` passed. No paper/Wiki recompilation or provider/embedding
request occurred; the durable ledger remains 11/15, all succeeded.
