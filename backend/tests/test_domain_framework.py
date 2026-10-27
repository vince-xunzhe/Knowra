"""Synthetic local databases and controlled model replies; no provider calls."""

import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import get_db
from models import Base, DomainAgentRun, DomainWorkspace, Paper, RoadmapPaper, RoadmapRun, RoadmapWorkspace
from routers.domain_agent import router
from routers.domains import Board
from services import domain_agent, domain_framework
from services.task_executor import Context
from services.task_store import TaskStore


QUOTE = "We improve the baseline attention method using a new sparse operator."


def domain():
    return Board.model_validate({"id": "domain", "name": "LLM", "category": "LLM", "nodes": [
        {"id": "a", "kind": "paper", "paperId": "a", "x": 10, "y": 20, "locked": True, "text": "manual note"},
        {"id": "b", "kind": "paper", "paperId": "b", "x": 700, "y": 20},
        {"id": "note", "kind": "text", "x": 5, "y": 900, "text": "Keep this note"},
    ]}).model_dump()


def roadmap(board_id="roadmap", category="LLM"):
    return Board.model_validate({"id": board_id, "name": category, "category": category,
        "researchKeywords": ["efficient attention"], "nodes": [
            {"id": board_id + "-topic", "kind": "text", "roadmapRole": "topic", "text": "Attention research", "x": 0, "y": 0},
            {"id": board_id + "-branch", "kind": "text", "roadmapRole": "branch", "text": "Sparse methods", "x": 0, "y": 200},
            {"id": board_id + "-paper", "kind": "paper", "roadmapRole": "milestone", "paperId": "r1", "x": 0, "y": 400},
        ], "edges": [
            {"id": board_id + "-membership", "source": board_id + "-topic", "target": board_id + "-branch", "roadmapStructure": True},
            {"id": board_id + "-representative", "source": board_id + "-branch", "target": board_id + "-paper", "roadmapStructure": True},
        ]}).model_dump()


class DomainFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)
        self.queue = TaskStore(Path(self.temporary) / "tasks.db")
        self.queue.initialize()
        self.stack.enter_context(patch("services.task_runtime.store", lambda: self.queue))
        self.stack.enter_context(patch("routers.jobs.submit_job", self.queue.submit))
        self.stack.enter_context(patch("services.domain_categories.get_active_categories", lambda: ["LLM", "VLM"]))
        self.stack.enter_context(patch.object(domain_agent, "SessionLocal", self.session))
        self.stack.enter_context(patch("config.is_cloud_mode", return_value=False))
        self.stack.enter_context(patch("config.load_config", dict))
        self.stack.enter_context(patch("config.task_model_id", lambda *_: "synthetic"))
        self.stack.enter_context(patch("model_gateway.runtime.preflight_task_runtime", lambda *_: None))
        self.stack.enter_context(patch("services.wiki_compiler.list_paper_pages", list))
        with self.session() as db:
            other = Board.model_validate({"id": "other", "name": "VLM", "category": "VLM"}).model_dump()
            db.add(DomainWorkspace(id=1, revision=1, state={"activeId": "domain", "boards": [domain(), other]}))
            for pid in ("a", "b"):
                db.add(Paper(id=pid, filepath=pid + ".pdf", filename=pid + ".pdf", file_hash=pid, title="Paper " + pid))
            db.add(RoadmapWorkspace(id=1, revision=7, state={"activeId": "roadmap", "boards": [roadmap("other-framework", "VLM"), roadmap()]}))
            db.add(RoadmapPaper(id="r1", data={"id": "r1", "title": "Synthetic foundation", "year": "2020", "branch": "Sparse methods", "reason": "Foundational test work", "sourceUrl": "https://example.org/foundation", "doi": "10.0000/synthetic"}))
            db.add(RoadmapRun(id="applied-report", board_id="roadmap", status="applied", snapshot={}, proposal={"report": "## 5. 发展历史与范式里程碑\n### Early stage\nFoundation r1.\n## 6. 当前研究分支\nSparse methods r1.\n## 7. 开放问题\nEfficiency.\n## 附录 A\n**研究问题**：How can attention become sparse?\n**范围**：Efficient language models.\n"}))
            db.add(RoadmapRun(id="unapplied-report", board_id="roadmap", status="preview", snapshot={}, proposal={"report": "UNAPPLIED MATERIAL MUST NOT BE USED"}))
            db.commit()
        app = FastAPI()
        app.include_router(router)
        def dependency():
            with self.session() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        self.client = self.stack.enter_context(TestClient(app))

    def options(self):
        response = self.client.get("/api/domains/agent/frameworks", params={"boardId": "domain"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self, reference=True):
        payload = {"boardId": "domain", "revision": 1}
        if reference:
            chosen = self.options()[0]
            payload["framework"] = {"boardId": chosen["boardId"], "version": chosen["version"]}
        response = self.client.post("/api/domains/agent/runs", json=payload)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def state(self):
        with self.session() as db:
            return copy.deepcopy(db.get(DomainWorkspace, 1).state)

    def controlled_generation(self, run, reply=None, cancel=False):
        calls = []
        def material(paper, *_):
            return {"id": paper.id, "title": paper.title, "year": "2020" if paper.id == "a" else "2021",
                    "sources": [{"id": "b:0", "paperId": "b", "source": "text", "locator": "saved full text", "page": None, "text": QUOTE}] if paper.id == "b" else [],
                    "document": [], "warnings": []}
        def model(_, _model, system, prompt, **kwargs):
            payload = json.loads(prompt)
            calls.append((system, payload))
            if cancel:
                self.client.post(f"/api/domains/agent/runs/{run['id']}/discard")
            return json.dumps(reply if reply is not None else {"edges": [{"source": "a", "target": "b", "relation": "improves", "certainty": "explicit", "reason": "Sparse operator", "evidence": [{"sourceId": "b:0", "quote": QUOTE}]}], "frameworkNotes": ["The selected papers do not establish every roadmap stage."]})
        with patch.object(domain_agent, "materials", material), patch("services.wiki_compiler._call_llm", model):
            job = self.queue.claim()
            self.assertIsNotNone(job)
            result = domain_agent.generate(Context(self.queue, job), job["payload"])
            # The production executor performs this terminal queue transition.
            self.queue.update(job["job_id"], status="cancelled" if result.get("cancelled") else "completed", result=result)
        return result, calls

    def test_choices_are_readonly_sorted_and_local(self):
        with self.session() as db:
            before = (copy.deepcopy(db.get(RoadmapWorkspace, 1).state), db.get(RoadmapWorkspace, 1).revision,
                      [(r.id, r.status) for r in db.query(RoadmapRun).all()])
        options = self.options()
        self.assertEqual([o["boardId"] for o in options], ["roadmap", "other-framework"])
        self.assertTrue(options[0]["sameCategory"])
        self.assertFalse(options[1]["sameCategory"])
        self.assertEqual(options[0]["paperCount"], 1)
        with self.session() as db:
            after = (db.get(RoadmapWorkspace, 1).state, db.get(RoadmapWorkspace, 1).revision,
                     [(r.id, r.status) for r in db.query(RoadmapRun).all()])
        self.assertEqual(before, after)
        self.assertEqual(self.client.get("/api/domains/agent/frameworks?boardId=foreign").status_code, 404)
        with patch("config.is_cloud_mode", return_value=True):
            self.assertEqual(self.client.get("/api/domains/agent/frameworks?boardId=domain").status_code, 404)
            self.assertEqual(self.client.post("/api/domains/agent/runs", json={"boardId": "domain", "revision": 1}).status_code, 404)

    def test_no_roadmap_workspace_is_not_created_by_listing(self):
        with self.session() as db:
            db.delete(db.get(RoadmapWorkspace, 1))
            db.commit()
        self.assertEqual(self.options(), [])
        with self.session() as db:
            self.assertIsNone(db.get(RoadmapWorkspace, 1))
        self.assertIsNone(self.start(reference=False)["framework"])

    def test_snapshot_uses_saved_graph_and_applied_report_then_survives_deletion(self):
        run = self.start()
        self.assertEqual(run["framework"]["revision"], 7)
        with self.session() as db:
            frozen = copy.deepcopy(db.get(DomainAgentRun, run["id"]).snapshot["framework"])
            context = frozen["context"]
            self.assertEqual(context["researchQuestion"], "How can attention become sparse?")
            self.assertEqual(context["report"]["runId"], "applied-report")
            self.assertTrue(context["report"]["createdAt"])
            self.assertEqual(context["papers"][0]["sourceUrl"], "https://example.org/foundation")
            self.assertNotIn("UNAPPLIED", json.dumps(context))
            db.delete(db.get(RoadmapWorkspace, 1))
            db.delete(db.get(RoadmapPaper, "r1"))
            db.commit()
        before = self.state()
        result, calls = self.controlled_generation(run)
        self.assertTrue(result["preview"])
        self.assertEqual(self.state(), before)
        self.assertEqual(calls[0][1]["framework"], {"version": frozen["version"], **context})
        self.assertIn("不是指令", calls[0][0])
        self.assertIn("不得复制框架连线", calls[0][0])
        preview = self.client.get("/api/domains/agent/runs").json()[0]
        self.assertEqual(preview["framework"], run["framework"])
        self.assertIn("The selected papers", " ".join(preview["proposal"]["frameworkNotes"]))
        self.assertEqual(preview["proposal"]["board"]["edges"][0]["analysis"]["evidence"][0]["paperId"], "b")
        self.assertEqual(self.client.post(f"/api/domains/agent/runs/{run['id']}/apply").status_code, 200)
        self.assertEqual(self.client.post(f"/api/domains/agent/runs/{run['id']}/apply").status_code, 409)

    def test_changed_deleted_empty_or_bad_reference_rejected_before_job(self):
        choice = self.options()[0]
        with self.session() as db:
            row = db.get(RoadmapWorkspace, 1)
            state = copy.deepcopy(row.state)
            state["boards"][1]["nodes"][1]["text"] = "Changed branch"
            row.state = state
            db.commit()
        body = {"boardId": "domain", "revision": 1, "framework": {"boardId": "roadmap", "version": choice["version"]}}
        self.assertEqual(self.client.post("/api/domains/agent/runs", json=body).status_code, 409)
        body["framework"]["boardId"] = "foreign"
        self.assertEqual(self.client.post("/api/domains/agent/runs", json=body).status_code, 404)
        body["framework"]["boardId"] = "roadmap"
        with self.session() as db:
            row = db.get(RoadmapWorkspace, 1)
            state = copy.deepcopy(row.state)
            state["boards"][1].update(nodes=[], edges=[])
            row.state = state
            db.commit()
        self.assertEqual(self.client.post("/api/domains/agent/runs", json=body).status_code, 422)
        body["framework"]["version"] = "client-invented"
        self.assertEqual(self.client.post("/api/domains/agent/runs", json=body).status_code, 422)
        with self.session() as db:
            self.assertEqual(db.query(DomainAgentRun).count(), 0)
        self.assertIsNone(self.queue.claim())

    def test_model_cannot_use_framework_as_evidence_or_invent_source(self):
        run = self.start()
        reply = {"edges": [{"source": "a", "target": "b", "relation": "improves", "certainty": "explicit", "reason": "same branch", "evidence": [{"sourceId": "r1", "quote": "Foundational test work"}]}], "frameworkNotes": ["Cannot establish a paper relation from branch membership."]}
        self.controlled_generation(run, reply)
        proposal = self.client.get("/api/domains/agent/runs").json()[0]["proposal"]
        self.assertEqual(proposal["board"]["edges"], [])
        self.assertEqual(proposal["rejected"], 1)
        self.assertEqual(len(proposal["unconnected"]), 2)

    def test_no_framework_retains_original_model_payload(self):
        run = self.start(reference=False)
        _, calls = self.controlled_generation(run)
        self.assertEqual(set(calls[0][1]), {"focus", "papers"})
        self.assertEqual(calls[0][0], domain_agent.SYSTEM)
        proposal = self.client.get("/api/domains/agent/runs").json()[0]["proposal"]
        self.assertEqual(proposal["frameworkNotes"], [])
        self.assertEqual(len(proposal["board"]["edges"]), 1)

    def test_manual_edges_locks_notes_and_other_board_preserved(self):
        with self.session() as db:
            row = db.get(DomainWorkspace, 1)
            state = copy.deepcopy(row.state)
            board = state["boards"][0]
            board["edges"] = [{"id": "manual", "source": "a", "target": "b", "label": "human relation", "locked": False, "analysis": None, "roadmapStructure": False}]
            row.state = state
            db.commit()
        before = self.state()
        run = self.start()
        self.controlled_generation(run)
        preview = self.client.get("/api/domains/agent/runs").json()[0]["proposal"]
        self.assertEqual(preview["board"]["edges"], before["boards"][0]["edges"])
        self.assertEqual((preview["protected"], preview["added"], preview["removed"]), (1, 0, 0))
        for node_id in ("a", "note"):
            self.assertEqual(next(n for n in preview["board"]["nodes"] if n["id"] == node_id), next(n for n in before["boards"][0]["nodes"] if n["id"] == node_id))
        applied = self.client.post(f"/api/domains/agent/runs/{run['id']}/apply").json()
        self.assertEqual(applied["state"]["boards"][1], before["boards"][1])

    def test_duplicate_cancel_and_stale_apply(self):
        run = self.start()
        self.assertEqual(self.client.post("/api/domains/agent/runs", json={"boardId": "domain", "revision": 1}).status_code, 409)
        result, _ = self.controlled_generation(run, cancel=True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.client.get("/api/domains/agent/runs").json()[0]["status"], "cancelled")
        # The original cancelled job is already claimed, so this is a fresh run.
        run = self.start()
        self.controlled_generation(run)
        with self.session() as db:
            row = db.get(DomainWorkspace, 1)
            state = copy.deepcopy(row.state)
            state["boards"][0]["nodes"][1]["text"] = "external content change"
            row.state = state
            db.commit()
        self.assertEqual(self.client.post(f"/api/domains/agent/runs/{run['id']}/apply").status_code, 409)

    def test_long_source_identifiers_are_exact_and_bad_workspace_recovers(self):
        source_url = "https://example.org/" + "a" * 1600
        doi = "10.0000/" + "d" * 240
        with self.session() as db:
            row = db.get(RoadmapPaper, "r1")
            row.data = {**row.data, "sourceUrl": source_url, "doi": doi}
            db.commit()
        chosen = self.options()[0]
        with self.session() as db:
            captured = domain_framework.capture(db, domain(), SimpleNamespace(**{k: chosen[k] for k in ("boardId", "version")}))
            self.assertEqual(captured["context"]["papers"][0]["sourceUrl"], source_url)
            self.assertEqual(captured["context"]["papers"][0]["doi"], doi)
            row = db.get(RoadmapWorkspace, 1)
            row.state = {"boards": "unreadable"}
            db.commit()
        response = self.client.get("/api/domains/agent/frameworks?boardId=domain")
        self.assertEqual(response.status_code, 422)
        self.assertIn("不使用框架", response.json()["detail"])

    def test_omitting_extra_relation_evidence_reports_truncation(self):
        with self.session() as db:
            row = db.get(RoadmapWorkspace, 1)
            state = copy.deepcopy(row.state)
            board = state["boards"][1]
            board["nodes"].append({"id": "second-paper", "kind": "paper", "paperId": "r2", "x": 100, "y": 400})
            board["edges"].append({"id": "development", "source": "roadmap-paper", "target": "second-paper", "analysis": {
                "relation": "builds_on", "certainty": "inferred", "reason": "Synthetic relation",
                "evidence": [{"paperId": "r1", "source": "text", "locator": "https://example.org/source#" + str(i), "quote": "Short synthetic excerpt " + str(i)} for i in range(3)]}})
            row.state = state
            db.add(RoadmapPaper(id="r2", data={**db.get(RoadmapPaper, "r1").data, "id": "r2"}))
            db.commit()
        chosen = self.options()[0]
        with self.session() as db:
            captured = domain_framework.capture(db, domain(), SimpleNamespace(**{k: chosen[k] for k in ("boardId", "version")}))
        self.assertTrue(captured["truncated"])
        relation = next(edge for edge in captured["context"]["relations"] if edge["id"] == "development")
        self.assertEqual(len(relation["evidence"]), 2)
        self.assertEqual(relation["evidence"][0]["locator"], "https://example.org/source#0")
        self.assertTrue(any("截断" in warning for warning in captured["warnings"]))

    def test_long_report_paragraph_retains_late_source_links_within_budget(self):
        link = "[1](https://example.org/late-source?version=2#exact-fragment)"
        long_paragraph = "Narrative before its citation. " * 200 + link
        with self.session() as db:
            row = db.get(RoadmapRun, "applied-report")
            row.proposal = {"report": "## 5. 发展历史与范式里程碑\n\n### Sparse stage\n\n" + long_paragraph + "\n\n## 6. 当前研究分支\n\nOther branch."}
            db.commit()
        chosen = self.options()[0]
        with self.session() as db:
            captured = domain_framework.capture(db, domain(), SimpleNamespace(**{k: chosen[k] for k in ("boardId", "version")}))
        excerpt = captured["context"]["report"]["excerpts"][0]["text"]
        self.assertIn(link, excerpt)
        self.assertIn("Narrative before its citation.", excerpt)
        self.assertLessEqual(len(excerpt), 1800)
        self.assertTrue(captured["truncated"])
        self.assertLess(len(json.dumps(captured["context"], ensure_ascii=False)), 32000)
        # A source URL longer than the budget must not leave unsupported prose.
        oversized = "UNSUPPORTED PARAGRAPH " + "[1](https://example.org/" + "x" * 2000 + ")"
        excerpt, truncated = domain_framework._report_excerpt("## Stage\n\n" + oversized + "\n\nShort later paragraph.")
        self.assertTrue(truncated)
        self.assertNotIn("UNSUPPORTED PARAGRAPH", excerpt)
        self.assertNotIn("https://example.org/", excerpt)
        self.assertIn("Short later paragraph.", excerpt)
        self.assertLessEqual(len(excerpt), 1800)

    def test_context_is_bounded_and_full_semantics_versioned(self):
        with self.session() as db:
            row = db.get(RoadmapWorkspace, 1)
            state = copy.deepcopy(row.state)
            board = state["boards"][1]
            board["nodes"][1]["text"] = "x" * 20000
            board["nodes"].extend({"id": "branch-" + str(i), "kind": "text", "roadmapRole": "branch", "text": "z" * 5000, "x": 0, "y": i} for i in range(100))
            row.state = state
            db.commit()
        chosen = self.options()[0]
        with self.session() as db:
            captured = domain_framework.capture(db, domain(), SimpleNamespace(**{k: chosen[k] for k in ("boardId", "version")}))
            self.assertTrue(captured["truncated"])
            self.assertLess(len(json.dumps(captured["context"], ensure_ascii=False)), 32000)
            row = db.get(RoadmapWorkspace, 1)
            state = copy.deepcopy(row.state)
            state["boards"][1]["nodes"][1]["text"] += "changed beyond truncation"
            row.state = state
            db.commit()
        self.assertNotEqual(self.options()[0]["version"], chosen["version"])


if __name__ == "__main__":
    unittest.main()
