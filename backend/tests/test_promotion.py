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


if __name__ == "__main__":
    unittest.main()
