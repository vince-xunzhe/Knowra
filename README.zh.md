# Knowra

[中文](README.zh.md) | [English](README.md)

Knowra 是一个本地优先的 AI 研究工作台，帮助用户从论文和领域知识中快速建立专家理解。它会扫描 PDF 论文，调用 OpenAI 模型抽取结构化回顾信息，并把论文、技术、数据集、关键发现和个人笔记组织成可浏览的知识图谱。

安装、依赖环境和启动命令请看 [安装说明](INSTALL.zh.md)。

## 应用功能

- **论文库**：扫描本地 PDF 目录，记录页数、首页预览图和每篇论文的处理状态。
- **论文回顾**：查看模型抽取的标题、作者、摘要、研究问题、方法、数据集、baseline、贡献和关键发现。
- **修复流程**：当模型回答格式有小问题时，可以编辑原始 response；也可以对单篇论文确认后重新处理，并调整抽取 Prompt。
- **个人笔记**：每篇论文支持 Markdown 笔记，可直接粘贴或拖入截图，并支持图片放大查看。
- **论文档案 markdown**：每篇论文都有一份持续累积的 markdown 档案，包含源资料信息、首次 file_search response、当前 response、用户笔记和完整追问记录。
- **知识图谱**：浏览论文、技术、数据集、研究领域、关键发现和相似节点之间的关系。
- **相似度重建**：按配置阈值重建 embedding 相似边，不需要重新抽取论文。
- **本地存储**：数据库、PDF、缩略图、笔记图片和配置都保存在本地 `data/` 目录。

## 界面预览

### 知识图谱

![Knowra 知识图谱页面](docs/assets/knowledge-wiki-preview.png)

### 论文库

![Knowra 论文库页面](docs/assets/knowledge-wiki-papers.png)

### 论文回顾

![Knowra 论文回顾页面](docs/assets/knowledge-wiki-review.png)

## 使用流程

1. 将 PDF 放到 `data/papers/`，或在设置中选择其他扫描目录。
2. 打开「图谱」页，扫描论文目录。
3. 批量处理论文，或在回顾页面对单篇论文重新处理。
4. 查看抽取结果；如果 response 格式有小错误，可以直接编辑并保存修复。
5. 阅读论文时添加 Markdown 个人笔记和截图。
6. 通过图谱探索论文、方法、数据集、研究领域和发现之间的连接。
7. 随着研究语料变化，调整 Prompt 或相似度阈值。

如果你想看一篇论文如何从 PDF 进入系统并最终变成图谱节点和边，可以直接看 [架构说明](docs/ARCHITECTURE.md) 里的链路图。

## 数据架构

Knowra 使用 SQLite 和本地文件系统保存运行数据。

### 核心表

- `papers`：每个 PDF 一行，包括路径、标题、作者、会议/期刊、年份、页数、处理状态、模型 response、解析后的抽取结果、个人笔记和聊天状态。
- `nodes`：从论文中生成的图谱实体，主要类型包括 `paper`、`technique`、`dataset`、`problem_area`、`finding`。
- `edges`：节点之间的类型化关系。
- `config`：本地应用设置，包括模型选择、扫描目录、相似度阈值和缓存的 assistant ID。
- `prompt`：可编辑的抽取 Prompt，后续处理会使用最新版本。

### 图谱关系

图谱使用类型化边组织论文知识：

- `uses`：论文或方法使用某项技术。
- `belongs_to`：论文或概念属于某个研究领域。
- `builds_on`：某个方法建立在另一个方法之上。
- `trained_on`：模型或论文使用某个数据集训练。
- `evaluated_on`：论文在某个数据集上评测。
- `compared_to`：论文与某个 baseline 对比。
- `finding`：论文支持某个关键发现。
- `similar`：两个节点通过 embedding 相似度连接。

### 本地文件

```text
data/
├── config.json              # 本地设置和 API Key，默认不提交
├── knowledge.db             # SQLite 数据库，默认不提交
├── papers/                  # 默认 PDF 扫描目录，默认不提交
├── artifacts/
    ├── first_pages/         # 首页预览图缓存
    └── note_images/         # 粘贴或拖入笔记的图片
└── paper_records/           # 每篇论文一份 markdown 知识档案
```

## 项目结构

```text
.
├── backend/                 # FastAPI API、数据库模型和论文处理服务
│   ├── routers/             # papers / graph / config / prompt / note image 路由
│   ├── services/            # PDF、扫描、LLM 抽取、图谱构建和清理逻辑
│   ├── config.py            # 运行配置与默认模型
│   ├── database.py          # SQLite 初始化与轻量迁移
│   └── requirements.txt
├── frontend/                # React + Vite 前端
│   ├── src/api/             # API client 与类型
│   ├── src/components/      # 图谱、详情、处理状态组件
│   └── src/pages/           # 图谱、论文、回顾、Prompt、设置页面
├── data/                    # 本地运行数据，仓库只保留占位
├── docs/                    # 架构、API 和开发说明
├── INSTALL.md               # 英文安装与快速开始说明
└── start.sh                 # 一键启动脚本
```

## 常用操作

- **重建相似度边**：在「设置」页执行「重建相似度边」，不会重新调用抽取模型。
- **重置图谱**：在「设置」页执行「重置图谱」，会清空生成的节点和边，并把论文标记为待处理。
- **修改抽取 Prompt**：在「Prompt」页编辑，后续处理会使用新 Prompt。
- **修复 response**：在「论文回顾」页编辑模型 response，保存后重新解析。
- **重新处理单篇论文**：使用单篇论文的重新处理按钮，应用会先弹出确认。
- **清除缓存 Assistant**：通过配置 API 将 `openai_assistant_id` 置空，下次处理会重新创建 assistant。

## 隐私说明

Knowra 面向本地个人研究流程。默认不会提交以下运行数据：

- `data/config.json`
- `data/knowledge.db`
- `data/papers/*`
- `data/artifacts/*`
- `data/paper_records/*`
- `backend/.venv`
- `frontend/node_modules`
- `frontend/dist`

模型处理会把论文内容发送到配置的 OpenAI API。请不要把私有或受版权限制的论文提交到共享仓库；如需共享示例数据，建议使用脱敏样例。

## 文档

- [安装说明](INSTALL.zh.md)
- [架构说明](docs/ARCHITECTURE.md)
- [API 说明](docs/API.md)
- [开发说明](docs/DEVELOPMENT.md)
