from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hybrid_claim_retrieval import HybridClaimRetriever, build_shadow_index
from services.paper_evidence_service import build_evidence_manifest
from services.two_stage_shadow_retrieval import execute_two_stage_shadow_retrieval
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
                        {"id": "claim-old", "paper_id": "paper-old", "kind": "claim"},
                        {"id": "claim-new", "paper_id": "paper-new", "kind": "claim"},
                    ]}
                if paper_ids == ["paper-old"]:
                    rows = ([{
                        "id": "evidence-old", "paper_id": "paper-old", "kind": "evidence",
                        "field_path": "pages/1/body", "title": "Methods", "text": "old " * 80,
                    }] if self.old_evidence else [])
                    return {"results": rows}
                return {"results": [{
                    "id": "evidence-new", "paper_id": "paper-new", "kind": "evidence",
                    "field_path": "pages/1/body", "title": "Results", "text": "new " * 80,
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
        self.assertEqual(result["budget"]["search_calls"], 5)


if __name__ == "__main__":
    unittest.main()
