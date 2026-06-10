import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_category_service import (
    PAPER_CATEGORY_DYNAMIC_3D,
    PAPER_CATEGORY_OTHER,
    PAPER_CATEGORY_VLA,
    PAPER_CATEGORY_WORLD_MODEL,
    effective_paper_category,
    normalize_paper_category,
    sync_paper_category_fields,
)


def _paper(title: str, filename: str = "demo.pdf", model: Optional[str] = None, override: Optional[str] = None):
    return SimpleNamespace(
        title=title,
        filename=filename,
        paper_category_model=model,
        paper_category_override=override,
    )


class PaperCategoryServiceTests(unittest.TestCase):
    def test_normalize_known_aliases(self):
        # Aliases normalise the model's chosen *label*, not paper text.
        self.assertEqual(normalize_paper_category("自动驾驶/VLA"), PAPER_CATEGORY_VLA)
        self.assertEqual(normalize_paper_category("world model"), PAPER_CATEGORY_WORLD_MODEL)
        self.assertEqual(normalize_paper_category("动态三维重建"), PAPER_CATEGORY_DYNAMIC_3D)

    def test_manual_override_wins(self):
        paper = _paper("Demo", model=PAPER_CATEGORY_WORLD_MODEL, override=PAPER_CATEGORY_VLA)
        self.assertEqual(effective_paper_category(paper, {"paper_category": "世界模型"}), PAPER_CATEGORY_VLA)

    def test_category_comes_from_model_label_only(self):
        # The effective category is the LLM's explicit ``paper_category`` —
        # normalised — and nothing else.
        paper = _paper("Some paper")
        self.assertEqual(
            effective_paper_category(paper, {"paper_category": "vision-language-action"}),
            PAPER_CATEGORY_VLA,
        )

    def test_no_keyword_guessing(self):
        # With no explicit model category, we do NOT scan title/keywords to
        # guess one — it falls back to 其他.
        paper = _paper("VGGT 3D Reconstruction with depth and gaussian")
        self.assertEqual(
            effective_paper_category(paper, {"keywords": ["geometry", "sfm", "depth"]}),
            PAPER_CATEGORY_OTHER,
        )

    def test_sync_backfills_from_model_label(self):
        paper = _paper("Latent World Model for Driving")
        changed = sync_paper_category_fields(paper, {"paper_category": "世界模型"})
        self.assertTrue(changed)
        self.assertEqual(paper.paper_category_model, PAPER_CATEGORY_WORLD_MODEL)

    def test_sync_no_backfill_without_model_label(self):
        paper = _paper("A paper with rich keywords but no model category")
        changed = sync_paper_category_fields(
            paper, {"keywords": ["world model", "driving"]}
        )
        self.assertFalse(changed)
        self.assertIsNone(paper.paper_category_model)


if __name__ == "__main__":
    unittest.main()
