"""Citation context retrieval and per-claim grounding, independent of live models."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.domain_agent import grounded_edges, retrieve
from services.domain_citation_analysis import deepen_relations, validate_findings
from services.domain_citation_context import document_sections, trace_citations

TITLE = "VectorNet: Efficient Structured Representation Learning"
TARGET = {"id": "a", "title": TITLE, "year": "2020"}


def paper(body, references=None, extra=None):
    refs = (
        references
        or f"References\n[29] Jane Smith. {TITLE}. 2020.\n[30] Bob Jones. Another unrelated study. 2021."
    )
    pages = [{"page": 2, "text": body}, {"page": 20, "text": refs}] + (extra or [])
    return {
        "id": "b",
        "title": "New method",
        "year": "2022",
        "document": document_sections(pages),
        "sources": [],
    }


def test_numeric_groups_ranges_and_multiple_uses_reach_body():
    citing = paper(
        "Prior methods [12, 29, 30] need costly preprocessing.\nOur approach instead learns end to end.",
        extra=[
            {
                "page": 21,
                "text": "Appendix A\nWe compare against the methods [27–31] under the same training budget.",
            }
        ],
    )
    trace = trace_citations(citing, TARGET)
    assert trace["referenceFound"] and trace["bodyCount"] == 2
    assert [s["page"] for s in trace["sources"] if s["section"] == "body"] == [2, 21]
    assert "costly preprocessing" in trace["sources"][0]["text"]
    assert "same training budget" in trace["sources"][1]["text"]


def test_author_year_and_named_mentions():
    for body in (
        "Smith et al. (2020) demonstrated strong performance but require costly preprocessing.",
        "VectorNet provides a useful baseline, but our method operates without preprocessing.",
    ):
        trace = trace_citations(
            paper(body, f"References\nJane Smith. {TITLE}. 2020."), TARGET
        )
        assert trace["bodyCount"] == 1
        assert trace["sources"][0]["section"] == "body"


def test_no_false_numeric_or_version_match_and_reference_only_status():
    trace = trace_citations(
        paper("Our baseline [129] is different. The experimental budget is 29 hours."),
        TARGET,
    )
    assert trace["bodyCount"] == 0 and trace["referenceFound"]
    target = {"id": "a", "title": "DeepSeek-V4: A long specific title", "year": "2026"}
    trace = trace_citations(
        paper(
            "DeepSeek-V4.1 is a different model.",
            "References\n[1] DeepSeek-V4.1: Another specific title. 2026.",
        ),
        target,
    )
    assert not trace["referenceFound"] and not trace["bodyCount"]


def test_reference_and_body_on_same_page_are_not_conflated():
    citing = paper(
        f"We use the estimator [29] because it is robust.\nReferences\n[29] Jane Smith. {TITLE}. 2020."
    )
    trace = trace_citations(citing, TARGET)
    assert trace["bodyCount"] == 1
    body = trace["sources"][0]
    assert "because it is robust" in body["text"] and "Jane Smith" not in body["text"]


def test_context_coverage_reports_truncation():
    citing = paper("We use the estimator [29].")
    citing["document"] = [
        {
            "page": i,
            "text": f"Experiment {i} compares against the estimator [29] in a controlled setting.",
            "section": "body",
        }
        for i in range(1, 16)
    ] + citing["document"][-1:]
    trace = trace_citations(citing, TARGET, limit=4)
    assert trace["bodyCount"] == 15 and trace["truncated"]
    assert len([s for s in trace["sources"] if s["section"] == "body"]) == 4


def test_findings_must_quote_citing_body_not_bibliography_or_fabrications():
    trace = trace_citations(
        paper(
            "We use [29] as a baseline because its preprocessing limits scalability."
        ),
        TARGET,
    )
    body, ref = trace["sources"][0], trace["sources"][-1]
    evidence = lambda s, quote: [{"sourceId": s["id"], "quote": quote}]
    raw = [
        {
            "aspect": "assessment",
            "text": "Authors note a scalability limitation.",
            "evidence": evidence(body, body["text"]),
        },
        {
            "aspect": "assessment",
            "text": "Unfounded praise.",
            "evidence": evidence(ref, ref["text"]),
        },
        {
            "aspect": "purpose",
            "text": "Invented use.",
            "evidence": evidence(body, "This invented quotation was never provided."),
        },
    ]
    found = validate_findings(raw, {s["id"]: s for s in trace["sources"]})
    assert len(found) == 1 and found[0]["evidence"][0]["page"] == 2
    edge = {
        "source": "a",
        "target": "b",
        "relation": "improves",
        "certainty": "explicit",
        "reason": "Unsupported improvement",
        "evidence": evidence(ref, ref["text"]),
    }
    result, _ = grounded_edges([edge], {}, {ref["id"]: ref}, "b", {"a", "b"})
    assert result[0]["relation"] == "cites"


class Context:
    def step(self, _, fn):
        return fn()

    def phase(self, _):
        pass


def test_second_pass_reads_all_located_contexts_and_attaches_supported_findings():
    citing = paper(
        "We use [29] as a baseline because its preprocessing limits scalability."
    )
    trace = trace_citations(citing, TARGET)
    body = trace["sources"][0]
    calls = []

    def model(_, model, system, user, **kwargs):
        prompt = json.loads(user)
        calls.append(prompt)
        assert prompt["citingPaper"]["id"] == "b"
        assert any("limits scalability" in e["text"] for e in prompt["excerpts"])
        evidence = [{"sourceId": body["id"], "quote": body["text"]}]
        return json.dumps(
            {
                "relation": "compares",
                "certainty": "explicit",
                "reason": "Used as a baseline with a stated scalability limitation.",
                "evidence": evidence,
                "findings": [
                    {
                        "aspect": "purpose",
                        "text": "Used as an experimental baseline.",
                        "evidence": evidence,
                    },
                    {
                        "aspect": "assessment",
                        "text": "Preprocessing limits scalability.",
                        "evidence": evidence,
                    },
                ],
            }
        )

    result = deepen_relations(
        Context(),
        "run",
        "model",
        {"a": TARGET, "b": citing},
        [],
        {("b", "a"): trace},
        model,
        lambda _: None,
    )
    assert len(calls) == 1
    assert result[0]["relation"] == "compares"
    discussion = result[0]["discussion"]
    assert discussion["status"] == "analyzed" and len(discussion["findings"]) == 2
    assert discussion["contextsUsed"] == discussion["contextsFound"] == 1


def test_reference_only_does_not_invent_an_author_opinion_or_call_model():
    citing = paper("Unrelated discussion with no matching citation.")
    trace = trace_citations(citing, TARGET)

    def model(*args, **kwargs):
        raise AssertionError("No body context to analyze")

    result = deepen_relations(
        Context(),
        "run",
        "model",
        {"a": TARGET, "b": citing},
        [],
        {("b", "a"): trace},
        model,
        lambda _: None,
    )
    assert result[0]["discussion"]["status"] == "reference_only"
    assert not result[0]["discussion"]["findings"]


def test_body_context_recall_improves_over_title_only_retrieval():
    # Five citation styles; title-only ranking picks the bibliography in all.
    cases = [
        "We compare with [29] using the same training budget.",
        "These earlier methods [12, 29, 30] are computationally costly.",
        "We improve on the estimators [27-31] using regularization.",
        "Smith et al. (2020) rely on costly preprocessing.",
        "VectorNet is a baseline with a substantial preprocessing cost.",
    ]
    old_hits = new_hits = 0
    for body in cases:
        citing = paper(body)
        citing["sources"] = [
            {**s, "id": str(i), "paperId": "b", "source": "pdf", "locator": ""}
            for i, s in enumerate(citing["document"])
        ]
        old = retrieve(citing, TITLE, 1)
        old_hits += any(body in s["text"] for s in old)
        new_hits += trace_citations(citing, TARGET)["bodyCount"] > 0
    assert old_hits == 0
    assert new_hits == len(cases)


def test_citation_paragraph_continues_on_next_page():
    citing = paper("Our analysis of [29] found that the method")
    citing["document"].insert(
        1,
        {
            "page": 3,
            "section": "body",
            "text": "requires costly preprocessing, which limits its applicability.",
        },
    )
    trace = trace_citations(citing, TARGET)
    assert [s["page"] for s in trace["sources"] if s["section"] == "body"] == [2, 3]
    assert "limits its applicability" in trace["sources"][1]["text"]


def test_author_year_does_not_bind_an_adjacent_reference():
    refs = f"References\nJones, B. (2019). Other paper.\nSmith, J. (2020). {TITLE}."
    trace = trace_citations(
        paper("Jones (2019) reports unrelated results.", refs), TARGET
    )
    assert trace["referenceFound"] and not trace["bodyCount"]


def test_old_analysis_without_discussion_still_matches_current_schema():
    from routers.domains import Board
    from services.domain_agent_state import content

    old = {
        "id": "b",
        "name": "Board",
        "nodes": [
            {"id": "a", "kind": "paper", "paperId": "p1", "x": 0, "y": 0},
            {"id": "b", "kind": "paper", "paperId": "p2", "x": 400, "y": 0},
        ],
        "edges": [
            {
                "id": "e",
                "source": "a",
                "target": "b",
                "analysis": {
                    "relation": "cites",
                    "certainty": "explicit",
                    "reason": "Cited",
                    "evidence": [
                        {
                            "paperId": "p2",
                            "source": "pdf",
                            "locator": "p20",
                            "page": 20,
                            "quote": "A verified bibliography entry.",
                        }
                    ],
                },
            }
        ],
    }
    assert content(old) == content(Board.model_validate(old).model_dump())
