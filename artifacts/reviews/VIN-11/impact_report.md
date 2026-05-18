# VIN-11 Impact Report

## Request
增强 Ask 会话追踪、引用来源展示、概念沉淀判重（支持 force-create）与写入后可检索性。

## Implementation Summary
- Ask 返回结构化 `citations` 并保留 `cited_files` 兼容。
- 会话追踪元数据进入 synthesis：`source_session_id/source_session_title/source_turn_indexes/source_cited_files`。
- synthesis 创建执行重复检测（标题/别名/模型 duplicate id），`force_create` 可覆盖。
- 写入概念与关系后触发 `reconcile_concept_pages_dir` + `wiki_search.rebuild_index`，保证即时可检索。

## Validation
- Command: `cd backend && python3 -m compileall . && python3 -m pytest -q || true`
- Result: `67 passed`, 1 warning (SQLAlchemy deprecation warning)

## Acceptance Criteria Check
- [x] Ask 回答展示可追踪引用来源
- [x] 概念沉淀执行重复检测并允许 force-create
- [x] 写入后可在图谱与 wiki 立即检索

## Risks
- 引用目前主要是文件级来源；段落级可追溯性可继续加强。
- `session_id` 目前为前端会话标识，后续可与服务端会话持久化对齐。
