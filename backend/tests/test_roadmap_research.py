"""No live models, library data or external network calls are used here."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import roadmap_research as research


def paper(pid, year, branch="Foundations"):
    return {"id": pid, "title": "Verified paper " + pid, "year": year, "authors": ["Author"],
            "abstract": f"The verified method {pid} learns reusable visual representations.",
            "arxivId": None, "doi": "10.1000/" + pid, "sourceUrl": "https://doi.org/10.1000/" + pid,
            "reason": "A relevant milestone", "branch": branch}


def synthesis():
    return {"papers": [{"id": "a", "reason": "Introduces reusable features", "branch": "Foundations"},
                        {"id": "b", "reason": "Extends reusable features", "branch": "Applications"}],
            "edges": [{"source": "a", "target": "b", "relation": "builds_on", "reason": "An inferred extension",
                       "evidence": [{"paperId": "b", "quote": "learns reusable visual representations"}]}]}


def board():
    return {"id": "vision", "name": "视觉", "category": "视觉", "nodes": [], "edges": [],
            "viewport": {"x": 80, "y": 80, "zoom": 1}}


def test_skill_is_the_actual_runtime_instruction_and_plan_is_bounded():
    instructions = research.skill_instructions()
    assert "shitu-roadmap" in instructions
    assert "## synthesize" in instructions
    assert research.validate_plan({"queries": ["vision"] * 10, "papers": []})["queries"] == ["vision"] * 3
    with pytest.raises(ValueError):
        research.validate_plan({"queries": [], "papers": [{"title": None}]})
    assert research.decode('```json\n{"papers":[]}\n```') == {"papers": []}
    with pytest.raises(ValueError):
        research.decode('[]')


def test_grounding_rejects_unknown_ids_fabricated_quotes_reverse_years_and_self_loops():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    good = copy.deepcopy(raw["edges"][0])
    raw["papers"].append({"id": "invented", "reason": "Fiction", "branch": "Unknown"})
    raw["edges"] = [dict(good, source="invented"), dict(good, target="a"),
                    dict(good, source="b", target="a"), dict(good, relation=[]),
                    dict(good, evidence=[{"paperId": "b", "quote": "This sentence was never in any source."}]), good]
    selected, edges, rejected = research.select_and_ground(raw, catalog)
    assert set(selected) == {"a", "b"}
    assert len(edges) == 1
    assert rejected == 6
    assert edges[0]["certainty"] == "inferred"
    assert edges[0]["evidence"][0]["locator"] == catalog["b"]["sourceUrl"]
    assert edges[0]["evidence"][0]["source"] == "text"


def test_preview_has_real_branch_groups_and_does_not_write_papers_to_library():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    initial = board()
    proposal, selected = research.proposal_for(initial, {}, catalog, synthesis(), [])
    assert initial["nodes"] == []
    groups = [n for n in proposal["board"]["nodes"] if n["kind"] == "group"]
    cards = [n for n in proposal["board"]["nodes"] if n["kind"] == "paper"]
    assert {g["text"] for g in groups} == {"Foundations", "Applications"}
    assert all(n["groupId"] in {g["id"] for g in groups} for n in cards)
    assert {p["id"] for p in selected} == {"a", "b"}
    assert proposal["board"]["edges"][0]["analysis"]["certainty"] == "inferred"


def test_regeneration_keeps_user_notes_groups_unselected_cards_and_locked_edges():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    preview, _ = research.proposal_for(board(), {}, catalog, synthesis(), [])
    old = preview["board"]
    card = next(n for n in old["nodes"] if n["kind"] == "paper")
    card.update(text="My handwritten note", locked=True, x=900, y=1200)
    old["edges"][0].update(locked=True, label="My connection")
    raw = synthesis()
    raw["papers"] = raw["papers"][1:]
    raw["edges"] = []
    result, _ = research.proposal_for(old, {}, catalog, raw, [])
    assert next(n for n in result["board"]["nodes"] if n["id"] == card["id"]) == card
    assert result["board"]["edges"] == old["edges"]
    assert len(result["board"]["nodes"]) == len(old["nodes"])
    assert result["protected"] == 1


def test_board_scoped_ids_are_stable_and_existing_arxiv_is_not_downgraded():
    candidate = paper("source-id", "2010")
    first = research.scope_catalog("board-a", [candidate], [])
    second = research.scope_catalog("board-b", [candidate], [])
    assert set(first) != set(second)
    assert research.scope_catalog("board-a", [candidate], []) == first
    existing = next(iter(first.values()))
    existing["arxivId"] = "1706.03762"
    existing["sourceUrl"] = "https://arxiv.org/abs/1706.03762"
    updated = research.scope_catalog("board-a", [candidate], [existing])
    assert len(updated) == 1
    assert updated[existing["id"]]["arxivId"] == "1706.03762"


def test_selected_papers_cannot_exceed_thirty():
    catalog = {str(i): paper(str(i), "2020") for i in range(45)}
    raw = {"papers": [{"id": p, "branch": "one", "reason": "Milestone"} for p in catalog], "edges": []}
    selected, _, _ = research.select_and_ground(raw, catalog)
    assert len(selected) == 30


def test_citation_requires_reference_or_explicit_mention_in_citing_abstract():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    raw["edges"][0]["relation"] = "cites"
    # A real quotation about a shared topic does not establish a citation.
    assert research.select_and_ground(raw, catalog)[1] == []
    catalog["b"]["references"] = ["https://doi.org/10.1000/a"]
    edge = research.select_and_ground(raw, catalog)[1][0]
    assert edge["evidence"][0]["quote"] == "https://doi.org/10.1000/a"
    assert edge["evidence"][0]["paperId"] == "b"
    catalog["b"]["references"] = []
    catalog["b"]["abstract"] = "We compare against Verified paper a in our experiments."
    raw["edges"][0]["evidence"] = [{"paperId": "b", "quote": catalog["b"]["abstract"]}]
    assert len(research.select_and_ground(raw, catalog)[1]) == 1
    # The cited work's own title is not evidence that the target cites it.
    raw["edges"][0]["evidence"] = [{"paperId": "a", "quote": catalog["a"]["title"]}]
    assert research.select_and_ground(raw, catalog)[1] == []


def test_catalog_reuses_punctuation_variant_but_not_a_different_same_title_work():
    existing = paper("old", "2010")
    existing.update(title="Learning: representations", doi=None)
    incoming = dict(existing, id="new", title="Learning representations", arxivId="1706.03762",
                    sourceUrl="https://arxiv.org/abs/1706.03762")
    result = research.scope_catalog("board-a", [incoming], [existing])
    assert list(result) == ["old"]
    assert result["old"]["arxivId"] == "1706.03762"
    incoming["authors"] = ["An entirely different author"]
    result = research.scope_catalog("board-a", [incoming], [existing])
    assert len(result) == 2
    assert result["old"] == existing


def test_fulltext_evidence_requires_correct_page_and_metadata_cannot_claim_explicit():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    raw["edges"][0]["certainty"] = "explicit"
    assert research.select_and_ground(raw, catalog)[1][0]["certainty"] == "inferred"
    excerpt = "We extend the method of Verified paper a with a new visual objective."
    catalog["b"].update(fullText=excerpt, fullTextPages=[{"page": 3, "text": excerpt}],
                        fullTextSource=catalog["b"]["sourceUrl"])
    raw["edges"][0]["evidence"] = [{"paperId": "b", "quote": excerpt, "page": 9}]
    assert research.select_and_ground(raw, catalog)[1] == []
    raw["edges"][0]["evidence"][0]["page"] = 3
    edge = research.select_and_ground(raw, catalog)[1][0]
    assert edge["certainty"] == "explicit"
    assert edge["evidence"][0]["locator"].endswith("#page=3")
    proposal, metadata = research.proposal_for(board(), {}, catalog, raw, [])
    assert proposal["board"]["edges"][0]["analysis"]["certainty"] == "explicit"
    assert all("fullText" not in p and "fullTextPages" not in p for p in metadata)


def test_fulltext_evaluation_without_verified_quotes_is_not_used():
    candidate = paper("a", "2020")
    candidate.update(fullTextPages=[{"page": 2, "text": "We evaluate on three independent datasets."}])
    forged = research.ground_evaluation({"contribution": "A fabricated result", "evidence": [
        {"quote": "We achieved human level understanding.", "page": 2},
    ]}, candidate)
    assert "error" in forged and "contribution" not in forged
    checked = research.ground_evaluation({"method": "Three datasets", "evidence": [
        {"quote": "We evaluate on three independent datasets.", "page": 2},
    ]}, candidate)
    assert checked["evidence"][0]["page"] == 2


def test_bibliography_elsewhere_does_not_turn_unrelated_excerpt_into_explicit_citation():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    unrelated = "We train every model for ten epochs."
    catalog["b"].update(fullText=unrelated + "\nReferences\nVerified paper a",
                        fullTextPages=[{"page": 1, "text": unrelated}, {"page": 9, "text": "Verified paper a"}])
    raw = synthesis()
    raw["edges"][0].update(relation="cites", certainty="explicit",
                            evidence=[{"paperId": "b", "quote": unrelated, "page": 1}])
    assert research.select_and_ground(raw, catalog)[1] == []
    catalog["b"]["references"] = ["10.1000/a"]
    edge = research.select_and_ground(raw, catalog)[1][0]
    assert edge["certainty"] == "inferred"
    assert edge["evidence"][0]["quote"] == "10.1000/a"


def test_screening_is_bounded_and_records_exclusions():
    catalog = {str(i): paper(str(i), "2020") for i in range(40)}
    raw = {"papers": [{"id": str(i), "reason": "A milestone", "branch": "one"} for i in range(30)],
           "keyPaperIds": list(catalog), "excluded": [{"id": "39", "reason": "Outside the question"},
                                                      {"id": "fiction", "reason": "Invented"}]}
    selected, keys, excluded, _ = research.screen_candidates(raw, catalog)
    assert len(selected) == 30 and len(keys) == 8
    assert excluded == [{"id": "39", "reason": "Outside the question"}]


def test_research_model_inherits_configured_domain_model_unless_explicit():
    from model_gateway import ensure_model_gateway_config

    cfg = {"model_gateway": {"task_bindings": {"domain_connect": {"model_id": "openai/gpt-4.1", "reasoning_effort": "high"}}}}
    resolved = ensure_model_gateway_config(copy.deepcopy(cfg))["model_gateway"]["task_bindings"]
    assert resolved["roadmap_research"] == resolved["domain_connect"]
    cfg["model_gateway"]["task_bindings"]["roadmap_research"] = "openai/gpt-4o-mini"
    assert ensure_model_gateway_config(cfg)["model_gateway"]["task_bindings"]["roadmap_research"]["model_id"] == "openai/gpt-4o-mini"


@pytest.fixture
def research_db(tmp_path, monkeypatch):
    from models import Base, RoadmapRun, RoadmapWorkspace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'library.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(research, "SessionLocal", sessions)
    monkeypatch.setattr("services.roadmap_state.SessionLocal", sessions)
    with sessions() as db:
        db.add(RoadmapWorkspace(id=1, revision=1, state={"boards": [board()], "activeId": "vision"}))
        db.add(RoadmapRun(id="run", board_id="vision", status="pending", snapshot={"board": board(), "heights": {}}))
        db.commit()
    yield sessions
    engine.dispose()


def test_complete_research_job_keeps_live_canvas_and_library_untouched(research_db, tmp_path, monkeypatch):
    from models import Paper, RoadmapPaper, RoadmapRun, RoadmapWorkspace
    from services.task_executor import Context
    from services.task_store import TaskStore

    monkeypatch.setattr("config.load_config", dict)
    monkeypatch.setattr("config.task_model_id", lambda cfg, task: "mock-model")
    monkeypatch.setattr("model_gateway.runtime.preflight_task_runtime", lambda *a: None)
    monkeypatch.setattr("services.roadmap_sources.collect_candidates", lambda *a, **kw: {
        "papers": [paper("a", "2010"), paper("b", "2020")], "warnings": [],
    })
    excerpt = "The full paper evaluates reusable visual features on three independent datasets."
    monkeypatch.setattr("services.roadmap_fulltext.read_key_papers", lambda papers, **kw: {
        "papers": [dict(p, fullText=excerpt, fullTextPages=[{"page": 2, "text": excerpt}],
                        fullTextStatus="read", fullTextSource=p["sourceUrl"],
                        fullTextCoverage={"pagesVisited": 1, "totalPages": 1, "truncated": False}) for p in papers],
        "warnings": [],
    })
    phases = []

    def model(_client, model_id, system, request, **kwargs):
        assert kwargs["task_id"] == "roadmap_research"
        assert "shitu-roadmap" in system
        data = json.loads(request)
        if "当前阶段：plan" in system:
            phases.append("plan")
            return json.dumps({"researchQuestion": "How did visual representations develop?", "scope": "Foundations",
                               "inclusionCriteria": ["Primary methods"], "exclusionCriteria": ["Off-topic"],
                               "queries": ["vision foundations"], "papers": []})
        if "当前阶段：evaluate" in system:
            phases.append("evaluate")
            return json.dumps({"contribution": "Reusable visual features", "evidence": [{"quote": excerpt, "page": 2}]})
        phases.append("screen" if "当前阶段：screen" in system else "synthesize")
        ids = [p["id"] for p in data["papers"]]
        result = synthesis()
        for selected, pid in zip(result["papers"], ids):
            selected["id"] = pid
        result["edges"][0].update(source=ids[0], target=ids[1])
        result["edges"][0]["evidence"][0]["paperId"] = ids[1]
        return json.dumps(result)

    monkeypatch.setattr("services.wiki_compiler._call_llm", model)
    queue = TaskStore(tmp_path / "tasks.db")
    queue.initialize()
    job = queue.submit("roadmap", "roadmap:vision", {"run_id": "run"})
    result = research.generate(Context(queue, job), job["payload"])
    assert phases == ["plan", "screen", "evaluate", "evaluate", "synthesize"]
    assert result["preview"]
    with research_db() as db:
        assert db.get(RoadmapRun, "run").status == "preview"
        assert len(db.get(RoadmapRun, "run").proposal["board"]["edges"]) == 1
        assert db.get(RoadmapRun, "run").proposal["report"]
        assert db.get(RoadmapWorkspace, 1).state["boards"][0]["nodes"] == []
        assert db.query(RoadmapPaper).count() == 2
        assert db.query(Paper).count() == 0


def test_cancelled_research_never_calls_model(research_db, monkeypatch):
    from models import RoadmapRun

    with research_db() as db:
        db.get(RoadmapRun, "run").status = "cancelled"
        db.commit()
    monkeypatch.setattr("services.wiki_compiler._call_llm", lambda *a, **kw: pytest.fail("No model after cancellation"))
    assert research.generate(None, {"run_id": "run"}) == {"cancelled": True}


def test_category_rename_preserves_both_workspaces(research_db):
    from models import DomainWorkspace, RoadmapWorkspace
    from services.domain_categories import rename_category_canvas

    with research_db() as db:
        db.add(DomainWorkspace(id=1, revision=1, state={"boards": [board()], "activeId": "vision"}, admitted={}))
        db.commit()
        rename_category_canvas(db, "视觉", "视觉生成")
        db.commit()
        for model in (DomainWorkspace, RoadmapWorkspace):
            row = db.get(model, 1)
            assert row.state["boards"][0]["id"] == "vision"
            assert row.state["boards"][0]["name"] == "视觉生成"
            assert row.revision == 2
