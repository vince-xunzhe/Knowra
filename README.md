# Knowledge Wiki

[中文](README.md) | [English](README.en.md)

Knowledge Wiki（应用内名为 Knowledge Tree）是一个本地论文知识图谱工具。它会扫描 PDF 论文，调用 OpenAI Assistants API + file_search 抽取结构化知识，再把论文、技术、数据集、研究领域和关键发现组织成可搜索、可浏览的图谱。

## 界面预览

### 知识图谱

![Knowledge Wiki 知识图谱页面](docs/assets/knowledge-wiki-preview.png)

### 论文库

![Knowledge Wiki 论文库页面](docs/assets/knowledge-wiki-papers.png)

### 论文回顾

![Knowledge Wiki 论文回顾页面](docs/assets/knowledge-wiki-review.png)

## 功能概览

- 扫描本地论文目录，记录 PDF 文件、页数、首页缩略图和处理状态。
- 使用可编辑 Prompt 抽取论文标题、作者、方法、数据集、baseline、贡献和关键发现。
- 自动生成知识节点与关系边，包括 `uses`、`belongs_to`、`builds_on`、`trained_on`、`evaluated_on`、`compared_to`、`finding` 和 `similar`。
- 基于 embedding 相似度连接跨论文概念，并支持按阈值重建相似边。
- 提供 React 前端界面：知识图谱、论文库、抽取结果回顾、Prompt 编辑和系统设置。
- 本地 SQLite 存储，默认数据位于 `data/`，方便个人知识库迭代。

## 技术栈

- 后端：FastAPI、SQLAlchemy、SQLite、OpenAI Python SDK、pypdf、pypdfium2。
- 前端：React、TypeScript、Vite、Tailwind CSS、Cytoscape、Axios、Lucide React。
- 运行脚本：`start.sh` 默认优先使用 Docker Compose 固定运行环境；没有 Docker 时自动回退到本机 Python/Node。

## 目录结构

```text
.
├── backend/                 # FastAPI API、数据库模型和论文处理服务
│   ├── routers/             # papers / graph / config / prompt 路由
│   ├── services/            # PDF、扫描、LLM 抽取、图谱构建逻辑
│   ├── config.py            # 运行配置与默认模型
│   ├── database.py          # SQLite 初始化与轻量迁移
│   └── requirements.txt
├── frontend/                # React + Vite 前端
│   ├── src/api/             # API client 与类型
│   ├── src/components/      # 图谱、节点详情、处理状态组件
│   └── src/pages/           # 图谱、论文、回顾、Prompt、设置页面
├── data/                    # 本地运行数据，仓库只保留空目录占位
│   ├── artifacts/           # 首页渲染图缓存
│   └── papers/              # 默认论文扫描目录
├── docs/                    # 架构、API 和开发说明
└── start.sh                 # 一键启动脚本
```

## 快速开始

### 1. 准备环境

推荐本机安装：

- Docker Desktop，或 Docker CLI + Colima + Docker Compose。
- 一个可用的 OpenAI API Key。

有 Docker runtime 时，项目不依赖宿主机的 Python、Node 或 npm 版本。没有 Docker 时，`start.sh` 会回退到本机模式，此时需要：

- Python 3.10 或更新版本。
- Node.js 20 或更新版本。
- npm。

### 2. 启动应用

```bash
./start.sh
```

脚本会自动：

1. 检测 Docker Compose 是否可用。
2. Docker 可用时，构建并启动固定版本的后端和前端容器；如果检测到 Colima 但 runtime 未启动，会自动启动 Colima。
3. Docker 不可用时，创建本机 Python 虚拟环境并安装前后端依赖。
4. 自动避开已占用端口，从 `8000` / `5173` 向后寻找可用端口。
5. 启动前端并打开浏览器。

可选运行模式：

```bash
KNOWLEDGE_WIKI_MODE=docker ./start.sh   # 强制容器模式
KNOWLEDGE_WIKI_MODE=native ./start.sh   # 强制本机模式
OPEN_BROWSER=0 ./start.sh               # 不自动打开浏览器
BACKEND_PORT=8000 FRONTEND_PORT=5173 ./start.sh
```

### 3. 配置论文处理

打开前端后进入「设置」页：

1. 填入 OpenAI API Key。
2. 确认扫描目录，默认是 `data/papers`。
3. 选择论文处理模型和 embedding 模型。
4. 设置相似度阈值，默认 `0.6`。
5. 保存设置。

也可以用环境变量临时提供 API Key：

```bash
export OPENAI_API_KEY="sk-..."
./start.sh
```

多机器部署时，也可以复制配置模板：

```bash
cp .env.example .env
```

然后在 `.env` 中填写 `OPENAI_API_KEY`。`.env` 会被 Docker Compose 和本机 fallback 共同读取，且不会提交到仓库。

### 4. 添加和处理论文

1. 将 PDF 放入 `data/papers/`，或在设置中改成其他论文目录。
2. 在「图谱」页点击「扫描目录」。
3. 点击「处理论文」批量抽取知识。
4. 在「论文」页查看单篇状态，失败项可以重试。
5. 在「回顾」页查看原始抽取结果，必要时调整 Prompt 后重跑。

## 手动运行

容器模式：

```bash
docker compose up --build
```

本机模式：

后端：

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 常用维护

- 重建相似度边：在「设置」页执行「重建相似度边」，不会重新调用大模型。
- 重置图谱：在「设置」页执行「重置图谱」，会清空知识节点和边，并把论文标记为待处理。
- 修改抽取 Prompt：在「Prompt」页编辑，后续处理会使用新 Prompt。
- 清除缓存 Assistant：设置接口支持把 `openai_assistant_id` 置空，下次处理会重新创建 Assistant。

## 数据与隐私

默认不提交以下本地运行数据：

- `data/config.json`：包含 API Key 和本地配置。
- `data/knowledge.db`：SQLite 知识库。
- `data/papers/*`：本地 PDF。
- `data/artifacts/*`：渲染出来的首页图片。
- `backend/.venv`、`frontend/node_modules`、`frontend/dist` 等依赖和构建产物。

如需共享示例数据，建议另建脱敏样例目录，并在 README 中说明来源和许可。

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [API 说明](docs/API.md)
- [开发说明](docs/DEVELOPMENT.md)
