import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.ask import AskRequest, _normalize_ask_citation, ask
from services.ask_agent import AskResult
from services.claim_support_answer import (
    build_answer_plan,
    build_support_ledger,
    render_shadow_answer,
)
from services.paper_evidence_service import build_evidence_manifest
from services.wiki_claims import build_claim_catalog


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _rendered_exact_citation() -> dict:
    sentence = "Actions enter the latent transition directly."
    manifest = build_evidence_manifest(
        paper_id="paper-fixture",
        source_file_hash=_hash(b"paper-fixture"),
        source_size_bytes=512,
        total_pages=1,
        pages=[{"page_number": 1, "status": "ok", "text": sentence * 8}],
        parser_version="router-contract@v1",
        chunk_chars=1_200,
        overlap_chars=50,
    )
    evidence_id = manifest["chunks"][0]["id"]
    catalog = build_claim_catalog(
        paper_id="paper-fixture",
        extraction={"title": "Action Dynamics", "innovations": {"this_work": sentence}},
        evidence_links={"innovations.this_work": [evidence_id]},
        evidence_manifest=manifest,
    )
    claim_id = next(
        item["id"] for item in catalog["claims"]
        if item["field_path"] == "innovations.this_work"
    )
    ledger = build_support_ledger(
        claim_catalogs=[catalog],
        evidence_manifests=[manifest],
        assessments=[{
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relationship": "supports",
            "method": "synthetic_fixture",
            "assessor_version": "router-contract@v1",
            "quote_start": 0,
            "quote_end": len(sentence),
            "quote": sentence,
            "rationale": "Synthetic router contract.",
        }],
    )
    support_id = ledger["support_records"][0]["id"]
    plan = build_answer_plan(
        question="How are actions modeled?",
        answer_kind="single_claim",
        retrieved_results=[
            {"id": claim_id, "paper_id": "paper-fixture", "kind": "claim"},
            {"id": evidence_id, "paper_id": "paper-fixture", "kind": "evidence"},
        ],
        support_ledger=ledger,
        claim_catalogs=[catalog],
        evidence_manifests=[manifest],
        stages=[{
            "label": "Action conditioning",
            "claim_id": claim_id,
            "support_record_ids": [support_id],
        }],
    )
    rendered = render_shadow_answer(
        plan,
        ledger,
        claim_catalogs=[catalog],
        evidence_manifests=[manifest],
    )
    return rendered["citations"][0]


class AskRouterTitleTests(unittest.TestCase):
    def test_real_claim_span_citation_shape_is_normalized_without_loss(self):
        rendered = _rendered_exact_citation()
        citation = _normalize_ask_citation(rendered)
        locator = rendered["locator"]
        self.assertEqual(citation.kind, "paper")
        self.assertEqual(citation.ref, "paper:paper-fixture")
        self.assertEqual(citation.paper_id, "paper-fixture")
        self.assertEqual(citation.page_number, locator["page_number"])
        self.assertEqual(citation.section_kind, locator["section_kind"])
        self.assertEqual(citation.section_heading, locator["section_heading"])
        self.assertEqual(citation.char_start, locator["page_char_start"])
        self.assertEqual(citation.char_end, locator["page_char_end"])
        self.assertEqual(citation.manifest_id, locator["manifest_id"])
        self.assertEqual(citation.number, rendered["number"])
        self.assertEqual(citation.claim_id, rendered["claim_id"])
        self.assertEqual(citation.assessor_version, rendered["assessor_version"])
        self.assertEqual(citation.source_file_hash, locator["source_file_hash"])
        self.assertEqual(citation.chunk_char_start, locator["chunk_char_start"])
        self.assertEqual(citation.chunk_char_end, locator["chunk_char_end"])
        self.assertEqual(citation.relationship, "supports")
        self.assertEqual(citation.provenance_status, "adjudicated_support")

    @patch("routers.ask.call_text_model", return_value="标题： 因果注意力")
    @patch(
        "routers.ask.ask_agent.run_ask_agent",
        return_value=AskResult(
            answer="## 回答\n\n它负责在注意力里建模因果约束。",
            cited_files=["data/wiki/papers/0001-causal-transformer.md"],
            citations=[
                {
                    "kind": "paper",
                    "ref": "data/wiki/papers/0001-causal-transformer.md",
                    "path": "data/wiki/papers/0001-causal-transformer.md",
                    "filename": "0001-causal-transformer.md",
                    "paper_id": 1,
                    "page_number": 7,
                    "section_kind": "method",
                    "section_heading": "3. Method",
                    "quote": "Actions enter the latent transition directly.",
                    "char_start": 120,
                    "char_end": 165,
                    "evidence_id": "evidence:fixture",
                    "support_record_id": "support:fixture",
                    "manifest_id": "manifest:fixture",
                    "provenance_status": "adjudicated_support",
                }
            ],
            trace=[],
            model="codex-cli/gpt-5.5",
            duration_ms=123,
            steps=2,
        ),
    )
    @patch("routers.ask.task_reasoning_effort", return_value="high")
    @patch("routers.ask.task_model_name", return_value="codex-cli/gpt-5.5")
    @patch("routers.ask.load_config", return_value={})
    def test_ask_response_includes_model_generated_session_title(
        self,
        mock_load_config,
        mock_task_model_name,
        mock_task_reasoning_effort,
        mock_run_ask_agent,
        mock_call_text_model,
    ):
        response = ask(
            AskRequest(
                question="因果注意力在模型构建里扮演什么角色？",
                history=[],
                session_id="ask-session-001",
            ),
            db=MagicMock(),
        )

        self.assertEqual(response.session_title, "因果注意力")
        self.assertEqual(response.session_id, "ask-session-001")
        self.assertEqual(response.model, "codex-cli/gpt-5.5")
        self.assertEqual(response.answer, "## 回答\n\n它负责在注意力里建模因果约束。")
        self.assertEqual(response.citations[0].paper_id, 1)
        self.assertEqual(response.citations[0].page_number, 7)
        self.assertEqual(response.citations[0].section_heading, "3. Method")
        self.assertEqual(
            response.citations[0].quote,
            "Actions enter the latent transition directly.",
        )
        self.assertEqual(response.citations[0].provenance_status, "adjudicated_support")
        self.assertEqual(mock_call_text_model.call_args.kwargs["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
