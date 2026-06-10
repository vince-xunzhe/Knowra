import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import wiki


class _SeqQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._obj


class _SeqDB:
    def __init__(self, objs):
        self._objs = list(objs)

    def query(self, _model):
        if not self._objs:
            return _SeqQuery(None)
        return _SeqQuery(self._objs.pop(0))


class WikiRouterIncrementalTests(unittest.TestCase):
    def setUp(self):
        wiki.compile_state["running"] = False

    @patch("routers.wiki.wiki_search_service.rebuild_index", return_value={"ok": True})
    @patch("routers.wiki.wiki_index.refresh_index", return_value=Path("/tmp/wiki/index.md"))
    @patch(
        "routers.wiki.reconcile_concept_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch(
        "routers.wiki.reconcile_paper_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch("routers.wiki.compile_concept_page", return_value=Path("/tmp/wiki/concepts/0007-concept.md"))
    @patch("routers.wiki.compile_paper_page", return_value=Path("/tmp/wiki/papers/0001-paper.md"))
    @patch(
        "routers.wiki.compute_freshness_summary",
        side_effect=[
            {
                "papers": {"missing": [{"paper_id": "1"}], "stale": []},
                "concepts": {"missing": [{"concept_id": "7"}], "stale": []},
            },
            {
                "papers": {"missing": [], "stale": []},
                "concepts": {"missing": [], "stale": []},
            },
        ],
    )
    @patch("routers.wiki.task_model_id", return_value="openai/gpt-4o-mini")
    @patch("routers.wiki.load_config", return_value={"openai_api_key": "k"})
    def test_recompile_dirty_items_compiles_stale_targets_and_refreshes_index(
        self,
        mock_load_config,
        mock_task_model_id,
        mock_freshness,
        mock_compile_paper,
        mock_compile_concept,
        mock_reconcile_papers,
        mock_reconcile_concepts,
        mock_refresh_index,
        mock_rebuild_search,
    ):
        paper = SimpleNamespace(id="1", title="Paper A", filename="a.pdf", processed=True, raw_llm_response="{}")
        concept = SimpleNamespace(id="7", title="Concept A")
        db = _SeqDB([paper, concept])

        resp = wiki.recompile_dirty_items(body=wiki.RecompileDirtyInput(), db=db)

        self.assertEqual(resp["requested"]["paper_ids"], ["1"])
        self.assertEqual(resp["requested"]["concept_ids"], ["7"])
        self.assertEqual(resp["compiled"]["papers"], 1)
        self.assertEqual(resp["compiled"]["concepts"], 1)
        self.assertEqual(resp["failed"]["count"], 0)

        mock_compile_paper.assert_called_once_with(paper, "k", "openai/gpt-4o-mini")
        mock_compile_concept.assert_called_once_with(concept, db, "k", "openai/gpt-4o-mini")
        mock_reconcile_papers.assert_called_once()
        mock_reconcile_concepts.assert_called_once()
        mock_refresh_index.assert_called_once()
        mock_rebuild_search.assert_called_once()
        self.assertEqual(mock_freshness.call_count, 2)

    @patch("routers.wiki._record_failure")
    @patch("routers.wiki.wiki_search_service.rebuild_index", return_value={"ok": True})
    @patch("routers.wiki.wiki_index.refresh_index", return_value=Path("/tmp/wiki/index.md"))
    @patch(
        "routers.wiki.reconcile_concept_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch(
        "routers.wiki.reconcile_paper_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch(
        "routers.wiki.compile_paper_page",
        side_effect=[Path("/tmp/wiki/papers/0001-paper.md"), RuntimeError("boom")],
    )
    @patch(
        "routers.wiki.compute_freshness_summary",
        side_effect=[
            {
                "papers": {"missing": [{"paper_id": "1"}, {"paper_id": "2"}], "stale": []},
                "concepts": {"missing": [], "stale": []},
            },
            {
                "papers": {"missing": [{"paper_id": "2"}], "stale": []},
                "concepts": {"missing": [], "stale": []},
            },
        ],
    )
    @patch("routers.wiki.task_model_id", return_value="openai/gpt-4o-mini")
    @patch("routers.wiki.load_config", return_value={"openai_api_key": "k"})
    def test_recompile_dirty_items_records_failure_and_continues(
        self,
        mock_load_config,
        mock_task_model_id,
        mock_freshness,
        mock_compile_paper,
        mock_reconcile_papers,
        mock_reconcile_concepts,
        mock_refresh_index,
        mock_rebuild_search,
        mock_record_failure,
    ):
        paper1 = SimpleNamespace(id="1", title="Paper A", filename="a.pdf", processed=True, raw_llm_response="{}")
        paper2 = SimpleNamespace(id="2", title="Paper B", filename="b.pdf", processed=True, raw_llm_response="{}")
        db = _SeqDB([paper1, paper2])

        resp = wiki.recompile_dirty_items(body=wiki.RecompileDirtyInput(), db=db)

        self.assertEqual(resp["compiled"]["papers"], 1)
        self.assertEqual(resp["failed"]["count"], 1)
        self.assertEqual(resp["failed"]["items"][0]["id"], "2")
        self.assertEqual(mock_compile_paper.call_count, 2)
        mock_record_failure.assert_called_once()
        mock_refresh_index.assert_called_once()
        mock_rebuild_search.assert_called_once()
        self.assertEqual(mock_freshness.call_count, 2)

    @patch("routers.wiki.wiki_search_service.rebuild_index", return_value={"ok": True})
    @patch("routers.wiki.wiki_index.refresh_index", return_value=Path("/tmp/wiki/index.md"))
    @patch(
        "routers.wiki.reconcile_concept_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch(
        "routers.wiki.reconcile_paper_pages_dir",
        return_value={"removed_count": 0, "duplicate_removed": 0, "orphan_removed": 0, "removed": []},
    )
    @patch("routers.wiki.compile_concept_page", return_value=Path("/tmp/wiki/concepts/0009-concept.md"))
    @patch("routers.wiki.compile_paper_page", return_value=Path("/tmp/wiki/papers/0003-paper.md"))
    @patch(
        "routers.wiki.compute_freshness_summary",
        side_effect=[
            {"papers": {"missing": [], "stale": []}, "concepts": {"missing": [], "stale": []}},
            {"papers": {"missing": [], "stale": []}, "concepts": {"missing": [], "stale": []}},
        ],
    )
    @patch("routers.wiki.task_model_id", return_value="openai/gpt-4o-mini")
    @patch("routers.wiki.load_config", return_value={"openai_api_key": "k"})
    def test_recompile_by_ids_targets_only_explicit_ids(
        self,
        mock_load_config,
        mock_task_model_id,
        mock_freshness,
        mock_compile_paper,
        mock_compile_concept,
        mock_reconcile_papers,
        mock_reconcile_concepts,
        mock_refresh_index,
        mock_rebuild_search,
    ):
        paper = SimpleNamespace(id="3", title="Paper C", filename="c.pdf", processed=True, raw_llm_response="{}")
        concept = SimpleNamespace(id="9", title="Concept C")
        db = _SeqDB([paper, concept])

        resp = wiki.recompile_by_ids(
            body=wiki.RecompileByIdsInput(paper_ids=["3"], concept_ids=["9"]),
            db=db,
        )

        self.assertEqual(resp["requested"]["include_missing"], False)
        self.assertEqual(resp["requested"]["include_stale"], False)
        self.assertEqual(resp["requested"]["paper_ids"], ["3"])
        self.assertEqual(resp["requested"]["concept_ids"], ["9"])
        self.assertEqual(resp["compiled"]["papers"], 1)
        self.assertEqual(resp["compiled"]["concepts"], 1)
        self.assertEqual(resp["failed"]["count"], 0)

        mock_compile_paper.assert_called_once_with(paper, "k", "openai/gpt-4o-mini")
        mock_compile_concept.assert_called_once_with(concept, db, "k", "openai/gpt-4o-mini")
        mock_reconcile_papers.assert_called_once()
        mock_reconcile_concepts.assert_called_once()
        mock_refresh_index.assert_called_once()
        mock_rebuild_search.assert_called_once()
        self.assertEqual(mock_freshness.call_count, 2)


if __name__ == "__main__":
    unittest.main()
