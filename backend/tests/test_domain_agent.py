"""Agent runs use isolated library/queue databases and deterministic model replies."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import DomainAgentRun, DomainWorkspace
from routers.domain_agent import router
from routers.domains import Board
from services import domain_agent as agent
from services.domain_agent_layout import arrange
from services.task_executor import Context
from services.task_store import TaskStore
from test_domains import context, node  # noqa: F401


@pytest.fixture
def env(context, monkeypatch, tmp_path):  # noqa: F811 — imported pytest fixture
    client, session = context
    client.app.include_router(router)
    queue = TaskStore(tmp_path / "tasks.db")
    queue.initialize()
    monkeypatch.setattr("services.task_runtime.store", lambda: queue)
    monkeypatch.setattr("routers.jobs.submit_job", queue.submit)
    monkeypatch.setattr(agent, "SessionLocal", session)
    snap = client.get("/api/domains").json()
    snap["state"]["boards"][0]["nodes"] = [node("a"), node("b", "partial", x=700)]
    assert client.put("/api/domains", json=snap).status_code == 200
    snap = client.get("/api/domains").json()
    response = client.post(
        "/api/domains/agent/runs",
        json={
            "boardId": snap["state"]["boards"][0]["id"],
            "revision": snap["revision"],
        },
    )
    assert response.status_code == 202, response.text
    return client, session, queue, response.json()


def test_readonly_across_windows_other_board_and_cancel(env):
    client, _, queue, run = env
    snap = client.get("/api/domains").json()
    changed = copy.deepcopy(snap)
    changed["state"]["boards"][0]["nodes"][0]["text"] = "racing edit"
    assert client.put("/api/domains", json=changed).status_code == 409
    renamed = copy.deepcopy(snap)
    renamed["state"]["boards"][0]["name"] = "Renamed during generation"
    assert client.put("/api/domains", json=renamed).status_code == 409
    snap["state"]["boards"][0]["viewport"]["x"] += 10
    snap["state"]["boards"][1]["nodes"] = [node("other")]
    assert client.put("/api/domains", json=snap).status_code == 200
    assert (
        client.post(
            "/api/domains/agent/runs",
            json={"boardId": run["boardId"], "revision": snap["revision"] + 1},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/domains/agent/runs/{run['id']}/discard").json()["status"]
        == "cancelled"
    )
    assert queue.get(run["jobId"])["status"] == "cancelled"
    changed = client.get("/api/domains").json()
    changed["state"]["boards"][0]["nodes"][0]["text"] = "after cancel"
    assert client.put("/api/domains", json=changed).status_code == 200


def preview(session, run):
    with session() as db:
        row = db.get(DomainAgentRun, run["id"])
        board = copy.deepcopy(row.snapshot["board"])
        board["nodes"][0]["x"] = 999
        row.status = "preview"
        row.proposal = {"board": board}
        db.commit()


def test_apply_preserves_other_board_and_view_and_returns_single_undo(env):
    client, session, _, run = env
    preview(session, run)
    assert client.get("/api/domains/agent/runs").json()[0]["status"] == "preview"
    snap = client.get("/api/domains").json()
    snap["state"]["boards"][1]["nodes"] = [node("other", text="keep this")]
    snap["state"]["boards"][0]["viewport"]["zoom"] = 0.7
    assert client.put("/api/domains", json=snap).status_code == 200
    result = client.post(f"/api/domains/agent/runs/{run['id']}/apply")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["state"]["boards"][0]["nodes"][0]["x"] == 999
    assert data["state"]["boards"][1]["nodes"][0]["text"] == "keep this"
    assert data["state"]["boards"][0]["viewport"]["zoom"] == 0.7
    assert data["before"]["nodes"][0]["x"] == 100
    assert client.post(f"/api/domains/agent/runs/{run['id']}/apply").status_code == 409
    data["state"]["boards"][0] = data.pop("before")
    assert client.put("/api/domains", json=data).status_code == 200


def test_stale_preview_never_applies(env):
    client, session, _, run = env
    preview(session, run)
    with session() as db:
        row = db.get(DomainWorkspace, 1)
        state = copy.deepcopy(row.state)
        state["boards"][0]["nodes"][0]["text"] = "external migration"
        row.state = state
        db.commit()
    assert client.post(f"/api/domains/agent/runs/{run['id']}/apply").status_code == 409


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_worker_failure_releases_readonly(env, status):
    client, _, queue, run = env
    queue.update(run["jobId"], status=status, error="worker stopped")
    assert client.get("/api/domains/agent/runs").json()[0]["status"] == "failed"
    snap = client.get("/api/domains").json()
    snap["state"]["boards"][0]["nodes"][0]["text"] = "editable"
    assert client.put("/api/domains", json=snap).status_code == 200


def source(pid="partial", kind="pdf"):
    return {
        "id": "s1",
        "paperId": pid,
        "source": kind,
        "locator": "PDF 第 30 页",
        "page": 30 if kind == "pdf" else None,
        "text": "We improve the method proposed in Paper A using a new attention operator.",
    }


def relation():
    return {
        "source": "ready",
        "target": "partial",
        "relation": "improves",
        "certainty": "explicit",
        "reason": "Extends the attention operator",
        "evidence": [{"sourceId": "s1", "quote": source()["text"]}],
    }


def test_evidence_rejects_invented_quotes_and_wrong_papers():
    good = relation()
    bad = copy.deepcopy(good)
    bad["evidence"][0]["quote"] = "This quote does not exist in any source."
    accepted, rejected = agent.grounded_edges(
        [good, bad], {}, {"s1": source()}, "partial", {"ready", "partial"}
    )
    assert rejected == 1 and len(accepted) == 1
    assert accepted[0]["evidence"][0]["page"] == 30
    assert not agent.grounded_edges(
        [good], {}, {"s1": source("other")}, "partial", {"ready", "partial"}
    )[0]
    downgraded = agent.grounded_edges(
        [good], {}, {"s1": source(kind="review")}, "partial", {"ready", "partial"}
    )[0]
    assert downgraded[0]["certainty"] == "inferred"
    assert agent.grounded_edges([], {}, {}, "partial", {"ready", "partial"}) == ([], 0)


def test_full_pdf_tail_reference_is_retrievable(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    pages = [Page("Unrelated filler " * 120) for _ in range(40)] + [
        Page("References: Paper A. A new attention operator for language models.")
    ]
    monkeypatch.setattr("pypdf.PdfReader", lambda _: SimpleNamespace(pages=pages))
    paper = SimpleNamespace(
        id="partial",
        raw_llm_response="{}",
        filepath="test.pdf",
        extracted_text="",
        title="Paper B",
        filename="b.pdf",
    )
    result = agent.materials(paper, None, lambda: None)
    excerpts = agent.retrieve(result, "Paper A attention operator", 4)
    assert any("References: Paper A" in s["text"] for s in excerpts)
    assert any(s["page"] == 41 for s in result["sources"])


def board_fixture():
    return Board.model_validate(
        {
            "id": "b",
            "name": "LLM",
            "nodes": [
                node("a"),
                node("b", "partial", x=800),
                node("a2", x=5000),
                {"id": "note", "kind": "text", "x": 20, "y": 20, "text": "preserve"},
                {"id": "g", "kind": "group", "x": 500, "y": 500, "locked": True},
                node("b2", "partial", x=550, y=550, groupId="g"),
            ],
        }
    ).model_dump()


def test_duplicate_selection_locks_notes_and_layout():
    board = board_fixture()
    grounded = agent.grounded_edges(
        [relation()], {}, {"s1": source()}, "partial", {"ready", "partial"}
    )[0]
    edges = agent.instantiate(board, grounded * 2)
    assert len(edges) == 1 and edges[0]["source"] == "a" and edges[0]["target"] == "b"
    edges[0]["locked"] = True
    edges[0]["label"] = "human label"
    board["edges"] = edges
    assert agent.instantiate(board, grounded) == edges
    board["nodes"][0]["locked"] = True
    result = arrange(board, {}, {})
    assert result["edges"] == edges
    for key in ("a", "note", "g", "b2"):
        assert next(n for n in result["nodes"] if n["id"] == key) == next(
            n for n in board["nodes"] if n["id"] == key
        )
    Board.model_validate(result)


def mock_generation(monkeypatch):
    monkeypatch.setattr("config.load_config", dict)
    monkeypatch.setattr("config.task_model_id", lambda *_: "fake")
    monkeypatch.setattr("model_gateway.runtime.preflight_task_runtime", lambda *_: None)
    monkeypatch.setattr("services.wiki_compiler.list_paper_pages", list)
    monkeypatch.setattr(
        agent,
        "materials",
        lambda p, *_: {
            "id": p.id,
            "title": p.id,
            "year": "2020" if p.id == "ready" else "2021",
            "sources": [source()] if p.id == "partial" else [],
            "warnings": [],
        },
    )


@pytest.mark.parametrize("mode", ["success", "cancel", "invalid"])
def test_generation_preview_never_mutates_live_canvas(env, monkeypatch, mode):
    client, _session, queue, run = env
    mock_generation(monkeypatch)
    before = client.get("/api/domains").json()

    def model(*args, **kwargs):
        if mode == "cancel":
            assert (
                client.post(f"/api/domains/agent/runs/{run['id']}/discard").status_code
                == 200
            )
        return "bad json" if mode == "invalid" else json.dumps({"edges": [relation()]})

    monkeypatch.setattr("services.wiki_compiler._call_llm", model)
    job = queue.claim()
    if mode == "invalid":
        with pytest.raises(ValueError):
            agent.generate(Context(queue, job), job["payload"])
    else:
        result = agent.generate(Context(queue, job), job["payload"])
        assert result.get("preview" if mode == "success" else "cancelled")
    assert client.get("/api/domains").json() == before
    current = client.get("/api/domains/agent/runs").json()[0]
    assert (
        current["status"]
        == {"success": "preview", "cancel": "cancelled", "invalid": "failed"}[mode]
    )
    if mode == "success":
        assert len(current["proposal"]["board"]["edges"]) == 1
        assert current["proposal"]["protected"] == 0


def test_model_binding_inherits_and_can_override():
    from model_gateway import ensure_model_gateway_config

    cfg = ensure_model_gateway_config(
        {
            "vlm_model": "gpt-4.1",
            "model_gateway": {
                "task_bindings": {
                    "paper_extract": {
                        "model_id": "openai/gpt-4.1",
                        "reasoning_effort": "high",
                    }
                }
            },
        }
    )
    bindings = cfg["model_gateway"]["task_bindings"]
    assert bindings["domain_connect"] == bindings["paper_extract"]
    cfg["model_gateway"]["task_bindings"]["domain_connect"] = {
        "model_id": "openai/gpt-4o",
        "reasoning_effort": "low",
    }
    assert (
        ensure_model_gateway_config(cfg)["model_gateway"]["task_bindings"][
            "domain_connect"
        ]["model_id"]
        == "openai/gpt-4o"
    )


def test_layout_respects_locked_child_in_unlocked_group_and_preserves_text():
    board = board_fixture()
    group = next(n for n in board["nodes"] if n["id"] == "g")
    group["locked"] = False
    child = next(n for n in board["nodes"] if n["id"] == "b2")
    child["locked"] = True
    child["text"] = "Protected note"
    board["nodes"][0]["groupId"] = "g"
    result = arrange(board, {}, {})
    mapped = {n["id"]: n for n in result["nodes"]}
    assert mapped["b2"] == child
    assert (mapped["g"]["x"], mapped["g"]["y"]) == (group["x"], group["y"])
    assert mapped["a"]["groupId"] == "g"
    assert mapped["a"]["y"] >= child["y"] + child["height"]


def test_duplicate_cards_prefer_same_group_over_shorter_distance():
    board = board_fixture()
    board["nodes"][2]["groupId"] = "g"
    grounded = agent.grounded_edges(
        [relation()], {}, {"s1": source()}, "partial", {"ready", "partial"}
    )[0]
    board["nodes"][0]["groupId"] = "other-group"
    edges = agent.instantiate(board, grounded)
    assert (edges[0]["source"], edges[0]["target"]) == ("a2", "b2")


def test_cycle_and_wide_cards_do_not_fail_or_overlap():
    board = board_fixture()
    board["nodes"] = board["nodes"][:3]
    for n in board["nodes"]:
        n["width"] = 800
    board["edges"] = [
        {
            "id": "cycle1",
            "source": "a",
            "target": "b",
            "label": "human",
            "locked": True,
        },
        {
            "id": "cycle2",
            "source": "b",
            "target": "a",
            "label": "human",
            "locked": True,
        },
    ]
    result = arrange(board, {}, {})
    assert result["edges"] == board["edges"]
    cards = sorted(result["nodes"], key=lambda n: n["y"])
    assert all(a["y"] + a["height"] < b["y"] for a, b in zip(cards, cards[1:]))  # noqa: RUF007 — Python 3.9
    Board.model_validate(result)


@pytest.mark.parametrize("cancel", [False, True])
def test_deep_reading_runs_inside_durable_preview_job(env, monkeypatch, cancel):
    client, _session, queue, run = env
    mock_generation(monkeypatch)
    title = "VectorNet: Efficient Structured Representation Learning"

    def material(p, *_):
        return {
            "id": p.id,
            "title": title if p.id == "ready" else "The follow-up paper",
            "year": "2020" if p.id == "ready" else "2022",
            "sources": [],
            "warnings": [],
            "document": []
            if p.id == "ready"
            else [
                {
                    "page": 2,
                    "section": "body",
                    "text": "We use the method [29] as our main experimental baseline.",
                },
                {
                    "page": 20,
                    "section": "references",
                    "text": f"[29] Jane Smith. {title}. 2020.",
                },
            ],
        }

    monkeypatch.setattr(agent, "materials", material)
    calls = []

    def model(_, _model, _system, prompt, **kwargs):
        payload = json.loads(prompt)
        calls.append(payload)
        if "focus" in payload:
            return '{"edges":[]}'
        body = next(s for s in payload["excerpts"] if s["section"] == "body")
        if cancel:
            client.post(f"/api/domains/agent/runs/{run['id']}/discard")
        evidence = [{"sourceId": body["sourceId"], "quote": body["text"]}]
        return json.dumps(
            {
                "relation": "compares",
                "certainty": "explicit",
                "reason": "Experimental baseline",
                "evidence": evidence,
                "findings": [
                    {
                        "aspect": "purpose",
                        "text": "Used as the main baseline.",
                        "evidence": evidence,
                    }
                ],
            }
        )

    monkeypatch.setattr("services.wiki_compiler._call_llm", model)
    before = client.get("/api/domains").json()
    job = queue.claim()
    result = agent.generate(Context(queue, job), job["payload"])
    assert len(calls) == 2
    assert client.get("/api/domains").json() == before
    current = client.get("/api/domains/agent/runs").json()[0]
    if cancel:
        assert result["cancelled"] and current["status"] == "cancelled"
    else:
        analysis = current["proposal"]["board"]["edges"][0]["analysis"]
        assert analysis["discussion"]["status"] == "analyzed"
        assert analysis["discussion"]["findings"][0]["evidence"][0]["page"] == 2
