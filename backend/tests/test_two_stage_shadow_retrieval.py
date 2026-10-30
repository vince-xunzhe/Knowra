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


def plan(paper_id: str, change: str, source: dict):
    return build_paper_compile_plan(
        paper_id=paper_id,
        title=paper_id,
        extraction={"title": paper_id, "innovations": {"this_work": change}},
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
                    plan("paper-a", "世界模型使用动作模型条件化潜在动力学", first),
                    plan("paper-b", "世界模型与动作模型学习动作结果", second),
                    plan("paper-ref", "世界模型动作模型引用", references),
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
            self.assertFalse(any(item["paper_id"] == "paper-ref" for item in result["evidence"]))


if __name__ == "__main__":
    unittest.main()
