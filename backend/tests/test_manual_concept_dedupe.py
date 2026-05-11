import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "fastapi" not in sys.modules:
    fastapi = ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def get(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def put(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    def _depends(dep=None):
        return dep

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail):
            super().__init__(detail if isinstance(detail, str) else repr(detail))
            self.status_code = status_code
            self.detail = detail

    fastapi.APIRouter = _APIRouter
    fastapi.Depends = _depends
    fastapi.HTTPException = _HTTPException
    sys.modules["fastapi"] = fastapi

from models import KnowledgeNode, Paper
from routers.graph import ManualConceptInput, create_manual_concept, update_manual_concept


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeDB:
    def __init__(self, nodes=None, papers=None):
        self.nodes = list(nodes or [])
        self.papers = list(papers or [])
        self.next_id = max([getattr(node, "id", 0) for node in self.nodes] + [0]) + 1

    def query(self, model):
        if model is KnowledgeNode:
            return _FakeQuery(self.nodes)
        if model is Paper:
            return _FakeQuery(self.papers)
        raise AssertionError(f"unexpected model: {model}")

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = self.next_id
            self.next_id += 1
        self.nodes.append(item)

    def commit(self):
        return None

    def refresh(self, item):
        return None


def _node(node_id: int, title: str, **overrides):
    base = dict(
        id=node_id,
        title=title,
        content=title,
        node_type="concept",
        node_origin="auto",
        hidden=False,
        tags=[],
        source_paper_ids=[],
        promotion_status="pending",
        promoted_by=None,
        embedding=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class ManualConceptDedupeTests(unittest.TestCase):
    def test_create_reuses_existing_node_and_adopts_it_as_manual(self):
        existing = _node(
            7,
            "闭环世界模型",
            hidden=True,
            tags=["世界模型"],
            source_paper_ids=[1],
        )
        db = _FakeDB(
            nodes=[existing],
            papers=[SimpleNamespace(id=1), SimpleNamespace(id=2)],
        )

        with patch("routers.graph._reconcile_curated_wiki"), patch(
            "routers.graph.get_node_detail_data",
            return_value={"id": 7, "title": "闭环世界模型"},
        ):
            result = create_manual_concept(
                ManualConceptInput(
                    title="闭环世界模型",
                    content="用户补充定义",
                    paper_ids=[1, 2],
                    tags=["世界模型", "规划"],
                ),
                db=db,
            )

        self.assertFalse(result["created"])
        self.assertTrue(result["reused_existing"])
        self.assertTrue(result["adopted_existing"])
        self.assertEqual(result["merged_tags"], 1)
        self.assertEqual(result["merged_papers"], 1)
        self.assertTrue(result["content_applied"])
        self.assertEqual(existing.node_origin, "manual")
        self.assertFalse(existing.hidden)
        self.assertEqual(existing.promotion_status, "promoted")
        self.assertEqual(existing.promoted_by, "user")
        self.assertEqual(existing.tags, ["世界模型", "规划"])
        self.assertEqual(existing.source_paper_ids, [1, 2])
        self.assertEqual(existing.content, "用户补充定义")

    def test_update_rejects_duplicate_title(self):
        target = _node(1, "时序表征", node_origin="manual", promotion_status="promoted")
        other = _node(2, "因果注意力", node_origin="manual", promotion_status="promoted")
        db = _FakeDB(nodes=[target, other], papers=[])

        with self.assertRaises(Exception) as exc:
            update_manual_concept(
                1,
                ManualConceptInput(
                    title="因果注意力",
                    content="新内容",
                    paper_ids=[],
                    tags=[],
                ),
                db=db,
            )

        err = exc.exception
        self.assertEqual(getattr(err, "status_code", None), 409)
        self.assertIsInstance(getattr(err, "detail", None), dict)
        self.assertEqual(err.detail["existing_node_id"], 2)
        self.assertIn("已存在同名概念", err.detail["message"])

    def test_create_does_not_reuse_existing_node_by_tag_only(self):
        existing = _node(
            7,
            "LoRA",
            node_type="technique",
            node_origin="auto",
            promotion_status="promoted",
            tags=["因果注意力"],
        )
        db = _FakeDB(nodes=[existing], papers=[SimpleNamespace(id=1)])

        with patch("routers.graph._reconcile_curated_wiki"), patch(
            "routers.graph.get_node_detail_data",
            side_effect=lambda _db, node_id: {"id": node_id, "title": "因果注意力"},
        ):
            result = create_manual_concept(
                ManualConceptInput(
                    title="因果注意力",
                    content="用户定义",
                    paper_ids=[1],
                    tags=["ask归纳"],
                ),
                db=db,
            )

        self.assertTrue(result["created"])
        self.assertFalse(result["reused_existing"])
        self.assertEqual(len(db.nodes), 2)
        self.assertEqual(db.nodes[-1].title, "因果注意力")
        self.assertEqual(db.nodes[-1].id, 8)


if __name__ == "__main__":
    unittest.main()
