from __future__ import annotations

import re
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

_LEGACY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        PAPER_CATEGORY_WORLD_MODEL,
        (
            "world model",
            "世界模型",
            "jepa",
            "latent world",
            "predictive world",
            "dynamics model",
            "imagination",
        ),
    ),
    (
        PAPER_CATEGORY_DYNAMIC_3D,
        (
            "dynamic 3d",
            "dynamic reconstruction",
            "animatable",
            "avatar",
            "avatars",
            "blendshape",
            "head avatar",
            "facial",
            "human avatar",
            "motion",
            "video avatar",
            "4d",
            "dynamic scene",
        ),
    ),
    (
        PAPER_CATEGORY_STATIC_3D,
        (
            "3d reconstruction",
            "三维重建",
            "structure from motion",
            "sfm",
            "multi-view stereo",
            "mvs",
            "stereo",
            "geometry",
            "gaussian",
            "depth",
            "point cloud",
            "view synthesis",
            "mast3r",
            "dust3r",
            "vggt",
        ),
    ),
    (
        PAPER_CATEGORY_VLA,
        (
            "vision-language-action",
            "vla",
            "autonomous driving",
            "自动驾驶",
            "driving",
            "trajectory",
            "planning",
            "closed-loop",
            "bev planner",
            "drive",
        ),
    ),
    (
        PAPER_CATEGORY_VLM,
        (
            "vision-language model",
            "vision-language",
            "vlm",
            "mllm",
            "multimodal",
            "多模态",
            "grounding",
            "llava",
            "qwen-vl",
            "gemini",
            "open vocabulary",
            "video understanding",
        ),
    ),
    (
        PAPER_CATEGORY_LLM,
        (
            "large language model",
            "language model",
            "llm",
            "instruction tuning",
            "reasoning",
            "agentic",
            "nlp",
            "text generation",
        ),
    ),
]

_CATEGORY_PRIORITY = {
    PAPER_CATEGORY_WORLD_MODEL: 6,
    PAPER_CATEGORY_DYNAMIC_3D: 5,
    PAPER_CATEGORY_STATIC_3D: 4,
    PAPER_CATEGORY_VLA: 3,
    PAPER_CATEGORY_VLM: 2,
    PAPER_CATEGORY_LLM: 1,
    PAPER_CATEGORY_OTHER: 0,
}


def _category_key(value: Any) -> str:
    return re.sub(r"[\s_/\-]+", "", str(value or "").strip().lower())


def normalize_paper_category(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in PAPER_CATEGORY_OPTIONS:
        return raw
    return _CATEGORY_ALIASES.get(_category_key(raw))


def _technique_names(extraction: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in extraction.get("techniques") or []:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip()
            if name:
                names.append(name)
        elif isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
    return names


def paper_category_text(paper: Any, extraction: dict[str, Any]) -> str:
    bits: list[str] = [
        getattr(paper, "title", None) or getattr(paper, "filename", None) or "",
        extraction.get("paper_category") or "",
        extraction.get("problem_area") or "",
        extraction.get("tech_stack_position") or "",
        " ".join(extraction.get("keywords") or []),
        " ".join(extraction.get("baselines") or []),
        " ".join(_technique_names(extraction)),
        extraction.get("abstract_summary") or "",
        extraction.get("core_contribution") or "",
    ]
    return "\n".join(str(bit) for bit in bits if bit).lower()


def legacy_classify_paper_category(paper: Any, extraction: dict[str, Any]) -> str:
    text = paper_category_text(paper, extraction)
    if not text.strip():
        return PAPER_CATEGORY_OTHER

    best = PAPER_CATEGORY_OTHER
    best_score = 0
    for category, rules in _LEGACY_RULES:
        score = sum(1 for rule in rules if rule.lower() in text)
        if score > best_score:
            best = category
            best_score = score
        elif score == best_score and score > 0:
            if _CATEGORY_PRIORITY[category] > _CATEGORY_PRIORITY[best]:
                best = category
    return best


def derive_model_paper_category(paper: Any, extraction: Optional[dict[str, Any]]) -> Optional[str]:
    if not isinstance(extraction, dict):
        return None
    explicit = normalize_paper_category(extraction.get("paper_category"))
    if explicit:
        return explicit
    return legacy_classify_paper_category(paper, extraction)


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
