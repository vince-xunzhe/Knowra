import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(BACKEND)]

from database import get_db
from model_gateway.catalog import BUILTIN_MODEL_REGISTRY, DEFAULT_PROVIDER_REGISTRY, TASK_BINDING_DEFAULTS
from model_gateway.config import ensure_model_gateway_config
from routers import jobs, papers
from services import task_runtime
from services.paper_extract_route import (
    CONFIRMATION_REQUIRED_CODE,
    FILE_SEARCH_PRICING_URL,
    ROUTE_CHANGED_CODE,
    UNSUPPORTED_ROUTE_CODE,
    FileSearchConfirmation,
    confirmation_as_dict,
    require_paper_extract_route,
    reset_active_file_search_confirmation,
    resolve_paper_extract_route,
    set_active_file_search_confirmation,
)
from services.task_executor import process_papers, pipeline
from services.task_store import TaskStore


def _make_cfg(extract_model="openai/gpt-4o", *, openai_key="test-openai-key", base_url=None):
    cfg = {
        "openai_api_key": openai_key,
        "vlm_model": "gpt-4o",
        "embedding_model": "text-embedding-3-small",
        "wiki_compile_model": "gpt-4o-mini",
        "model_gateway": {
            "providers": deepcopy(DEFAULT_PROVIDER_REGISTRY),
            "models": deepcopy(BUILTIN_MODEL_REGISTRY),
            "task_bindings": {
                task: {"model_id": model, "reasoning_effort": "medium"}
                for task, model in TASK_BINDING_DEFAULTS.items()
            },
        },
    }
    cfg["model_gateway"]["task_bindings"]["paper_extract"] = {
        "model_id": extract_model,
        "reasoning_effort": "high",
    }
    for provider in cfg["model_gateway"]["providers"]:
        if provider["id"] == "openai":
            provider["api_key"] = openai_key
            provider["enabled"] = True
            if base_url is not None:
                provider["base_url"] = base_url
        if provider["id"] == "codex-cli":
            provider["enabled"] = True
        if provider["id"] == "kimi":
            provider["api_key"] = "test-kimi-key"
    return ensure_model_gateway_config(cfg)


def _confirm(cfg=None, extract_model="openai/gpt-4o", **kwargs):
    route = resolve_paper_extract_route(cfg or _make_cfg(extract_model, **kwargs))
    return {
        "acknowledged": True,
        "route_fingerprint": route["route_fingerprint"],
    }, route


class PaperExtractRouteTests(unittest.TestCase):
    def test_openai_binding_uses_file_search_and_requires_confirmation(self):
        cfg = _make_cfg("openai/gpt-4o")
        route = resolve_paper_extract_route(cfg)
        self.assertEqual(route["provider_id"], "openai")
        self.assertEqual(route["provider_type"], "openai")
        self.assertEqual(route["model_id"], "openai/gpt-4o")
        self.assertEqual(route["upstream_model"], "gpt-4o")
        self.assertEqual(route["surface"], "openai_file_search")
        self.assertEqual(route["file_search_api"], "assistants")
        self.assertTrue(route["requires_file_search_confirmation"])
        self.assertTrue(route["supported"])
        self.assertEqual(route["pricing_url"], FILE_SEARCH_PRICING_URL)
        self.assertNotIn("api_key", route)
        public = require_paper_extract_route(cfg, _confirm(cfg)[0])
        self.assertEqual(public["route_fingerprint"], route["route_fingerprint"])

    def test_gpt54_uses_responses_file_search(self):
        cfg = _make_cfg("openai/gpt-5.4")
        route = resolve_paper_extract_route(cfg)
        self.assertEqual(route["file_search_api"], "responses")
        self.assertTrue(route["requires_file_search_confirmation"])

    def test_codex_binding_does_not_require_confirmation(self):
        cfg = _make_cfg("codex-cli/gpt-6-astra")
        route = resolve_paper_extract_route(cfg)
        self.assertEqual(route["provider_type"], "codex_cli")
        self.assertEqual(route["surface"], "codex_cli_hierarchy")
        self.assertFalse(route["requires_file_search_confirmation"])
        self.assertTrue(route["supported"])
        require_paper_extract_route(cfg, None)

    def test_compatible_api_is_not_assumed_to_support_file_search(self):
        cfg = _make_cfg("kimi/kimi-k2.5")
        # Bind a catalog-compatible model if present; otherwise inject one.
        if resolve_paper_extract_route(cfg)["provider_type"] != "openai_compatible":
            cfg["model_gateway"]["models"].append({
                "id": "kimi/kimi-k2.5",
                "label": "Kimi test",
                "provider_id": "kimi",
                "upstream_model": "kimi-k2.5",
                "model_kind": "chat",
                "supports_vision": False,
                "supported_tasks": ["paper_extract"],
                "builtin": False,
            })
            cfg = ensure_model_gateway_config(cfg)
        route = resolve_paper_extract_route(cfg)
        self.assertEqual(route["provider_type"], "openai_compatible")
        self.assertFalse(route["supported"])
        self.assertFalse(route["requires_file_search_confirmation"])
        self.assertIn("兼容", route["unsupported_reason"])
        with self.assertRaises(Exception) as caught:
            require_paper_extract_route(cfg, None)
        self.assertEqual(caught.exception.code, UNSUPPORTED_ROUTE_CODE)

    def test_fingerprint_changes_when_model_or_base_url_changes(self):
        first = resolve_paper_extract_route(_make_cfg("openai/gpt-4o"))
        second = resolve_paper_extract_route(_make_cfg("openai/gpt-5.4"))
        third = resolve_paper_extract_route(_make_cfg("openai/gpt-4o", base_url="https://example.invalid/v1"))
        self.assertNotEqual(first["route_fingerprint"], second["route_fingerprint"])
        self.assertNotEqual(first["route_fingerprint"], third["route_fingerprint"])
        self.assertTrue(third["has_custom_base_url"])
        # Changing only the API key does not change the fingerprint.
        same = resolve_paper_extract_route(_make_cfg("openai/gpt-4o", openai_key="another-test-key"))
        self.assertEqual(first["route_fingerprint"], same["route_fingerprint"])


class FileSearchConfirmationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = TaskStore(Path(self.tmp.name) / "tasks.db")
        self.queue.initialize()
        for mock in [
            patch.object(task_runtime, "_store", self.queue),
            patch.object(task_runtime, "ensure_worker"),
            patch.object(task_runtime, "in_worker", return_value=False),
        ]:
            mock.start()
            self.addCleanup(mock.stop)
        self.cfg = _make_cfg("openai/gpt-4o")
        self.confirm, self.route = _confirm(self.cfg)
        self.config_patch = patch("services.paper_extract_route.load_config", return_value=self.cfg)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def _client(self, papers_rows=None):
        db = MagicMock()
        paper = SimpleNamespace(id="p1", filename="paper.pdf")
        if papers_rows is None:
            db.query.return_value.filter.return_value.all.return_value = [paper]
            db.query.return_value.filter.return_value.first.return_value = paper
        else:
            db.query.return_value.filter.return_value.all.return_value = papers_rows
            db.query.return_value.filter.return_value.first.return_value = papers_rows[0] if papers_rows else None
        app = FastAPI()
        app.include_router(papers.router)
        app.include_router(jobs.router)
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db

    def test_extract_route_endpoint_hides_secrets(self):
        client, _ = self._client()
        response = client.get("/api/papers/extract-route")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_file_search_confirmation"])
        self.assertEqual(body["model_id"], "openai/gpt-4o")
        self.assertNotIn("api_key", body)
        self.assertNotIn("openai_api_key", body)
        serialized = str(body)
        self.assertNotIn("test-openai-key", serialized)

    def test_process_endpoints_reject_missing_confirmation_without_queuing(self):
        client, db = self._client()
        for path in (
            "/api/process",
            "/api/papers/p1/process",
            "/api/papers/p1/retry",
            "/api/papers/p1/reprocess",
        ):
            response = client.post(path)
            self.assertEqual(response.status_code, 409, path)
            self.assertEqual(response.json()["detail"]["code"], CONFIRMATION_REQUIRED_CODE)
            self.assertIn("不包含在 ChatGPT 或 Codex 订阅", response.json()["detail"]["message"])
        self.assertEqual(self.queue.active(), [])
        db.close.assert_not_called()

    def test_retry_failed_rejects_missing_confirmation(self):
        failed = SimpleNamespace(
            id="p1",
            filename="paper.pdf",
            processed=False,
            error="boom",
            processing_status="failed",
            last_error_stage="extracting",
            last_error_reason="boom",
            last_error_recoverable=True,
            retry_count=1,
        )
        client, db = self._client([failed])
        db.query.return_value.filter.return_value.all.return_value = [failed]
        response = client.post("/api/papers/retry_failed")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.queue.active(), [])

    def test_confirmed_process_all_queues_job_without_model_call(self):
        client, _ = self._client()
        with patch.object(papers, "_process_single", side_effect=AssertionError("model called")) as run:
            response = client.post(
                "/api/process",
                json={"file_search_confirmation": self.confirm},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("accepted", True) or response.json().get("running"))
        run.assert_not_called()
        self.assertEqual(len(self.queue.active()), 1)
        payload = self.queue.active()[0]["payload"]
        self.assertEqual(payload["file_search_confirmation"], self.confirm)
        self.assertEqual(payload["paper_ids"], ["p1"])

    def test_stale_confirmation_is_rejected_after_binding_change(self):
        stale, _ = _confirm(_make_cfg("openai/gpt-4o"))
        switched = _make_cfg("openai/gpt-5.4")
        client, _ = self._client()
        with patch("services.paper_extract_route.load_config", return_value=switched):
            response = client.post(
                "/api/process",
                json={"file_search_confirmation": stale},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], ROUTE_CHANGED_CODE)
        self.assertEqual(self.queue.active(), [])

    def test_pipeline_requires_confirmation_and_stores_it(self):
        client, _ = self._client()
        denied = client.post("/api/pipeline/run", json={"use_llm": True})
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(self.queue.active(), [])
        accepted = client.post(
            "/api/pipeline/run",
            json={"use_llm": True, "file_search_confirmation": self.confirm},
        )
        self.assertEqual(accepted.status_code, 202)
        job = self.queue.active()[0]
        self.assertEqual(job["kind"], "pipeline")
        self.assertEqual(job["payload"]["file_search_confirmation"], self.confirm)

    def test_codex_process_does_not_need_confirmation(self):
        cfg = _make_cfg("codex-cli/gpt-6-astra")
        client, _ = self._client()
        with patch("services.paper_extract_route.load_config", return_value=cfg):
            response = client.post("/api/process")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.queue.active()), 1)
        self.assertNotIn("file_search_confirmation", self.queue.active()[0]["payload"])

    def test_compatible_binding_is_rejected_on_all_start_endpoints(self):
        cfg = _make_cfg("kimi/kimi-k2.5")
        if resolve_paper_extract_route(cfg)["provider_type"] != "openai_compatible":
            cfg["model_gateway"]["models"].append({
                "id": "kimi/kimi-k2.5",
                "label": "Kimi test",
                "provider_id": "kimi",
                "upstream_model": "kimi-k2.5",
                "model_kind": "chat",
                "supports_vision": False,
                "supported_tasks": ["paper_extract"],
                "builtin": False,
            })
            cfg = ensure_model_gateway_config(cfg)
        client, _ = self._client()
        with patch("services.paper_extract_route.load_config", return_value=cfg):
            response = client.post("/api/process")
            pipeline_response = client.post("/api/pipeline/run", json={"use_llm": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], UNSUPPORTED_ROUTE_CODE)
        self.assertEqual(pipeline_response.status_code, 400)
        self.assertEqual(self.queue.active(), [])

    def test_resume_requires_fresh_confirmation_when_route_is_file_search(self):
        job = self.queue.submit("papers", "papers", {"paper_ids": ["p1"]})
        self.queue.update(job["job_id"], status="failed", error="interrupted")
        client, _ = self._client()
        denied = client.post(f"/api/jobs/{job['job_id']}/resume")
        self.assertEqual(denied.status_code, 409)
        accepted = client.post(
            f"/api/jobs/{job['job_id']}/resume",
            json={"file_search_confirmation": self.confirm},
        )
        self.assertEqual(accepted.status_code, 202)
        stored = self.queue.get(job["job_id"])
        self.assertEqual(stored["payload"]["file_search_confirmation"], self.confirm)
        self.assertEqual(stored["status"], "queued")


class ExecutionGuardTests(unittest.TestCase):
    def test_process_papers_stops_before_marking_or_extracting_when_route_changed(self):
        stale, _ = _confirm(_make_cfg("openai/gpt-4o"))
        current = _make_cfg("openai/gpt-5.4")
        ctx = SimpleNamespace(checkpoints={}, watch=MagicMock(), step=MagicMock(), flush=MagicMock())
        with patch("services.paper_extract_route.load_config", return_value=current), \
             patch("database.assert_processing_schema"), \
             patch("routers.papers._mark_processing_started") as marked, \
             patch("routers.papers._process_single") as extract:
            with self.assertRaises(Exception) as caught:
                process_papers(ctx, {"paper_ids": ["p1"], "file_search_confirmation": stale})
        self.assertEqual(caught.exception.code, ROUTE_CHANGED_CODE)
        marked.assert_not_called()
        extract.assert_not_called()

    def test_process_papers_accepts_matching_confirmation_and_binds_context(self):
        from services.paper_extract_route import get_active_file_search_confirmation
        from routers import papers as papers_mod

        cfg = _make_cfg("openai/gpt-4o")
        confirm, _ = _confirm(cfg)
        old_state = dict(papers_mod.processing_state)
        self.addCleanup(papers_mod.processing_state.update, old_state)
        papers_mod.processing_state.update(
            done=0, succeeded=0, errors=0, failed_papers=[], running=False, current="", last_message=""
        )
        ctx = SimpleNamespace(checkpoints={"paper:p1": True}, watch=MagicMock(), step=MagicMock(), flush=MagicMock())
        with patch("services.paper_extract_route.load_config", return_value=cfg), \
             patch("database.assert_processing_schema"), \
             patch("routers.papers._mark_processing_started"), \
             patch("routers.papers._process_single") as extract, \
             patch("services.ask_generation_refresh.mark_and_enqueue_refresh"):
            process_papers(ctx, {"paper_ids": ["p1"], "file_search_confirmation": confirm})
        extract.assert_not_called()  # checkpointed paper skipped
        self.assertIsNone(get_active_file_search_confirmation())

    def test_pipeline_forwards_confirmation_into_extract_step(self):
        confirm, _ = _confirm()
        captured = {}

        def fake_process(ctx, payload):
            captured.update(payload)
            return {"succeeded": 0, "errors": 0}

        def step(key, fn):
            if key == "pending":
                return ["p1"]
            return fn()

        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        ctx = SimpleNamespace(checkpoints={}, flush=MagicMock(), phase=MagicMock(), step=step)
        with patch("services.task_executor.process_papers", side_effect=fake_process), \
             patch("database.SessionLocal", return_value=session), \
             patch("routers.papers.scan_papers", return_value={}), \
             patch("routers.papers.paper_work_counts", return_value={"failed_count": 0}), \
             patch("services.task_executor.promote", return_value={}), \
             patch("services.task_executor.compile_items", return_value={}), \
             patch("services.task_executor.lint", return_value={}), \
             patch("routers.promotion.accept_llm_proposals", return_value={}):
            pipeline(ctx, {"use_llm": True, "file_search_confirmation": confirm})
        self.assertEqual(captured.get("paper_ids"), ["p1"])
        self.assertEqual(captured.get("file_search_confirmation"), confirm)


class ProviderBindingTests(unittest.TestCase):
    def test_file_search_client_uses_settings_provider_not_positional_key(self):
        from services.vlm_service import extract_knowledge_from_paper

        cfg = _make_cfg("openai/gpt-4o", openai_key="provider-key", base_url="https://example.invalid/v1")
        confirm, route = _confirm(cfg)
        client = MagicMock()
        token = set_active_file_search_confirmation(confirm)
        try:
            with patch("services.vlm_service._resolve_model_gateway_context", return_value=(cfg, "openai")), \
                 patch("services.paper_extract_route.load_config", return_value=cfg), \
                 patch("services.vlm_service.load_config", return_value=cfg), \
                 patch("services.vlm_service.create_openai_client_for_model", return_value=(client, "gpt-4o", {"id": "openai", "provider_type": "openai"}, {"id": "openai/gpt-4o", "upstream_model": "gpt-4o"})) as create, \
                 patch("services.vlm_service._ensure_file", return_value="file-1"), \
                 patch("services.vlm_service._run_extraction_once", return_value=(
                     {
                         "title": "Example Paper",
                         "authors": ["Alice"],
                         "problem_area": "视觉",
                         "abstract_summary": "摘要",
                         "techniques": [{"name": "Transformer", "role": "主干", "aliases": [], "builds_on": []}],
                         "datasets": [{"name": "ImageNet", "purpose": "评测"}],
                         "baselines": ["CLIP"],
                         "key_findings": [{"short": "更强", "detail": "提升"}],
                     },
                     '{"title":"Example Paper"}',
                     "",
                     "thread-1",
                     "vs-1",
                 )), \
                 patch("services.vlm_service.build_pdf_evidence", side_effect=RuntimeError("audit skipped")):
                extract_knowledge_from_paper(
                    "dummy.pdf",
                    "PROMPT",
                    "ignored-legacy-key",
                    "gpt-4o",
                )
            create.assert_called_once()
            called_cfg, called_model = create.call_args[0][:2]
            self.assertEqual(called_model, "openai/gpt-4o")
            self.assertNotIn("api_key_override", create.call_args.kwargs)
            openai_provider = next(p for p in called_cfg["model_gateway"]["providers"] if p["id"] == "openai")
            self.assertEqual(openai_provider["api_key"], "provider-key")
            self.assertEqual(openai_provider["base_url"], "https://example.invalid/v1")
        finally:
            reset_active_file_search_confirmation(token)

    def test_extract_without_confirmation_does_not_create_openai_client(self):
        from model_gateway.runtime import ProviderConfigurationError
        from services.vlm_service import extract_knowledge_from_paper

        cfg = _make_cfg("openai/gpt-4o")
        with patch("services.vlm_service._resolve_model_gateway_context", return_value=(cfg, "openai")), \
             patch("services.paper_extract_route.load_config", return_value=cfg), \
             patch("services.vlm_service.load_config", return_value=cfg), \
             patch("services.vlm_service.create_openai_client_for_model") as create, \
             patch("services.vlm_service._ensure_file") as ensure_file:
            with self.assertRaises(ProviderConfigurationError):
                extract_knowledge_from_paper("dummy.pdf", "PROMPT", "test-openai-key", "gpt-4o")
        create.assert_not_called()
        ensure_file.assert_not_called()
