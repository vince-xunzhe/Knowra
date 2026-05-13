from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from config import (
    AVAILABLE_EMBEDDING_MODELS,
    AVAILABLE_MODELS,
    MODEL_GATEWAY_MODEL_OPTIONS,
    AVAILABLE_WIKI_COMPILE_MODELS,
    load_config,
    save_config,
)
from path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from model_gateway import (
    ensure_model_gateway_config,
    mask_model_gateway_config,
    run_provider_healthcheck,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    scan_directory: Optional[str] = None
    vlm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    wiki_compile_model: Optional[str] = None
    model_gateway: Optional[dict] = None
    similarity_threshold: Optional[float] = None
    use_first_page_image: Optional[bool] = None
    # Send "" to clear the cached assistant and force a rebuild on next run.
    openai_assistant_id: Optional[str] = None


class ProviderTestRequest(BaseModel):
    model_gateway: Optional[dict] = None


def _sanitize_masked_provider_keys(gateway: Optional[dict]) -> None:
    if not isinstance(gateway, dict):
        return
    providers = gateway.get("providers")
    if not isinstance(providers, list):
        return
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        api_key = provider.get("api_key")
        if isinstance(api_key, str) and ("..." in api_key or api_key == "****"):
            provider.pop("api_key", None)


@router.get("")
def get_config():
    cfg = load_config()
    # Don't expose the full extraction_prompt here (use /api/prompt)
    cfg.pop("extraction_prompt", None)
    if cfg.get("openai_api_key"):
        key = cfg["openai_api_key"]
        cfg["openai_api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    cfg = mask_model_gateway_config(cfg)
    cfg["available_models"] = AVAILABLE_MODELS
    cfg["available_embedding_models"] = AVAILABLE_EMBEDDING_MODELS
    cfg["available_wiki_compile_models"] = AVAILABLE_WIKI_COMPILE_MODELS
    cfg["available_model_gateway_models"] = MODEL_GATEWAY_MODEL_OPTIONS
    return cfg


@router.post("")
def update_config(updates: ConfigUpdate):
    data = {k: v for k, v in updates.model_dump().items() if v is not None}
    _sanitize_masked_provider_keys(data.get("model_gateway"))
    result = save_config(data)
    result.pop("extraction_prompt", None)
    if result.get("openai_api_key"):
        key = result["openai_api_key"]
        result["openai_api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    result = mask_model_gateway_config(result)
    result["available_models"] = AVAILABLE_MODELS
    result["available_embedding_models"] = AVAILABLE_EMBEDDING_MODELS
    result["available_wiki_compile_models"] = AVAILABLE_WIKI_COMPILE_MODELS
    result["available_model_gateway_models"] = MODEL_GATEWAY_MODEL_OPTIONS
    return result


@router.post("/model_gateway/providers/{provider_id}/test")
def test_provider(provider_id: str, payload: Optional[ProviderTestRequest] = None):
    cfg = load_config()
    if payload and payload.model_gateway is not None:
        gateway = payload.model_gateway
        _sanitize_masked_provider_keys(gateway)
        cfg["model_gateway"] = gateway
        cfg = ensure_model_gateway_config(cfg)
    try:
        result = run_provider_healthcheck(cfg, provider_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    save_config({"model_gateway": cfg.get("model_gateway")})
    fresh = load_config()
    fresh.pop("extraction_prompt", None)
    if fresh.get("openai_api_key"):
        key = fresh["openai_api_key"]
        fresh["openai_api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    fresh = mask_model_gateway_config(fresh)
    fresh["available_models"] = AVAILABLE_MODELS
    fresh["available_embedding_models"] = AVAILABLE_EMBEDDING_MODELS
    fresh["available_wiki_compile_models"] = AVAILABLE_WIKI_COMPILE_MODELS
    fresh["available_model_gateway_models"] = MODEL_GATEWAY_MODEL_OPTIONS
    return {
        "result": result,
        "config": fresh,
    }
