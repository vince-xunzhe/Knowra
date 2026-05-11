# LLM 调用地图

这份文档梳理工程里真正会调用模型的地方，以及每一处最终使用的是哪一个配置模型。

先看结论：

- `vlm_model`：负责“读 PDF”
- `wiki_compile_model`：负责“消费已抽取知识再加工”
- `embedding_model`：负责“向量化和相似连接”

默认配置见 [backend/config.py](/Users/vince/Documents/knowledge-tree/backend/config.py:63)：

- `vlm_model = gpt-4o`
- `wiki_compile_model = gpt-4o-mini`
- `embedding_model = text-embedding-3-small`

## 总表

| 功能 | 入口 / 触发点 | 实现位置 | 使用的配置项 | 默认模型 | 底层 OpenAI 调用 |
| --- | --- | --- | --- | --- | --- |
| 论文结构化抽取 | 处理论文 `/api/papers/process` | [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:362) -> [backend/services/vlm_service.py](/Users/vince/Documents/knowledge-tree/backend/services/vlm_service.py:589) | `vlm_model` | `gpt-4o` | `Responses + file_search` 或 `Assistants + file_search` |
| 论文追问 | `/api/papers/{paper_id}/chat` | [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:753) -> [backend/services/vlm_service.py](/Users/vince/Documents/knowledge-tree/backend/services/vlm_service.py:693) | `vlm_model` | `gpt-4o` | `Responses + file_search` 或 `Assistants thread` |
| 图谱节点 embedding | 论文抽取后建图 | [backend/services/graph_service.py](/Users/vince/Documents/knowledge-tree/backend/services/graph_service.py:213) | `embedding_model` | `text-embedding-3-small` | `client.embeddings.create(...)` |
| 论文 Wiki 页编译 | 论文处理完成后的自动编译 / 手动重编译 | [backend/services/wiki_compiler.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_compiler.py:458) | `wiki_compile_model` | `gpt-4o-mini` | `responses.create(...)` 或 `chat.completions.create(...)` |
| 概念 Wiki 页编译 | 论文处理完成后的增量编译 / 手动重编译 | [backend/services/wiki_compiler.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_compiler.py:574) | `wiki_compile_model` | `gpt-4o-mini` | `responses.create(...)` 或 `chat.completions.create(...)` |
| 知识库 `index.md` 重建 | `/api/wiki/index/rebuild` | [backend/services/wiki_index.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_index.py:138) | `wiki_compile_model` | `gpt-4o-mini` | 复用 `_call_llm(...)` |
| Ask 问答 | `/api/wiki/ask` | [backend/routers/ask.py](/Users/vince/Documents/knowledge-tree/backend/routers/ask.py:64) -> [backend/services/ask_agent.py](/Users/vince/Documents/knowledge-tree/backend/services/ask_agent.py:325) | `wiki_compile_model` | `gpt-4o-mini` | `responses.create(...)` 工具循环或 `chat.completions.create(...)` 工具循环 |
| Ask 生成概念前的整理 / 判重 / 关系生成 | `/api/wiki/concepts/from_synthesis` | [backend/routers/ask.py](/Users/vince/Documents/knowledge-tree/backend/routers/ask.py:262) -> [backend/services/synthesis_concept_service.py](/Users/vince/Documents/knowledge-tree/backend/services/synthesis_concept_service.py:350) | `wiki_compile_model` | `gpt-4o-mini` | 复用 `_call_llm(...)` |
| 概念精选中的 LLM 判定 | `/api/promotion/run` 且 `use_llm=true` | [backend/routers/promotion.py](/Users/vince/Documents/knowledge-tree/backend/routers/promotion.py:82) -> [backend/services/promotion_llm.py](/Users/vince/Documents/knowledge-tree/backend/services/promotion_llm.py:164) | `wiki_compile_model` | `gpt-4o-mini` | 复用 `_call_llm(...)` |

## 按模型配置看

### `vlm_model`

职责：读 PDF、做论文抽取、做论文上下文内追问。

实际调用点：

- 论文抽取：[backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:362)
- 论文追问：[backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:753)

底层规则：

- `gpt-5.5 / gpt-5.4 / gpt-5.4-mini` 走 `Responses API`，[backend/services/vlm_service.py](/Users/vince/Documents/knowledge-tree/backend/services/vlm_service.py:51)
- 其他支持 `file_search` 的模型走 `Assistants API`

因此这里的“模型名”由设置页决定，但“API surface”会随模型变化。

### `wiki_compile_model`

职责：不再直接读 PDF，而是消费已经抽取好的 JSON / wiki / 候选概念，再做写作、问答、判定。

实际调用点：

- 论文 Wiki 页编译：[backend/services/wiki_compiler.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_compiler.py:458)
- 概念 Wiki 页编译：[backend/services/wiki_compiler.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_compiler.py:574)
- Wiki 索引重建：[backend/services/wiki_index.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_index.py:138)
- Ask 问答：[backend/services/ask_agent.py](/Users/vince/Documents/knowledge-tree/backend/services/ask_agent.py:325)
- Ask 归纳概念：[backend/services/synthesis_concept_service.py](/Users/vince/Documents/knowledge-tree/backend/services/synthesis_concept_service.py:350)
- 概念精选 LLM 判定：[backend/services/promotion_llm.py](/Users/vince/Documents/knowledge-tree/backend/services/promotion_llm.py:164)

底层规则：

- 统一复用 [backend/services/wiki_compiler.py](/Users/vince/Documents/knowledge-tree/backend/services/wiki_compiler.py:247) 里的 `_call_llm(...)`
- `gpt-5.x` 走 `responses.create(...)`
- 其他模型走 `chat.completions.create(...)`

### `embedding_model`

职责：给知识节点生成向量，并据此补 `similar` 相似边。

实际调用点：

- 新建自动节点时算 embedding：[backend/services/graph_service.py](/Users/vince/Documents/knowledge-tree/backend/services/graph_service.py:213)
- 入口来自论文抽取完成后的建图：[backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:408)

底层调用：

- [backend/services/vlm_service.py](/Users/vince/Documents/knowledge-tree/backend/services/vlm_service.py:801) `client.embeddings.create(input=text, model=model)`

## 每条链路怎么触发

### 1. 论文处理

主链路：

1. `/api/papers/process`
2. `extract_knowledge_from_paper(..., model=cfg["vlm_model"])`
3. `add_nodes_from_paper_extraction(..., embedding_model=cfg["embedding_model"])`
4. `compile_paper_page(..., model=cfg["wiki_compile_model"])`
5. `compile_concept_pages_for_paper(..., model=cfg["wiki_compile_model"])`

对应代码：

- [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:362)
- [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:408)
- [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:433)

### 2. 论文问答

主链路：

1. `/api/papers/{paper_id}/chat`
2. `run_chat_turn(..., model=cfg["vlm_model"])`

对应代码：

- [backend/routers/papers.py](/Users/vince/Documents/knowledge-tree/backend/routers/papers.py:730)

### 3. Ask 页面问答

主链路：

1. `/api/wiki/ask`
2. `run_ask_agent(..., model=cfg["wiki_compile_model"])`
3. agent 在工具循环里多次调用 `list_wiki_index / search_wiki / read_wiki`
4. 最终模型汇总生成答案

对应代码：

- [backend/routers/ask.py](/Users/vince/Documents/knowledge-tree/backend/routers/ask.py:64)
- [backend/services/ask_agent.py](/Users/vince/Documents/knowledge-tree/backend/services/ask_agent.py:341)

注意：Ask 不是“一次提问只调一次模型”。一个复杂问题可能会多轮 `tool call -> tool output -> model 再思考`。

### 4. Ask 存为概念页

主链路：

1. `/api/wiki/concepts/from_synthesis`
2. `analyze_synthesis_concept(..., model=cfg["wiki_compile_model"])`
3. 模型返回：
   - `summary`
   - `body_markdown`
   - `tags / aliases`
   - `duplicate_concept_id / duplicate_reason`
   - `related_links`
4. 后端再据此建节点、建边、写 concept markdown

对应代码：

- [backend/routers/ask.py](/Users/vince/Documents/knowledge-tree/backend/routers/ask.py:206)
- [backend/services/synthesis_concept_service.py](/Users/vince/Documents/knowledge-tree/backend/services/synthesis_concept_service.py:43)

### 5. 概念精选

主链路：

1. `/api/promotion/run`
2. 先跑 heuristic
3. 如果 `use_llm=true`，再跑 `run_llm_pass(...)`
4. 模型输出 `promote / reject`

对应代码：

- [backend/routers/promotion.py](/Users/vince/Documents/knowledge-tree/backend/routers/promotion.py:82)
- [backend/services/promotion_llm.py](/Users/vince/Documents/knowledge-tree/backend/services/promotion_llm.py:164)

## 真正的 LLM 调用 vs 配套 OpenAI 资源调用

下面这些会调用 OpenAI，但不属于“模型推理本身”：

- `client.files.create(...)`：上传 PDF
- `client.vector_stores.create(...)`：为 `file_search` 建索引
- `client.beta.assistants.create(...)`：创建 assistant
- `client.beta.threads.create(...)`：创建对话线程

这些主要出现在 [backend/services/vlm_service.py](/Users/vince/Documents/knowledge-tree/backend/services/vlm_service.py:136) 一带。

真正吃 token 的模型调用是：

- `client.responses.create(...)`
- `client.chat.completions.create(...)`
- `client.embeddings.create(...)`

## 当前稳定版的一句话心智模型

- `vlm_model`：把 PDF 变成结构化知识
- `embedding_model`：把结构化知识变成可连接的向量图
- `wiki_compile_model`：把结构化知识再写成 Wiki、再拿来 Ask、再拿来做概念判断

