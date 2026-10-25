# 本机 Web 安装说明

[中文](INSTALL.zh.md) | [English](INSTALL.md)

每轮先从最新 main 建立独立 worktree 和分支，详见[开发工作区与验收](docs/DEVELOPMENT.md)。不要共享旧目录的虚拟环境、node_modules 或研究数据。

## 运行时

- Python **3.12.13+，限 3.12.x**，见 `.python-version`。
- Node **24.14.1+，限 24.x** 与 npm，见 `.node-version`。
- PATH 中可用的 `uv`；本轮 Python 锁由 uv 0.11.14 生成。

这是本机 Web 基线，不代表移动端/容器版本要求。安装和离线验收无需真实 API Key。

## 显式安装与验证

```bash
PYTHON_BIN=python3.12 ./scripts/install-native.sh
./scripts/check.sh
./start.sh --check
```

安装入口在当前 worktree 创建 `backend/.venv`，从官方 PyPI 安装 `backend/requirements-dev.lock` 中有哈希的 wheel，并从官方 npm 按现有锁执行 `npm ci --ignore-scripts`。缓存及空 npm 配置都在 `.cache/`，不启动服务、不迁移数据库、不重签名或修改系统权限。已有不兼容 Python 环境会被拒绝，不自动覆盖。

验收入口运行隔离后端子集、polling 单测、TypeScript/Vite 构建、完整 ESLint（零警告）和真实 React 组件的 mock 浏览器回归。需已安装 Chrome/Chromium，不自动下载浏览器；任何失败或缺少前置条件均返回非零。浏览器使用临时 profile 和随机回环端口，全部 API mock，外网请求被阻断；不运行 PostgreSQL、真实服务或现有整站 E2E。`start.sh --check` 只做启动前检查，不导入应用。浏览器配置见[开发验收说明](docs/DEVELOPMENT.md)。

## 显式启动

```bash
BACKEND_PORT=8103 FRONTEND_PORT=5183 ./start.sh
```

并行 worktree 各用一对空闲端口，占用时明确失败。启动只使用已安装依赖，不自动安装包、选 Docker、启 Colima、重签名原生模块或打开浏览器。可显式设 `KNOWLEDGE_WIKI_MODE=native`；旧 `auto`/`docker` 值会被拒绝并说明原因。现有 Docker 文件保持不变，容器操作仍需另行明确选择。

API 启动可能初始化/迁移本 worktree 的 `data/` 并按既有策略启动独立 worker。检测到已有 SQLite 时，需先确认备份和迁移影响，再设置 `KNOWRA_ALLOW_EXISTING_DATA=1`。不允许链接其他工作区数据或使用外部队列路径。关闭 API/Web **不会停止独立 worker**，须遵循[后台任务说明](docs/TASK-WORKERS.md)。

真实凭据可以在明确授权的运行会话中通过应用设置配置。根 `.env` 默认不读取；明确需要本 worktree 的配置时设 `KNOWRA_LOAD_ENV=1`，由 Uvicorn 解析环境值，不作为 shell source。不得提交真实凭据。后端热重载仍仅在 `BACKEND_RELOAD=1` 时开启。

数据备份目标、加密和保留策略由用户决定后再实施，清单见开发文档。本轮没有自动备份、数据库复制或两机同步。完成开发、验证、review修复及冲突核验后，在当轮发布授权范围交付非 Draft PR，由用户 merge。
