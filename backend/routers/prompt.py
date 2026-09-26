from fastapi import APIRouter
from pydantic import BaseModel
from config import load_config
from presentation_preferences import (
    Locale, default_prompt, get_prompt as localized_prompt, save_prompt,
    read_preferences, set_language,
)

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


class PromptUpdate(BaseModel):
    extraction_prompt: str


class LanguageUpdate(BaseModel):
    locale: Locale


@router.get("/preferences")
def get_preferences():
    return {"locale": read_preferences()["locale"]}


@router.put("/preferences")
def update_preferences(body: LanguageUpdate):
    set_language(body.locale)
    return {"locale": body.locale}


@router.get("")
def get_prompt(locale: Locale = "zh"):
    cfg = load_config(resolve_presentation=False)
    return {
        "extraction_prompt": localized_prompt("extraction", locale, cfg),
        "default_prompt": default_prompt("extraction", locale),
    }


@router.post("")
def update_prompt(body: PromptUpdate, locale: Locale = "zh"):
    save_prompt("extraction", locale, body.extraction_prompt)
    return {"message": "Prompt saved", "length": len(body.extraction_prompt)}


@router.post("/reset")
def reset_prompt(locale: Locale = "zh"):
    text = default_prompt("extraction", locale)
    save_prompt("extraction", locale, text)
    return {"message": "Prompt reset to default", "extraction_prompt": text}
