"""Domain integration tests use an isolated SQLite DB, never the user's library."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import get_db
from models import Base, DomainWorkspace, Paper
from routers.domains import router
from services.paper_dedupe_service import _rewrite_domain_references


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(
        "services.paper_category_service._active_cache",
        ["LLM", "VLM", "VLA", "三维重建-静态", "三维重建-动态", "世界模型", "其他"],
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    with session() as db:
        for paper_id, processed, response in [
            (
                "ready",
                True,
                {
                    "title": "Paper A",
                    "year": 2020,
                    "core_contribution": "Contribution",
                    "abstract_summary": "Summary",
                    "problem": "Problem",
                    "motivation": "Motivation",
                },
            ),
            ("partial", True, {"abstract_summary": "Only a summary"}),
            ("pending", False, {"abstract_summary": "Not yet processed"}),
            ("empty", True, {}),
            ("malformed", True, "broken JSON"),
            ("array", True, []),
        ]:
            db.add(
                Paper(
                    id=paper_id,
                    filepath=f"{paper_id}.pdf",
                    filename=f"{paper_id}.pdf",
                    file_hash=paper_id,
                    processed=processed,
                    raw_llm_response=json.dumps(response)
                    if not isinstance(response, str)
                    else response,
                )
            )
        db.commit()
    app = FastAPI()
    app.include_router(router)

    def dependency():
        with session() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        yield client, session
    engine.dispose()


def node(key="instance-1", paper="ready", **kwargs):
    return {"id": key, "kind": "paper", "paperId": paper, "x": 100, "y": 200, **kwargs}


def test_defaults_inherit_shared_categories_and_cannot_be_deleted_locally(context):
    client, _ = context
    snapshot = client.get("/api/domains").json()
    assert [b["name"] for b in snapshot["state"]["boards"]] == [
        "LLM",
        "VLM",
        "VLA",
        "三维重建-静态",
        "三维重建-动态",
        "世界模型",
        "其他",
    ]
    assert all(b["name"] == b["category"] for b in snapshot["state"]["boards"])
    assert client.get("/api/domains").json() == snapshot
    snapshot["state"] = {"boards": [], "activeId": None}
    assert client.put("/api/domains", json=snapshot).status_code == 422


def test_review_eligibility_uses_only_the_four_review_fields(context):
    client, _ = context
    response = client.get("/api/domains/papers")
    assert response.status_code == 200
    papers = {p["id"]: p for p in response.json()}
    assert papers["ready"]["eligible"] and not papers["ready"]["incomplete"]
    assert papers["partial"]["eligible"] and papers["partial"]["incomplete"]
    assert papers["partial"]["sections"]["problem"] == ""
    for key in ("pending", "empty", "malformed", "array"):
        assert not papers[key]["eligible"]


def test_duplicate_instances_notes_edges_and_viewport_roundtrip(context):
    client, session = context
    snapshot = client.get("/api/domains").json()
    board = snapshot["state"]["boards"][0]
    board["nodes"] = [
        node(text="First independent note"),
        node("instance-2", text="Second note", x=700),
        {"id": "group", "kind": "group", "text": "Architecture", "x": 20, "y": 30},
    ]
    board["nodes"][0]["groupId"] = "group"
    board["edges"] = [
        {
            "id": "edge",
            "source": "instance-1",
            "target": "instance-2",
            "label": "Comparison",
        }
    ]
    board["viewport"] = {"x": -40, "y": 900, "zoom": 0.6}
    assert client.put("/api/domains", json=snapshot).status_code == 200
    saved = client.get("/api/domains").json()
    assert saved["state"]["boards"][0]["viewport"] == board["viewport"]
    assert saved["state"]["boards"][0]["nodes"][1]["text"] == "Second note"
    with session() as db:
        assert db.query(Paper).count() == 6
        assert not db.get(Paper, "ready").notes


@pytest.mark.parametrize("paper", ["pending", "empty", "malformed", "missing"])
def test_server_rejects_ineligible_import_even_if_client_bypassed(context, paper):
    client, _ = context
    snapshot = client.get("/api/domains").json()
    snapshot["state"]["boards"][0]["nodes"] = [node(paper=paper)]
    assert client.put("/api/domains", json=snapshot).status_code == 422
    assert client.get("/api/domains").json()["revision"] == 1


def test_existing_references_survive_review_loss_and_can_be_undone(context):
    client, session = context
    snapshot = client.get("/api/domains").json()
    snapshot["state"]["boards"][0]["nodes"] = [node()]
    assert client.put("/api/domains", json=snapshot).status_code == 200
    with session() as db:
        db.get(Paper, "ready").raw_llm_response = "{}"
        db.get(Paper, "ready").processed = False
        db.commit()
    saved = client.get("/api/domains").json()
    removed = copy.deepcopy(saved)
    removed["state"]["boards"][0]["nodes"] = []
    assert client.put("/api/domains", json=removed).status_code == 200
    saved["revision"] += 1
    assert client.put("/api/domains", json=saved).status_code == 200  # Undo a removal
    saved = client.get("/api/domains").json()
    saved["state"]["boards"][0]["nodes"].append(node("new-instance"))
    assert client.put("/api/domains", json=saved).status_code == 422


def test_conflicting_save_does_not_overwrite_newer_data(context):
    client, _ = context
    snapshot = client.get("/api/domains").json()
    snapshot["state"]["boards"][0]["nodes"] = [node(text="First writer")]
    assert client.put("/api/domains", json=snapshot).status_code == 200
    snapshot["state"]["boards"][0]["nodes"][0]["text"] = "Stale writer"
    assert client.put("/api/domains", json=snapshot).status_code == 409
    assert (
        client.get("/api/domains").json()["state"]["boards"][0]["nodes"][0]["text"]
        == "First writer"
    )


@pytest.mark.parametrize(
    "nodes,edges",
    [
        ([node(), node()], []),
        ([node(groupId="missing")], []),
        ([{"id": "group", "kind": "group", "groupId": "group", "x": 0, "y": 0}], []),
        ([node()], [{"id": "edge", "source": "instance-1", "target": "missing"}]),
        ([{"id": "text", "kind": "text", "paperId": "ready", "x": 0, "y": 0}], []),
    ],
)
def test_invalid_graph_structure_is_rejected(context, nodes, edges):
    client, _ = context
    snapshot = client.get("/api/domains").json()
    snapshot["state"]["boards"][0].update(nodes=nodes, edges=edges)
    assert client.put("/api/domains", json=snapshot).status_code == 422


def test_dedupe_rewrites_all_instances_and_invalidates_stale_saves(context):
    client, session = context
    snapshot = client.get("/api/domains").json()
    snapshot["state"]["boards"][0]["nodes"] = [node(), node("instance-2")]
    assert client.put("/api/domains", json=snapshot).status_code == 200
    with session() as db:
        _rewrite_domain_references(db, {"ready": "partial"})
        db.commit()
        assert db.get(DomainWorkspace, 1).admitted == {
            "instance-1": "partial",
            "instance-2": "partial",
        }
    result = client.get("/api/domains").json()
    assert result["revision"] == 3
    assert [n["paperId"] for n in result["state"]["boards"][0]["nodes"]] == [
        "partial",
        "partial",
    ]


@pytest.mark.parametrize("populated", [False, True])
def test_legacy_upgrade_preserves_content_and_custom_domains(context, populated):
    from routers.domains import Board, WorkspaceState

    client, session = context
    legacy = WorkspaceState(
        boards=[
            Board(id="llm", name="LLM", nodes=[node(text="Keep this note")]),
            Board(
                id="3d", name="三维重建", nodes=[node("3d-paper")] if populated else []
            ),
            Board(id="custom", name="My topic"),
        ],
        activeId="llm",
    ).model_dump()
    for board in legacy["boards"]:
        board.pop("category")
    with session() as db:
        db.add(
            DomainWorkspace(
                id=1, revision=7, state=legacy, admitted={"instance-1": "ready"}
            )
        )
        db.commit()
    snapshot = client.get("/api/domains").json()
    assert snapshot["revision"] == 8
    assert snapshot["state"]["activeId"] == "llm"
    assert snapshot["state"]["boards"][0]["nodes"] == legacy["boards"][0]["nodes"]
    assert snapshot["state"]["boards"][-1]["id"] == "custom"
    assert any(b["id"] == "3d" for b in snapshot["state"]["boards"]) == populated
    assert client.get("/api/domains").json() == snapshot
    with session() as db:
        assert db.get(DomainWorkspace, 1).admitted == {"instance-1": "ready"}


def test_category_add_rename_remove_keeps_canvas_identity_and_contents(
    context, monkeypatch
):
    from routers import papers

    client, session = context
    snapshot = client.get("/api/domains").json()
    board = snapshot["state"]["boards"][0]
    board["nodes"] = [node(text="Saved note")]
    board["viewport"] = {"x": 20, "y": -100, "zoom": 0.5}
    assert client.put("/api/domains", json=snapshot).status_code == 200
    categories = [b["name"] for b in snapshot["state"]["boards"]]

    def set_categories(names):
        categories[:] = names
        monkeypatch.setattr(
            "services.paper_category_service._active_cache", list(names)
        )

    monkeypatch.setattr(papers, "set_active_categories", set_categories)
    monkeypatch.setattr(papers, "sync_record_from_paper", lambda *a, **kw: None)
    with session() as db:
        papers.rename_paper_category(
            "LLM", papers.CategoryRenameInput(new_name="Language models"), db
        )
    renamed = client.get("/api/domains").json()
    first = renamed["state"]["boards"][0]
    assert first["id"] == board["id"]
    assert first["category"] == first["name"] == "Language models"
    assert first["nodes"][0]["text"] == "Saved note"
    assert first["viewport"] == board["viewport"]
    assert client.put("/api/domains", json=snapshot).status_code == 409
    set_categories(categories + ["Robotics"])
    added = client.get("/api/domains").json()
    assert any(b["category"] == "Robotics" for b in added["state"]["boards"])
    set_categories([c for c in categories if c not in ("Language models", "VLM")])
    removed = client.get("/api/domains").json()["state"]
    kept = next(b for b in removed["boards"] if b["id"] == board["id"])
    assert kept["category"] is None and kept["nodes"] == first["nodes"]
    assert all(b["name"] != "VLM" for b in removed["boards"])
