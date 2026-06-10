from __future__ import annotations

from typing import Any, Optional


PAPER_CATEGORY_LLM = "LLM"
PAPER_CATEGORY_VLM = "VLM"
PAPER_CATEGORY_VLA = "VLA"
PAPER_CATEGORY_STATIC_3D = "三维重建-静态"
PAPER_CATEGORY_DYNAMIC_3D = "三维重建-动态"
PAPER_CATEGORY_WORLD_MODEL = "世界模型"
PAPER_CATEGORY_OTHER = "其他"

PAPER_CATEGORY_OPTIONS = [
    PAPER_CATEGORY_LLM,
    PAPER_CATEGORY_VLM,
    PAPER_CATEGORY_VLA,
    PAPER_CATEGORY_STATIC_3D,
    PAPER_CATEGORY_DYNAMIC_3D,
    PAPER_CATEGORY_WORLD_MODEL,
    PAPER_CATEGORY_OTHER,
]

# The built-in defaults stay fixed. The *active* list is user-editable and
# persisted in config under "paper_categories"; it seeds from these defaults.
DEFAULT_PAPER_CATEGORY_OPTIONS = list(PAPER_CATEGORY_OPTIONS)

_active_cache: Optional[list] = None


def _clean_category_list(raw: Any) -> list:
    out: list = []
    seen: set = set()
    for item in raw or []:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    # "其他" is the structural fallback — always present, always last.
    out = [c for c in out if c != PAPER_CATEGORY_OTHER]
    out.append(PAPER_CATEGORY_OTHER)
    return out


def get_active_categories() -> list:
    """Active, user-editable category list (config-backed, cached).

    Falls back to the built-in defaults when nothing custom is stored.
    Call refresh_active_categories() after mutating config."""
    global _active_cache
    if _active_cache is not None:
        return _active_cache
    opts = None
    try:
        from config import load_config

        raw = load_config().get("paper_categories")
        if isinstance(raw, list) and raw:
            opts = _clean_category_list(raw)
    except Exception:
        opts = None
    if not opts:
        opts = _clean_category_list(DEFAULT_PAPER_CATEGORY_OPTIONS)
    _active_cache = opts
    return opts


def refresh_active_categories() -> None:
    global _active_cache
    _active_cache = None


def set_active_categories(categories: list) -> list:
    """Persist a new active category list to config; returns the cleaned list."""
    cleaned = _clean_category_list(categories)
    from config import save_config

    save_config({"paper_categories": cleaned})
    refresh_active_categories()
    return get_active_categories()


# Synonym table for the model's OWN category label — e.g. the LLM may write
# "Vision-Language-Action" or "自动驾驶" where we want "VLA". This only
# normalises the model's chosen *label*; it does NOT scan paper text for
# keywords to guess a category.
_CATEGORY_ALIASES = {
    "llm": PAPER_CATEGORY_LLM,
    "largelanguagemodel": PAPER_CATEGORY_LLM,
    "languagemodel": PAPER_CATEGORY_LLM,
    "大语言模型": PAPER_CATEGORY_LLM,
    "语言模型": PAPER_CATEGORY_LLM,
    "nlp": PAPER_CATEGORY_LLM,
    "vlm": PAPER_CATEGORY_VLM,
    "visionlanguagemodel": PAPER_CATEGORY_VLM,
    "visionlanguage": PAPER_CATEGORY_VLM,
    "multimodal": PAPER_CATEGORY_VLM,
    "多模态": PAPER_CATEGORY_VLM,
    "多模态vlm": PAPER_CATEGORY_VLM,
    "vla": PAPER_CATEGORY_VLA,
    "visionlanguageaction": PAPER_CATEGORY_VLA,
    "自动驾驶": PAPER_CATEGORY_VLA,
    "自动驾驶vla": PAPER_CATEGORY_VLA,
    "三维重建静态": PAPER_CATEGORY_STATIC_3D,
    "三维重建-静态": PAPER_CATEGORY_STATIC_3D,
    "3d重建静态": PAPER_CATEGORY_STATIC_3D,
    "3dreconstructionstatic": PAPER_CATEGORY_STATIC_3D,
    "static3dreconstruction": PAPER_CATEGORY_STATIC_3D,
    "三维重建动态": PAPER_CATEGORY_DYNAMIC_3D,
    "三维重建-动态": PAPER_CATEGORY_DYNAMIC_3D,
    "动态三维重建": PAPER_CATEGORY_DYNAMIC_3D,
    "3d重建动态": PAPER_CATEGORY_DYNAMIC_3D,
    "dynamic3dreconstruction": PAPER_CATEGORY_DYNAMIC_3D,
    "世界模型": PAPER_CATEGORY_WORLD_MODEL,
    "worldmodel": PAPER_CATEGORY_WORLD_MODEL,
    "其他": PAPER_CATEGORY_OTHER,
    "other": PAPER_CATEGORY_OTHER,
}


def _category_key(value: Any) -> str:
    import re

    return re.sub(r"[\s_/\-]+", "", str(value or "").strip().lower())


def normalize_paper_category(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    active = get_active_categories()
    # Exact match against the active list — this is what lets user-added
    # custom categories validate (they have no alias entry).
    if raw in active:
        return raw
    # Built-in alias mapping, but only resolve to a category still active
    # (so a removed default doesn't sneak back in via an alias).
    aliased = _CATEGORY_ALIASES.get(_category_key(raw))
    if aliased and aliased in active:
        return aliased
    return None


def derive_model_paper_category(paper: Any, extraction: Optional[dict[str, Any]]) -> Optional[str]:
    """The model's category = the LLM's own ``paper_category`` field, normalised
    to a currently-active category.

    There are deliberately NO keyword / text-scanning heuristics: the category
    comes from the model's semantic judgement (or the user's manual override).
    If the model didn't provide a usable category we leave it unset, and the
    effective category falls back to 其他."""
    if not isinstance(extraction, dict):
        return None
    return normalize_paper_category(extraction.get("paper_category"))


def effective_paper_category(paper: Any, extraction: Optional[dict[str, Any]] = None) -> str:
    override = normalize_paper_category(getattr(paper, "paper_category_override", None))
    if override:
        return override
    model_value = normalize_paper_category(getattr(paper, "paper_category_model", None))
    if model_value:
        return model_value
    derived = derive_model_paper_category(paper, extraction)
    return derived or PAPER_CATEGORY_OTHER


def sync_paper_category_fields(
    paper: Any,
    extraction: Optional[dict[str, Any]] = None,
    overwrite_model: bool = False,
) -> bool:
    changed = False

    normalized_override = normalize_paper_category(getattr(paper, "paper_category_override", None))
    if getattr(paper, "paper_category_override", None) != normalized_override:
        paper.paper_category_override = normalized_override
        changed = True

    derived_model = derive_model_paper_category(paper, extraction)
    current_model = normalize_paper_category(getattr(paper, "paper_category_model", None))
    next_model = current_model
    if overwrite_model:
        next_model = derived_model
    elif current_model is None and derived_model is not None:
        next_model = derived_model

    if getattr(paper, "paper_category_model", None) != next_model:
        paper.paper_category_model = next_model
        changed = True

    return changed


def paper_category_source(paper: Any) -> str:
    if normalize_paper_category(getattr(paper, "paper_category_override", None)):
        return "manual"
    if normalize_paper_category(getattr(paper, "paper_category_model", None)):
        return "model"
    return "none"
