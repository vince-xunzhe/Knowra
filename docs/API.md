# API 说明

后端默认运行在 `http://localhost:8000`。前端开发服务器通过 Vite 代理访问 `/api`。

## 状态

### `GET /`

健康检查。

响应示例：

```json
{
  "message": "Knowledge Tree API is running"
}
```

### `GET /api/status`

返回当前批量处理状态。

```json
{
  "running": false,
  "total": 0,
  "done": 0,
  "errors": 0,
  "current": ""
}
```

## 配置

### `GET /api/config`

读取当前配置。返回的 `openai_api_key` 会被脱敏，`extraction_prompt` 不在该接口返回。

### `POST /api/config`

更新配置。

请求体字段均为可选：

```json
{
  "openai_api_key": "sk-...",
  "scan_directory": "/absolute/path/to/papers",
  "vlm_model": "gpt-4o",
  "embedding_model": "text-embedding-3-small",
  "similarity_threshold": 0.6,
  "use_first_page_image": true,
  "openai_assistant_id": ""
}
```

说明：

- `openai_assistant_id` 传空字符串可清除缓存 Assistant，下次处理时重新创建。
- `use_first_page_image` 是兼容旧流程的配置项；当前 Assistants + file_search 流程主要读取 PDF 附件。

## Prompt

### `GET /api/prompt`

读取当前抽取 Prompt 和默认 Prompt。

### `POST /api/prompt`

保存抽取 Prompt。

```json
{
  "extraction_prompt": "请按指定 JSON 结构抽取论文知识..."
}
```

### `POST /api/prompt/reset`

将抽取 Prompt 重置为内置默认值。

## 论文

### `POST /api/scan`

扫描配置中的 `scan_directory`，把新增 PDF 写入数据库。

响应示例：

```json
{
  "new_found": 3,
  "total": 12,
  "unprocessed": 3
}
```

### `GET /api/papers`

列出所有论文及处理状态。

### `GET /api/papers/{paper_id}`

读取单篇论文详情，包括抽取文本、原始模型响应和关联知识节点。

### `GET /api/papers/{paper_id}/file`

返回 PDF 文件。

### `GET /api/papers/{paper_id}/first_page`

返回 PDF 首页 PNG 缩略图。

### `POST /api/process`

启动后台任务，批量处理所有未处理且无错误的论文。

### `POST /api/papers/{paper_id}/process`

启动后台任务，处理单篇论文。

### `POST /api/papers/{paper_id}/retry`

清除单篇论文错误状态，并重新处理。

## 图谱

### `GET /api/graph`

返回全部图谱节点和边。

响应结构：

```json
{
  "nodes": [
    {
      "id": "1",
      "title": "Transformer",
      "content": "技术描述",
      "node_type": "technique",
      "tags": ["attention"],
      "source_paper_ids": [1],
      "created_at": "2026-04-21T00:00:00+00:00"
    }
  ],
  "edges": [
    {
      "id": "1",
      "source": "1",
      "target": "2",
      "relation_type": "builds_on",
      "weight": 1.0
    }
  ]
}
```

### `GET /api/nodes/{node_id}`

返回节点详情、相邻节点和关联边。

### `GET /api/search?q=keyword`

按标题和内容搜索知识节点，最多返回 20 条。

### `POST /api/graph/rebuild_edges`

删除现有 `similar` 边，并用当前相似度阈值重新计算。

### `POST /api/graph/reset`

清空所有知识节点和边，将所有论文标记为未处理。该操作不会删除 PDF 文件或论文记录。
