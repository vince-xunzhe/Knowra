# Knowra 端到端运行手册

把整套链路（桌面端 → 云端 → 移动端）从零跑通。读这个文件，按顺序执行。

每条命令旁注明预期输出；偏离了就停下来定位。

---

## 0. 拓扑速览

```
┌──────────────┐  push (3-step    ┌──────────────────────────┐
│  桌面 Knowra │  prepare/upload  │   Cloud FastAPI on Fly   │
│   (Tauri)    │ ───────────────▶ │  + Supabase Postgres/Stg │
└──────────────┘     commit       └──────────────────────────┘
   ▲                                          │
   │ 本地 SQLite (data/knowledge.db)          │ snapshot + Ask
   │ + 本地 wiki/.md + PDF (永远不出本机)     │
                                              ▼
                                   ┌─────────────────────┐
                                   │  iOS / Android      │
                                   │  (Expo / RN)        │
                                   └─────────────────────┘
```

四个角色：本地 SQLite（桌面真数据） → 云后端（同步中继 + Ask agent） → Supabase
（Postgres + Storage） → 移动端（只读消费 + Ask）。

PDF 和 OpenAI key 永远只在本机；云端不存任意一项。

---

## 阶段 1 · 本地桌面端跑通（前置：你已在做）

### 1.1 装依赖 + 启动

```bash
cd ~/Documents/knowledge-tree
./start.sh
```

期望：
- 终端打印 `Starting backend on http://localhost:8000`
- 终端打印 `Starting frontend on http://localhost:5173`
- 浏览器自动开 `http://localhost:5173`

### 1.2 健康检查

```bash
curl -s http://localhost:8000/api/papers | head -c 200
```

期望前缀：`[{"id":...,"filename":"...","title":...`

如果是 `[]`：桌面 DB 是空的（新装机器）。先到「资料」页扫描 PDF + 处理。

如果是 500：看 uvicorn 日志，最可能是 SQLite schema 漂移。`backend/database.py:_migrate()`
应处理新列；如果还是炸，删 `data/knowledge.db` 重新扫（**会丢数据，最后手段**）。

### 1.3 浏览器侧检查

UI 上确认能看到：
- 知识：图谱渲染节点
- 回顾：论文列表
- 资料：论文卡片
- 看板：统计卡片
- 设置：能看到「云同步」section

任何一项卡在「加载中…」超过 5 秒：打开 DevTools (Cmd+Opt+I) → Console 抓红字 + Network
抓 4xx/5xx 接口。

---

## 阶段 2 · 多租户迁移（一次性，且**不可逆**）

桌面 SQLite 历史上 id 是 INTEGER；云端 schema 要求 UUID 字符串。这步把所有
id 改写为 UUID + 加 `user_id` 列 + 把 `source_paper_ids` 里的 int 改字符串。

⚠️ **必做备份**：

```bash
cp data/knowledge.db data/knowledge.db.before-multitenant.bak
```

dry-run 看影响范围（不写库）：

```bash
backend/.venv/bin/python -m backend.scripts.migrate_multitenant --dry-run
```

期望输出：报告会动多少行、哪些表被改 schema。

正式跑（看完 dry-run 没问题）：

```bash
backend/.venv/bin/python -m backend.scripts.migrate_multitenant --confirm
```

期望：

```
✓ papers: 37 rows rewritten (INT → UUID)
✓ knowledge_nodes: 625 rows rewritten
✓ knowledge_edges: 4265 rows rewritten
✓ source_paper_ids JSON rewritten: 423 nodes
✓ _meta marker written
```

之后**再不要重复跑** — migrator 检测到 `_meta` 标记会拒绝二次执行。

### 2.x 自检

```bash
backend/.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/knowledge.db')
row = c.execute('SELECT id FROM papers LIMIT 1').fetchone()
print('paper.id =', row[0], type(row[0]).__name__)
"
```

期望：`paper.id = <某个 UUID 字符串> str`。如果还是 int，迁移没生效，回滚备份。

---

## 阶段 3 · Supabase 项目准备

### 3.1 创建项目

到 [supabase.com](https://supabase.com)，新建 project。等 ~2 分钟初始化完成。

### 3.2 抄三个值（一会儿三处都要填）

Settings → API：

- **Project URL** → 给 `SUPABASE_PROJECT_URL` 和移动端用
- **service_role key** → 给云后端 `SUPABASE_SERVICE_ROLE_KEY`（**绝不能进前端**）
- **anon public key** → 给桌面端 + 移动端 UI 用

Settings → API → JWT Settings：

- **JWT Secret** → 给云后端 `SUPABASE_JWT_SECRET`

### 3.3 应用 SQL migrations

```bash
brew install supabase/tap/supabase    # 一次性
cd ~/Documents/knowledge-tree
supabase link --project-ref <你的 project ref>
supabase db push
```

或者手工：Supabase 控制台 → SQL Editor → 依次粘 `supabase/migrations/0001_meta.sql`
… `0005_cloud_llm.sql`。

期望：5 个 migrations 全 success；Database → Tables 能看到 `papers`,
`knowledge_nodes`, `knowledge_edges`, `wiki_files`, `sync_sessions`,
`cloud_deletions`, `cloud_llm_calls`, `cloud_revisions`, `user_profiles`。

### 3.4 创建 Storage bucket

控制台 → Storage → Create bucket：

- 名字：`wiki`
- Public：**关闭**

然后 Storage → Policies → New policy → 粘 `docs/DEPLOY-CLOUD.md §2.2` 里的 SQL。

### 3.5 启用 Email auth

Authentication → Providers → Email：开启（默认 on）。

---

## 阶段 4 · 部署云后端到 Fly.io

### 4.1 装 + 登录 fly

```bash
brew install flyctl
fly auth signup   # 或 fly auth login
```

### 4.2 创建 app

```bash
cd ~/Documents/knowledge-tree
fly launch --name knowra-cloud --region nrt --no-deploy
```

Fly 会提示一堆选项 — 大多选默认。

### 4.3 编辑生成的 `fly.toml`

照 `docs/DEPLOY-CLOUD.md §3.2` 改成：

```toml
app = "knowra-cloud"
primary_region = "nrt"

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

### 4.4 注入 secrets

```bash
fly secrets set \
  SUPABASE_PROJECT_URL=https://xxxxx.supabase.co \
  SUPABASE_SERVICE_ROLE_KEY=eyJ... \
  SUPABASE_JWT_SECRET=...
```

### 4.5 部署

```bash
fly deploy
```

等 2–4 分钟。期望末尾：`✔ Deployment finished` + 给出 `https://knowra-cloud.fly.dev` URL。

### 4.6 健康检查

```bash
curl -i https://knowra-cloud.fly.dev/api/cloud/me
```

期望：

```
HTTP/2 401
{"detail":{"error":"token_missing","message":"..."}}
```

401 不是错，是正确的「这接口需要 JWT，你没带，所以拒绝」。如果是 500，看 `fly logs`。

---

## 阶段 5 · 桌面端连云 + 第一次同步

### 5.1 配置 + 登录

桌面 UI → 设置 → 云同步 → 展开：

- Supabase URL：`https://xxxxx.supabase.co`
- Supabase anon key：`eyJ...`
- 云后端 URL：`https://knowra-cloud.fly.dev`

点保存。切到「注册」tab，邮箱 + 密码，注册（如果 Supabase 开了 email confirm，到邮箱点确认链接然后回来登录）。

### 5.2 第一次推

回知识页 → 流水线控制台 → ⑤ 同步 → 「立即同步」。

期望进度条：
- 「准备中」（拉本地快照 + 调 /prepare）
- 「上传中 N/N」（每篇 wiki 文件一个 PUT）
- 「提交中」（调 /commit）
- 绿色 ✓「已同步 · revision 1 · papers 37 / nodes 625 / edges 4265 / wiki XX」

如果失败：
- 「准备中」失败：看 fly logs，可能是 schema 没 push、auth 没配
- 「上传中」失败：Storage bucket 不存在或 RLS 策略错
- 「提交中」失败：HEAD check 失败（大概率 bucket 配置）

### 5.3 云端自检

```bash
TOKEN=$(浏览器 DevTools Console: localStorage.getItem('knowra.cloud.session') 然后 .access_token)

curl -H "Authorization: Bearer $TOKEN" https://knowra-cloud.fly.dev/api/cloud/me
```

期望返回你的 user_id + stats（papers/concepts/edges/wiki_files 计数应等于你刚同步的数字）。

---

## 阶段 6 · 移动端跑起来

### 6.1 装 + 启动

```bash
cd ~/Documents/knowledge-tree/mobile
npm install         # 第一次跑要 1-2 分钟
npm start
```

期望：终端出现 QR 码 + 提示 `Expo Go: scan with iOS camera`。

### 6.2 iPhone 上扫码

1. iPhone 装 [Expo Go](https://apps.apple.com/us/app/expo-go/id982107779)（免费）
2. 手机 + Mac 同 Wi-Fi
3. iPhone 原生「相机」app 扫终端 QR → 跳转 Expo Go 加载

期望：app 启动到登录页，下方两个 tab：登录 / 设置。

### 6.3 配置 + 登录

- 设置 tab → 填三个 URL/key（同桌面端） + OpenAI API key → 保存
- 登录 tab → 用桌面端注册过的邮箱密码 → 登录

期望：自动跳到主 4 tab（资料 / 概念 / Ask / 设置）。资料 tab 应能看到桌面同步上去的全部论文。

### 6.4 端到端验

| 动作 | 期望 |
|---|---|
| 资料 tab → 下拉刷新 | spinner → 列表更新 |
| 点任意一篇带 wiki 的论文 | 跳详情，看到 markdown 原文 |
| 概念 tab → 任意 promoted 概念 | 看到该概念的 wiki 页 |
| Ask tab → 提问"什么是 RoPE?" | 转圈 5-30s → 出现答案 + 引用列表 |
| 设置 tab → 登出 | 回到 登录 / 设置 两 tab 状态 |

---

## 阶段 7 · 故障应急

| 现象 | 优先查 |
|---|---|
| 桌面同步「准备中」失败 | `fly logs` 看 /api/sync/prepare 返回；多半是 JWT secret 或 schema |
| 桌面同步「上传中」失败 | Supabase Storage bucket 名字（必须 `wiki`）+ RLS policy 是否粘对 |
| 移动端登录 401 | Supabase anon key 复制时漏了字符；URL 末尾不能带 `/` |
| 移动端列表空但桌面已同步 | 接 user_id 不匹配（用别的账号同步的）；登出重登 |
| Ask 永远转圈 | 看 fly logs：要么 OpenAI key 错，要么 cloud_ask rate limit（60 calls / 5 min） |
| `pg_config not found` 安装报错 | 已修；如果还出，跑 `pip install -r backend/requirements.txt` 单独装 |

具体每个表的 RLS / cross-user trigger 设计：`docs/SCHEMA-MIGRATION.md`。同步协议字段：
`docs/SYNC-PROTOCOL.md`。架构总览：`docs/ARCHITECTURE-CLOUD.md`。

---

## 阶段 8 · 上架 App Store（不在 v1 范围）

需要：
- 苹果开发者账号（$99/年）
- EAS Build：`npx eas build --platform ios`
- TestFlight 内测
- App Review 提交

时机：等核心功能在内测稳定 1 个月后再做。当前 mobile/ 用 Expo Go 跑就够日常用。

---

## 当前 TODO（截至本运行手册写完时）

- [x] 桌面端端到端
- [x] 多租户迁移代码
- [x] Cloud FastAPI（路由 + 测试 178 个）
- [x] 桌面端「云同步」UI + 同步 agent
- [x] 移动端 Expo 原型（5 个 screen）
- [ ] **你要做**：阶段 2-6（迁移 + 部署 + 跑通）
- [ ] mobile 加 markdown 富文本渲染（按需）
- [ ] mobile 离线缓存（按需）
- [ ] App Store / TestFlight（远期）
