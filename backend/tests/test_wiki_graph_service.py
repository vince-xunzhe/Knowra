import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.wiki_graph_service import build_wiki_graph, classify_paper_category


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, papers):
        self.papers = papers

    def query(self, model):
        return _FakeQuery(self.papers)


def _paper(pid: int, title: str, extraction: dict):
    now = datetime(2026, 4, 30, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=pid,
        title=title,
        filename=f"{pid:04d}.pdf",
        processed_at=now,
        created_at=now,
        raw_llm_response=json.dumps(extraction, ensure_ascii=False),
    )


class WikiGraphServiceTests(unittest.TestCase):
    def test_classify_world_model_category(self):
        paper = _paper(1, "Latent World Models for Driving", {
            "title": "Latent World Models for Driving",
            "problem_area": "autonomous driving world model",
            "techniques": [{"name": "JEPA World Model"}],
            "keywords": ["world model"],
            "year": 2025,
        })
        self.assertEqual(classify_paper_category(paper, json.loads(paper.raw_llm_response)), "世界模型")

    def test_build_graph_chains_papers_and_links_concepts(self):
        paper1 = _paper(1, "Depth Reconstruction A", {
            "title": "Depth Reconstruction A",
            "problem_area": "3d reconstruction",
            "keywords": ["3d reconstruction"],
            "year": 2024,
        })
        paper2 = _paper(2, "Depth Reconstruction B", {
            "title": "Depth Reconstruction B",
            "problem_area": "3d reconstruction",
            "keywords": ["geometry"],
            "year": 2025,
        })
        db = _FakeDB([paper1, paper2])
        concept = SimpleNamespace(
            id=7,
            title="Depth Benchmark",
            node_type="dataset",
            source_paper_ids=[1, 2],
        )

        with patch("services.wiki_graph_service.list_paper_pages", return_value=[
            {"paper_id": 1, "filename": "0001-a.md", "title": paper1.title, "compiled_at": "2026-04-30T08:00:00+00:00"},
            {"paper_id": 2, "filename": "0002-b.md", "title": paper2.title, "compiled_at": "2026-04-30T08:01:00+00:00"},
        ]), patch("services.wiki_graph_service.list_concept_pages", return_value=[
            {
                "concept_id": 7,
                "filename": "0007-depth.md",
                "title": "Depth Benchmark",
                "node_type": "dataset",
                "compiled_at": "2026-04-30T08:02:00+00:00",
                "source_paper_ids": [1, 2],
            },
        ]), patch("services.wiki_graph_service.list_publishable_concept_nodes", return_value=[concept]):
            graph = build_wiki_graph(db, active_kind="paper", active_id=2)

        timeline_edges = [edge for edge in graph["edges"] if edge["relation_type"] == "timeline"]
        support_edges = [edge for edge in graph["edges"] if edge["relation_type"] == "supports"]
        active_papers = [node for node in graph["nodes"] if node["kind"] == "paper" and node["active"]]

        self.assertEqual(len(timeline_edges), 1)
        self.assertEqual(len(support_edges), 2)
        self.assertEqual(len(active_papers), 1)
        self.assertEqual(active_papers[0]["paper_id"], 2)

    def test_build_graph_includes_publishable_db_concept_without_compiled_page(self):
        paper = _paper(1, "Drive World Model", {
            "title": "Drive World Model",
            "paper_category": "世界模型",
            "keywords": ["world model"],
            "year": 2026,
        })
        db = _FakeDB([paper])
        concept = SimpleNamespace(
            id=9,
            title="闭环世界模型",
            node_type="concept",
            source_paper_ids=[1],
        )

        with patch("services.wiki_graph_service.list_paper_pages", return_value=[
            {"paper_id": 1, "filename": "0001-drive.md", "title": paper.title, "compiled_at": "2026-04-30T08:00:00+00:00"},
        ]), patch("services.wiki_graph_service.list_concept_pages", return_value=[]), patch(
            "services.wiki_graph_service.list_publishable_concept_nodes",
            return_value=[concept],
        ):
            graph = build_wiki_graph(db)

        concept_nodes = [node for node in graph["nodes"] if node["kind"] == "concept"]
        self.assertEqual(len(concept_nodes), 1)
        self.assertEqual(concept_nodes[0]["title"], "闭环世界模型")
        self.assertIsNone(concept_nodes[0]["filename"])
        self.assertEqual(concept_nodes[0]["category"], "世界模型")


if __name__ == "__main__":
    unittest.main()
