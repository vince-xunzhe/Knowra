# 开发说明

本文档记录本地开发、校验和发布时最常用的操作。

## 本地开发

一键启动：

```bash
./start.sh
```

分别启动：

```bash
cd backend
. .venv/bin/activate
uvicorn main:app --port 8000 --reload
```

```bash
cd frontend
npm run dev
```

## 依赖管理

后端依赖写在 `backend/requirements.txt`。新增 Python 依赖后，手动更新该文件。

前端依赖写在 `frontend/package.json` 和 `frontend/package-lock.json`。新增依赖时在 `frontend/` 下执行：

```bash
npm install package-name
```

## 基础校验

后端语法检查：

```bash
python3 -m compileall -q -x 'backend/\\.venv' backend
```

前端构建：

```bash
cd frontend
npm run build
```

前端 lint：

```bash
cd frontend
npm run lint
```

## 数据目录

`data/` 是本地运行状态目录。仓库只保留以下占位文件：

- `data/papers/.gitkeep`
- `data/artifacts/.gitkeep`

以下内容默认被 `.gitignore` 排除：

- PDF 文件。
- SQLite 数据库。
- 渲染图片缓存。
- `data/config.json`，因为它可能包含 API Key。

## 处理失败排查

1. 在「论文」页查看单篇错误。
2. 在「回顾」页查看 `raw_llm_response`，确认模型是否返回了合法 JSON。
3. 检查「设置」页里的 API Key、模型和扫描目录。
4. 如果修改了 Prompt，优先对一篇论文重试，再批量处理。
5. 如果相似边过多或过少，只需要调整阈值并重建相似度边。

## 发布建议

提交前建议运行：

```bash
python3 -m compileall -q -x 'backend/\\.venv' backend
cd frontend && npm run build
```

如果仓库里出现了本地数据，先确认 `.gitignore` 是否覆盖，然后只暂存源码、文档和必要配置。
