import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from model_gateway import ensure_model_gateway_config, run_provider_healthcheck
from model_gateway.catalog import BUILTIN_MODEL_REGISTRY
from model_gateway.runtime import _run_codex_cli, _summarize_codex_cli_failure


class ModelGatewayConfigTests(unittest.TestCase):
    def test_legacy_fields_seed_task_bindings_and_openai_provider(self):
        cfg = ensure_model_gateway_config(
            {
                "openai_api_key": "sk-test-1234567890",
                "vlm_model": "gpt-5.4",
                "embedding_model": "text-embedding-3-large",
                "wiki_compile_model": "gpt-4.1-mini",
            }
        )

        gateway = cfg["model_gateway"]
        self.assertEqual(gateway["task_bindings"]["paper_extract"]["model_id"], "openai/gpt-5.4")
        self.assertEqual(gateway["task_bindings"]["paper_chat"]["model_id"], "openai/gpt-5.4")
        self.assertEqual(gateway["task_bindings"]["embedding"]["model_id"], "openai/text-embedding-3-large")
        self.assertEqual(gateway["task_bindings"]["wiki_compile"]["model_id"], "openai/gpt-4.1-mini")
        self.assertEqual(gateway["task_bindings"]["ask_agent"]["model_id"], "openai/gpt-4.1-mini")
        self.assertEqual(gateway["task_bindings"]["ask_synthesis"]["model_id"], "openai/gpt-4.1-mini")
        self.assertEqual(gateway["task_bindings"]["promotion_judge"]["model_id"], "openai/gpt-4.1-mini")
        self.assertEqual(gateway["task_bindings"]["paper_extract"]["reasoning_effort"], "medium")

        openai_provider = next(
            provider for provider in gateway["providers"] if provider["id"] == "openai"
        )
        self.assertEqual(openai_provider["api_key"], "sk-test-1234567890")
        self.assertTrue(any(provider["id"] == "kimi" for provider in gateway["providers"]))
        self.assertTrue(any(provider["id"] == "deepseek" for provider in gateway["providers"]))
        self.assertTrue(any(provider["id"] == "qwen" for provider in gateway["providers"]))
        self.assertTrue(any(provider["id"] == "minimax" for provider in gateway["providers"]))
        self.assertTrue(gateway["task_specs"])
        self.assertTrue(gateway["available_provider_types"])

    def test_task_bindings_sync_legacy_fields_for_openai_models(self):
        cfg = ensure_model_gateway_config(
            {
                "vlm_model": "gpt-4o",
                "embedding_model": "text-embedding-3-small",
                "wiki_compile_model": "gpt-4o-mini",
                "model_gateway": {
                    "task_bindings": {
                        "paper_extract": {"model_id": "openai/gpt-4.1", "reasoning_effort": "high"},
                        "paper_chat": {"model_id": "openai/gpt-4.1", "reasoning_effort": "high"},
                        "embedding": {"model_id": "openai/text-embedding-3-large"},
                        "wiki_compile": {"model_id": "openai/gpt-5.4-mini", "reasoning_effort": "low"},
                        "ask_agent": {"model_id": "openai/gpt-5.4-mini", "reasoning_effort": "low"},
                        "ask_synthesis": {"model_id": "openai/gpt-5.4-mini", "reasoning_effort": "low"},
                        "promotion_judge": {"model_id": "openai/gpt-5.4-mini", "reasoning_effort": "low"},
                    }
                },
            }
        )

        self.assertEqual(cfg["vlm_model"], "gpt-4.1")
        self.assertEqual(cfg["embedding_model"], "text-embedding-3-large")
        self.assertEqual(cfg["wiki_compile_model"], "gpt-5.4-mini")

    def test_string_task_bindings_remain_backward_compatible(self):
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "task_bindings": {
                        "wiki_compile": "openai/gpt-4o-mini",
                    }
                }
            }
        )

        binding = cfg["model_gateway"]["task_bindings"]["wiki_compile"]
        self.assertEqual(binding["model_id"], "openai/gpt-4o-mini")
        self.assertEqual(binding["reasoning_effort"], "medium")

    def test_codex_models_cover_all_non_embedding_tasks(self):
        codex_model = next(
            model for model in BUILTIN_MODEL_REGISTRY if model["id"] == "codex-cli/gpt-5.4-mini"
        )

        self.assertIn("paper_extract", codex_model["supported_tasks"])
        self.assertIn("paper_chat", codex_model["supported_tasks"])
        self.assertIn("ask_agent", codex_model["supported_tasks"])
        self.assertNotIn("embedding", codex_model["supported_tasks"])

    def test_builtin_models_do_not_keep_stale_saved_supported_tasks(self):
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "models": [
                        {
                            "id": "codex-cli/gpt-5.4",
                            "label": "Codex CLI / GPT-5.4",
                            "provider_id": "codex-cli",
                            "upstream_model": "gpt-5.4",
                            "model_kind": "chat",
                            "supports_vision": True,
                            "supported_tasks": ["wiki_compile"],
                            "builtin": True,
                        }
                    ]
                }
            }
        )

        codex_model = next(
            model for model in cfg["model_gateway"]["models"] if model["id"] == "codex-cli/gpt-5.4"
        )
        self.assertIn("paper_extract", codex_model["supported_tasks"])
        self.assertIn("paper_chat", codex_model["supported_tasks"])


class ModelGatewayHealthcheckTests(unittest.TestCase):
    @patch("model_gateway.health._run_codex_cli", return_value="OK")
    def test_codex_cli_healthcheck_updates_provider_status(self, mock_run_codex_cli):
        cfg = ensure_model_gateway_config({})

        result = run_provider_healthcheck(cfg, "codex-cli")

        provider = next(
            provider
            for provider in cfg["model_gateway"]["providers"]
            if provider["id"] == "codex-cli"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(provider["last_test_status"], "ok")
        self.assertTrue(provider["last_tested_at"])
        self.assertEqual(provider["last_test_message"], "OK")
        mock_run_codex_cli.assert_called_once()


class ModelGatewayCodexCliRuntimeTests(unittest.TestCase):
    @patch("model_gateway.runtime.subprocess.run")
    def test_codex_cli_uses_current_exec_flags(self, mock_run):
        output_path_holder = {}

        def _fake_run(args, **kwargs):
            output_index = args.index("--output-last-message") + 1
            output_path = Path(args[output_index])
            output_path_holder["path"] = output_path
            output_path.write_text("OK", encoding="utf-8")

            class _Completed:
                returncode = 0
                stderr = b""

            return _Completed()

        mock_run.side_effect = _fake_run

        raw = _run_codex_cli(
            {"command": "codex"},
            "gpt-5.4-mini",
            "请只回复 OK。",
        )

        called_args = mock_run.call_args.args[0]
        self.assertEqual(raw, "OK")
        self.assertIn("--sandbox", called_args)
        self.assertIn("read-only", called_args)
        self.assertIn("--output-last-message", called_args)
        self.assertNotIn("--ask-for-approval", called_args)
        self.assertFalse(output_path_holder["path"].exists())

    @patch("model_gateway.runtime.subprocess.run")
    def test_codex_cli_passes_reasoning_effort_override(self, mock_run):
        def _fake_run(args, **kwargs):
            output_index = args.index("--output-last-message") + 1
            Path(args[output_index]).write_text("OK", encoding="utf-8")

            class _Completed:
                returncode = 0
                stderr = b""

            return _Completed()

        mock_run.side_effect = _fake_run

        _run_codex_cli(
            {"command": "codex"},
            "gpt-5.4-mini",
            "请只回复 OK。",
            reasoning_effort="high",
        )

        called_args = mock_run.call_args.args[0]
        self.assertIn("-c", called_args)
        self.assertIn('model_reasoning_effort="high"', called_args)

    @patch("model_gateway.runtime.subprocess.run")
    def test_codex_cli_passes_images_when_provided(self, mock_run):
        image_path = ROOT / "tmp-test-image.png"
        image_path.write_text("fake", encoding="utf-8")

        def _fake_run(args, **kwargs):
            output_index = args.index("--output-last-message") + 1
            Path(args[output_index]).write_text("OK", encoding="utf-8")

            class _Completed:
                returncode = 0
                stderr = b""

            return _Completed()

        mock_run.side_effect = _fake_run
        try:
            _run_codex_cli(
                {"command": "codex"},
                "gpt-5.4-mini",
                "请只回复 OK。",
                image_paths=[str(image_path)],
            )
        finally:
            image_path.unlink(missing_ok=True)

        called_args = mock_run.call_args.args[0]
        self.assertIn("--image", called_args)
        self.assertIn(str(image_path), called_args)

    @patch("model_gateway.runtime.subprocess.run")
    def test_codex_cli_uses_custom_timeout_when_provided(self, mock_run):
        def _fake_run(args, **kwargs):
            output_index = args.index("--output-last-message") + 1
            Path(args[output_index]).write_text("OK", encoding="utf-8")

            class _Completed:
                returncode = 0
                stderr = b""

            return _Completed()

        mock_run.side_effect = _fake_run

        _run_codex_cli(
            {"command": "codex"},
            "gpt-5.4-mini",
            "请只回复 OK。",
            timeout_s=321,
        )

        self.assertEqual(mock_run.call_args.kwargs["timeout"], 321)

    def test_codex_cli_reasoning_suffix_gets_actionable_error(self):
        with self.assertRaises(Exception) as exc:
            _run_codex_cli(
                {"command": "codex"},
                "gpt-5.4-high",
                "请只回复 OK。",
            )

        self.assertIn("不支持模型名 'gpt-5.4-high'", str(exc.exception))
        self.assertIn("gpt-5.4", str(exc.exception))

    def test_codex_cli_failure_summary_extracts_invalid_model_message(self):
        raw = (
            "OpenAI Codex v0.130.0-alpha.5\n"
            "ERROR: {\"type\":\"error\",\"status\":400,\"error\":{\"type\":\"invalid_request_error\","
            "\"message\":\"The 'gpt-5.4-high' model is not supported when using Codex with a ChatGPT account.\"}}\n"
        )

        message = _summarize_codex_cli_failure(raw, "gpt-5.4-high")

        self.assertIn("不支持模型名 'gpt-5.4-high'", message)
        self.assertIn("gpt-5.4", message)


if __name__ == "__main__":
    unittest.main()
