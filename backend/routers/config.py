from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from config import load_config, save_config, AVAILABLE_MODELS

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    scan_directory: Optional[str] = None
    vlm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    similarity_threshold: Optional[float] = None
    use_first_page_image: Optional[bool] = None
    # Send "" to clear the cached assistant and force a rebuild on next run.
    openai_assistant_id: Optional[str] = None


@router.get("")
def get_config():
    cfg = load_config()
    # Don't expose the full extraction_prompt here (use /api/prompt)
    cfg.pop("extraction_prompt", None)
    if cfg.get("openai_api_key"):
        key = cfg["openai_api_key"]
        cfg["openai_api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    cfg["available_models"] = AVAILABLE_MODELS
    return cfg


@router.post("")
def update_config(updates: ConfigUpdate):
    data = {k: v for k, v in updates.model_dump().items() if v is not None}
    result = save_config(data)
    result.pop("extraction_prompt", None)
    if result.get("openai_api_key"):
        key = result["openai_api_key"]
        result["openai_api_key"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "****"
    return result
