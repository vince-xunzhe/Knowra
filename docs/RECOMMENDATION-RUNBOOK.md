# 个性化推荐运行手册

首版采用内容匹配、个人正反馈与有界 AI 精排。用户已确认先在本机执行，以后迁移至常驻 SSH 主机。现有公共推荐仍可从“全部论文”访问。

## 上线顺序

1. 在测试数据库先执行 `supabase/migrations/0009_personal_recommendations.sql`，验证隔离，再按项目已有发布流程迁移云端并部署后端。**本次没有执行生产迁移或部署。** PostgreSQL 必须先迁移再启用新云端路由；SQLite 测试环境由 `init_cloud_schema` 建表。
2. 更新桌面前后端及手机应用，登录并将知识库同步到云端。画像只读取当前用户已同步的论文及结构化知识节点。
3. 桌面“推荐 → 为你精选 → 执行节点与调用预算 → 在本机启动节点”。复用本机 Codex CLI 的 ChatGPT 登录；推荐模型在模型设置的独立 `recommend_rank` 任务中选择，首次默认继承现有 CLI 任务模型。
4. 点击“更新精选”，查看节点、任务和最近完成时间。常规任务按上海时区周一、三、五 09:00 的时间槽去重；后台按已有调度轮询，worker 领取时也会补查，可能晚于整点。
5. “加入知识库”下载选中的论文并创建真实本地记录，云同步成功后确认采纳。候选检索与精排不下载 PDF。手机仅创建待入库请求，由桌面完成。

本机凭证只通过进程环境传递，不写入浏览器存储或现有认证文件。后端退出会停止自己启动的 worker 与其 CLI 子进程。电脑休眠/关机后无法生成新精选；5 分钟没有心跳时站内提示离线，旧精选仍可阅读。

## 命令行与后续 SSH 主机

在桌面生成一个新的节点凭证，在目标机器的终端安全设置 `KNOWRA_REC_WORKER_TOKEN`（勿写进命令历史或提交仓库），配置 `KNOWRA_CLOUD_URL` 为云服务 HTTPS 根地址。worker 主动连接云端，不需要云端 SSH 到电脑。

```bash
backend/.venv/bin/python backend/scripts/recommendation_worker.py --provider cli
```

目标机器需要仓库、Python 依赖与已登录 ChatGPT 的兼容 Codex CLI。可用 `codex login status` 检查登录状态。代码使用结构化输出、临时空目录、只读沙箱、忽略用户工具配置及禁用工具能力；版本不兼容时降级为基础排序，不偷偷改用 API。未来将同一命令交给目标机器已有的进程管理器即可；随后撤销旧节点凭证、停止本机节点。

- `--once`：领取并执行一个任务后退出；没有任务则直接退出。
- `--no-ai`：只验证基础排序。
- 租约 15 分钟，单批最多 3 次执行；迟到结果拒绝。恢复节点会重领过期任务。
- 同名节点重新注册会立即撤销旧凭证。凭证只能访问其所属用户的推荐任务；云端只存哈希。

## 显式 API 路径与预算

在模型设置中把 `recommend_rank` 绑定到 API 模型，在节点显式选择 `--provider api`，并提供当前模型**人民币/百万 token 的输入和输出价格上界**：

```bash
backend/.venv/bin/python backend/scripts/recommendation_worker.py --provider api \
  --input-cny-per-million INPUT_PRICE --output-cny-per-million OUTPUT_PRICE
```

将占位符替换成已核实价格，包含汇率与供应商附加费用。接口须遵守模型输出 token 上限；不支持可靠计费上界的供应商不要启用。价格缺失、预算不足或模型失败时明确显示基础排序。付费请求前按输入 UTF-8 字节及最大输出预留预算，每用户每 UTC 自然月累计预留不超过 ¥30；中断与失败预留不释放，因此可能提前停用。该值是保守预留上界，**不是实际账单**。

CLI 仅允许可识别的 ChatGPT 登录；API Key CLI 登录须改走上述预算路径。CLI 费用/额度无法折算为已知人民币账单，不显示为零费用，单节点并发为 1。不会复制或上传 Codex 认证文件。

## 验证命令

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

后两个输入是明确标识的**合成契约样本**。CLI smoke 使用真实本机登录，AI 没有执行成功时退出码为 1。回放比较相同候选集上的时间排序与内容排序，过滤时间截点之后的知识库和论文，不把未采纳当负例。合成结果仅证明管线行为，不代表用户推荐质量；真实目标是首次浏览后 14 天内入库率至少 30%，需要上线收集成熟样本。

## 排障与回滚

- 页面报表不存在：先核对云数据库迁移与后端版本；刷新前端不能解决缺表。
- 节点在线而模型不可用：检查节点 `codex login status`、`codex exec --help`、推荐任务绑定、CLI 额度。日志不输出令牌/模型原始错误。
- 离线：恢复机器或网络；已有任务和旧精选保留。任务达到重试上限后当天手动刷新或下个周期会产生新时间槽。
- 入库成功但同步失败：本地文件与记录保留，重试云同步才会写采纳反馈；不要把“下载成功”当成反馈成功。
- 个性化参数异常：使用已认证云 API `GET /api/cloud/personal-recommendations/profile/versions` 选择自己的已完成批次，再 `POST /api/cloud/personal-recommendations/profile/rollback`，JSON 为 `{"batch_id":"所选批次"}`。仅恢复反馈参数，不恢复已删除论文或覆盖当前课题；保留全部真实采纳事件。
- 功能回退：停止/撤销 worker，回退应用版本，保留新增表与反馈数据。新表不修改旧公共推荐及本地论文结构。不要直接 DROP 表丢失反馈；结构性回滚须先备份并确认无人使用新版本。

首版限制详见设计文档第 11 节：有限分页没有逐页游标，英文词项召回，多样性软约束，AI 特征未单独拆分缓存命中调用，暂无自动退化检测。
