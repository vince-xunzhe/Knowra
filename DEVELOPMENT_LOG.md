# 开发记录

记录更新：2026-10-26。以下是核验时的快照，PR 链接是实时状态来源；合并状态在后续正常迭代中核验并刷新，不直接修改 main，也不单独反复创建状态更新 PR。

| 日期／分支 | 问题与变更 | 验证摘要与未验证项 | PR | 状态 |
| --- | --- | --- | --- | --- |
| 2026-10-26 · `codex/fix-readonly-preflight-20261026` | 分离只读环境预检与实际启动的数据许可，已有数据库不再阻断 `--check`，正式启动保护保留。 | 22 项启动回归、30 项后端、前端及真实本地启动统一验收通过；曾出现间歇构建超时，根因未定，未验证日常数据。 | [#26](https://github.com/vince-xunzhe/Knowra/pull/26) | 待合并；核验 2026-10-26；基于 `404507d` |
| 2026-10-25 · `codex/fix-native-startup-20261025` | 修复日常 main 固定选择旧 Python 3.9 环境、安装也无迁移路径的问题。统一环境选择，保留旧环境，补安装失败恢复与可操作提示。 | 独立安装、18 项启动回归及统一验收通过；未启动真实服务或迁移日常 main 环境。 | [#25](https://github.com/vince-xunzhe/Knowra/pull/25) | 已合并 main：`404507d`；核验 2026-10-26 |
| 2026-10-25 · `codex/infra-worktrees-20261025` | 建立独立环境与隔离验收；修复 React lint 的 18 errors／3 warnings，补会话、编辑和召回状态回归。 | 隔离统一验收通过；详细验证及超时记录见 PR。 | [#24](https://github.com/vince-xunzhe/Knowra/pull/24) | 已合并 main：`95a1a2e`；核验 2026-10-25 |
| 2026-10-25 · `codex/development-handoff-20261025` | 整理开发交接、启动说明和本地数据边界。 | 文档与忽略规则静态核对；未运行应用或真实服务。 | [#23](https://github.com/vince-xunzhe/Knowra/pull/23) | 已合并 main：`8274acc`；核验 2026-10-25 |
