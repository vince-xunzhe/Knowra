# Install

[中文](INSTALL.zh.md) | [English](INSTALL.md)

This guide covers environment requirements, startup commands, and manual run options for Knowledge Wiki.

## Requirements

Recommended:

- Docker Desktop, or Docker CLI + Colima + Docker Compose.
- A valid OpenAI API key.

When Docker is available, the project runs in pinned containers and does not depend on host Python, Node, or npm versions.

Native fallback requirements:

- Python 3.10 or newer.
- Node.js 20 or newer.
- npm.

## Quick Start

```bash
./start.sh
```

The launcher will:

1. Detect whether Docker Compose is available.
2. Build and start backend/frontend containers when Docker is available.
3. Start Colima automatically when Colima is installed but stopped.
4. Fall back to native Python/Node setup when Docker is unavailable.
5. Avoid occupied ports by searching upward from `8000` and `5173`.
6. Start the frontend and open the browser.

## Runtime Options

```bash
KNOWLEDGE_WIKI_MODE=docker ./start.sh   # force container mode
KNOWLEDGE_WIKI_MODE=native ./start.sh   # force native mode
OPEN_BROWSER=0 ./start.sh               # do not open the browser automatically
BACKEND_PORT=8000 FRONTEND_PORT=5173 ./start.sh
```

## Configure The API Key

You can configure the API key in the app Settings page after startup.

For temporary shell-based setup:

```bash
export OPENAI_API_KEY="sk-..."
./start.sh
```

For repeated local runs, copy the config template:

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` in `.env`. The `.env` file is read by both Docker Compose and the native fallback, and is ignored by Git.

## First Run

1. Start the app with `./start.sh`.
2. Open Settings.
3. Enter your OpenAI API key.
4. Confirm the scan directory, defaulting to `data/papers`.
5. Select the paper processing model and embedding model.
6. Set the similarity threshold, defaulting to `0.6`.
7. Save settings.

## Manual Run

Container mode:

```bash
docker compose up --build
```

Native backend:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

Native frontend:

```bash
cd frontend
npm install
npm run dev
```

## Troubleshooting

- If a port is already occupied, `start.sh` automatically tries the next port.
- If Docker is unavailable, use `KNOWLEDGE_WIKI_MODE=native ./start.sh`.
- If native mode fails on Node, check that `node -v` is 20 or newer.
- If paper processing fails, confirm the OpenAI API key and model settings in the app Settings page.

Return to [README](README.md).
