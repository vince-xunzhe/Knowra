# 个性化推荐运行手册

默认采用完整本地优先：本机知识库 → 本机画像和候选缓存 → 本机持久化队列 → Codex CLI 精排 → 本机采纳反馈。无需 Fly.io、Supabase、云端登录或论文云同步。联网仍用于检索 arXiv 元数据和调用 Codex；“本地优先”不表示模型离线推理。

## 本机启动

1. 按项目现有方式启动桌面前后端。后端在本地模式下自动创建 `data/recommendations.db` 的六张推荐表，并启动单个 CLI worker；原有论文库保持原位。默认推荐模型继承现有 CLI 任务，可在设置中单独选择 `recommend_rank`。
2. 打开“推荐 → 为你精选”，直接使用当前工作区的本地论文和可见知识节点建立画像，不需要登录。服务首次启动或周期到期后会生成任务，也可点击“更新精选”。
3. 周期为上海时区周一、三、五 09:00；worker 每 30 秒检查并补查到期时间槽。每批最多 10 篇，相关性不足时不凑数。每天手动更新最多一个独立批次。
4. “加入知识库”下载所选论文并创建真实本地记录；随后本地队列或页面读取会核对真实记录，幂等确认采纳。下载失败、待入库、收藏都不计采纳。候选检索和精排不会下载 PDF。

例如从仓库根目录启动后端：

```bash
cd backend
KNOWRA_DEPLOY_MODE=local .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

状态接口为 `GET http://127.0.0.1:8000/api/recommendations/personal`；进程管理为 `/api/recommendations/worker`。推荐接口限制 loopback 访问。本地身份仅作用于独立子应用，不改变已有云路由的认证。

桌面后端退出会停止其 worker 和 CLI 子进程。休眠或关机期间暂停生成，恢复后继续检查任务；5 分钟无心跳会提示离线。已有精选、画像、反馈和任务保留。库内论文删除不会自动变成负向反馈。

## 独立 worker 与 API 模式

通常无需手动启动 worker。如果需要自己管理进程，先在页面停止自动节点，保持后端运行，再从仓库根目录执行：

```bash
backend/.venv/bin/python backend/scripts/recommendation_worker.py --local --provider cli
```

`--local` 直接使用本机知识库与 SQLite 队列，不需要 worker token 或 HTTP 服务连接；`--once` 领取一个任务后退出，`--no-ai` 只做基础排序。同一推荐库通过操作系统文件锁只允许一个 worker，重复命令会退出。若节点由另一个后端或终端管理，页面能显示其运行状态，但必须在原进程中停止。后端重新启动会重新启动默认 CLI 节点。

若显式采用付费 API，在设置中将 `recommend_rank` 绑定到 API 模型，然后停止默认节点，提供经核实的人民币/百万 token 输入和输出价格上界：

```bash
backend/.venv/bin/python backend/scripts/recommendation_worker.py --local --provider api \
  --input-cny-per-million INPUT_PRICE --output-cny-per-million OUTPUT_PRICE
```

替换价格占位符，包含汇率与附加费用。付费调用前按 UTF-8 字节和最大输出预留成本，每工作区每 UTC 自然月上限 ¥30；中断与失败不退预留，因此可能提前停用。页面显示保守预留上界，不是实际账单。价格缺失、预算不足或模型失败会明确显示基础排序，不会偷偷切换付费 API。纯样本 `--local-input` 模式不支持付费 API，以免绕过持久化预算。

CLI 使用本机既有 ChatGPT 登录，认证文件不复制或上传。运行时移除 API Key 环境变量，临时空目录、只读沙箱、结构化输出及禁用工具能力限制候选文本的执行能力。CLI 费用未知时不显示为零；单节点并发为 1。

## 以后迁移至 SSH 主机

迁移的是服务、知识库及推荐状态，不只是一条 CLI 命令：

1. 在目标机器安装仓库与依赖，配置该机器自己的 Codex CLI 登录和模型绑定。
2. 停止原后端和 worker，备份并迁移项目数据目录、原有知识库数据库及所需论文文件，保持/调整库内路径。SQLite 使用 WAL；停止所有写入后执行 SQLite backup，或一致地复制数据库及其 WAL，不能只复制活跃数据库的主文件。
3. 在目标机器以本地模式启动服务并绑定 `127.0.0.1`。可设置 `KNOWRA_REC_DB` 指向迁移后的推荐数据库；该变量仅改变推荐库，不改变原有论文库路径。
4. 使用 SSH 隧道连接目标服务，例如 `ssh -L 8000:127.0.0.1:8000 HOST`，桌面前端仍访问本机转发端口。核对画像论文数、历史批次和反馈后再交给目标机器的进程管理器。

目标主机离线时页面提示连接失败，恢复后重新读取持久化状态。该迁移尚未在实际远端主机执行。

## 可选云端与手机模式

“全部论文”以及手机精选仍沿用已有云服务。手机目前不能读取这份本机画像、批次和反馈；两种模式不自动合并。桌面个人精选不因云端不可用而失效。

仅启用云端个人推荐时才需要先验证并执行 `supabase/migrations/0009_personal_recommendations.sql`、部署新云后端并同步知识库。云 API 可注册作用域受限的节点凭证，将其安全设置为 `KNOWRA_REC_WORKER_TOKEN`，再提供 `KNOWRA_CLOUD_URL` 并运行不带 `--local` 的 worker。云端只存令牌哈希。该可选路径不绑定特定云厂商；本次没有生产迁移或部署，也不需要 Fly 登录。

## 排障、验证与回滚

- 页面显示本机接口未加载：重启新版桌面后端，检查上述本地状态接口；无需检查 Fly 登录。旧服务若只占端口却不响应，应定位并重启对应进程。
- 模型不可用：检查本机 `codex login status`、CLI 版本、推荐模型绑定和额度；页面保留明确标记的基础排序。推荐配置变更后停止并重新启动节点。
- 节点中断：租约 15 分钟，单批最多 3 次执行；过期任务恢复，迟到结果拒绝。首次恢复可能要等待旧租约到期。
- 入库中断：文件与真实记录以原有论文库为准，页面保留待完成请求；无需云同步确认反馈。
- 参数回滚：`GET /api/recommendations/personal/profile/versions` 选历史批次，再 `POST /api/recommendations/personal/profile/rollback`，JSON 为 `{"batch_id":"所选批次"}`。只恢复反馈参数，保留实际库内论文、当前课题和采纳事件。
- 功能回退：停止 worker 后回退应用，保留推荐数据库与原有知识库。不要删除数据库来“刷新”推荐。

从仓库根目录运行：

```bash
backend/.venv/bin/python -m pytest -q
backend/.venv/bin/python backend/scripts/recommendation_worker.py \
  --local-input backend/tests/fixtures/recommendation_smoke.json \
  --output /tmp/knowra-rec-cli-smoke.json
backend/.venv/bin/python backend/scripts/evaluate_recommendations.py \
  --input backend/tests/fixtures/recommendation_replay.json \
  --output /tmp/knowra-rec-replay.json
```

后两个输入均为合成契约样本。CLI smoke 使用真实本机登录，AI 失败则退出码 1；回放不能替代真实效果验证。14 天浏览后入库率 30% 为目标，尚无成熟数据。其他首版限制见设计文档第 11 节。
