# Sync 协议契约

定义桌面端、移动端、云后端之间的 HTTP API 契约。

> **设计原则**：v1 单向（桌面 → 云 → 移动只读）；幂等；增量；零冲突。

---

## 0. 命名空间

| 前缀 | 调用方 | 部署模式 |
|---|---|---|
| `/api/*` | 桌面端 React 前端 → 桌面端 FastAPI（localhost） | local |
| `/api/sync/prepare` `/api/sync/commit` | 桌面端 FastAPI → 云 FastAPI | cloud（不是 local） |
| `/api/cloud/*` | 移动端 RN → 云 FastAPI | cloud |
| `<storage>.supabase.co/storage/...` | 桌面端 FastAPI 直传 `.md` 内容 | cloud（通过预签名 URL） |

> **重要**：`/api/sync/*` 和 `/api/cloud/*` 都只在 cloud 部署模式下提供；
> local 模式只暴露原有 `/api/*` 给桌面前端用。
> `.md` 文件**不经过我们的 FastAPI**，桌面端用预签名 URL 直传 Storage，
> 详见 §2。

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

## 2. 桌面 → 云：3 步上传协议

桌面端在 pipeline 跑完后自动跑这套流程；用户也能在 UI 上手动触发。

**为什么是 3 步而不是 1 步**：`.md` 文件通过 **Supabase Storage 预签名 URL 直传**，
不经过我们的 FastAPI 服务器。这样云后端零带宽消耗、零内存峰值、文件级进度可见、
增量检测靠 content_hash 跳过未变文件。

```
┌─────────────┐   1. prepare      ┌──────────────┐
│  桌面端      │ ────────────────► │  云 FastAPI   │
│             │   metadata only   │              │
│             │ ◄──────────────── │              │
│             │   signed URLs     │              │
│             │                   └──────────────┘
│             │   2. parallel PUT  ┌──────────────┐
│             │ ────────────────► │  Supabase     │
│             │   raw .md content │  Storage     │
│             │ ◄──────────────── │              │
│             │   200 OK          └──────────────┘
│             │   3. commit       ┌──────────────┐
│             │ ────────────────► │  云 FastAPI   │
│             │   sync_session_id │              │
│             │ ◄──────────────── │              │
└─────────────┘   revision        └──────────────┘
```

### 2.1 Step 1：`POST /api/sync/prepare`

桌面端发送增量的**全部元数据**（包括 wiki_files 的 content_hash 但不含 content）：

```http
POST /api/sync/prepare
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "device_id": "fbb0e60e-4f3a-...",
  "since": "2026-05-27T10:30:00Z",         // null 表示全量
  "tables": {
    "papers":           [ {...metadata...}, ... ],
    "knowledge_nodes":  [ {...}, ... ],
    "knowledge_edges":  [ {...}, ... ],
    "wiki_files":       [
      {
        "id": "...",
        "user_id": "...",
        "kind": "paper",
        "rel_path": "papers/0001-foo.md",
        "content_hash": "sha256:abc123...",
        "size_bytes": 4321,
        "title": "Foo Bar",
        "aliases": ["paper:1"],
        "compiled_at": "2026-05-27T11:00:00Z",
        "paper_id": "0e8b..."
      }
    ]
  },
  "deletions": {
    "papers": ["..."],
    "knowledge_nodes": ["..."],
    "knowledge_edges": ["..."],
    "wiki_files": ["..."]
  }
}
```

**Response（200）**：

```json
{
  "sync_session_id": "8c4f2a-...",         // 用于第三步 commit
  "expires_at": "2026-05-27T11:15:00Z",    // 10 分钟有效期
  "uploads_required": [
    {
      "rel_path": "papers/0001-foo.md",
      "upload_url": "https://<project>.supabase.co/storage/v1/object/upload/sign/wiki/<user>/papers/0001-foo.md?token=...",
      "method": "PUT",
      "headers": { "Content-Type": "text/markdown", "x-upsert": "true" }
    }
  ],
  "uploads_skipped": [
    { "rel_path": "papers/0002-bar.md", "reason": "content_hash unchanged" }
  ],
  "validation_errors": []
}
```

**关键行为**：
- `uploads_required` 只包含**真正需要上传的文件**。对每个 `wiki_files` 入参，云端查 `(user_id, rel_path)` 的当前 `content_hash`，相同就放进 `uploads_skipped`，跳过实际上传。
- 表格元数据（papers / nodes / edges）已经**临时写入 staging area**（不是最终表）。pending 在 `sync_session_id` 下，等 commit 才生效。
- staging area 1 小时未 commit 自动 GC。

### 2.2 Step 2：并发 PUT 到 Storage

对 `uploads_required` 中每个文件，桌面端并发执行：

```http
PUT <upload_url>
Content-Type: text/markdown
x-upsert: true

<.md file raw bytes>
```

**关键点**：
- 直接走 Supabase Storage 域名，**不经过我们的 FastAPI**
- 上传 URL 内嵌一次性 token，10 分钟有效
- 单文件失败 → 单独重试（其他文件不受影响）
- 实际并发度建议 **6 并发**（避免压爆 Storage rate limit）

### 2.3 Step 3：`POST /api/sync/commit`

所有 PUT 都 200 后，桌面端通知云端"上传完成"：

```http
POST /api/sync/commit
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "sync_session_id": "8c4f2a-...",
  "uploaded": [
    {
      "rel_path": "papers/0001-foo.md",
      "content_hash": "sha256:abc123..."     // 再次确认（防中间篡改 / 客户端 bug）
    }
  ]
}
```

云端做 4 件事：
1. 校验 sync_session_id 没过期、属于当前 user
2. 对每个 uploaded 文件，向 Storage HEAD 一次，验真 content_hash 与传上来的一致
3. 把 staging 元数据 commit 到正式表（papers / knowledge_nodes / knowledge_edges / wiki_files）
4. revision++，返回新 revision

**Response（成功）**：

```http
200 OK
Content-Type: application/json

{
  "revision": 47,
  "accepted": {
    "papers": 5,
    "knowledge_nodes": 12,
    "knowledge_edges": 18,
    "wiki_files": 3
  },
  "rejected": [],
  "server_now": "2026-05-27T11:05:12Z"
}
```

**Response（部分失败）**：

```http
207 Multi-Status
{
  "revision": 47,
  "accepted": { "papers": 4, ... },
  "rejected": [
    {
      "table": "knowledge_edges",
      "id": "...",
      "reason": "source node user mismatch",
      "code": "FK_VIOLATION"
    },
    {
      "table": "wiki_files",
      "rel_path": "papers/0001-foo.md",
      "reason": "Storage HEAD content_hash mismatch",
      "code": "HASH_MISMATCH"
    }
  ],
  "server_now": "..."
}
```

桌面端：accepted 的更新本地 sync 水位线；rejected 的写入 `pending_tables` 下次重试。

### 2.4 错误码

| 状态码 | 来源 | 客户端动作 |
|---|---|---|
| 401 | prepare/commit | refresh JWT 后重试 |
| 403 | prepare/commit | 用户被禁用，停止同步 |
| 410 | commit | sync_session 已过期，从 prepare 重来 |
| 413 | prepare | metadata 过大；拆 batch（单批 papers 上限 200） |
| 429 | prepare/commit/Storage | 等待 `Retry-After` |
| Storage 4xx | PUT | 检查 upload_url 是否过期；过期重做 prepare |

### 2.5 幂等性保证

| 客户端动作 | 服务器行为 |
|---|---|
| 同一份 prepare 重发 | 返回同一个 sync_session_id（按 device_id + since 的 hash 去重） |
| 同一个文件 PUT 重发 | Storage 默认 upsert，覆盖即可 |
| 同一个 commit 重发 | 服务器按 sync_session_id 去重，第二次返回上次的 revision |
| 失败重试整个流程 | 从 prepare 重新开始；旧 session 会被 GC |

**关键不变量**：单个 sync_session_id 只能 commit 一次。多次提交 commit 会返回 cached response，不会重复进 revision。

### 2.6 离线 / 中断恢复

| 场景 | 处理 |
|---|---|
| prepare 后客户端崩 | sync_session 1h 自动 GC，重启从 prepare 重来 |
| 上传到一半（10 文件，传了 5 个）网络断 | 重连后继续传剩余 5 个；已传的不重传 |
| 上传完了 commit 前崩 | 重启后 commit；服务器 HEAD 检查 Storage 上文件齐了就接受 |
| commit 5xx | 客户端指数退避重试同一个 sync_session_id |

> **注意**：方案 B 比 A 多了 2 个 HTTP round-trip，但实际**总耗时基本一样**（Storage 直传非常快），且能干掉 base64 编解码开销和云后端的大 payload 处理。

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
       Step 1 POST /api/sync/prepare {
         since: "T-2 hour",   // 上次成功 sync 的时间
         tables: { papers: 5, wiki_files: 5, ... }
       }
T+3min0s5 云返回 sync_session_id + 5 个 upload_url
       Step 2 并发 PUT 5 个 .md 文件直传到 Supabase Storage
T+3min1s 全部 PUT 完成
       Step 3 POST /api/sync/commit { sync_session_id, uploaded: [...] }
T+3min1s5 云返回 200 + revision=48
T+3min1s5 sync_state.last_pushed_at = T+3min1s5
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
| 移动端长轮询 / WebSocket 实时更新 | 不做 v1；下拉刷新足够 |
| Snapshot 端点是否流式（NDJSON）以避免大 payload OOM | 1000+ 节点开始考虑 |
| Storage 上传并发度（当前默认 6） | 监控 429 后调 |
