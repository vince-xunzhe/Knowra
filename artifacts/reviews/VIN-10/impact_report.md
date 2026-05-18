# VIN-10 Impact Report

## User impact
- 单篇论文/概念页可独立重编译；也可按脏标记批量增量编译，不再依赖全量任务。
- 编译完成后同一任务内自动刷新 `data/wiki/index.md` 并重建搜索索引，Ask 更快看到最新标题与摘要。

## Implementation summary
- 增量入口：
  - `POST /api/wiki/papers/{paper_id}/recompile`
  - `POST /api/wiki/concepts/{concept_id}/recompile`
  - `POST /api/wiki/recompile/dirty`（按 freshness missing/stale + 手动 ID）
- 失败记录与重试：
  - `compile_state.failed_items`
  - `POST /api/wiki/retry_failed_item` (`kind=paper|concept`, `item_id`)
- 失败不中断：
  - `recompile/dirty` 中单项失败进入 `failed.items`，后续项继续执行
- 索引增量刷新：
  - `wiki_index.refresh_index()` 生成 deterministic `index.md`
  - frontmatter 增加 `source_digest`，`index_summary` 可判定 stale
- Ask freshness保障：
  - 增量编译路径统一触发 `wiki_index.refresh_index()` + `wiki_search.rebuild_index()`

## Validation
- `cd backend && python3 -m compileall . && python3 -m pytest -q || true` ✅ (`69 passed, 1 warning`)

## Risks
- `failed_items` 仅内存持久化，服务重启后清空。
- 增量 index 文案为模板化输出，质量可能低于 full LLM rebuild。

## Acceptance checklist
- [x] 提供按 `paper_id` / `concept_id` 触发的增量编译入口。
- [x] 失败记录 + 单项重试，且不阻断其它页面产出。
- [x] index 刷新后 Ask 可检索到新页面标题与摘要。
