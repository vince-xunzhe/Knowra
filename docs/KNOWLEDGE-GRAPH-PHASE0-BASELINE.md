# Knowra 知识图谱 Phase 0 基线

> 状态：Completed  
> 完成日期：2026-10-08  
> 基线产物：`artifacts/knowledge_graph_phase0_baseline.json`  
> 合成评估集：`backend/tests/fixtures/knowledge_graph_phase0.json`

## 1. 目的与隐私边界

本基线用于后续知识图谱阶段的自动比较，不改变现有论文、图谱、Wiki、搜索索引或模型配置。

基线产物只包含：

- 聚合节点、边和分布指标；
- 聚合延迟；
- 数据库字段名和 migration 文件名；
- API 字段契约；
- 合成 fixture 的路径和 SHA256。

明确不写入：

- 论文标题、文件名和路径；
- 节点 ID、标题、正文、标签和 embedding；
- Wiki 正文；
- 原始 Prompt、模型回答或凭证。

FTS 性能测试读取现有 Wiki，但使用临时 SQLite 数据库，完成后删除临时文件，不修改 `data/wiki_search.sqlite`。

## 2. 可重复采集

生成基线：

```bash
.venv/bin/python backend/scripts/knowledge_graph_baseline.py \
  --benchmark-fts \
  --fts-trials 3
```

默认输出：

```text
artifacts/knowledge_graph_phase0_baseline.json
```

在后续阶段生成新基线并自动比较：

```bash
.venv/bin/python backend/scripts/knowledge_graph_baseline.py \
  --benchmark-fts \
  --compare-to artifacts/knowledge_graph_phase0_baseline.json \
  --output artifacts/knowledge_graph_current.json
```

比较结果写入新产物的 `comparison.metric_deltas`，并单独报告 API contract 是否变化。

## 3. 图谱指标

采集时工作区 revision 为 `71df1cc`，分支为 `agent/NOISSUE-recommendation-dedup`；采集器同时记录 `source_worktree_dirty`，用于提示基线是否包含未提交实现。

| 指标 | 全量图 | 默认策展图 |
| --- | ---: | ---: |
| 节点 | 2,169 | 763 |
| 边 | 19,509 | 3,385 |
| 孤儿节点 | 5 | 4 |
| 平均度 | 17.989 | 8.873 |
| 连通分量 | 6 | 5 |
| 最大连通分量节点 | 2,164 | 759 |

完整性指标：

| 指标 | 结果 |
| --- | ---: |
| 断裂边引用 | 0 |
| 自环 | 0 |
| 隐藏节点 | 3 |
| 重复边行 | 32 |

重复边是后续阶段需要保持可见的现状指标，不在 Phase 0 自动修复，以免改变用户图谱。阶段 1 明确关系 provenance 和去重所有权后再处理。

关系分布：

| 关系 | 数量 |
| --- | ---: |
| `similar` | 15,652 |
| `evaluated_on` | 917 |
| `compared_to` | 890 |
| `builds_on` | 724 |
| `trained_on` | 608 |
| `uses` | 558 |
| `belongs_to` | 160 |

## 4. 延迟基线

模型调用延迟来自 `llm_calls` 中已存在的遥测，只统计成功且有 `latency_ms` 的记录；本阶段没有为采集基线额外调用模型。

| 任务 | 成功/总调用 | P50 | P95 | 说明 |
| --- | ---: | ---: | ---: | --- |
| `paper_extract` | 560 / 766 | 118.591 s | 254.052 s | 单次论文抽取模型调用，不等同于完整 paper job |
| `wiki_compile` | 1,544 / 1,646 | 131.006 s | 173.792 s | 单次 Wiki 编译模型调用 |
| `ask_agent` | 94 / 95 | 0 ms | 0 ms | 92 条历史记录写入了 0，不能作为可靠端到端分位数 |

`ask_agent` 中只有 2 条正延迟样本，正样本 P50 为 89.286 s。后续比较应同时看 `zero_latency_samples` 和 `positive_successful_latency`，在遥测补齐前不得用全量 P50 声称 Ask 变快。

后台任务数据库当前只有一个完整 pipeline 生命周期样本。它从 job 创建到最终更新共约 10.49 小时，包含排队、模型等待和所有阶段，不应解释为单篇处理耗时。扫描任务 7 个样本，P50 为 504 ms、P95 为 801 ms。

FTS 重建使用 772 个编译文档（146 个 paper、626 个 concept）和临时数据库运行 3 次：

| 指标 | 结果 |
| --- | ---: |
| 平均 | 491.599 ms |
| P50 | 474.895 ms |
| P95 | 532.615 ms |
| 最大 | 539.028 ms |

机器负载和磁盘缓存会影响绝对值，后续应在相同命令、相同文档规模下比较。

## 5. 结构查询与 Ask 固定样例

合成 fixture 不包含用户数据，覆盖以下结构查询：

1. 单节点邻居；
2. 两节点最短路径；
3. 两篇论文的共享概念；
4. 某概念关联的论文；
5. 不连通节点；
6. 默认策展视图下的重名节点；
7. 别名查询；
8. 隐藏节点/别名隔离。

固定 Ask 样例共 6 个，覆盖：

- 内容型：概念定义、数据集用途；
- 结构型：共享概念、连接路径；
- 混合型：先通过图缩小范围再读 Wiki、证据不足时明确拒答。

Phase 0 的 reference evaluator 已验证 fixture 内所有期望路径和结果自洽。Phase 2 实现图查询服务时，应直接复用该 fixture，并让生产查询结果与固定期望比较。

## 6. API 兼容契约

`GET /api/graph` 顶层必须继续包含：

```text
nodes, edges
```

冻结的节点必需字段：

```text
id, title, content, node_type, origin, hidden,
concept_candidate, publishable_concept,
promotion_status, promoted_by, promotion_reason,
last_promotion_eval_at, tags, source_paper_ids,
paper_id, concept_id, created_at
```

冻结的边必需字段：

```text
id, source, target, relation_type, weight, created_at
```

测试使用合成 SQLite 数据库调用真实 `get_graph_data()`，并对字段集合做精确断言。阶段 1 可以新增可选 provenance 字段，但不得静默删除或重命名上述字段。

注意：当前 TypeScript `GraphEdge` 没有声明后端已返回的 `created_at`，运行时不会报错，但后续更新类型时应保持新增字段向后兼容。`source_paper_ids` 在后端为 opaque string，部分前端类型仍写成 `number[]`；这是已记录的类型债务，不在 Phase 0 扩大改动范围。

## 7. 本地与云端 Schema 映射

| 范围 | 本地 SQLite | 云端 Postgres/Supabase | 状态 |
| --- | --- | --- | --- |
| 节点表 | SQLAlchemy `KnowledgeNode` + `database._migrate()` | `0003_knowledge.sql` | 字段语义对应 |
| 边表 | SQLAlchemy `KnowledgeEdge` | `0003_knowledge.sql` | 核心字段对应 |
| 主键 | SQLite `TEXT` UUID | Postgres `UUID` | 表达不同，语义一致 |
| JSON | SQLite SQLAlchemy JSON/TEXT | Postgres `JSONB` | 序列化兼容 |
| 用户隔离 | nullable `user_id`，桌面本地使用 | `user_id NOT NULL` + RLS | 云端约束更严格 |
| 边一致性 | 应用层维护 | FK + `check_edge_user_consistency` trigger | 云端额外写时保护 |
| `updated_at` | 本地图表当前不保存 | 云端字段 + touch trigger | 明确的部署差异 |
| 边唯一性 | 由应用写入/修复逻辑维护 | `(user_id, source_id, target_id, relation_type)` UNIQUE | 阶段 1 去重需同时验证 |

当前本地 `_meta` 已记录 `multitenant_v1`，设置时间为 `2026-06-01 16:00:57`。知识图谱云端基准 migration 是 `supabase/migrations/0003_knowledge.sql`。任何阶段 1 schema 变更必须同时修改：

1. `backend/models.py`；
2. `backend/database.py` 幂等 SQLite migration；
3. `backend/multitenant_migration.py` 中重建表结构；
4. 新的 Supabase migration；
5. 本地/云端同步 payload 与测试。

## 8. 验证结果

### 已通过

```text
.venv/bin/python -m pytest -q
347 passed, 8 skipped, 31 warnings

.venv/bin/python -m pytest -q backend/tests/test_knowledge_graph_baseline.py
3 passed, 1 warning

cd frontend && node --test tests/polling.test.mjs
5 passed

cd frontend && npm run build
TypeScript build and Vite production build passed
```

生产构建有现存的 bundle 大小提示，后端测试有 SQLAlchemy/Pydantic/JWT 警告；均未造成失败，本阶段没有掩盖或修改这些无关告警。

### 当前工具链缺口

```text
.venv/bin/python -m ruff check .
失败：No module named ruff

.venv/bin/python -m mypy .
失败：No module named mypy

bash evals/run_eval.sh --split val --output artifacts/latest_eval
未运行：仓库没有 evals/ 目录
```

这些结果按 Phase 0 要求如实冻结。阶段 0 的新增 Python 代码已由 targeted tests、完整 pytest 和导入执行覆盖；若后续把 lint/typecheck/eval 作为强制门禁，需要先补充开发依赖、配置和评估目录，不能把“命令不存在”记录成成功。

## 9. Phase 0 结论

- 匿名基线可重复生成，并可以与后续版本自动比较。
- 合成结构查询和 Ask 样例不依赖真实论文或用户数据。
- API 字段契约已有自动化测试保护。
- 本地/云端 schema 与 migration 边界已记录。
- 后端完整测试、前端轮询测试和生产构建通过。
- 当前图谱没有断裂边和自环；32 条重复边作为阶段 1 的已知基线保留。

