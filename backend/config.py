import os
import json
from pathlib import Path
from prompts import DEFAULT_PAPER_PROMPT, DEFAULT_PROMOTION_PROMPT
from path_utils import PAPERS_DIR, portable_data_path, resolve_papers_directory
from path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from model_gateway import (
    BUILTIN_MODEL_REGISTRY,
    DEFAULT_PROVIDER_REGISTRY,
    TASK_BINDING_DEFAULTS,
    ensure_model_gateway_config,
    get_bound_model_id,
    get_task_binding,
)

CONFIG_FILE = Path(__file__).parent.parent / "data" / "config.json"
DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"

# ── Deploy mode ─────────────────────────────────────────────────────────
# Controls whether this FastAPI process runs as:
#   "local"  → desktop sidecar mode: SQLite, no auth, single user, Codex CLI on
#   "cloud"  → multi-tenant cloud mode: Postgres (Supabase), JWT auth, no Codex
# See docs/ARCHITECTURE-CLOUD.md §4.3.
#
# Default is "local" so an unset env var keeps current desktop behavior.
DEPLOY_MODE = os.environ.get("KNOWRA_DEPLOY_MODE", "local").lower().strip()
if DEPLOY_MODE not in {"local", "cloud"}:
    raise RuntimeError(
        f"KNOWRA_DEPLOY_MODE must be 'local' or 'cloud', got: {DEPLOY_MODE!r}"
    )


def is_cloud_mode() -> bool:
    """Convenience predicate used by routers that gate behavior on deploy mode."""
    return DEPLOY_MODE == "cloud"

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

MODEL_GATEWAY_MODEL_OPTIONS = [
    {
        "id": model["id"],
        "label": model["label"],
        "desc": ", ".join(model.get("supported_tasks") or []),
        "supports_vision": bool(model.get("supports_vision", False)),
        "provider_id": model.get("provider_id"),
        "upstream_model": model.get("upstream_model"),
        "model_kind": model.get("model_kind"),
        "supported_tasks": list(model.get("supported_tasks") or []),
        "builtin": bool(model.get("builtin", False)),
    }
    for model in BUILTIN_MODEL_REGISTRY
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
        # Extraction chain resilience knobs.
        "paper_process_max_retries": 3,
        "paper_process_backoff_base_seconds": 1.5,
        "paper_process_backoff_max_seconds": 20.0,
        "extraction_prompt": DEFAULT_PAPER_PROMPT,
        # User-editable system prompt for the concept-promotion LLM stage.
        # Default is the built-in template so first-time users get the
        # heuristic + Agent pipeline out of the box. Setting it to ""
        # via the editor is the explicit "skip Agent, only run heuristic"
        # opt-out — preserved for back-compat and power users.
        "promotion_prompt": DEFAULT_PROMOTION_PROMPT,
        "openai_assistant_id": None,    # cached Assistants API assistant id, reused across runs
        "model_gateway": {
            "providers": DEFAULT_PROVIDER_REGISTRY,
            "models": BUILTIN_MODEL_REGISTRY,
            "task_bindings": TASK_BINDING_DEFAULTS,
        },
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
    defaults = ensure_model_gateway_config(defaults)
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
    current = ensure_model_gateway_config(current)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current


def task_model_id(cfg: dict, task_id: str) -> str:
    return get_bound_model_id(cfg, task_id)


def task_model_name(cfg: dict, task_id: str) -> str:
    model_id = get_bound_model_id(cfg, task_id)
    if model_id.startswith("openai/"):
        return model_id.split("/", 1)[1]
    return model_id


def task_reasoning_effort(cfg: dict, task_id: str) -> str:
    return get_task_binding(cfg, task_id).get("reasoning_effort") or "medium"
