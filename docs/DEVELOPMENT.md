# 开发工作区与验收

每轮迭代从最新远端 `main` 新建 worktree 和独立分支。原目录、旧 worktree、未提交文件和日常研究数据都保留；不 reset/stash/clean。发布需要当轮授权，完成验证、review 修复和冲突核验后交付非 Draft PR，由用户 merge。

## 新建一轮工作区

在已有仓库中先检查状态和远端地址，再执行（`task_id` 每轮不同）：

```bash
GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false status --short
git remote -v
git fetch --no-tags origin main
task_id=native-checks
git worktree add -b "work/$task_id" "../knowra-$task_id" origin/main
git worktree list
cd "../knowra-$task_id"
```

不要输出含认证信息的远端 URL。每个 worktree 自己安装受记录管理的 `backend/.venv`（保留旧环境迁移时为 `backend/.venv-native-py312`）、`frontend/node_modules`，不跨工作区链接环境或 `data/`。`.cache/` 内工具缓存与验收快照也独立。旧工作区清理不属于初始化操作。

## 安装、启动与端口

本机 Web 基线为 Python **3.12.13+ / 3.12.x**、Node **24.14.1+ / 24.x**；固定版本见 `.python-version`、`.node-version`；当前 Web 锁为 Vite 8.0.10、TypeScript 6.0.3。准备好这些运行时和 `uv`（本轮锁由 0.11.14 生成）后：

```bash
./scripts/install-native.sh
./start.sh --check
BACKEND_PORT=8103 FRONTEND_PORT=5183 ./start.sh
```

安装只用官方 PyPI 哈希锁、wheel 和 npm 锁；清空包管理器继承配置并使用工作区缓存。npm 使用 `ci --ignore-scripts`：当前仅可选 `fsevents` 标记安装脚本，Web 构建使用包中现成的原生绑定。新增依赖/脚本须重新核查，不能以 `npm rebuild`、重签名或移除隔离属性盲目修复。

安装器只寻找已安装的基线 Python，不下载运行时；可用 `PYTHON_BIN` 明确指定受支持解释器，`NODE_BIN` 可指定 `.../bin/node` 并仅调整本次命令 PATH。旧环境迁移和日常 main 的使用步骤见 [INSTALL.zh.md](../INSTALL.zh.md)。安装记录包含环境路径、Python 版本、锁哈希和 installing/ready 状态，安装失败不能被视为就绪。

安装不启动服务。启动不安装依赖、不启动 Docker/Colima、不处理原生签名、不自动打开浏览器。`--check` 只验证运行时、安装锁标记、数据边界和端口，不导入应用、不打开数据库；已有数据库仅提示。普通 `./start.sh` 默认使用本地数据，不要求确认或数据许可开关。默认端口为 8103/5183；并行工作区必须显式分配另一对空闲端口，占用时失败而不偷偷换端口。

`data/` 仍锚定当前 worktree，API 启动会初始化或维护该目录，并按既有策略启动独立 worker。已有数据库默认使用，不再按是否存在数据拦截终端或非交互启动。默认使用本地数据不等于后端只读，数据维护和后台队列行为没有改变；代理验收仍仅使用合成数据。不允许数据目录链接到其他工作区，任务队列必须留在当前 `data/`，启动入口固定 local 模式。根 `.env` 默认不读取，确需本工作区凭据时用 `KNOWRA_LOAD_ENV=1` 显式交给 Uvicorn 解析；检查和安装不需要凭据。完整影响和备份步骤见 [INSTALL.zh.md](../INSTALL.zh.md)。

后端默认不热重载；开发可设 `BACKEND_RELOAD=1`。关闭启动脚本只关闭 API/Web，**不会终止独立 worker**。worker 的默认策略没有改变，手动 external 模式也须另行启动 worker；关闭、升级和恢复见 [TASK-WORKERS.md](TASK-WORKERS.md)。不要将启动当作验收。

## 一条命令验收

```bash
./scripts/check.sh
```

修改启动入口后，使用 `./scripts/check.sh --real-start`：常规检查全部通过后，额外运行 `scripts/check_native_start.py`，在 `.cache/` 的独立源码快照、合成旧数据库、临时 HOME 和随机回环端口中直接运行 `./start.sh` 启动真实 Uvicorn／Vite，无交互输入、无数据许可环境变量，并用现有 Chrome 验证页面导航和真实 API 响应。快照物理环境只读使用本 worktree 刚安装的依赖，前端依赖物理复制；不复制日常数据或配置。主任务 worker 使用 external 模式，推荐库预置不可领取的合成租约并校验其不变，确保不执行推荐任务；测试阻断外部网络与模型 CLI。检查包括已有数据只读预检、默认启动、合成旧列迁移及数据保留；要求 API 完成 shutdown hook、API/Web 及本次推荐 worker 正常退出，最终进程／端口均须释放，任何恢复清理均不算通过。启动器只等待它启动的直接子进程，不递归终止独立主任务 worker；异常关闭或日常数据库锁等待不在此合成场景的证明范围内。日志和合成数据留在忽略的缓存中供诊断，不是对日常数据库迁移或所有生产行为的验证。

此入口先运行 `scripts/test_native_startup.py`：在本工作区临时夹具中直接执行安装、`--check` 和日常 `./start.sh`，使用合成配置与 mock 回环服务；然后依次执行：

1. `scripts/check.sh` 使用安装记录选定的解释器运行 `scripts/check_backend.py`：在本 worktree `.cache/` 内复制必要 Python 源码，使用独立临时数据，运行明确列出的 queue/executor/background-dispatch 子集与 PostgreSQL opt-in 门禁检查；禁止外网、真实配置/日常数据读取及快照外写入。不是整个后端套件。
前端检查由 `scripts/check_frontend.py` 在 `.cache/` 源码快照中执行，排除 `.env*` 并清空继承环境，构建明确禁用 Vite env 加载；不指向日常服务。

2. `npm --prefix frontend run test:unit`：Node 自带 test runner 执行 polling 单测，不混收 Playwright。
3. `npm --prefix frontend run build`：TypeScript 与 Vite 生产构建。
4. `npm --prefix frontend run lint`：完整 ESLint，错误和警告都使验收失败；不降低规则或忽略问题。
5. 真实 React 组件的 mock 浏览器回归：覆盖 Ask 会话、索引请求和归纳标题，Review 论文切换、聊天输入和笔记编辑，以及召回操作中关闭重开弹窗。仅在源码快照中构建专用测试页面，使用随机回环端口、临时浏览器 profile 和合成数据；全部 API mock，外网请求被阻断。

任何一步失败会返回非零，其他检查仍完成；缺少浏览器等前置条件不能当作跳过后通过。默认入口启动专用测试页面的静态服务与临时浏览器，不启动应用后端、真实模型服务或 PostgreSQL。现有整站 Playwright/E2E 另行审批与隔离，不自动执行。PostgreSQL 测试会重建 schema，仅在专用可丢弃实例且显式 opt-in 时运行，详见 `backend/requirements-postgres.txt`；URL 环境变量本身不足以启用。

浏览器回归需要已安装的 Chrome/Chromium；默认查找 macOS Chrome 或 `PATH` 中的 Chromium/Chrome，不自动下载。其他安装位置可显式指定绝对路径：`KNOWRA_BROWSER_EXECUTABLE=/absolute/path/to/chrome ./scripts/check.sh`。测试入口与合成夹具位于 `frontend/tests/lifecycle-*`，通过 `scripts/check_frontend.py` 接入快照；类型、构建、lint、浏览器结果分别记录于 `.cache/frontend-check-*/`。取消或超时时，入口会终止本次子进程组；清理失败必须报告，不能将其视为通过。

## 更新依赖

修改后端直接依赖 `backend/requirements.txt` 或测试输入 `backend/requirements-dev.in` 后，在本工作区使用官方源重新生成锁（不要手改哈希）：

```bash
UV_CACHE_DIR="$PWD/.cache/uv" uv --no-config pip compile backend/requirements-dev.in --python-version 3.12 --universal --generate-hashes --no-header --no-annotate --only-binary :all: --default-index https://pypi.org/simple --output-file backend/requirements-dev.lock
```

再执行安装与验收。前端保留 `package-lock.json` 的精确版本和 integrity；只有确需升级时才更新依赖。不能复制另一台机器的 venv/node_modules。下载依赖需要网络，验收默认离线。

## 数据备份：待用户决定，不自动执行

代码通过 Git；数据、秘密、环境不随 Git 或两台机器自动同步。开始备份前由用户决定目标位置、加密方式、保留周期、可停机窗口和是否保留可重建产物。

- 必须评估：`data/knowledge.db`、`data/tasks.db`、`data/recommendations.db`、论文 `data/papers/`、人工/派生 wiki `data/wiki/`、`data/paper_records/`、运行配置 `data/config.json` 与必要偏好文件。配置可能含凭据，只能进入用户批准的受保护备份，绝不写入公开仓库。
- 可按恢复需求决定：`data/artifacts/`、`data/roadmap_assets/`、其他可重建缓存；不能未经确认删除原件。
- 排除：`.venv`、`node_modules`、`.cache`、`frontend/dist`、日志和进程锁。活跃 SQLite 使用 backup API 取得一致快照；不要仅复制 `.db` 并丢弃 WAL，也不要同步正在写入的数据库。
- 恢复演练应使用另一个隔离 worktree 和独立端口，验证数据库完整性、记录/论文数量及资源引用，禁用真实外部服务；通过后再讨论正式迁移。

本轮只提供此清单，未读取、复制、移动或删除研究数据，也未创建自动备份/两机同步。CI、部署、移动端打包和旧 worktree 清理后置。

## Ask shadow 接线（默认关闭）

实验性 claim/source 检索只能通过一个已经验证、不可变且位于 live `data/` 之外的 generation 使用。默认 `KNOWRA_ASK_SHADOW_MODE=off`；此时 Ask 不解析 generation 路径，也不增加 trace。显式设置 `observe` 和绝对 `KNOWRA_ASK_SHADOW_ROOT` 只进行只读候选检索，结果不得改变用户答案、引用或现有 evidence gate。显式设置 `assist` 时，仅在旧 Wiki evidence gate 不足、两阶段检索达到来源覆盖门槛、且回答模型对每个实质段落使用允许的 evidence ID 时，才用最多 4 条有页码/字符定位的原文片段生成回答；未知或缺失引用、模型/预算失败、来源覆盖不足都回退到原有拒答。`assist` 的引用表示“定位到原文”，不能解释为已经自动完成 entailment adjudication；切回 `off` 即为即时回滚。

generation root 不能是 live data 的子目录或祖先，任一父路径、manifest 与 index 都不能走 symlink/`..`。manifest 必须同时绑定 index 文件的 SHA-256/字节数、corpus/dense digest 和模型版本，并保持 `production_eligible=false`。SQLite 以 `immutable`、`query_only`、零锁等待打开；本地物理文件逐块 hash、检索及复核共享 5 秒总观察上限，超时或任何损坏都只产生不含正文的 `unavailable` trace，不回退到 live Wiki 或自动重建。trace 序列化会剥离候选正文和回答证据；只有显式 `assist` 的回答模型调用会接收上述有界片段。该上限可在块间中止，不声称能抢占一次阻塞的底层文件读取。文件 hash 只证明当前 generation 内部内容一致，并不是外部签名或可信发布凭据。真实 generation 的创建、备份、恢复、私有语料评测和任何部署/cutover 均是另行审批事项。
