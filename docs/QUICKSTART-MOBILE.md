# Knowra 云端 + 移动端启动手册（≈40 分钟）

把桌面端本地数据同步到云、并在 iPhone 上用 Expo Go 跑起来。

> 全程在你的 Mac 上敲命令；只有最后一步要在 iPhone 上操作。
>
> 这是「手把手」简化版，遇到深坑参考 `docs/RUNBOOK-E2E.md`。

---

## 你将得到什么

```
✅ Supabase 项目（免费层，Postgres + Storage + Auth）
✅ Fly.io 上的云后端（auto-stop，每月 $0-2）
✅ 桌面端「立即同步」一键推 38 篇论文 / 625 概念 / 121 wiki 上云
✅ iPhone 上用 Expo Go 扫码登录看你的知识库
```

需要：
- 邮箱（注册 Supabase + Fly）
- 一张能付费的信用卡（Fly.io 验证用，免费层不扣费）
- iPhone + Expo Go app（免费，**不用** Apple Developer 账号）

---

## 工程架构速览

读懂这一节再开始操作 — 后面每一步在干什么会更清楚。

### 1. 三层拓扑：谁是生产者，谁是消费者

```mermaid
flowchart TB
    subgraph DESKTOP["🖥 桌面端 (Mac + Tauri) · 生产者"]
        direction TB
        DUI["Vite + React UI<br/>localhost:5173"]
        DAPI["FastAPI uvicorn<br/>localhost:8000"]
        DDB[("SQLite<br/>data/knowledge.db")]
        DPDF["📄 PDF<br/>data/papers/"]
        DWIKI["📝 Wiki .md<br/>data/wiki/"]
        DUI --> DAPI
        DAPI --> DDB
        DAPI --> DPDF
        DAPI --> DWIKI
    end

    subgraph CLOUD["☁ Fly.io · 无状态中继"]
        CAPI["FastAPI<br/>KNOWRA_DEPLOY_MODE=cloud<br/>(同一份代码！)"]
    end

    subgraph SUPABASE["📦 Supabase · 持久层"]
        SPG[("Postgres<br/>+ RLS 多租户")]
        SST["Storage bucket<br/>wiki/{user_id}/..."]
        SAUTH["Auth<br/>JWT 颁发"]
    end

    subgraph MOBILE["📱 iPhone (Expo Go) · 消费者"]
        MAPP["RN App<br/>4 个 tab"]
        MMEM[("AsyncStorage<br/>session + OpenAI key")]
        MAPP --> MMEM
    end

    DAPI -- "① 同步 push<br/>(Bearer JWT)" --> CAPI
    MAPP -- "② snapshot 拉取<br/>(Bearer JWT)" --> CAPI
    MAPP -- "③ Ask 提问<br/>(带 OpenAI key)" --> CAPI

    CAPI --> SPG
    CAPI --> SST
    DAPI -. "登录<br/>邮箱+密码" .-> SAUTH
    MAPP -. "登录" .-> SAUTH
    SAUTH -. "JWT" .-> DAPI
    SAUTH -. "JWT" .-> MAPP

    classDef red fill:#7f1d1d,stroke:#fca5a5,color:#fff
    class DPDF red
```

**关键事实**：
- 桌面端是**唯一**产生数据的地方（扫 PDF → LLM 抽取 → 编译 wiki）
- 云后端**没有业务逻辑**，只是搬运工 + 鉴权 + 调一次 OpenAI（Ask）
- 移动端**只读**消费 + 提问，不写数据
- Supabase Postgres + Storage 是**唯一的云端持久层**；Fly.io 上的 FastAPI 重启都不会丢数据

### 2. 隐私红线：什么留本机、什么上云

| 资源 | 桌面端 | 云后端 / Supabase | 移动端 |
|---|:---:|:---:|:---:|
| 📄 **PDF 原文** | ✅ 在 `data/papers/` | ❌ **永不上云** | ❌ 看不到 |
| 📝 Wiki 编译产物 (.md) | ✅ 在 `data/wiki/` | ✅ Storage（每用户隔离） | ✅ 拉取阅读 |
| 🧠 论文元数据 / 概念 / 边 | ✅ SQLite | ✅ Postgres | ✅ 拉取 |
| 🔑 OpenAI API key | ✅ `data/config.json` | ❌ **永不存** | ✅ AsyncStorage（仅本机） |
| 🪪 Supabase 登录态 | ✅ localStorage | — | ✅ AsyncStorage |
| 💬 Ask 提问内容 | — | ⏳ 仅请求期内存（响应后丢弃） | ✅ 本机历史 |

> **PDF 不出本机** 是设计硬约束 — 桌面端 sync agent 根本不读 PDF 字节，云端 schema 也没有放 PDF 的字段。
>
> **OpenAI key 不上云** — Ask 接口的 key 字段只活在请求处理栈帧里，云端 `cloud_llm_calls` 审计表只记 model / token / latency。

### 3. 「立即同步」一次完整调用：3 步上传协议

```mermaid
sequenceDiagram
    autonumber
    participant D as 🖥 桌面端
    participant C as ☁ 云后端
    participant PG as Postgres
    participant ST as Storage

    Note over D: 用户点「立即同步」
    D->>D: gatherLocalSnapshot()<br/>读 SQLite + wiki/.md<br/>算 content_hash
    D->>C: ① POST /api/sync/prepare<br/>{papers, nodes, edges, wiki_files[meta]}
    C->>PG: 缓存到 sync_sessions
    C->>PG: 查 wiki_files 现有 hash<br/>哪些需要上传？
    C->>ST: sign_upload(path) × N
    C-->>D: sync_session_id<br/>+ N 个 signed PUT URL
    loop 每个新/变更 wiki
        D->>ST: ② PUT 字节 → signed URL<br/>(不经过 C，直传 Storage)
    end
    D->>C: ③ POST /api/sync/commit<br/>{session_id, uploaded[hash]}
    C->>ST: HEAD 每个文件验 hash
    C->>PG: 把 sync_sessions 的缓存<br/>合并到 papers/nodes/edges/wiki_files
    C-->>D: revision=N + 接受/拒绝统计
    Note over D: 显示 ✅ 已同步
```

**为什么不一步搞定**？
- 论文元数据小（几 KB），可以塞 prepare body
- Wiki .md 加起来可能几 MB，**走签名 URL 直传 Storage 不过云后端** → 省带宽 + 不卡 FastAPI 进程
- Commit 阶段做 hash 校验，发现传错就拒绝（防中间被换包）

### 4. iPhone 看一篇文章 / 问一个问题

```mermaid
sequenceDiagram
    participant M as 📱 iPhone
    participant C as ☁ 云后端
    participant PG as Postgres
    participant ST as Storage
    participant AI as OpenAI

    Note over M: 进入"资料" tab
    M->>C: GET /api/cloud/snapshot
    C->>PG: 查 user_id 名下全部行
    C->>ST: sign_download() × N (signed URL)
    C-->>M: papers + nodes + wiki_files[meta + download_url]
    Note over M: 显示列表

    Note over M: 点一篇 wiki
    M->>ST: GET signed_download_url
    ST-->>M: .md 字节
    Note over M: 渲染

    Note over M: 切到 Ask tab，输入问题
    M->>C: POST /api/cloud/ask<br/>{question, openai_api_key, history}
    C->>PG: 检索相关 wiki (title LIKE)
    C->>AI: chat.completions(消息+上下文+key)
    AI-->>C: 答案
    C->>PG: 记录 cloud_llm_calls<br/>(只存 model/tokens/latency,<br/>不存 key 不存内容)
    C-->>M: answer + citations
    Note over M: 显示对话
```

**注意 key 的轨迹**：从 iPhone AsyncStorage 出来 → HTTPS 到云后端进 `_real_llm_call(...)` 栈帧 → OpenAI → 返回后从栈帧释放。`cloud_llm_calls` 这张审计表里**只有元数据**，没有 key、没有 prompt、没有 answer。

### 5. 代码 / 进程 / 数据三者映射

```
knowledge-tree/                        进程            数据落点
├── backend/                           ┐
│   ├── main.py                        │ uvicorn (本机 8000)
│   ├── routers/                       │  ├─ local 路由：papers/graph/wiki/...
│   │   ├── papers.py                  │  └─ cloud 路由：sync/cloud_*
│   │   ├── graph.py                   │   (KNOWRA_DEPLOY_MODE 决定挂哪些)
│   │   ├── wiki.py                    │
│   │   ├── sync.py            ────────┼──► (云模式) prepare/commit
│   │   ├── cloud.py           ────────┼──► (云模式) me/snapshot/ask
│   │   └── sync_local.py      ────────┼──► (本地模式) /api/sync/local_snapshot
│   ├── services/                      │
│   │   ├── storage.py                 │  ├─ InMemoryStorage（测试）
│   │   │                              │  └─ SupabaseStorage（生产）
│   │   ├── cloud_ask.py               │  Ask agent（云端用）
│   │   └── wiki_compiler.py           │  本地 wiki 编译
│   ├── cloud_db.py                    │  cloud 模式下 Postgres engine
│   ├── cloud_models.py                │  cloud 表的 SQLAlchemy 定义
│   └── models.py              ────────┴──► data/knowledge.db (本机 SQLite)
│
├── frontend/                          ┐
│   └── src/                           │ vite dev (本机 5173)
│       ├── api/                       │
│       │   ├── client.ts              │  ├─ 调本机 FastAPI /api/*
│       │   └── cloud.ts               │  └─ 调云后端 /api/cloud/*
│       ├── hooks/useCloudAuth.ts      │  Supabase 会话 (localStorage)
│       ├── services/syncAgent.ts      │  prepare→PUT→commit 编排
│       └── components/                │
│           ├── CloudSyncSection.tsx   │  设置 → 云同步面板
│           └── SyncStageCard.tsx      │  控制台 ⑤ 同步
│
├── mobile/                            ┐
│   ├── App.tsx                        │ Metro bundler + Expo Go
│   └── src/                           │  (运行在你 iPhone 上)
│       ├── api/cloud.ts               │  axios + AsyncStorage（不是 localStorage）
│       ├── contexts/                  │
│       │   ├── AuthContext.tsx        │  全局会话
│       │   └── SnapshotContext.tsx    │  缓存的快照
│       └── screens/                   │  Login / Papers / Concepts /
│                                      │  WikiDetail / Ask / Settings
│
├── supabase/migrations/               ┐ 一次性: supabase db push
│   ├── 0001_meta.sql                  │
│   ├── 0002_papers.sql                │  →  Supabase Postgres
│   ├── 0003_knowledge.sql             │     建表 + RLS policy + 触发器
│   ├── 0004_wiki.sql                  │
│   └── 0005_cloud_llm.sql             ┘
│
├── fly.toml                              ──►  Fly.io 部署配置
└── data/                              ┐
    ├── knowledge.db                   │  本机数据真源
    ├── papers/  (PDF, 🔒 不出本机)    │
    └── wiki/    (.md, 同步到云)        ┘
```

**两个进程一份代码**：`backend/main.py` 读环境变量 `KNOWRA_DEPLOY_MODE`，决定挂哪些路由 + 用哪个 DB engine。本机用 `local` 模式，Fly 上用 `cloud` 模式。

### 6. JWT 鉴权流程（怎么"登录"）

```mermaid
sequenceDiagram
    participant U as 你
    participant App as 桌面/移动端
    participant SA as Supabase Auth
    participant API as 云后端

    Note over U,App: 第一次：注册
    U->>App: 输入邮箱+密码
    App->>SA: POST /auth/v1/signup
    SA-->>App: access_token (1h) + refresh_token (长期)
    App->>App: 存到 localStorage/<br/>AsyncStorage

    Note over U,App: 日常：调云接口
    App->>API: GET /api/cloud/snapshot<br/>Authorization: Bearer <access_token>
    API->>API: HS256 验签<br/>(用 SUPABASE_JWT_SECRET)
    API->>API: 提取 sub → user_id<br/>所有查询带 WHERE user_id=...
    API-->>App: 你自己的数据

    Note over App: access_token 快到期
    App->>SA: POST /auth/v1/token?grant_type=refresh
    SA-->>App: 新 access_token
    App->>App: 自动续期，用户无感
```

**为什么云后端不用 Supabase service_role**？
- service_role key 能绕过 RLS — 一旦泄漏 = 所有用户数据裸奔
- 用户的 JWT 走正常 RLS 通路，Postgres 自己保证「user A 看不到 user B 的行」
- service_role 仅留给云后端**自己**写 sync_sessions / 调 storage signed URL 等内部操作

---

```bash
# Supabase CLI
brew install supabase/tap/supabase

# Fly.io CLI
brew install flyctl

# 检查都装好了
supabase --version    # 期望: 1.x.x 或 2.x.x
fly version           # 期望: flyctl v0.x.x
```

---

## 阶段 2 · 创建 Supabase 项目（10 分钟）

### 2.1 注册 + 建项目

1. 打开 https://supabase.com → Sign up（用 GitHub 登录最快）
2. New project：
   - **Name**：`knowra`
   - **Database Password**：随便生成一个强密码，**记下来**，等下要用
   - **Region**：`Northeast Asia (Tokyo)` 或离你最近的
   - **Plan**：Free（够用，500MB DB / 1GB Storage）
3. 等 ~2 分钟初始化

### 2.2 抄三个值（关键，**等下要填好几处**）

进入项目后，左下角 ⚙️ **Settings → API**：

| 字段 | 来源 | 用在哪 |
|---|---|---|
| **Project URL** | Project URL 那一栏 | 桌面端、移动端、云后端 |
| **anon public** | Project API keys → anon public | 桌面端、移动端（前端用） |
| **service_role** | Project API keys → service_role（点 Reveal） | 云后端（**绝不能**进前端） |

再去 **Settings → API → JWT Settings**：

| 字段 | 来源 | 用在哪 |
|---|---|---|
| **JWT Secret** | JWT Secret 那一栏（点 Reveal） | 云后端 |

把这 **4 个值** 复制到一个临时记事本，下面要反复用。

### 2.3 应用数据库 schema

```bash
cd ~/Documents/knowledge-tree

# 输入：你在 supabase.com 控制台 URL 里看到的 project-ref
# （比如项目地址 https://app.supabase.com/project/abcde12345，那 ref 就是 abcde12345）
supabase link --project-ref <你的 project-ref>
# 它会要你输入 Database Password — 用 2.1 记下的那个

# 推送 5 个 SQL migrations
supabase db push
```

期望输出末尾：
```
Applying migration 0001_meta.sql...
Applying migration 0002_papers.sql...
Applying migration 0003_knowledge.sql...
Applying migration 0004_wiki.sql...
Applying migration 0005_cloud_llm.sql...
Finished supabase db push.
```

到 Supabase 控制台 **Database → Tables** 确认能看到：`papers / knowledge_nodes / knowledge_edges / wiki_files / sync_sessions / cloud_deletions / cloud_llm_calls / cloud_revisions / user_profiles / sync_state` 共 10 张表。

### 2.4 创建 Storage bucket

控制台左侧 **Storage → New bucket**：
- Name：`wiki`
- Public bucket：**关闭**（私有，靠预签名 URL 访问）
- 点 Create bucket

然后 **Storage → Policies → New policy → For full customization**，粘下面这两条 SQL：

```sql
-- 服务端 service_role 可读写
CREATE POLICY "service_role full access" ON storage.objects
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 用户只能读自己 user_id 路径下的对象
CREATE POLICY "user reads own wiki" ON storage.objects
  FOR SELECT TO authenticated
  USING (
    bucket_id = 'wiki' AND
    split_part(name, '/', 1) = auth.uid()::text
  );
```

点 Save，应该看到两条 policy 加上了。

### 2.5 开启 Email 登录

控制台 **Authentication → Providers → Email**：保持 **Enable** 状态（默认就是）。

> 💡 想跳过邮箱确认（自己测着用）：**Authentication → Sign In / Up → Auth Providers → Email** 里把 **Confirm email** 关掉。

---

## 阶段 3 · 部署云后端到 Fly.io（15 分钟）

### 3.1 登录 fly

```bash
fly auth signup
# 或者已有账号：fly auth login
```

浏览器会弹出来让你填信息。

### 3.2 创建 app（不部署）

```bash
cd ~/Documents/knowledge-tree
fly launch --name knowra-cloud --region nrt --no-deploy
```

它会问几个问题：
- `Would you like to copy its configuration to the new app?` → **No**
- `Would you like to setup a Postgres database now?` → **No**（我们用 Supabase 的 PG）
- `Would you like to deploy now?` → **No**（先配 secrets）

### 3.3 改 `fly.toml`

`fly launch` 会在项目根生成 `fly.toml`。**全部内容覆盖成**：

```toml
app = "knowra-cloud"
primary_region = "nrt"

[build]
  dockerfile = "backend/Dockerfile"

[env]
  KNOWRA_DEPLOY_MODE = "cloud"
  PORT = "8000"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0

[[services.http_checks]]
  interval = "30s"
  timeout = "5s"
  grace_period = "10s"
  method = "get"
  path = "/api/cloud/me"
  protocol = "http"
```

### 3.4 注入 secrets（用 2.2 的 4 个值）

```bash
fly secrets set \
  SUPABASE_PROJECT_URL="https://<你的 project-ref>.supabase.co" \
  SUPABASE_SERVICE_ROLE_KEY="<service_role key>" \
  SUPABASE_JWT_SECRET="<JWT Secret>" \
  CLOUD_DATABASE_URL="<Supabase Postgres URI>"
```

> **CLOUD_DATABASE_URL** 怎么拿：Supabase 控制台 → **Project Settings → Database → Connection string → URI**，
> 把里面的 `[YOUR-PASSWORD]` 替换成你 2.1 记的 password。
> 格式像 `postgresql://postgres:xxx@db.abcde.supabase.co:5432/postgres`

期望输出：
```
Secrets are staged for the first deployment
```

### 3.5 部署

```bash
fly deploy
```

等 **3-5 分钟**，最后看到：

```
✔ Deployment finished
```

会给你一个 URL，类似 `https://knowra-cloud.fly.dev`。**记下这个 URL**。

### 3.6 健康检查

```bash
curl -i https://knowra-cloud.fly.dev/api/cloud/me
```

期望：

```
HTTP/2 401
{"detail":{"error":"token_missing","message":"..."}}
```

**401 是对的** — 接口活着，只是你没带 JWT。

如果 500 或连不上：`fly logs` 看 stderr。最常见是 secret 名字拼错。

---

## 阶段 4 · 桌面端推数据上云（5 分钟）

### 4.1 浏览器打开桌面 Knowra

```
http://localhost:5173
```

如果服务没跑：
```bash
cd ~/Documents/knowledge-tree
./start.sh
```

### 4.2 配置云连接

UI 上点左侧 ⚙️ **设置** → 展开 **云同步**：

| 字段 | 填什么 |
|---|---|
| Supabase URL | `https://<project-ref>.supabase.co` |
| Supabase anon key | 2.2 抄的 anon public key |
| 云后端 URL | 3.5 拿到的 `https://knowra-cloud.fly.dev` |

点保存（右下角 ⌘S）。

### 4.3 注册 + 登录

在同一个「云同步」面板切到 **注册** tab：
- 邮箱 + 密码（≥6 位）
- 点 注册

如果 Supabase 没关 email confirm，去你的邮箱点确认链接。回来切到 **登录** tab，用刚才邮箱密码登录。

成功后面板会变成：
```
👤 邮箱
   abc-def-uuid
   上次同步：从未
   [登出]
```

### 4.4 第一次同步

到左侧 🧠 **知识** 页 → 左边「流水线控制台」拉到底 → ⑤ **同步** → 点 **立即同步**。

会依次看到：
1. 「准备中」
2. 「上传中 0/N → N/N」（每篇 wiki 一个 PUT）
3. 「提交中」
4. ✅「已同步 · revision 1 · papers 38 / nodes 625 / edges 4265 / wiki 121」

**整个过程通常 30 秒到 2 分钟**（看你 wiki 文件多少）。

### 4.5 云端自检

```bash
# 拿你刚登录的 token（浏览器 DevTools Console 里跑）
# JSON.parse(localStorage.getItem('knowra.cloud.session')).access_token

TOKEN="粘上面 token"
curl -H "Authorization: Bearer $TOKEN" \
  https://knowra-cloud.fly.dev/api/cloud/me
```

期望：
```json
{
  "user_id": "...",
  "email": "...",
  "stats": {"papers": 38, "concepts": 119, "edges": 4265, "wiki_files": 121, ...}
}
```

数字应等于你刚推上去的。

---

## 阶段 5 · 移动端跑起来（10 分钟）

### 5.1 iPhone 上装 Expo Go

App Store 搜 **Expo Go**（免费，绿色图标），装好。

### 5.2 启动 Metro dev server

新开一个终端窗口：

```bash
cd ~/Documents/knowledge-tree/mobile
npm install     # 第一次跑，3-5 分钟
npm start
```

期望：
- 终端打出一个 **QR 码**
- 一堆提示，包括 `› Press a │ open Android`、`› Press i │ open iOS simulator`

> Mac 和 iPhone 必须在**同一个 WiFi**。如果你 Mac 连的是 5GHz、iPhone 连的是 2.4GHz（同一 SSID 两段），换成同一段。

### 5.3 iPhone 扫码

打开 iPhone 自带「**相机**」app → 对准终端里的 QR 码 → 上方会弹「在 Expo Go 中打开」→ 点。

Expo Go 会自动下载 JS bundle（~2.2MB，10 秒），然后 app 启动到一个**登录页 + 设置 tab** 的状态。

### 5.4 在 iPhone 上配置

进 **设置** tab，填 **4 个字段**（前 3 个和桌面端一样）：

| 字段 | 填什么 |
|---|---|
| Supabase URL | `https://<project-ref>.supabase.co` |
| Supabase anon key | anon public key |
| 云后端 URL | `https://knowra-cloud.fly.dev` |
| OpenAI API key | 你自己的 `sk-...`，Ask 提问时才用，仅本机 |

点 **保存设置**。

> OpenAI key 在哪拿：https://platform.openai.com/api-keys → Create new secret key。

### 5.5 登录 + 浏览

切到 **登录** tab，用桌面端 4.3 步注册的邮箱密码登录。

成功后自动跳进主界面（4 个 tab）：
- **资料**：38 篇论文列表
- **概念**：119 个概念
- **Ask**：跨论文提问
- **设置**：管理

每个 tab 下拉刷新同步最新数据。点论文 / 概念条目进单页 wiki 详情，会看到 markdown 原文。Ask 提问会调你的 OpenAI key。

---

## 后续日常使用

| 你想 | 怎么做 |
|---|---|
| 处理新论文 | 桌面 → 资料 → 扫描目录 → 处理 |
| 把新数据推到云 | 桌面 → 知识 → 控制台 ⑤ 同步 → 立即同步 |
| 在手机看新内容 | iPhone → 任意 list → 下拉刷新 |
| 在手机问问题 | iPhone → Ask → 输入问题（用你自己的 OpenAI key） |
| 切换 iPhone 账号 | iPhone → 设置 → 登出 → 重登 |

---

## 常见坑

### 「立即同步」失败 `prepare 401`
JWT 不对。重启浏览器 / 重新登录云同步面板。

### 「立即同步」失败 `prepare 500`
看 `fly logs` 看后端报错。最常见：
- `SUPABASE_JWT_SECRET` 抄错了一个字符
- `CLOUD_DATABASE_URL` 里 `[YOUR-PASSWORD]` 没替换

修完：
```bash
fly secrets set SUPABASE_JWT_SECRET="..."
# 自动重启
```

### 「立即同步」上传失败
Supabase Storage bucket 名字不是 `wiki`（必须小写），或 policy 没粘对。

### iPhone Expo Go 显示「无法连接」
- Mac 和 iPhone 不在同一 WiFi 段
- Mac 防火墙挡了 19000 端口 → 系统设置 → 网络 → 防火墙 → 关掉，或加白名单

### iPhone 登录 401
- Supabase anon key 复制时漏了 `eyJ` 开头或末尾的 `==`
- 云后端 URL 末尾不能带 `/`

### iPhone Ask 永远转圈
- OpenAI key 错（拿真 key 测一下：`curl -H "Authorization: Bearer sk-..." https://api.openai.com/v1/models`）
- 云端 rate limit（60 次 / 5 分钟 / 每用户），等下再试

### 想看云端到底存了啥
Supabase 控制台 → Database → Tables → 点 `papers` / `wiki_files` 等表，能直接看你刚推上去的数据。

---

## 成本预估

| 项 | 月成本 |
|---|---|
| Supabase Free Tier | $0 |
| Fly.io 256MB machine（auto-stop） | $0-2 |
| OpenAI 提问（你自付） | 不计 |
| **总计** | **< $2/月** |

100 人活跃用户时升级 Supabase Pro $25 + Fly always-on $10 ≈ $35/月。当前只你一个用，Free 层够用很久。

---

## 想关停回到纯本地？

```bash
# Fly 上停 app（不删，随时再开）
fly scale count 0 -a knowra-cloud

# Supabase 项目不动也没钱（免费）
# 桌面端：设置 → 云同步 → 登出
# 之后桌面端继续完全本地工作，跟没接云一样
```

---

要更深的架构 / 协议细节，看：
- `docs/ARCHITECTURE-CLOUD.md` — 整体拓扑
- `docs/SYNC-PROTOCOL.md` — push / pull 协议
- `docs/RUNBOOK-E2E.md` — 8 阶段完整 runbook（含 App Store 上架未来路径）
