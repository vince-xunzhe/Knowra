from __future__ import annotations

from copy import deepcopy
from typing import Any

from .catalog import (
    AVAILABLE_PROVIDER_TYPES,
    BUILTIN_MODEL_REGISTRY,
    DEFAULT_PROVIDER_REGISTRY,
    TASK_BINDING_DEFAULTS,
    TASK_SPECS,
)


LEGACY_TO_MODEL_ID = {
    "gpt-5.5": "openai/gpt-5.5",
    "gpt-5.4": "openai/gpt-5.4",
    "gpt-5.4-mini": "openai/gpt-5.4-mini",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4-turbo": "openai/gpt-4-turbo",
    "gpt-4.1": "openai/gpt-4.1",
    "gpt-4.1-mini": "openai/gpt-4.1-mini",
    "text-embedding-3-large": "openai/text-embedding-3-large",
    "text-embedding-3-small": "openai/text-embedding-3-small",
}

MODEL_ID_TO_LEGACY = {value: key for key, value in LEGACY_TO_MODEL_ID.items()}
TASK_IDS = {task["id"] for task in TASK_SPECS}
TASK_SPECS_BY_ID = {task["id"]: task for task in TASK_SPECS}
DEFAULT_REASONING_EFFORT = "medium"
VALID_REASONING_EFFORTS = {"low", "medium", "high"}


def _merge_by_id(defaults: list[dict[str, Any]], saved: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged = {item["id"]: deepcopy(item) for item in defaults if item.get("id")}
    for item in saved or []:
        item_id = item.get("id")
        if not item_id:
            continue
        if item_id in merged:
            merged[item_id].update({k: deepcopy(v) for k, v in item.items()})
        else:
            merged[item_id] = deepcopy(item)
    return list(merged.values())


def _merge_models_by_id(defaults: list[dict[str, Any]], saved: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged = {item["id"]: deepcopy(item) for item in defaults if item.get("id")}
    for item in saved or []:
        item_id = item.get("id")
        if not item_id:
            continue
        default_item = merged.get(item_id)
        if default_item and bool(default_item.get("builtin")):
            # Builtin model metadata is owned by code, not by the persisted
            # config file. This lets us ship support matrix changes (for
            # example newly supported tasks) without old config snapshots
            # pinning stale capabilities forever.
            continue
        if item_id in merged:
            merged[item_id].update({k: deepcopy(v) for k, v in item.items()})
        else:
            merged[item_id] = deepcopy(item)
    return list(merged.values())


def _normalize_supported_tasks(model: dict[str, Any]) -> list[str]:
    tasks = []
    seen: set[str] = set()
    for raw in model.get("supported_tasks") or []:
        task_id = str(raw or "").strip()
        if task_id and task_id in TASK_IDS and task_id not in seen:
            tasks.append(task_id)
            seen.add(task_id)
    return tasks


def _normalize_provider(provider: dict[str, Any]) -> dict[str, Any]:
    out = dict(provider)
    out["id"] = str(out.get("id") or "").strip()
    out["label"] = str(out.get("label") or out["id"]).strip() or out["id"]
    out["provider_type"] = str(out.get("provider_type") or "openai_compatible").strip()
    out["enabled"] = bool(out.get("enabled", True))
    out.setdefault("base_url", "")
    out.setdefault("api_key", "")
    out.setdefault("command", "codex")
    out.setdefault("healthcheck_model", "")
    out.setdefault("last_tested_at", None)
    out.setdefault("last_test_status", "never")
    out.setdefault("last_test_message", "")
    return out


def _normalize_model(model: dict[str, Any]) -> dict[str, Any]:
    out = dict(model)
    out["id"] = str(out.get("id") or "").strip()
    out["label"] = str(out.get("label") or out["id"]).strip() or out["id"]
    out["provider_id"] = str(out.get("provider_id") or "").strip()
    out["upstream_model"] = str(out.get("upstream_model") or out["id"]).strip() or out["id"]
    out["model_kind"] = str(out.get("model_kind") or "chat").strip()
    out["supports_vision"] = bool(out.get("supports_vision", False))
    out["supported_tasks"] = _normalize_supported_tasks(out)
    out["builtin"] = bool(out.get("builtin", False))
    return out


def _normalize_reasoning_effort(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in VALID_REASONING_EFFORTS:
        return text
    return DEFAULT_REASONING_EFFORT


def _normalize_task_binding(raw: Any, default_model_id: str) -> dict[str, str]:
    if isinstance(raw, dict):
        model_id = str(raw.get("model_id") or raw.get("model") or "").strip()
        if model_id:
            model_id = LEGACY_TO_MODEL_ID.get(model_id, model_id)
        return {
            "model_id": model_id or default_model_id,
            "reasoning_effort": _normalize_reasoning_effort(raw.get("reasoning_effort")),
        }

    text = str(raw or "").strip()
    if text:
        text = LEGACY_TO_MODEL_ID.get(text, text)
    return {
        "model_id": text or default_model_id,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
    }


def _legacy_binding_defaults(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    bindings = {
        task_id: {
            "model_id": model_id,
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
        }
        for task_id, model_id in TASK_BINDING_DEFAULTS.items()
    }
    legacy_vlm = LEGACY_TO_MODEL_ID.get(str(cfg.get("vlm_model") or "").strip())
    legacy_embed = LEGACY_TO_MODEL_ID.get(str(cfg.get("embedding_model") or "").strip())
    legacy_wiki = LEGACY_TO_MODEL_ID.get(str(cfg.get("wiki_compile_model") or "").strip())
    if legacy_vlm:
        bindings["paper_extract"]["model_id"] = legacy_vlm
        bindings["paper_chat"]["model_id"] = legacy_vlm
    if legacy_embed:
        bindings["embedding"]["model_id"] = legacy_embed
    if legacy_wiki:
        bindings["wiki_compile"]["model_id"] = legacy_wiki
        bindings["ask_agent"]["model_id"] = legacy_wiki
        bindings["ask_synthesis"]["model_id"] = legacy_wiki
        bindings["promotion_judge"]["model_id"] = legacy_wiki
    return bindings


def ensure_model_gateway_config(cfg: dict[str, Any]) -> dict[str, Any]:
    gateway = deepcopy(cfg.get("model_gateway") or {})
    providers = _merge_by_id(DEFAULT_PROVIDER_REGISTRY, gateway.get("providers"))
    providers = [_normalize_provider(item) for item in providers if item.get("id")]
    provider_ids = {provider["id"] for provider in providers}

    models = _merge_models_by_id(BUILTIN_MODEL_REGISTRY, gateway.get("models"))
    models = [
        _normalize_model(item)
        for item in models
        if item.get("id") and item.get("provider_id") in provider_ids
    ]

    bindings = _legacy_binding_defaults(cfg)
    raw_bindings = gateway.get("task_bindings") or {}
    for task_id in TASK_IDS:
        bindings[task_id] = _normalize_task_binding(
            raw_bindings.get(task_id),
            bindings[task_id]["model_id"],
        )

    gateway = {
        "providers": providers,
        "models": models,
        "task_bindings": bindings,
        "task_specs": deepcopy(TASK_SPECS),
        "available_provider_types": deepcopy(AVAILABLE_PROVIDER_TYPES),
    }

    openai_provider = next((provider for provider in providers if provider["id"] == "openai"), None)
    if openai_provider is not None:
        if cfg.get("openai_api_key") and not openai_provider.get("api_key"):
            openai_provider["api_key"] = str(cfg["openai_api_key"])
        if openai_provider.get("api_key") and cfg.get("openai_api_key") != openai_provider.get("api_key"):
            cfg["openai_api_key"] = openai_provider["api_key"]

    cfg["model_gateway"] = gateway
    sync_legacy_fields(cfg)
    return cfg


def sync_legacy_fields(cfg: dict[str, Any]) -> dict[str, Any]:
    gateway = cfg.get("model_gateway") or {}
    bindings = gateway.get("task_bindings") or {}
    paper_extract_binding = _normalize_task_binding(bindings.get("paper_extract"), TASK_BINDING_DEFAULTS["paper_extract"])
    embedding_binding = _normalize_task_binding(bindings.get("embedding"), TASK_BINDING_DEFAULTS["embedding"])
    wiki_binding = _normalize_task_binding(bindings.get("wiki_compile"), TASK_BINDING_DEFAULTS["wiki_compile"])
    cfg["vlm_model"] = MODEL_ID_TO_LEGACY.get(
        paper_extract_binding["model_id"],
        cfg.get("vlm_model", "gpt-4o"),
    )
    cfg["embedding_model"] = MODEL_ID_TO_LEGACY.get(
        embedding_binding["model_id"],
        cfg.get("embedding_model", "text-embedding-3-small"),
    )
    cfg["wiki_compile_model"] = MODEL_ID_TO_LEGACY.get(
        wiki_binding["model_id"],
        cfg.get("wiki_compile_model", "gpt-4o-mini"),
    )
    openai_provider = get_provider_entry(cfg, "openai")
    if openai_provider and openai_provider.get("api_key"):
        cfg["openai_api_key"] = openai_provider["api_key"]
    return cfg


def get_provider_entry(cfg: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    gateway = cfg.get("model_gateway") or {}
    for provider in gateway.get("providers") or []:
        if provider.get("id") == provider_id:
            return provider
    return None


def get_model_entry(cfg: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    gateway = cfg.get("model_gateway") or {}
    normalized = LEGACY_TO_MODEL_ID.get(model_id, model_id)
    for model in gateway.get("models") or []:
        if model.get("id") == normalized:
            return model
    if normalized != model_id:
        for model in gateway.get("models") or []:
            if model.get("id") == model_id:
                return model
    return None


def get_bound_model_id(cfg: dict[str, Any], task_id: str) -> str:
    gateway = cfg.get("model_gateway") or {}
    bindings = gateway.get("task_bindings") or {}
    fallback = TASK_BINDING_DEFAULTS.get(task_id, "")
    return _normalize_task_binding(bindings.get(task_id), fallback)["model_id"]


def get_task_binding(cfg: dict[str, Any], task_id: str) -> dict[str, str]:
    gateway = cfg.get("model_gateway") or {}
    bindings = gateway.get("task_bindings") or {}
    fallback = TASK_BINDING_DEFAULTS.get(task_id, "")
    return _normalize_task_binding(bindings.get(task_id), fallback)


def mask_model_gateway_config(cfg: dict[str, Any]) -> dict[str, Any]:
    gateway = deepcopy(cfg.get("model_gateway") or {})
    for provider in gateway.get("providers") or []:
        api_key = str(provider.get("api_key") or "")
        if api_key:
            provider["api_key"] = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "****"
    cfg["model_gateway"] = gateway
    return cfg
