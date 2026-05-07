# Knowra

[中文](README.zh.md) | [English](README.md)

Knowra is a local-first research workspace for building expertise from papers and domain knowledge. It scans PDF papers, extracts structured review data with an OpenAI model, and turns papers, methods, datasets, findings, and notes into an interactive knowledge graph.

For installation, runtime dependencies, and startup commands, see [Install](INSTALL.md).

## What The App Does

- **Paper library**: scan a local folder of PDFs, keep page counts and first-page previews, and track processing state for each paper.
- **Paper review**: inspect model-extracted metadata, summaries, research questions, methods, datasets, baselines, contributions, and findings.
- **Repair workflow**: edit raw model responses when formatting is slightly wrong, reprocess a single paper with confirmation, and tune the extraction prompt.
- **Personal notes**: write Markdown notes per paper, paste or drop screenshots into notes, and zoom images in a lightbox.
- **Paper record markdown**: keep a durable markdown record for every paper, including source metadata, the first file_search response, current working response, notes, and the full follow-up chat log.
- **Knowledge graph**: browse generated nodes for papers, techniques, datasets, research areas, findings, and similarity links.
- **Similarity rebuilds**: recompute embedding-based `similar` edges with a configurable threshold without re-running paper extraction.
- **Local storage**: keep the database, PDFs, thumbnails, note images, and configuration under the local `data/` directory.

## Screenshots

### Knowledge Graph

![Knowra graph page](docs/assets/knowledge-wiki-preview.png)

### Paper Library

![Knowra paper library page](docs/assets/knowledge-wiki-papers.png)

### Paper Review

![Knowra paper review page](docs/assets/knowledge-wiki-review.png)

## Typical Workflow

1. Put PDF papers in `data/papers/`, or choose another scan directory in Settings.
2. Open the Graph page and scan the paper directory.
3. Process papers in batch, or reprocess a specific paper from the review view.
4. Review the extracted response, fix small formatting issues if needed, and save the repaired response.
5. Add personal Markdown notes and screenshots while reading.
6. Use the graph to explore connections between papers, techniques, datasets, findings, and research areas.
7. Adjust the prompt or similarity threshold as your research corpus evolves.

For the end-to-end pipeline from a PDF to graph nodes and edges, see [Architecture](docs/ARCHITECTURE.md).

## Wiki Compilation

Every processed paper can be compiled into a markdown wiki entry at `data/wiki/papers/{id}-{slug}.md`. This step **does not re-read the PDF and does not call file_search** — it just asks the LLM to rewrite the already-extracted JSON in DB into Chinese narrative markdown, tagging method / concept / dataset names with `[[…]]` backlink markers so tools like Obsidian can navigate between papers and concepts.

```mermaid
flowchart TD
    A["DB: paper.raw_llm_response<br/>extracted JSON"] --> P
    B["DB: paper.title / authors / filename"] --> P
    C["DB: paper.notes<br/>user notes"] --> P
    P["_paper_user_prompt()"] --> L
    S["PAPER_PAGE_SYSTEM<br/>fixed system prompt"] --> L
    L["chat.completions.create<br/>model=gpt-5.4"] --> M["markdown body"]
    M --> F["wrap with YAML frontmatter"]
    F --> O[("data/wiki/papers/{id}-{slug}.md")]
```

Concept pages (`data/wiki/concepts/{id}-{slug}.md`) follow a similar flow, but the input is a concept name plus the relevant snippets from every paper that touches it, and the LLM writes a cross-paper synthesis (consensus, disagreements, open questions) instead of rewriting a single paper:

```mermaid
flowchart TD
    K["DB: KnowledgeNode<br/>title / node_type / content / source_paper_ids"] --> P
    R["DB: Paper.raw_llm_response<br/>(per source_paper_id)"] --> SN["_snippet_for_paper()<br/>pick 8 high-signal fields"]
    SN --> P["_concept_user_prompt()"]
    SP["CONCEPT_PAGE_SYSTEM<br/>fixed system prompt"] --> L
    P --> L["chat.completions.create<br/>model=gpt-5.4, max_tokens=1500"]
    L --> M["markdown body<br/>with [[paper:{id}]] inline refs"]
    M --> F["wrap with YAML frontmatter"]
    F --> O[("data/wiki/concepts/{id}-{slug}.md")]
```

## Data Model

Knowra stores runtime data in SQLite and the local filesystem.

### Core Tables

- `papers`: one row per PDF, including path, title, authors, venue, year, page count, processing status, model response, parsed extraction result, notes, and chat state.
- `nodes`: graph entities generated from processed papers. Main node types include `paper`, `technique`, `dataset`, `problem_area`, and `finding`.
- `edges`: typed graph relationships between nodes.
- `config`: local application settings, including model choices, scan directory, similarity threshold, and cached assistant ID.
- `prompt`: the editable extraction prompt used by future processing runs.

### Graph Relationships

The graph uses typed edges to make paper knowledge navigable:

- `uses`: a paper or method uses a technique.
- `belongs_to`: a paper or concept belongs to a research area.
- `builds_on`: a method builds on another method.
- `trained_on`: a model or paper trains on a dataset.
- `evaluated_on`: a paper evaluates on a dataset.
- `compared_to`: a paper compares against a baseline.
- `finding`: a paper supports a key finding.
- `similar`: two nodes are connected by embedding similarity.

### Local Files

```text
data/
├── config.json              # local settings and API key; ignored by Git
├── knowledge.db             # SQLite database; ignored by Git
├── papers/                  # default PDF scan directory; ignored by Git
├── artifacts/
    ├── first_pages/         # rendered first-page previews
    └── note_images/         # pasted or dropped note images
└── paper_records/           # one markdown knowledge record per paper
```

## Project Layout

```text
.
├── backend/                 # FastAPI API, database models, and paper services
│   ├── routers/             # papers / graph / config / prompt / note image routes
│   ├── services/            # PDF, scanning, LLM extraction, graph building, cleanup
│   ├── config.py            # runtime config and model defaults
│   ├── database.py          # SQLite initialization and lightweight migrations
│   └── requirements.txt
├── frontend/                # React + Vite frontend
│   ├── src/api/             # API client and types
│   ├── src/components/      # graph, detail, and processing status components
│   └── src/pages/           # graph, papers, review, prompt, and settings pages
├── data/                    # local runtime data; repo keeps only placeholders
├── docs/                    # architecture, API, and development docs
├── INSTALL.md               # install and quick start guide
└── start.sh                 # one-command launcher
```

## Operations

- **Rebuild similarity edges**: run Rebuild Similarity Edges in Settings. This does not call the extraction model again.
- **Reset graph**: run Reset Graph in Settings. This clears generated nodes and edges and marks papers as unprocessed.
- **Edit extraction prompt**: use the Prompt page; future processing runs use the updated prompt.
- **Repair a response**: use the Paper Review page to edit the model response, then save and reparse it.
- **Reprocess a paper**: use the per-paper reprocess action; the app asks for confirmation before starting.
- **Clear cached assistant**: set `openai_assistant_id` to an empty value through the config API; the next run creates a new assistant.

## Privacy

Knowra is designed for local personal research workflows. By default, the repository ignores:

- `data/config.json`
- `data/knowledge.db`
- `data/papers/*`
- `data/artifacts/*`
- `data/paper_records/*`
- `backend/.venv`
- `frontend/node_modules`
- `frontend/dist`

Model processing sends paper content to the configured OpenAI API. Keep private or licensed papers out of shared repositories, and share only sanitized sample data when needed.

## Docs

- [Install](INSTALL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Development](docs/DEVELOPMENT.md)
