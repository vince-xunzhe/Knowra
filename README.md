# Knowledge Wiki

[中文](README.zh.md) | [English](README.md)

Knowledge Wiki, named Knowledge Tree in the app, is a local paper knowledge graph tool. It scans PDF papers, uses OpenAI Assistants API with file_search to extract structured insights, and turns papers, methods, datasets, research areas, findings, and notes into a searchable interactive research map.

## Screenshots

### Knowledge Graph

![Knowledge Wiki graph page](docs/assets/knowledge-wiki-preview.png)

### Paper Library

![Knowledge Wiki paper library page](docs/assets/knowledge-wiki-papers.png)

### Paper Review

![Knowledge Wiki paper review page](docs/assets/knowledge-wiki-review.png)

## Features

- Scan a local paper directory and track PDF files, page counts, first-page thumbnails, and processing status.
- Use an editable prompt to extract titles, authors, methods, datasets, baselines, contributions, and key findings.
- Build knowledge nodes and typed edges such as `uses`, `belongs_to`, `builds_on`, `trained_on`, `evaluated_on`, `compared_to`, `finding`, and `similar`.
- Connect concepts across papers with embedding similarity and rebuild similarity edges with a configurable threshold.
- Provide a React UI for the knowledge graph, paper library, paper review, prompt editing, and settings.
- Store everything locally in SQLite under `data/`, making it practical for iterative personal research workflows.

## Tech Stack

- Backend: FastAPI, SQLAlchemy, SQLite, OpenAI Python SDK, pypdf, and pypdfium2.
- Frontend: React, TypeScript, Vite, Tailwind CSS, Cytoscape, Axios, and Lucide React.
- Startup: `start.sh` prefers Docker Compose for a pinned runtime, and falls back to local Python/Node when Docker is unavailable.

## Project Structure

```text
.
├── backend/                 # FastAPI API, database models, and paper services
│   ├── routers/             # papers / graph / config / prompt routes
│   ├── services/            # PDF, scanning, LLM extraction, and graph building
│   ├── config.py            # runtime config and model defaults
│   ├── database.py          # SQLite initialization and lightweight migrations
│   └── requirements.txt
├── frontend/                # React + Vite frontend
│   ├── src/api/             # API client and types
│   ├── src/components/      # graph, node detail, and processing status components
│   └── src/pages/           # graph, papers, review, prompt, and settings pages
├── data/                    # local runtime data; the repo only keeps empty placeholders
│   ├── artifacts/           # first-page render cache
│   └── papers/              # default paper scan directory
├── docs/                    # architecture, API, and development docs
└── start.sh                 # one-command launcher
```

## Quick Start

### 1. Prepare The Environment

Recommended:

- Docker Desktop, or Docker CLI + Colima + Docker Compose.
- A valid OpenAI API key.

When a Docker runtime is available, the project does not depend on the host Python, Node, or npm versions. If Docker is unavailable, `start.sh` falls back to native mode and needs:

- Python 3.10 or newer.
- Node.js 20 or newer.
- npm.

### 2. Start The App

```bash
./start.sh
```

The script will:

1. Detect whether Docker Compose is available.
2. Build and start pinned backend/frontend containers when Docker is available; if Colima is detected but stopped, it starts Colima automatically.
3. Fall back to native Python/Node setup when Docker is unavailable.
4. Avoid occupied ports by searching from `8000` / `5173` upward.
5. Start the frontend and open the browser.

Optional modes:

```bash
KNOWLEDGE_WIKI_MODE=docker ./start.sh   # force container mode
KNOWLEDGE_WIKI_MODE=native ./start.sh   # force native mode
OPEN_BROWSER=0 ./start.sh               # do not open the browser automatically
BACKEND_PORT=8000 FRONTEND_PORT=5173 ./start.sh
```

### 3. Configure Paper Processing

Open the app and go to Settings:

1. Enter your OpenAI API key.
2. Confirm the scan directory, which defaults to `data/papers`.
3. Select the paper processing model and embedding model.
4. Set the similarity threshold, defaulting to `0.6`.
5. Save the settings.

You can also provide the API key through an environment variable:

```bash
export OPENAI_API_KEY="sk-..."
./start.sh
```

For multi-machine deployments, copy the config template:

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` in `.env`. The `.env` file is read by both Docker Compose and the native fallback, and is ignored by Git.

### 4. Add And Process Papers

1. Put PDFs in `data/papers/`, or set another scan directory in Settings.
2. Click Scan Directory on the Graph page.
3. Click Process Papers to extract knowledge in batch.
4. Check per-paper status in the Paper Library; failed items can be retried.
5. Review extracted results on the Paper Review page, edit responses or prompts when needed, and reprocess papers.

## Manual Run

Container mode:

```bash
docker compose up --build
```

Native mode:

Backend:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Maintenance

- Rebuild similarity edges: run Rebuild Similarity Edges in Settings. This does not call the model again.
- Reset graph: run Reset Graph in Settings. This clears nodes and edges and marks papers as unprocessed.
- Edit extraction prompt: use the Prompt page; future processing runs will use the updated prompt.
- Clear cached assistant: set `openai_assistant_id` to an empty value through the config API. The next run creates a new assistant.

## Data And Privacy

The following local runtime data is ignored by default:

- `data/config.json`: contains API keys and local config.
- `data/knowledge.db`: SQLite knowledge database.
- `data/papers/*`: local PDF files.
- `data/artifacts/*`: rendered first-page images.
- `backend/.venv`, `frontend/node_modules`, `frontend/dist`, and other dependency or build artifacts.

If you need to share sample data, create a separate sanitized example directory and document its source and license.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Development](docs/DEVELOPMENT.md)
