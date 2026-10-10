"""Local research canvas with validated references and optimistic saves."""

from __future__ import annotations

from typing import Annotated, Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models import DomainWorkspace, Paper
from pydantic import BaseModel, ConfigDict, Field, model_validator
from services.paper_category_service import effective_paper_category
from services.vlm_service import parse_extraction_response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

router = APIRouter(prefix="/api/domains", tags=["domains"])
REVIEW_FIELDS = ("core_contribution", "abstract_summary", "problem", "motivation")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Viewport(StrictModel):
    x: float = 80
    y: float = 80
    zoom: float = Field(default=1, ge=0.15, le=2.5)


class CanvasNode(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["paper", "text", "group"]
    x: float = Field(ge=-1000000, le=1000000)
    y: float = Field(ge=-1000000, le=1000000)
    width: float = Field(default=280, ge=120, le=10000)
    height: float = Field(default=160, ge=60, le=10000)
    paperId: str | None = Field(default=None, max_length=100)
    groupId: str | None = Field(default=None, max_length=100)
    text: str = Field(default="", max_length=50000)
    locked: bool = False

    @model_validator(mode="after")
    def check_kind(self):
        if (self.kind == "paper") != bool(self.paperId):
            raise ValueError("Only imported paper references can have a paperId")
        if self.kind == "group" and self.groupId:
            raise ValueError("Nested groups are not supported")
        return self


class CanvasEdge(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: str
    target: str
    label: str = Field(default="", max_length=2000)
    locked: bool = False


class Board(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=10000)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=20000)
    viewport: Viewport = Field(default_factory=Viewport)

    @model_validator(mode="after")
    def check_references(self):
        if not self.name.strip():
            raise ValueError("Domain name is required")
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or len({e.id for e in self.edges}) != len(
            self.edges
        ):
            raise ValueError("Duplicate element IDs")
        for node in self.nodes:
            if node.groupId and (
                node.groupId not in nodes or nodes[node.groupId].kind != "group"
            ):
                raise ValueError("Invalid group reference")
        for edge in self.edges:
            if edge.source == edge.target or any(
                key not in nodes or nodes[key].kind != "paper"
                for key in (edge.source, edge.target)
            ):
                raise ValueError("Connections must join two paper instances")
        return self


class WorkspaceState(StrictModel):
    boards: list[Board] = Field(max_length=100)
    activeId: str | None = None

    @model_validator(mode="after")
    def check_ids(self):
        ids = [b.id for b in self.boards]
        node_ids = [n.id for b in self.boards for n in b.nodes]
        if len(set(ids)) != len(ids) or len(set(node_ids)) != len(node_ids):
            raise ValueError("Domain and instance IDs must be unique")
        if self.activeId is not None and self.activeId not in ids:
            raise ValueError("Active domain does not exist")
        return self


class SaveWorkspace(StrictModel):
    revision: int = Field(ge=1)
    state: WorkspaceState


def review_paper(paper: Paper) -> dict:
    """Use the same parser and field names as the full Paper Review page."""
    try:
        extraction = parse_extraction_response(paper.raw_llm_response or "")
        if not isinstance(extraction, dict):
            extraction = {}
    except (ValueError, TypeError, KeyError):
        extraction = {}
    sections = {
        key: extraction.get(key, "").strip()
        if isinstance(extraction.get(key), str)
        else ""
        for key in REVIEW_FIELDS
    }
    placeholders = {"null", "none", "n/a", "暂无", "待生成", "-"}
    sections = {k: "" if v.lower() in placeholders else v for k, v in sections.items()}
    reason = (
        "not_reviewed"
        if not paper.processed
        else "empty_review"
        if not any(sections.values())
        else None
    )
    year = extraction.get("year")
    return {
        "id": str(paper.id),
        "title": paper.title or extraction.get("title") or paper.filename,
        "year": str(year) if isinstance(year, (str, int)) else "",
        "category": effective_paper_category(paper, extraction),
        "sections": sections,
        "eligible": reason is None,
        "reason": reason,
        "incomplete": not all(sections.values()),
    }


@router.get("/papers")
def domain_papers(db: Annotated[Session, Depends(get_db)]):
    # The import picker does not need full PDF text or chat transcripts.
    papers = (
        db.query(Paper)
        .options(
            load_only(
                Paper.id,
                Paper.title,
                Paper.filename,
                Paper.processed,
                Paper.raw_llm_response,
                Paper.paper_category_model,
                Paper.paper_category_override,
            )
        )
        .order_by(Paper.created_at)
        .all()
    )
    return [review_paper(p) for p in papers]


def workspace(db: Session) -> DomainWorkspace:
    row = db.get(DomainWorkspace, 1)
    if row is None:
        initial = WorkspaceState(
            boards=[Board(id="llm", name="LLM"), Board(id="3d", name="三维重建")],
            activeId="llm",
        )
        row = DomainWorkspace(id=1, revision=1, state=initial.model_dump(), admitted={})
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.get(DomainWorkspace, 1)
    return row


@router.get("")
def get_workspace(db: Annotated[Session, Depends(get_db)]):
    row = workspace(db)
    return {"revision": row.revision, "state": row.state}


@router.put("")
def save_workspace(body: SaveWorkspace, db: Annotated[Session, Depends(get_db)]):
    row = workspace(db)
    if body.revision != row.revision:
        raise HTTPException(
            409, "The canvas changed in another window. Reload before saving."
        )
    admitted = dict(row.admitted or {})
    papers = {}
    for board in body.state.boards:
        for node in board.nodes:
            if node.kind != "paper":
                continue
            if node.id in admitted:
                if admitted[node.id] != node.paperId:
                    raise HTTPException(
                        422, "A paper instance cannot change its source"
                    )
                continue
            if node.paperId not in papers:
                paper = db.get(Paper, node.paperId)
                papers[node.paperId] = review_paper(paper) if paper else None
            paper = papers[node.paperId]
            if not paper or not paper["eligible"]:
                raise HTTPException(
                    422, "Import requires a readable paper review with nonempty content"
                )
            admitted[node.id] = node.paperId
    # Conditional UPDATE also detects a concurrent writer after the read above.
    updated = (
        db.query(DomainWorkspace)
        .filter_by(id=1, revision=body.revision)
        .update(
            {
                "state": body.state.model_dump(),
                "admitted": admitted,
                "revision": body.revision + 1,
            },
            synchronize_session=False,
        )
    )
    if not updated:
        db.rollback()
        raise HTTPException(
            409, "The canvas changed in another window. Reload before saving."
        )
    db.commit()
    return {"revision": body.revision + 1}
