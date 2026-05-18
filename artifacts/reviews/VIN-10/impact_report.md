# VIN-10 Impact Report

## User impact
- 支持按显式 `paper_ids` / `concept_ids` 做增量重编译，不必触发全量任务。
- 同一任务内自动刷新 `data/wiki/index.md` 并重建搜索索引，Ask 可更快看到最新标题与摘要。

## Implementation summary
- 增量入口：
  - `POST /api/wiki/papers/{paper_id}/recompile`
  - `POST /api/wiki/concepts/{concept_id}/recompile`
  - `POST /api/wiki/recompile/dirty`
  - `POST /api/wiki/recompile/by_ids`（新增）
- 失败记录与重试：
  - `compile_state.failed_items`
  - `POST /api/wiki/retry_failed_item` (`kind=paper|concept`, `item_id`)
- 并发与状态：
  - dirty/by_ids 两条增量路径统一走 `_run_incremental_recompile`，接入 `_try_acquire` / `_tick` / `_finish` 生命周期，避免并发重编译冲突
- Ask freshness：
  - 增量编译结束统一调用 `wiki_index.refresh_index()` 与 `wiki_search.rebuild_index()`

## Validation
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m compileall .` ✅
- `cd /Users/vince/Documents/knowledge-tree-v2-workspaces/VIN-10/backend && python3 -m pytest -q` ✅ (`72 passed, 1 warning`)
- `cd /Users/vince/Documents/knowledge-tree-v2/backend && python3 -m compileall . && python3 -m pytest -q || true` ✅ (`61 passed, 1 warning`)

## Risks
- `failed_items` 仅内存持久化，服务重启后会清空。
- index 增量渲染仍是 deterministic 模板，不追求 LLM full rebuild 的文案质量。

## Acceptance checklist
- [x] 提供按 `paper_id` / `concept_id` 触发的增量编译入口。
- [x] 失败记录 + 单项重试，且不阻断其它页面产出。
- [x] index 刷新后 Ask 可检索到新页面标题与摘要。
