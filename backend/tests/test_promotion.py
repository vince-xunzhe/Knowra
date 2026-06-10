import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.promotion_service import (
    _heuristic_decide,
    HEURISTIC_PROMOTE_MIN_PAPERS,
)
from services.graph_service import (
    PROMOTION_PROMOTED,
    PROMOTION_REJECTED,
)


def _node(**overrides):
    base = dict(
        title="Diffusion",
        node_type="technique",
        node_origin="auto",
        hidden=False,
        promotion_status="pending",
        promoted_by=None,
        last_promotion_eval_at=None,
        source_paper_ids=[1, 2],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class HeuristicTests(unittest.TestCase):
    def test_short_title_rejected(self):
        decision = _heuristic_decide(_node(title="X"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, PROMOTION_REJECTED)

    def test_numeric_title_rejected(self):
        decision = _heuristic_decide(_node(title="2024.05"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, PROMOTION_REJECTED)

    def test_no_source_papers_rejected(self):
        decision = _heuristic_decide(_node(source_paper_ids=[]))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, PROMOTION_REJECTED)

    def test_three_papers_promoted(self):
        decision = _heuristic_decide(
            _node(source_paper_ids=list(range(1, HEURISTIC_PROMOTE_MIN_PAPERS + 1)))
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, PROMOTION_PROMOTED)

    def test_two_papers_ambiguous(self):
        # Falls between reject (≤ 0 papers / short title) and auto-promote
        # (≥ 3 papers) — should defer to LLM stage.
        decision = _heuristic_decide(_node(source_paper_ids=[1, 2]))
        self.assertIsNone(decision)

    def test_one_paper_ambiguous(self):
        decision = _heuristic_decide(_node(source_paper_ids=[1]))
        self.assertIsNone(decision)


class ParseDecisionsTests(unittest.TestCase):
    """The LLM stage maps decisions back to nodes by id. After the
    multitenant migration node ids are UUID strings; the parser used to
    do int(id) and silently dropped every decision → all candidates
    stuck 'still ambiguous' (the 自动剔除 no-op). These lock in
    string-keyed parsing for both UUID and legacy-int ids."""

    def test_parses_uuid_ids(self):
        from services.promotion_llm import _parse_decisions
        raw = (
            '[{"id":"2d620237-3ea3-4205-ab84-7687284417d1","decision":"reject","reason":"r"},'
            '{"id":"abc-def-uuid","decision":"promote","reason":"ok"}]'
        )
        d = _parse_decisions(raw)
        self.assertIn("2d620237-3ea3-4205-ab84-7687284417d1", d)
        self.assertEqual(d["abc-def-uuid"]["decision"], "promote")

    def test_parses_legacy_int_ids_as_strings(self):
        from services.promotion_llm import _parse_decisions
        d = _parse_decisions('[{"id":5,"decision":"reject","reason":"x"}]')
        self.assertIn("5", d)
        self.assertEqual(d["5"]["decision"], "reject")

    def test_strips_code_fences_and_ignores_bad_decisions(self):
        from services.promotion_llm import _parse_decisions
        raw = (
            '```json\n[{"id":"u1","decision":"promote","reason":"a"},'
            '{"id":"u2","decision":"maybe","reason":"b"}]\n```'
        )
        d = _parse_decisions(raw)
        self.assertIn("u1", d)
        self.assertNotIn("u2", d)  # "maybe" isn't promote/reject → dropped


if __name__ == "__main__":
    unittest.main()
