"""Explicit preview lifecycle; generation never writes the live canvas."""

from copy import deepcopy
from typing import Annotated
from uuid import uuid4

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from models import DomainAgentRun
from pydantic import BaseModel, ConfigDict, Field
from services import task_runtime
from services.domain_agent_state import (
    BUSY,
    begin_write,
    content,
    public_run,
    refresh_run,
)
from sqlalchemy import func
from sqlalchemy.orm import Session, defer

from routers.domains import Board, workspace

router = APIRouter(prefix="/api/domains/agent", tags=["domains"])


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    boardId: str
    revision: int = Field(ge=1)
    # Measured card heights improve layout without changing paper content.
    heights: dict[str, float] = Field(default_factory=dict)


@router.get("/runs")
def runs(db: Annotated[Session, Depends(get_db)]):
    # Poll only the latest run for each board, without loading old snapshots.
    ranked = db.query(
        DomainAgentRun.id.label("id"),
        func.row_number()
        .over(
            partition_by=DomainAgentRun.board_id,
            order_by=(DomainAgentRun.created.desc(), DomainAgentRun.id.desc()),
        )
        .label("rank"),
    ).subquery()
    rows = (
        db.query(DomainAgentRun)
        .join(ranked, DomainAgentRun.id == ranked.c.id)
        .filter(
            ranked.c.rank == 1,
        )
        .options(defer(DomainAgentRun.snapshot))
        .all()
    )
    latest = {}
    for run in rows:
        if run.board_id in latest:
            continue
        refresh_run(db, run)
        latest[run.board_id] = public_run(run, task_runtime.store())
    db.commit()
    return list(latest.values())


@router.post("/runs", status_code=202)
def start(body: StartRequest, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    row = workspace(db, commit=False)
    if row.revision != body.revision:
        raise HTTPException(409, "画布已变化，请先重新加载后生成。")
    board = next((b for b in row.state["boards"] if b["id"] == body.boardId), None)
    if not board:
        raise HTTPException(404, "类别不存在")
    if len({n["paperId"] for n in board["nodes"] if n["kind"] == "paper"}) < 2:
        raise HTTPException(422, "请先导入至少两篇不同论文。")
    for existing in (
        db.query(DomainAgentRun)
        .filter_by(board_id=body.boardId)
        .filter(DomainAgentRun.status.in_(BUSY))
        .all()
    ):
        refresh_run(db, existing)
        if existing.status in BUSY:
            raise HTTPException(409, "请先应用、放弃或取消当前方案。")
    heights = {
        n["id"]: min(10000, max(60, body.heights.get(n["id"], n["height"])))
        for n in board["nodes"]
    }
    run = DomainAgentRun(
        id=str(uuid4()),
        board_id=body.boardId,
        status="pending",
        snapshot={"board": deepcopy(board), "heights": heights},
    )
    db.add(run)
    db.commit()
    try:
        from routers.jobs import submit_job

        job = submit_job("domain", "domain:" + body.boardId, {"run_id": run.id}, run.id)
        run.job_id = job["job_id"]
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(DomainAgentRun, run.id)
        run.status, run.error = "failed", str(exc)
        db.commit()
        raise
    return public_run(run, task_runtime.store())


def find_run(db, run_id):
    run = db.get(DomainAgentRun, run_id)
    if not run:
        raise HTTPException(404, "方案不存在")
    return run


@router.post("/runs/{run_id}/discard")
def discard(run_id: str, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    run = find_run(db, run_id)
    if run.status in BUSY:
        run.status = "cancelled" if run.status == "pending" else "discarded"
        run.proposal = None
    db.commit()
    if run.job_id and run.status == "cancelled":
        task_runtime.store().cancel_queued(run.job_id)
    return public_run(run, task_runtime.store())


@router.post("/runs/{run_id}/apply")
def apply(run_id: str, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    run = find_run(db, run_id)
    if run.status != "preview" or not run.proposal:
        raise HTTPException(409, "没有可应用的预览")
    row = workspace(db, commit=False)
    state = deepcopy(row.state)
    board = next((b for b in state["boards"] if b["id"] == run.board_id), None)
    if not board or content(board) != content(run.snapshot["board"]):
        raise HTTPException(409, "原画布已变化，请放弃此方案后重新生成。")
    before = deepcopy(board)
    proposal = run.proposal["board"]
    board.update(nodes=proposal["nodes"], edges=proposal["edges"])
    Board.model_validate(board)
    row.state, row.revision = state, row.revision + 1
    run.status = "applied"
    run.proposal = None
    db.commit()
    return {"state": row.state, "revision": row.revision, "before": before}
