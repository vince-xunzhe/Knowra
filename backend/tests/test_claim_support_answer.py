from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.claim_support_answer import (
    ClaimSupportError,
    build_answer_plan,
    build_support_ledger,
    execute_api_answer,
    execute_cli_answer,
    render_shadow_answer,
    validate_answer_plan,
    validate_support_ledger,
)
from services.paper_evidence_service import build_evidence_manifest
from services.wiki_claims import build_claim_catalog


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _rehash(item: dict, key: str, prefix: str = "") -> None:
    payload = {name: value for name, value in item.items() if name != key}
    digest = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    item[key] = f"{prefix}{digest}"


def _manifest(
    paper_id: str,
    sentence: str,
    *,
    source_file_hash: str | None = None,
) -> dict:
    return build_evidence_manifest(
        paper_id=paper_id,
        source_file_hash=source_file_hash or _hash(f"{paper_id}-source".encode()),
        source_size_bytes=512,
        total_pages=1,
        pages=[{
            "page_number": 1,
            "status": "ok",
            "text": (sentence + " ") * 8,
        }],
        parser_version="synthetic-1",
        chunk_chars=1_200,
        overlap_chars=50,
    )


class ClaimSupportAnswerTests(unittest.TestCase):
    def setUp(self):
        self.early_sentence = (
            "In 2018, earlier world models predicted observations while action remained a separate component."
        )
        self.late_sentence = (
            "In 2022, later world models conditioned the learned latent transition directly on actions."
        )
        self.early_manifest = _manifest("paper-early", self.early_sentence)
        self.late_manifest = _manifest("paper-late", self.late_sentence)
        self.early_evidence = self.early_manifest["chunks"][0]["id"]
        self.late_evidence = self.late_manifest["chunks"][0]["id"]
        self.early_catalog = self._catalog(
            self.early_manifest,
            year=2018,
            title="Passive Prediction",
            claim="Prediction and action remained separate.",
            evidence_id=self.early_evidence,
        )
        self.late_catalog = self._catalog(
            self.late_manifest,
            year=2022,
            title="Action-conditioned Dynamics",
            claim="Actions became explicit inputs to the latent transition.",
            evidence_id=self.late_evidence,
        )
        self.catalogs = [self.early_catalog, self.late_catalog]
        self.manifests = [self.early_manifest, self.late_manifest]
        self.early_claim = self._innovation_claim(self.early_catalog)
        self.late_claim = self._innovation_claim(self.late_catalog)
        self.early_year_claim = self._field_claim(self.early_catalog, "year")
        self.late_year_claim = self._field_claim(self.late_catalog, "year")
        self.assessments = [
            self._assessment(
                self.early_claim, self.early_evidence, self.early_sentence
            ),
            self._assessment(
                self.late_claim, self.late_evidence, self.late_sentence
            ),
            self._assessment(
                self.early_year_claim, self.early_evidence, self.early_sentence
            ),
            self._assessment(
                self.late_year_claim, self.late_evidence, self.late_sentence
            ),
        ]
        self.ledger = self._ledger(self.assessments)
        self.support_by_claim = {
            item["claim_id"]: item["id"]
            for item in self.ledger["support_records"]
            if item["relationship"] == "supports"
        }
        self.retrieved = [
            {"id": self.early_claim, "paper_id": "paper-early", "kind": "claim"},
            {"id": self.late_claim, "paper_id": "paper-late", "kind": "claim"},
            {"id": self.early_evidence, "paper_id": "paper-early", "kind": "evidence"},
            {"id": self.late_evidence, "paper_id": "paper-late", "kind": "evidence"},
        ]

    @staticmethod
    def _catalog(
        manifest: dict,
        *,
        year: int | None,
        title: str,
        claim: str,
        evidence_id: str,
    ) -> dict:
        extraction = {
            "title": title,
            "innovations": {"this_work": claim},
            "historical_position": {"overall": "A synthetic stage."},
        }
        if year is not None:
            extraction["year"] = year
        evidence_links = {"innovations.this_work": [evidence_id]}
        if year is not None:
            evidence_links["year"] = [evidence_id]
        return build_claim_catalog(
            paper_id=manifest["paper_id"],
            extraction=extraction,
            evidence_links=evidence_links,
            evidence_manifest=manifest,
        )

    @staticmethod
    def _innovation_claim(catalog: dict) -> str:
        return ClaimSupportAnswerTests._field_claim(catalog, "innovations.this_work")

    @staticmethod
    def _field_claim(catalog: dict, field_path: str) -> str:
        return next(
            item["id"]
            for item in catalog["claims"]
            if item["field_path"] == field_path
        )

    def _assessment(
        self,
        claim_id: str,
        evidence_id: str,
        sentence: str,
        *,
        relationship: str = "supports",
        method: str = "synthetic_fixture",
    ) -> dict:
        return {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relationship": relationship,
            "method": method,
            "assessor_version": "synthetic-reviewer@v1",
            "quote_start": 0,
            "quote_end": len(sentence),
            "quote": sentence,
            "rationale": "Explicit fixture adjudication.",
        }

    def _ledger(self, assessments, *, catalogs=None, manifests=None):
        return build_support_ledger(
            claim_catalogs=catalogs or self.catalogs,
            evidence_manifests=manifests or self.manifests,
            assessments=assessments,
        )

    def _plan(self, **overrides):
        arguments = {
            "question": "世界模型与动作模型的结合经历了哪些建模变化？",
            "answer_kind": "evolution",
            "retrieved_results": self.retrieved,
            "support_ledger": self.ledger,
            "claim_catalogs": self.catalogs,
            "evidence_manifests": self.manifests,
            "stages": [
                {
                    "label": "阶段一：分离",
                    "claim_id": self.early_claim,
                    "support_record_ids": [self.support_by_claim[self.early_claim]],
                },
                {
                    "label": "阶段二：动作条件化",
                    "claim_id": self.late_claim,
                    "support_record_ids": [self.support_by_claim[self.late_claim]],
                },
            ],
        }
        arguments.update(overrides)
        return build_answer_plan(**arguments)

    def _render(self, plan, ledger=None, *, catalogs=None, manifests=None):
        return render_shadow_answer(
            plan,
            ledger or self.ledger,
            claim_catalogs=catalogs or self.catalogs,
            evidence_manifests=manifests or self.manifests,
        )

    def test_shadow_evolution_copies_claims_and_cites_exact_spans(self):
        plan = self._plan()
        answer = self._render(plan)
        self.assertFalse(plan["answerable"])
        self.assertTrue(plan["shadow_answerable"])
        self.assertFalse(answer["answerable"])
        self.assertTrue(answer["shadow_answerable"])
        self.assertFalse(answer["production_eligible"])
        self.assertEqual(answer["trust_scope"], "synthetic_fixture")
        self.assertIn("Prediction and action remained separate\\.", answer["markdown"])
        self.assertEqual(
            {item["quote"] for item in answer["citations"]},
            {self.early_sentence, self.late_sentence},
        )
        self.assertEqual(
            {item["locator"]["manifest_id"] for item in answer["citations"]},
            {self.early_manifest["manifest_id"], self.late_manifest["manifest_id"]},
        )
        self.assertTrue(
            {self.early_year_claim, self.late_year_claim}.issubset(
                {item["claim_id"] for item in answer["citations"]}
            )
        )

    def test_production_cli_and_api_refuse_identically_without_shadow_text(self):
        plan = self._plan()
        kwargs = {
            "claim_catalogs": self.catalogs,
            "evidence_manifests": self.manifests,
        }
        cli = execute_cli_answer(plan, self.ledger, **kwargs)
        api = execute_api_answer(plan, self.ledger, **kwargs)
        self.assertEqual(cli, api)
        self.assertFalse(cli["answerable"])
        self.assertEqual(cli["citations"], [])
        self.assertEqual(cli["refusal_reasons"], ["trusted_review_receipt_required"])
        self.assertNotIn("Prediction and action", cli["markdown"])

    def test_declared_manual_review_is_still_shadow_only(self):
        assessments = [
            {**item, "method": "declared_manual_review", "assessor_version": "reviewer@2026-10-30"}
            for item in self.assessments
        ]
        ledger = self._ledger(assessments)
        support_by_claim = {item["claim_id"]: item["id"] for item in ledger["support_records"]}
        plan = self._plan(
            support_ledger=ledger,
            stages=[
                {"label": "early", "claim_id": self.early_claim, "support_record_ids": [support_by_claim[self.early_claim]]},
                {"label": "late", "claim_id": self.late_claim, "support_record_ids": [support_by_claim[self.late_claim]]},
            ],
        )
        self.assertEqual(ledger["trust_scope"], "declared_manual_review")
        self.assertTrue(plan["shadow_answerable"])
        self.assertFalse(plan["production_eligible"])
        self.assertEqual(
            execute_api_answer(
                plan,
                ledger,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )["refusal_reasons"],
            ["trusted_review_receipt_required"],
        )

    def test_candidate_links_without_adjudication_refuse(self):
        ledger = self._ledger([])
        plan = self._plan(
            support_ledger=ledger,
            stages=[
                {"label": "early", "claim_id": self.early_claim, "support_record_ids": []},
                {"label": "late", "claim_id": self.late_claim, "support_record_ids": []},
            ],
        )
        answer = self._render(plan, ledger)
        self.assertFalse(answer["shadow_answerable"])
        self.assertTrue(any("lacks_adjudicated_support" in item for item in answer["refusal_reasons"]))
        self.assertEqual(answer["citations"], [])

    def test_missing_retrieval_or_independent_source_refuses(self):
        missing_evidence = [item for item in self.retrieved if item["id"] != self.late_evidence]
        self.assertIn(
            "stage_2_evidence_not_retrieved",
            self._plan(retrieved_results=missing_evidence)["refusal_reasons"],
        )

        shared_hash = _hash(b"one-copied-pdf")
        first_manifest = _manifest("copied-a", self.early_sentence, source_file_hash=shared_hash)
        second_manifest = _manifest("copied-b", self.late_sentence, source_file_hash=shared_hash)
        first_catalog = self._catalog(first_manifest, year=2018, title="a", claim="a claim", evidence_id=first_manifest["chunks"][0]["id"])
        second_catalog = self._catalog(second_manifest, year=2022, title="b", claim="b claim", evidence_id=second_manifest["chunks"][0]["id"])
        first_claim = self._innovation_claim(first_catalog)
        second_claim = self._innovation_claim(second_catalog)
        assessments = [
            self._assessment(first_claim, first_manifest["chunks"][0]["id"], self.early_sentence),
            self._assessment(second_claim, second_manifest["chunks"][0]["id"], self.late_sentence),
        ]
        ledger = self._ledger(assessments, catalogs=[first_catalog, second_catalog], manifests=[first_manifest, second_manifest])
        records = {item["claim_id"]: item for item in ledger["support_records"]}
        retrieved = []
        for claim_id, paper_id, manifest in (
            (first_claim, "copied-a", first_manifest),
            (second_claim, "copied-b", second_manifest),
        ):
            retrieved.extend((
                {"id": claim_id, "paper_id": paper_id, "kind": "claim"},
                {"id": manifest["chunks"][0]["id"], "paper_id": paper_id, "kind": "evidence"},
            ))
        plan = build_answer_plan(
            question="What changed?",
            answer_kind="evolution",
            retrieved_results=retrieved,
            support_ledger=ledger,
            claim_catalogs=[first_catalog, second_catalog],
            evidence_manifests=[first_manifest, second_manifest],
            stages=[
                {"label": "a", "claim_id": first_claim, "support_record_ids": [records[first_claim]["id"]]},
                {"label": "b", "claim_id": second_claim, "support_record_ids": [records[second_claim]["id"]]},
            ],
        )
        self.assertIn("independent_source_files_required", plan["refusal_reasons"])

    def test_evolution_requires_present_chronological_years(self):
        reversed_catalogs = [
            self._catalog(self.early_manifest, year=2025, title="early", claim="Prediction and action remained separate.", evidence_id=self.early_evidence),
            self._catalog(self.late_manifest, year=2020, title="late", claim="Actions became explicit inputs to the latent transition.", evidence_id=self.late_evidence),
        ]
        claims = [self._innovation_claim(item) for item in reversed_catalogs]
        assessments = [
            self._assessment(claims[0], self.early_evidence, self.early_sentence),
            self._assessment(claims[1], self.late_evidence, self.late_sentence),
        ]
        ledger = self._ledger(assessments, catalogs=reversed_catalogs)
        records = {item["claim_id"]: item["id"] for item in ledger["support_records"]}
        retrieved = [
            {"id": claims[0], "paper_id": "paper-early", "kind": "claim"},
            {"id": claims[1], "paper_id": "paper-late", "kind": "claim"},
            {"id": self.early_evidence, "paper_id": "paper-early", "kind": "evidence"},
            {"id": self.late_evidence, "paper_id": "paper-late", "kind": "evidence"},
        ]
        plan = build_answer_plan(
            question="What changed?",
            answer_kind="evolution",
            retrieved_results=retrieved,
            support_ledger=ledger,
            claim_catalogs=reversed_catalogs,
            evidence_manifests=self.manifests,
            stages=[
                {"label": "early", "claim_id": claims[0], "support_record_ids": [records[claims[0]]]},
                {"label": "late", "claim_id": claims[1], "support_record_ids": [records[claims[1]]]},
            ],
        )
        self.assertIn("stage_order_not_chronological", plan["refusal_reasons"])

        missing_catalog = self._catalog(self.early_manifest, year=None, title="early", claim="Prediction and action remained separate.", evidence_id=self.early_evidence)
        missing_claim = self._innovation_claim(missing_catalog)
        missing_ledger = self._ledger(
            [self._assessment(missing_claim, self.early_evidence, self.early_sentence)],
            catalogs=[missing_catalog],
            manifests=[self.early_manifest],
        )
        support_id = missing_ledger["support_records"][0]["id"]
        single = build_answer_plan(
            question="Evolution?",
            answer_kind="evolution",
            retrieved_results=[
                {"id": missing_claim, "paper_id": "paper-early", "kind": "claim"},
                {"id": self.early_evidence, "paper_id": "paper-early", "kind": "evidence"},
            ],
            support_ledger=missing_ledger,
            claim_catalogs=[missing_catalog],
            evidence_manifests=[self.early_manifest],
            stages=[{"label": "only", "claim_id": missing_claim, "support_record_ids": [support_id]}],
        )
        self.assertIn("stage_years_required", single["refusal_reasons"])

    def test_conflict_must_target_stage_and_is_never_support(self):
        contradiction = self._assessment(
            self.early_claim,
            self.late_evidence,
            self.late_sentence,
            relationship="contradicts",
        )
        ledger = self._ledger(self.assessments + [contradiction])
        records = {item["id"]: item for item in ledger["support_records"]}
        conflict_id = next(item["id"] for item in records.values() if item["relationship"] == "contradicts")
        support_ids = {item["claim_id"]: item["id"] for item in records.values() if item["relationship"] == "supports"}
        plan = build_answer_plan(
            question="Where is the conflict?",
            answer_kind="conflict",
            retrieved_results=self.retrieved,
            support_ledger=ledger,
            claim_catalogs=self.catalogs,
            evidence_manifests=self.manifests,
            stages=[{"label": "claim", "claim_id": self.early_claim, "support_record_ids": [support_ids[self.early_claim]]}],
            conflict_record_ids=[conflict_id],
        )
        answer = self._render(plan, ledger)
        self.assertTrue(answer["shadow_answerable"])
        self.assertIn("## 冲突证据", answer["markdown"])

        wrong_provenance = [
            (
                {"id": item["id"], "paper_id": "WRONG", "kind": "claim"}
                if item["id"] == self.late_evidence
                else item
            )
            for item in self.retrieved
        ]
        with self.assertRaisesRegex(ClaimSupportError, "conflict evidence provenance"):
            build_answer_plan(
                question="Where is the conflict?",
                answer_kind="conflict",
                retrieved_results=wrong_provenance,
                support_ledger=ledger,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
                stages=[{"label": "claim", "claim_id": self.early_claim, "support_record_ids": [support_ids[self.early_claim]]}],
                conflict_record_ids=[conflict_id],
            )

        with self.assertRaisesRegex(ClaimSupportError, "does not target"):
            build_answer_plan(
                question="Wrong conflict",
                answer_kind="conflict",
                retrieved_results=self.retrieved,
                support_ledger=ledger,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
                stages=[{"label": "late", "claim_id": self.late_claim, "support_record_ids": [support_ids[self.late_claim]]}],
                conflict_record_ids=[conflict_id],
            )

    def test_quote_pair_version_and_source_binding_fail_closed(self):
        bad_quote = {**self.assessments[0], "quote": "not source bytes"}
        with self.assertRaisesRegex(ClaimSupportError, "does not match source"):
            self._ledger([bad_quote])
        short = {**self.assessments[0], "quote_end": 3, "quote": self.early_sentence[:3]}
        with self.assertRaisesRegex(ClaimSupportError, "not substantive"):
            self._ledger([short])
        with self.assertRaisesRegex(ClaimSupportError, "multiple adjudications"):
            self._ledger([self.assessments[0], {**self.assessments[0], "relationship": "contradicts"}])

        replacement = _manifest("paper-early", self.early_sentence, source_file_hash=_hash(b"new-version"))
        replacement_evidence = replacement["chunks"][0]["id"]
        with self.assertRaisesRegex(ClaimSupportError, "source version"):
            self._ledger(
                [self._assessment(self.early_claim, replacement_evidence, self.early_sentence)],
                catalogs=[self.early_catalog],
                manifests=[self.early_manifest, replacement],
            )

    def test_rehashed_ledger_and_plan_tampering_still_fail_against_sources(self):
        ledger = copy.deepcopy(self.ledger)
        ledger["source_chunks"][0]["text"] += " forged"
        _rehash(ledger, "ledger_signature")
        with self.assertRaisesRegex(ClaimSupportError, "validated manifest"):
            validate_support_ledger(
                ledger,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

        upgraded = copy.deepcopy(self.ledger)
        upgraded["production_eligible"] = True
        _rehash(upgraded, "ledger_signature")
        with self.assertRaisesRegex(ClaimSupportError, "upgraded"):
            validate_support_ledger(
                upgraded,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

        plan = self._plan(retrieved_results=[item for item in self.retrieved if item["id"] != self.late_evidence])
        plan["refusal_reasons"] = []
        plan["shadow_answerable"] = True
        _rehash(plan, "plan_id")
        with self.assertRaisesRegex(ClaimSupportError, "readiness was altered"):
            validate_answer_plan(
                plan,
                self.ledger,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

    def test_validator_replays_span_locator_discovery_and_rationale_constraints(self):
        one_character = copy.deepcopy(self.ledger)
        record = one_character["support_records"][0]
        chunk = next(item for item in one_character["source_chunks"] if item["id"] == record["evidence_id"])
        record["quote"] = chunk["text"][0]
        record["quote_hash"] = _hash(record["quote"].encode())
        record["locator"]["chunk_char_start"] = 0
        record["locator"]["chunk_char_end"] = 1
        record["locator"]["page_char_start"] = chunk["page_char_start"]
        record["locator"]["page_char_end"] = chunk["page_char_start"] + 1
        _rehash(record, "id", "support:")
        _rehash(one_character, "ledger_signature")
        with self.assertRaisesRegex(ClaimSupportError, "quote/locator"):
            validate_support_ledger(
                one_character,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

        forged_locator = copy.deepcopy(self.ledger)
        record = forged_locator["support_records"][0]
        record["locator"]["section_heading"] = "FORGED SECTION"
        _rehash(record, "id", "support:")
        _rehash(forged_locator, "ledger_signature")
        with self.assertRaisesRegex(ClaimSupportError, "validated source"):
            validate_support_ledger(
                forged_locator,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

        forged_metadata = copy.deepcopy(self.ledger)
        record = forged_metadata["support_records"][0]
        record["discovery_status"] = "explicit_assessment_only"
        record["rationale"] = "x" * 1_001
        _rehash(record, "id", "support:")
        _rehash(forged_metadata, "ledger_signature")
        with self.assertRaisesRegex(ClaimSupportError, "quote/locator"):
            validate_support_ledger(
                forged_metadata,
                claim_catalogs=self.catalogs,
                evidence_manifests=self.manifests,
            )

    def test_year_is_adjudicated_and_conflicting_catalog_years_fail(self):
        innovations_only = self._ledger(self.assessments[:2])
        records = {item["claim_id"]: item["id"] for item in innovations_only["support_records"]}
        plan = self._plan(
            support_ledger=innovations_only,
            stages=[
                {"label": "early", "claim_id": self.early_claim, "support_record_ids": [records[self.early_claim]]},
                {"label": "late", "claim_id": self.late_claim, "support_record_ids": [records[self.late_claim]]},
            ],
        )
        self.assertIn("stage_1_year_lacks_adjudicated_support", plan["refusal_reasons"])
        self.assertIn("stage_2_year_lacks_adjudicated_support", plan["refusal_reasons"])

        year_contradiction = self._assessment(
            self.early_year_claim,
            self.late_evidence,
            self.late_sentence,
            relationship="contradicts",
        )
        conflict_ledger = self._ledger(self.assessments + [year_contradiction])
        conflicted = self._plan(support_ledger=conflict_ledger)
        self.assertIn("unresolved_adjudicated_conflict", conflicted["refusal_reasons"])
        self.assertFalse(conflicted["shadow_answerable"])

        conflicting = self._catalog(
            self.early_manifest,
            year=2019,
            title="Conflicting chronology",
            claim="A different extraction claim.",
            evidence_id=self.early_evidence,
        )
        with self.assertRaisesRegex(ClaimSupportError, "disagree on the publication year"):
            build_support_ledger(
                claim_catalogs=[self.early_catalog, conflicting],
                evidence_manifests=[self.early_manifest],
                assessments=[],
            )

    def test_markdown_html_and_reference_injection_is_escaped(self):
        malicious_claim = "**invented** [^99]: <script>alert(1)</script>"
        malicious_sentence = "Evidence [^88]: <img src=x onerror=alert(1)> is explicitly source text."
        manifest = _manifest("paper-malicious", malicious_sentence)
        evidence_id = manifest["chunks"][0]["id"]
        catalog = self._catalog(manifest, year=2020, title="malicious", claim=malicious_claim, evidence_id=evidence_id)
        claim_id = self._innovation_claim(catalog)
        ledger = self._ledger(
            [self._assessment(claim_id, evidence_id, malicious_sentence)],
            catalogs=[catalog],
            manifests=[manifest],
        )
        support_id = ledger["support_records"][0]["id"]
        plan = build_answer_plan(
            question="What?",
            answer_kind="single_claim",
            retrieved_results=[
                {"id": claim_id, "paper_id": "paper-malicious", "kind": "claim"},
                {"id": evidence_id, "paper_id": "paper-malicious", "kind": "evidence"},
            ],
            support_ledger=ledger,
            claim_catalogs=[catalog],
            evidence_manifests=[manifest],
            stages=[{"label": "# forged\n[^77]: bad", "claim_id": claim_id, "support_record_ids": [support_id]}],
        )
        answer = self._render(plan, ledger, catalogs=[catalog], manifests=[manifest])
        self.assertNotIn("<script>", answer["markdown"])
        self.assertNotIn("<img", answer["markdown"])
        self.assertNotIn("\n[^77]:", answer["markdown"])
        self.assertIn("\\[^99\\]", answer["markdown"])
        self.assertEqual(answer["citations"][0]["quote"], malicious_sentence)

    def test_all_budgets_fail_closed(self):
        with mock.patch("services.claim_support_answer.MAX_RETRIEVED_RESULTS", 1):
            with self.assertRaisesRegex(ClaimSupportError, "retrieved result count"):
                self._plan()
        with mock.patch("services.claim_support_answer.MAX_STAGE_SUPPORTS", 0):
            with self.assertRaisesRegex(ClaimSupportError, "support ids"):
                self._plan()
        plan = self._plan()
        with mock.patch("services.claim_support_answer.MAX_OUTPUT_CHARACTERS", 10):
            with self.assertRaisesRegex(ClaimSupportError, "output budget"):
                self._render(plan)


if __name__ == "__main__":
    unittest.main()
