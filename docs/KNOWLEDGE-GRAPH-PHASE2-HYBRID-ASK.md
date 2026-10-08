# Knowra 知识图谱 Phase 2：图原生查询与 Hybrid Ask

> 状态：已完成（2026-10-08）  
> 依赖：Phase 1 边级 provenance 已完成

## 1. 本轮目标

让 Ask 能直接使用策展图回答共享概念、关系路径和桥接关系问题，同时保持 Wiki/FTS 作为内容结论的证据层。图只负责确定性检索，不替代 Wiki，不让模型凭节点标题编造论文结论。

## 2. 已落地架构

新增 `backend/services/graph_query_service.py`，提供：

- `find_nodes`
- `get_node`
- `get_neighbors`
- `shortest_path`
- `find_shared_neighbors`
- `find_papers_for_concept`
- `find_concepts_for_paper`
- `explain_edge`

所有查询默认只看策展后的可见图；诊断调用才可显式包含 pending、rejected 和 hidden。查询硬限制为：最大深度 4、最多 50 个结果、单次服务预算 500 ms。结果稳定排序，不返回 embedding，也不把整图送入模型。

Ask 的 OpenAI tool-calling 路径已加入上述 8 个工具；Codex CLI 路径会先做确定性图检索，再执行现有 index/FTS/read_wiki 检索。Ask prompt 明确区分：

- 结构事实：节点、边、路径及 provenance；
- 内容事实：实际读取的 Wiki 文件；
- 综合问题：图缩小范围，再读 Wiki。

trace 继续记录工具、参数、结果摘要和耗时，并新增 `hit_node_ids`。引用使用 `graph_node`、`graph_edge`、`paper`、`concept` 等 kind，前端分别标注“结构节点 / 结构关系 / Wiki 内容”。重复的同参数工具调用会被拦截，每次 Ask 最多 8 轮、16 次工具调用。

## 3. HTTP API

新增只读接口：

```text
GET /api/graph/query/nodes
GET /api/graph/query/nodes/{node_id}
GET /api/graph/query/nodes/{node_id}/neighbors
GET /api/graph/query/path
GET /api/graph/query/shared
GET /api/graph/query/edges/{edge_id}
```

前端 API client 已提供节点查找、邻居和路径方法，节点详情、Ask trace 或后续路径可视化可以复用同一契约。

## 4. 离线评估

运行：

```bash
backend/.venv/bin/python backend/scripts/knowledge_graph_phase2_eval.py --iterations 50
```

产物：`artifacts/knowledge_graph_phase2_eval.json`

| 指标 | Phase 0 | Phase 2 当前结果 |
| --- | ---: | ---: |
| 生产图查询服务 | 无 | 8 个只读操作 |
| 固定结构查询精确通过 | reference evaluator 8/8 | production service 8/8 |
| Ask evidence/tool contract | fixture 6 个样例 | 6/6 |
| 图查询延迟样本 | 无 | 400 |
| 平均 / P50 / P95 | 无 | 0.216 / 0.238 / 0.272 ms |
| 图检索模型 token | 不适用 | 0 |
| 6 个 Ask 样例预期工具调用 | 仅定义 | 总计 10，均值 1.667 |

这些数字是无用户数据、无付费模型调用的确定性离线评估。语言表达层由 mocked Responses tool loop、Codex CLI retrieval 和既有 Ask 回归测试覆盖；它不能替代真实模型的线上主观回答评分，因此不得据此声称端到端 Ask 延迟或语言质量提升。

## 5. 兼容性与降级

- 内容型 Ask 保留原有 `list_wiki_index / search_wiki / read_wiki`。
- 图查询失败会向模型返回结构化错误，不会删除或修改图数据。
- 达到工具步数上限后沿用原有 wrap-up 逻辑，基于已取得证据作答。
- API 只新增路由和字段；已有 `/api/graph`、Ask response 与 Wiki 引用字段保持兼容。

## 6. 验证结果

- Phase 2 定向后端测试：24 项通过（图查询、API、Ask 工具循环、既有图策展）。
- Phase 0 fixture：结构查询 8/8，Ask evidence contract 6/6。
- 完整后端测试：`377 passed, 8 skipped`。
- 前端 `npm run build`：通过。
- 真实本地图谱只读冒烟：节点搜索 56.88 ms、邻居 244.19 ms、四跳路径 221.90 ms，均在 500 ms 服务预算内；没有写数据库。
- `npm run lint`：未通过，仍为仓库既有的 18 个 React hooks/error 与 3 个 warning；本轮新增行没有产生新的 lint 诊断。
- `ruff`、`mypy`：当前 Python 环境未安装，未执行。

## 7. 验收结论

- 固定 fixture 的结果与 Phase 0 reference evaluator 完全一致且顺序稳定。
- Responses tool loop 可执行图工具并返回节点/边 citations；Codex CLI 可获得图检索上下文后继续读取 Wiki。
- 结构引用与 Wiki 内容引用在 API kind、system prompt 和前端展示中均已区分。
- 原有 Wiki-only 工具未删除，完整回归测试通过，内容型检索保持兼容。
- 离线质量、延迟、token 和工具调用数已写入版本化 artifact；没有用离线数字冒充线上模型质量。
