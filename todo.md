# Knowra — Roadmap / TODO

> Aligned to Andrej Karpathy's "LLM Knowledge Bases" blueprint.
> Status legend: ✅ done · 🟡 partial · ❌ not started · �doing

Karpathy blueprint components & where Knowra stands:

| Component | Status | Notes |
|---|---|---|
| Data ingest → LLM compiles wiki .md | ✅ | PDF only (no web/repo ingest) |
| Backlinks / categorization / index.md | ✅ | alias frontmatter backfilled — `[[paper:N]]`/`[[concept:N]]` resolve in Obsidian |
| IDE = Obsidian | ✅ | `data/.obsidian/`; alias links + lint-report.md viewable in vault |
| Q&A agent against wiki | ✅ | `ask_agent.py` — chat.completions / Responses / local paths |
| Output + file back into wiki | ✅ | `synthesis_concept_service` + Marp/report export (`wiki_output_service`) |
| Linting / health checks | ✅ | `wiki_lint_service` — stubs / merge / missing-crosscut / followups + report |
| Search engine as LLM tool | ✅ | `search_wiki` |
| Multi-provider / local CLI | ✅ | `model_gateway/` (openai / compatible / codex_cli) — exceeds blueprint |
| Synthetic data + finetune | ❌ | deferred, expected |

## Backlog (priority order)

### P0 — Obsidian alias frontmatter  �doing
- Add `aliases` to every wiki .md so `[[paper:N]]` / `[[concept:N]]` /
  title / slug resolve to real notes in Obsidian (backlink graph works).
- Approach: inject `aliases` in `compile_paper_page` / `compile_concept_page`
  meta dicts + a one-shot **no-LLM backfill** over existing files +
  `index.md`.
- Acceptance: open vault in Obsidian → graph view shows backlinks;
  clicking `[[paper:9]]` in a concept page navigates to the paper note.

### P2 — Output formats beyond markdown  �doing
- Ask answer → export as **Marp slides** and/or **structured report**,
  filed back into the wiki (`data/wiki/decks/`, `data/wiki/reports/`)
  so it's viewable in Obsidian and re-queryable by the agent.
- matplotlib/code-exec output deferred (needs sandbox) — note only.
- Acceptance: from the Ask drawer, "导出 → 幻灯/报告" writes a wiki
  file and returns its path; file is alias-tagged so the agent can
  find it next query.

### P1 — Wiki linting / health-check agent  ✅ DONE
- `services/wiki_lint_service.py`: rule layer (stub / cosine-merge /
  missing-crosscut, ~0 token) + ONE bounded LLM call for judgment +
  follow-up questions. New `wiki_lint` model-gateway task.
- Endpoints: `POST /api/wiki/lint/run`, `GET /api/wiki/lint/status`,
  `GET /api/wiki/lint/report`. Writes `data/wiki/lint-report.md`
  (alias-tagged, Obsidian-resolvable links).
- UI: `WikiLintModal` (健康检查 button in graph header) with inline
  apply actions: stub→recompile concept, merge→reject duplicate,
  followups→copy to clipboard for Ask.
- Follow-up ideas (not yet built): a dedicated merge endpoint that
  re-points source_paper_ids instead of reject-only; scheduled/auto
  lint; web-search imputation (overlaps P5).

### P3 — Web ingest + PDF figure extraction  ❌
- Accept URLs / Obsidian Web Clipper .md into `raw/`.
- Extract PDF figures → `data/wiki/images/{paper_id}/` + inline refs;
  highest information density in papers is in figures.

### P4 — Proactive agent  ❌
- Surface "questions worth asking" / "concepts worth merging" without
  the user prompting.

### P5 — Web search tool for the agent  ❌
- Give the linter/agent a web-search tool to impute missing data
  (Karpathy uses web searchers in his health checks).

## Notes for future agents
- Model selection goes through `model_gateway/` task bindings, NOT
  the legacy `wiki_compile_model` field directly (legacy fields are
  synced for back-compat). Tasks: paper_extract / paper_chat /
  embedding / wiki_compile / ask_agent / ask_synthesis / promotion_judge.
- Wiki frontmatter round-trips via `wiki_compiler._parse_frontmatter`
  / `_render_frontmatter` — safe to patch a file by parse → mutate
  meta → `_render_frontmatter(meta) + body`.
- Promotion lifecycle: pending / promoted / rejected on KnowledgeNode;
  concept pages compile only for promoted nodes.
