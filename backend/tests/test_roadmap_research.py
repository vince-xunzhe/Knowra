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


def test_preview_has_topic_branch_tree_and_distinct_evidence_backed_edges():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    initial = board()
    proposal, selected = research.proposal_for(initial, {}, catalog, synthesis(), [])
    assert initial["nodes"] == []
    topics = [n for n in proposal["board"]["nodes"] if n["roadmapRole"] == "topic"]
    branches = [n for n in proposal["board"]["nodes"] if n["roadmapRole"] == "branch"]
    cards = [n for n in proposal["board"]["nodes"] if n["kind"] == "paper"]
    assert len(topics) == 1 and topics[0]["text"] == "视觉"
    assert {n["text"] for n in branches} == {"Foundations", "Applications"}
    assert all(n["kind"] == "text" for n in topics + branches)
    assert all(n["groupId"] is None for n in cards)
    assert max(n["y"] for n in topics) < min(n["y"] for n in branches)
    assert max(n["y"] for n in branches) < min(n["y"] for n in cards)
    assert len({n["x"] for n in branches}) == 2
    structures = [edge for edge in proposal["board"]["edges"] if edge["roadmapStructure"]]
    assert len(structures) == 4 and all(edge["analysis"] is None for edge in structures)
    assert {p["id"] for p in selected} == {"a", "b"}
    assert proposal["board"]["edges"][0]["analysis"]["certainty"] == "inferred"


def test_vertical_layout_stacks_branches_and_develops_papers_to_the_right():
    from services.roadmap_layout import arrange_tree

    catalog = {pid: paper(pid, year) for pid, year in (("a", "2010"), ("b", "2012"), ("c", "2020"))}
    raw = {
        "papers": [{"id": pid, "reason": "A milestone", "branch": "Foundations"} for pid in catalog],
        "edges": [{"source": "a", "target": "c", "relation": "evolves_into", "reason": "A checked evolution",
                   "evidence": [{"paperId": "c", "quote": catalog["c"]["abstract"]}]}],
    }
    proposal, _ = research.proposal_for(board(), {}, catalog, raw, [])
    graph = proposal["board"]
    topic = next(node for node in graph["nodes"] if node["roadmapRole"] == "topic")
    branch = next(node for node in graph["nodes"] if node["roadmapRole"] == "branch")
    arranged = arrange_tree(graph, catalog, {}, topic["id"], {"Foundations": branch["id"]}, "vertical")
    nodes = {node.get("paperId") or node["id"]: node for node in arranged["nodes"]}
    assert nodes[topic["id"]]["x"] < nodes[branch["id"]]["x"] < nodes["a"]["x"] < nodes["c"]["x"]
    assert nodes["a"]["x"] == nodes["b"]["x"]
    assert nodes["a"]["y"] != nodes["b"]["y"]


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


def test_rerun_can_preserve_or_clear_unlocked_history_while_protecting_locks_and_notes():
    catalog = {pid: paper(pid, year) for pid, year in (("a", "2010"), ("b", "2020"), ("c", "2021"))}
    raw = {
        "papers": [{"id": pid, "reason": "A milestone", "branch": "Foundations"} for pid in catalog],
        "edges": [
            {"source": source, "target": target, "relation": "evolves_into", "reason": "Checked evolution",
             "evidence": [{"paperId": target, "quote": f"The verified method {target} learns reusable visual representations."}]}
            for source, target in (("a", "b"), ("b", "c"))
        ],
    }
    first, _ = research.proposal_for(board(), {}, catalog, raw, [])
    old = first["board"]
    relationships = [edge for edge in old["edges"] if not edge["roadmapStructure"]]
    relationships[0]["locked"] = True
    card = next(node for node in old["nodes"] if node.get("paperId") == "a")
    card["text"] = "Keep this note"
    rerun = {"papers": raw["papers"], "edges": []}

    preserved, _ = research.proposal_for(old, {}, catalog, rerun, [], "preserve")
    preserved_ids = {edge["id"] for edge in preserved["board"]["edges"]}
    assert all(edge["id"] in preserved_ids for edge in relationships)
    assert preserved["removed"] == 0

    cleared, _ = research.proposal_for(old, {}, catalog, rerun, [], "clear")
    cleared_ids = {edge["id"] for edge in cleared["board"]["edges"]}
    assert relationships[0]["id"] in cleared_ids
    assert relationships[1]["id"] not in cleared_ids
    assert cleared["removed"] == 1
    assert next(node for node in cleared["board"]["nodes"] if node["id"] == card["id"])["text"] == "Keep this note"


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


def test_source_refresh_preserves_saved_branch_until_synthesis_selects_a_replacement():
    existing = paper("old", "2010", branch="Saved branch")
    incoming = dict(existing, id="resolver", reason="", branch="", abstract="Updated source abstract")
    catalog = research.scope_catalog("vision", [incoming], [existing])
    assert catalog["old"]["branch"] == "Saved branch"
    assert catalog["old"]["reason"] == existing["reason"]
    assert catalog["old"]["abstract"] == "Updated source abstract"


def test_branch_reclassification_does_not_steal_an_existing_branch_label():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    raw["papers"][1]["branch"] = "Foundations"
    first, _ = research.proposal_for(board(), {}, catalog, raw, [])
    old_branch = next(node for node in first["board"]["nodes"] if node["roadmapRole"] == "branch")
    raw["papers"][0]["branch"] = "New direction"
    second, _ = research.proposal_for(first["board"], {}, catalog, raw, [])
    branches = [node for node in second["board"]["nodes"] if node["roadmapRole"] == "branch"]
    assert {node["text"] for node in branches} == {"Foundations", "New direction"}
    assert next(node for node in branches if node["text"] == "Foundations")["id"] == old_branch["id"]


def test_selected_papers_cannot_exceed_thirty():
    catalog = {str(i): paper(str(i), "2020") for i in range(45)}
    raw = {"papers": [{"id": p, "branch": "one", "reason": "Milestone"} for p in catalog], "edges": []}
    selected, _, _ = research.select_and_ground(raw, catalog)
    assert len(selected) == 30


@pytest.mark.parametrize(("relation", "label"), [("evolves_into", "演进"), ("branches_into", "分化"),
                                               ("enables", "突破推动"), ("unifies", "融合")])
def test_development_relations_require_evidence_and_keep_milestone_role(relation, label):
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    raw["papers"][0]["milestone"] = True
    raw["papers"][1]["milestone"] = False
    raw["edges"][0]["relation"] = relation
    result, metadata = research.proposal_for(board(), {}, catalog, raw, [])
    cards = {node["paperId"]: node for node in result["board"]["nodes"] if node["kind"] == "paper"}
    assert cards["a"]["roadmapRole"] == "milestone" and cards["b"]["roadmapRole"] is None
    relation_edge = next(edge for edge in result["board"]["edges"] if not edge["roadmapStructure"])
    assert relation_edge["label"] == label and relation_edge["analysis"]["relation"] == relation
    assert all("milestone" not in entry for entry in metadata)
    raw["edges"][0]["evidence"] = []
    rejected, _ = research.proposal_for(board(), {}, catalog, raw, [])
    assert all(edge["roadmapStructure"] and edge["analysis"] is None for edge in rejected["board"]["edges"])
    assert {entry["paperId"] for entry in rejected["unconnected"]} == {"a", "b"}


def test_same_branch_connects_entry_papers_without_forcing_scholarly_chains():
    catalog = {pid: paper(pid, year) for pid, year in (("a", "2010"), ("b", "2020"), ("c", "2021"))}
    raw = synthesis()
    raw["papers"][1]["branch"] = "Foundations"
    raw["papers"].append({"id": "c", "reason": "An independent approach", "branch": "Foundations"})
    raw["edges"][0]["relation"] = "evolves_into"
    result, _ = research.proposal_for(board(), {}, catalog, raw, [])
    graph = result["board"]
    cards = {node["paperId"]: node for node in graph["nodes"] if node["kind"] == "paper"}
    branch = next(node for node in graph["nodes"] if node["roadmapRole"] == "branch")
    roots = {edge["target"] for edge in graph["edges"] if edge["source"] == branch["id"]}
    assert roots == {cards["a"]["id"], cards["c"]["id"]}
    assert cards["a"]["y"] < cards["b"]["y"]
    assert cards["a"]["y"] == cards["c"]["y"]
    assert cards["a"]["x"] != cards["c"]["x"]
    assert len([edge for edge in graph["edges"] if edge["analysis"]]) == 1
    assert result["unconnected"] == [{"paperId": "c", "title": catalog["c"]["title"]}]


def test_legacy_blank_groups_become_compact_branch_lanes():
    catalog = {pid: paper(pid, year) for pid, year in (("a", "2010"), ("b", "2020"), ("c", "2021"))}
    raw = {
        "papers": [{"id": pid, "reason": "A milestone", "branch": "Foundations"} for pid in catalog],
        "edges": [
            {"source": "a", "target": "b", "relation": "evolves_into", "reason": "Checked evolution",
             "evidence": [{"paperId": "b", "quote": "The verified method b learns reusable visual representations."}]},
        ],
    }
    first, _ = research.proposal_for(board(), {}, catalog, raw, [])
    old = first["board"]
    cards = [node for node in old["nodes"] if node["kind"] == "paper"]
    old["nodes"].append({"id": "legacy", "kind": "group", "x": 80, "y": 80,
                         "width": 640, "height": 1800, "text": "Foundations"})
    for index, card in enumerate(cards):
        card.update(groupId="legacy", x=104, y=140 + index * 500)

    result, _ = research.proposal_for(old, {}, catalog, raw, [])
    graph = result["board"]
    mapped = {node.get("paperId"): node for node in graph["nodes"] if node["kind"] == "paper"}
    group = next(node for node in graph["nodes"] if node["id"] == "legacy")
    branch = next(node for node in graph["nodes"] if node.get("roadmapRole") == "branch")
    assert branch["y"] < group["y"] < mapped["a"]["y"]
    assert mapped["a"]["y"] < mapped["b"]["y"]
    assert mapped["a"]["y"] == mapped["c"]["y"]
    assert mapped["a"]["x"] != mapped["c"]["x"]
    assert group["height"] < 1800
    assert all(group["y"] < card["y"] < group["y"] + group["height"] for card in mapped.values())


def test_rerun_preserves_renamed_labels_user_groups_notes_and_all_locked_items():
    from routers.domains import Board

    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020"), "c": paper("c", "2021")}
    preview, _ = research.proposal_for(board(), {}, catalog, synthesis(), [])
    old = preview["board"]
    topic = next(node for node in old["nodes"] if node["roadmapRole"] == "topic")
    topic.update(text="My topic annotation", locked=True, x=150, y=70)
    branch = next(node for node in old["nodes"] if node["roadmapRole"] == "branch" and node["text"] == "Foundations")
    branch.update(text="My renamed branch", locked=True, x=250, y=300)
    card = next(node for node in old["nodes"] if node.get("paperId") == "a")
    card.update(text="Private note", groupId="user-group", x=400, y=900)
    old["nodes"].extend([{"id": "user-group", "kind": "group", "x": 360, "y": 850,
                          "width": 800, "height": 600, "text": "My selected studies"},
                         {"id": "user-note", "kind": "text", "x": 1250, "y": 80, "text": "Do not move this note"}])
    for edge in old["edges"]:
        if edge["target"] == branch["id"] or not edge["roadmapStructure"]:
            edge.update(locked=True, label="Human label")
    old = Board.model_validate(old).model_dump()
    originals = {node["id"]: copy.deepcopy(node) for node in old["nodes"]
                 if node["id"] in (topic["id"], branch["id"], card["id"], "user-group", "user-note")}
    locks = [copy.deepcopy(edge) for edge in old["edges"] if edge["locked"]]
    raw = {"papers": [{"id": "c", "reason": "New milestone", "branch": "Foundations", "milestone": True}], "edges": []}
    result, _ = research.proposal_for(old, {}, catalog, raw, [])
    actual = {node["id"]: node for node in result["board"]["nodes"]}
    assert all(actual[key] == value for key, value in originals.items())
    assert all(edge in result["board"]["edges"] for edge in locks)
    assert len([node for node in actual.values() if node["roadmapRole"] == "topic"]) == 1
    assert len([node for node in actual.values() if node["roadmapRole"] == "branch"]) == 2
    assert {node["paperId"] for node in actual.values() if node["kind"] == "paper"} == {"a", "b", "c"}
    assert result["protected"] == len(locks)


def test_locked_paper_cycle_is_kept_and_attached_to_tree_without_infinite_layout():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    raw = synthesis()
    raw["papers"][1]["branch"] = "Foundations"
    first, _ = research.proposal_for(board(), {}, catalog, raw, [])
    old = first["board"]
    forward = next(edge for edge in old["edges"] if edge["analysis"])
    forward["locked"] = True
    reverse = dict(copy.deepcopy(forward), id="locked-cycle", source=forward["target"], target=forward["source"])
    old["edges"].append(reverse)
    raw["edges"] = []
    result, _ = research.proposal_for(old, {}, catalog, raw, [])
    assert forward in result["board"]["edges"] and reverse in result["board"]["edges"]
    branch = next(node for node in result["board"]["nodes"] if node["roadmapRole"] == "branch")
    assert any(edge["source"] == branch["id"] and edge["roadmapStructure"] for edge in result["board"]["edges"])


def test_generated_development_relations_do_not_add_circular_progression():
    catalog = {pid: paper(pid, "2020") for pid in ("a", "b", "c")}
    raw = {"papers": [{"id": pid, "reason": "A milestone", "branch": "One branch"} for pid in catalog],
           "edges": [{"source": a, "target": b, "relation": "evolves_into", "reason": "A checked evolution",
                      "evidence": [{"paperId": b, "quote": "learns reusable visual representations"}]}
                     for a, b in (("a", "b"), ("b", "c"), ("c", "a"))]}
    _, relations, rejected = research.select_and_ground(raw, catalog)
    assert [(entry["source"], entry["target"]) for entry in relations] == [("a", "b"), ("b", "c")]
    assert rejected == 1


def test_schema_separates_tree_structure_from_scholarly_evidence():
    from pydantic import ValidationError
    from routers.domains import Board, CanvasNode

    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    preview, _ = research.proposal_for(board(), {}, catalog, synthesis(), [])
    graph = preview["board"]
    structure = next(edge for edge in graph["edges"] if edge["roadmapStructure"])
    analysis = next(edge["analysis"] for edge in graph["edges"] if edge["analysis"])
    forged = copy.deepcopy(graph)
    next(edge for edge in forged["edges"] if edge["id"] == structure["id"])["analysis"] = analysis
    with pytest.raises(ValidationError):
        Board.model_validate(forged)
    forged = copy.deepcopy(graph)
    next(edge for edge in forged["edges"] if edge["id"] == structure["id"])["roadmapStructure"] = False
    with pytest.raises(ValidationError):
        Board.model_validate(forged)
    with pytest.raises(ValidationError):
        CanvasNode.model_validate({"id": "bad", "kind": "text", "x": 0, "y": 0, "roadmapRole": "milestone"})


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


def test_earlier_papers_own_description_cannot_establish_explicit_development():
    catalog = {"a": paper("a", "2010"), "b": paper("b", "2020")}
    excerpt = "Our network learns reusable visual representations from local windows."
    catalog["a"].update(fullText=excerpt, fullTextPages=[{"page": 2, "text": excerpt}],
                        fullTextSource=catalog["a"]["sourceUrl"])
    raw = synthesis()
    raw["edges"][0].update(relation="evolves_into", certainty="explicit",
                           evidence=[{"paperId": "a", "quote": excerpt, "page": 2}])
    assert research.select_and_ground(raw, catalog)[1][0]["certainty"] == "inferred"


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
    with research_db() as db:
        run = db.get(RoadmapRun, "run")
        run.snapshot = {
            **run.snapshot,
            "board": {**run.snapshot["board"], "researchKeywords": ["3DGS", "4DGS"]},
        }
        db.commit()
    collected_plan = {}

    def collect(plan, *_args, **_kwargs):
        collected_plan.update(plan)
        return {
            "papers": [paper("a", "2010"), paper("b", "2020")], "warnings": [],
            "searchLog": [{"source": "arxiv", "status": "ok", "query": "vision foundations", "matchedCount": 2}],
        }

    monkeypatch.setattr("services.roadmap_sources.collect_candidates", collect)
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
        assert data["focusKeywords"] == ["3DGS", "4DGS"]
        if "当前阶段：plan" in system:
            phases.append("plan")
            return json.dumps({"researchQuestion": "How did visual representations develop?", "scope": "Foundations",
                               "inclusionCriteria": ["Primary methods"], "exclusionCriteria": ["Off-topic"],
                               "queries": ["vision foundations"], "papers": []})
        if "当前阶段：evaluate" in system:
            phases.append("evaluate")
            return json.dumps({"contribution": "Reusable visual features", "evidence": [{"quote": excerpt, "page": 2}]})
        phases.append("screen" if "当前阶段：screen" in system else "synthesize")
        if phases[-1] == "synthesize":
            coverage = data["researchCoverage"]
            assert coverage["searchLog"][0]["matchedCount"] == 2
            assert coverage["candidateCount"] == coverage["selectedCount"] == 2
            assert len(coverage["fullText"]) == 2
            assert all(p["fullTextCoverage"]["pagesVisited"] == 1 for p in coverage["fullText"])
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
    assert collected_plan["focusKeywords"] == ["3DGS", "4DGS"]
    assert result["preview"]
    with research_db() as db:
        stored_run = db.get(RoadmapRun, "run")
        assert stored_run.status == "preview"
        assert stored_run.proposal["board"]["researchKeywords"] == ["3DGS", "4DGS"]
        assert "本轮关注关键词：3DGS、4DGS" in stored_run.proposal["report"]
        assert len([edge for edge in stored_run.proposal["board"]["edges"]
                    if not edge["roadmapStructure"]]) == 1
        assert stored_run.proposal["report"]
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
