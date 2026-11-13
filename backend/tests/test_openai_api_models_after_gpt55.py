import sys
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(BACKEND)]

from config import AVAILABLE_MODELS, AVAILABLE_WIKI_COMPILE_MODELS
from model_gateway.catalog import (
    BUILTIN_MODEL_REGISTRY,
    DEFAULT_PROVIDER_REGISTRY,
    OPENAI_RESPONSES_ONLY_MODELS,
    TASK_BINDING_DEFAULTS,
    TASK_SPECS,
    openai_model_uses_responses_api,
    openai_upstream_model_name,
)
from model_gateway.config import LEGACY_TO_MODEL_ID, VALID_REASONING_EFFORTS, ensure_model_gateway_config
from model_gateway.runtime import _call_text_model_unbudgeted
from services.paper_extract_route import (
    require_paper_extract_route,
    resolve_paper_extract_route,
)
from services.vlm_service import _run_with_responses_file_search, model_uses_responses_api


# Official OpenAI API model IDs verified from
# https://developers.openai.com/api/docs/models on 2026-11-13.
# These are the post-GPT-5.5 vision + file_search models added to the
# native OpenAI catalog for 主模型. Codex CLI shorthand IDs (gpt-5.6-ter /
# gpt-5.6-lun) are intentionally not included.
NEW_OPENAI_API_UPSTREAM_IDS = (
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)
NEW_OPENAI_API_MODEL_IDS = tuple(f"openai/{name}" for name in NEW_OPENAI_API_UPSTREAM_IDS)
EXISTING_RESPONSES_MODELS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini")
CODEX_SHORTHAND_UPSTREAM = ("gpt-5.6-ter", "gpt-5.6-lun")
EXCLUDED_UNVERIFIED_API_IDS = (
    "gpt-5.6",  # alias that routes to Sol; we expose explicit IDs only
    "gpt-5.6-cyber",
    "gpt-6-cyber",
    "gpt-5.6-ter",
    "gpt-5.6-lun",
    "gpt-6-pro",
    "gpt-6.1-pro",
)

MAIN_TASK_IDS = [
    task["id"]
    for task in TASK_SPECS
    if task["task_type"] != "embedding"
    and task["id"] not in {"recommend_rank", "domain_connect", "roadmap_research"}
]


def _openai_provider(cfg_providers, *, api_key="test-openai-key"):
    providers = deepcopy(cfg_providers)
    for provider in providers:
        if provider["id"] == "openai":
            provider["api_key"] = api_key
            provider["enabled"] = True
        if provider["id"] == "codex-cli":
            provider["enabled"] = True
    return providers


def _cfg(extract_model="openai/gpt-4o", *, models=None, bindings=None, reasoning_effort="high"):
    task_bindings = {
        task: {"model_id": model, "reasoning_effort": "medium"}
        for task, model in TASK_BINDING_DEFAULTS.items()
    }
    if bindings:
        task_bindings.update(deepcopy(bindings))
    task_bindings["paper_extract"] = {
        "model_id": extract_model,
        "reasoning_effort": reasoning_effort,
    }
    return ensure_model_gateway_config(
        {
            "openai_api_key": "test-openai-key",
            "vlm_model": "gpt-4o",
            "embedding_model": "text-embedding-3-small",
            "wiki_compile_model": "gpt-4o-mini",
            "model_gateway": {
                "providers": _openai_provider(DEFAULT_PROVIDER_REGISTRY),
                "models": deepcopy(models if models is not None else BUILTIN_MODEL_REGISTRY),
                "task_bindings": task_bindings,
            },
        }
    )


class CatalogVisibilityTests(unittest.TestCase):
    def test_new_openai_api_models_are_builtin_vision_main_candidates(self):
        by_id = {model["id"]: model for model in BUILTIN_MODEL_REGISTRY}
        for model_id, upstream in zip(NEW_OPENAI_API_MODEL_IDS, NEW_OPENAI_API_UPSTREAM_IDS):
            with self.subTest(model_id=model_id):
                model = by_id[model_id]
                self.assertEqual(model["provider_id"], "openai")
                self.assertEqual(model["upstream_model"], upstream)
                self.assertEqual(model["model_kind"], "chat")
                self.assertTrue(model["supports_vision"])
                self.assertTrue(model["builtin"])
                for task_id in MAIN_TASK_IDS:
                    self.assertIn(task_id, model["supported_tasks"])
                self.assertNotIn("embedding", model["supported_tasks"])

    def test_settings_main_model_filter_includes_new_openai_models(self):
        visible = [
            model["id"]
            for model in BUILTIN_MODEL_REGISTRY
            if all(task_id in model["supported_tasks"] for task_id in MAIN_TASK_IDS)
            and model["provider_id"] == "openai"
        ]
        for model_id in NEW_OPENAI_API_MODEL_IDS:
            self.assertIn(model_id, visible)
        self.assertIn("openai/gpt-5.5", visible)
        self.assertIn("openai/gpt-4o", visible)
        self.assertNotIn("openai/text-embedding-3-large", visible)

    def test_codex_shorthand_ids_are_not_registered_as_openai_api_models(self):
        openai_ids = {model["id"] for model in BUILTIN_MODEL_REGISTRY if model["provider_id"] == "openai"}
        openai_upstream = {
            model["upstream_model"]
            for model in BUILTIN_MODEL_REGISTRY
            if model["provider_id"] == "openai"
        }
        for raw in EXCLUDED_UNVERIFIED_API_IDS:
            self.assertNotIn(f"openai/{raw}", openai_ids)
            if raw in CODEX_SHORTHAND_UPSTREAM:
                self.assertNotIn(raw, openai_upstream)
        # Codex keeps its own shorthand catalog entries, distinct from API IDs.
        builtin_ids = {model["id"] for model in BUILTIN_MODEL_REGISTRY}
        self.assertIn("codex-cli/gpt-5.6-ter", builtin_ids)
        self.assertIn("codex-cli/gpt-5.6-lun", builtin_ids)
        self.assertIn("codex-cli/gpt-6-astra", builtin_ids)
        ter = next(m for m in BUILTIN_MODEL_REGISTRY if m["id"] == "codex-cli/gpt-5.6-ter")
        self.assertEqual(ter["upstream_model"], "gpt-5.6-ter")
        self.assertNotEqual(ter["upstream_model"], "gpt-5.6-terra")

    def test_defaults_are_unchanged(self):
        self.assertEqual(TASK_BINDING_DEFAULTS["paper_extract"], "openai/gpt-4o")
        self.assertEqual(TASK_BINDING_DEFAULTS["wiki_compile"], "openai/gpt-4o-mini")
        self.assertEqual(VALID_REASONING_EFFORTS, {"low", "medium", "high"})

    def test_legacy_dropdown_lists_include_new_vision_models(self):
        available_ids = [item["id"] for item in AVAILABLE_MODELS]
        wiki_ids = [item["id"] for item in AVAILABLE_WIKI_COMPILE_MODELS]
        for upstream in NEW_OPENAI_API_UPSTREAM_IDS:
            self.assertIn(upstream, available_ids)
            self.assertIn(upstream, wiki_ids)
            available = next(item for item in AVAILABLE_MODELS if item["id"] == upstream)
            self.assertTrue(available["supports_vision"])


class ConfigNormalizeTests(unittest.TestCase):
    def test_stale_saved_builtin_list_still_receives_new_openai_models(self):
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "models": [
                        {
                            "id": "openai/gpt-5.5",
                            "label": "stale label that must be replaced",
                            "provider_id": "openai",
                            "upstream_model": "gpt-5.5",
                            "model_kind": "chat",
                            "supports_vision": True,
                            "supported_tasks": ["wiki_compile"],
                            "builtin": True,
                        }
                    ],
                    "task_bindings": {
                        "paper_extract": {
                            "model_id": "codex-cli/gpt-6-astra",
                            "reasoning_effort": "high",
                        }
                    },
                }
            }
        )
        by_id = {model["id"]: model for model in cfg["model_gateway"]["models"]}
        for model_id in NEW_OPENAI_API_MODEL_IDS:
            self.assertIn(model_id, by_id)
            self.assertIn("paper_extract", by_id[model_id]["supported_tasks"])
        # Saved builtin metadata is not allowed to pin stale capabilities.
        self.assertIn("paper_extract", by_id["openai/gpt-5.5"]["supported_tasks"])
        self.assertEqual(by_id["openai/gpt-5.5"]["label"], "OpenAI / GPT-5.5")
        # Existing user binding is left alone.
        self.assertEqual(
            cfg["model_gateway"]["task_bindings"]["paper_extract"]["model_id"],
            "codex-cli/gpt-6-astra",
        )
        self.assertEqual(
            cfg["model_gateway"]["task_bindings"]["paper_extract"]["reasoning_effort"],
            "high",
        )

    def test_custom_user_model_is_preserved(self):
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "models": [
                        {
                            "id": "openai/my-fine-tune",
                            "label": "Custom fine-tune",
                            "provider_id": "openai",
                            "upstream_model": "ft:custom",
                            "model_kind": "chat",
                            "supports_vision": False,
                            "supported_tasks": ["wiki_compile"],
                            "builtin": False,
                        }
                    ]
                }
            }
        )
        custom = next(
            model
            for model in cfg["model_gateway"]["models"]
            if model["id"] == "openai/my-fine-tune"
        )
        self.assertEqual(custom["upstream_model"], "ft:custom")
        self.assertFalse(custom["supports_vision"])
        self.assertIn("wiki_compile", custom["supported_tasks"])
        self.assertNotIn("paper_extract", custom["supported_tasks"])

    def test_legacy_short_names_normalize_to_official_openai_ids(self):
        for upstream, model_id in zip(NEW_OPENAI_API_UPSTREAM_IDS, NEW_OPENAI_API_MODEL_IDS):
            with self.subTest(upstream=upstream):
                self.assertEqual(LEGACY_TO_MODEL_ID[upstream], model_id)
                cfg = ensure_model_gateway_config({"vlm_model": upstream})
                self.assertEqual(
                    cfg["model_gateway"]["task_bindings"]["paper_extract"]["model_id"],
                    model_id,
                )

    def test_codex_shorthand_is_not_mapped_to_openai_terra_or_luna(self):
        for shorthand in CODEX_SHORTHAND_UPSTREAM:
            self.assertNotIn(shorthand, LEGACY_TO_MODEL_ID)
        self.assertEqual(LEGACY_TO_MODEL_ID["gpt-5.6-terra"], "openai/gpt-5.6-terra")
        self.assertEqual(LEGACY_TO_MODEL_ID["gpt-5.6-luna"], "openai/gpt-5.6-luna")
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "task_bindings": {
                        "paper_extract": {
                            "model_id": "codex-cli/gpt-5.6-ter",
                            "reasoning_effort": "medium",
                        }
                    }
                }
            }
        )
        self.assertEqual(
            cfg["model_gateway"]["task_bindings"]["paper_extract"]["model_id"],
            "codex-cli/gpt-5.6-ter",
        )


class ResponsesRoutingTests(unittest.TestCase):
    def test_helper_recognizes_new_and_existing_responses_models(self):
        for name in NEW_OPENAI_API_UPSTREAM_IDS + EXISTING_RESPONSES_MODELS:
            with self.subTest(name=name):
                self.assertTrue(openai_model_uses_responses_api(name))
                self.assertTrue(openai_model_uses_responses_api(f"openai/{name}"))
                self.assertTrue(model_uses_responses_api(name))
        self.assertFalse(openai_model_uses_responses_api("gpt-4o"))
        self.assertFalse(openai_model_uses_responses_api("openai/gpt-4o"))
        self.assertFalse(openai_model_uses_responses_api("gpt-5.6-ter"))
        self.assertFalse(openai_model_uses_responses_api("gpt-5.6-lun"))
        self.assertFalse(openai_model_uses_responses_api("gpt-5.6"))
        self.assertEqual(openai_upstream_model_name("openai/gpt-6.1-sol"), "gpt-6.1-sol")
        self.assertEqual(set(EXISTING_RESPONSES_MODELS) | set(NEW_OPENAI_API_UPSTREAM_IDS), set(OPENAI_RESPONSES_ONLY_MODELS))

    def test_each_new_model_resolves_to_responses_file_search(self):
        fingerprints = set()
        for model_id, upstream in zip(NEW_OPENAI_API_MODEL_IDS, NEW_OPENAI_API_UPSTREAM_IDS):
            with self.subTest(model_id=model_id):
                route = resolve_paper_extract_route(_cfg(model_id))
                self.assertEqual(route["provider_type"], "openai")
                self.assertEqual(route["provider_id"], "openai")
                self.assertEqual(route["model_id"], model_id)
                self.assertEqual(route["upstream_model"], upstream)
                self.assertEqual(route["surface"], "openai_file_search")
                self.assertEqual(route["file_search_api"], "responses")
                self.assertTrue(route["requires_file_search_confirmation"])
                self.assertTrue(route["supported"])
                self.assertEqual(route["reasoning_effort"], "high")
                fingerprints.add(route["route_fingerprint"])
        self.assertEqual(len(fingerprints), len(NEW_OPENAI_API_MODEL_IDS))

    def test_old_openai_and_codex_routes_are_unchanged(self):
        assistants = resolve_paper_extract_route(_cfg("openai/gpt-4o"))
        self.assertEqual(assistants["file_search_api"], "assistants")
        self.assertEqual(assistants["surface"], "openai_file_search")
        self.assertTrue(assistants["requires_file_search_confirmation"])

        gpt55 = resolve_paper_extract_route(_cfg("openai/gpt-5.5"))
        self.assertEqual(gpt55["file_search_api"], "responses")

        codex = resolve_paper_extract_route(_cfg("codex-cli/gpt-6-astra"))
        self.assertEqual(codex["provider_type"], "codex_cli")
        self.assertEqual(codex["surface"], "codex_cli_hierarchy")
        self.assertIsNone(codex["file_search_api"])
        self.assertFalse(codex["requires_file_search_confirmation"])

        shorthand = resolve_paper_extract_route(_cfg("codex-cli/gpt-5.6-ter"))
        self.assertEqual(shorthand["provider_type"], "codex_cli")
        self.assertEqual(shorthand["upstream_model"], "gpt-5.6-ter")
        self.assertIsNone(shorthand["file_search_api"])
        self.assertFalse(shorthand["requires_file_search_confirmation"])

    def test_file_search_confirmation_is_required_and_accepted_for_new_models(self):
        for model_id in NEW_OPENAI_API_MODEL_IDS:
            with self.subTest(model_id=model_id):
                cfg = _cfg(model_id)
                route = resolve_paper_extract_route(cfg)
                with self.assertRaises(Exception) as exc:
                    require_paper_extract_route(cfg)
                self.assertEqual(exc.exception.code, "file_search_confirmation_required")

                accepted = require_paper_extract_route(
                    cfg,
                    confirmation={
                        "acknowledged": True,
                        "route_fingerprint": route["route_fingerprint"],
                    },
                )
                self.assertEqual(accepted["model_id"], model_id)
                self.assertEqual(accepted["file_search_api"], "responses")

                stale = resolve_paper_extract_route(_cfg("openai/gpt-4o"))
                with self.assertRaises(Exception) as changed:
                    require_paper_extract_route(
                        cfg,
                        confirmation={
                            "acknowledged": True,
                            "route_fingerprint": stale["route_fingerprint"],
                        },
                    )
                self.assertEqual(changed.exception.code, "file_search_route_changed")


class ResponsesParameterTests(unittest.TestCase):
    def test_extract_responses_kwargs_omit_temperature_and_send_reasoning(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(id="resp_test", output_text="{}")
        with patch("services.vlm_service.track_call", side_effect=lambda fn, **_kwargs: fn()):
            raw, response_id = _run_with_responses_file_search(
                client,
                "gpt-6-astra",
                "vs_test",
                "extract this paper",
                reasoning_effort="high",
            )
        self.assertEqual(raw, "{}")
        self.assertEqual(response_id, "resp_test")
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6-astra")
        self.assertEqual(
            kwargs["tools"],
            [{"type": "file_search", "vector_store_ids": ["vs_test"]}],
        )
        self.assertEqual(kwargs["reasoning"], {"effort": "high"})
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("top_p", kwargs)
        self.assertEqual(kwargs["text"], {"format": {"type": "json_object"}})

        client.responses.create.reset_mock()
        with patch("services.vlm_service.track_call", side_effect=lambda fn, **_kwargs: fn()):
            _run_with_responses_file_search(
                client,
                "gpt-6.1-sol",
                "vs_test",
                "extract this paper",
                reasoning_effort="xhigh",
            )
        kwargs = client.responses.create.call_args.kwargs
        self.assertNotIn("reasoning", kwargs)
        self.assertNotIn("temperature", kwargs)

    def test_call_text_model_routes_new_openai_models_to_responses_without_temperature(self):
        for model_id, upstream in zip(NEW_OPENAI_API_MODEL_IDS, NEW_OPENAI_API_UPSTREAM_IDS):
            with self.subTest(model_id=model_id):
                client = MagicMock()
                client.responses.create.return_value = SimpleNamespace(output_text="ok")
                cfg = _cfg(model_id)
                with patch("model_gateway.runtime._create_openai_client", return_value=client), patch(
                    "model_gateway.runtime.track_call", side_effect=lambda fn, **_kwargs: fn()
                ):
                    text = _call_text_model_unbudgeted(
                        cfg,
                        model_id=model_id,
                        system="sys",
                        user="hello",
                        temperature=0.3,
                        reasoning_effort="medium",
                    )
                self.assertEqual(text, "ok")
                client.chat.completions.create.assert_not_called()
                kwargs = client.responses.create.call_args.kwargs
                self.assertEqual(kwargs["model"], upstream)
                self.assertEqual(kwargs["instructions"], "sys")
                self.assertEqual(kwargs["input"], "hello")
                self.assertEqual(kwargs["reasoning"], {"effort": "medium"})
                self.assertNotIn("temperature", kwargs)
                self.assertNotIn("max_tokens", kwargs)

    def test_gpt4o_text_calls_still_use_chat_completions(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="chat-ok"))]
        )
        cfg = _cfg("openai/gpt-4o")
        with patch("model_gateway.runtime._create_openai_client", return_value=client), patch(
            "model_gateway.runtime.track_call", side_effect=lambda fn, **_kwargs: fn()
        ):
            text = _call_text_model_unbudgeted(
                cfg,
                model_id="openai/gpt-4o",
                system="sys",
                user="hello",
                temperature=0.3,
                reasoning_effort="medium",
            )
        self.assertEqual(text, "chat-ok")
        client.responses.create.assert_not_called()
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4o")
        self.assertEqual(kwargs["temperature"], 0.3)

    def test_responses_helper_is_the_shared_source_of_truth(self):
        from services import paper_extract_route, vlm_service
        from model_gateway import runtime

        # The three former copies no longer keep a private set that can drift.
        self.assertFalse(hasattr(vlm_service, "RESPONSES_ONLY_MODELS"))
        self.assertFalse(hasattr(paper_extract_route, "RESPONSES_ONLY_MODELS"))
        self.assertFalse(hasattr(runtime, "RESPONSES_ONLY_MODELS"))
        self.assertTrue(vlm_service.model_uses_responses_api("gpt-6-astra"))
        self.assertEqual(
            paper_extract_route._file_search_api("gpt-6.1-sol"),
            "responses",
        )


if __name__ == "__main__":
    unittest.main()
