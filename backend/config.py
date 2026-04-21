import os
import json
from pathlib import Path
from prompts import DEFAULT_PAPER_PROMPT

CONFIG_FILE = Path(__file__).parent.parent / "data" / "config.json"
DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"
PAPERS_DIR = Path(__file__).parent.parent / "data" / "papers"

# Exposed OpenAI models for the frontend dropdown.
# Only file_search-compatible models. Reasoning models (o1/o3) removed because
# the Assistants API + file_search pipeline does not support them.
AVAILABLE_MODELS = [
    {"id": "gpt-4o", "label": "GPT-4o", "supports_vision": True, "desc": "推荐，视觉+文本"},
    {"id": "gpt-4o-mini", "label": "GPT-4o-mini", "supports_vision": True, "desc": "快，便宜"},
    {"id": "gpt-4-turbo", "label": "GPT-4 Turbo", "supports_vision": True, "desc": "长上下文"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "supports_vision": True, "desc": "最新主力"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1-mini", "supports_vision": True, "desc": "性价比"},
]


def load_config() -> dict:
    defaults = {
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "scan_directory": str(PAPERS_DIR),
        "vlm_model": "gpt-4o",
        "embedding_model": "text-embedding-3-small",
        "similarity_threshold": 0.6,
        "use_first_page_image": True,   # kept for backwards-compat; ignored by the Assistants pipeline
        "extraction_prompt": DEFAULT_PAPER_PROMPT,
        "openai_assistant_id": None,    # cached Assistants API assistant id, reused across runs
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_config(updates: dict) -> dict:
    current = load_config()
    current.update(updates)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current
