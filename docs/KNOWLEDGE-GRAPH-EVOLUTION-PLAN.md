# Knowra 知识图谱演进计划

> 状态：Active（阶段 0、1 已完成）  
> 目标：在保留 Knowra「论文领域模型 + 概念策展 + Wiki/Ask 闭环」的前提下，分阶段吸收 Graphify 在关系可解释性、图原生查询、结构分析和增量维护方面的设计。  
> 实施方式：每个阶段单独建 Linear issue、分支和 PR；上一阶段达到验收标准后再进入下一阶段。

## 1. 背景与结论

Knowra 和 Graphify 都生成知识图谱，但两者的核心定位不同：

- Knowra 是面向论文研究的知识工作台。SQL 图谱承担结构化知识与策展状态，编译后的 Wiki 是主要阅读和 Ask 检索层。
- Graphify 是面向代码库和混合文件的图原生索引器。图本身是主要查询载体，并强调确定性抽取、边级溯源、路径查询、社区分析和文件级增量更新。

本计划不以替换 Knowra 的现有架构为目标，而是选择性引入以下能力：

1. 每条关系可解释、可追溯、可评估。
2. Ask 可以直接查询图结构，而不只读取 Wiki。
3. 用图结构聚类补充 embedding 相似度。
4. 用统一 manifest 管理 PDF、抽取、图谱、Wiki 和搜索索引之间的派生关系。
5. 逐步统一不同知识来源的抽取与写图契约。

## 2. 保留与不做

### 2.1 必须保留

- SQLite/Postgres 继续作为图谱和业务状态的主存储。
- 保留 `paper / technique / dataset / problem_area / concept` 领域模型。
- 保留 embedding；显式关系、结构聚类和向量相似关系并存。
- 保留 concept promotion、人工编辑、Wiki 编译、Wiki lint 和 Ask 回填闭环。
- 保持本地优先，并兼容现有云同步和多租户字段。
- 所有结构化 LLM 输出必须继续经过解析和校验后才能写库。

### 2.2 当前非目标

- 不用 `graph.json`、NetworkX 或图数据库替代关系数据库。
- 不一次性复制 Graphify 的多语言代码解析器。
- 不把领域节点全部泛化为通用 `entity`。
- 不在第一阶段引入 Neo4j、FalkorDB 等新的运行时依赖。
- 不因为本计划改变现有验证集、推荐评估集或用户数据。
- 不把 Git hook 作为论文知识库的主要更新触发机制。

## 3. 当前架构基线

当前主链路是：

```text
PDF / 用户笔记
    ↓
论文结构化抽取 JSON
    ↓
knowledge_nodes / knowledge_edges
    ↓
候选概念 promotion
    ↓
论文页 / 概念页 Wiki 编译
    ↓
SQLite FTS5
    ↓
Ask Agent 检索、回答和概念回填
```

当前主要限制：

1. `KnowledgeEdge` 只有关系类型和权重，无法稳定回答「为什么有这条边」。
2. Ask 主要使用 `index.md + FTS5 + read_wiki`，图谱还不是直接查询工具。
3. `similar` 边能表达文本相似，但缺少基于全图结构的社区、桥接点和异常分析。
4. PDF、抽取结果、图谱、Wiki、FTS 之间已有局部签名和重建逻辑，但没有统一派生账本。
5. 不同入口的写图行为还没有统一成可校验的 extraction fragment 和 mutation plan。

## 4. 目标架构

```text
Source
  ├─ PDF
  ├─ Notes / Ask synthesis
  └─ Future: BibTeX / LaTeX / repository metadata
        ↓
Extractor
        ↓
Validated ExtractionFragment
        ↓
Entity Resolver
        ↓
GraphMutationPlan
        ↓
Transactional Graph Writer
        ├─ nodes
        ├─ edges + provenance + evidence
        └─ graph version / manifest
              ↓
        Graph Query Service
          ├─ neighbors / shared neighbors
          ├─ paths / edge explanation
          └─ communities / bridges / orphans
              ↓
        Wiki Compiler + FTS
              ↓
        Hybrid Ask
          ├─ graph narrows the scope
          └─ Wiki provides readable evidence
```

## 5. 分阶段路线图

| 阶段 | 主题 | 可独立交付的结果 | 前置依赖 |
| --- | --- | --- | --- |
| 0 | 基线与契约冻结 | 记录当前指标、固定兼容性和评估样例 | 无 |
| 1 | 边级溯源与可信度 | 用户可以查看每条边的来源、证据和生成方式 | 阶段 0 |
| 2 | 图原生查询与 Hybrid Ask | Ask 能调用邻居、路径、共享概念等图工具 | 阶段 1 |
| 3 | 结构聚类与 Wiki lint | 社区、桥接节点和孤儿检测进入图谱与健检 | 阶段 2 |
| 4 | 统一 manifest 与精确失效 | 每层派生物可判断是否需要重建 | 阶段 1，可与阶段 3 并行设计 |
| 5 | ExtractionFragment 与 MutationPlan | 抽取、实体解析和写库边界统一 | 阶段 1、4 |
| 6 | Diff、导出与确定性来源 | 支持图谱差异、标准导出及 BibTeX/LaTeX 等扩展 | 阶段 5 |

---

## 6. 阶段 0：基线与契约冻结

### 目标

在修改数据模型前保存可对比的行为、性能和质量基线，避免只针对单个样例优化。

### 工作项

- [x] 记录当前节点数、边数、关系类型分布、孤儿节点数和平均度。
- [x] 记录当前论文处理、Wiki 编译、FTS 重建和 Ask 的延迟基线。
- [x] 建立一组固定的结构查询样例，至少覆盖：
  - 单节点邻居；
  - 两节点最短路径；
  - 两篇论文的共享概念；
  - 某概念关联的论文；
  - 不连通节点；
  - 重名、别名和隐藏节点。
- [x] 建立一组固定 Ask 样例，区分内容型、结构型和综合型问题。
- [x] 冻结当前 API 响应中的节点、边字段兼容要求。
- [x] 记录当前数据库迁移版本和云端 schema 对应关系。

### 输出

- 图谱基线 JSON 或 Markdown 报告，保存到 `artifacts/`，不提交原始用户数据。
- 自动化测试 fixture 使用合成数据，不使用 `data/raw/`。

### 验收标准

- 后续阶段可以自动比较 baseline 和新结果。
- 评估样例不依赖真实私人论文内容。
- `pytest -q`、`ruff check .`、`python -m mypy .` 的现状结果已记录。

### 完成记录（2026-10-08）

- 匿名聚合基线：`artifacts/knowledge_graph_phase0_baseline.json`。
- 可重复采集与比较：`backend/scripts/knowledge_graph_baseline.py`。
- 合成评估 fixture：`backend/tests/fixtures/knowledge_graph_phase0.json`。
- API contract、隐私边界和 fixture 自洽测试：`backend/tests/test_knowledge_graph_baseline.py`。
- 完整结果、schema 映射和工具链缺口：`docs/KNOWLEDGE-GRAPH-PHASE0-BASELINE.md`。
- 验证：后端 `347 passed, 8 skipped`；前端轮询 `5 passed`；生产构建通过。
- `ruff`、`mypy` 未安装，仓库没有 `evals/`；已记录为工具链缺口，没有误报成功。

---

## 7. 阶段 1：边级溯源与可信度

### 目标

任何可见边都能够回答：谁创建的、基于什么证据、可信度多少、应在何时失效。

### 建议数据模型

在 `KnowledgeEdge` 和对应 Supabase schema 中增加：

| 字段 | 建议类型 | 含义 |
| --- | --- | --- |
| `origin` | string | `explicit / inferred / embedding / manual / legacy` |
| `confidence` | float nullable | 关系可信度，范围 `0..1` |
| `source_paper_id` | string nullable | 主要来源论文 |
| `source_field` | string nullable | 如 `techniques`、`builds_on`、`datasets` |
| `evidence` | text nullable | 支持关系的短证据，不保存无界全文 |
| `metadata` | JSON nullable | 模型、规则、相似度或辅助来源 |
| `extractor_version` | string nullable | 生成这条边的抽取器/schema 版本 |

### 兼容策略

- 现有 `similar` 边回填为 `origin=embedding`，`confidence` 使用已有相似度或保持空值。
- 现有 `curated_link` 回填为 `origin=manual`。
- 无法可靠判断的历史语义边回填为 `origin=legacy`，不得伪造证据。
- 新字段先 nullable，完成回填和兼容验证后再评估是否增加约束。
- API 只新增字段，不删除或重命名当前字段。

### 工作项

- [x] 新增本地数据库幂等迁移和 Supabase migration。
- [x] 将 `_add_edge` 改为接收结构化 edge spec，禁止新调用点丢失 origin。
- [x] 论文抽取关系写入 `source_paper_id` 和 `source_field`。
- [x] embedding、人工概念、Ask synthesis 和修复任务写入正确 origin。
- [x] 更新图谱 API 序列化和云同步 payload。
- [x] 前端增加边详情或关系检查面板。
- [x] 为历史数据实现安全、可重复执行的 backfill。
- [x] 增加重复边合并规则，明确不同 origin 是否允许并存。

### 验收标准

- 新建边的 provenance 覆盖率为 100%。
- 历史边全部有明确 origin，至少为 `legacy`。
- 用户可从图谱 UI 查看关系来源和证据。
- 重处理单篇论文只替换该论文生成的可失效边，不删除人工边。
- SQLite 和 Postgres 行为一致，多租户边不能跨用户引用节点。
- 旧版前端读取新增字段后不发生破坏性变化。

### 重点测试

- 显式、推断、embedding、manual、legacy 五种边。
- 单篇重处理后的 stale edge 清理。
- 两篇论文共同支持同一条边时的 provenance 保留。
- 人工边不被自动重建删除。
- schema migration 幂等性和云端 RLS/触发器。

### 完成记录（2026-10-08）

- 结构化写入与多来源合并：`backend/services/edge_provenance.py`。
- SQLite 幂等迁移、旧 multitenant 重建路径和 Supabase migration 已同步。
- 论文抽取、embedding、Ask synthesis、人工关联及修复路径均写入明确 origin。
- 图谱 API、桌面 snapshot、云端 commit/snapshot 和前端类型均为兼容性新增字段。
- 节点详情面板可查看关系来源、置信度、来源数量、论文、字段及短证据。
- 单篇重处理只失效该论文的 explicit contribution；manual/inferred/embedding/legacy 保留。
- 真实数据库 backup 演练：19,653 条边合并为 18,484 条逻辑边，缺失 origin 和同向重复组均为 0。
- 完整设计、用户代价和验证结果：`docs/KNOWLEDGE-GRAPH-PHASE1-PROVENANCE.md`。
- 验证：后端 `369 passed, 8 skipped`；前端生产构建通过；既有 ESLint/toolchain 缺口已如实记录。

---

## 8. 阶段 2：图原生查询与 Hybrid Ask

### 目标

让知识图谱成为 Ask 的真实检索工具，同时保留 Wiki 作为最终证据和阅读层。

### 服务边界

新增独立的 `graph_query_service`，首批只提供确定性、只读能力：

```text
find_nodes(query, node_type?, limit?)
get_node(node_id)
get_neighbors(node_id, relation_type?, depth=1, limit?)
shortest_path(source_id, target_id, max_depth?)
find_shared_neighbors(node_ids, relation_type?)
find_papers_for_concept(concept_id)
find_concepts_for_paper(paper_id)
explain_edge(edge_id)
```

### 设计约束

- 查询默认只使用策展后的可见图；诊断接口可显式包含 pending/rejected/hidden。
- 对深度、返回节点数和执行时间设置硬上限。
- 返回稳定 JSON，不把整图塞进 LLM 上下文。
- `explain_edge` 必须使用阶段 1 的 provenance，不让模型自行编造原因。
- 图工具只负责检索，答案仍由 Ask Agent 综合，并读取相关 Wiki 作为内容证据。

### Ask 路由策略

```text
内容型问题 → index / FTS / read_wiki
结构型问题 → graph tools
综合型问题 → graph tools 缩小范围 → read_wiki 获取证据 → 回答
```

### 工作项

- [ ] 实现独立查询服务和单元测试。
- [ ] 将图工具加入 Ask 的 tool schema 和 dispatcher。
- [ ] 更新 Ask system prompt，明确图工具与 Wiki 工具的职责。
- [ ] trace 中记录工具、参数、结果摘要、耗时和命中节点。
- [ ] 答案引用区分「结构路径」和「Wiki 内容来源」。
- [ ] 为常见查询增加 API，供前端详情页复用。
- [ ] 加入超时、结果上限和循环工具调用保护。

### 验收标准

- 固定结构查询 fixture 的结果完全正确且顺序稳定。
- Ask 能回答共享概念、关系路径和桥接关系问题。
- 所有结构性陈述能追溯到节点、边或 Wiki 文件。
- 内容型问题的现有 Wiki 检索能力不退化。
- 记录 baseline 与新版本的回答质量、延迟、token 和工具调用数。

---

## 9. 阶段 3：结构聚类与 Wiki lint

### 目标

在 embedding 相似度之外，引入全图拓扑信号，发现主题社区、桥接概念、孤儿和可能缺失的知识页。

### 算法决策点

实现前先做小型 ADR，比较：

- NetworkX Louvain：依赖和部署相对简单；
- Leiden：社区质量更强，但可能增加原生/可选依赖；
- 仅使用当前 SQL 图数据做基础 connected components/centrality：作为零新增依赖基线。

算法输出必须可复现：固定随机种子、记录算法名和版本。

### 首批指标

- `community_id`
- `degree` / `weighted_degree`
- `bridge_score` 或 betweenness centrality
- `is_orphan`
- `component_id`
- 社区内论文数、概念数和关系密度

### 工作项

- [ ] 实现从策展图生成分析快照，不直接修改原始边。
- [ ] 将分析结果缓存为派生数据，并记录 graph signature。
- [ ] 图谱页面支持按社区着色和筛选。
- [ ] Dashboard 增加社区、桥接节点和孤儿统计。
- [ ] Wiki lint 增加以下规则：
  - 高密度论文社区没有 promoted 概念；
  - 高 bridge score 节点缺少概念页；
  - promoted 概念长期孤立；
  - 异常 super-hub 可能由过度合并造成。
- [ ] LLM 只对规则层候选做判定，不直接决定社区结构。

### 验收标准

- 相同图输入产生相同社区结果。
- 删除或隐藏节点后分析快照能正确失效。
- lint 提议包含结构证据，而不是只给自然语言判断。
- 不修改 validation split；记录规则层 precision 抽样和新旧 lint 指标。
- 中小规模知识库分析时间满足交互或后台任务预算。

---

## 10. 阶段 4：统一 Manifest 与精确失效

### 目标

建立从原始来源到所有派生物的版本链，精确决定应该重跑哪一层。

### 建议 manifest 内容

```json
{
  "source": {
    "paper_id": "...",
    "sha256": "..."
  },
  "extraction": {
    "schema_version": 1,
    "prompt_hash": "...",
    "model": "...",
    "output_hash": "..."
  },
  "graph": {
    "builder_version": 1,
    "node_ids": [],
    "edge_ids": [],
    "signature": "..."
  },
  "wiki": {
    "compiler_version": 1,
    "model": "...",
    "source_signature": "...",
    "filename": "..."
  },
  "search": {
    "index_version": 1,
    "indexed_signature": "..."
  }
}
```

### 失效矩阵

| 变化 | 需要重跑 | 不需要重跑 |
| --- | --- | --- |
| PDF 内容变化 | extraction、graph、wiki、search | 无 |
| extraction prompt/schema 变化 | extraction、graph、wiki、search | PDF 扫描 |
| embedding 模型变化 | embedding、similar edges、结构分析 | 论文语义抽取、普通 Wiki 页面 |
| promotion 状态变化 | curated snapshot、相关 Wiki、search、结构分析 | 论文抽取 |
| Wiki 模型/模板变化 | Wiki、search | graph extraction |
| 用户笔记变化 | 相关 paper Wiki、search | 不相关论文和图边，除非规则明确依赖笔记 |

### 工作项

- [ ] 决定 manifest 使用数据库表还是版本化 JSON；优先保证事务一致性。
- [ ] 定义各层 signature 和版本字段。
- [ ] 将现有 Wiki `source_signature` 纳入统一状态。
- [ ] 后台任务根据失效矩阵提交最小工作集。
- [ ] 增加 reconcile 命令，检测文件、数据库和索引不一致。
- [ ] 增加 dry-run，显示将重建的对象和原因。

### 验收标准

- 任一派生物都可以追溯到输入版本。
- 未变化论文不会重复调用抽取模型。
- 单篇笔记变化不会触发无关论文编译。
- manifest 丢失或损坏时可以安全重建，不丢用户内容。
- worker 中断后可通过 checkpoint 和 manifest 继续执行。

---

## 11. 阶段 5：ExtractionFragment 与 GraphMutationPlan

### 目标

把「抽取」「实体解析」「图谱变更」「事务写入」拆成稳定契约，为后续知识来源扩展和图谱 diff 打基础。

### 建议结构

```python
ExtractionFragment(
    source=SourceDescriptor(...),
    schema_version=1,
    nodes=[ExtractedNode(...)],
    edges=[ExtractedEdge(...)],
)

GraphMutationPlan(
    create_nodes=[...],
    update_nodes=[...],
    create_edges=[...],
    update_edges=[...],
    delete_stale_edges=[...],
    merge_candidates=[...],
)
```

### 工作项

- [ ] 定义 Pydantic schema 和版本策略。
- [ ] 现有论文 extraction 适配为 fragment，不立刻改变 Prompt 输出格式。
- [ ] 将别名匹配、去重和 paper-node 规则移入 resolver。
- [ ] resolver 生成 mutation plan，不直接提交数据库。
- [ ] writer 在单事务中应用 plan，并产出变更摘要。
- [ ] 提供 dry-run 和调试序列化格式。
- [ ] 保证 manual 节点/边和自动节点/边的所有权边界清晰。

### 验收标准

- 相同 fragment 和数据库快照产生相同 mutation plan。
- plan 可序列化、校验和用于测试断言。
- 应用失败时事务完整回滚。
- 现有 PDF 处理结果与 baseline 在允许差异范围内一致。
- 新 extractor 不需要直接调用 ORM 写图。

---

## 12. 阶段 6：Diff、导出与确定性来源

### 12.1 图谱 Diff

支持比较两个快照或一次 mutation 前后：

- 新增、删除、合并节点；
- 新增、删除、改变类型或置信度的边；
- 社区和 centrality 变化；
- 哪些变化来自模型、Prompt、用户编辑或原始文件。

优先用于模型/Prompt 升级评估，不自动覆盖用户数据。

### 12.2 标准导出

按优先级实现：

1. 稳定 schema 的 `graph.json`；
2. GraphML；
3. Mermaid 局部路径；
4. Cypher 文件；
5. 可选的只读 MCP 图查询服务。

导出物是快照，不是主数据库。

### 12.3 确定性知识来源

首选适合科研工作流的解析器：

- BibTeX：作者、年份、venue、引用 key；
- LaTeX：章节、公式标签、`\\cite{}` 引用；
- DOI/arXiv metadata：稳定的论文身份和引用信息；
- 论文配套仓库：README、包依赖和主要目录，暂不做全语言调用图。

遵循原则：能确定性解析的字段不交给 LLM 猜，语义理解部分才进入模型。

### 验收标准

- diff 可用于比较 baseline 和新图，不依赖节点输出顺序。
- JSON/GraphML 导出可重新读取并保持节点、边及 provenance。
- 确定性解析器有 fixture，离线测试不调用网络或模型。
- 外部来源失败不影响现有 PDF 主流程。

## 13. 评估与观测指标

每个阶段必须同时记录 baseline 和新指标。

### 图谱质量

- provenance 覆盖率；
- 无证据自动边比例；
- 重复节点和重复边数量；
- 孤儿节点比例；
- relation type 分布；
- 人工抽查的关系 precision；
- 重处理前后非预期 graph diff 数量。

### Ask 质量

- 固定问题集正确率；
- 引用或路径可验证率；
- 图工具命中率；
- 平均工具调用步数；
- 平均延迟和 token；
- “材料不足”时是否正确拒绝编造。

### 增量与稳定性

- 单篇变化触发的实际重建对象数；
- 缓存命中率；
- worker 恢复成功率；
- Wiki/FTS/DB reconcile 错误数；
- SQLite 和 Postgres 结果一致性。

## 14. 通用测试要求

每个 PR 至少覆盖：

- 单元测试：纯函数、schema、resolver、查询和算法。
- 集成测试：SQLite 写入、重处理、任务恢复和 Wiki/FTS 更新。
- Postgres/Supabase 测试：migration、RLS、跨用户约束和同步兼容。
- 前端测试：旧 payload 兼容、新字段展示和图操作。
- 回归测试：固定合成论文集，不针对单个真实案例优化。

Canonical commands：

```bash
pytest -q
ruff check .
python -m mypy .
bash evals/run_eval.sh --split val --output artifacts/latest_eval
python evals/summarize_metrics.py artifacts/latest_eval
```

若仓库当前缺少某条命令所需的配置，应在 PR 中如实记录，不以跳过结果代替成功结果。

## 15. 数据迁移与回滚原则

- 所有新增字段先以 nullable 和向后兼容方式上线。
- migration 必须幂等或明确记录只执行一次的边界。
- 不删除原始 extraction、用户笔记、manual 节点或 manual 边。
- 自动重建前保留可恢复快照或可重放来源。
- 新算法结果以派生数据保存，不直接覆盖人工分类。
- 旧版客户端在读取新增 API 字段时必须继续工作。
- 每个阶段都要定义 feature flag 或降级路径，保证可以回到原有 Wiki-only Ask 和无分析快照模式。

## 16. PR 拆分与交付要求

每个阶段应拆成多个小 PR，避免把 schema、后端、前端和算法一次性混在一个超大变更中。推荐顺序：

1. schema/migration；
2. 后端写入与兼容回填；
3. 查询/API；
4. 前端展示；
5. eval、文档和运维工具。

每个 PR 必须写明：

- Problem
- Approach
- Files changed
- Commands run
- Results or metrics
- Risks
- Follow-up ideas

分支使用 `agent/<LinearIssueIdentifier>`，提交使用 `<ISSUE_KEY>: short summary`。完成后只开 PR 并把 Linear issue 移到 Human Review，不自动合并。

## 17. 阶段完成清单

- [x] 阶段 0：基线与契约冻结（2026-10-08）
- [x] 阶段 1：边级溯源与可信度（2026-10-08）
- [ ] 阶段 2：图原生查询与 Hybrid Ask
- [ ] 阶段 3：结构聚类与 Wiki lint
- [ ] 阶段 4：统一 Manifest 与精确失效
- [ ] 阶段 5：ExtractionFragment 与 GraphMutationPlan
- [ ] 阶段 6：Diff、导出与确定性来源

每完成一个阶段，在本节勾选，并在对应章节追加：完成日期、关联 issue/PR、实际指标、偏离计划的决策和下一阶段前置条件。
