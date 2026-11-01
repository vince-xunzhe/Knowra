# Ask generation 生命周期验收

## 统一交付

最终交付从 `origin/main` 的 `b32701a` 新建独立 worktree，将默认启用原文证据链的 #55 代码与本生命周期修复整合为一个直接面向 `main` 的候选；整合后的代码树与完成真实 release 验收的本地候选逐字节一致。已有 #55 保持开放且不自动关闭／合并，统一 PR 可独立完成两部分交付。新 worktree 使用独立 Python 3.12.13 venv 与 Node 24.14.1 依赖，不复制其他 worktree 的环境或数据。

## 结果

本轮在隔离 worktree 中补齐论文资料与不可变 Ask generation 的生命周期同步。论文批处理、重新处理、手工修正抽取、notes 更新及论文去重删除都会先写入可合并的持久 refresh marker，再由单写 worker 基于一致 SQLite 快照构建候选 generation。只有 library revision 在构建前后及 promotion 后均稳定、页面解析零失败、locator/source hash 审核、generation 全树落盘和原子 promotion 全部通过，active pointer 才可用；任何失败都不把未验证候选当成可用证据。已有 active 时，promotion 后检测到资料变化会 compare-and-swap 回滚到原 generation，即使 refresh marker 本次落盘失败也独立执行回滚；运行时在检索前后、证据发送给模型前以及模型回答采纳前核验 revision，revision 缺失、损坏或既有论文过期时 fail closed。

## Definition of Done

- [x] library revision 覆盖 processed paper 集合、真实 `filepath`、PDF 实际 SHA-256/size/mtime/ctime、数据库旧 MD5、结构化抽取、标题和 notes；generation manifest 绑定独立 inventory 的 SHA-256。v2 签名 inventory 还携带路径哈希、source SHA/MD5 与 device/inode/birthtime/stat 精度指纹：birthtime 只参与身份比较，只有会随内容变更的 ctime 本身呈现亚毫秒精度且文件位于受管 papers 根时，才允许持久及进程内 hash 复用；mtime 即使精细也不能单独授权缓存。外部扫描根、低精度 ctime 或任何指纹变化会在所有调用路径重新计算正文 hash。合成回归覆盖 birthtime 单独精细、mtime 单独精细及固定同 path/inode/size/mtime/ctime 后原位替换，后者的 `refresh_status` 仍准确报告 stale。
- [x] 新增、重处理、部分批次成功、抽取修正、notes 更新和去重删除均触发持久 refresh；marker 是事实源，pipeline 冲突、worker 中断及失败退避后都会重新排队。API supervisor 与 external worker 本身都能巡视遗留 marker，不把冲突误报成已完成。
- [x] marker 读写及确认使用跨进程文件锁和 request-id compare-and-delete；8 个真实子进程并发写入仍得到一个完整 marker，新请求不会被旧任务删除。
- [x] SQLite online backup、嵌套同名 PDF、明确配置的外部 `scan_directory`、PDF SHA-256 校验副本、输出目录隔离、跨进程文件锁和 active compare-and-swap 均 fail closed。外部文件不会直接交给 builder，而会按 paper ID 写入隔离快照；任意未配置绝对路径被拒绝。
- [x] notes-only、删除等未改变 PDF 的更新复用页级 evidence；新增论文只解析新 PDF；最终索引仍在新 generation 内完整原子构建。
- [x] 过期 generation 在 UI 独立显示；revision 不可读、legacy generation 无 inventory、删除或修改既有论文时 Ask 不再读取旧快照，纯新增期间旧快照可继续回答已有资料直到刷新完成。
- [x] index 文件及其父目录、generation 内全部文件／目录及父目录在 pointer promotion 前 `fsync`；本轮分别注入 marker 临时文件、marker 父目录、generation artifact、generation 目录及 generation 父目录 `fsync` 失败，既有 control 测试覆盖 pointer 文件落盘、replace 与目录落盘。未确认的 promotion 结果不会删除仍可能被 active 引用的候选。
- [x] promotion 后再次核对 library revision；已有 active 时发生变化会回滚原 generation。marker 写入和 rollback 分别尝试，marker fsync 失败不能跳过 rollback；首次 promotion 无旧 active 且 marker fsync 同时失败时，错误同时披露两个事实并由运行时 fail closed。Ask 在检索前后、provider 调用前以及回答模型返回后再次核对 active generation 与已有论文 input digest；真实 SQLite 在合成模型调用期间变化的回归确认返回答案和引用均被丢弃。
- [x] 需重算 PDF hash 时使用独立、可终止的本机 subprocess；deadline 到达后依次 terminate/kill 并用有界 `wait` 回收，不会在 `communicate` 超时后再次调用 `communicate` 而覆盖原异常。真实慢 hash 及 terminate→kill 故障注入均验证超时拒绝。
- [x] 面向启动、状态 API 和 UI 的 `refresh_status` 为不可信源 hash 设置 1 秒 subprocess 预算；超时返回可观察的 `library_revision_timeout`，startup 继续排入 durable 后台 refresh。后台 worker 构建后的最终核验显式使用完整哈希，不因前台预算误报构建失败。
- [x] 合成回归覆盖新增、notes 更新、删除、真实 pypdf 文本替换解析、同名嵌套及外部扫描路径、MD5/SHA 绑定、同尺寸/同 mtime 替换、同 stat 不同 inode、弱文件系统同 inode 原位替换、pipeline 冲突、真实跨进程 write-vs-ack 与多 writer 竞争、external worker 失败退避重排、部分成功、构建中／promotion 中／查询中 revision 改变、marker 落盘失败时独立回滚、provider 前删除、模型调用期间真实 SQLite 变化、关键分阶段 fsync/replace 故障、真实 v1 `source_cache` 升级、删除后真实检索 fail-closed、证据复用和首次 generation。

## 验证

- Python 3.12.13；Node 24.14.1；Vite 8.0.10。
- 70 项 generation refresh／shadow integration 聚焦回归通过（refresh 42、shadow 28）；refresh 专项包含真实子进程、真实 pypdf、SQLite、FTS/index 与 control pointer 路径，不把故障注入或合成结果描述成日常资料实测。
- 统一 `./scripts/check.sh --real-start` 通过：29 项 native startup、271 项离线 backend、frontend unit/types/build、ESLint 0 warning、8 项真实 React mock-network browser regressions。随后全新合成发布快照运行真实 FastAPI、Vite 与 React，已有行与迁移保留、provider 阻断、正常 shutdown 完成，随机端口及进程均清理，隔离 guard 0 violation。
- 在提交 `40f1168` 建立全新 release worktree，以官方锁文件完成普通 `install-native.sh` 安装，并复制先前已校验的恢复副本（明确排除 config、`.env`、日志和凭据）。未设置 Ask 开关的普通 `start.sh` 自动排队并在 107.932 秒内构建、审核和启用 198 篇／5,249 页、0 失败页的 generation；真实 React Ask 抽屉显示“原文证据 已启用”，API 返回 `ready/fresh`。在真实界面编辑一条隔离笔记后 8.203 秒完成新快照，恢复原值后 8.153 秒再次 fresh；笔记集合逻辑 SHA-256 与原恢复副本相同。实际 control pointer 回滚到旧同 revision generation、再恢复最新 generation 均保持 ready。
- 在用户既有授权范围内，从同一 release React 界面提交原第 3 个基准问题，实际链路为 React→FastAPI→官方 Codex CLI→GPT-6 Astra：58.9 秒、6 个工具步骤、4 条页级引用。逐条人工复核确认回答覆盖表征压缩、生成式／预测式规划、patch／object-centric 取舍和 latent particles／多模态条件边界，所有实质性陈述均受所附原文支持，没有虚构年份或完整演进顺序，并明确材料不足。仅发送该问题及最多 4 段已选证据，未发送完整 PDF、数据库、配置或新增问题；隔离合成论文从未发给 provider。首次选择 OpenAI API provider 因隔离环境没有 API key 在请求前立即失败，没有模型调用或证据传输，随后通过界面改用已授权的 Codex CLI/Astra。
- 真实 React“资料”界面上传一篇单页隔离合成 PDF 后显示总数 198→199。为避免新增模型授权，抽取状态仅在隔离数据库中离线补齐；durable refresh 自动生成并启用 199-paper generation，真实检索返回该论文第 1 页且 locator/source hash 完整。随后用 PDF hash 不一致注入构建失败：任务以 `library paper hash does not match its source` fail closed，旧 active pointer 未被替换；恢复 PDF 后 supervisor 按 60 秒退避自动重排并恢复 `ready/fresh`。产品当前没有论文删除 API 或界面入口，因此删除动作只能在隔离副本终端完成，不能冒充“用户从 UI 删除”；删除后真实 React 立即回到 198/198，唯一标记搜索无结果，active claims SQLite 中该 paper id 为 0，自动生成 198-paper fresh generation。
- 停止隔离 API/Web 后，使用正常 `start.sh`（只指定仓库要求的 Node 24 可执行文件和独立端口，没有 Ask 功能开关）重新启动；API 返回同一 198-paper generation 的 `ready/fresh`。全局 Node 26 的首次启动被版本检查按预期拒绝，没有启动半成品服务。恢复连接后再次从普通 `start.sh` 启动，真实 React Ask 抽屉明确显示“原文证据 已启用”及同一 generation，58.9 秒 Astra 回答、4 条页级引用与证据边界均在浏览器刷新／进程重启后保留；资料页仍为 198/198，精确合成标记搜索显示无匹配。SQLite 只读复核为 198 paper rows、0 个精确标记 paper／knowledge node，active claims 中该 paper id 为 0。随后 API/Web 与独立 worker 均正常关闭，8138/5238 无监听、任务队列无 queued/running。
- 512 MiB 单论文合成库清空进程缓存后，v2 无可信 inventory 的独立 hash subprocess 为 930.725 ms；随后以该 v2 inventory 复核同一受管文件为 0.669 ms，revision 完全一致且记录 `stat_reliable=true`。这是本机单次 `library_revision` 合成测量，不是完整 Ask p95；外部／低于毫秒精度的文件系统主动选择安全重算，可能在 5 秒预算内返回不可用，而不接受无法证明的新鲜度。
- 首次直接扩展专项在受限沙箱有 5 项 external worker 领取队列超时；获准的 disposable 检查中全部通过。首次统一检查另有 3 项被离线 guard 拒绝，因为新 hash worker 的最小环境未继承 guard 标记；修复为仅在存在时传递 `KNOWRA_OFFLINE_ROOT`／`PYTHONPATH`，仍不传 provider、代理或凭据，随后完整统一检查原样重跑全绿。没有放宽数据根或网络边界。

## 数据、备份与回滚

- 本轮没有读取配置或修改日常数据库、论文、Wiki、generation、control pointer 和既有 worker。release 验收只修改全新 worktree 内的已验证恢复副本；普通启动和真实界面操作均使用独立端口，结束后完整关闭。
- refresh 只读 live library，所有数据库/PDF 快照和候选写在 `.knowra` 隔离目录；临时 snapshot 无论成功失败都会清理。
- 自动 refresh 不替代用户数据备份：它不改 live 数据；模型重新抽取、数据库迁移或覆盖用户资料仍须先执行完整备份。
- promotion 前失败保持旧 active；promotion 后旧 generation 和 promotion record 保留。已有 active 时，最后 revision 核验失败会自动 compare-and-swap rollback；marker 落盘失败会作为独立错误报告，但不阻止回滚。没有旧 active 的首次 promotion 无可回滚目标，错误会明确说明该边界；新 pointer 会因 revision 不匹配而在 Ask 运行时 fail closed，startup reconciliation 后续重建。
- 系统没有让论文文件写入、数据库事务、模型调用共同持有一个全局锁；它使用构建前后／切换后／检索前后／provider 前／回答采纳前的 revision 检查缩小窗口并 fail closed。provider 前复核与实际网络发送之间仍存在不可完全消除的极窄竞态，不能承诺资料在模型请求整个持续时间内不可变。前台 1 秒 deadline 覆盖正文 hash subprocess；SQLite 只读查询、网络文件系统的路径解析／单次 `stat` 和本地 IPC 本身不具备可抢占的硬实时上限。较大弱／外部文件库可能暂时显示 `library_revision_timeout` 并由后台完整刷新，而不是以旧 cache 冒充 fresh；高频连续变更也可能延后新 generation 就绪。

## Release 验收边界

- PR #55 尚未合并，但统一候选已包含其远端提交及本生命周期修复，因此不要求用户先合并 #55；没有自行合并或部署。已有 #55 不由本轮擅自关闭，统一 PR 直接面向 `main`，用户只需 review／合并统一 PR。
- 真实恢复副本覆盖普通安装、无开关启动、首次自动 generation、真实 React 状态、真实界面 notes 更新／恢复与隔离合成论文上传、真实 Astra 回答及引用、失败回退／自动重试、删除后 UI 不可见、普通重启和 pointer 回滚。并发变更、worker 中断、磁盘／fsync 失败及 legacy 升级仍由隔离合成／真实 pypdf 回归覆盖，没有为验收改动日常论文。
- 仍有一项准确边界：产品没有用户可操作的论文删除入口，因此删除触发只能验证底层生命周期和删除后的真实 UI 状态，不能验证“从 UI 发起删除”。普通重启后的真实 React 状态、持久化回答和引用已完成复核；是否把“提供论文删除 UI”纳入本轮产品范围仍需明确决定。
- 日常真实资料变更或超出同批 5 问／每题最多 4 段证据的模型调用仍需新授权；本轮严格使用原第 3 问，没有扩大传输范围。
