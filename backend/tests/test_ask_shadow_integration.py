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
from services.ask_generation_control import promote_generation
from services.hybrid_claim_retrieval import build_shadow_index
from services.paper_evidence_service import (
    build_evidence_manifest,
    write_evidence_manifest,
)
from services.ask_retrieval_budget import RetrievalBudget
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
        write_evidence_manifest(
            self.manifest,
            self.shadow / "evidence" / "paper-1.json",
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
        self.control = self.root / "ask-generation-control"
        promote_generation(
            generation_root=self.shadow,
            control_root=self.control,
            live_data_root=self.live_data,
            promotion_id="fixture-review-v1",
            reviewer="synthetic-test",
            reviewed_at="2026-10-31T00:00:00Z",
            acceptance_sha256=_hash(b"synthetic-acceptance"),
            accepted_failed_pages=0,
            expected_active_sha256=None,
        )
        self.env = {
            "KNOWRA_ASK_SHADOW_MODE": "observe",
            "KNOWRA_ASK_SHADOW_ROOT": str(self.shadow),
        }
        self.assist_env = {
            "KNOWRA_ASK_SHADOW_MODE": "assist",
            "KNOWRA_ASK_GENERATION_CONTROL": str(self.control),
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

    def test_assist_mode_returns_bounded_exact_source_spans_but_trace_hides_them(self):
        env = self.assist_env
        result = observe_question(
            "How do actions enter latent transition dynamics?",
            adapter="cli",
            env=env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            query_routes=["action latent transition", "actions dynamics"],
            include_answer_evidence=True,
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["mode"], "assist")
        self.assertTrue(result["production_eligible"])
        self.assertEqual(result["reason"], "coverage_met")
        self.assertTrue(result["answer_evidence"])
        excerpt = result["answer_evidence"][0]
        self.assertIn("actions became explicit inputs", excerpt["quote"])
        self.assertEqual(
            excerpt["source_file_hash"], self.manifest["source"]["file_hash_sha256"]
        )
        self.assertLessEqual(
            len(excerpt["quote"]),
            ask_agent.ask_shadow_integration.MAX_ANSWER_EVIDENCE_CHARACTERS,
        )
        payload = observation_trace_payload(result)
        self.assertTrue(json.loads(payload)["production_eligible"])
        self.assertNotIn("actions became explicit inputs", payload)
        self.assertNotIn("answer_evidence", payload)

    def test_unpromoted_direct_root_cannot_enable_assist(self):
        env = {
            "KNOWRA_ASK_SHADOW_MODE": "assist",
            "KNOWRA_ASK_SHADOW_ROOT": str(self.shadow),
        }
        result = observe_question(
            "How do actions enter latent transition dynamics?",
            adapter="cli",
            env=env,
            live_data_root=self.live_data,
            production_wiki_root=self.live_wiki,
            include_answer_evidence=True,
        )

        self.assertFalse(
            ask_agent.ask_shadow_integration.answer_mode_enabled(
                env, live_data_root=self.live_data
            )
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "generation_control_missing")
        self.assertFalse(result["production_eligible"])
        assessment = ask_agent.EvidenceAssessment(
            status="insufficient",
            required_content_sources=1,
            content_refs=(),
            graph_evidence_count=0,
            search_entity_terms=0,
            index_available=True,
            reason="synthetic unpromoted generation",
        )
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(ask_agent, "call_text_model") as model_call,
        ):
            finished = ask_agent._finish_result(
                answer="",
                question="How do actions enter latent transition dynamics?",
                trace=[],
                model="codex-cli/synthetic",
                duration_ms=1,
                assessment=assessment,
                budget=RetrievalBudget(),
                cfg={"prompt_locale": "zh"},
            )
        model_call.assert_not_called()
        self.assertIn("## 证据不足", finished.answer)

    def test_answer_evidence_prefers_primary_sections_and_deduplicates_chunks(self):
        def item(
            evidence_id: str,
            *,
            paper_id: str,
            page: int,
            section: str,
            routes: list[int],
        ) -> dict:
            return {
                "id": evidence_id,
                "paper_id": paper_id,
                "locator": {"page_number": page, "section_kind": section},
                "route_indexes": routes,
                "route_score": 1 / 61,
            }

        selected = ask_agent.ask_shadow_integration._select_answer_evidence([
            item("related", paper_id="paper-a", page=8, section="related_work", routes=[0, 1, 2]),
            item("background", paper_id="paper-b", page=4, section="background", routes=[0, 1]),
            item("abstract", paper_id="paper-c", page=2, section="abstract", routes=[1]),
            item("weak-related", paper_id="paper-d", page=7, section="related_work", routes=[0]),
            item("core", paper_id="paper-a", page=6, section="introduction", routes=[0, 2]),
            item("core-overlap", paper_id="paper-a", page=6, section="introduction", routes=[0, 2]),
        ])

        self.assertEqual(
            [result["id"] for result in selected],
            ["core", "background", "abstract", "weak-related"],
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

    def test_assist_mode_plans_cli_routes_and_replaces_refusal_with_cited_source_answer(self):
        assessment = ask_agent.EvidenceAssessment(
            status="insufficient",
            required_content_sources=1,
            content_refs=(),
            graph_evidence_count=0,
            search_entity_terms=0,
            index_available=True,
            reason="synthetic",
        )
        evidence_id = self.manifest["chunks"][0]["id"]
        env = self.assist_env
        real_observe = ask_agent.ask_shadow_integration.observe_question

        def observe(question, *, adapter, query_routes=None, include_answer_evidence=False):
            return real_observe(
                question,
                adapter=adapter,
                env=env,
                live_data_root=self.live_data,
                production_wiki_root=self.live_wiki,
                query_routes=query_routes,
                include_answer_evidence=include_answer_evidence,
            )

        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(
                ask_agent.ask_shadow_integration,
                "observe_question",
                side_effect=observe,
            ),
            mock.patch.object(
                ask_agent,
                "call_text_model",
                side_effect=[
                    '["action latent transition", "actions dynamics"]',
                    f"## Answer\n\nActions enter the transition. [{evidence_id}]",
                ],
            ) as model_call,
        ):
            result = ask_agent._finish_result(
                answer="",
                question="How do actions enter latent transition dynamics?",
                trace=[
                    ask_agent.TraceStep(0, "search_wiki", {"q": "动作模型"}, "", 0),
                    ask_agent.TraceStep(1, "search_wiki", {"q": "潜在动力学"}, "", 0),
                ],
                model="codex-cli/synthetic",
                duration_ms=3,
                assessment=assessment,
                budget=RetrievalBudget(),
                cfg={"prompt_locale": "zh"},
            )

        self.assertEqual(model_call.call_count, 2)
        self.assertIn("Actions enter the transition", result.answer)
        self.assertIn("检索命中不等于自动完成蕴含判断", result.answer)
        self.assertEqual(result.citations[0]["evidence_id"], evidence_id)
        self.assertEqual(
            result.citations[0]["provenance_status"], "retrieved_source_text"
        )
        self.assertNotIn("relationship", result.citations[0])
        shadow_trace = next(item for item in result.trace if item.tool == "shadow_retrieval")
        self.assertNotIn("Actions enter", shadow_trace.raw_result)
        self.assertEqual(
            shadow_trace.args["query_routes"],
            ["action latent transition", "actions dynamics"],
        )

    def test_evolution_route_prompt_requires_stage_diversity_without_domain_hardcoding(self):
        prompt = ask_agent.SHADOW_ROUTE_SYSTEM_PROMPT
        self.assertIn("中英双语宽主题锚点", prompt)
        self.assertIn("早期定义或基线", prompt)
        self.assertIn("中间机制或效率改进", prompt)
        self.assertIn("后续替代或推广", prompt)
        self.assertIn("不要编造具体方法名", prompt)
        self.assertIn("宽主题问题不得缩成某个下游任务", prompt)
        self.assertNotIn("DDIM", prompt)
        self.assertNotIn("扩散模型", prompt)

    def test_assist_mode_unknown_model_citation_fails_closed_to_legacy_refusal(self):
        assessment = ask_agent.EvidenceAssessment(
            status="insufficient",
            required_content_sources=1,
            content_refs=(),
            graph_evidence_count=0,
            search_entity_terms=0,
            index_available=True,
            reason="synthetic refusal",
        )
        evidence_id = self.manifest["chunks"][0]["id"]
        observation = {
            "status": "ready",
            "mode": "assist",
            "reason": "coverage_met",
            "generation_id": "fixture",
            "coverage": {"met": True},
            "budget": {},
            "candidates": [],
            "candidate_locators": [],
            "answer_evidence": [{
                "evidence_id": evidence_id,
                "paper_id": "paper-1",
                "quote": "Actions enter the transition.",
            }],
        }
        unknown = "evidence:" + "f" * 64
        with (
            mock.patch.dict(os.environ, self.assist_env, clear=False),
            mock.patch.object(
                ask_agent.ask_shadow_integration,
                "observe_question",
                return_value=observation,
            ),
            mock.patch.object(
                ask_agent,
                "call_text_model",
                return_value=f"Unsupported claim [{unknown}]",
            ),
        ):
            result = ask_agent._finish_result(
                answer="",
                question="question",
                trace=[ask_agent.TraceStep(
                    0, "search_wiki", {"q": "route one"}, "", 0
                )],
                model="api/synthetic",
                duration_ms=1,
                assessment=assessment,
                budget=RetrievalBudget(),
                cfg={"prompt_locale": "zh"},
            )

        self.assertEqual(result.citations, [])
        self.assertIn("## 证据不足", result.answer)
        self.assertFalse(any(item.tool == "shadow_answer" for item in result.trace))

    def test_assist_mode_incomplete_retrieval_never_calls_answer_model(self):
        assessment = ask_agent.EvidenceAssessment(
            status="insufficient",
            required_content_sources=1,
            content_refs=(),
            graph_evidence_count=0,
            search_entity_terms=0,
            index_available=True,
            reason="synthetic incomplete retrieval",
        )
        observation = {
            "status": "ready",
            "mode": "assist",
            "reason": "retrieval_coverage_incomplete",
            "generation_id": "fixture",
            "coverage": {"met": False},
            "budget": {},
            "candidates": [],
            "candidate_locators": [],
            "answer_evidence": [],
        }
        with (
            mock.patch.dict(os.environ, self.assist_env, clear=False),
            mock.patch.object(
                ask_agent.ask_shadow_integration,
                "observe_question",
                return_value=observation,
            ),
            mock.patch.object(ask_agent, "call_text_model") as model_call,
        ):
            result = ask_agent._finish_result(
                answer="",
                question="question",
                trace=[ask_agent.TraceStep(
                    0, "search_wiki", {"q": "route one"}, "", 0
                )],
                model="api/synthetic",
                duration_ms=1,
                assessment=assessment,
                budget=RetrievalBudget(),
                cfg={"prompt_locale": "zh"},
            )

        model_call.assert_not_called()
        self.assertEqual(result.citations, [])
        self.assertIn("## 证据不足", result.answer)

    def test_assist_answer_requires_each_substantive_list_item_to_be_cited(self):
        evidence_id = self.manifest["chunks"][0]["id"]
        answer = (
            f"- First substantive claim has a valid source citation. [{evidence_id}]\n"
            "- Second substantive claim is deliberately left without a source citation."
        )
        self.assertFalse(
            ask_agent._shadow_answer_paragraphs_are_cited(answer, {evidence_id})
        )


if __name__ == "__main__":
    unittest.main()
