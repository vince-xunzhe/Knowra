import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from model_gateway import ensure_model_gateway_config, run_provider_healthcheck
from model_gateway.catalog import BUILTIN_MODEL_REGISTRY
from model_gateway.runtime import (
    _resolve_codex_cli_command,
    _run_codex_cli,
    _summarize_codex_cli_failure,
)


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
        model_ids = [
            "codex-cli/gpt-5.4-mini",
            "codex-cli/gpt-5.6-sol",
            "codex-cli/gpt-5.6-ter",
            "codex-cli/gpt-5.6-lun",
            "codex-cli/gpt-6-astra",
        ]

        for model_id in model_ids:
            with self.subTest(model_id=model_id):
                codex_model = next(
                    model for model in BUILTIN_MODEL_REGISTRY if model["id"] == model_id
                )
                self.assertIn("paper_extract", codex_model["supported_tasks"])
                self.assertIn("paper_chat", codex_model["supported_tasks"])
                self.assertIn("ask_agent", codex_model["supported_tasks"])
                self.assertNotIn("embedding", codex_model["supported_tasks"])

    def test_existing_config_receives_new_codex_models(self):
        cfg = ensure_model_gateway_config(
            {
                "model_gateway": {
                    "models": [
                        {
                            "id": "codex-cli/gpt-5.5",
                            "label": "Codex CLI / GPT-5.5",
                            "provider_id": "codex-cli",
                            "upstream_model": "gpt-5.5",
                            "model_kind": "chat",
                            "supports_vision": True,
                            "supported_tasks": ["paper_extract"],
                            "builtin": True,
                        }
                    ]
                }
            }
        )

        model_ids = {model["id"] for model in cfg["model_gateway"]["models"]}
        self.assertTrue(
            {
                "codex-cli/gpt-5.6-sol",
                "codex-cli/gpt-5.6-ter",
                "codex-cli/gpt-5.6-lun",
                "codex-cli/gpt-6-astra",
            }.issubset(model_ids)
        )

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
    def setUp(self):
        env = patch.dict(os.environ, {'CODEX_CLI_PATH': ''})
        env.start()
        self.addCleanup(env.stop)
        executable = patch('model_gateway.runtime.validate_codex_cli', return_value='codex')
        executable.start()
        self.addCleanup(executable.stop)

    @patch("model_gateway.runtime.shutil.which", return_value=None)
    @patch("model_gateway.runtime._default_codex_cli_candidates")
    def test_codex_cli_falls_back_to_desktop_app_binary(
        self,
        mock_candidates,
        _mock_which,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = Path(tmp_dir) / "codex"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            mock_candidates.return_value = [executable]

            self.assertEqual(_resolve_codex_cli_command("codex"), str(executable))

    @patch("model_gateway.runtime.shutil.which", return_value="/custom/bin/codex")
    def test_codex_cli_prefers_path_lookup(self, _mock_which):
        self.assertEqual(_resolve_codex_cli_command("codex"), "/custom/bin/codex")

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
    def test_codex_cli_passes_registered_model_names(self, mock_run):
        def _fake_run(args, **kwargs):
            output_index = args.index("--output-last-message") + 1
            Path(args[output_index]).write_text("OK", encoding="utf-8")

            class _Completed:
                returncode = 0
                stderr = b""

            return _Completed()

        mock_run.side_effect = _fake_run

        for model_name in (
            "gpt-5.6-sol",
            "gpt-5.6-ter",
            "gpt-5.6-lun",
            "gpt-6-astra",
        ):
            with self.subTest(model_name=model_name):
                _run_codex_cli(
                    {"command": "codex"},
                    model_name,
                    "请只回复 OK。",
                )

                called_args = mock_run.call_args.args[0]
                model_index = called_args.index("--model") + 1
                self.assertEqual(called_args[model_index], model_name)

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

    def test_codex_cli_failure_summary_explains_required_upgrade(self):
        raw = (
            "OpenAI Codex v0.151.0-alpha.7.2\n"
            "ERROR: {\"type\":\"error\",\"status\":400,\"error\":{"
            "\"message\":\"The 'gpt-6-astra' model requires a newer version of Codex.\"}}\n"
        )

        message = _summarize_codex_cli_failure(raw, "gpt-6-astra")

        self.assertIn("gpt-6-astra", message)
        self.assertIn("更新版本", message)
        self.assertIn("升级", message)


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'CODEX_CLI_PATH': ''})
        env.start()
        self.addCleanup(env.stop)

    def binary(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return path

    def test_new_and_legacy_app_layouts_are_candidates(self):
        from model_gateway.runtime import _default_codex_cli_candidates
        candidates = _default_codex_cli_candidates()
        for root in (Path('/Applications'), Path.home() / 'Applications'):
            for app in ('Codex.app', 'ChatGPT.app'):
                resources = root / app / 'Contents/Resources'
                self.assertIn(resources / 'codex', candidates)
                self.assertIn(resources / 'codex-cli/CodexCLI.app/Contents/MacOS/codex', candidates)

    def test_discovery_is_fresh_after_app_replacement(self):
        from model_gateway.runtime import validate_codex_cli
        old = self.binary('old/codex')
        new = self.binary('new/codex')
        with patch('model_gateway.runtime.shutil.which', return_value=None), \
             patch('model_gateway.runtime._default_codex_cli_candidates', return_value=[old, new]):
            self.assertEqual(validate_codex_cli({}), str(old))
            old.unlink()
            self.assertEqual(validate_codex_cli({}), str(new))

    def test_explicit_path_then_environment_override_then_path(self):
        explicit, env = self.binary('explicit'), self.binary('env')
        from model_gateway.runtime import validate_codex_cli, ProviderConfigurationError
        with patch.dict(os.environ, {'CODEX_CLI_PATH': str(env)}), \
             patch('model_gateway.runtime.shutil.which', return_value='/different/codex'):
            self.assertEqual(validate_codex_cli({'command': str(explicit)}), str(explicit))
            self.assertEqual(validate_codex_cli({}), str(env))
            env.unlink()
            with self.assertRaises(ProviderConfigurationError):
                validate_codex_cli({})

    def test_missing_or_nonexecutable_cli_is_not_retried_or_called(self):
        from model_gateway.runtime import ProviderConfigurationError
        from services.paper_pipeline_service import is_recoverable_error
        path = self.binary('no-permission')
        path.chmod(0o644)
        with patch('model_gateway.runtime.subprocess.run') as run:
            for command in (str(path), str(self.root / 'missing')):
                with self.assertRaises(ProviderConfigurationError) as error:
                    _run_codex_cli({'command': command}, 'gpt-6-astra', 'test')
                self.assertFalse(is_recoverable_error(error.exception))
                self.assertNotIn('解析失败', str(error.exception))
            run.assert_not_called()

    def test_preflight_uses_bound_provider_only_for_cli_tasks(self):
        from model_gateway.runtime import preflight_task_runtime, ProviderConfigurationError
        cfg = ensure_model_gateway_config({'model_gateway': {'task_bindings': {
            'paper_extract': {'model_id': 'codex-cli/gpt-6-astra'}}}})
        with patch('model_gateway.runtime.validate_codex_cli',
                   side_effect=ProviderConfigurationError('missing')) as validate:
            with self.assertRaises(ProviderConfigurationError):
                preflight_task_runtime(cfg, 'paper_extract')
            validate.assert_called_once()
            validate.reset_mock()
            preflight_task_runtime(cfg, 'embedding')
            validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
