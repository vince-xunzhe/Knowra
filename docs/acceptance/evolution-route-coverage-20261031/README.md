# 演进问题路由覆盖与冷启动检索验收

日期：2026-10-31
分支：`codex/evolution-route-coverage-20261031`
依赖提交：`cfc281b1912eefc88f1e4a6ac20c4ca3f23aff03`
本轮开始时 `origin/main`：`918614aed15a7e83dfe678099518252fd8e1f409`

## 真实失败与根因

同一个扩散采样演进问题出现了两类可复现失败：

1. 冷进程首次查询会在 `retrieval` 阶段 fail closed，热缓存后的同一 generation 又能完成。原因是纯词法搜索仍读取、解析并校验全部 records（真实隔离索引约 380 MB），单次 500 ms 截止时间会在冷读时中断。
2. 成功回答的一条证据来自单目深度任务中的单步确定性预测。原主题 anchor 把 `sampling` 当成泛词，只要求命中“diffusion model”即可，因此下游应用能冒充通用采样演进阶段。

这两项分别属于检索执行路径和查询主题约束，不是针对某个 evidence ID、论文标题或问题字符串的硬编码。

## 修改

- 纯词法查询先由 FTS 和现有 kind/status/paper 过滤得到最多 20 个 ID，只 hydrate 这些有界候选。只有显式提供 dense query vector 时才扫描向量语料。
- 演进型路由要求宽主题锚点、早期基线、中间机制／效率及后续替代／推广，并保持与原问题相同的抽象范围；禁止把宽主题缩成下游任务或孤立适配。
- 泛词集合只移除意图／结构词；`sampling` 保留为本问题的实际主题维度。合成反例验证泛化 Markov sampling 和只提 diffusion 的下游应用均不能通过 anchor。
- `shadow_retrieval` trace 显示实际 query routes；generation fail closed 日志只记录安全的校验阶段与异常类型，不输出路径、查询、正文或异常消息。

## 验证

### 自动化

- 专项：47 项通过（hybrid retrieval、Ask shadow integration、two-stage retrieval）。
- 统一检查：29 项 native startup、219 项 backend、前端 unit/types/build、ESLint 0 warnings、8 项 React 浏览器回归全部通过。
- 第一次在受限执行沙箱中运行统一检查时，socket bind 被系统以 `EPERM` 拒绝；按检查本身需要的一次性 loopback 权限原样重跑后通过。该失败不是测试断言或业务回归。

### 真实隔离 generation，只读

- 每次以新 Python 进程打开已审核、不可变的隔离 generation。
- 4 条阶段路由共执行 9 次搜索，约 1.17 秒完成。
- 结果为 `ready / coverage_met`，4 条 evidence 来自 4 篇论文，页码、章节和 source locator 完整。
- 证据覆盖采样参数化／已知方差、短链加速、epsilon/Langevin 关系及连续时间 SDE/ODE；不再包含单目深度单步预测。
- 未写入、重编译或切换 daily research data；未启动独立 worker，未部署。

### 真实模型与 UI 边界

- 修复前真实 React → API → Codex CLI/Astra 链路完成，证明实际界面、会话、模型与引用渲染可工作，同时暴露了上述主题漂移，因而没有把该结果算作质量通过。
- 修复后的最后一次 API 请求在执行环境断线时丢失了进程通道；没有响应文件或持久日志能证明是否已经调用模型。该结果标记为“不确定”，没有自动重复发送。
- 用户随后明确授权这一次可能重复的重试。在独立工作树数据、独立 8128/5208 端口和不启动 worker 的前提下，真实 React → API → 官方 Codex CLI → GPT-6 Astra 首次冷请求完成：49.1 秒、6 个 trace 步骤，shadow 为 `ready / coverage_met`，9 次检索用时 271 ms，shadow 总观测 451 ms，最终选用 3 段原文证据。
- 回答分别陈述了已知方差／学习方差、DDIM 短链、连续 SDE/ODE 与 Langevin 校正，以及 DDIM 一致性／潜变量操控。逐条复核均能由所引原文支持，未虚构年份或完整历史顺序；回答明确保留了“不足以构成完整历史演进”的证据边界，且不再包含单目深度下游任务证据。
- 刷新真实界面后，本地会话标题、1 轮问答、模型标识、回答和 3 处精确原文引用全部保留。未发送完整论文、数据库或新问题。
- 关闭测试浏览器和服务后，8128/5208 端口无监听；`off` 模式回归证明不解析或读取 generation。当前 control 仍是既有的 rollback pointer，指向已审核 generation；实际 index 与 generation manifest 的 SHA-256 与 promotion 记录一致。未切换生产 pointer，未修改 daily research data，未部署。

## Definition of Done

- [x] 冷词法检索不扫描全语料，保持 dense 路径兼容。
- [x] 演进路由和主题 anchor 不含领域专用硬编码。
- [x] 合成正例、泛化负例、下游范围漂移和旧行为回归通过。
- [x] 真实隔离 generation 冷进程在预算内返回 4 篇／4 条可定位证据。
- [x] 统一检查全绿。
- [x] 修复后的真实 React/API/Astra 冷启链路回答及引用逐条语义验收。
- [x] 会话刷新、`off` 回滚、generation 完整性与最终进程／端口清理复核。
- [ ] 所有依赖轮次整体终审通过后才统一发布 PR；本轮不得单独发布。
