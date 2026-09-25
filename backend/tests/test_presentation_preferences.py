"""Language isolation, legacy compatibility, and unchanged output contracts."""
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import presentation_preferences as preferences
from prompts import DEFAULT_PAPER_PROMPT
from routers import prompt


@pytest.fixture
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PREFERENCES_FILE", tmp_path / "presentation.json")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path


def test_legacy_prompts_and_all_language_overrides_are_independent(isolated_preferences):
    legacy = {"extraction_prompt": "legacy custom extraction", "promotion_prompt": "legacy promotion"}
    for locale in preferences.LOCALES:
        preferences.save_prompt("extraction", locale, f"custom {locale}")
        preferences.save_prompt("promotion", locale, f"curation {locale}")
    for locale in preferences.LOCALES:
        preferences.set_language(locale)
        resolved = preferences.resolve_prompts({**legacy, "similarity_threshold": 0.61})
        assert resolved["extraction_prompt"] == f"custom {locale}"
        assert resolved["promotion_prompt"] == f"curation {locale}"
        assert resolved["similarity_threshold"] == 0.61
    preferences.save_prompt("extraction", "ja", preferences.default_prompt("extraction", "ja"))
    assert preferences.get_prompt("extraction", "en", legacy) == "custom en"
    assert preferences.get_prompt("extraction", "zh", legacy) == "custom zh"
    assert legacy["extraction_prompt"] == "legacy custom extraction"


def test_language_selection_does_not_rewrite_legacy_config(isolated_preferences):
    config.save_config({"extraction_prompt": "legacy", "promotion_prompt": "", "similarity_threshold": 0.71})
    before = config.CONFIG_FILE.read_bytes()
    preferences.set_language("es")
    assert config.CONFIG_FILE.read_bytes() == before
    assert config.load_config()["extraction_prompt"] == preferences.default_prompt("extraction", "es")
    config.save_config({"similarity_threshold": 0.72})
    stored = json.loads(config.CONFIG_FILE.read_text())
    assert stored["extraction_prompt"] == "legacy"
    assert stored["promotion_prompt"] == ""
    assert "prompt_locale" not in stored
    preferences.set_language("zh")
    assert config.load_config()["extraction_prompt"] == "legacy"
    assert config.load_config()["promotion_prompt"] == ""


@pytest.mark.parametrize("locale", preferences.LOCALES)
def test_templates_keep_exact_schema_and_empty_promotion_semantics(isolated_preferences, locale):
    template = preferences.default_prompt("extraction", locale)
    # Compare the full schema, including nesting, arrays and scalar types.
    original = DEFAULT_PAPER_PROMPT.split('\n{\n', 1)[1].split('\n}\n', 1)[0]
    assert '\n{\n' + original + '\n}\n' in template
    assert set(re.findall(r'"([a-z_]+)":', template)) >= set(re.findall(r'"([a-z_]+)":', original))
    preferences.save_prompt("promotion", locale, "")
    assert preferences.get_prompt("promotion", locale, {}) == ""
    preferences.save_prompt("extraction", locale, "")
    assert preferences.get_prompt("extraction", locale, {}) == template


def test_api_validates_locale_and_reset_only_changes_requested_language(isolated_preferences):
    app = FastAPI()
    app.include_router(prompt.router)
    with TestClient(app) as client:
        assert client.put('/api/prompt/preferences', json={"locale": "de"}).status_code == 422
        assert not preferences.PREFERENCES_FILE.exists()
        for locale in preferences.LOCALES:
            assert client.post(f'/api/prompt?locale={locale}', json={"extraction_prompt": f"{locale} custom"}).status_code == 200
        assert client.get('/api/prompt?locale=../../config').status_code == 422
        assert client.post('/api/prompt/reset?locale=ja').status_code == 200
        assert client.get('/api/prompt?locale=en').json()['extraction_prompt'] == 'en custom'
        assert client.put('/api/prompt/preferences', json={"locale": "es"}).json() == {"locale": "es"}
        assert config.load_config()['extraction_prompt'] == 'es custom'


def test_language_directives_preserve_machine_contract(isolated_preferences):
    text = '输出中文。{"id": 42, "decision": "promote"} [[paper:42]] list_wiki_index()'
    assert preferences.response_instructions(text, 'zh') == text
    for locale in ('en', 'ja', 'es'):
        localized = preferences.response_instructions(text, locale)
        for token in ('"id": 42', '"decision": "promote"', '[[paper:42]]', 'list_wiki_index()'):
            assert token in localized
        assert '输出中文' not in localized


def test_recommendation_language_changes_only_the_explanation_directive():
    from model_gateway.recommendations import make_prompt
    profile = {"focus": "保留用户原文中文", "long_term": []}
    papers = [{"title": "原始中文标题", "abstract": "中文内容", "arxiv_id": "1234.56789"}]
    original = make_prompt(profile, papers)
    assert make_prompt(profile, papers, "zh") == original
    for locale, language in (("en", "英文"), ("ja", "日语"), ("es", "西班牙语")):
        assert make_prompt(profile, papers, locale) == original.replace("reason 用中文", f"reason 用{language}")
