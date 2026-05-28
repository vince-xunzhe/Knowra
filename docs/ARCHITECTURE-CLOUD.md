# 云架构设计（SaaS 多租户）

本文档冻结 Phase 0 的架构决策。所有后续 phase 的实施都以此文档为契约；
任何偏离都需要先回来修这份文档。

---

## 1. 产品形态

> 一句话：**桌面端是数据制造者，云端是同步与分发层，移动端是只读消费者。**

```
┌───────────────────────────┐                ┌──────────────────────┐
│  Tauri 桌面 (主干)         │                │  RN 移动 (浏览/Ask)  │
│  ─────────────              │                │  ──────────          │
│  • FastAPI + React          │                │  • Expo + RN         │
│  • Codex CLI (本地)         │                │  • react-native-svg  │
│  • 抽取 / 编译 / lint        │                │  • 用户自己的 OpenAI  │
│  • PDF / 图谱 / 全部交互    │                │    key 跑 Ask         │
│  • 写入本地 SQLite          │                │  • 只 GET，不 push    │
└──────────┬────────────────┘                └──────────┬───────────┘
            │ sync push (incremental)                    │ HTTPS + JWT
            │  Auth via Supabase                         │
            ▼                                            ▼
┌───────────────────────────────────────────────────────────────────┐
│                       Supabase 云                                  │
│  ──────────────────                                                │
│  • Postgres (per-user 数据，RLS 隔离)                              │
│  • Storage R2 (.md 文件)                                            │
│  • Auth (邮箱 / OAuth / 重置密码 / 邮件验证)                       │
│                                                                    │
│  PDF 不上云。仅元数据存 papers.local_path                          │
└───────────────────────────────────────────────────────────────────┘
```

## 2. 关键不变量（不可妥协）

| # | 不变量 | 后果 |
|---|---|---|
| 1 | **PDF 永远不出本机** | 移动端看不到原始 PDF，只能看抽取后的结构化字段 + 编译后的 .md |
| 2 | **桌面端继续是 source of truth** | 任何 schema 变更先在桌面 SQLite 落地、可跑通后才推到云 |
| 3 | **同步是单向（桌面→云→移动）** v1 | 移动端不写回。笔记 / Ask 会话回流留给 Phase 4 |
| 4 | **Codex CLI 路由继续可用** | 桌面端的 LLM 调用仍可走本地 Codex；只有移动端的 Ask 走云端 OpenAI |
| 5 | **Obsidian-native 在桌面端继续成立** | `data/wiki/` 仍是本地 vault，frontmatter aliases 仍工作 |
| 6 | **用户自带 LLM key** | 我们不转售 token，不做计费，不烧初期资本 |

## 3. 技术栈与替代项

### 3.1 云后端：Supabase

**选定理由**：
- Postgres + Auth + Storage + Edge Functions 一体化，免运维
- RLS（Row Level Security）原生支持多租户隔离
- 免费层：500MB DB、1GB Storage、50000 monthly active users，覆盖闭包内测
- 客户端 SDK 成熟（@supabase/supabase-js 用于 RN，supabase-py 用于 FastAPI）
- 离开方案：导出 Postgres dump + R2 文件 → 任意自托管 PG + S3 兼容存储

**放弃的备选**：
- Cloudflare D1：边缘原生但仍 beta，复杂 JOIN 性能不够
- Firebase：Firestore 是文档型，与现有关系型 schema 不匹配
- 自托管 PG on Fly.io：运维成本太高，1 人项目不划算

### 3.2 桌面端打包：Tauri

**选定理由**：
- 最终包体积 ~20MB（Electron 是 ~200MB）
- Rust 启动器 + 系统 webview，启动速度近原生
- 内置 sidecar 机制，可优雅启动并管理 FastAPI 子进程
- 三平台（macOS / Windows / Linux）单次配置

**放弃的备选**：
- Electron：体积、启动、内存都明显劣势
- 让用户跑 docker / git clone：劝退 99% 非开发用户

### 3.3 移动端：React Native + Expo

**选定理由**：
- 与现有 React 代码同语言、同状态库（TanStack Query）
- Expo Managed Workflow 首版交付最快，无需配置 Xcode/Gradle
- 后续如需要原生模块（生物认证、推送），可 eject 到 Bare Workflow
- 单 codebase 同时上 iOS + Android

**放弃的备选**：
- PWA：iOS Safari 对 PWA 的支持仍受限（无推送、PWA Storage 配额小）
- 双原生（Swift + Kotlin）：开发量 ~2x，维护永远不同步

### 3.4 Auth：Supabase Auth

**选定理由**：
- 与 Supabase Postgres 同源，`auth.uid()` 直接可用于 RLS policy
- 支持邮箱密码 + Google/GitHub OAuth，免邮件服务自管
- JWT 自动续期、密码重置、邮箱验证开箱即用
- 客户端 SDK 自动处理 token 刷新

**放弃的备选**：
- 自写 Auth：6 周代码 + 持续维护，没有任何竞争优势
- Auth0：定价对小规模过贵；与 RLS 集成需要额外胶水

## 4. 组件职责

### 4.1 桌面端（Tauri 包装）

| 模块 | 职责 |
|---|---|
| Tauri shell | 启动 FastAPI 子进程；处理深链接（`knowra://` URL scheme） |
| FastAPI 后端 | 现有所有 router + `routers/sync.py`（新增） |
| SQLite | 本地数据持久化；schema 与云端 Postgres 1:1 对齐（外加可空 `user_id`） |
| Codex CLI 路由 | 继续供本地 LLM 调用使用 |
| Sync agent | pipeline 跑完后自动调 `POST /api/sync/push`；可用 user-facing "立即同步" 按钮 |

### 4.2 云端（Supabase）

| 模块 | 职责 |
|---|---|
| Postgres | 多租户数据存储（每张表 `user_id` 列 + RLS policy） |
| Storage | `.md` 文件（wiki/papers/`*.md`、wiki/concepts/`*.md`、index.md、lint-report.md） |
| Auth | 用户注册、登录、密码重置、JWT 签发 |
| Edge Functions | 不在 v1 范围内（保留扩展空间） |

### 4.3 云后端 FastAPI（与桌面端共享代码库，部署模式不同）

**关键决策：复用同一个 FastAPI codebase，通过环境变量切换部署模式**：

```python
# config.py
DEPLOY_MODE = os.getenv("KNOWRA_DEPLOY_MODE", "local")
# "local"  → SQLite, no auth, single user, Codex enabled
# "cloud"  → Postgres (Supabase), JWT auth, multi-tenant, no Codex
```

**云模式与本地模式的差异**：

| 项 | local | cloud |
|---|---|---|
| 数据库 | SQLite (`data/knowledge.db`) | Supabase Postgres |
| Auth | 无 | JWT 中间件（验证 Supabase token） |
| Codex CLI | 可用 | 禁用（cloud 服务器没有 codex） |
| `/api/sync/*` | 提供 push 端点（桌面调云时不用） | 提供 push 端点（接收桌面推送） |
| `/api/cloud/*` | 不提供 | 提供（移动端调） |

部署：cloud 模式打 Docker 镜像跑在 Fly.io / Render 上；
local 模式由 Tauri sidecar 启动。

### 4.4 移动端（React Native）

| 模块 | 职责 |
|---|---|
| Supabase RN SDK | Auth + 实时数据订阅 |
| TanStack Query | 数据 fetch / 缓存 |
| react-native-sqlite-storage | 离线缓存云端快照 |
| react-native-svg + d3-force | 简化版图谱（移动端不用 Cytoscape） |
| react-native-markdown-display | wiki .md 渲染 |
| react-native-pdf | 预留，但 v1 不用（PDF 不上云） |

## 5. 数据流（4 个核心场景）

### 5.1 桌面端用户新加论文 → 移动端看到

```
1. 用户在桌面端 data/papers/ 放新 PDF
2. 桌面端扫描 → 处理 → 编译 → lint 全跑完
3. Sync agent 自动触发 POST /api/sync/push
   → 上传 delta：新 papers row + 新 nodes / edges + 新 .md 文件
4. 云 Postgres 落地 + Storage 落地，标记 sync_revision++
5. 移动端 30 秒后 pull or 用户下拉刷新 → GET /api/cloud/snapshot
6. 移动端 SQLite 更新 + UI 重渲染
```

### 5.2 移动端 Ask

```
1. 用户在移动端输入问题
2. 移动端 POST /api/cloud/ask
   header: Bearer <jwt>
   body: { question, user_openai_key, model? }
3. 云后端：
   - 验证 JWT，取 user_id
   - 从 Postgres 读该 user 的 wiki index + 相关 .md
   - 用 user_openai_key 调 OpenAI（不存留 key，仅本次调用）
   - 返回答案 + 引用
4. 移动端展示答案
```

⚠️ **关键安全细节**：用户 OpenAI key 不存云端 Postgres。
传输只在请求内、不写日志、不写 llm_calls 表（或写时脱敏）。
v2 可以加 KMS 加密存留以支持后台任务。

### 5.3 桌面端处理失败 → 同步状态

```
1. 桌面 pipeline 在某篇论文上失败
2. SQLite papers.processing_status = 'failed'，error 字段填充
3. Sync push 时这一行也被推上去
4. 移动端 GET 看到这条 paper 是 failed，UI 显示警告
   （移动端不能 retry，retry 仍需桌面端）
```

### 5.4 用户在桌面端注销当前账号 / 切换账号

```
1. 用户切换到账号 B
2. 桌面端清空本地的 sync state（不删 papers / nodes，只清"上次同步到哪"）
3. 下次 push 会作为全量推送
4. 账号 B 的云端数据与账号 A 完全隔离（RLS 保证）
```

## 6. 安全模型

### 6.1 信任边界

| 边界 | 信任方 | 不信任方 |
|---|---|---|
| 桌面端 ↔ 云后端 | 桌面端被信任为"该用户的设备" | 云端不信任桌面发来的 user_id（用 JWT 重新派生） |
| 移动端 ↔ 云后端 | 同上 | 同上 |
| 用户 A ↔ 用户 B | 都不被信任跨越 | RLS 在 Postgres 强制隔离 |

### 6.2 关键保护项

| 数据 | 保护 |
|---|---|
| 用户密码 | 仅 Supabase Auth 哈希存储，FastAPI 不接触 |
| JWT | HTTPS only；客户端存 secureStore（RN: expo-secure-store；桌面: keychain） |
| 用户的 OpenAI key | 仅传输不存留；FastAPI 在 Ask handler 内即用即弃 |
| 已编译 .md | 用户的；其他用户看不到（RLS） |
| PDF | 永远不出本机 |

### 6.3 已知威胁与对策

| 威胁 | 对策 |
|---|---|
| JWT 泄漏 | 短期 token + refresh；客户端用 secure storage |
| RLS policy 写漏 | Phase 5 专门一轮安全审计 + 端到端测试（用户 A 尝试访问用户 B 数据） |
| 用户 OpenAI key 在日志中泄漏 | Ask handler 用专门的脱敏 logger；review checklist |
| 桌面端被入侵 → 推送脏数据 | 云端有最近 30 天 sync revision 记录；可回滚 |
| 拒绝服务（Ask 过量） | 云后端按 user_id rate limit；超额拒绝 |

## 7. 显式不做（截至 v1）

| 不做 | 原因 |
|---|---|
| PDF 上云 | 隐私护城河 |
| 移动端的 pipeline | 让桌面做；移动端只读 |
| 双向 sync | 留给 Phase 4 |
| 自己写 Auth | Supabase 提供 |
| 移动端 cytoscape 移植 | 用简化版图（d3-force 节点） |
| 移动端 PWA + 原生都做 | 一条路走到底 |
| 多设备桌面端 sync | 假设一个账号同时只有一台桌面在用；多设备桌面端写并发留给 v2 |
| 团队 / 共享空间 | Phase 6+ |
| 离线 ingest（移动端添加 PDF） | 与"PDF 本地"不变量直接冲突 |

## 8. 成功标准（Phase 0 完成的判断）

- [ ] 本文档（ARCHITECTURE-CLOUD.md）通过 review
- [ ] SCHEMA-MIGRATION.md 列出全部表的 Postgres 等价 + RLS policy 草案
- [ ] SYNC-PROTOCOL.md 给出 push / pull 的 API 契约（含错误码）
- [ ] PHASE-PLAN.md 把 Phase 1-6 拆到周粒度
- [ ] `supabase/migrations/0001_init.sql` 在本地 supabase CLI 能成功 apply
- [ ] `model_gateway/auth.py` 给出 JWT 验证 interface（空实现）

只有这 6 个交付物都 ready，才能进入 Phase 1（写 sync push 业务代码）。

## 9. 风险跟踪（live document）

> 这是一个活跃文档，每个 Phase 完成后回来更新。

| # | 风险 | 严重度 | 状态 | 缓解 |
|---|---|---|---|---|
| 1 | 用户的 OpenAI key 怎么管 | 🔴 | 未解 | v1 仅传输不存留；v2 加 KMS 加密 |
| 2 | SQLite → Postgres schema 兼容 | 🔴 | 未解 | Phase 0 双跑验证 |
| 3 | App Store 第一次审核被打回 | 🟡 | 未解 | 提前准备 Privacy + 订阅条款 |
| 4 | Tauri + Python IPC | 🟡 | 未解 | 用 Tauri sidecar 启动 uvicorn |
| 5 | 同步冲突（多设备桌面） | 🟢 | 假设单设备 | last-write-wins，监控 |
| 6 | Supabase 免费层超额 | 🟡 | 监控 | 监控告警，超 → 升级或自托管 |
