from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import wiki_search
from services.ask_quality_eval import FixtureError, evaluate_fixture, load_and_evaluate


FIXTURE = Path(__file__).parent / "fixtures" / "ask_quality_eval_v1.json"


class AskQualityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_production_search_is_measured_against_oracle_layers(self):
        report = evaluate_fixture(self.fixture)

        self.assertEqual(report["mode"], "offline_deterministic_production_fts")
        oracle = {case["id"]: case for case in report["oracle"]["cases"]}
        observed = oracle["long-evolution-two-entities"]
        self.assertEqual(observed["source_claim_upper_bound"]["coverage"], 1.0)
        self.assertEqual(observed["extraction_claim_upper_bound"]["coverage"], 1.0)
        self.assertEqual(observed["wiki_claim_upper_bound"]["coverage"], 1.0)

        concept = oracle["concept-retention-oracle"]
        self.assertEqual(concept["extraction_claim_upper_bound"]["coverage"], 1.0)
        self.assertEqual(concept["wiki_claim_upper_bound"]["coverage"], 0.0)

    def test_long_evolution_question_shape_fails_without_body_aware_retrieval(self):
        report = evaluate_fixture(self.fixture)
        production_cases = {
            case["id"]: case
            for case in report["strategies"]["production"]["cases"]
        }

        for query_id in (
            "long-evolution-two-entities",
            "long-evolution-one-entity",
        ):
            case = production_cases[query_id]
            self.assertEqual(case["mode"], "strict_no_match")
            self.assertEqual(case["ranking"]["recall_at_k"], 0.0)
            self.assertEqual(
                case["retrieved_wiki_claim_coverage"]["coverage"], 0.0
            )

    def test_control_cases_measure_ranking_and_abstention(self):
        report = evaluate_fixture(self.fixture)
        cases = {
            case["id"]: case
            for case in report["strategies"]["production"]["cases"]
        }

        exact = cases["exact-title-control"]
        self.assertEqual(exact["ranked_document_ids"][0], "paper:separate-dynamics")
        self.assertEqual(exact["ranking"]["precision_at_k"], 0.333333)
        self.assertEqual(exact["ranking"]["precision_in_returned"], 1.0)
        self.assertEqual(exact["ranking"]["reciprocal_rank"], 1.0)
        self.assertEqual(exact["ranking"]["ndcg_at_k"], 1.0)
        self.assertTrue(cases["no-answer-control"]["abstained"])
        self.assertEqual(
            report["strategies"]["production"]["metrics"]["no_answer_accuracy"],
            1.0,
        )

    def test_fixture_validation_rejects_missing_relevant_document(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["queries"][0]["relevant_document_ids"] = ["paper:missing"]

        with self.assertRaisesRegex(FixtureError, "missing documents"):
            evaluate_fixture(fixture)

    def test_fixture_validation_rejects_unsupported_derived_claims(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["documents"][0]["extracted_claim_ids"] = ["claim:not-in-source"]

        with self.assertRaisesRegex(FixtureError, "lack source support"):
            evaluate_fixture(fixture)

    def test_fixture_validation_requires_source_grounded_answer_labels(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["queries"][0]["required_claim_ids"] = ["claim:not-in-source"]

        with self.assertRaisesRegex(FixtureError, "lack relevant source support"):
            evaluate_fixture(fixture)

    def test_fixture_validation_rejects_ambiguous_empty_labels_and_boolean_k(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["documents"][0]["aliases"] = ["  "]
        with self.assertRaisesRegex(FixtureError, "whitespace-only"):
            evaluate_fixture(fixture)

        fixture = copy.deepcopy(self.fixture)
        fixture["documents"][0]["source_claim_ids"] = [" claim:spaced"]
        with self.assertRaisesRegex(FixtureError, "leading or trailing whitespace"):
            evaluate_fixture(fixture)

        fixture = copy.deepcopy(self.fixture)
        fixture["queries"][0]["k"] = True
        with self.assertRaisesRegex(FixtureError, "between 1 and 100"):
            evaluate_fixture(fixture)

        fixture = copy.deepcopy(self.fixture)
        fixture["queries"][0]["required_claim_ids"] = []
        with self.assertRaisesRegex(FixtureError, "no required claims"):
            evaluate_fixture(fixture)

        fixture = copy.deepcopy(self.fixture)
        fixture["queries"][-1]["required_claim_ids"] = ["claim:unexpected"]
        with self.assertRaisesRegex(FixtureError, "unanswerable but has required claims"):
            evaluate_fixture(fixture)

    def test_output_is_reproducible_except_for_latency(self):
        first = evaluate_fixture(self.fixture)
        second = evaluate_fixture(self.fixture)
        for report in (first, second):
            for strategy in report["strategies"].values():
                strategy.pop("latency_ms")
                for case in strategy["cases"]:
                    case.pop("latency_ms")
        self.assertEqual(first, second)

    def test_evaluation_restores_the_existing_entity_cache(self):
        previous = wiki_search._ENTITY_CACHE
        sentinel = (("sentinel", 1, 2, "wiki"), (("Alias", "Title"),))
        wiki_search._ENTITY_CACHE = sentinel
        try:
            evaluate_fixture(self.fixture)
            self.assertIs(wiki_search._ENTITY_CACHE, sentinel)
        finally:
            wiki_search._ENTITY_CACHE = previous

    def test_loader_never_needs_repository_data_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture_path = Path(temporary) / "fixture.json"
            fixture_path.write_text(json.dumps(self.fixture), encoding="utf-8")
            report = load_and_evaluate(fixture_path)

        self.assertEqual(report["dataset"], "synthetic-cross-paper-evidence-v1")


if __name__ == "__main__":
    unittest.main()
