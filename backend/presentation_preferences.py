"""Presentation preferences and language-scoped prompt overrides.

Kept apart from model/provider configuration. Selecting a language never rewrites
legacy prompts or existing papers, and resets affect only the requested locale.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal

from prompts import DEFAULT_PAPER_PROMPT, DEFAULT_PROMOTION_PROMPT

Locale = Literal["zh", "ja", "es", "en"]
LOCALES = ("zh", "ja", "es", "en")
PREFERENCES_FILE = Path(__file__).resolve().parents[1] / "data" / "presentation_preferences.json"
TEMPLATES_DIR = Path(__file__).parent / "prompt_locales"
_LOCK = RLock()


def read_preferences() -> dict:
    with _LOCK:
        try:
            data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"locale": data.get("locale") if data.get("locale") in LOCALES else "zh",
                        "prompts": data.get("prompts", {}) if isinstance(data.get("prompts"), dict) else {}}
        except (OSError, ValueError):
            pass
        return {"locale": "zh", "prompts": {}}


def _write(data: dict) -> None:
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREFERENCES_FILE.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, PREFERENCES_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def set_language(locale: Locale) -> None:
    if locale not in LOCALES:
        raise ValueError("Unsupported locale")
    with _LOCK:
        data = read_preferences()
        data["locale"] = locale
        _write(data)


def default_prompt(kind: str, locale: str) -> str:
    if kind not in ("extraction", "promotion") or locale not in LOCALES:
        raise ValueError("Unsupported prompt or locale")
    if locale == "zh":
        return DEFAULT_PAPER_PROMPT if kind == "extraction" else DEFAULT_PROMOTION_PROMPT
    return (TEMPLATES_DIR / locale / f"{kind}.md").read_text(encoding="utf-8").strip()


def get_prompt(kind: str, locale: str, legacy_config: dict, preferences: dict | None = None) -> str:
    data = preferences if preferences is not None else read_preferences()
    default = default_prompt(kind, locale)
    bank = data["prompts"].get(locale, {})
    if isinstance(bank, dict) and isinstance(bank.get(kind), str):
        return bank[kind] if bank[kind] or kind == "promotion" else default  # Empty promotion disables the agent.
    if locale == "zh":
        return legacy_config.get(f"{kind}_prompt", default)
    return default


def save_prompt(kind: str, locale: str, text: str) -> None:
    default_prompt(kind, locale)  # Validate before any write.
    with _LOCK:
        data = read_preferences()
        if not isinstance(data["prompts"].get(locale), dict):
            data["prompts"][locale] = {}
        data["prompts"][locale][kind] = text
        _write(data)


def resolve_prompts(config: dict) -> dict:
    data = read_preferences()
    locale = data["locale"]
    return {**config, "prompt_locale": locale,
            "extraction_prompt": get_prompt("extraction", locale, config, data),
            "promotion_prompt": get_prompt("promotion", locale, config, data)}


def response_instructions(instructions: str, locale: str = "zh") -> str:
    """Localize presentation directives only, never user/source payloads.

    The Chinese path is byte-for-byte compatible. Existing tools, retrieval,
    model routing, parser contracts and task criteria are unchanged.
    """
    if locale not in ("en", "ja", "es"):
        return instructions
    language = {"en": "英文", "ja": "日语", "es": "西班牙语"}[locale]
    headings = {"en": "Sources", "ja": "引用元", "es": "Fuentes"}
    localized = instructions.replace("中文", language).replace("## 📚 引用来源", f"## 📚 {headings[locale]}")
    directive = (TEMPLATES_DIR / locale / "response.md").read_text(encoding="utf-8").strip()
    return f"{localized}\n\n{directive}"
