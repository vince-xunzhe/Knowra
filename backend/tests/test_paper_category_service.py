import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_category_service import (
    PAPER_CATEGORY_DYNAMIC_3D,
    PAPER_CATEGORY_STATIC_3D,
    PAPER_CATEGORY_VLA,
    PAPER_CATEGORY_WORLD_MODEL,
    effective_paper_category,
    legacy_classify_paper_category,
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
        self.assertEqual(normalize_paper_category("自动驾驶/VLA"), PAPER_CATEGORY_VLA)
        self.assertEqual(normalize_paper_category("world model"), PAPER_CATEGORY_WORLD_MODEL)
        self.assertEqual(normalize_paper_category("动态三维重建"), PAPER_CATEGORY_DYNAMIC_3D)

    def test_manual_override_wins(self):
        paper = _paper("Demo", model=PAPER_CATEGORY_WORLD_MODEL, override=PAPER_CATEGORY_VLA)
        self.assertEqual(effective_paper_category(paper, {"paper_category": "世界模型"}), PAPER_CATEGORY_VLA)

    def test_legacy_classifier_distinguishes_static_and_dynamic_3d(self):
        static_paper = _paper("VGGT for 3D Reconstruction")
        dynamic_paper = _paper("Gaussian Avatars for Head Animation")

        self.assertEqual(
            legacy_classify_paper_category(static_paper, {"keywords": ["geometry", "sfm", "depth"]}),
            PAPER_CATEGORY_STATIC_3D,
        )
        self.assertEqual(
            legacy_classify_paper_category(dynamic_paper, {"keywords": ["avatar", "animatable", "video avatar"]}),
            PAPER_CATEGORY_DYNAMIC_3D,
        )

    def test_sync_backfills_missing_model_category(self):
        paper = _paper("Latent World Model for Driving")
        changed = sync_paper_category_fields(
            paper,
            {
                "problem_area": "autonomous driving world model",
                "keywords": ["world model", "driving"],
            },
        )

        self.assertTrue(changed)
        self.assertEqual(paper.paper_category_model, PAPER_CATEGORY_WORLD_MODEL)


if __name__ == "__main__":
    unittest.main()
