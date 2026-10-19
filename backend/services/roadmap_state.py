"""Local roadmap catalog, preview leases, and library adoption status."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from database import SessionLocal
from fastapi import HTTPException
from models import Paper, RoadmapPaper, RoadmapRun
from pydantic import Field, field_validator
from routers.domains import Board, StrictModel

from services.domain_agent_state import begin_write, content
from services.personal_recommendation import base_id, normalized_title

BUSY = ("pending", "preview")


class PaperMetadata(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    year: str = Field(default="", max_length=30)
    authors: list[str] = Field(default_factory=list, max_length=100)
    abstract: str = Field(default="", max_length=20000)
    arxivId: Optional[str] = Field(default=None, max_length=100)  # noqa: UP045 - Pydantic evaluates annotations on Python 3.9
    doi: Optional[str] = Field(default=None, max_length=300)  # noqa: UP045 - Pydantic evaluates annotations on Python 3.9
    sourceUrl: str = Field(min_length=1, max_length=2000)
    reason: str = Field(default="", max_length=5000)
    branch: str = Field(default="", max_length=200)
    references: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("arxivId")
    @classmethod
    def arxiv_id(cls, value):
        if value is None:
            return value
        from services.roadmap_import import canonical_arxiv_id

        return canonical_arxiv_id(value)

    @field_validator("sourceUrl")
    @classmethod
    def source_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
            raise ValueError("A public HTTP source URL is required")
        return value


class Cancelled(Exception):
    pass


def check(run_id):
    with SessionLocal() as db:
        run = db.get(RoadmapRun, run_id)
        if not run or run.status != "pending":
            raise Cancelled()


def fail_run(run_id, error):
    with SessionLocal() as db:
        run = db.get(RoadmapRun, run_id)
        if run and run.status == "pending":
            run.status, run.error = "failed", str(error)[:3000]
            db.commit()


def persist_preview(run_id, proposal, papers):
    """Called only after research has verified sources; no public catalog-write API."""
    board = Board.model_validate(proposal["board"]).model_dump()
    verified = [PaperMetadata.model_validate(p).model_dump() for p in papers]
    if len({p["id"] for p in verified}) != len(verified):
        raise ValueError("Duplicate researched paper IDs")
    with SessionLocal() as db:
        begin_write(db)
        run = db.get(RoadmapRun, run_id)
        if not run or run.status != "pending":
            raise Cancelled()
        if board["id"] != run.board_id:
            raise ValueError("Research proposal changed its board")
        for data in verified:
            row = db.get(RoadmapPaper, data["id"])
            if row is None:
                db.add(RoadmapPaper(id=data["id"], data=data))
            else:
                data.update(merge_metadata(row.data, data))
        db.flush()
        known = {p[0] for p in db.query(RoadmapPaper.id).all()}
        validate_references(board, known)
        # Existing card metadata is part of the proposal, too. Discarding must
        # not leave newly generated explanations attached to the original graph.
        run.proposal = dict(deepcopy(proposal), board=board, _papers=verified)
        run.status = "preview"
        db.commit()


def merge_metadata(old, incoming):
    data = deepcopy(incoming)
    if normalized_title(old.get("title")) != normalized_title(data["title"]) or any(
        old.get(key) and data.get(key) and old[key] != data[key]
        for key in ("arxivId", "doi")
    ):
        raise ValueError("A researched paper ID cannot change its source")
    # A verified arXiv version may be discovered for a DOI-only classic. Known
    # identifiers survive source responses which omit those optional fields.
    for key in ("arxivId", "doi"):
        data[key] = data.get(key) or old.get(key)
    return data


def validate_references(board, known):
    if any(n["kind"] == "paper" and n["paperId"] not in known for n in board["nodes"]):
        raise HTTPException(422, "只能使用已核验来源的识途论文卡片。")


def refresh_run(db, run):
    if run.status != "pending":
        return
    from services.task_runtime import store

    queue = store()
    job = queue.get(run.job_id) if run.job_id else queue.latest("roadmap:" + run.board_id)
    if job and job.get("payload", {}).get("run_id") == run.id:
        if not run.job_id:
            run.job_id = job["job_id"]
        if job["status"] in ("failed", "interrupted", "cancelled", "completed"):
            run.status = "cancelled" if job["status"] == "cancelled" else "failed"
            run.error = job.get("error") or "调研任务未生成有效预览，请重新调研。"
    elif (datetime.now(timezone.utc) - run.created.replace(tzinfo=timezone.utc)).total_seconds() > 60:
        run.status, run.error = "failed", "调研任务未能提交，请重新调研。"


def guard_readonly(db, before, after):
    old, new = ({b["id"]: b for b in state["boards"]} for state in (before, after))
    for run in db.query(RoadmapRun).filter(RoadmapRun.status.in_(BUSY)).all():
        refresh_run(db, run)
        if run.status in BUSY and (
            run.board_id not in new or run.board_id not in old
            or content(old[run.board_id]) != content(new[run.board_id])
            or old[run.board_id]["name"] != new[run.board_id]["name"]
        ):
            raise HTTPException(409, "该方向正在调研或预览，仅可浏览。请先应用、放弃或取消。")


def public_run(run, queue=None):
    job = queue.get(run.job_id) if queue and run.job_id else None
    proposal = deepcopy(run.proposal) if run.status == "preview" else None
    if proposal:
        proposal.pop("_papers", None)
    return {
        "id": run.id, "boardId": run.board_id, "status": run.status,
        "jobId": run.job_id, "error": run.error,
        "progress": (job or {}).get("progress") or {},
        "proposal": proposal,
    }


def library_index(db):
    """Only metadata is needed; never load PDF text, notes or chat histories."""
    rows = db.query(Paper.id, Paper.filename, Paper.title, Paper.authors, Paper.filepath, Paper.processed).all()
    by_id, by_arxiv, by_title = {}, {}, {}
    for row in rows:
        by_id[str(row.id)] = row
        aid = base_id(row.filename)
        if aid:
            by_arxiv.setdefault(aid, row)
        title = normalized_title(row.title)
        if title:
            by_title.setdefault(title, []).append(row)
    return by_id, by_arxiv, by_title


def library_match(paper, index):
    by_id, by_arxiv, by_title = index
    data = paper.data
    identified = by_id.get(paper.library_paper_id) or by_arxiv.get(data.get("arxivId"))
    if identified is not None:
        return identified
    authors = {normalized_title(name) for name in data.get("authors", []) if isinstance(name, str) and normalized_title(name)}
    if not authors:
        return None
    candidates = []
    for row in by_title.get(normalized_title(data["title"]), []):
        local_id = base_id(row.filename)
        if local_id and data.get("arxivId") and local_id != data["arxivId"]:
            continue
        local_authors = {normalized_title(name) for name in (row.authors or []) if isinstance(name, str) and normalized_title(name)}
        if authors & local_authors:
            candidates.append(row)
    # Generic titles can name unrelated papers. A name match must corroborate
    # the title, and ambiguous matches must not suppress a requested download.
    return candidates[0] if len(candidates) == 1 else None


def public_paper(paper, index, downloads=None):
    data = paper.data
    local = library_match(paper, index)
    download = (downloads or {}).get(paper.id, {}) if local is None else {}
    return {
        "id": paper.id, "title": data["title"], "year": data.get("year", ""),
        "category": data.get("branch", ""),
        "sections": {"core_contribution": data.get("reason", ""),
                     "abstract_summary": data.get("abstract", ""), "problem": "", "motivation": ""},
        "eligible": True, "reason": None, "incomplete": False,
        "roadmap": {
            "arxivId": data.get("arxivId"), "doi": data.get("doi"),
            "sourceUrl": data["sourceUrl"], "reason": data.get("reason", ""),
            "branch": data.get("branch", ""),
            "libraryPaperId": str(local.id) if local is not None else None,
            "reviewReady": bool(local.processed) if local is not None else False,
            "libraryStatus": "in_library" if local is not None else "not_imported" if data.get("arxivId") else "no_arxiv",
            "downloadStatus": download.get("status"), "downloadError": download.get("error"),
        },
    }
