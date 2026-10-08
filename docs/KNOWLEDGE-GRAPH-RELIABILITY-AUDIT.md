# 知识图谱可靠性与审计增量（4A / 5A / 6A / 3A）

状态：实现与完整回归完成，等待人工审查

实施顺序：4A → 5A → 6A → 3A
分支：`agent/NOISSUE-graph-reliability-audit`

关联 PR：[#15](https://github.com/vince-xunzhe/Knowra/pull/15)（堆叠于阶段 2 的 PR #13）

## 1. Problem

阶段 2 已经让 Ask 能进行图原生检索，但论文处理和图谱演进仍有四类风险：

- 数据库缺列只能在运行到具体 SQL 时才暴露，模型调用可能已经发生；
- 重处理会先删除旧图谱，再进行新抽取和写入，中途失败会让可用知识暂时消失；
- 抽取、实体解析和 ORM 写入没有可序列化边界，难以 dry-run、回滚和比较；
- 图谱缺少稳定导出、差异和基础拓扑健检，模型或 Prompt 升级后的变化难以审计。

## 2. Approach

### 2.1 阶段 4A：处理可靠性

- 启动和 worker 处理前执行 migration preflight，逐表核对 ORM 所需列；缺失时在任何模型调用前停止任务。
- 每篇论文使用原子写入的版本化 JSON manifest，记录：
  - PDF SHA-256；
  - extraction schema、Prompt hash、模型与输出 hash；
  - graph builder、mutation plan hash；
  - Wiki compiler、搜索索引版本；
  - 当前 checkpoint 和失败阶段。
- manifest 是可重建派生元数据，不进入知识数据库，也不参与云同步；丢失或损坏时降级为安全重建，不删除用户内容。
- 图阶段失败后重试会复用已完成且 hash 一致的抽取，不再次调用模型；显式“重新处理”会主动失效 extraction 及下游层。
- 保留任务队列已有的请求幂等键和 job checkpoint，并增加论文级 checkpoint。

### 2.2 阶段 5A：安全写图

- 引入版本化 Pydantic 契约：`SourceDescriptor`、`ExtractionFragment`、`ExtractedNode`、`ExtractedEdge`、`GraphMutationPlan`。
- 不改变现有 extraction Prompt；适配器把原 JSON 规范化成 fragment。
- resolver 只读取数据库快照，统一处理别名、paper-node、共享来源和 deterministic node id，产出 plan，不写数据库。
- plan 明确列出创建、更新、保持、detach、删除、显式 provenance 失效、similarity 替换和新边。
- writer 是唯一写边界；失败时 rollback 整个调用方事务，成功后仍由调用方决定 commit。
- 重处理不再预删旧图。只有完整 plan 可以应用时，才在同一事务中替换该论文拥有的图切片。
- manual 节点不会被 resolver 自动采用或 writer 覆盖。
- `GET /api/graph/mutations/preview/{paper_id}` 提供零模型调用、零写入 dry-run。

### 2.3 阶段 6A：可审计

- `graph.json` 使用 `knowra.graph.v1` 稳定 schema，节点、边、provenance 和论文身份均稳定排序。
- export signature 不依赖节点或边输入顺序。
- diff 输出新增、删除、字段变化以及 `user`、`embedding`、`model_or_rule`、`legacy_or_unknown` 来源分类。
- DOI/arXiv ID 从文件名、显式结构字段或本地 PDF 文本确定性解析；不联网、不额外调用模型；没有稳定 ID 时回退到 PDF SHA-256。
- 导出是只读快照，不替代 SQLite 主数据库。

### 2.4 阶段 3A：结构质量

- 对策展图计算 deterministic connected components、degree、weighted degree、orphan、super-hub 和组件密度。
- `community_id` 在 3A 中等同稳定的 `component_id`；暂不引入 Louvain/Leiden 依赖。
- 分析结果按 graph signature 缓存；节点隐藏、删除或边变化会自然产生新 signature 并失效缓存。
- Wiki lint 增加孤儿、super-hub、多个论文但无 promoted 概念的连通分量规则；报告包含具体证据节点 ID。
- LLM 不参与结构计算，因此基础结构检查为零 token。

## 3. Public API

| API | 用途 | 是否写入 | 模型调用 |
| --- | --- | --- | --- |
| `GET /api/papers/{paper_id}/manifest` | 查看论文级版本链和 checkpoint | 否 | 否 |
| `GET /api/graph/mutations/preview/{paper_id}` | 预览 mutation plan | 否 | 否 |
| `GET /api/graph/export` | 导出稳定 `graph.json` | 否 | 否 |
| `POST /api/graph/diff` | 比较两个快照或快照与当前图 | 否 | 否 |
| `GET /api/graph/analysis` | 获取结构分析；`refresh=true` 跳过缓存 | 仅派生缓存 | 否 |

## 4. Files changed

- 可靠性：`backend/database.py`、`backend/services/pipeline_manifest.py`、`backend/services/task_executor.py`、`backend/routers/papers.py`
- 安全写图：`backend/services/graph_mutation_service.py`、`backend/services/graph_service.py`
- 审计：`backend/services/graph_audit_service.py`、`backend/routers/graph.py`
- 结构质量：`backend/services/graph_analysis_service.py`、`backend/services/wiki_lint_service.py`
- 前端/API：`frontend/src/api/client.ts`、`frontend/src/components/WikiLintModal.tsx`、三种语言翻译
- 测试/eval：`backend/tests/test_graph_reliability_audit.py`、`backend/tests/test_graph_query_api.py`、`backend/tests/test_sqlite_concurrency.py`、`backend/scripts/knowledge_graph_reliability_eval.py`

## 5. Commands run and results

阶段 2 基线：后端 `377 passed, 8 skipped`，专项 `24 passed`，前端生产构建通过。

本阶段专项检查：

```text
.venv/bin/pytest -q tests/test_graph_reliability_audit.py tests/test_graph_query_api.py
9 passed

.venv/bin/pytest -q <事务、图谱、lint、worker 专项集合>
47 passed

npm run build
passed（保留已有的大 bundle warning）

.venv/bin/pytest -q
385 passed, 8 skipped

ruff check .
未执行：当前环境未安装 ruff

.venv/bin/python -m mypy .
未执行：当前虚拟环境未安装 mypy

npm run lint
未通过：18 errors, 3 warnings，均位于本次未修改的既有前端文件；本次修改的
WikiLintModal.tsx 和 client.ts 没有 lint 诊断
```

离线 eval：`artifacts/knowledge_graph_reliability_eval.json`

- 合同检查：10/10；
- 200 次 plan：mean 0.417 ms，P95 0.434 ms；
- 200 次结构分析（合成图）：mean 0.682 ms，P95 0.794 ms；
- 模型调用：0；
- dry-run、幂等、顺序无关 signature、diff、分析缓存全部通过。

真实数据库只读冒烟：schema preflight 通过；稳定导出 2,218 节点、18,759 边；
策展图结构分析 768 节点、3,306 边、1 个连通分量、0 个孤儿、11 个 super-hub；
mutation preview、manifest、图查询和前端访问均通过。已有论文尚未生成 manifest，接口按设计返回
`untracked`；身份会在未来处理或显式重处理时写入，因此本次真实导出的 identity 数为 0。

## 6. Compatibility and cost

- 原有 `/api/graph`、阶段 2 查询和 Ask 契约不变。
- 现有 extraction Prompt 不变，不要求用户重新配置模型。
- 正常处理不会增加 LLM 调用；图失败恢复路径会减少重复抽取 token。
- manifest 和结构缓存只增加少量本地 JSON 磁盘占用。
- 图 plan、diff、connected-components 均是本地确定性计算。

## 7. Risks

- JSON manifest 与知识数据库不是同一个事务；数据库提交后如果 manifest 写入失败，数据仍安全，但下次可能保守地重建并增加一次计算。manifest 永远不能覆盖或删除用户内容。
- 3A 的 `community_id` 是 connected component，不代表语义社区；图规模和真实需求确认前不引入 Louvain/Leiden。
- DOI 从本地 PDF 文本解析时可能遇到参考文献中的 DOI；导出同时保留 identity provenance 和文件 hash，后续可增加 BibTeX/出版商元数据优先级。
- `graph.json` 当前用于导出、diff 和验证，不提供覆盖式导入，避免误覆盖主数据库。

## 8. Follow-up ideas

- 阶段 4B：把 Wiki 现有 source signature 完整并入 manifest，按失效矩阵生成最小任务集和 reconcile 报告。
- 阶段 3B：在真实图上评估 Louvain 与 Leiden，再决定是否加入社区着色、bridge score 和 Dashboard。
- 阶段 6B：增加受校验的导入、GraphML/Mermaid/Cypher；有明确客户端需求后再提供只读 MCP。
- 阶段 6C：引入 BibTeX/LaTeX 确定性解析器，优先使用显式 DOI、引用 key 和 `\\cite{}`。
