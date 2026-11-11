# Concept Wiki bounded-input acceptance

## Scope

This iteration replaces the production concept Wiki compiler's silent
`1500`-character concept-description prefix, `2500`-character per-paper
prefix, and `600`-character per-field prefix with a bounded compilation plan.

- Complete selected narrative and graph extraction fields are split into
  source-labelled 2,000-character chunks without dropping their tails.
- Chunks are round-robin scheduled by paper, then packed into user prompts no
  longer than 14,000 characters.
- Multiple map results are combined through the same 14,000-character bound;
  no single request grows with the whole library.
- Concept-page frontmatter records source documents, characters, chunks,
  batches, map/reduce calls and the prompt bound.
- `text_coverage: complete` means every character in the selected inputs was
  presented to a map call. It does **not** establish semantic completeness.
  Figures and formula layout remain `visual_coverage: unsupported`.

The selected extraction scope covers the current narrative and graph contract
(`title`, category/keywords, contribution/summary/problem/motivation,
principle, innovations, results, history, limitations, techniques, datasets,
baselines, contributions and findings) plus the historical `method` field.
PyTorch sample code and arbitrary unknown fields are not concept-compilation
inputs.

## Targeted verification

Tested from worktree `/tmp/knowra-concept-wiki-bounded-input-20261111` at the
commit recorded in the PR:

```text
python3 -m unittest \
  backend/tests/test_concept_source_compilation.py \
  backend/tests/test_wiki_compiler_reconcile.py -v

Ran 8 tests in 0.004s
OK
```

The tests cover:

- long concept and extraction-field tails surviving chunking;
- every planned chunk appearing exactly once across bounded prompts;
- round-robin representation of multiple papers;
- bounded map and reduce prompts;
- hierarchical synthesis and paper citation propagation;
- the real `compile_concept_page` path writing coverage frontmatter;
- existing duplicate/orphan page reconciliation behavior.

Additional checks:

```text
PYTHONPYCACHEPREFIX=$PWD/.cache/pycache python3 -m py_compile \
  backend/services/concept_source_compilation.py \
  backend/services/wiki_compiler.py \
  backend/tests/test_concept_source_compilation.py
git diff --check
```

The targeted tests used the available Apple Python 3.9.6 interpreter. The
repository's managed Python 3.12.13 environment was not installed in this new
worktree, and the full check suite was intentionally not run under this
iteration's agreed lightweight validation scope.

## Data and cost boundary

- Real business model calls: **0**
- Embedding calls: **0**
- Real compile endpoints: not invoked
- Daily papers, Wiki, database and running services: not read or modified
- Inputs in tests: synthetic only

## User verification after merge

Recompile a concept through the normal Wiki compile action. Open the generated
concept page and inspect its frontmatter. `compiler_policy` should be
`bounded-concept-sources-v1`; `source_characters`, `source_chunks`,
`source_compile_batches`, `source_map_calls`, `source_reduce_calls` and
`source_prompt_character_limit` expose the actual plan. Review the body and
its `[[paper:id]]` citations against the source paper pages. A real model run
is still required to judge semantic retention; this PR proves bounded input
coverage, not model comprehension.

## Not verified here

- Real provider output quality or call cost
- Visual understanding of figures, table layout or formula typography
- Unknown extraction fields outside the documented selected scope
- Full backend/frontend/startup suite
- Deployment or migration of existing Wiki pages
