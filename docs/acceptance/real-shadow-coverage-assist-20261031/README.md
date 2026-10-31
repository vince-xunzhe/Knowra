# Ask 原文覆盖与 assist 验收（2026-10-31）

## 结论

本轮不是按单个问句增加关键词。真实只读诊断把两个覆盖缺口分别定位为：

- 时间型问题：相关中间年份论文已存在、PDF 解析完整、claim 也能召回，但候选论文预算只保留前 5 篇，相关论文排第 6，正文阶段因此不可见。根因是候选排序／预算，不是语料缺失、解析损失或最终上下文上限。
- 记忆型问题：相关原文与页码存在且解析完整；部分论文没有对应 claim，另一些 claim 被全局 top-10 截断。claim 限定的正文搜索因此返回空。根因同时包含 claim 抽取损失与召回截断；现有语料仍不足以证明一条完整、统一的历史演进链。

修复后，时间问题使用现有 8 篇总预算保留中间候选；claim 覆盖不足时增加至多 4 路、要求主题锚点且具有重复／多路信号的全局原文救援。结果仍须经过来源覆盖门槛，普通概览 drilldown 不会升级为回答证据。

生产 Ask 新增显式 `assist` 模式：默认 `off` 不触碰 generation，`observe` 保持只观察；只有旧 Wiki 门槛不足、不可变 shadow 覆盖达标、回答每个实质段落和列表项都使用允许的 evidence ID 时才输出原文回答。任何未知／缺失引用、预算、模型、索引或覆盖失败都回到原拒答；`off` 是即时回滚。引用明确标记为“检索到的原文”，不宣称自动 entailment adjudication。

## 可复核验收

- 合成回归：时间中间候选、claim 缺口的原文救援、宽查询单次噪声、无答案、预算、默认关闭、observe 不改答案、CLI 路由规划、API 共用证据、未知引用、逐段／逐列表引用、trace 正文隔离、回滚。
- 真实隔离 shadow：同一批 5 个批准问题全部 `coverage_met`；每题 4 条以内原文、3–4 篇论文、页码与字符范围完整；检索调用分别为 9／8／9／9／14，均在 19 次硬上限内；399 个文件前后尺寸和 mtime 不变。
- 实际模型：经签名／公证已核验的 ChatGPT 内置官方 Codex CLI，单次 GPT-6 Astra 批量调用；只发送同一批 5 问与每题最多 4 条选中片段，不发送完整论文、数据库或配置。返回 5／5 JSON；所有回答内引用均属于白名单并与 `used_evidence_ids` 完全一致，5／5 逐段／逐列表引用通过，5／5 明示证据边界。模型调用返回码 0；未购买额度。
- 生产桥回放：使用真实只读 shadow 与上述实际模型回答，5／5 经 CLI 生产 `_finish_result` 路径形成 3／4／4／4／4 条页码＋字符定位引用；trace 不含证据正文；`off` 回滚保持原拒答且无 shadow trace；shadow 文件不变。
- 统一检查：`NODE_BIN=/Users/vince/.local/bin/node ./scripts/check.sh` 通过，包含 29 项启动、199 项后端、前端 unit／类型／构建、零 warning lint 和 8 项隔离浏览器回归。首次未提权运行因执行沙箱禁止 loopback／fixture worker 失败；按检查本身要求在隔离权限下重跑通过。

以上两项模型／生产桥结果记录的是终审前所选证据，不能单凭引用白名单与
`coverage_met` 推断语义质量。终审逐条比对原文后，第 1／2／4／5 问的实际回答受到
所引片段支持并保留了不确定性；第 3 问虽格式通过，却漏掉同篇论文中直接报告采样
步数／速度改进的核心正文，反而给较泛的背景片段保留了上下文槽位。因此最终状态
不是 5／5 语义通过。

证据择优现已做系统性修复：当某论文在四个槽位内只有 background／related-work，
而候选中存在该论文的直接正文时，用直接正文替换该论文的次级材料；其余检索排名
不变，同页同章节重叠块只占一个槽位。真实只读复验中，第 3 问 9 次检索选中了核心
正文，399 个 shadow 文件前后不变；第 1／2／4／5 问的已批准证据集合保持不变。
修订后的第 3 问尚未再次调用外部模型，旧输出因引用了已退出白名单的片段会被生产
校验正确拒绝。终审后回放的第 1／2／4／5 问仍分别形成 3／4／4／4 条精确引用，
第 3 问为 0 条并保留旧拒答；故当前没有可声称通过的新 5／5 生产桥结果。

终审修复后的统一检查再次通过：29 项启动、200 项后端、前端 unit／类型／构建、
零 warning lint 与 8 项隔离浏览器回归。

## 数据与运行边界

- 未写入或重新编译原始论文、Wiki、SQLite、配置；未启动 API、Web 或两个生产 worker。
- 使用已验证的本机普通备份、独立恢复副本和 shadow；配置／密钥不在备份或提交中。
- 现有旧 shadow index 创建于 locator 增补前，因此真实回放具备 evidence ID、manifest、页码和字符范围，但 `source_file_hash` 为空；新代码生成的 index 会同时保存 source hash。此项不影响旧 generation 的只读兼容。
- 未部署、未 push、未创建 PR；用户仍决定后续统一 review、合并和启用 `assist`。
- 未启动真实 API/Web，也未以真实用户会话经过路由、会话持久化和 UI 操作；生产桥
  验收是 `_finish_result` 的本地回放，其中模型传输被替换为此前真实返回的文本。
- 没有新建采用最新 locator schema 的真实 generation；缺少 generation、root 无效或
  `off` 时均保留旧拒答，不读取或生成 shadow 数据。
- 当前 builder 与 loader 只接受 `production_eligible: false` 的 shadow artifact；
  `assist` 的环境变量是唯一启用门槛，尚无经过审核、可原子回滚的 generation promote
  记录。因此这里只能称为显式 opt-in 的生产代码桥，不能称为已完成生产切换。

## 复现命令

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest \
  backend.tests.test_ask_shadow_integration \
  backend.tests.test_two_stage_shadow_retrieval \
  backend.tests.test_hybrid_claim_retrieval \
  backend.tests.test_ask_agent \
  backend.tests.test_ask_evidence_gate \
  backend.tests.test_ask_retrieval_orchestration

NODE_BIN=/Users/vince/.local/bin/node ./scripts/check.sh
```

真实 shadow、模型输入／输出及允许 ID 清单只保存在本机临时验收路径，不进入仓库。
