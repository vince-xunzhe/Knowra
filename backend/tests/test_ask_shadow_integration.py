from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ask_agent
from services.ask_shadow_integration import (
    AskShadowIntegrationError,
    _index_identity,
    observe_question,
    observation_trace_payload,
)
from services.hybrid_claim_retrieval import build_shadow_index
from services.paper_evidence_service import build_evidence_manifest
from services.wiki_shadow_compiler import build_paper_compile_plan


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class AskShadowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.live_data = self.root / "live-data"
        self.live_wiki = self.live_data / "wiki"
        self.live_wiki.mkdir(parents=True)
        self.shadow = self.root / "ask-shadow-generation"
        self.index = self.shadow / "indexes" / "claims.sqlite"
        sentence = (
            "In 2022, actions became explicit inputs to the latent transition dynamics."
        )
        self.manifest = build_evidence_manifest(
            paper_id="paper-1",
            source_file_hash=_hash(b"synthetic-paper"),
            source_size_bytes=512,
            total_pages=1,
            pages=[{
                "page_number": 1,
                "status": "ok",
                "text": (sentence + " ") * 8,
            }],
            parser_version="synthetic-integration@v1",
            chunk_chars=1_200,
            overlap_chars=50,
        )
        evidence_id = self.manifest["chunks"][0]["id"]
        self.plan = build_paper_compile_plan(
            paper_id="paper-1",
            title="Action Conditioned Dynamics",
            extraction={
                "title": "Action Conditioned Dynamics",
                "year": 2022,
                "innovations": {
                    "this_work": "Actions enter the latent transition dynamics."
                },
            },
            notes="",
            model="synthetic-no-provider",
            locale="zh",
            source_file_hash=self.manifest["source"]["file_hash_sha256"],
            evidence_links={"innovations.this_work": [evidence_id]},
            evidence_manifest=self.manifest,
        )
        claim_id = next(
            item["id"]
            for item in self.plan["bounded_payload"]["retrieval_claims"]
            if item["field_path"] == "innovations.this_work"
        )
        report = build_shadow_index(
            index_path=self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.live_wiki,
            compile_plans=[self.plan],
            evidence_manifests=[self.manifest],
            vectors_by_id={
                claim_id: [1.0, 0.0],
                evidence_id: [0.99, 0.01],
            },
            dense_model_id="synthetic-integration-vectors@v1",
        )
        self.generation = {
            "schema_version": 1,
            "generation_id": "fixture-generation-v1",
            "status": "ready",
            "index_relpath": "indexes/claims.sqlite",
            "source_digest": report["source_digest"],
            "dense_index_digest": report["dense_index_digest"],
            "dense_model_id": report["dense_model_id"],
            "index_sha256": _hash(self.index.read_bytes()),
            "index_bytes": self.index.stat().st_size,
            "production_eligible": False,
        }
        (self.shadow / "generation.json").write_text(
            json.dumps(self.generation, sort_keys=True),
            encoding="utf-8",
        )
        self.env = {
            "KNOWRA_ASK_SHADOW_MODE": "observe",
            "KNOWRA_ASK_SHADOW_ROOT": str(self.shadow),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _observe(self, adapter: str, **kwargs):
        return observe_question(
            "How do actions enter latent transition dynamics?",
            adapter=adapter,
            env=self.env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            **kwargs,
        )

    def test_default_off_does_not_resolve_or_read_a_generation(self):
        with mock.patch(
            "services.ask_shadow_integration._safe_generation_root",
            side_effect=AssertionError("disabled mode touched storage"),
        ):
            result = observe_question(
                "question",
                adapter="api",
                env={
                    "KNOWRA_ASK_SHADOW_MODE": "off",
                    "KNOWRA_ASK_SHADOW_ROOT": "/does/not/exist",
                },
                live_data_root=self.live_data,
                production_wiki_root=self.live_wiki,
            )
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["production_eligible"])
        self.assertEqual(result["candidates"], [])

    def test_cli_and_api_share_candidates_provenance_and_hard_budgets(self):
        before = {
            path.relative_to(self.shadow): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.shadow.rglob("*")
            if path.is_file()
        }
        cli = self._observe(
            "cli",
            query_vector=[1.0, 0.0],
            dense_model_id="synthetic-integration-vectors@v1",
        )
        api = self._observe(
            "api",
            query_vector=[1.0, 0.0],
            dense_model_id="synthetic-integration-vectors@v1",
        )
        after = {
            path.relative_to(self.shadow): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.shadow.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(cli["status"], "ready")
        self.assertFalse(cli["production_eligible"])
        self.assertEqual(cli["candidates"], api["candidates"])
        self.assertEqual(cli["candidate_locators"], api["candidate_locators"])
        self.assertEqual(cli["coverage"], api["coverage"])
        for field in (
            "max_search_calls", "max_result_characters", "max_elapsed_ms",
            "max_serialized_characters", "max_observation_elapsed_ms",
            "max_index_integrity_bytes",
        ):
            self.assertEqual(cli["budget"][field], api["budget"][field])
        self.assertLessEqual(
            cli["budget"]["observation_elapsed_ms"],
            cli["budget"]["max_observation_elapsed_ms"],
        )
        self.assertEqual(
            cli["budget"]["index_integrity_bytes"], self.index.stat().st_size * 2
        )
        self.assertTrue(cli["coverage"]["met"])
        self.assertTrue(cli["candidate_locators"])
        self.assertFalse(any("text" in item or "quote" in item for item in cli["candidates"]))
        payload = observation_trace_payload(cli)
        self.assertNotIn("Actions enter", payload)
        self.assertNotIn("query", payload)

    def test_two_stage_routes_are_shared_read_only_and_keep_shadow_status(self):
        before = {
            path.relative_to(self.shadow): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.shadow.rglob("*")
            if path.is_file()
        }
        routes = [
            "world model action latent dynamics",
            "action-conditioned world model",
        ]
        cli = observe_question(
            "How did world and action models change over time?",
            adapter="cli",
            env=self.env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            query_routes=routes,
        )
        api = observe_question(
            "How did world and action models change over time?",
            adapter="api",
            env=self.env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            query_routes=routes,
        )
        after = {
            path.relative_to(self.shadow): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.shadow.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(cli["status"], "ready")
        self.assertEqual(cli["candidates"], api["candidates"])
        self.assertEqual(cli["candidate_locators"], api["candidate_locators"])
        self.assertFalse(cli["production_eligible"])
        self.assertIn("source_drilldown_used", cli["coverage"])
        self.assertLessEqual(
            cli["budget"]["search_calls"], cli["budget"]["max_search_calls"]
        )

    def test_two_stage_routes_fail_closed_on_invalid_shape_or_detached_vector(self):
        invalid = observe_question(
            "question",
            adapter="cli",
            env=self.env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            query_routes=[1],
        )
        detached = observe_question(
            "question",
            adapter="api",
            env=self.env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            query_routes=["world model"],
            query_vector=[1.0, 0.0],
            dense_model_id="synthetic-integration-vectors@v1",
        )
        self.assertEqual(invalid["status"], "unavailable")
        self.assertEqual(detached["status"], "unavailable")

    def test_invalid_generation_fails_closed_without_touching_live_data(self):
        original = (self.shadow / "generation.json").read_text(encoding="utf-8")
        tampered = {**self.generation, "source_digest": "0" * 64}
        (self.shadow / "generation.json").write_text(
            json.dumps(tampered), encoding="utf-8"
        )
        result = self._observe("api")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "shadow_generation_invalid")
        self.assertEqual(list(self.live_data.rglob("*")), [self.live_wiki])

        (self.shadow / "generation.json").write_text(original, encoding="utf-8")
        overlap = observe_question(
            "question",
            adapter="api",
            env={
                "KNOWRA_ASK_SHADOW_MODE": "observe",
                "KNOWRA_ASK_SHADOW_ROOT": str(self.live_data),
            },
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
        )
        self.assertEqual(overlap["status"], "unavailable")

    def test_manifest_is_bound_to_actual_index_bytes(self):
        with sqlite3.connect(self.index) as conn:
            conn.execute("UPDATE records SET paper_id = ?", ("forged-paper",))
        result = self._observe("api")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "shadow_generation_invalid")

    def test_sqlite_failure_is_isolated_from_existing_ask(self):
        with sqlite3.connect(self.index) as conn:
            conn.execute("DROP TABLE search")
        self.generation["index_sha256"] = _hash(self.index.read_bytes())
        self.generation["index_bytes"] = self.index.stat().st_size
        (self.shadow / "generation.json").write_text(
            json.dumps(self.generation, sort_keys=True), encoding="utf-8"
        )
        result = self._observe("api")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "shadow_generation_invalid")

    def test_resigned_malformed_vector_isolated_on_lexical_production_path(self):
        with sqlite3.connect(self.index) as conn:
            conn.execute("UPDATE records SET vector = ?", ("null",))
        self.generation["index_sha256"] = _hash(self.index.read_bytes())
        self.generation["index_bytes"] = self.index.stat().st_size
        (self.shadow / "generation.json").write_text(
            json.dumps(self.generation, sort_keys=True), encoding="utf-8"
        )
        result = self._observe("api")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "shadow_generation_invalid")

    def test_index_hashing_obeys_the_observation_deadline(self):
        with mock.patch(
            "services.ask_shadow_integration.perf_counter", return_value=10.0
        ):
            with self.assertRaises(AskShadowIntegrationError):
                _index_identity(self.index, deadline=9.0)

    def test_parent_symlink_in_generation_root_fails_closed(self):
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(self.shadow.parent, target_is_directory=True)
        result = observe_question(
            "question",
            adapter="api",
            env={
                "KNOWRA_ASK_SHADOW_MODE": "observe",
                "KNOWRA_ASK_SHADOW_ROOT": str(linked_parent / self.shadow.name),
            },
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
        )
        self.assertEqual(result["status"], "unavailable")

    def test_manifest_requires_file_identity_fields(self):
        del self.generation["index_sha256"]
        (self.shadow / "generation.json").write_text(
            json.dumps(self.generation, sort_keys=True), encoding="utf-8"
        )
        result = self._observe("api")
        self.assertEqual(result["status"], "unavailable")

    def test_observe_mode_only_appends_candidate_telemetry_to_existing_answer(self):
        assessment = ask_agent.EvidenceAssessment(
            status="limited",
            required_content_sources=1,
            content_refs=("data/wiki/papers/0001-fixture.md",),
            graph_evidence_count=0,
            search_entity_terms=1,
            index_available=True,
            reason="synthetic existing evidence",
        )
        with mock.patch.dict(os.environ, {"KNOWRA_ASK_SHADOW_MODE": "off"}, clear=False):
            baseline = ask_agent._finish_result(
                answer="Existing answer",
                question="How do actions enter latent transition dynamics?",
                trace=[],
                model="codex-cli/synthetic",
                duration_ms=10,
                assessment=assessment,
            )
        real_observe = ask_agent.ask_shadow_integration.observe_question
        with (
            mock.patch.dict(os.environ, self.env, clear=False),
            mock.patch.object(
                ask_agent.ask_shadow_integration,
                "observe_question",
                side_effect=lambda question, *, adapter, query_routes=None: real_observe(
                    question,
                    adapter=adapter,
                    env=self.env,
                    live_data_root=self.live_data,
                    production_wiki_root=self.live_wiki,
                    query_routes=query_routes,
                ),
            ),
        ):
            observed = ask_agent._finish_result(
                answer="Existing answer",
                question="How do actions enter latent transition dynamics?",
                trace=[],
                model="codex-cli/synthetic",
                duration_ms=10,
                assessment=assessment,
            )
        self.assertEqual(observed.answer, baseline.answer)
        self.assertEqual(observed.citations, baseline.citations)
        self.assertEqual(observed.cited_files, baseline.cited_files)
        self.assertEqual([item.tool for item in baseline.trace], ["evidence_gate"])
        self.assertEqual(
            [item.tool for item in observed.trace],
            ["evidence_gate", "shadow_retrieval"],
        )
        shadow_payload = json.loads(observed.trace[-1].raw_result)
        self.assertFalse(shadow_payload["production_eligible"])
        self.assertNotIn("candidates", shadow_payload)
        self.assertNotIn("quote", observed.trace[-1].raw_result)
        self.assertEqual(
            observed.duration_ms,
            baseline.duration_ms + observed.trace[-1].duration_ms,
        )

    def test_finish_result_forwards_unique_search_routes_to_shadow_observer(self):
        trace = [
            ask_agent.TraceStep(0, "search_wiki", {"q": "世界模型"}, "one", 1),
            ask_agent.TraceStep(1, "search_wiki", {"q": "world model"}, "two", 1),
            ask_agent.TraceStep(2, "search_wiki", {"q": "世界模型"}, "duplicate", 1),
        ]
        assessment = ask_agent.EvidenceAssessment(
            status="insufficient",
            required_content_sources=1,
            content_refs=(),
            graph_evidence_count=0,
            search_entity_terms=1,
            index_available=True,
            reason="synthetic",
        )
        with mock.patch.object(
            ask_agent.ask_shadow_integration,
            "observe_question",
            return_value={"status": "disabled"},
        ) as observe:
            ask_agent._finish_result(
                answer="answer",
                question="question",
                trace=trace,
                model="codex-cli/synthetic",
                duration_ms=2,
                assessment=assessment,
            )
        self.assertEqual(observe.call_args.kwargs["adapter"], "cli")
        self.assertEqual(
            observe.call_args.kwargs["query_routes"],
            ["世界模型", "world model"],
        )


if __name__ == "__main__":
    unittest.main()
