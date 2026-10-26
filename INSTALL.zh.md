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
./scripts/install-native.sh
./scripts/check.sh
./start.sh --check
```

安装入口读取当前目录的成功安装记录；全新目录创建 `backend/.venv`，发现未托管或旧版环境时改建 `backend/.venv-native-py312`，保留原 `.venv` 和已有 `.venv-py312`。随后从官方 PyPI 安装 `backend/requirements-dev.lock` 中有哈希的 wheel，并从官方 npm 按现有锁执行 `npm ci --ignore-scripts`。缓存及空 npm 配置都在 `.cache/`，不启动服务、不迁移数据库、不重签名或修改系统权限。不会删除、移动或覆盖旧环境。安装器通过 `python3.12` 或 `uv` 寻找已安装的基线 Python，不自动下载运行时；也可显式指定 `PYTHON_BIN=/absolute/path/to/python3.12`。系统引导 Python 可以较旧，应用仍严格要求 3.12.13+／3.12.x。若 PATH 选到其他 Node 主版本，为相关命令加 `NODE_BIN=/absolute/path/to/node24/bin/node`，不改全局设置。

安装未完成会记录 `installing` 状态，启动拒绝使用；重跑安装器可继续补齐它自己创建的环境。未受记录管理、恰好占用目标路径的环境不会被覆盖。

验收入口先运行直接调用真实 shell 启动／安装入口的临时夹具回归（服务为 mock），再运行隔离后端子集、polling 单测、TypeScript/Vite 构建、完整 ESLint（零警告）和真实 React 组件的 mock 浏览器回归。需已安装 Chrome/Chromium，不自动下载浏览器；任何失败或缺少前置条件均返回非零。浏览器使用临时 profile 和随机回环端口，全部 API mock，外网请求被阻断；不运行 PostgreSQL、真实服务或现有整站 E2E。`start.sh --check` 只做启动前检查，不导入应用。浏览器配置见[开发验收说明](docs/DEVELOPMENT.md)。

## 显式启动

```bash
BACKEND_PORT=8103 FRONTEND_PORT=5183 ./start.sh
```

并行 worktree 各用一对空闲端口，占用时明确失败。启动只使用已安装依赖，不自动安装包、选 Docker、启 Colima、重签名原生模块或打开浏览器。可显式设 `KNOWLEDGE_WIKI_MODE=native`；旧 `auto`/`docker` 值会被拒绝并说明原因。现有 Docker 文件保持不变，容器操作仍需另行明确选择。

日常使用直接运行 `./start.sh`，默认使用当前目录已有的本地数据；终端和非交互调用均无需确认或数据许可环境开关。环境、路径和端口校验仍在服务启动前执行。

正式启动会读取应用本地配置，可能更新 schema／回填，删除历史 finding 节点、合并或修复记录和边、更新论文记录、重建 wiki 索引，并恢复可能调用外部服务的旧任务／定时推荐。数据库已初始化不代表再次启动只读。不允许链接其他工作区数据或使用外部队列路径。关闭 API/Web **不会停止独立 worker**，须遵循[后台任务说明](docs/TASK-WORKERS.md)。

需要备份时，在启动前选择仓库外受保护的备份位置；脚本没有预选位置，不自动备份。按[备份清单](docs/DEVELOPMENT.md#数据备份待用户决定不自动执行)评估知识库、任务库、推荐库、论文记录与 wiki。应协调 API 和独立 worker 停止写入，或使用一致性 SQLite backup；不能只复制活跃的 `.db` 而遗漏 WAL。对重要已有数据，应先在一致性备份的临时副本上核对 schema 兼容和维护影响再更新；`--check` 不检查数据库 schema，也不保证无需迁移。

真实凭据可以在明确授权的运行会话中通过应用设置配置。根 `.env` 默认不读取；明确需要本 worktree 的配置时设 `KNOWRA_LOAD_ENV=1`，由 Uvicorn 解析环境值，不作为 shell source。不得提交真实凭据。后端热重载仍仅在 `BACKEND_RELOAD=1` 时开启。

数据备份目标、加密和保留策略由用户决定后再实施，清单见开发文档。本轮没有自动备份、数据库复制或两机同步。完成开发、验证、review修复及冲突核验后，在当轮发布授权范围交付非 Draft PR，由用户 merge。

## 更新后如何在日常 main 目录启动

合并代码不会自动迁移被 Git 忽略的本地环境。在日常目录先运行一次 `./scripts/install-native.sh`，再运行 `./start.sh --check`。如果原 `backend/.venv` 是 Python 3.9，安装器会保留它，另建并登记 Python 3.12 环境；以后 `./start.sh` 自动使用登记的环境，无需激活，也不会边启动边安装。不把开发 worktree 的环境链接回 main。

如果预检提示已有数据库，此时只检查了存在性。环境、路径和端口有效时，`./start.sh --check` 成功退出，不打开数据库或启动 API／前端／worker。随后直接运行 `./start.sh` 使用本地数据；正常启动仍可能执行上文列出的初始化和维护，默认使用本地数据不等于后端只读。安装不读取凭据或研究数据。工作区验收不会更新日常目录；相关 PR 合并后需 pull main。本次仅移除确认流程，已有环境有效且依赖锁未变时无需重新安装。
