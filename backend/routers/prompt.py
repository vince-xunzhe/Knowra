from fastapi import APIRouter
from pydantic import BaseModel
from config import load_config, save_config
from prompts import DEFAULT_PAPER_PROMPT

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


class PromptUpdate(BaseModel):
    extraction_prompt: str


@router.get("")
def get_prompt():
    cfg = load_config()
    return {
        "extraction_prompt": cfg.get("extraction_prompt", DEFAULT_PAPER_PROMPT),
        "default_prompt": DEFAULT_PAPER_PROMPT,
    }


@router.post("")
def update_prompt(body: PromptUpdate):
    save_config({"extraction_prompt": body.extraction_prompt})
    return {"message": "Prompt saved", "length": len(body.extraction_prompt)}


@router.post("/reset")
def reset_prompt():
    save_config({"extraction_prompt": DEFAULT_PAPER_PROMPT})
    return {"message": "Prompt reset to default", "extraction_prompt": DEFAULT_PAPER_PROMPT}
