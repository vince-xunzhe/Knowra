from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hybrid_claim_retrieval import HybridClaimRetriever, build_shadow_index
from services.paper_evidence_service import build_evidence_manifest
from services.two_stage_shadow_retrieval import (
    _evidence_anchor_query,
    _matches_primary_anchors,
    execute_two_stage_shadow_retrieval,
)
from services.wiki_shadow_compiler import build_paper_compile_plan


def manifest(paper_id: str, text: str):
    import hashlib
    return build_evidence_manifest(
        paper_id=paper_id,
        source_file_hash=hashlib.sha256(paper_id.encode()).hexdigest(),
        source_size_bytes=100,
        total_pages=1,
        pages=[{"page_number": 1, "status": "ok", "text": text * 8}],
        parser_version="synthetic@test",
    )


def plan(paper_id: str, change: str, source: dict, year: int):
    return build_paper_compile_plan(
        paper_id=paper_id,
        title=paper_id,
        extraction={"title": paper_id, "year": year, "innovations": {"this_work": change}},
        notes="",
        model="synthetic",
        locale="zh",
        source_file_hash=source["source"]["file_hash_sha256"],
        evidence_manifest=source,
    )


class TwoStageShadowRetrievalTests(unittest.TestCase):
    def test_translated_topic_anchor_rejects_generic_sampling_and_markov_text(self):
        queries = [
            "扩散模型采样方法的演进与主要改进综述",
            "diffusion model sampling early DDPM reverse diffusion Markov chain",
        ]
        anchor = _evidence_anchor_query(queries)
        self.assertFalse(_matches_primary_anchors(anchor, {
            "title": "Generic generative modeling",
            "text": "Sampling requires a reverse Markov chain for inference.",
        }))
        self.assertTrue(_matches_primary_anchors(anchor, {
            "title": "Denoising diffusion probabilistic models",
            "text": "DDPM uses a reverse Markov chain for diffusion sampling.",
        }))
        self.assertFalse(_matches_primary_anchors(anchor, {
            "title": "Diffusion models for monocular depth estimation",
            "text": "A single-step deterministic model is fast and accurate.",
        }))

    def test_balances_sources_and_drops_reference_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shadow, production = root / "shadow", root / "production"
            production.mkdir()
            first = manifest("paper-a", "Method\nActions condition latent world dynamics. ")
            second = manifest("paper-b", "Results\nWorld models learn imagined action outcomes. ")
            references = manifest("paper-ref", "References\nWorld model action model citation. ")
            index = shadow / "index.sqlite"
            build_shadow_index(
                index_path=index,
                shadow_root=shadow,
                production_wiki_root=production,
                compile_plans=[
                    plan("paper-a", "世界模型使用动作模型条件化潜在动力学", first, 2018),
                    plan("paper-b", "世界模型与动作模型学习动作结果", second, 2024),
                    plan("paper-ref", "世界模型动作模型引用", references, 2022),
                ],
                evidence_manifests=[first, second, references],
            )
            result = execute_two_stage_shadow_retrieval(
                HybridClaimRetriever(index, shadow_root=shadow, production_wiki_root=production),
                question="世界模型与动作模型如何结合？",
                queries=["世界模型 动作模型", "world model action dynamics"],
            )
            self.assertTrue(result["answerable"], {
                "coverage": result["coverage"],
                "claims": [item["paper_id"] for item in result["claims"]],
                "evidence": [item["paper_id"] for item in result["evidence"]],
            })
            self.assertGreaterEqual(result["coverage"]["evidence_papers"], 2)
            self.assertEqual(result["coverage"]["candidate_years"], [2018, 2022, 2024])
            self.assertEqual(result["coverage"]["evidence_years"], [2018, 2024])
            self.assertFalse(any(item["paper_id"] == "paper-ref" for item in result["evidence"]))

    def test_repairs_only_supported_missing_temporal_endpoint_with_bounded_call(self):
        class EndpointRetriever:
            def __init__(self, *, old_evidence: bool):
                self.old_evidence = old_evidence
                self.calls = []

            def paper_years(self, paper_ids):
                return {"paper-old": 2018, "paper-new": 2024}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                self.calls.append((query, tuple(kinds), tuple(paper_ids or ())))
                if kinds == ["claim"]:
                    return {"results": [
                        {"id": "claim-old", "paper_id": "paper-old", "kind": "claim",
                         "title": "World action", "text": "world action dynamics"},
                        {"id": "claim-new", "paper_id": "paper-new", "kind": "claim",
                         "title": "World action", "text": "world action dynamics"},
                    ]}
                if paper_ids == ["paper-old"]:
                    rows = ([{
                        "id": "evidence-old", "paper_id": "paper-old", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Methods",
                        "text": "world action dynamics " * 40,
                    }] if self.old_evidence else [])
                    return {"results": rows}
                return {"results": [{
                    "id": "evidence-new", "paper_id": "paper-new", "kind": "evidence",
                    "field_path": "pages/1/body", "title": "Results",
                    "text": "world action transition " * 40,
                }]}

        supported = EndpointRetriever(old_evidence=True)
        result = execute_two_stage_shadow_retrieval(
            supported,
            question="建模经历了哪些变化？",
            queries=["world model evolution", "latent dynamics changes"],
        )
        self.assertTrue(result["answerable"])
        self.assertEqual(result["coverage"]["evidence_years"], [2018, 2024])
        self.assertEqual(result["budget"]["search_calls"], 5)

        unsupported = EndpointRetriever(old_evidence=False)
        result = execute_two_stage_shadow_retrieval(
            unsupported,
            question="建模经历了哪些变化？",
            queries=["world model evolution", "latent dynamics changes"],
        )
        self.assertFalse(result["answerable"])
        self.assertEqual(result["coverage"]["evidence_years"], [2024])
        self.assertEqual(result["budget"]["search_calls"], 7)

    def test_primary_topic_anchors_reject_cross_domain_route_overlap(self):
        class CrossDomainRetriever:
            def paper_years(self, paper_ids):
                return {"paper-a": 2020, "paper-b": 2024}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [
                        {"id": "claim-a", "paper_id": "paper-a", "kind": "claim",
                         "title": "扩散模型", "text": "扩散模型采样算法"},
                        {"id": "claim-b", "paper_id": "paper-b", "kind": "claim",
                         "title": "扩散模型", "text": "扩散模型采样算法"},
                    ]}
                return {"results": [
                    {
                        "id": "irrelevant-a", "paper_id": "paper-a", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Language distillation",
                        "text": "student model sampling and teacher policy " * 30,
                    },
                    {
                        "id": "irrelevant-b", "paper_id": "paper-b", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Depth estimation",
                        "text": "single-step deterministic depth model " * 30,
                    },
                ]}

        result = execute_two_stage_shadow_retrieval(
            CrossDomainRetriever(),
            question="扩散模型的采样算法经历了哪些变化？",
            queries=[
                "扩散模型 采样算法 演进 diffusion model sampling",
                "扩散模型 少步采样 蒸馏 few-step sampling distillation",
            ],
        )
        self.assertFalse(result["answerable"])
        self.assertEqual(result["coverage"]["evidence_records"], 0)

    def test_temporal_question_requires_distinct_evidence_years_not_just_papers(self):
        class SameYearRetriever:
            def paper_years(self, paper_ids):
                return {"paper-a": 2020, "paper-b": 2020, "paper-c": 2024}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [
                        {"id": "claim-a", "paper_id": "paper-a", "kind": "claim",
                         "title": "Diffusion sampling", "text": "diffusion sampling"},
                        {"id": "claim-b", "paper_id": "paper-b", "kind": "claim",
                         "title": "Diffusion sampling", "text": "diffusion sampling"},
                        {"id": "claim-c", "paper_id": "paper-c", "kind": "claim",
                         "title": "Diffusion sampling", "text": "diffusion sampling"},
                    ]}
                return {"results": [
                    {
                        "id": "evidence-a", "paper_id": "paper-a", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Diffusion sampling",
                        "text": "diffusion sampling process " * 30,
                    },
                    {
                        "id": "evidence-b", "paper_id": "paper-b", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Diffusion sampling",
                        "text": "diffusion sampling objective " * 30,
                    },
                ]}

        result = execute_two_stage_shadow_retrieval(
            SameYearRetriever(),
            question="扩散模型的采样算法经历了哪些变化？",
            queries=["扩散模型 采样算法 diffusion model sampling"],
        )
        self.assertFalse(result["answerable"])
        self.assertEqual(result["coverage"]["evidence_papers"], 2)
        self.assertEqual(result["coverage"]["evidence_years"], [2020])
        self.assertFalse(result["coverage"]["temporal_years_sufficient"])

    def test_source_drilldown_is_candidate_only_and_never_satisfies_coverage(self):
        class DrilldownOnlyRetriever:
            def paper_years(self, paper_ids):
                return {"paper-a": 2018, "paper-b": 2024}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [
                        {"id": "claim-a", "paper_id": "paper-a", "kind": "claim",
                         "title": "世界模型", "text": "世界模型动作预测"},
                        {"id": "claim-b", "paper_id": "paper-b", "kind": "claim",
                         "title": "世界模型", "text": "世界模型动作预测"},
                    ]}
                if query == "abstract introduction conclusion":
                    return {"results": [{
                        "id": f"overview-{paper_ids[0]}", "paper_id": paper_ids[0],
                        "kind": "evidence", "field_path": "pages/1/abstract",
                        "title": "Abstract", "text": "generic overview text " * 20,
                    }]}
                return {"results": []}

        result = execute_two_stage_shadow_retrieval(
            DrilldownOnlyRetriever(),
            question="世界模型在建模上经历了哪些变化？",
            queries=["世界模型 建模 变化"],
        )
        self.assertFalse(result["answerable"])
        self.assertTrue(result["coverage"]["source_drilldown_used"])
        self.assertEqual(result["coverage"]["candidate_source_records"], 2)
        self.assertEqual(result["coverage"]["evidence_records"], 0)
        self.assertEqual(result["coverage"]["evidence_years"], [])

    def test_anchored_endpoint_repair_precedes_candidate_only_drilldown(self):
        class DrilldownAndRepairRetriever:
            def paper_years(self, paper_ids):
                return {"paper-old": 2018, "paper-new": 2024}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [
                        {"id": "claim-old", "paper_id": "paper-old", "kind": "claim",
                         "title": "World model", "text": "world model action dynamics"},
                        {"id": "claim-new", "paper_id": "paper-new", "kind": "claim",
                         "title": "World model", "text": "world model action dynamics"},
                    ]}
                if query == "abstract introduction conclusion":
                    return {"results": [{
                        "id": f"overview-{paper_ids[0]}", "paper_id": paper_ids[0],
                        "kind": "evidence", "field_path": "pages/1/abstract",
                        "title": "Abstract", "text": "generic overview text " * 20,
                    }]}
                if paper_ids and len(paper_ids) == 1:
                    return {"results": [{
                        "id": f"evidence-{paper_ids[0]}", "paper_id": paper_ids[0],
                        "kind": "evidence", "field_path": "pages/2/body",
                        "title": "World model action dynamics",
                        "text": "world model action dynamics transition " * 20,
                    }]}
                return {"results": []}

        result = execute_two_stage_shadow_retrieval(
            DrilldownAndRepairRetriever(),
            question="How did world models change over time?",
            queries=["world model action dynamics"],
        )
        self.assertTrue(result["answerable"])
        self.assertFalse(result["coverage"]["source_drilldown_used"])
        self.assertEqual(result["coverage"]["candidate_source_records"], 2)
        self.assertEqual(result["coverage"]["evidence_records"], 2)
        self.assertEqual(result["coverage"]["evidence_years"], [2018, 2024])

    def test_temporal_candidate_budget_keeps_relevant_middle_year(self):
        class MiddleYearRetriever:
            def paper_years(self, paper_ids):
                years = {
                    "paper-old": 2020,
                    "paper-new": 2026,
                    "paper-noise-a": 2026,
                    "paper-noise-b": 2026,
                    "paper-noise-c": 2025,
                    "paper-middle": 2021,
                }
                return {paper: years[paper] for paper in paper_ids}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [
                        {
                            "id": f"claim-{paper}", "paper_id": paper, "kind": "claim",
                            "title": "Diffusion sampling", "text": "diffusion sampling process",
                        }
                        for paper in (
                            "paper-old", "paper-new", "paper-noise-a", "paper-noise-b",
                            "paper-noise-c", "paper-middle",
                        )
                    ]}
                if paper_ids and "paper-middle" in paper_ids:
                    return {"results": [
                        {
                            "id": "evidence-old", "paper_id": "paper-old",
                            "kind": "evidence", "field_path": "pages/1/body",
                            "title": "Diffusion sampling",
                            "text": "diffusion sampling baseline process " * 20,
                        },
                        {
                            "id": "evidence-middle", "paper_id": "paper-middle",
                            "kind": "evidence", "field_path": "pages/2/body",
                            "title": "Diffusion sampling",
                            "text": "diffusion sampling variance acceleration " * 20,
                        },
                    ]}
                return {"results": []}

        result = execute_two_stage_shadow_retrieval(
            MiddleYearRetriever(),
            question="扩散采样经历了哪些变化？",
            queries=["diffusion sampling changes"],
            required_papers=1,
            required_evidence=1,
        )
        self.assertTrue(result["answerable"])
        self.assertIn(2021, result["coverage"]["evidence_years"])

    def test_global_source_rescue_requires_corroboration_and_stays_bounded(self):
        class GlobalRescueRetriever:
            def paper_years(self, paper_ids):
                years = {"paper-claim": 2018, "paper-memory": 2022, "paper-noise": 2024}
                return {paper: years[paper] for paper in paper_ids}

            def search(self, query, *, kinds, paper_ids=None, limit=10):
                if kinds == ["claim"]:
                    return {"results": [{
                        "id": "claim", "paper_id": "paper-claim", "kind": "claim",
                        "title": "Representation memory", "text": "representation memory learning",
                    }]}
                if paper_ids:
                    return {"results": [{
                        "id": "claim-source", "paper_id": "paper-claim",
                        "kind": "evidence", "field_path": "pages/1/body",
                        "title": "Representation memory",
                        "text": "representation memory recurrent baseline " * 20,
                    }]}
                if query == "representation memory evolution":
                    return {"results": [
                        {
                            "id": "memory-a", "paper_id": "paper-memory", "kind": "evidence",
                            "field_path": "pages/1/body", "title": "Representation memory",
                            "text": "representation memory bank learning " * 20,
                        },
                        {
                            "id": "noise", "paper_id": "paper-noise", "kind": "evidence",
                            "field_path": "pages/1/body", "title": "Representation memory",
                            "text": "representation memory allocation learning " * 20,
                        },
                    ]}
                return {"results": [{
                    "id": "memory-b", "paper_id": "paper-memory", "kind": "evidence",
                    "field_path": "pages/2/body", "title": "External memory",
                    "text": "representation external memory learning " * 20,
                }]}

        result = execute_two_stage_shadow_retrieval(
            GlobalRescueRetriever(),
            question="How did representation memory evolve over time?",
            queries=["representation memory evolution", "external memory learning"],
            required_papers=2,
            required_evidence=2,
        )
        self.assertTrue(result["answerable"])
        self.assertTrue(result["coverage"]["global_evidence_rescue_used"])
        self.assertTrue(any(item["paper_id"] == "paper-memory" for item in result["evidence"]))
        self.assertFalse(any(item["paper_id"] == "paper-noise" for item in result["evidence"]))
        self.assertLessEqual(
            result["budget"]["search_calls"], result["budget"]["max_search_calls"]
        )


if __name__ == "__main__":
    unittest.main()
