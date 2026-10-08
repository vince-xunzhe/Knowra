# Knowra 知识图谱 Phase 1：边级溯源与可信度

> 状态：Completed  
> 完成日期：2026-10-08  
> 前置基线：`artifacts/knowledge_graph_phase0_baseline.json`

## 1. 交付结果

阶段 1 将知识边从“只有关系类型和权重”升级为可解释、可同步、可精确失效的关系记录。
图谱 API 保留阶段 0 的全部字段，只新增 provenance 字段；旧前端忽略新增字段即可继续工作。

新增边字段：

| 字段 | 说明 |
| --- | --- |
| `origin` | `explicit / inferred / embedding / manual / legacy` |
| `confidence` | 可选的 `0..1` 置信度 |
| `source_paper_id` | 顶层主要来源论文 |
| `source_field` | 抽取字段或产生关系的功能路径 |
| `evidence` | 最长 1000 字符的短证据，不保存无界全文 |
| `metadata` | 结构化补充信息及完整 `provenance[]` |
| `extractor_version` | 抽取 schema、模型或规则版本 |

SQLAlchemy 的 `metadata` 是保留属性，因此 ORM 内部使用 `edge_metadata`；数据库列、API 和同步协议仍统一使用 `metadata`。

## 2. 来源映射

| 生成路径 | origin | source_field / metadata |
| --- | --- | --- |
| 论文结构化抽取 | `explicit` | `problem_area`、`techniques`、`techniques[].builds_on`、`datasets`、`baselines` |
| Ask synthesis 关系 | `inferred` | `ask_synthesis.related_links`，记录 synthesis 节点和模型/规则版本 |
| 向量相似边 | `embedding` | `embedding`，记录阈值、上下文和 embedding model |
| 人工概念到论文的关联 | `manual` | `manual_concept.paper_ids`，标记 `created_by=user` |
| 无法可靠归因的历史关系 | `legacy` | 不伪造论文来源、证据或模型版本 |

历史 `similar` 可安全判定为 `embedding`，其合法 `weight` 回填为 `confidence`；历史 `curated_link` 回填为 `manual`。

## 3. 多来源合并与失效规则

数据库继续维持“一条逻辑边一行”：

```text
(source_id, target_id, relation_type) -> one edge row
                                      -> metadata.provenance[]
```

当两篇论文或两种生成方式支持同一条边时，各自贡献进入 `metadata.provenance[]`。顶层字段是按 `manual > explicit > inferred > embedding > legacy` 选出的便捷投影，不会覆盖或丢弃其他来源。

单篇论文重处理时：

1. 只移除 `origin=explicit` 且 `source_paper_id` 等于该论文的 contribution；
2. 仍有其他论文或生成方式支持时保留逻辑边并更新顶层投影；
3. 最后一个 explicit contribution 被移除时才删除该自动边；
4. `manual`、`inferred`、`embedding` 和 `legacy` 不会因单篇论文重处理而被误删；
5. 节点确实被删除时，现有端点清理逻辑仍会移除悬空边。

`backfill_and_merge_edge_provenance()` 会幂等补齐来源、合并同向重复边和反向重复 `similar` 边，并在本地 SQLite 建立逻辑边唯一索引。

## 4. Schema 与同步

- 本地模型：`backend/models.py`
- 本地幂等迁移：`database.migrate_edge_provenance_columns()`
- 旧 INT ID 重建迁移：`backend/multitenant_migration.py`
- 云端模型：`backend/cloud_models.py`
- Supabase migration：`supabase/migrations/0010_edge_provenance.sql`
- 同步 schema：`backend/schemas/sync.py`
- 本地 snapshot、云端 commit 和 cloud snapshot 均传递全部 provenance 字段
- 云端 `source_paper_id` 使用 FK `ON DELETE SET NULL`
- 原有 RLS 和租户唯一约束保持不变；`edge_user_consistency_check` 同时校验节点端点和新增来源论文的租户归属
- 云同步在应用层执行同样的节点快照及来源论文归属校验，因此 SQLite 测试后端与 Postgres 拒绝规则一致

桌面本地数据库启动时会先执行只包含加列与保守回填的 schema compatibility preflight；即使检测到跨 API 重启存活的 worker，也不能在该 preflight 之前提前返回。可能删除或合并记录的维护任务仍只在无活动 worker 时运行。2026-10-08 曾因启动顺序错误跳过 provenance 列迁移，导致 3 篇论文处理报 `no such column: knowledge_edges.origin`；现场数据库已幂等补齐，失败论文已重新入队，并增加了 active-worker 启动回归测试。

### 真实数据库副本演练（仅聚合结果）

| 指标 | 结果 |
| --- | ---: |
| 迁移前边数 | 19,653 |
| 迁移后逻辑边数 | 18,484 |
| 合并重复/反向相似边 | 1,169 |
| 迁移后缺失 origin | 0 |
| 迁移后同向重复组 | 0 |

演练没有输出节点名、论文内容、证据文本或 ID，也没有写入正在使用的数据库。

## 5. API 与 UI

`GET /api/graph` 和 `GET /api/nodes/{id}` 的边对象新增七个可选字段，阶段 0 的 `id/source/target/relation_type/weight/created_at` 保持不变。

图谱右侧节点详情的“关联节点”区域现在会显示：

- 关系类型；
- 来源类别；
- confidence 百分比（存在时）；
- provenance 来源数量；
- 每条 contribution 的来源类别、论文、结构化字段和置信度；
- 每条 contribution 的短证据。

人工概念生成的 synthetic edge 也返回完整的 `manual` provenance，因此所有可见边都有可解释来源。

## 6. 用户代价

- **接口兼容性**：纯新增字段，无需新增用户配置，旧版客户端可以忽略。
- **Codex CLI**：仍在现有模型网关和任务框架内运行，没有引入新的模型接口。
- **Token**：阶段 1 不增加模型调用；provenance 来自已有抽取结果、相似度计算和用户操作，因此模型 token 增量为 0。
- **存储/同步**：每条边增加少量标量和 JSON provenance。多论文共同支持时 payload 随 contribution 数量增长，但 evidence 被限制为 1000 字符。
- **首次启动**：需要执行一次本地列迁移和去重；之后只做快速幂等检查。真实数据副本上的 19,653 条边演练在约 1 秒量级完成。

## 7. 验证

自动化测试覆盖：

- 五种 origin 和置信度范围校验；
- 论文抽取、embedding、Ask synthesis、人工关联和修复任务；
- 两篇论文共同支持及单篇重处理；
- 人工边不被自动失效；
- 同向重复边与反向 `similar` 合并；
- SQLite migration 重复执行；
- Supabase 字段、约束、RLS/跨租户触发器契约；
- 图谱 API、local snapshot、cloud commit 和 cloud snapshot；
- TypeScript 类型和生产构建。

验证结果：

| 检查 | 结果 |
| --- | --- |
| 后端完整测试 | `369 passed, 8 skipped` |
| 阶段 1 定向测试 | `26 passed` |
| 前端轮询测试 | `5 passed` |
| 前端生产构建 | 通过 |
| ESLint | 未通过；仓库既有 18 errors / 3 warnings，集中在 React effect、Fast Refresh 和空白字符规则；本阶段新增 provenance UI 可正常 TypeScript 编译 |
| `ruff check .` | 当前虚拟环境未安装 ruff |
| `python -m mypy .` | 当前虚拟环境未安装 mypy |
| Postgres 集成测试 | 无测试 DSN 时按既有规则跳过；migration 静态契约和云端同步测试已通过 |

前端构建仍有既有的大 chunk 警告，不影响构建成功。

## 8. 后续阶段接口

阶段 2 的 `explain_edge(edge_id)` 可直接读取本阶段的顶层字段和 `metadata.provenance[]`，不需要再次修改边表。图查询服务应把 provenance 当作只读解释数据，并继续以短 evidence 为边界，不把论文全文复制到边上。
