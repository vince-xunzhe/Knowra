# 安装说明

[中文](INSTALL.zh.md) | [English](INSTALL.md)

本文档说明 Knowledge Wiki 的依赖环境、启动命令和手动运行方式。

## 环境要求

推荐安装：

- Docker Desktop，或 Docker CLI + Colima + Docker Compose。
- 一个可用的 OpenAI API Key。

有 Docker runtime 时，项目会使用固定容器环境运行，不依赖宿主机 Python、Node 或 npm 版本。

本机 fallback 模式需要：

- Python 3.10 或更新版本。
- Node.js 20 或更新版本。
- npm。

## 快速开始

```bash
./start.sh
```

启动脚本会自动：

1. 检测 Docker Compose 是否可用。
2. Docker 可用时构建并启动后端和前端容器。
3. 如果检测到 Colima 但 runtime 未启动，会自动启动 Colima。
4. Docker 不可用时回退到本机 Python/Node 模式。
5. 自动避开已占用端口，从 `8000` 和 `5173` 向后寻找可用端口。
6. 启动前端并打开浏览器。

## 运行选项

```bash
KNOWLEDGE_WIKI_MODE=docker ./start.sh   # 强制容器模式
KNOWLEDGE_WIKI_MODE=native ./start.sh   # 强制本机模式
OPEN_BROWSER=0 ./start.sh               # 不自动打开浏览器
BACKEND_PORT=8000 FRONTEND_PORT=5173 ./start.sh
```

## 配置 API Key

启动后可以在应用的「设置」页配置 API Key。

也可以通过环境变量临时提供：

```bash
export OPENAI_API_KEY="sk-..."
./start.sh
```

如果需要长期本地运行，可以复制配置模板：

```bash
cp .env.example .env
```

然后在 `.env` 中填写 `OPENAI_API_KEY`。`.env` 会被 Docker Compose 和本机 fallback 共同读取，并且默认不提交到 Git。

## 首次使用

1. 运行 `./start.sh` 启动应用。
2. 打开「设置」页。
3. 填入 OpenAI API Key。
4. 确认扫描目录，默认是 `data/papers`。
5. 选择论文处理模型和 embedding 模型。
6. 设置相似度阈值，默认 `0.6`。
7. 保存设置。

## 手动运行

容器模式：

```bash
docker compose up --build
```

本机后端：

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

本机前端：

```bash
cd frontend
npm install
npm run dev
```

## 排查

- 如果端口被占用，`start.sh` 会自动尝试下一个端口。
- 如果 Docker 不可用，可以使用 `KNOWLEDGE_WIKI_MODE=native ./start.sh`。
- 如果本机模式的 Node 报错，请确认 `node -v` 是 20 或更新版本。
- 如果论文处理失败，请检查应用「设置」页中的 OpenAI API Key 和模型配置。

返回 [README](README.zh.md)。
