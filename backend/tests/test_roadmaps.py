"""Roadmap catalog and downloads operate on isolated local stores and fake HTTP."""

import copy
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from database import get_db
from models import Base, Paper, RoadmapPaper, RoadmapRun, RoadmapWorkspace
from routers.roadmaps import router
from services import roadmap_import, roadmap_state
from services.task_executor import Context
from services.task_store import TaskStore


def metadata(pid="classic", **changes):
    return {"id": pid, "title": "A Classic Learning Method", "year": "2017",
            "authors": ["Alice"], "abstract": "A grounded abstract", "arxivId": "1706.03762",
            "doi": None, "sourceUrl": "https://arxiv.org/abs/1706.03762",
            "reason": "Introduced a foundational technique", "branch": "Architecture", **changes}


def node(pid="classic", nid="node", **changes):
    return {"id": nid, "kind": "paper", "paperId": pid, "x": 100, "y": 200, **changes}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr("services.paper_category_service._active_cache", ["LLM", "世界模型", "其他"])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    queue = TaskStore(tmp_path / "tasks.db")
    queue.initialize()
    monkeypatch.setattr("services.task_runtime.store", lambda: queue)
    monkeypatch.setattr("routers.jobs.submit_job", queue.submit)
    monkeypatch.setattr(roadmap_state, "SessionLocal", factory)
    monkeypatch.setattr(roadmap_import, "SessionLocal", factory)
    monkeypatch.setattr("config.load_config", lambda: {"scan_directory": str(tmp_path / "pdfs")})
    monkeypatch.setattr("services.paper_record_service.sync_record_from_paper", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(router)

    def dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=factory, queue=queue, root=tmp_path)
    engine.dispose()


def add_catalog(env, *entries):
    with env.db() as db:
        for entry in entries:
            data = roadmap_state.PaperMetadata.model_validate(entry).model_dump()
            db.add(RoadmapPaper(id=data["id"], data=data))
        db.commit()


def saved_board(env, *ids):
    snap = env.client.get("/api/roadmaps").json()
    snap["state"]["boards"][0]["nodes"] = [node(pid, "node-" + pid) for pid in ids]
    response = env.client.put("/api/roadmaps", json=snap)
    assert response.status_code == 200, response.text
    return env.client.get("/api/roadmaps").json()


def start_research(env):
    snap = env.client.get("/api/roadmaps").json()
    response = env.client.post("/api/roadmaps/agent/runs", json={
        "boardId": snap["state"]["boards"][0]["id"], "revision": snap["revision"], "heights": {},
    })
    assert response.status_code == 202, response.text
    return response.json()


def make_proposal(env, run, papers, report=""):
    with env.db() as db:
        board = copy.deepcopy(db.get(RoadmapRun, run["id"]).snapshot["board"])
    board["nodes"] = [node(p["id"], "generated-" + p["id"]) for p in papers]
    proposal = {"board": board, "added": 0, "removed": 0, "protected": 0,
                "rejected": 0, "warnings": [], "unconnected": [], "report": report}
    roadmap_state.persist_preview(run["id"], proposal, papers)


def test_report_tracks_preview_apply_and_discard_without_changing_library(env):
    run = start_research(env)
    board_id = run["boardId"]
    endpoint = "/api/roadmaps/report?boardId=" + board_id
    assert env.client.get(endpoint).json() is None
    make_proposal(env, run, [metadata()], "# First report\n\nVerified sources")
    assert env.client.get(endpoint).json()["status"] == "preview"
    assert env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply").status_code == 200
    assert env.client.get(endpoint).json()["markdown"].startswith("# First report")
    assert env.client.get(endpoint).json()["status"] == "applied"
    env.queue.update(run["jobId"], status="completed")
    second = start_research(env)
    make_proposal(env, second, [metadata()], "# Revised report")
    assert env.client.get(endpoint).json()["markdown"] == "# Revised report"
    assert env.client.post(f"/api/roadmaps/agent/runs/{second['id']}/discard").status_code == 200
    assert env.client.get(endpoint).json()["runId"] == run["id"]
    with env.db() as db:
        assert db.query(Paper).count() == 0


def test_workspace_inherits_categories_but_rejects_fabricated_papers(env):
    snap = env.client.get("/api/roadmaps").json()
    assert [b["name"] for b in snap["state"]["boards"]] == ["LLM", "世界模型", "其他"]
    assert env.client.get("/api/roadmaps").json() == snap
    unknown = copy.deepcopy(snap)
    unknown["state"]["boards"][0]["nodes"] = [node()]
    assert env.client.put("/api/roadmaps", json=unknown).status_code == 422
    deleted = {"state": {"boards": [], "activeId": None}, "revision": snap["revision"]}
    assert env.client.put("/api/roadmaps", json=deleted).status_code == 422
    with env.db() as db:
        assert db.query(Paper).count() == 0


def test_external_catalog_can_be_edited_without_downloading_and_saves_conflict(env):
    add_catalog(env, metadata())
    snap = saved_board(env, "classic")
    snap["state"]["boards"][0]["nodes"][0]["text"] = "Human annotation"
    assert env.client.put("/api/roadmaps", json=snap).status_code == 200
    assert env.client.put("/api/roadmaps", json=snap).status_code == 409
    with env.db() as db:
        assert db.query(Paper).count() == 0
        assert db.get(RoadmapWorkspace, 1).state["boards"][0]["nodes"][0]["text"] == "Human annotation"


def test_library_status_is_live_version_agnostic_and_title_fallback(env):
    add_catalog(env, metadata(), metadata("old", title="Old Classic", arxivId="hep-th/9901001"),
                metadata("title", title="Non-Arxiv: Classic!", arxivId=None, sourceUrl="https://doi.org/10.1000/example"),
                metadata("other", title="Unavailable PDF", arxivId=None))
    with env.db() as db:
        for pid, filename, title in [("local", "arxiv_1706.03762v3.pdf", "Renamed"),
                                     ("legacy", "hep-th_9901001v2.pdf", "Legacy"),
                                     ("titled", "random.pdf", "non arxiv classic")]:
            db.add(Paper(id=pid, filename=filename, filepath=filename, file_hash=pid, title=title,
                         authors=["Alice"], processed=pid == "local"))
        db.commit()
    papers = {p["id"]: p for p in env.client.get("/api/roadmaps/papers").json()}
    assert papers["classic"]["roadmap"]["libraryPaperId"] == "local"
    assert papers["classic"]["roadmap"]["reviewReady"] is True
    assert papers["old"]["roadmap"]["libraryStatus"] == "in_library"
    assert papers["title"]["roadmap"]["libraryStatus"] == "in_library"
    assert papers["other"]["roadmap"]["libraryStatus"] == "no_arxiv"
    with env.db() as db:
        db.delete(db.get(Paper, "local"))
        db.commit()
    papers = {p["id"]: p for p in env.client.get("/api/roadmaps/papers").json()}
    assert papers["classic"]["roadmap"]["libraryStatus"] == "not_imported"


def test_empty_board_research_is_preview_only_then_applies_and_can_undo(env):
    run = start_research(env)
    make_proposal(env, run, [metadata()])
    assert env.client.get("/api/roadmaps").json()["state"]["boards"][0]["nodes"] == []
    assert env.client.get("/api/roadmaps/papers").json()[0]["title"] == metadata()["title"]
    assert env.client.get("/api/roadmaps/agent/runs").json()[0]["status"] == "preview"
    snap = env.client.get("/api/roadmaps").json()
    snap["state"]["boards"][0]["viewport"]["zoom"] = 0.7
    assert env.client.put("/api/roadmaps", json=snap).status_code == 200
    applied = env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply")
    assert applied.status_code == 200, applied.text
    snap = applied.json()
    assert len(snap["state"]["boards"][0]["nodes"]) == 1
    assert snap["state"]["boards"][0]["viewport"]["zoom"] == 0.7
    assert env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply").status_code == 409
    snap["state"]["boards"][0] = snap.pop("before")
    assert env.client.put("/api/roadmaps", json=snap).status_code == 200
    with env.db() as db:
        assert db.query(Paper).count() == 0


def test_research_protects_edits_and_cancelled_result_cannot_publish(env):
    run = start_research(env)
    snap = env.client.get("/api/roadmaps").json()
    snap["state"]["boards"][0]["nodes"] = [{"id": "note", "kind": "text", "x": 0, "y": 0, "text": "racing edit"}]
    assert env.client.put("/api/roadmaps", json=snap).status_code == 409
    assert env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/discard").json()["status"] == "cancelled"
    with pytest.raises(roadmap_state.Cancelled):
        make_proposal(env, run, [metadata()])
    with env.db() as db:
        assert db.query(RoadmapPaper).count() == 0
    assert env.client.put("/api/roadmaps", json=snap).status_code == 200


def test_stale_preview_and_worker_interruption(env):
    run = start_research(env)
    make_proposal(env, run, [metadata()])
    with env.db() as db:
        row = db.get(RoadmapWorkspace, 1)
        state = copy.deepcopy(row.state)
        state["boards"][0]["nodes"] = [{"id": "changed", "kind": "text", "x": 1, "y": 1, "text": "migration"}]
        row.state = state
        db.commit()
    assert env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply").status_code == 409
    env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/discard")
    env.queue.update(run["jobId"], status="completed")
    again = start_research(env)
    env.queue.update(again["jobId"], status="interrupted", error="worker stopped")
    assert env.client.get("/api/roadmaps/agent/runs").json()[0]["status"] == "failed"


def test_preview_rejects_unregistered_references_and_changed_catalog_identity(env):
    run = start_research(env)
    add_catalog(env, metadata())
    with pytest.raises(ValueError, match="cannot change"):
        make_proposal(env, run, [metadata(title="Invented Different Work")])
    with pytest.raises(ValueError):
        make_proposal(env, run, [metadata(sourceUrl="javascript:alert(1)")])


def test_catalog_can_gain_verified_arxiv_version_but_cannot_switch_doi(env):
    run = start_research(env)
    add_catalog(env, metadata(arxivId=None, doi="10.1000/classic"))
    with pytest.raises(ValueError, match="cannot change"):
        make_proposal(env, run, [metadata(doi="10.1000/different")])
    make_proposal(env, run, [metadata(doi="10.1000/classic")])
    displayed = env.client.get("/api/roadmaps/papers").json()[0]
    assert displayed["roadmap"]["arxivId"] == "1706.03762"
    env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply")
    with env.db() as db:
        assert db.get(RoadmapPaper, "classic").data["arxivId"] == "1706.03762"


def test_metadata_changes_are_previewed_and_discard_preserves_original(env):
    add_catalog(env, metadata())
    saved_board(env, "classic")
    run = start_research(env)
    make_proposal(env, run, [metadata(reason="A new proposed explanation")])
    shown = env.client.get("/api/roadmaps/papers").json()[0]
    assert shown["roadmap"]["reason"] == "A new proposed explanation"
    assert "_papers" not in env.client.get("/api/roadmaps/agent/runs").json()[0]["proposal"]
    with env.db() as db:
        assert db.get(RoadmapPaper, "classic").data["reason"] == metadata()["reason"]
    env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/discard")
    shown = env.client.get("/api/roadmaps/papers").json()[0]
    assert shown["roadmap"]["reason"] == metadata()["reason"]


def test_metadata_changes_commit_atomically_with_applied_board(env):
    add_catalog(env, metadata())
    saved_board(env, "classic")
    run = start_research(env)
    make_proposal(env, run, [metadata(reason="Reviewed milestone explanation")])
    with env.db() as db:
        assert db.get(RoadmapPaper, "classic").data["reason"] == metadata()["reason"]
    applied = env.client.post(f"/api/roadmaps/agent/runs/{run['id']}/apply")
    assert applied.status_code == 200
    with env.db() as db:
        assert db.get(RoadmapPaper, "classic").data["reason"] == "Reviewed milestone explanation"
        assert db.get(RoadmapRun, run["id"]).status == "applied"
        assert db.get(RoadmapWorkspace, 1).state == applied.json()["state"]


@pytest.mark.parametrize("value,expected", [
    ("1706.03762v3", "1706.03762"), ("https://arxiv.org/abs/1706.03762v1", "1706.03762"),
    ("https://arxiv.org/pdf/hep-th/9901001v2.pdf", "hep-th/9901001"), ("arXiv:hep-th/9901001", "hep-th/9901001"),
])
def test_canonical_arxiv_id(value, expected):
    assert roadmap_import.canonical_arxiv_id(value) == expected


@pytest.mark.parametrize("value", ["../../escape", "https://evil.test/1706.03762", "1706.03762?x=y", "text 1706.03762", "file:///1706.03762"])
def test_invalid_arxiv_identifiers_are_rejected(value):
    with pytest.raises(ValueError):
        roadmap_import.canonical_arxiv_id(value)


class FakeResponse(io.BytesIO):
    def __init__(self, data, url="https://arxiv.org/pdf/1706.03762", length=None):
        super().__init__(data)
        self.url = url
        self.headers = {} if length is False else {"Content-Length": str(len(data) if length is None else length)}

    def geturl(self):
        return self.url


def pdf_bytes():
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def fake_http(monkeypatch, data=None, **kwargs):
    calls = []
    data = pdf_bytes() if data is None else data

    def open_url(request, timeout):
        calls.append(request.full_url)
        return FakeResponse(data, **kwargs)

    monkeypatch.setattr(roadmap_import.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_url))
    monkeypatch.setattr(roadmap_import, "wait_for_arxiv_slot", lambda: None)
    return calls


def test_download_is_official_atomic_version_deduped_and_no_overwrite(monkeypatch, tmp_path):
    calls = fake_http(monkeypatch)
    path = roadmap_import.download_arxiv("1706.03762v2", tmp_path)
    assert path.read_bytes().startswith(b"%PDF-")
    assert roadmap_import.download_arxiv("1706.03762v3", tmp_path) == path
    assert calls == ["https://arxiv.org/pdf/1706.03762"]
    assert not list(tmp_path.glob("*.part"))
    (tmp_path / "arxiv_2001.00001.pdf").write_text("User file")
    with pytest.raises(ValueError, match="同名文件"):
        roadmap_import.download_arxiv("2001.00001", tmp_path)
    assert (tmp_path / "arxiv_2001.00001.pdf").read_text() == "User file"


@pytest.mark.parametrize("data,kwargs", [
    (b"<html>error</html>", {}), (b"%PDF-1.7 incomplete", {"length": 100}),
    (b"%PDF-1.7 data", {"url": "https://evil.test/file"}),
    (b"%PDF-1.7 large", {"length": 100 * 1024 * 1024}),
    (b"%PDF-1.7\ntruncated without xref", {"length": False}),
])
def test_failed_download_never_leaves_a_pdf(monkeypatch, tmp_path, data, kwargs):
    fake_http(monkeypatch, data, **kwargs)
    with pytest.raises(ValueError):
        roadmap_import.download_arxiv("1706.03762", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_redirect_handler_rejects_nonofficial_host():
    handler = roadmap_import.ArxivRedirectHandler()
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/private")


def test_pdf_download_rate_limit_waits_only_remaining_interval(monkeypatch):
    ticks = iter([10.0, 10.0, 11.0, 13.0, 20.0, 20.0])
    waits = []
    monkeypatch.setattr(roadmap_import, "_last_request", None)
    monkeypatch.setattr(roadmap_import.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(roadmap_import.time, "sleep", waits.append)
    roadmap_import.wait_for_arxiv_slot()
    roadmap_import.wait_for_arxiv_slot()
    roadmap_import.wait_for_arxiv_slot()
    assert waits == [2.0, 0]


def start_import(env, snap):
    response = env.client.post("/api/roadmaps/imports", json={
        "boardId": snap["state"]["boards"][0]["id"], "revision": snap["revision"],
    })
    assert response.status_code == 202, response.text
    return response.json()


def test_batch_download_marks_library_only_after_success_continues_failures_and_retries(env, monkeypatch):
    add_catalog(env, metadata(), metadata("failure", title="Second Paper", arxivId="2001.00001"),
                metadata("nonarxiv", title="Conference-only Paper", arxivId=None))
    snap = saved_board(env, "classic", "failure", "nonarxiv")
    job = start_import(env, snap)
    assert start_import(env, snap)["job_id"] == job["job_id"]
    statuses = {p["id"]: p["roadmap"] for p in env.client.get("/api/roadmaps/papers").json()}
    assert statuses["classic"]["downloadStatus"] == "queued"
    assert statuses["classic"]["libraryStatus"] == "not_imported"
    assert statuses["nonarxiv"]["downloadStatus"] is None
    calls = []

    def download(aid, directory):
        calls.append(aid)
        if aid == "2001.00001":
            raise RuntimeError("arXiv unavailable")
        Path(directory).mkdir(exist_ok=True)
        target = Path(directory) / ("arxiv_" + aid + ".pdf")
        target.write_bytes(pdf_bytes())
        return target

    monkeypatch.setattr(roadmap_import, "download_arxiv", download)
    claimed = env.queue.claim()
    result = roadmap_import.import_board(Context(env.queue, claimed), claimed["payload"])
    env.queue.update(job["job_id"], status="completed", result=result)
    assert result["imported"] == result["failed"] == 1
    assert result["total"] == 2
    statuses = {p["id"]: p["roadmap"] for p in env.client.get("/api/roadmaps/papers").json()}
    assert statuses["classic"]["libraryStatus"] == "in_library"
    assert statuses["classic"]["reviewReady"] is False
    assert statuses["failure"]["libraryStatus"] == "not_imported"
    assert statuses["failure"]["downloadStatus"] == "failed"
    with env.db() as db:
        assert db.query(Paper).count() == 1
        assert db.query(Paper).one().processed is False
    again = start_import(env, snap)
    claimed = env.queue.claim()
    result = roadmap_import.import_board(Context(env.queue, claimed), claimed["payload"])
    assert result["skipped"] == result["failed"] == 1
    assert calls.count("1706.03762") == 1
    assert again["job_id"] != job["job_id"]


def test_import_checks_revision_and_refuses_nonarxiv_only(env):
    add_catalog(env, metadata("offline", arxivId=None))
    snap = saved_board(env, "offline")
    body = {"boardId": snap["state"]["boards"][0]["id"], "revision": snap["revision"] - 1}
    assert env.client.post("/api/roadmaps/imports", json=body).status_code == 409
    body["revision"] += 1
    assert env.client.post("/api/roadmaps/imports", json=body).status_code == 422


def test_import_matches_existing_library_without_downloading(env, monkeypatch):
    add_catalog(env, metadata())
    with env.db() as db:
        db.add(Paper(id="existing", filename="renamed.pdf", filepath="known.pdf", file_hash="known",
                     title=metadata()["title"], authors=["Alice"]))
        db.commit()
    monkeypatch.setattr(roadmap_import, "download_arxiv", lambda *_: pytest.fail("Must skip existing library paper"))
    assert roadmap_import.import_paper("classic") == {"status": "skipped", "libraryPaperId": "existing"}


@pytest.mark.parametrize("authors", [[], ["Unrelated Author"]])
def test_same_title_without_corresponding_authors_is_not_in_library(env, authors):
    add_catalog(env, metadata())
    with env.db() as db:
        db.add(Paper(id="unrelated", filename="other.pdf", filepath="other.pdf", file_hash="other",
                     title=metadata()["title"], authors=authors))
        db.commit()
    assert env.client.get("/api/roadmaps/papers").json()[0]["roadmap"]["libraryStatus"] == "not_imported"


def test_ambiguous_same_title_author_matches_do_not_suppress_download(env):
    add_catalog(env, metadata())
    with env.db() as db:
        for pid in ("first", "second"):
            db.add(Paper(id=pid, filename=pid + ".pdf", filepath=pid + ".pdf", file_hash=pid,
                         title=metadata()["title"], authors=["Alice"]))
        db.commit()
    assert env.client.get("/api/roadmaps/papers").json()[0]["roadmap"]["libraryStatus"] == "not_imported"


def test_same_title_author_with_different_arxiv_id_is_not_a_match(env):
    add_catalog(env, metadata())
    with env.db() as db:
        db.add(Paper(id="different-id", filename="arxiv_2401.00001.pdf", filepath="other.pdf", file_hash="other",
                     title=metadata()["title"], authors=["Alice"]))
        db.commit()
    assert env.client.get("/api/roadmaps/papers").json()[0]["roadmap"]["libraryStatus"] == "not_imported"


def test_successful_checkpoint_does_not_mask_later_library_deletion(env, monkeypatch):
    add_catalog(env, metadata())
    snap = saved_board(env, "classic")
    start_import(env, snap)
    claimed = env.queue.claim()
    checkpoint = {"import:classic": {"status": "imported", "libraryPaperId": "deleted"}}
    env.queue.update(claimed["job_id"], checkpoints=checkpoint)
    claimed = env.queue.get(claimed["job_id"])
    attempts = []

    def retry(pid):
        attempts.append(pid)
        raise RuntimeError("try again")

    monkeypatch.setattr(roadmap_import, "import_paper", retry)
    result = roadmap_import.import_board(Context(env.queue, claimed), claimed["payload"])
    assert attempts == ["classic"]
    assert result["failed"] == 1
