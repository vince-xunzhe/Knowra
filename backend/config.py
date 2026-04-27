import os
import json
from pathlib import Path
from prompts import DEFAULT_PAPER_PROMPT
from path_utils import PAPERS_DIR, portable_data_path, resolve_papers_directory

CONFIG_FILE = Path(__file__).parent.parent / "data" / "config.json"
DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"

# Exposed OpenAI models for the frontend dropdown.
# Only file_search-compatible models. Reasoning models (o1/o3) removed because
# the Assistants API + file_search pipeline does not support them.
AVAILABLE_MODELS = [
    {"id": "gpt-5.5", "label": "GPT-5.5", "supports_vision": True, "desc": "最强主力，复杂推理与专业工作"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "supports_vision": True, "desc": "高性能主力，适合深度论文抽取"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4-mini", "supports_vision": True, "desc": "更快更省，仍支持 file_search"},
    {"id": "gpt-4o", "label": "GPT-4o", "supports_vision": True, "desc": "推荐，视觉+文本"},
    {"id": "gpt-4o-mini", "label": "GPT-4o-mini", "supports_vision": True, "desc": "快，便宜"},
    {"id": "gpt-4-turbo", "label": "GPT-4 Turbo", "supports_vision": True, "desc": "长上下文"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "supports_vision": True, "desc": "最新主力"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1-mini", "supports_vision": True, "desc": "性价比"},
]

AVAILABLE_EMBEDDING_MODELS = [
    {"id": "text-embedding-3-large", "label": "text-embedding-3-large", "desc": "精度更高，适合更细的语义连接"},
    {"id": "text-embedding-3-small", "label": "text-embedding-3-small", "desc": "默认推荐，成本和效果更均衡"},
]

# Wiki compile is a pure summarization task — no file_search, no PDF —
# so any chat-completions OR responses-capable model works. Defaults below
# are sorted cheap → strong; gpt-4o-mini is the fast/cheap recommendation.
AVAILABLE_WIKI_COMPILE_MODELS = [
    {"id": "gpt-4o-mini", "label": "GPT-4o-mini", "desc": "默认推荐，便宜快速"},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1-mini", "desc": "性价比"},
    {"id": "gpt-4o", "label": "GPT-4o", "desc": "更稳，写作质量更高"},
    {"id": "gpt-4.1", "label": "GPT-4.1", "desc": "主力，更稳"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4-mini", "desc": "Responses API 通道"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "desc": "Responses API 通道，写作更细"},
    {"id": "gpt-5.5", "label": "GPT-5.5", "desc": "Responses API 通道，最强"},
]


def _upgrade_extraction_prompt(prompt: str) -> str:
    if not prompt:
        return DEFAULT_PAPER_PROMPT

    upgraded = prompt
    upgraded = upgraded.replace(
        '{"name": <string>, "plain": <string>}',
        '{"name": <string>, "formula": <string>, "plain": <string>}',
    )
    upgraded = upgraded.replace(
        '- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, plain: "白话解释这条公式在做什么"}；plain 不要粘 LaTeX',
        '- principle.key_formulas: **至少列 2-4 条**论文最关键的公式；每条 {name: "式(3) 自注意力" 之类, formula: "公式正文，可用 LaTeX 或论文里的标准写法", plain: "白话解释这条公式在做什么"}；`formula` 必须填写真正公式内容，不能为空，`plain` 不要粘 LaTeX',
    )
    upgraded = upgraded.replace(
        '[ ] principle.key_formulas 至少 2 条',
        '[ ] principle.key_formulas 至少 2 条，且每条都有非空 formula',
    )
    return upgraded


def load_config() -> dict:
    defaults = {
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "scan_directory": str(PAPERS_DIR),
        "vlm_model": "gpt-4o",
        "embedding_model": "text-embedding-3-small",
        # Used by services.wiki_compiler. Decoupled from vlm_model because
        # compile is plain summarization and doesn't need the strongest model.
        # Env var WIKI_COMPILE_MODEL overrides only at first-run / unset state;
        # once saved into config.json the user's choice wins.
        "wiki_compile_model": os.environ.get("WIKI_COMPILE_MODEL", "gpt-4o-mini"),
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
    defaults["extraction_prompt"] = _upgrade_extraction_prompt(
        defaults.get("extraction_prompt", DEFAULT_PAPER_PROMPT)
    )
    if defaults.get("scan_directory"):
        defaults["scan_directory"] = str(
            resolve_papers_directory(defaults["scan_directory"])
        )
    return defaults


def save_config(updates: dict) -> dict:
    if "scan_directory" in updates:
        updates = {
            **updates,
            "scan_directory": portable_data_path(updates["scan_directory"]),
        }
    current = load_config()
    current.update(updates)
    if "extraction_prompt" in current:
        current["extraction_prompt"] = _upgrade_extraction_prompt(current["extraction_prompt"])
    if current.get("scan_directory"):
        current["scan_directory"] = portable_data_path(current["scan_directory"])
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current
