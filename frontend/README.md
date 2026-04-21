# Knowledge Wiki Frontend

这是 Knowledge Wiki 的 React 前端，基于 Vite、TypeScript、Tailwind CSS 和 Cytoscape 构建。

## 页面

- `GraphPage`：知识图谱主界面，支持扫描论文、批量处理、节点搜索和类型过滤。
- `PapersPage`：论文库，支持网格/列表视图、状态过滤、单篇处理和失败重试。
- `ReviewPage`：查看单篇论文的提取文本、原始模型响应和生成的知识节点。
- `PromptEditorPage`：编辑和重置论文抽取 Prompt。
- `SettingsPage`：配置 API Key、扫描目录、模型、相似度阈值和维护操作。

## 开发

安装依赖：

```bash
npm install
```

启动开发服务：

```bash
npm run dev
```

构建生产包：

```bash
npm run build
```

运行 lint：

```bash
npm run lint
```

## API

前端通过 `src/api/client.ts` 访问后端。开发环境下，Vite 会把 `/api` 请求代理到 FastAPI 服务。

默认端口：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

更多接口说明见根目录的 `docs/API.md`。
