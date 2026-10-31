from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.claim_support_answer import (
    build_answer_plan,
    build_support_ledger,
    execute_api_answer,
    execute_cli_answer,
    render_shadow_answer,
)
from services.hybrid_claim_retrieval import (
    HybridClaimRetriever,
    build_retrieval_plan,
    build_shadow_index,
    execute_api_plan,
    execute_cli_plan,
)
from services.paper_evidence_service import build_evidence_manifest
from services.wiki_shadow_compiler import build_paper_compile_plan


QUESTION = "世界模型与动作模型的结合，在建模上经历了哪些变化？"


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest(paper_id: str, sentence: str) -> dict:
    return build_evidence_manifest(
        paper_id=paper_id,
        source_file_hash=_hash(f"{paper_id}-synthetic-pdf".encode()),
        source_size_bytes=1_024,
        total_pages=1,
        pages=[{
            "page_number": 1,
            "status": "ok",
            "text": (sentence + " ") * 8,
        }],
        parser_version="synthetic-e2e@v1",
        chunk_chars=1_200,
        overlap_chars=50,
    )


def _compile_plan(
    paper_id: str,
    title: str,
    year: int,
    change: str,
    manifest: dict,
) -> dict:
    evidence_id = manifest["chunks"][0]["id"]
    return build_paper_compile_plan(
        paper_id=paper_id,
        title=title,
        extraction={
            "title": title,
            "year": year,
            "problem": "How should world dynamics represent action?",
            "innovations": {
                "this_work": change,
                "why_better": "The factorization makes the modeling change explicit.",
            },
            "historical_position": {"overall": change},
            "limitations": "Synthetic fixture; it is not a real quality claim.",
        },
        notes="",
        model="synthetic-no-provider",
        locale="zh",
        source_file_hash=manifest["source"]["file_hash_sha256"],
        evidence_links={
            "year": [evidence_id],
            "innovations.this_work": [evidence_id],
        },
        evidence_manifest=manifest,
    )


def _claim(catalog: dict, field_path: str) -> str:
    return next(
        item["id"]
        for item in catalog["claims"]
        if item["field_path"] == field_path
    )


class ShadowAskEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.shadow = self.root / "shadow"
        self.production = self.root / "production"
        self.production.mkdir()
        self.index = self.shadow / "indexes" / "claims.sqlite"

        self.early_sentence = (
            "In 2018, the world model predicted observations while action remained a separate component."
        )
        self.late_sentence = (
            "In 2022, actions became explicit inputs to the learned latent transition and rollout dynamics."
        )
        self.negative_sentence = (
            "In 2021, a manufacturing action model changed world-coordinate spline dynamics and tolerances."
        )
        self.early_manifest = _manifest("paper-early", self.early_sentence)
        self.late_manifest = _manifest("paper-late", self.late_sentence)
        self.negative_manifest = _manifest("paper-negative", self.negative_sentence)
        self.manifests = [
            self.early_manifest,
            self.late_manifest,
            self.negative_manifest,
        ]
        self.early_plan = _compile_plan(
            "paper-early",
            "Passive Observation World Model",
            2018,
            "Prediction and action remained separate components.",
            self.early_manifest,
        )
        self.late_plan = _compile_plan(
            "paper-late",
            "Action Conditioned Latent Dynamics",
            2022,
            "Actions became explicit inputs to the latent transition model.",
            self.late_manifest,
        )
        self.negative_plan = _compile_plan(
            "paper-negative",
            "Manufacturing Contour Spline",
            2021,
            "An action model changed world-coordinate spline dynamics for manufacturing tolerances.",
            self.negative_manifest,
        )
        self.compile_plans = [
            self.early_plan,
            self.late_plan,
            self.negative_plan,
        ]
        self.catalogs = [plan["claim_catalogs"][0] for plan in self.compile_plans]
        self.early_catalog, self.late_catalog, self.negative_catalog = self.catalogs
        self.early_claim = _claim(self.early_catalog, "innovations.this_work")
        self.late_claim = _claim(self.late_catalog, "innovations.this_work")
        self.negative_claim = _claim(self.negative_catalog, "innovations.this_work")
        self.early_year = _claim(self.early_catalog, "year")
        self.late_year = _claim(self.late_catalog, "year")
        self.early_evidence = self.early_manifest["chunks"][0]["id"]
        self.late_evidence = self.late_manifest["chunks"][0]["id"]
        self.negative_evidence = self.negative_manifest["chunks"][0]["id"]

        vectors = {
            self.early_claim: [1.0, 0.0, 0.0],
            self.late_claim: [0.99, 0.01, 0.0],
            self.early_evidence: [0.98, 0.02, 0.0],
            self.late_evidence: [0.97, 0.03, 0.0],
            self.negative_claim: [0.75, 0.66, 0.0],
            self.negative_evidence: [0.74, 0.67, 0.0],
        }
        self.index_report = build_shadow_index(
            index_path=self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
            compile_plans=self.compile_plans,
            evidence_manifests=self.manifests,
            vectors_by_id=vectors,
            dense_model_id="synthetic-e2e-vectors@v1",
        )
        self.retriever = HybridClaimRetriever(
            self.index,
            shadow_root=self.shadow,
            production_wiki_root=self.production,
        )
        self.assessments = [
            self._assessment(self.early_claim, self.early_evidence, self.early_sentence),
            self._assessment(self.late_claim, self.late_evidence, self.late_sentence),
            self._assessment(self.early_year, self.early_evidence, self.early_sentence),
            self._assessment(self.late_year, self.late_evidence, self.late_sentence),
        ]
        self.ledger = build_support_ledger(
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
            assessments=self.assessments,
        )
        self.supports = {
            item["claim_id"]: item["id"]
            for item in self.ledger["support_records"]
            if item["relationship"] == "supports"
        }

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _assessment(claim_id: str, evidence_id: str, sentence: str, *, relationship: str = "supports") -> dict:
        return {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relationship": relationship,
            "method": "synthetic_fixture",
            "assessor_version": "e2e-fixture@v1",
            "quote_start": 0,
            "quote_end": len(sentence),
            "quote": sentence,
            "rationale": "Explicit synthetic end-to-end oracle.",
        }

    def _retrieval(self, *, query: str, vector: list[float] | None) -> dict:
        route = {"query": query}
        if vector is not None:
            route.update({
                "query_vector": vector,
                "dense_model_id": "synthetic-e2e-vectors@v1",
            })
        plan = build_retrieval_plan(
            question=query,
            routes=[route],
            required_papers=2,
            required_source_records=2,
            limit=4,
        )
        cli = execute_cli_plan(self.retriever, plan)
        api = execute_api_plan(self.retriever, plan)
        self.assertEqual(
            [item["id"] for item in cli["results"]],
            [item["id"] for item in api["results"]],
        )
        return cli

    @staticmethod
    def _retrieved_provenance(result: dict) -> list[dict[str, str]]:
        return [
            {"id": item["id"], "paper_id": item["paper_id"], "kind": item["kind"]}
            for item in result["results"]
        ]

    def _answer_plan(self, retrieved: dict, *, ledger=None, conflicts=()):
        return build_answer_plan(
            question=QUESTION,
            answer_kind="evolution",
            retrieved_results=self._retrieved_provenance(retrieved),
            support_ledger=ledger or self.ledger,
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
            stages=[
                {
                    "label": "分离建模",
                    "claim_id": self.early_claim,
                    "support_record_ids": [self.supports[self.early_claim]],
                },
                {
                    "label": "动作条件化",
                    "claim_id": self.late_claim,
                    "support_record_ids": [self.supports[self.late_claim]],
                },
            ],
            conflict_record_ids=conflicts,
        )

    def test_full_shadow_path_improves_recall_then_emits_exact_citations(self):
        lexical = self.retriever.search(QUESTION, limit=4)
        hybrid = self._retrieval(query=QUESTION, vector=[1.0, 0.0, 0.0])
        oracle = {
            self.early_claim,
            self.late_claim,
            self.early_evidence,
            self.late_evidence,
        }
        lexical_ids = {item["id"] for item in lexical["results"]}
        hybrid_ids = {item["id"] for item in hybrid["results"]}
        lexical_recall = len(oracle & lexical_ids) / len(oracle)
        hybrid_recall = len(oracle & hybrid_ids) / len(oracle)
        self.assertEqual(lexical_recall, 0.0)
        self.assertEqual(hybrid_recall, 1.0)
        self.assertEqual(len(oracle & hybrid_ids) / len(hybrid_ids), 1.0)
        self.assertNotIn(self.negative_claim, hybrid_ids)
        self.assertNotIn(self.negative_evidence, hybrid_ids)
        self.assertTrue(hybrid["coverage"]["met"])

        answer_plan = self._answer_plan(hybrid)
        shadow = render_shadow_answer(
            answer_plan,
            self.ledger,
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
        )
        self.assertTrue(shadow["shadow_answerable"])
        self.assertFalse(shadow["answerable"])
        self.assertEqual(len(shadow["citations"]), 4)
        self.assertEqual(
            {item["claim_id"] for item in shadow["citations"]},
            {self.early_claim, self.late_claim, self.early_year, self.late_year},
        )
        self.assertTrue(all(item["quote"] in {self.early_sentence, self.late_sentence} for item in shadow["citations"]))
        self.assertFalse(any(self.production.iterdir()))
        self.assertGreater(self.index_report["records"], len(oracle))

        kwargs = {"claim_catalogs": self.catalogs, "evidence_manifests": self.manifests}
        self.assertEqual(
            execute_cli_answer(answer_plan, self.ledger, **kwargs),
            execute_api_answer(answer_plan, self.ledger, **kwargs),
        )
        self.assertEqual(
            execute_api_answer(answer_plan, self.ledger, **kwargs)["refusal_reasons"],
            ["trusted_review_receipt_required"],
        )

    def test_no_answer_and_hard_negative_cannot_cross_the_evidence_gate(self):
        no_answer = self._retrieval(query="zephyr qzxv unsupported facet", vector=None)
        self.assertEqual(no_answer["results"], [])
        no_answer_plan = self._answer_plan(no_answer)
        self.assertFalse(no_answer_plan["shadow_answerable"])
        no_answer_shadow = render_shadow_answer(
            no_answer_plan,
            self.ledger,
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
        )
        self.assertEqual(no_answer_shadow["citations"], [])
        adapter_kwargs = {
            "claim_catalogs": self.catalogs,
            "evidence_manifests": self.manifests,
        }
        cli_refusal = execute_cli_answer(no_answer_plan, self.ledger, **adapter_kwargs)
        api_refusal = execute_api_answer(no_answer_plan, self.ledger, **adapter_kwargs)
        self.assertEqual(cli_refusal, api_refusal)
        self.assertFalse(api_refusal["answerable"])
        self.assertEqual(api_refusal["citations"], [])
        self.assertEqual(
            api_refusal["refusal_reasons"],
            ["trusted_review_receipt_required"],
        )

        negative = self._retrieval(
            query="world action model manufacturing spline dynamics",
            vector=[0.75, 0.66, 0.0],
        )
        negative_ids = {item["id"] for item in negative["results"]}
        self.assertIn(self.negative_claim, negative_ids)
        plan = self._answer_plan(negative)
        self.assertFalse(plan["shadow_answerable"])
        self.assertTrue(
            any(item.endswith("_not_retrieved") for item in plan["refusal_reasons"])
        )
        refused = render_shadow_answer(
            plan,
            self.ledger,
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
        )
        self.assertEqual(refused["citations"], [])

    def test_unlisted_cross_source_conflict_blocks_an_otherwise_complete_answer(self):
        conflict = self._assessment(
            self.early_claim,
            self.late_evidence,
            self.late_sentence,
            relationship="contradicts",
        )
        ledger = build_support_ledger(
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
            assessments=self.assessments + [conflict],
        )
        hybrid = self._retrieval(query=QUESTION, vector=[1.0, 0.0, 0.0])
        plan = self._answer_plan(hybrid, ledger=ledger)
        self.assertFalse(plan["shadow_answerable"])
        self.assertIn("unresolved_adjudicated_conflict", plan["refusal_reasons"])


if __name__ == "__main__":
    unittest.main()
