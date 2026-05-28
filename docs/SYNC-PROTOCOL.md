# Sync 协议契约

定义桌面端、移动端、云后端之间的 HTTP API 契约。

> **设计原则**：v1 单向（桌面 → 云 → 移动只读）；幂等；增量；零冲突。

---

## 0. 命名空间

| 前缀 | 调用方 | 部署模式 |
|---|---|---|
| `/api/*` | 桌面端 React 前端 → 桌面端 FastAPI（localhost） | local |
| `/api/sync/*` | 桌面端 FastAPI → 云 FastAPI | cloud（不是 local） |
| `/api/cloud/*` | 移动端 RN → 云 FastAPI | cloud |

> **重要**：`/api/sync/*` 和 `/api/cloud/*` 都只在 cloud 部署模式下提供；
> local 模式只暴露原有 `/api/*` 给桌面前端用。

---

## 1. Auth

所有 `/api/sync/*` 和 `/api/cloud/*` 请求：

```
Authorization: Bearer <supabase_jwt>
```

JWT 由 Supabase Auth 签发。云后端用 `model_gateway/auth.py` 的 `verify_jwt()` 验证签名并取出 `user_id`。

JWT 失效 → 返回 `401 Unauthorized`：
```json
{ "error": "unauthorized", "message": "token expired" }
```

客户端应自动用 refresh token 续期后重试一次。

---

## 2. 桌面 → 云：`POST /api/sync/push`

桌面端在 pipeline 跑完后自动调用；用户也能在 UI 上手动触发。

### 2.1 Request

```http
POST /api/sync/push
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "device_id": "fbb0e60e-4f3a-...",        // 桌面端首次启动时生成
  "since": "2026-05-27T10:30:00Z",         // null 表示全量
  "tables": {
    "papers": [
      {
        "id": "0e8b...",
        "user_id": "...",                  // 必须 == auth.uid()，否则被拒
        "filepath": "/Users/.../foo.pdf",
        "filename": "foo.pdf",
        ...                                // 全字段
        "updated_at": "2026-05-27T11:02:33Z"
      }
    ],
    "knowledge_nodes": [ ... ],
    "knowledge_edges": [ ... ],
    "wiki_files": [
      {
        "id": "...",
        "user_id": "...",
        "kind": "paper",
        "rel_path": "papers/0001-foo.md",
        "content_hash": "sha256:abc123...",
        "title": "Foo Bar",
        "aliases": ["paper:1"],
        "compiled_at": "2026-05-27T11:00:00Z",
        "paper_id": "0e8b...",
        "size_bytes": 4321
        // ⚠️ 不包含 content；content 走 multipart 或 Storage 上传 URL
      }
    ]
  },
  "deletions": {                            // 桌面端删除的 ID
    "papers": ["..."],
    "knowledge_nodes": ["..."],
    "knowledge_edges": ["..."],
    "wiki_files": ["..."]
  }
}
```

### 2.2 Wiki 文件内容上传

每条 `wiki_files` 行只携带元数据；实际 `.md` 内容通过两步：

**方案 A（v1 简化）：嵌入 base64**

适合首版，简单。push payload 内额外字段：

```json
"wiki_file_contents": {
  "papers/0001-foo.md": "base64:..."
}
```

云后端解码 → 写到 Storage `wiki/{user_id}/papers/0001-foo.md`。

> 缺点：payload 可能大。32 papers × 50KB = 1.6MB，可接受。100+ 篇时考虑方案 B。

**方案 B（v2）：预签名 URL**

云后端先返回每个文件的 Storage 预签名 PUT URL，桌面端单独并发上传，最后再 commit。
留给 Phase 4。

### 2.3 Response（成功）

```http
200 OK
Content-Type: application/json

{
  "revision": 47,                          // 服务器单调递增版本号
  "accepted": {
    "papers": 5,
    "knowledge_nodes": 12,
    "knowledge_edges": 18,
    "wiki_files": 3
  },
  "rejected": [],                          // 详见下面
  "server_now": "2026-05-27T11:05:12Z"
}
```

桌面端收到后：
- 更新本地 `sync_state.last_pushed_at = server_now`
- 更新本地 `sync_state.last_push_revision = revision`

### 2.4 Response（部分失败）

```http
207 Multi-Status
Content-Type: application/json

{
  "revision": 47,
  "accepted": { "papers": 4, ... },
  "rejected": [
    {
      "table": "knowledge_edges",
      "id": "...",
      "reason": "source node user mismatch",
      "code": "FK_VIOLATION"
    }
  ],
  "server_now": "..."
}
```

桌面端：accepted 的更新本地 `updated_at` 不再推；rejected 的标记到 `pending_tables` 字段下次重试。

### 2.5 Response（鉴权 / 配额错误）

| 状态码 | 含义 | 客户端动作 |
|---|---|---|
| 401 | JWT 失效 | refresh 后重试 |
| 403 | 用户被禁用 | 显示账户问题，停止同步 |
| 413 | payload 太大 | 拆批；单批 papers 上限 50 |
| 429 | 限速 | 等待 `Retry-After` 秒数 |
| 5xx | 服务器问题 | 指数退避，最多 5 次 |

### 2.6 幂等性保证

服务器看 `id` + `updated_at`：
- 服务器已有更新（updated_at >= incoming.updated_at）→ 忽略（不算 rejected）
- 服务器没有 / 更旧 → upsert
- `wiki_files.content_hash` 相同 → 不重新写 Storage

桌面端重试同一份 payload **多次都安全**。

---

## 3. 移动端 → 云：`GET /api/cloud/snapshot`

移动端进入 app 或下拉刷新时调用，拉自己用户的全量数据。

### 3.1 Request

```http
GET /api/cloud/snapshot?since=2026-05-27T10:00:00Z
Authorization: Bearer <jwt>
```

`since` 可选；不传则返回全量。

### 3.2 Response

```http
200 OK
Content-Type: application/json

{
  "revision": 47,
  "server_now": "2026-05-27T11:05:12Z",
  "papers": [ { ... } ],
  "knowledge_nodes": [ { ... } ],
  "knowledge_edges": [ { ... } ],
  "wiki_files": [
    {
      ...
      "download_url": "https://supabase.../sign?token=..."   // 预签名 URL，10 min 有效
    }
  ],
  "deleted_since": {
    "papers": ["..."],
    "knowledge_nodes": ["..."],
    "knowledge_edges": ["..."],
    "wiki_files": ["..."]
  }
}
```

移动端：
- upsert 数据到本地 SQLite 缓存
- 删除 `deleted_since` 中的 ID（保持与云端一致）
- 需要展示 `.md` 内容时按 `download_url` 拉（带本地缓存）

### 3.3 性能预算

| 项 | 目标 |
|---|---|
| snapshot 首次拉取（32 papers + 523 nodes + 3635 edges） | < 800ms |
| 增量拉取（since 后 10 分钟内的变更） | < 200ms |
| .md 文件下载 | 单文件 < 500ms（CDN cache + 60KB 平均） |

---

## 4. 移动端 Ask：`POST /api/cloud/ask`

### 4.1 Request

```http
POST /api/cloud/ask
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "question": "What is RoPE?",
  "openai_api_key": "sk-...",         // ⚠️ 仅本次调用，不存留
  "model": "gpt-5",                    // optional
  "history": [                          // optional, 多轮对话
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "reasoning_effort": "medium"          // optional, gpt-5 系列
}
```

### 4.2 Response

```http
200 OK
Content-Type: application/json

{
  "answer": "RoPE (Rotary Position Embedding) is ...",
  "citations": [
    {
      "kind": "concept",
      "ref": "[[concept:42]]",
      "concept_id": "...",
      "title": "RoPE"
    }
  ],
  "trace": [
    { "step": 0, "tool": "search_wiki", "args": {...}, "result_summary": "...", "duration_ms": 320 },
    ...
  ],
  "tokens": {
    "prompt": 1234,
    "completion": 567,
    "total": 1801
  }
}
```

### 4.3 关键安全细节

| 行为 | 实施 |
|---|---|
| 用户 OpenAI key 不存数据库 | handler 仅在请求 lifecycle 内使用 |
| 不写入 cloud_llm_calls 表的 prompt / completion 内容 | 仅记 token 数 / latency / 成败 |
| 不写日志 | logger 配置脱敏 |
| 速率限制 | 单用户 60 calls / 5 min；超出 429 |
| 拒绝太长的 question / history | 上限 8000 字符；超出 400 |

### 4.4 错误码

| 状态码 | 场景 |
|---|---|
| 400 | question 空 / 太长 / openai_api_key 缺失 |
| 401 | JWT 失效 |
| 402 | OpenAI 返回 quota_exceeded（透传） |
| 429 | 我们的限速 |
| 502 | OpenAI 调用失败（网络 / 5xx） |

---

## 5. 辅助端点

### 5.1 `GET /api/cloud/me`

```http
GET /api/cloud/me
Authorization: Bearer <jwt>

200 OK
{
  "user_id": "...",
  "email": "...",
  "display_name": "...",
  "stats": {
    "papers": 32,
    "concepts": 82,
    "edges": 3635,
    "last_desktop_sync_at": "2026-05-27T11:05:12Z",
    "wiki_size_bytes": 1234567
  }
}
```

### 5.2 `GET /api/cloud/wiki/{file_id}`

获取单个 wiki 文件内容（移动端按需拉，不放在 snapshot 里以减小 snapshot 体积）：

```http
GET /api/cloud/wiki/{file_id}
Authorization: Bearer <jwt>

200 OK
Content-Type: text/markdown
<frontmatter + markdown 正文>
```

为减轻服务器压力，**实现上等价为 302 redirect 到 Supabase Storage 预签名 URL**：

```http
302 Found
Location: https://supabase.../sign?token=...
```

### 5.3 `POST /api/cloud/wiki/search`

简单搜索（v1：title LIKE；v2 用 FTS）：

```http
POST /api/cloud/wiki/search
Authorization: Bearer <jwt>
Content-Type: application/json

{ "q": "rope", "kind": "concept", "limit": 20 }

200 OK
{
  "hits": [
    { "id": "...", "kind": "concept", "title": "RoPE", "snippet": "..." }
  ]
}
```

---

## 6. 错误响应格式（统一）

```json
{
  "error": "<error_code>",
  "message": "<human-readable>",
  "details": { ... }     // 可选，调试用
}
```

`error_code` 枚举：
- `unauthorized` (401)
- `forbidden` (403)
- `not_found` (404)
- `validation_error` (400)
- `conflict` (409)
- `payload_too_large` (413)
- `rate_limited` (429)
- `upstream_error` (502)
- `internal_error` (500)

---

## 7. 同步生命周期（端到端举例）

```
T+0    用户在桌面端添加 5 篇新 PDF 到 data/papers/
T+1    扫描 → papers 表新增 5 行（处理状态 'scanning'）
T+3min papers 处理完 → updated_at 翻新
T+3min wiki_compiler 编译 → wiki_files 新增 5 行 + .md 文件落地
T+3min sync agent 自动触发：
       POST /api/sync/push {
         since: "T-2 hour",   // 上次成功 sync 的时间
         tables: { papers: 5, wiki_files: 5, ... },
         wiki_file_contents: { ... }
       }
T+3min1s 云返回 200 + revision=48
T+3min1s sync_state.last_pushed_at = T+3min1s
T+5min   用户在移动端打开 app
T+5min   GET /api/cloud/snapshot?since=null   (首次)
T+5min1s 移动端拿到 47 papers + 528 nodes + 3640 edges 落到本地缓存
T+10min  用户在移动端点了一个新概念页 → 已在本地缓存，直接渲染
T+15min  用户在移动端 Ask "RoPE 是什么"
T+15min  POST /api/cloud/ask {...}
T+15min4s 移动端收到答案 + 引用，渲染
```

---

## 8. 待定项（Phase 1 决定）

| 项 | 倾向 |
|---|---|
| 桌面 → 云的实时推送（pipeline 跑完后立刻推） vs 定时推（每 10 min） | 立刻推 + UI 显示同步状态 |
| Wiki 内容上传方案 A vs B | A（base64 嵌入）v1；> 100 篇时迁 B |
| 移动端长轮询 / WebSocket 实时更新 | 不做 v1；下拉刷新足够 |
| Snapshot 端点是否流式（NDJSON）以避免大 payload OOM | 1000+ 节点开始考虑 |
