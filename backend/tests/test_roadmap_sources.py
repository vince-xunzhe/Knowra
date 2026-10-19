"""Roadmap discovery trusts metadata responses, never a model's identifiers."""

import io
import json
import sys
import urllib.error
import urllib.parse
import uuid
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import roadmap_sources as sources


def feed(*papers):
    entries = []
    for paper in papers:
        aid = paper.get("id", "1706.03762v3")
        entry_id = paper.get("url", "http://arxiv.org/abs/" + aid)
        entries.append(
            f"<entry><id>{escape(entry_id)}</id><title>{escape(paper['title'])}</title>"
            f"<published>{paper.get('published', '2017-06-12T00:00:00Z')}</published>"
            f"<summary>{escape(paper.get('abstract', 'Verified abstract.'))}</summary>"
            "<author><name>Jane Doe</name></author>"
            + (f"<arxiv:doi>{escape(paper['doi'])}</arxiv:doi>" if paper.get("doi") else "")
            + "</entry>"
        )
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            + "".join(entries) + "</feed>").encode()


def work(title="Learning representations by back-propagating errors", doi="10.1038/323533a0"):
    return {"DOI": doi, "title": [title], "author": [{"given": "David", "family": "Rumelhart"}],
            "published-print": {"date-parts": [[1986, 10, 9]]},
            "created": {"date-parts": [[2025]]},
            "abstract": "<jats:p>A verified <jats:italic>abstract</jats:italic>.</jats:p>",
            "reference": [{"DOI": "10.1000/OLDER"}, {"DOI": "https://evil.example/a"}, {}]}


class Response:
    def __init__(self, url, raw, headers=None):
        self.url = url
        self.raw = io.BytesIO(raw)
        self.headers = headers or {}

    def geturl(self):
        return self.url

    def read(self, size):
        return self.raw.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.raw.close()


@pytest.fixture
def network(monkeypatch):
    """No sockets or real sleep; retain request timestamps for pacing assertions."""
    state = SimpleNamespace(now=0.0, calls=[], handler=lambda url, params: None)

    def sleep(seconds):
        state.now += seconds

    def open_request(request, timeout):
        assert timeout == sources.REQUEST_TIMEOUT
        url = request.full_url
        assert sources._allowed_api_url(url), url
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        state.calls.append({"url": url, "params": params, "at": state.now})
        result = state.handler(url, params)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, Response):
            return result
        if result is None:
            result = feed() if url.startswith(sources.ARXIV_API) else {"message": {"items": []}}
        if isinstance(result, dict):
            result = json.dumps(result).encode()
        return Response(url, result)

    monkeypatch.setattr(sources.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    monkeypatch.setattr(sources.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(sources.time, "sleep", sleep)
    monkeypatch.setattr(sources, "_arxiv_last_request", None)
    return state


@pytest.mark.parametrize("supplied,returned,canonical", [
    ("1706.03762v5", "1706.03762v7", "1706.03762"),
    ("arXiv: 1706.03762", "1706.03762v1", "1706.03762"),
    ("https://arxiv.org/pdf/1706.03762v2.pdf", "1706.03762v3", "1706.03762"),
    ("hep-th/9901001v3", "hep-th/9901001v4", "hep-th/9901001"),
    ("http://arxiv.org/abs/cs/9901001v2", "cs/9901001v5", "cs/9901001"),
])
def test_ids_are_verified_and_versions_share_stable_identity(network, supplied, returned, canonical):
    title = "Attention Is All You Need"
    network.handler = lambda url, params: feed({"id": returned, "title": title})
    result = sources.collect_candidates({"papers": [{"title": title, "arxivId": supplied}]}, "")
    assert result["warnings"] == []
    assert len(result["papers"]) == 1
    paper = result["papers"][0]
    assert paper["arxivId"] == canonical
    assert paper["sourceUrl"] == "https://arxiv.org/abs/" + canonical
    assert paper["id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, paper["sourceUrl"]))
    assert paper["authors"] == ["Jane Doe"] and paper["year"] == "2017"
    assert paper["reason"] == paper["branch"] == ""
    assert network.calls[0]["params"]["id_list"] == [canonical]
    assert len(network.calls) == 1


def test_resolving_id_does_not_establish_identity_when_title_is_wrong(network):
    def handler(url, params):
        if params.get("id_list"):
            return feed({"id": "1706.00001", "title": "An entirely unrelated publication"})
        return feed({"id": "1706.03762v2", "title": "Attention Is All You Need"})
    network.handler = handler
    result = sources.collect_candidates({"papers": [{
        "title": "Attention Is All You Need", "arxivId": "1706.00001", "reason": "Invented reason"
    }]}, "")
    assert [p["arxivId"] for p in result["papers"]] == ["1706.03762"]
    assert result["papers"][0]["reason"] == ""
    assert len(network.calls) == 2
    assert network.calls[1]["params"]["sortBy"] == ["relevance"]


def test_wrong_title_is_not_accepted_even_with_similar_search_result(network):
    def handler(url, params):
        if url.startswith(sources.ARXIV_API):
            return feed({"title": "Learning representations from images, part II"})
        return {"message": {"items": [work("Learning representations from images, part II")]}}
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": "Learning representations from images"}]}, "")
    assert result["papers"] == []
    assert any("已从候选列表排除" in w for w in result["warnings"])


def test_doi_only_classic_is_kept_with_verified_year_source_and_references(network):
    classic = work()
    network.handler = lambda url, params: {"message": classic} if url.startswith(sources.CROSSREF_API) else feed()
    result = sources.collect_candidates({"papers": [{
        "title": classic["title"][0], "doi": "https://doi.org/10.1038/323533A0"
    }]}, "")
    assert result["warnings"] == []
    paper = result["papers"][0]
    assert paper["arxivId"] is None
    assert paper["doi"] == "10.1038/323533a0"
    assert paper["sourceUrl"] == "https://doi.org/10.1038/323533a0"
    assert paper["year"] == "1986"
    assert paper["authors"] == ["David Rumelhart"]
    assert paper["abstract"] == "A verified abstract ."
    assert paper["references"] == ["10.1000/older"]
    assert network.calls[1]["url"] == sources.CROSSREF_API + "/10.1038%2F323533a0"


def test_wrong_doi_response_falls_back_to_exact_title_search(network):
    classic = work()
    def handler(url, params):
        if url.startswith(sources.ARXIV_API):
            return feed()
        if "/works/" in url:
            return {"message": work("An unrelated classic", "10.1000/invalid-match")}
        return {"message": {"items": [work("A fuzzy wrong title"), classic]}}
    network.handler = handler
    result = sources.collect_candidates({"papers": [{
        "title": classic["title"][0], "doi": "10.1000/invalid-match"
    }]}, "")
    assert result["papers"][0]["doi"] == classic["DOI"]
    assert len(network.calls) == 3


def test_crossref_title_match_ignores_only_case_punctuation_and_spacing(network):
    classic = work("Learning Representations: by Back-Propagating Errors")
    network.handler = lambda url, params: {"message": {"items": [classic]}} if url.startswith(sources.CROSSREF_API) else feed()
    result = sources.collect_candidates({"papers": [{"title": "learning representations by back propagating errors"}]}, "")
    assert len(result["papers"]) == 1


def test_ambiguous_exact_titles_are_not_silently_assigned_a_random_id(network):
    title = "Universal Networks"
    network.handler = lambda url, params: (
        feed({"id": "1706.00001", "title": title}, {"id": "1706.00002", "title": title})
        if url.startswith(sources.ARXIV_API) else None)
    result = sources.collect_candidates({"papers": [{"title": title}]}, "")
    assert result["papers"] == []


def test_seed_query_and_versions_are_deduped_with_run_local_cache(network):
    title = "A specific milestone"
    network.handler = lambda url, params: feed({"id": "1706.03762v2", "title": title})
    plan = {"papers": [{"title": title}, {"title": title}], "queries": ["representation learning"]}
    result = sources.collect_candidates(plan, "LLM")
    assert len(result["papers"]) == 1
    assert len(network.calls) == 3  # One title search and two broad searches.
    assert len({call["url"] for call in network.calls}) == 3
    for before, after in zip(network.calls, network.calls[1:]):
        assert after["at"] - before["at"] >= 3


def test_same_preprint_is_preferred_over_crossref_publication(network):
    title = work()["title"][0]
    def handler(url, params):
        if url.startswith(sources.CROSSREF_API):
            classic = work()
            classic["author"] = [{"given": "Jane", "family": "Doe"}]
            return {"message": {"items": [classic]}}
        if params.get("search_query", [""])[0].startswith('ti:'):
            return feed()
        return feed({"id": "1706.03762", "title": title})
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": title}], "queries": ["neural networks"]}, "")
    assert len(result["papers"]) == 1
    assert result["papers"][0]["arxivId"] == "1706.03762"
    assert result["papers"][0]["doi"] == work()["DOI"]
    assert result["papers"][0]["references"] == ["10.1000/older"]


def test_same_title_but_different_authors_do_not_transfer_doi_to_wrong_preprint(network):
    title = work()["title"][0]
    def handler(url, params):
        if url.startswith(sources.CROSSREF_API):
            return {"message": {"items": [work()]}}
        if params.get("search_query", [""])[0].startswith('ti:'):
            return feed()
        return feed({"id": "1706.03762", "title": title})
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": title}], "queries": ["neural networks"]}, "")
    assert len(result["papers"]) == 2
    preprint = next(p for p in result["papers"] if p["arxivId"])
    assert preprint["doi"] is None


def test_references_fit_persistent_metadata_limit():
    classic = work()
    classic["reference"] = [{"DOI": f"10.1000/older-{i}"} for i in range(250)]
    assert len(sources._crossref_metadata(classic)["references"]) == 200


def test_conflicting_verified_dois_are_not_deduped_by_title_and_author():
    left = sources._crossref_metadata(work())
    right = sources._crossref_metadata(work(doi="10.1000/another-paper"))
    assert not sources._same_record(left, right)


def test_query_discovery_uses_relevance_and_surveys_not_only_latest(network):
    progress = []
    network.handler = lambda url, params: feed({"id": "cs/9901001v2", "title": "Historical Survey", "published": "1999-01-01"})
    result = sources.collect_candidates({"queries": ["neural networks"]}, "LLM", progress=progress.append)
    assert result["papers"][0]["year"] == "1999"
    assert len(progress) == 1
    assert all(c["params"]["sortBy"] == ["relevance"] for c in network.calls)
    assert "ti:survey OR ti:review" in network.calls[1]["params"]["search_query"][0]


def test_arxiv_pacing_is_shared_across_runs(network):
    sources.collect_candidates({"queries": ["one"]}, "")
    sources.collect_candidates({"queries": ["two"]}, "")
    assert [call["at"] for call in network.calls] == [0, 3, 6, 9]


def test_candidate_and_query_bounds(network):
    sequence = 0
    def handler(url, params):
        nonlocal sequence
        sequence += 1
        return feed(*[{"id": f"2001.{sequence * 1000 + index:05}", "title": f"Milestone {sequence} {index}"}
                      for index in range(80)])
    network.handler = handler
    result = sources.collect_candidates({"queries": ["one", "two", "three", "four"]}, "")
    assert len(result["papers"]) == 80
    assert len(network.calls) <= 6
    assert result["screening"]["excludedByLimit"] == 80
    assert result["screening"]["candidatesConsidered"] == 160


@pytest.mark.parametrize("bad", [
    "https://evil.example/abs/1706.03762", "https://arxiv.org@evil.example/abs/1706.03762",
    "http://127.0.0.1/abs/1706.03762", "https://arxiv.org/abs/1706.03762?next=http://localhost",
    "https://arxiv.org:invalid/abs/1706.03762", "../../credentials",
])
def test_untrusted_model_identifier_urls_are_never_requested(network, bad):
    result = sources.collect_candidates({"papers": [{
        "title": "Unverifiable Candidate", "arxivId": bad, "doi": bad, "sourceUrl": bad
    }]}, "")
    assert result["papers"] == []
    assert all(not call["params"].get("id_list") for call in network.calls)
    assert all(urllib.parse.urlsplit(c["url"]).hostname in {"export.arxiv.org", "api.crossref.org"}
               for c in network.calls)


def test_external_response_entry_urls_do_not_become_sources(network):
    network.handler = lambda url, params: feed({"title": "A Candidate", "url": "https://evil.example/abs/1706.03762"}) if url.startswith(sources.ARXIV_API) else None
    result = sources.collect_candidates({"papers": [{"title": "A Candidate"}]}, "")
    assert result["papers"] == []


def test_redirect_handler_refuses_even_official_redirects_to_preserve_pacing():
    for url in ("http://127.0.0.1/private", sources.ARXIV_API + "?id_list=1706.03762"):
        with pytest.raises(sources.SourceError, match="跳转"):
            sources._NoRedirect().redirect_request(None, None, 302, "Found", {}, url)


@pytest.mark.parametrize("declared", [True, False])
def test_response_size_is_bounded_with_or_without_content_length(network, monkeypatch, declared):
    monkeypatch.setattr(sources, "MAX_RESPONSE_BYTES", 100)
    network.handler = lambda url, params: Response(url, b"x" * 101, {"Content-Length": "101"} if declared else {})
    result = sources.collect_candidates({"queries": ["test"]}, "")
    assert result["papers"] == []
    assert any("大小限制" in warning for warning in result["warnings"])
    assert len(network.calls) == 2  # Unsafe responses are not retried.


def test_returned_external_url_and_xml_entities_are_refused(network):
    network.handler = lambda url, params: Response("http://127.0.0.1/metadata", feed({"title": "X"}))
    result = sources.collect_candidates({"queries": ["test"]}, "")
    assert result["papers"] == []
    assert any("官方" in warning for warning in result["warnings"])
    with pytest.raises(sources.SourceError, match="XML"):
        sources._parse_arxiv(b'<!DOCTYPE foo [<!ENTITY x "boom">]><foo>&x;</foo>')


def test_unavailable_sources_retry_with_bound_and_exclude_unverified_papers(network):
    network.handler = lambda url, params: urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO())
    result = sources.collect_candidates({"papers": [{"title": "Unverified"}, {"title": "Unverified"}]}, "")
    assert result["papers"] == []
    assert len(network.calls) == 6  # Three attempts/source, failed request cached.
    assert any("HTTP 503" in warning for warning in result["warnings"])
    arxiv_calls = [call for call in network.calls if call["url"].startswith(sources.ARXIV_API)]
    assert all(after["at"] - before["at"] >= 3 for before, after in zip(arxiv_calls, arxiv_calls[1:]))


def test_429_retry_can_recover_and_is_paced(network):
    attempts = 0
    def handler(url, params):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return urllib.error.HTTPError(url, 429, "Rate limit", {"Retry-After": "5"}, io.BytesIO())
        return feed({"title": "Recovered", "id": "1706.03762"})
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": "Recovered", "arxivId": "1706.03762"}]}, "")
    assert len(result["papers"]) == 1
    assert network.calls[1]["at"] >= 5


@pytest.mark.parametrize("failure", [429, 503, "network"])
def test_unavailable_arxiv_stops_run_requests_but_crossref_continues_for_all_seeds(network, failure):
    def handler(url, params):
        if url.startswith(sources.ARXIV_API):
            return (urllib.error.URLError("Connection failed") if failure == "network" else
                    urllib.error.HTTPError(url, failure, "Unavailable", {}, io.BytesIO()))
        title = params["query.title"][0]
        return {"message": {"items": [work(title, "10.1000/" + title.split()[-1])]}}
    network.handler = handler
    plan = {"papers": [{"title": f"Milestone {i}", "arxivId": f"2001.{i:05}"} for i in range(35)],
            "queries": ["machine learning", "representation learning", "neural networks"]}
    result = sources.collect_candidates(plan, "")
    assert len(result["papers"]) == 35
    arxiv_calls = [c for c in network.calls if c["url"].startswith(sources.ARXIV_API)]
    assert len(arxiv_calls) == 3  # One bounded attempt group, not 35 groups plus queries.
    assert len(network.calls) == 38
    assert any("本次已暂停该来源，请稍后重试" in warning for warning in result["warnings"])
    logs = [entry for entry in result["searchLog"] if entry["source"] == "arxiv"]
    assert logs[0]["status"] == "failed" and logs[0]["attempts"] == 3
    assert all(entry["status"] == "skipped" and entry["attempts"] == 0 for entry in logs[1:])
    assert all(entry["error"] and entry["matchedCount"] == 0 for entry in logs)
    crossref_logs = [entry for entry in result["searchLog"] if entry["source"] == "crossref"]
    assert len(crossref_logs) == 35
    assert all(entry["status"] == "ok" and entry["matchedCount"] == 1 for entry in crossref_logs)


@pytest.mark.parametrize("header", ["60", "http-date"])
def test_long_retry_after_stops_source_without_sleep_or_early_retry(network, monkeypatch, header):
    now = 1700000000
    monkeypatch.setattr(sources.time, "time", lambda: now)
    retry_after = formatdate(now + 60, usegmt=True) if header == "http-date" else header
    classic = work()
    def handler(url, params):
        if url.startswith(sources.ARXIV_API):
            return urllib.error.HTTPError(url, 429, "Rate limit", {"Retry-After": retry_after}, io.BytesIO())
        return {"message": {"items": [classic]}}
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": classic["title"][0]}, {"title": "Another seed"}],
                                         "queries": ["neural networks"]}, "")
    assert len(result["papers"]) == 1
    assert len([c for c in network.calls if c["url"].startswith(sources.ARXIV_API)]) == 1
    assert network.now == 0
    assert any("稍后重试" in warning for warning in result["warnings"])


def test_unavailable_crossref_does_not_stop_arxiv_for_other_seeds(network):
    def handler(url, params):
        if url.startswith(sources.CROSSREF_API):
            return urllib.error.URLError("Connection failed")
        if "Verified" in params.get("search_query", [""])[0]:
            return feed({"id": "1706.03762", "title": "Verified milestone"})
        return feed()
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": title} for title in
                                        ["Missing first", "Verified milestone", "Missing last"]]}, "")
    assert [p["title"] for p in result["papers"]] == ["Verified milestone"]
    assert len([c for c in network.calls if c["url"].startswith(sources.CROSSREF_API)]) == 3
    assert len([c for c in network.calls if c["url"].startswith(sources.ARXIV_API)]) == 3


def test_not_found_is_per_paper_and_does_not_disable_source(network):
    def handler(url, params):
        if url.startswith(sources.ARXIV_API):
            return feed()
        if "10.1000%2Fmissing" in url:
            return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
        return {"message": {"items": [work()]}}
    network.handler = handler
    result = sources.collect_candidates({"papers": [{"title": work()["title"][0], "doi": "10.1000/missing"}]}, "")
    assert len(result["papers"]) == 1
    assert len([c for c in network.calls if c["url"].startswith(sources.CROSSREF_API)]) == 2


def test_search_audit_separates_hits_identity_screening_duplicates_and_cache(network):
    title = "Verified milestone"
    def handler(url, params):
        if params.get("id_list"):
            return feed({"title": title, "id": "1706.03762"})
        if url.startswith(sources.ARXIV_API):
            return feed({"title": "An unrelated search hit", "id": "1706.00001"})
        return {"message": {"items": []}}
    network.handler = handler
    seed = {"title": title, "arxivId": "1706.03762"}
    result = sources.collect_candidates({"papers": [seed, {"title": "Unknown work"}, seed,
                                                     {"title": "Unknown work"}, {}, None]}, "")
    assert result["screening"] == {
        "suggestedSeeds": 6, "invalidSeeds": 2, "verifiedSeeds": 2,
        "unverifiedSeeds": 2, "candidatesConsidered": 2, "duplicatesRemoved": 1,
        "excludedByLimit": 0, "candidateCount": 1,
    }
    logs = result["searchLog"]
    assert len(logs) == 5 and len(network.calls) == 3
    assert logs[0]["queryType"] == "identifier" and logs[0]["query"] == "1706.03762"
    # A search hit is not itself a verified seed match.
    assert logs[1]["queryType"] == "title" and logs[1]["matchedCount"] == 1
    assert logs[2]["query"] == "Unknown work" and logs[2]["matchedCount"] == 0
    assert all(entry["cached"] and entry["attempts"] == 0 for entry in logs[3:])
    assert all(entry["status"] == "ok" and entry["error"] == "" for entry in logs)
    assert any("未能核验论文《Unknown work》" in warning for warning in result["warnings"])


def test_cancellation_propagates_while_waiting_between_requests(network):
    class Cancelled(Exception):
        pass
    def cancelled():
        if network.now >= 0.5:
            raise Cancelled()
    with pytest.raises(Cancelled):
        sources.collect_candidates({"queries": ["test"]}, "", cancelled=cancelled)
    assert len(network.calls) == 1
    assert not sources._arxiv_lock.locked()


def test_malformed_crossref_optional_metadata_does_not_crash():
    malformed = work()
    malformed.update(author={"bad": 1}, reference=42, published={"date-parts": "invalid"})
    paper = sources._crossref_metadata(malformed)
    assert paper["authors"] == paper["references"] == []
    assert paper["year"] == "1986"
