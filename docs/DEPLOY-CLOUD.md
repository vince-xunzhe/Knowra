# Cloud FastAPI 部署指南

桌面端依然由 Tauri / 本地 uvicorn 自起；这份文档只覆盖**云后端**的部署
（接收桌面端 sync push + 给移动端提供 `/api/cloud/*`）。

## 1. 前置准备

| 资源 | 用途 | 申请位置 |
|---|---|---|
| Supabase 项目 | Postgres + Auth + Storage | https://supabase.com 控制台 |
| Fly.io 账号 | 跑 FastAPI 容器 | https://fly.io |
| Docker | 本地构建镜像 | brew install docker |

从 Supabase 项目 → Settings → API 复制：
- `Project URL`（`SUPABASE_PROJECT_URL`）
- `service_role key`（`SUPABASE_SERVICE_ROLE_KEY`，⚠️ 仅服务器用）
- `JWT Secret`（`SUPABASE_JWT_SECRET`，从 Settings → API → JWT Settings）

## 2. 一次性的 Supabase 准备

### 2.1 应用 SQL 迁移

```bash
# 安装 supabase CLI
brew install supabase/tap/supabase

# 链接到你的项目
supabase link --project-ref <your-project-ref>

# 跑迁移
supabase db push
```

或手动到 SQL Editor 依次粘贴：
- `supabase/migrations/0001_meta.sql`
- `supabase/migrations/0002_papers.sql`
- `supabase/migrations/0003_knowledge.sql`
- `supabase/migrations/0004_wiki.sql`
- `supabase/migrations/0005_cloud_llm.sql`

### 2.2 创建 Storage bucket

到 Supabase 控制台 → Storage → Create bucket：
- 名称：`wiki`
- Public：**关闭**（私有，靠预签名 URL 访问）

bucket 策略（在 Storage → Policies）：

```sql
-- 服务端 service_role 可以读写任何对象
CREATE POLICY "service_role full access" ON storage.objects
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 普通用户仅能读自己 user_id 路径下的文件（预签名也走这条）
CREATE POLICY "user reads own wiki" ON storage.objects
  FOR SELECT TO authenticated
  USING (
    bucket_id = 'wiki' AND
    split_part(name, '/', 2) = auth.uid()::text
  );
```

### 2.3 配置 Auth providers

到 Authentication → Providers：
- Email：开启
- Google（可选）：开启 + 配 OAuth client_id / secret

## 3. Cloud FastAPI 容器

### 3.1 Dockerfile

项目根部已有 `backend/Dockerfile`（local 用），用同一个但启动命令改为 cloud 模式：

```dockerfile
# 已存在的 Dockerfile 可直接复用；通过环境变量切换
ENV KNOWRA_DEPLOY_MODE=cloud
ENV KNOWRA_STORAGE_BACKEND=          # 留空 → 走真实 SupabaseStorage
```

### 3.2 Fly.io 部署

```bash
fly launch --name knowra-cloud --region nrt --no-deploy
```

编辑生成的 `fly.toml`：

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

设置 secrets（不进 Git）：

```bash
fly secrets set \
  SUPABASE_PROJECT_URL=https://xxx.supabase.co \
  SUPABASE_SERVICE_ROLE_KEY=eyJ... \
  SUPABASE_JWT_SECRET=your-jwt-secret
```

部署：

```bash
fly deploy
```

部署完后端 URL 类似 `https://knowra-cloud.fly.dev`。

## 4. 验证

```bash
# 健康检查（不需要 auth，应返回 401 而非 500）
curl -i https://knowra-cloud.fly.dev/api/cloud/me

# 应得到：
# HTTP/1.1 401 Unauthorized
# {"detail":{"error":"token_missing","message":"..."}}
```

桌面端配置同步目标：

```bash
# 在桌面端 settings 里填入
KNOWRA_CLOUD_BASE_URL=https://knowra-cloud.fly.dev
```

（W5 frontend 完成后会有 UI 配置界面）

## 5. 监控 / 告警

| 项 | 阈值 | 操作 |
|---|---|---|
| Supabase DB 用量 | > 400MB（免费层 500MB） | 升级到 Pro $25/月 |
| Supabase Storage | > 800MB（免费层 1GB） | 同上 |
| Supabase 月活用户 | > 40000（免费层 50000） | 同上 |
| Fly.io machines | 自动起停足够 | 不用动 |
| 单用户 Ask 429 占比 | > 5% | 检查是否被滥用，考虑加重 rate limit |

监控通道：
- Supabase → Reports → 看 DB / Storage / Auth 用量
- Fly.io → Apps → Metrics → 看 RPS / 错误率
- 桌面端日志 → 看 sync push 失败重试

## 6. 应急

| 触发条件 | 操作 |
|---|---|
| Supabase 整体宕机 | 桌面端继续本地工作，sync push 重试机制会兜底；移动端断网 |
| Fly.io app 宕机 | 同上 |
| 发现 RLS 漏洞 | 立即 `fly secrets unset` 移除 service_role key，桌面端 sync 会断；修复后重新配置 |
| 用户的 OpenAI key 疑似泄漏（不应该发生） | 我们不存任何 key，从 Sentry / 日志重新审计 ；通知用户在 OpenAI 后台 revoke key |
| Postgres 数据丢失 | Supabase 每日自动备份；恢复到 yesterday 之前的 snapshot |

## 7. 成本估算（闭包内测，1 个用户）

| 项 | 月成本 |
|---|---|
| Supabase Free Tier | $0 |
| Fly.io（256MB machine, auto-stop） | ~$0–$2 |
| OpenAI（用户自付） | $0（不计你的成本） |
| **总计** | **< $2/月** |

公开上架后，按 100 个活跃用户估：
- Supabase Pro $25/月
- Fly.io 2 个 always-on machines ~$10/月
- 总计 ~$35/月

## 8. 下一步（部署完后）

- 用 `supabase functions deploy` 部署一个每小时跑一次的 GC 函数清理过期 `sync_sessions` 和 `cloud_deletions` tombstones（> 90 天）
- 在 cloud FastAPI 加 Sentry 错误监控
- 加 Grafana / Datadog 看核心指标
