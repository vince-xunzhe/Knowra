# AGENTS.md

## Project Overview

Describe the target repository in one or two sentences.

## Do Not Touch

- `.env*`
- credentials or auth files
- raw datasets under `data/raw/`
- production deployment config unless the issue explicitly asks for it

## Canonical Commands

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Unit Tests

```bash
pytest -q
```

### Lint / Typecheck

```bash
ruff check .
python -m mypy .
```

### Evaluation

```bash
bash evals/run_eval.sh --split val --output artifacts/latest_eval
python evals/summarize_metrics.py artifacts/latest_eval
```

## ML / Eval Rules

- Record baseline and new metrics.
- Do not change validation splits unless the issue explicitly asks.
- Do not optimize for a single example only.
- Keep structured outputs valid.
- Prefer small, documented hypotheses and ablations.

## PR Requirements

Each PR should include:

- Problem
- Approach
- Files changed
- Commands run
- Results or metrics
- Risks
- Follow-up ideas

## Branch Naming

Use `agent/<LinearIssueIdentifier>`, for example:

```text
agent/SL-123
```

## Commit Style

Use small commits. Commit message format:

```text
<ISSUE_KEY>: short summary
```

## Human Handoff

Do not merge. Open a pull request and move the Linear issue to Human Review.
