# Ask 默认原文证据链验收

## 结果

本轮修复已合并功能“存在但默认关闭”的 release 回归：正常启动在当前知识库旁自动发现经过审核、可回滚的 generation，不再要求用户设置 `KNOWRA_ASK_SHADOW_MODE` 或 control 路径。若没有合法 promotion，旧 Ask 路径保持可用且界面明确显示“尚无已审核的原文证据 generation”；显式 `off` 仍可作为紧急回滚。

Ask 抽屉现在分开显示三种状态：Wiki 总览、全文 FTS 与原文证据 generation。绿色 Wiki/FTS 不再被误解为页级证据链已经启用。

## Definition of Done

- [x] 新 worktree `/private/tmp/knowra-ask-default-assist-20261031`，分支 `codex/ask-default-assist-20261031`，base `b32701a052ff097ed99fb6b0dc0a82a57f179d74`。
- [x] 无 Ask 环境变量时，已审核 generation 自动进入 `assist`；无 generation 时 fail closed，不读取未经审核的 shadow root。
- [x] API 返回不含路径、论文内容或配置的状态；React 同时显示 Wiki、FTS 和原文证据状态。
- [x] 显式 `off`、`observe` 和自定义 control 兼容保留。
- [x] 合成 promotion 走真实 generation control、locator/source hash 审核和原子 active pointer；正常 `start.sh` 启动真实 FastAPI/Vite，API 与 React 均显示 `synthetic-release-generation-v1`，无需特殊 Ask 开关。
- [x] 用户日常库先完成一致备份、完整恢复和校验，再从恢复副本生成 generation；原始论文、Wiki 与数据库未被 generation 构建覆盖。
- [x] 日常真实 React → API → Codex CLI → GPT-6 Astra 用原问题完成：61.8s、6 步、9 次 shadow 检索、4 条原文引用；trace 为 `shadow_retrieval mode=assist` 和 `shadow_answer evidence_count=4`。
- [x] 回答逐条对照引用：所述潜空间/离散表示、生成式与预测式路径、对象中心表示及潜在粒子/多条件均由所列页级原文支持；回答明确披露材料不足以还原完整时间演进，没有伪造年份、取代关系或完整历史。
- [x] 日常服务保持运行；隔离 release API/Web/worker 全部清理，8129/5209 无监听。

## 验证

- Python 3.12.13；Node 24.14.1；Vite 8.0.10；Google Chrome。
- 23 项 Ask shadow 专项：通过。
- 统一检查：29 项 native startup、221 项后端、frontend unit/types/build、ESLint 0 warning；浏览器生命周期 8/8 通过。首次受限沙箱运行因 loopback `EPERM` 失败，按检查所需权限原样重跑通过。
- release fixture：锁定安装完成，`start.sh --check` 通过；普通 `start.sh` 启动后 `/api/wiki/ask/evidence-status` 返回 `ready/assist`，真实 React 显示已启用 generation；关闭后 API/Web/worker 无遗留。
- 实际日常 generation：198/198 篇、5,249 页、失败页 0、11,182 evidence chunks、24,530 claims、35,712 index records；原问题双语主题路由达到 6 条原文证据、6 篇论文、2020/2024/2026，检索观测约 0.46s。

## UI 证据

- [修改前：只有旧状态](before-index-status.png)
- [修改后：独立显示原文证据 generation](after-index-statuses.png)
- [浏览器结果](browser-results.json)

截图只使用合成状态。日常真实问答截图未提交，避免把私有论文片段发布到公开 PR；上面的真实结果仅记录汇总指标。

## 数据与回滚

- 新备份 `knowledge-tree-20261031T053446Z`：2,455 个非数据库文件 source→backup→restore 差异均为 0；三个 SQLite 的 source、backup、restore `integrity_check=ok`；配置、日志和历史备份按批准范围排除。
- generation 从恢复副本构建，promotion record/active pointer 绑定 generation manifest、index identity、source digest、locator/source hash 和验收 SHA-256。
- 合并后普通启动自动发现 checkout-local `.knowra/ask-generation-control`。紧急回滚可将 active pointer 原子切回已审核 promotion，或由运维显式 `KNOWRA_ASK_SHADOW_MODE=off`；不删除现有 generation。

## 限制

- 原 #55 层本身只负责自动发现经过审核的本地 promotion；本统一 PR 的后续生命周期层已补上首次及资料变更后的 durable refresh、零失败审核、原子 promotion 与失败回退，详见 [Ask generation 生命周期验收](../ask-generation-lifecycle-20261031/README.md)。它仍不会绕过来源哈希、locator、页面解析和 revision 稳定性门槛，把未审核候选静默当成可用证据。
- 日常验证发生在本 PR 合并前，因此现有 main 进程使用了同一 reviewed control 的过渡性环境注入；合并本 PR 后，普通启动才无需该注入。
- 自动 claim entailment 仍未实现；回答模型受 evidence ID 白名单、页级引用和证据不足边界约束，但语义质量仍需评测。
- generation 仍是不可变快照；新增、重新处理、抽取修正、notes 更新及去重删除会请求新 generation，成功后原子 promote，失败则保留旧 active 或对已修改／删除证据 fail closed。产品目前没有用户可操作的论文删除入口，因此只验证了底层删除生命周期及删除后的真实 UI 状态，未冒充“从 UI 发起删除”。
