# 开发记录

记录更新：2026-10-26。以下是核验时的快照，PR 链接是实时状态来源；合并状态在后续正常迭代中核验并刷新，不直接修改 main，也不单独反复创建状态更新 PR。

| 日期／分支 | 问题与变更 | 验证摘要与未验证项 | PR | 状态 |
| --- | --- | --- | --- | --- |
| 2026-10-26 · `codex/suoji-import-graph-export-20261026` | 索骥按本地论文 ID 标识当前画布已导入项并防重复；索骥／识途完整图与研报流程图支持高清 PNG、300 DPI 元数据及像素上限。 | 统一验收、8 项专项单测、11 项合成 SQLite＋真实 API／浏览器流程及 PNG 解码／像素检查通过；独立审查问题已修复。未验证日常数据、外部服务或跨浏览器。 | 尚未创建 | 本地验证完成，待本轮公开发布确认；核验 2026-10-26；基于 `0209fc2` |
| 2026-10-26 · `codex/fix-default-local-data-20261026` | 移除 START 确认及已有数据许可开关要求，默认使用本地数据；保留预检和退出清理。固定 Vite 8.0.10，避开旧原生依赖中的已知挂起回归。 | 统一验收通过：23 项启动、31 项后端、5 项前端单测、类型／构建／lint、5 项 UI 及合成已有数据真实启动；此前构建超时的依赖诊断与有界对照保留在提交中，日常数据未验证。 | [#29](https://github.com/vince-xunzhe/Knowra/pull/29) | 已合并 main：`0209fc2`；核验 2026-10-26 |
| 2026-10-26 · `agent/NOISSUE-favicon-request` | API 为浏览器自动发起的 `/favicon.ico` 探测返回空响应，避免无害的 404 噪声。 | 28 项启动回归、31 项离线后端、前端单测／类型／构建／lint 及 5 项生命周期 UI 验收通过；未启动真实服务或读取日常数据。 | [#28](https://github.com/vince-xunzhe/Knowra/pull/28) | 已合并 main：`3d5a6e3`；核验 2026-10-26 |
| 2026-10-26 · `codex/fix-daily-start-20261026` | 日常启动改为终端说明影响并就地确认，非交互仍需明确许可；退出时等待 API/Web 完成关闭，避免推荐 worker 遗留。不记住数据许可或改动维护逻辑。 | 28 项启动回归、30 项后端、前端及合成旧库真实启动统一验收通过，正常关闭无遗留；未操作或验证日常数据。 | [#27](https://github.com/vince-xunzhe/Knowra/pull/27) | 已合并 main：`77848c5`；核验 2026-10-26 |
| 2026-10-26 · `codex/fix-readonly-preflight-20261026` | 分离只读环境预检与实际启动的数据许可，已有数据库不再阻断 `--check`，正式启动保护保留。 | 22 项启动回归、30 项后端、前端及真实本地启动统一验收通过；曾出现间歇构建超时，根因未定，未验证日常数据。 | [#26](https://github.com/vince-xunzhe/Knowra/pull/26) | 已合并 main：`80524c9`；核验 2026-10-26 |
| 2026-10-25 · `codex/fix-native-startup-20261025` | 修复日常 main 固定选择旧 Python 3.9 环境、安装也无迁移路径的问题。统一环境选择，保留旧环境，补安装失败恢复与可操作提示。 | 独立安装、18 项启动回归及统一验收通过；未启动真实服务或迁移日常 main 环境。 | [#25](https://github.com/vince-xunzhe/Knowra/pull/25) | 已合并 main：`404507d`；核验 2026-10-26 |
| 2026-10-25 · `codex/infra-worktrees-20261025` | 建立独立环境与隔离验收；修复 React lint 的 18 errors／3 warnings，补会话、编辑和召回状态回归。 | 隔离统一验收通过；详细验证及超时记录见 PR。 | [#24](https://github.com/vince-xunzhe/Knowra/pull/24) | 已合并 main：`95a1a2e`；核验 2026-10-25 |
| 2026-10-25 · `codex/development-handoff-20261025` | 整理开发交接、启动说明和本地数据边界。 | 文档与忽略规则静态核对；未运行应用或真实服务。 | [#23](https://github.com/vince-xunzhe/Knowra/pull/23) | 已合并 main：`8274acc`；核验 2026-10-25 |
| 2026-10-22 · `agent/NOISSUE-sync-session-expiry` | 延长云同步会话并在 HTTP 410 后自动重建会话、复用已上传文件。 | 同步路由、浏览器恢复测试、前端构建与改动文件 lint 通过；全仓旧测试／lint／类型债务见 PR。 | [#22](https://github.com/vince-xunzhe/Knowra/pull/22) | 已合并 main：`e0f602f`；核验 2026-10-26 |
