# 识途（Roadmap）

识途面向系统技术大类，调研约 20–30 篇经典及里程碑论文，生成可编辑的发展路线图。它复用索骥的拖拽、备注、分组、连线、锁定和侧栏交互，但有独立的画布、撤销历史和本地存储。索骥已有画布不受影响。

## 使用流程

1. 在左侧选择「识途」，再选择技术方向。大类继承系统分类，也可新增自定义方向。
2. 点击「调研路线图」。后台依次规划研究问题和标准、多库检索、分层筛选、关键论文全文阅读与评估、主题综合、最终核验和成文。生成期间当前方向仅可浏览；可以切换到其他方向。
3. 查看生成的方案，选择应用或放弃。首次生成会按技术分支建立可编辑分组。重新调研保留既有卡片、文字、分组、备注及锁定连线；其余连线和未锁定位置可重新组织。每次最多筛选 30 篇，历史卡片保留后总数可超过 30。
4. 点击卡片查看入选理由、摘要与来源，或点击连线查看证据。顶部「调研报告」可查看并下载包含七步记录的 Markdown。可以继续人工修正布局、备注和关系。
5. 点击「一键下载 arXiv」，下载当前已应用路线图上的 arXiv 论文到配置的论文目录和资料库。按钮不触发自动论文解析。卡片和顶部统计反映入库状态；已有回顾可直接跳转。

非 arXiv 的经典论文仍在图中并带来源链接，批量下载会跳过它们。“非 arXiv”表示此次核验未找到 arXiv 版本，不是断言该论文从未发布预印本。

## 专用 skill 与模型

运行时直接读取仓库中的 [shitu-roadmap/SKILL.md](../skills/shitu-roadmap/SKILL.md) 和 [输出契约](../skills/shitu-roadmap/references/output-contract.md)。同一目录也可安装到 Codex 的 skills 目录独立使用；后端不依赖用户主目录中是否安装该 skill。

模型任务为 `roadmap_research`。未单独配置时继承 `domain_connect`（索骥连线）的模型绑定；可在设置的「识途调研」中独立选择模型。模型负责提出检索线索与综合分析，不直接决定哪些论文已入库，也不接受未经来源核验的虚构书目信息。

## 核验与证据边界

- 当前运行时查询 arXiv 官方 Atom API 和 Crossref DOI 元数据；通过规范标题核对种子论文，保留可追溯来源。身份合并优先使用标识符，同名回退需要作者匹配，避免替换不同论文。
- 标题/摘要筛选后，重点读取最多 8 篇论文。优先使用已入库的本地 PDF/提取文本，否则只从官方 arXiv 临时下载全文。临时 PDF 自动清理，不入库、不访问付费全文。每篇最多读取 50 页、50,000 字符，逐页保留覆盖、空白页、失败页和截断信息；无页码的本地提取文本不会编造页码。
- 逐篇模型评估提取贡献、方法、实验、限制和关联，并核对原文引文。综合阶段只使用通过筛选的论文和有可定位证据的评估。元数据核验不等于经典性或继承关系被原文证明；有全文引文且作者明确陈述的关系可标原文明示，其余为 AI 推断。
- 连线引文必须来自已提供的标题、摘要或对应页的全文；未知论文 ID、虚构引文、错误页码、自环及反向年代关系会被过滤。引用边还必须有目标论文参考文献中的源论文标识符，或目标摘要/全文中的明确提及。主题相近不构成引用。
- 无法核验的论文作为缺口报告，返回数量允许少于 20。论文摘要缺失时不补造摘要或引文；证据不足时保留独立节点。

## 本机数据与任务

新增本机 SQLite 表 `roadmap_workspace`、`roadmap_papers`、`roadmap_runs`（由现有数据库初始化流程建表），不参与云端同步。来源论文 ID 与资料库 `Paper.id` 分离。

- `GET/PUT /api/roadmaps`：带 revision 乐观并发检查的工作区。
- `GET /api/roadmaps/papers`：已核验目录、预览说明、动态入库/下载状态。
- `GET/POST /api/roadmaps/agent/runs`：调研任务；`/{id}/apply`、`/{id}/discard` 应用、放弃或取消。
- `GET/POST /api/roadmaps/imports`：查看最近批次、显式发起当前路线图下载。
- `GET /api/roadmaps/report?boardId=...`：当前预览或最近已应用的结构化 Markdown 调研报告；放弃预览后恢复已应用报告。

后台任务种类为 `roadmap` 和 `roadmap_import`。方案预览不修改实时画布或论文库；已有论文说明随应用事务写入，放弃后恢复旧说明。应用后撤销仅回退画布调整，不回退已核验的来源信息和入选说明。只为预览显示注册的新来源目录条目不代表论文入库。当前方向在调研/预览期间由前后端共同限制编辑，应用前检查原图是否发生变化。

下载仅访问官方 arXiv HTTPS 地址，串行限流、每份 PDF 上限 80 MB，验证文件结构后原子保存，不覆盖同名文件。逐篇保存结果，部分失败不会丢失成功项，重试会跳过已入库论文。下载中仍可编辑画布；该批次处理点击按钮时的论文快照。

## 验证

后端使用隔离 SQLite 和模拟模型/网络验证来源核验、身份匹配、证据过滤、预览放弃/应用、锁定保护、并发保存、下载失败和断点续传。前端使用 Playwright 验证空画布调研、预览应用、状态显示、部分失败重试、跨模块跳转、独立画布及模型配置，并回归原索骥功能。

```bash
.venv/bin/python -m pytest -q backend/tests/test_roadmap_research.py backend/tests/test_roadmap_sources.py backend/tests/test_roadmap_fulltext.py backend/tests/test_roadmap_report.py backend/tests/test_roadmaps.py backend/tests/test_model_gateway.py backend/tests/test_domain_agent.py backend/tests/test_domains.py backend/tests/test_task_queue.py backend/tests/test_task_executor.py
cd frontend
npm run build
E2E_PORT=4183 npm run test:e2e -- tests/domain.spec.ts tests/roadmap.spec.ts tests/appearance.spec.ts
```

真实模型输出的调研质量仍需按具体方向人工检查。元数据 API 的覆盖率、网络可用性和缺失摘要会影响候选数量与连线完整性；自动测试不将这些外部因素伪装成已验证的研究质量指标。
