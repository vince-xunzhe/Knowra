# 分期执行计划

> 总周期：**5 个月**（1 人全职估算）；闭包内测，仅 1 个用户但按 SaaS 架构建。

| Phase | 内容 | 工期 | 阻塞依赖 |
|---|---|---|---|
| **0** | 设计冻结（本批文档） | 1 周 | — |
| **1** | 鉴权 + 云数据层 + sync push | 5-6 周 | Phase 0 |
| **2** | Tauri 桌面打包 | 2 周 | 可与 Phase 1 末并行 |
| **3** | RN 移动端 read-only | 6-8 周 | Phase 1 完成 |
| **4** | 移动端 Ask + 双向 sync 准备 | 2-3 周 | Phase 3 |
| **5** | 多租户审计 + 性能 | 2-3 周 | Phase 4 |
| **6** | App Store / Play Store 提交 | 2-3 周 | Phase 5 |

---

## Phase 0 · 设计冻结（W1）

### 交付物（review 通过即关闭）

- [x] `docs/ARCHITECTURE-CLOUD.md`
- [x] `docs/SCHEMA-MIGRATION.md`
- [x] `docs/SYNC-PROTOCOL.md`
- [x] `docs/PHASE-PLAN.md`（本文档）
- [ ] `supabase/migrations/0001_init.sql`（可在本地 supabase CLI apply）
- [ ] `model_gateway/auth.py` interface 骨架（无实现）

### 验收

- review 这 6 个文档 + 文件，确认没有遗漏的架构决策
- 在本地 `supabase start` 跑通 `supabase migration up`
- 用 dummy JWT 测 `verify_jwt()` interface 的输入输出形状

---

## Phase 1 · 鉴权 + 云数据层 + sync push（W2-W7）

### W2：环境搭建 + 鉴权框架

- [ ] 注册 Supabase project（dev + staging）
- [ ] 配置 Supabase Auth：开启 email + Google OAuth
- [ ] 后端添加 `KNOWRA_DEPLOY_MODE` 环境变量切换
- [ ] `model_gateway/auth.py` 完整实现：`verify_jwt(token) → user_id`
- [ ] FastAPI dependency `Depends(current_user)` 验证 + 缓存
- [ ] 写 unit test：合法 / 过期 / 篡改 JWT 三类

### W3：本地 SQLite → UUID + user_id 迁移

- [ ] backend/database.py 添加 `_migrate_to_multitenant()`
- [ ] 创建 `papers_id_remap` 等 3 张临时映射表
- [ ] INT id → UUID 重写所有 4 张主表
- [ ] knowledge_nodes.source_paper_ids JSON 重写
- [ ] knowledge_edges.source_id/target_id 重写
- [ ] 跑通：32 papers + 523 nodes + 3635 edges 完整迁移
- [ ] 对比迁移前后行数 / 关键聚合查询结果一致

### W4：Postgres schema + RLS

- [ ] `supabase/migrations/0002_papers.sql` papers 表完整 schema
- [ ] `supabase/migrations/0003_knowledge.sql` knowledge_nodes + knowledge_edges
- [ ] `supabase/migrations/0004_wiki.sql` wiki_files + sync_state + user_profiles
- [ ] `supabase/migrations/0005_cloud_llm.sql` cloud_llm_calls
- [ ] 所有表的 RLS policy + edge consistency trigger
- [ ] 写端到端隔离测试：用户 A 不能读 / 写用户 B 的数据

### W5：sync prepare + commit backend

- [ ] `backend/routers/sync.py` 新增 `POST /api/sync/prepare` 和 `POST /api/sync/commit`
- [ ] payload 验证：所有 user_id == auth.uid()
- [ ] prepare 流程：
  - [ ] 写 staging（pending session 表，1h TTL）
  - [ ] 对每个 wiki_files 查 content_hash → 决定 uploads_required vs uploads_skipped
  - [ ] 调 Supabase Storage SDK 签发 PUT URL（10 min 有效）
  - [ ] 返回 sync_session_id + uploads_required[]
- [ ] commit 流程：
  - [ ] 校验 sync_session_id 有效性 + ownership
  - [ ] 对每个 uploaded 文件 Storage HEAD 校验 content_hash
  - [ ] staging → 正式表的 upsert（按 id + updated_at watermark）
  - [ ] revision++ 返回 accepted/rejected 报告
- [ ] 1h GC：未 commit 的 staging session 自动清理（含已上传到 Storage 的 orphan 文件）
- [ ] 错误码 401/403/410/413/429 全覆盖

### W6：sync frontend (desktop)

- [ ] frontend 添加 Settings 页"云同步"section（登录/注销/状态）
- [ ] PipelineConsole 加 ⑤ 同步 stage（可见同步状态）
- [ ] pipeline 跑完自动触发 prepare → 并发 PUT → commit 三步流程
- [ ] 并发上传控制（默认 6 并发，配置可调）
- [ ] 文件级进度反馈（"上传中 5/12"）
- [ ] 中断恢复：commit 失败 → 重 commit；prepare 失败 → 整个重来
- [ ] device_id 持久化（首次启动生成，存 macOS keychain / Windows credential store）

### W7：cloud snapshot 端点 + 内测

- [ ] `GET /api/cloud/snapshot` 实现（cloud 模式专属）
- [ ] `GET /api/cloud/me` 实现
- [ ] `GET /api/cloud/wiki/{file_id}` 实现（302 to Storage）
- [ ] 部署 cloud FastAPI 到 Fly.io / Render
- [ ] 你（首个用户）完成完整 push → snapshot 流程
- [ ] 监控指标：push 延迟、snapshot 大小、上传带宽

### 验收

- [ ] 桌面端添加新论文 → 处理完 → 自动同步 → 云 PG 能查到
- [ ] 重启桌面端，再次同步是 no-op（幂等：prepare 返回 0 uploads_required）
- [ ] 单文件内容修改后再同步 → 只这一个文件出现在 uploads_required
- [ ] 删除论文 → 推送 → 云端也删除（包括 Storage 上的 .md）
- [ ] curl `/api/cloud/snapshot` 返回正确数据
- [ ] 用 Postman 拿别人的 JWT 查询自己数据被 RLS 阻断
- [ ] 模拟"PUT 一半网络断" → 重连后只重传剩余文件
- [ ] 模拟"commit 5xx" → 重 commit 安全幂等
- [ ] 1h 未 commit 的 staging 自动 GC（含 Storage 上的 orphan 文件）

---

## Phase 2 · Tauri 桌面打包（W6-W7 并行）

### W6：Tauri 骨架

- [ ] `npm create tauri-app@latest` 起 desktop wrapper
- [ ] Rust main.rs：启动 uvicorn 子进程 + 等待端口就绪
- [ ] Tauri sidecar 配置：把 FastAPI Python 打进包
- [ ] localhost:8000 → React 前端正常加载

### W7：打包 + 分发

- [ ] macOS DMG + Windows MSI + Linux AppImage
- [ ] 自动签名（Apple Developer Account 配置）
- [ ] 自动更新（Tauri updater + GitHub releases）
- [ ] icon、metadata、关于页面

### 验收

- [ ] 下载 DMG → 拖入应用 → 双击启动 → 看到主界面
- [ ] 第一次启动生成 device_id，存到 macOS keychain
- [ ] 关闭 app 时优雅停止 uvicorn

---

## Phase 3 · RN 移动端 read-only（W8-W15）

### W8：环境 + 鉴权

- [ ] `npx create-expo-app mobile`
- [ ] 安装 `@supabase/supabase-js`，配置 client
- [ ] 登录 / 注销 / 注册 UI
- [ ] expo-secure-store 存 JWT
- [ ] 自动 refresh token

### W9：数据层

- [ ] react-native-sqlite-storage 本地缓存 schema
- [ ] TanStack Query + 自定义 Supabase fetcher
- [ ] snapshot 拉取 + diff 落库
- [ ] 离线模式：无网络时读本地缓存

### W10-W11：浏览类页面

- [ ] 资料页：论文卡片列表，搜索
- [ ] 单篇论文详情：结构化字段渲染（与桌面 ReviewPage 一致字段顺序）
- [ ] 概念列表
- [ ] 单个概念详情：渲染 .md（react-native-markdown-display）

### W12-W13：图谱可视化

- [ ] 用 react-native-svg + d3-force-3d 实现简化图谱
- [ ] 节点 = 圆点；点击查看 detail
- [ ] 双指缩放、单指拖拽
- [ ] 节点类型颜色与桌面一致

### W14：搜索 + 看板

- [ ] 全局搜索：调 `/api/cloud/wiki/search`
- [ ] 简化版 dashboard：总览 + 标签云 + 中枢概念 Top-10

### W15：联调 + bugfix

- [ ] 整个 RN app 跑稳
- [ ] Expo dev build 在你的真机上测
- [ ] 性能：list 3000+ 节点不卡

### 验收

- [ ] 登录 → 看到与桌面同步过去的全部数据
- [ ] 离线时能浏览缓存
- [ ] 任意页面下拉刷新触发增量 snapshot
- [ ] 在桌面新加一篇 → 移动端下拉刷新可见

---

## Phase 4 · 移动端 Ask（W16-W17）

### W16：Ask UI + Auth

- [ ] 设置页：用户输入自己的 OpenAI key + 选模型
- [ ] key 存 expo-secure-store（不传云）
- [ ] Ask 页：输入框 + 答案区 + 引用列表 + trace 折叠

### W17：Ask 后端

- [ ] cloud FastAPI 添加 `POST /api/cloud/ask`
- [ ] 仅请求 lifecycle 内使用 user 的 OpenAI key
- [ ] cloud_llm_calls 表只记 meta，不记 prompt/response 内容
- [ ] rate limit + 错误码处理

### 验收

- [ ] 用户填 OpenAI key 后能成功 Ask
- [ ] 重启 app key 还在（secure store）
- [ ] DB 检查：cloud_llm_calls 没记任何 prompt 内容
- [ ] 没 key 时 Ask 给出友好提示

---

## Phase 5 · 多租户审计 + 性能（W18-W20）

### W18：安全审计

- [ ] 创建 2 个测试用户
- [ ] 手工构造 user A 的 JWT 访问 user B 资源 → 必须全部 401/403
- [ ] 检查所有日志：没有 OpenAI key、没有 raw prompt
- [ ] 检查所有 query：都经过 RLS（手动 EXPLAIN 一遍）

### W19：性能

- [ ] 桌面端 push 1000+ 行的延迟
- [ ] 移动端 snapshot 1000+ 行的延迟
- [ ] Supabase 监控：DB connections / Storage usage / Auth 调用量

### W20：监控 + 告警

- [ ] Cloud FastAPI 接 Sentry
- [ ] Supabase Storage usage 告警（接近 1GB）
- [ ] Supabase DB usage 告警（接近 500MB）
- [ ] cloud_llm_calls 异常告警（429 比例 > 5%）

### 验收

- [ ] 跨租户访问全部阻断
- [ ] 所有指标在合理区间
- [ ] 告警 channel 通了

---

## Phase 6 · 上架（W21-W23）

### W21：iOS 上架准备

- [ ] Apple Developer 账号
- [ ] App Store Connect app record
- [ ] 应用图标、截图、描述、隐私政策
- [ ] 用户隐私问卷（"我不收集 PDF" 是关键卖点）
- [ ] TestFlight 内测分发

### W22：Android 上架准备

- [ ] Google Play Console 账号
- [ ] Play Store 内测 track
- [ ] 同样的素材
- [ ] Internal testing 分发

### W23：审核 + 修复

- [ ] App Store 审核（首次通常 1-2 周）
- [ ] Google Play 审核（通常更快，1-3 天）
- [ ] 处理 reviewer 反馈
- [ ] 正式发布

### 验收

- [ ] 任意人从 App Store 下载 → 注册 → 看到自己的数据
- [ ] 任意人从 Google Play 下载 → 同上

---

## 跨 Phase 持续做的事

| 事项 | 频率 |
|---|---|
| 写 unit test + 集成 test | 每个 PR |
| 更新 `ARCHITECTURE-CLOUD.md` 的 "风险跟踪" section | 每周 |
| 监控 Supabase 用量 | 每周 |
| Review 客户端 secrets 处理（OpenAI key、JWT）| 每月 |
| 备份 Supabase 数据 | 每周 |

---

## 风险触发的应急计划

| 触发条件 | 应急动作 |
|---|---|
| Supabase 免费层超额且不想付费 | 迁到 Cloudflare Workers + D1（要重写一些 SQL） |
| App Store 拒审 5 次以上 | 评估是否回退到只发 Web 移动版本 |
| 桌面端 Codex CLI 用户出现并发问题 | 临时关闭桌面端的并发同步，改为顺序队列 |
| Multi-tenant 漏洞被发现 | 立即停止接收新用户注册，48 小时内修补 |

---

## 出口条件（什么时候可以宣布 v1 完成）

所有都满足才算：

- [ ] 桌面端从安装到运行 < 30 秒
- [ ] 桌面端 sync 端到端无错 7 天
- [ ] 移动端从打开到看到数据 < 3 秒（缓存命中）
- [ ] 移动端 Ask 端到端无错 7 天
- [ ] 多租户审计通过
- [ ] App Store + Play Store 都上架成功
- [ ] 你自己作为唯一用户使用 2 周没有阻塞 bug
