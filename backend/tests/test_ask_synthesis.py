import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType
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

from models import KnowledgeEdge, KnowledgeNode, Paper
from routers.ask import SynthesisConceptInput, create_concept_from_synthesis


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, nodes=None, papers=None, edges=None):
        self.nodes = list(nodes or [])
        self.papers = list(papers or [])
        self.edges = list(edges or [])
        self.added = []
        self.added_edges = []
        self.next_id = max([getattr(node, "id", 0) for node in self.nodes] + [0]) + 1

    def query(self, model):
        if model is KnowledgeNode:
            return _FakeQuery(self.nodes)
        if model is Paper:
            return _FakeQuery(self.papers)
        if model is KnowledgeEdge:
            return _FakeQuery(self.edges)
        raise AssertionError(f"unexpected model: {model}")

    def add(self, item):
        if isinstance(item, KnowledgeEdge):
            self.edges.append(item)
            self.added_edges.append(item)
            return
        if getattr(item, "id", None) is None:
            item.id = self.next_id
            self.next_id += 1
        self.nodes.append(item)
        self.added.append(item)

    def commit(self):
        return None

    def refresh(self, item):
        return None


def _concept(
    node_id: int,
    title: str,
    *,
    node_type: str = "concept",
    node_origin: str = "manual",
    promotion_status: str = "promoted",
    hidden: bool = False,
    tags=None,
    source_paper_ids=None,
):
    return SimpleNamespace(
        id=node_id,
        title=title,
        content=title,
        node_type=node_type,
        node_origin=node_origin,
        promotion_status=promotion_status,
        hidden=hidden,
        tags=list(tags or []),
        source_paper_ids=list(source_paper_ids or []),
    )


class AskSynthesisConceptTests(unittest.TestCase):
    def setUp(self):
        self.load_config_patch = patch(
            "routers.ask.load_config",
            return_value={"openai_api_key": "", "wiki_compile_model": "gpt-4o-mini"},
        )
        self.load_config_patch.start()

    def tearDown(self):
        self.load_config_patch.stop()

    def test_duplicate_title_returns_conflict_with_existing_concept(self):
        existing = _concept(7, "闭环世界模型", tags=["synthesis"])
        db = _FakeDB(nodes=[existing])

        with patch(
            "routers.ask._concept_page_path",
            return_value=Path("/tmp/0007-closed-loop-world-model.md"),
        ), self.assertRaises(Exception) as exc:
            create_concept_from_synthesis(
                SynthesisConceptInput(
                    title="  闭环世界模型  ",
                    body="总结正文",
                    tags=["synthesis"],
                ),
                db=db,
            )

        err = exc.exception
        self.assertEqual(getattr(err, "status_code", None), 409)
        self.assertEqual(err.detail["duplicate_concept"]["concept_id"], 7)
        self.assertEqual(err.detail["duplicate_concept"]["title"], "闭环世界模型")
        self.assertEqual(
            err.detail["duplicate_concept"]["filename"],
            "0007-closed-loop-world-model.md",
        )
        self.assertTrue(err.detail["can_force_create"])
        self.assertEqual(db.added, [])

    def test_force_create_allows_duplicate_title(self):
        existing = _concept(7, "闭环世界模型", tags=["synthesis"])
        db = _FakeDB(nodes=[existing], papers=[SimpleNamespace(id=1)])

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch("routers.ask.WIKI_CONCEPTS_DIR", base), patch(
                "routers.ask._concept_page_path",
                side_effect=lambda node: base / f"{node.id:04d}-forced-duplicate.md",
            ), patch("routers.ask.reconcile_concept_pages_dir"), patch(
                "routers.ask.wiki_search_service.rebuild_index"
            ):
                result = create_concept_from_synthesis(
                    SynthesisConceptInput(
                        title="闭环世界模型",
                        body="总结正文",
                        source_question="什么是闭环世界模型？",
                        force_create=True,
                        source_paper_ids=[1],
                        tags=["synthesis"],
                    ),
                    db=db,
                )

            created_path = base / f"{result['concept_id']:04d}-forced-duplicate.md"
            self.assertTrue(created_path.exists())

        self.assertTrue(result["created"])
        self.assertFalse(result["reused_existing"])
        self.assertTrue(result["forced_create"])
        self.assertNotEqual(result["concept_id"], existing.id)
        self.assertEqual(len(db.added), 1)

    def test_model_duplicate_returns_reasoned_conflict(self):
        existing = _concept(11, "LoRA", node_type="technique", node_origin="auto")
        db = _FakeDB(nodes=[existing])

        with patch(
            "routers.ask.analyze_synthesis_concept",
            return_value=SimpleNamespace(
                used_model=True,
                model="gpt-4o-mini",
                summary="LoRA 的摘要",
                body_markdown="## 定义\n\nLoRA 正文",
                tags=["低秩适配"],
                aliases=["Low-Rank Adaptation"],
                related_links=[],
                duplicate_concept_id=11,
                duplicate_reason="二者都在描述同一个低秩适配微调方法，只是命名不同。",
            ),
        ), patch(
            "routers.ask._concept_page_path",
            return_value=Path("/tmp/0011-lora.md"),
        ), self.assertRaises(Exception) as exc:
            create_concept_from_synthesis(
                SynthesisConceptInput(
                    title="低秩适配",
                    body="Ask 回答正文",
                    tags=["ask归纳"],
                ),
                db=db,
            )

        err = exc.exception
        self.assertEqual(getattr(err, "status_code", None), 409)
        self.assertEqual(err.detail["duplicate_concept"]["concept_id"], 11)
        self.assertIn("模型判断它与现有概念", err.detail["message"])
        self.assertIn("低秩适配微调方法", err.detail["duplicate_reason"])
        self.assertEqual(db.added, [])

    def test_model_analysis_enriches_created_concept_summary_and_body(self):
        existing = _concept(7, "Transformer", node_type="technique", node_origin="auto")
        db = _FakeDB(
            nodes=[existing],
            papers=[SimpleNamespace(id=1, title="Paper One", filename="paper1.pdf")],
        )

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch(
                "routers.ask.analyze_synthesis_concept",
                return_value=SimpleNamespace(
                    used_model=True,
                    model="gpt-4o-mini",
                    summary="因果注意力是一种约束信息流方向的注意力机制。",
                    body_markdown=(
                        "## 定义\n\n因果注意力限制 token 只能看到过去信息。\n\n"
                        "## 与相关概念关系\n\n它常见于 [[Transformer]] 自回归建模。"
                    ),
                    tags=["注意力", "自回归"],
                    aliases=["causal attention"],
                    related_links=[SimpleNamespace(concept_id=7, relation_type="builds_on")],
                    duplicate_concept_id=None,
                    duplicate_reason="",
                ),
            ), patch("routers.ask.WIKI_CONCEPTS_DIR", base), patch(
                "routers.ask._concept_page_path",
                side_effect=lambda node: base / f"{node.id:04d}-causal-attention.md",
            ), patch("routers.ask.reconcile_concept_pages_dir"), patch(
                "routers.ask.wiki_search_service.rebuild_index"
            ):
                result = create_concept_from_synthesis(
                    SynthesisConceptInput(
                        title="因果注意力",
                        body="原始 Ask 回答",
                        source_question="什么是因果注意力？",
                        source_paper_ids=[1],
                        tags=["ask归纳"],
                    ),
                    db=db,
                )

            created_path = base / f"{result['concept_id']:04d}-causal-attention.md"
            self.assertTrue(created_path.exists())
            text = created_path.read_text(encoding="utf-8")
            self.assertIn('aliases:', text)
            self.assertIn('summary: "因果注意力是一种约束信息流方向的注意力机制。"', text)
            self.assertIn("## 与相关概念关系", text)

        self.assertTrue(result["created"])
        self.assertTrue(result["analysis_used"])
        self.assertEqual(result["analysis_model"], "gpt-4o-mini")
        self.assertEqual(result["related_concepts_added"], 1)
        self.assertEqual(db.added[0].content, "因果注意力是一种约束信息流方向的注意力机制。")
        self.assertEqual(db.added[0].tags, ["ask归纳", "注意力", "自回归"])
        self.assertEqual(len(db.edges), 1)
        self.assertEqual(db.edges[0].source_id, db.added[0].id)
        self.assertEqual(db.edges[0].target_id, 7)
        self.assertEqual(db.edges[0].relation_type, "builds_on")

    def test_tag_overlap_does_not_trigger_duplicate_conflict(self):
        existing = _concept(
            7,
            "LoRA",
            node_type="technique",
            node_origin="auto",
            tags=["因果注意力"],
        )
        db = _FakeDB(nodes=[existing], papers=[SimpleNamespace(id=1)])

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch("routers.ask.WIKI_CONCEPTS_DIR", base), patch(
                "routers.ask._concept_page_path",
                side_effect=lambda node: base / f"{node.id:04d}-causal-attention.md",
            ), patch("routers.ask.reconcile_concept_pages_dir"), patch(
                "routers.ask.wiki_search_service.rebuild_index"
            ):
                result = create_concept_from_synthesis(
                    SynthesisConceptInput(
                        title="因果注意力",
                        body="总结正文",
                        source_question="什么是因果注意力？",
                        source_paper_ids=[1],
                        tags=["ask归纳"],
                    ),
                    db=db,
                )

            created_path = base / f"{result['concept_id']:04d}-causal-attention.md"
            self.assertTrue(created_path.exists())

        self.assertTrue(result["created"])
        self.assertFalse(result["reused_existing"])
        self.assertFalse(result["forced_create"])
        self.assertNotEqual(result["concept_id"], existing.id)
        self.assertEqual(len(db.added), 1)

    def test_session_synthesis_creates_new_concept_and_records_scope_metadata(self):
        existing = _concept(7, "另一概念", tags=["synthesis"])
        db = _FakeDB(
            nodes=[existing],
            papers=[SimpleNamespace(id=1)],
        )

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch("routers.ask.WIKI_CONCEPTS_DIR", base), patch(
                "routers.ask._concept_page_path",
                side_effect=lambda node: base / f"{node.id:04d}-new-concept.md",
            ), patch("routers.ask.reconcile_concept_pages_dir"), patch(
                "routers.ask.wiki_search_service.rebuild_index"
            ):
                result = create_concept_from_synthesis(
                    SynthesisConceptInput(
                        title="闭环世界模型",
                        body="总结正文",
                        source_question="什么是闭环世界模型？",
                        source_questions=["什么是闭环世界模型？", "它和 Transformer 有什么关系？"],
                        synthesis_scope="session",
                        source_paper_ids=[1, 999],
                        tags=["synthesis"],
                    ),
                    db=db,
                )

            created_path = base / f"{result['concept_id']:04d}-new-concept.md"
            self.assertTrue(created_path.exists())
            text = created_path.read_text(encoding="utf-8")
            self.assertIn("# 闭环世界模型", text)
            self.assertIn('synthesis_scope: "session"', text)
            self.assertIn('synthesis_question: "什么是闭环世界模型？"', text)
            self.assertIn('- "它和 Transformer 有什么关系？"', text)

        self.assertTrue(result["created"])
        self.assertFalse(result["reused_existing"])
        self.assertFalse(result["forced_create"])
        self.assertNotEqual(result["concept_id"], existing.id)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].source_paper_ids, [1])
        self.assertEqual(db.added[0].tags, ["synthesis"])


if __name__ == "__main__":
    unittest.main()
