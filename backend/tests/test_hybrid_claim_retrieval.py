from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hybrid_claim_retrieval import (
    HybridClaimRetriever,
    HybridRetrievalError,
    build_retrieval_plan,
    build_shadow_index,
    execute_api_plan,
    execute_cli_plan,
)
from services.paper_evidence_service import build_evidence_manifest
from services import hybrid_claim_retrieval
from services import wiki_shadow_compiler
from services.wiki_shadow_compiler import (
    ShadowCompileError,
    build_concept_compile_plan,
    build_paper_compile_plan,
)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest(paper_id: str, sentence: str) -> dict:
    text = (sentence + " ") * 12
    return build_evidence_manifest(
        paper_id=paper_id,
        source_file_hash=_hash(f"{paper_id}-pdf".encode()),
        source_size_bytes=512,
        total_pages=1,
        pages=[{"page_number": 1, "status": "ok", "text": text}],
        parser_version="synthetic-1",
        chunk_chars=600,
        overlap_chars=50,
    )


def _extraction(title: str, change: str) -> dict:
    return {
        "title": title,
        "problem": "How should a model represent change?",
        "innovations": {
            "previous_work": "Earlier systems used a different factorization.",
            "this_work": change,
            "why_better": "The representation enables a more direct test.",
        },
        "historical_position": {"overall": change},
        "limitations": "The synthetic evaluation is deliberately narrow.",
    }


def _plan(
    paper_id: str,
    title: str,
    change: str,
    manifest: dict,
    *,
    link_change: bool = False,
) -> dict:
    links = (
        {"innovations.this_work": [manifest["chunks"][0]["id"]]}
        if link_change
        else None
    )
    return build_paper_compile_plan(
        paper_id=paper_id,
        title=title,
        extraction=_extraction(title, change),
        notes="",
        model="synthetic-model",
        locale="zh",
        source_file_hash=manifest["source"]["file_hash_sha256"],
        evidence_links=links,
        evidence_manifest=manifest if link_change else None,
    )


class HybridClaimRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.shadow = self.root / "shadow"
        self.production = self.root / "production-wiki"
        self.production.mkdir()
        self.index = self.shadow / "indexes" / "claims.sqlite"

        self.passive_manifest = _manifest(
            "paper-passive",
            "Earlier world models predicted observations without action-conditioned transitions.",
        )
        self.action_manifest = _manifest(
            "paper-action",
            "Later systems put actions inside the latent transition and rollout dynamics.",
        )
        self.negative_manifest = _manifest(
            "paper-boundary",
            "Boundary estimation fits geometric contours and manufacturing tolerances.",
        )
        self.passive = _plan(
            "paper-passive",
            "Passive Observation Predictor",
            "Prediction and action remained separate components.",
            self.passive_manifest,
        )
        self.action = _plan(
            "paper-action",
            "Action Conditioned Dynamics",
            "Actions became explicit inputs to the learned latent transition model.",
            self.action_manifest,
            link_change=True,
        )
        self.negative = _plan(
            "paper-boundary",
            "Boundary Geometry Model",
            "A contour model changed its spline interpolation order.",
            self.negative_manifest,
        )

        def claim(plan: dict, path: str) -> str:
            return next(
                item["id"]
                for item in plan["bounded_payload"]["retrieval_claims"]
                if item["field_path"] == path
            )

        self.passive_claim = claim(self.passive, "innovations.this_work")
        self.action_claim = claim(self.action, "innovations.this_work")
        self.negative_claim = claim(self.negative, "innovations.this_work")
        self.action_evidence = self.action_manifest["chunks"][0]["id"]
        vectors = {
            self.passive_claim: [0.98, 0.02, 0.0],
            self.action_claim: [1.0, 0.0, 0.0],
            self.action_evidence: [0.96, 0.04, 0.0],
            self.negative_claim: [0.0, 1.0, 0.0],
        }
        self.report = build_shadow_index(
            index_path=self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
            compile_plans=[self.passive, self.action, self.negative],
            evidence_manifests=[
                self.passive_manifest,
                self.action_manifest,
                self.negative_manifest,
            ],
            vectors_by_id=vectors,
            dense_model_id="synthetic-vectors-v1@fixture",
        )
        self.retriever = HybridClaimRetriever(
            self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_index_is_shadow_only_and_preserves_provenance_states(self):
        self.assertGreater(self.report["claims"], 0)
        self.assertGreater(self.report["evidence_chunks"], 0)
        self.assertEqual(self.report["dense_model_id"], "synthetic-vectors-v1@fixture")
        self.assertFalse(any(self.production.iterdir()))
        claim = self.retriever.search(
            "explicit inputs learned latent transition",
            kinds=["claim"],
            paper_ids=["paper-action"],
        )["results"][0]
        self.assertEqual(claim["id"], self.action_claim)
        self.assertEqual(claim["support_status"], "candidate_evidence")
        self.assertEqual(claim["locator"]["candidate_evidence_ids"], [self.action_evidence])
        source = self.retriever.search(
            "latent transition rollout dynamics",
            kinds=["evidence"],
            support_statuses=["source_text"],
        )["results"][0]
        self.assertEqual(source["support_status"], "source_text")
        self.assertIn("page_number", source["locator"])

    def test_long_cross_paper_question_uses_dense_without_promoting_support(self):
        lexical_only = self.retriever.search(
            "世界模型与动作模型的结合，在建模上经历了哪些变化？",
            limit=4,
        )
        result = self.retriever.search(
            "世界模型与动作模型的结合，在建模上经历了哪些变化？",
            query_vector=[1.0, 0.0, 0.0],
            dense_model_id="synthetic-vectors-v1@fixture",
            limit=4,
        )
        ids = [item["id"] for item in result["results"]]
        self.assertIn(self.action_claim, ids)
        self.assertIn(self.passive_claim, ids)
        self.assertIn(self.action_evidence, ids)
        self.assertNotIn(self.negative_claim, ids)
        relevant = {self.action_claim, self.passive_claim, self.action_evidence}
        lexical_recall = len(relevant & {
            item["id"] for item in lexical_only["results"]
        }) / len(relevant)
        hybrid_recall = len(relevant & set(ids)) / len(relevant)
        self.assertEqual(lexical_recall, 0.0)
        self.assertEqual(hybrid_recall, 1.0)
        statuses = {item["id"]: item["support_status"] for item in result["results"]}
        self.assertEqual(statuses[self.action_claim], "candidate_evidence")
        self.assertEqual(statuses[self.action_evidence], "source_text")

    def test_exact_lexical_and_hard_negative_no_answer_controls(self):
        exact = self.retriever.search(
            "Action Conditioned Dynamics", kinds=["claim"], limit=5
        )
        self.assertTrue(exact["results"])
        self.assertTrue(all(
            item["paper_id"] == "paper-action" for item in exact["results"]
        ))
        no_answer = self.retriever.search("zephyr qzxv unsupported facet")
        self.assertEqual(no_answer["results"], [])
        negative = self.retriever.search(
            "manufacturing spline contour", kinds=["claim"], limit=3
        )
        self.assertTrue(negative["results"])
        self.assertTrue(all(
            item["paper_id"] == "paper-boundary" for item in negative["results"]
        ))

    def test_lexical_only_search_hydrates_only_bounded_fts_candidates(self):
        with mock.patch(
            "services.hybrid_claim_retrieval._validate_record",
            wraps=hybrid_claim_retrieval._validate_record,
        ) as validate_record:
            result = self.retriever.search(
                "Action Conditioned Dynamics", kinds=["claim"], limit=5
            )
        self.assertTrue(result["results"])
        self.assertEqual(validate_record.call_count, result["lexical_candidates"])
        self.assertLess(validate_record.call_count, self.report["records"])

    def test_cjk_natural_question_recalls_terms_without_hard_coding_topics(self):
        chinese_root = self.root / "chinese-shadow"
        chinese_index = chinese_root / "indexes" / "claims.sqlite"
        relevant = _plan(
            "paper-chinese-world", "动作条件世界模型",
            "世界模型把动作作为潜在状态转移的显式输入。",
            _manifest("paper-chinese-world", "Actions condition latent transitions."),
        )
        unrelated = _plan(
            "paper-chinese-factory", "制造业轮廓模型",
            "制造业动作规范改变了样条轮廓的公差。",
            _manifest("paper-chinese-factory", "Manufacturing spline tolerance."),
        )
        build_shadow_index(
            index_path=chinese_index,
            shadow_root=chinese_root,
            production_wiki_root=self.production,
            compile_plans=[relevant, unrelated],
        )
        retriever = HybridClaimRetriever(
            chinese_index,
            shadow_root=chinese_root,
            production_wiki_root=self.production,
        )
        natural = retriever.search(
            "世界模型与动作模型的结合，在建模上经历了哪些变化？",
            kinds=["claim"], limit=5,
        )
        self.assertTrue(natural["results"])
        self.assertEqual(natural["results"][0]["paper_id"], "paper-chinese-world")
        absent = retriever.search(
            "量子引力中的全息纠缠熵如何计算？", kinds=["claim"], limit=5
        )
        self.assertEqual(absent["results"], [])

    def test_cli_and_api_share_one_bounded_plan_and_single_gap(self):
        plan = build_retrieval_plan(
            question="How did prediction become action conditioned?",
            routes=[{
                "query": "Prediction and action remained separate components",
                "kinds": ["claim"],
            }],
            gap_route={
                "query": "actions inside latent transition rollout dynamics",
                "kinds": ["evidence"],
            },
            required_papers=2,
            required_source_records=1,
            limit=5,
        )
        cli = execute_cli_plan(self.retriever, plan)
        api = execute_api_plan(self.retriever, plan)
        self.assertEqual(
            [item["id"] for item in cli["results"]],
            [item["id"] for item in api["results"]],
        )
        self.assertEqual(
            [route["name"] for route in cli["routes"]],
            [route["name"] for route in api["routes"]],
        )
        self.assertEqual(cli["budget"]["search_calls"], 2)
        self.assertTrue(cli["budget"]["gap_used"])
        self.assertLessEqual(cli["budget"]["search_calls"], 5)
        self.assertIn("never asserts entailment", cli["evidence_semantics"])
        self.assertEqual(cli["corpus_signature"], self.report["source_digest"])

    def test_no_answer_plan_stops_without_gap_or_fake_evidence(self):
        plan = build_retrieval_plan(
            question="Does an absent synthetic claim exist?",
            routes=[{"query": "zephyr qzxv unsupported facet"}],
            required_source_records=0,
        )
        result = execute_cli_plan(self.retriever, plan)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["budget"]["search_calls"], 1)
        self.assertFalse(result["budget"]["gap_used"])
        self.assertFalse(result["answerable"])
        self.assertFalse(result["coverage"]["met"])

        with_gap = build_retrieval_plan(
            question="Still absent",
            routes=[{"query": "zephyr qzxv unsupported facet"}],
            gap_route={"query": "still no matching synthetic claim"},
            required_papers=2,
            required_source_records=1,
        )
        exhausted = execute_api_plan(self.retriever, with_gap)
        self.assertTrue(exhausted["budget"]["gap_used"])
        self.assertEqual(exhausted["budget"]["search_calls"], 2)
        self.assertFalse(exhausted["coverage"]["met"])
        self.assertEqual(
            exhausted["answerability_reason"], "retrieval_coverage_incomplete"
        )

    def test_plan_and_signed_ledger_tampering_fail_closed(self):
        plan = build_retrieval_plan(
            question="question", routes=[{"query": "transition model"}]
        )
        tampered_plan = copy.deepcopy(plan)
        tampered_plan["routes"][0]["query"] = "altered"
        with self.assertRaisesRegex(HybridRetrievalError, "altered"):
            execute_cli_plan(self.retriever, tampered_plan)

        tampered_compile = copy.deepcopy(self.action)
        tampered_compile["bounded_payload"]["retrieval_claims"][0]["text"] += " changed"
        with self.assertRaisesRegex(HybridRetrievalError, "compile plan failed"):
            build_shadow_index(
                index_path=self.shadow / "tampered.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[tampered_compile],
            )

        internally_inconsistent = copy.deepcopy(self.passive)
        ledger = internally_inconsistent["bounded_payload"]["retrieval_claims"]
        changed = next(item for item in ledger if item["id"] == self.passive_claim)
        changed["evidence_status"] = "candidate_evidence"
        changed["candidate_evidence_ids"] = [f"evidence:{'0' * 64}"]
        wiki_shadow_compiler._finalize_plan(internally_inconsistent)
        with self.assertRaisesRegex(HybridRetrievalError, "conflicts with validated catalog"):
            build_shadow_index(
                index_path=self.shadow / "inconsistent.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[internally_inconsistent],
            )

        forged_text = copy.deepcopy(self.passive)
        forged_claim = next(
            item for item in forged_text["bounded_payload"]["retrieval_claims"]
            if item["id"] == self.passive_claim
        )
        forged_claim["text"] = "forged searchable claim"
        forged_claim["retained_text_hash"] = hashlib.sha256(
            forged_claim["text"].encode()
        ).hexdigest()
        wiki_shadow_compiler._finalize_plan(forged_text)
        with self.assertRaisesRegex(HybridRetrievalError, "validated catalog"):
            build_shadow_index(
                index_path=self.shadow / "forged-text.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[forged_text],
            )

        forged_title = copy.deepcopy(self.passive)
        forged_title["title"] = "Forged index title"
        for claim in forged_title["bounded_payload"]["retrieval_claims"]:
            claim["paper_title"] = "Forged index title"
        wiki_shadow_compiler._finalize_plan(forged_title)
        with self.assertRaisesRegex(HybridRetrievalError, "identity conflicts"):
            build_shadow_index(
                index_path=self.shadow / "forged-title.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[forged_title],
            )

        concept_identity = build_concept_compile_plan(
            concept_id="signed-concept",
            title="Signed concept title",
            description="",
            source_papers=[{
                "paper_id": "paper-action",
                "title": "Action Conditioned Dynamics",
                "extraction": _extraction(
                    "Action Conditioned Dynamics",
                    "Actions became explicit inputs to the learned latent transition model.",
                ),
            }],
            model="synthetic-model",
            locale="zh",
        )
        concept_identity["entity_id"] = "forged-concept"
        with self.assertRaisesRegex(HybridRetrievalError, "identity conflicts"):
            build_shadow_index(
                index_path=self.shadow / "forged-concept.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[concept_identity],
            )

        wrong_source = copy.deepcopy(self.action_manifest)
        wrong_source["source"]["file_hash_sha256"] = "0" * 64
        with self.assertRaisesRegex(ShadowCompileError, "source hash"):
            build_paper_compile_plan(
                paper_id="paper-action",
                title="Action Conditioned Dynamics",
                extraction={"title": "Action Conditioned Dynamics"},
                notes="",
                model="synthetic-model",
                locale="zh",
                source_file_hash=self.action_manifest["source"]["file_hash_sha256"],
                evidence_manifest=wrong_source,
            )

    def test_dimension_budget_and_path_contracts_fail_closed(self):
        with self.assertRaisesRegex(HybridRetrievalError, "dimension"):
            self.retriever.search("transition", query_vector=[1.0, 0.0])
        with self.assertRaisesRegex(HybridRetrievalError, "route count"):
            build_retrieval_plan(
                question="q",
                routes=[{"query": f"q{index}"} for index in range(5)],
            )
        with self.assertRaisesRegex(HybridRetrievalError, "stale"):
            HybridClaimRetriever(
                self.index,
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                expected_source_digest="0" * 64,
            )
        with self.assertRaisesRegex(HybridRetrievalError, "dimension budget"):
            self.retriever.search("transition", query_vector=[1.0] * 4_097)
        with self.assertRaisesRegex(HybridRetrievalError, "filter budget"):
            self.retriever.search(
                "transition", paper_ids=[f"paper-{index}" for index in range(51)]
            )
        with self.assertRaisesRegex(HybridRetrievalError, "below shadow root"):
            build_shadow_index(
                index_path=self.root / "outside.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[self.action],
            )

    def test_vectors_require_versioned_provider_metadata(self):
        with self.assertRaisesRegex(HybridRetrievalError, "model and revision"):
            build_shadow_index(
                index_path=self.shadow / "missing-model.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                compile_plans=[self.action],
                vectors_by_id={self.action_claim: [1.0, 0.0, 0.0]},
            )
        with self.assertRaisesRegex(HybridRetrievalError, "does not match"):
            self.retriever.search(
                "transition",
                query_vector=[1.0, 0.0, 0.0],
                dense_model_id="other-model@v1",
            )

    def test_dense_signature_binds_vectors_and_search_has_a_deadline(self):
        second = self.shadow / "different-vectors.sqlite"
        report = build_shadow_index(
            index_path=second,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
            compile_plans=[self.action],
            vectors_by_id={self.action_claim: [0.8, 0.2, 0.0]},
            dense_model_id="synthetic-vectors-v1@fixture",
        )
        self.assertNotEqual(
            report["dense_index_digest"], self.report["dense_index_digest"]
        )
        with mock.patch(
            "services.hybrid_claim_retrieval.perf_counter",
            side_effect=[0.0] + [1.0] * 2_000,
        ):
            with self.assertRaisesRegex(HybridRetrievalError, "latency budget"):
                self.retriever.search("transition", timeout_ms=1)

    def test_retriever_rejects_an_atomic_index_generation_change(self):
        build_shadow_index(
            index_path=self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
            compile_plans=[self.action],
            vectors_by_id={self.action_claim: [0.8, 0.2, 0.0]},
            dense_model_id="synthetic-vectors-v1@fixture",
        )
        with self.assertRaisesRegex(HybridRetrievalError, "changed after"):
            self.retriever.search("transition")

    def test_corpus_and_record_character_caps_are_enforced(self):
        with mock.patch("services.hybrid_claim_retrieval.MAX_INDEX_RECORDS", 1):
            with self.assertRaisesRegex(HybridRetrievalError, "record budget"):
                build_shadow_index(
                    index_path=self.shadow / "too-many.sqlite",
                    shadow_root=self.shadow,
                    production_wiki_root=self.production,
                    compile_plans=[self.action],
                )
        with mock.patch("services.hybrid_claim_retrieval.MAX_LOCATIONS_PER_RECORD", 1):
            concept = build_concept_compile_plan(
                concept_id="location-cap",
                title="Action-conditioned world models",
                description="",
                source_papers=[{
                    "paper_id": "paper-action",
                    "title": "Action Conditioned Dynamics",
                    "extraction": _extraction(
                        "Action Conditioned Dynamics",
                        "Actions became explicit inputs to the learned latent transition model.",
                    ),
                    "evidence_links": {"innovations.this_work": [self.action_evidence]},
                    "evidence_manifest": self.action_manifest,
                }],
                model="synthetic-model",
                locale="zh",
            )
            with self.assertRaisesRegex(HybridRetrievalError, "provenance exceeds"):
                build_shadow_index(
                    index_path=self.shadow / "too-many-locations.sqlite",
                    shadow_root=self.shadow,
                    production_wiki_root=self.production,
                    compile_plans=[self.action, concept],
                )
        with mock.patch(
            "services.hybrid_claim_retrieval.MAX_RECORD_METADATA_CHARACTERS", 500
        ):
            with self.assertRaisesRegex(HybridRetrievalError, "provenance exceeds"):
                build_shadow_index(
                    index_path=self.shadow / "merged-metadata.sqlite",
                    shadow_root=self.shadow,
                    production_wiki_root=self.production,
                    compile_plans=[self.action, concept],
                )
        oversized = build_evidence_manifest(
            paper_id="oversized",
            source_file_hash=_hash(b"oversized-pdf"),
            source_size_bytes=20_000,
            total_pages=1,
            pages=[{
                "page_number": 1,
                "status": "ok",
                "text": "oversized evidence " * 700,
            }],
            parser_version="synthetic-1",
            chunk_chars=20_000,
            overlap_chars=0,
        )
        with self.assertRaisesRegex(HybridRetrievalError, "character budget"):
            build_shadow_index(
                index_path=self.shadow / "oversized.sqlite",
                shadow_root=self.shadow,
                production_wiki_root=self.production,
                evidence_manifests=[oversized],
            )

        with mock.patch(
            "services.hybrid_claim_retrieval.MAX_SERIALIZED_SEARCH_CHARACTERS", 1
        ):
            with self.assertRaisesRegex(HybridRetrievalError, "serialized output"):
                self.retriever.search("transition")
        plan = build_retrieval_plan(
            question="transition",
            routes=[{"query": "transition"}],
            required_source_records=0,
        )
        with mock.patch(
            "services.hybrid_claim_retrieval.MAX_SERIALIZED_PLAN_CHARACTERS", 1
        ):
            with self.assertRaisesRegex(HybridRetrievalError, "serialized budget"):
                execute_cli_plan(self.retriever, plan)
        with self.assertRaisesRegex(HybridRetrievalError, "overlap"):
            build_shadow_index(
                index_path=self.production / "claim.sqlite",
                shadow_root=self.production,
                production_wiki_root=self.production,
                compile_plans=[self.action],
            )

    def test_paper_and_concept_locations_share_one_canonical_claim(self):
        change = "Actions became explicit inputs to the learned latent transition model."
        concept = build_concept_compile_plan(
            concept_id="concept-action",
            title="Action-conditioned world models",
            description="Synthetic concept.",
            source_papers=[{
                "paper_id": "paper-action",
                "title": "Action Conditioned Dynamics",
                "extraction": _extraction("Action Conditioned Dynamics", change),
                "evidence_links": {
                    "innovations.this_work": [self.action_evidence]
                },
                "evidence_manifest": self.action_manifest,
            }],
            model="synthetic-model",
            locale="zh",
        )
        path = self.shadow / "paper-concept.sqlite"
        report = build_shadow_index(
            index_path=path,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
            compile_plans=[self.action, concept],
            vectors_by_id={self.action_claim: [1.0, 0.0, 0.0]},
            dense_model_id="synthetic-vectors-v1@fixture",
        )
        retriever = HybridClaimRetriever(
            path,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
        )
        result = retriever.search(
            "explicit inputs learned latent transition",
            kinds=["claim"],
            paper_ids=["paper-action"],
        )["results"]
        canonical = next(item for item in result if item["id"] == self.action_claim)
        self.assertEqual(len(canonical["source_signatures"]), 2)
        self.assertEqual(
            {item["plan_kind"] for item in canonical["locations"]},
            {"paper", "concept"},
        )
        self.assertEqual(report["records"], report["claims"])

    def test_repeated_routes_do_not_multiply_scores_and_traces_are_compact(self):
        plan = build_retrieval_plan(
            question="action transition",
            routes=[
                {"query": "explicit inputs learned latent transition", "kinds": ["claim"]},
                {"query": "Action Conditioned Dynamics", "kinds": ["claim"]},
            ],
            required_source_records=0,
        )
        result = execute_cli_plan(self.retriever, plan)
        action = next(item for item in result["results"] if item["id"] == self.action_claim)
        self.assertLessEqual(action["plan_score"], round(1 / 61, 9))
        self.assertTrue(all(
            "text" not in item
            for route in result["routes"]
            for item in route["results"]
        ))
        self.assertLessEqual(
            result["budget"]["result_characters"],
            result["budget"]["max_result_characters"],
        )

    def test_coverage_selector_jointly_uses_source_and_paper_diversity(self):
        def item(identifier: str, paper_id: str, score: float) -> dict:
            return {
                "id": identifier,
                "paper_id": paper_id,
                "kind": "evidence",
                "support_status": "source_text",
                "title": identifier,
                "text": "source text",
                "text_hash": "0" * 64,
                "text_characters": 11,
                "text_truncated": False,
                "field_path": "page:1/body",
                "source_signatures": ["manifest:" + "0" * 64],
                "locations": [],
                "locator": {},
                "score": score,
                "channels": ["lexical"],
                "channel_ranks": {"lexical": 1},
            }

        class StubRetriever:
            source_digest = "1" * 64
            dense_index_digest = "2" * 64
            dense_model_id = "unavailable"

            def search(self, *_args, **_kwargs):
                results = [
                    item("a", "paper-a", 0.03),
                    item("b", "paper-a", 0.02),
                    item("c", "paper-b", 0.01),
                ]
                return {
                    "query": "q",
                    "channels_used": ["lexical"],
                    "results": results,
                    "lexical_candidates": 3,
                    "dense_candidates": 0,
                    "dense_status": "unavailable",
                    "dense_model_id": "unavailable",
                    "corpus_signature": self.source_digest,
                    "dense_index_signature": self.dense_index_digest,
                    "result_characters": 33,
                    "serialized_characters": 1_000,
                    "elapsed_ms": 0.1,
                }

        plan = build_retrieval_plan(
            question="q",
            routes=[{"query": "q"}],
            required_papers=2,
            required_source_records=2,
            limit=2,
        )
        result = execute_cli_plan(StubRetriever(), plan)
        self.assertEqual({item["paper_id"] for item in result["results"]}, {"paper-a", "paper-b"})
        self.assertTrue(result["coverage"]["met"])

    def test_index_metadata_rejects_an_unrelated_database(self):
        unrelated = self.shadow / "not-index.sqlite"
        unrelated.write_text(json.dumps({"not": "sqlite"}), encoding="utf-8")
        with self.assertRaisesRegex(HybridRetrievalError, "shadow retrieval index"):
            HybridClaimRetriever(
                unrelated,
                shadow_root=self.shadow,
                production_wiki_root=self.production,
            )


if __name__ == "__main__":
    unittest.main()
