"""Local discovery canvases, research previews and explicit batch adoption."""

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from models import RoadmapPaper, RoadmapRun, RoadmapWorkspace
from pydantic import Field
from services import task_runtime
from services.domain_agent_state import begin_write, content
from services.domain_categories import reconcile_categories
from services.paper_category_service import get_active_categories
from services.roadmap_state import (
    BUSY,
    guard_readonly,
    library_index,
    merge_metadata,
    public_paper,
    public_run,
    refresh_run,
    validate_references,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from routers.domains import Board, SaveWorkspace, StrictModel, WorkspaceState

router = APIRouter(prefix="/api/roadmaps", tags=["roadmaps"])


def workspace(db: Session, commit: bool = True) -> RoadmapWorkspace:
    row = db.get(RoadmapWorkspace, 1)
    if row is None:
        state = WorkspaceState.model_validate(reconcile_categories(
            {"boards": [], "activeId": None}, get_active_categories()
        )).model_dump()
        row = RoadmapWorkspace(id=1, revision=1, state=state)
        db.add(row)
        try:
            db.commit() if commit else db.flush()
        except IntegrityError:
            db.rollback()
            row = db.get(RoadmapWorkspace, 1)
    for _ in range(3):
        state = WorkspaceState.model_validate(reconcile_categories(row.state, get_active_categories())).model_dump()
        if state == row.state:
            return row
        changed = db.query(RoadmapWorkspace).filter_by(id=1, revision=row.revision).update(
            {"state": state, "revision": row.revision + 1}, synchronize_session=False,
        )
        if changed:
            db.commit() if commit else db.flush()
        else:
            db.rollback()
        db.expire_all()
        row = db.get(RoadmapWorkspace, 1)
        if changed:
            return row
    raise HTTPException(409, "路线图已在其他窗口变化，请重新加载。")


@router.get("")
def get_workspace(db: Annotated[Session, Depends(get_db)]):
    row = workspace(db)
    return {"revision": row.revision, "state": row.state}


@router.put("")
def save_workspace(body: SaveWorkspace, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    row = workspace(db, commit=False)
    after = body.state.model_dump()
    guard_readonly(db, row.state, after)
    if body.revision != row.revision:
        raise HTTPException(409, "路线图已变化，请重新加载后保存。")
    inherited = {b["id"]: b["category"] for b in row.state["boards"] if b.get("category")}
    submitted = {b.id: b.category for b in body.state.boards if b.category}
    if submitted != inherited or any(b.category and b.name != b.category for b in body.state.boards):
        raise HTTPException(422, "请在知识页管理系统大类。")
    known = {p[0] for p in db.query(RoadmapPaper.id).all()}
    for board in after["boards"]:
        validate_references(board, known)
    updated = db.query(RoadmapWorkspace).filter_by(id=1, revision=body.revision).update(
        {"state": after, "revision": body.revision + 1}, synchronize_session=False,
    )
    if not updated:
        raise HTTPException(409, "路线图已变化，请重新加载后保存。")
    db.commit()
    return {"revision": body.revision + 1}


@router.get("/papers")
def papers(db: Annotated[Session, Depends(get_db)]):
    from services.roadmap_import import download_states

    index = library_index(db)
    downloads = download_states(task_runtime.store())
    proposals = db.query(RoadmapRun.proposal).filter_by(status="preview").all()
    preview_data = {p["id"]: p for row in proposals for p in (row.proposal or {}).get("_papers", [])}
    return [public_paper(SimpleNamespace(id=p.id, data=preview_data.get(p.id, p.data), library_paper_id=p.library_paper_id), index, downloads)
            for p in db.query(RoadmapPaper).order_by(RoadmapPaper.created).all()]


class StartRequest(StrictModel):
    boardId: str
    revision: int = Field(ge=1)
    heights: dict[str, float] = Field(default_factory=dict)
    relationPolicy: Literal["refresh", "clear", "preserve"] = "refresh"


def find_board(row, board_id):
    board = next((b for b in row.state["boards"] if b["id"] == board_id), None)
    if not board:
        raise HTTPException(404, "技术方向不存在")
    return board


@router.get("/agent/runs")
def runs(db: Annotated[Session, Depends(get_db)]):
    ranked = db.query(
        RoadmapRun.id.label("id"),
        func.row_number().over(partition_by=RoadmapRun.board_id,
                              order_by=(RoadmapRun.created.desc(), RoadmapRun.id.desc())).label("rank"),
    ).subquery()
    rows = db.query(RoadmapRun).join(ranked, RoadmapRun.id == ranked.c.id).filter(
        ranked.c.rank == 1
    ).options(defer(RoadmapRun.snapshot)).all()
    for run in rows:
        refresh_run(db, run)
    db.commit()
    return [public_run(run, task_runtime.store()) for run in rows]


def ensure_no_research(db, board_id):
    for run in db.query(RoadmapRun).filter_by(board_id=board_id).filter(RoadmapRun.status.in_(BUSY)).all():
        refresh_run(db, run)
        if run.status in BUSY:
            raise HTTPException(409, "请先应用、放弃或取消当前调研方案。")


@router.post("/agent/runs", status_code=202)
def start(body: StartRequest, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    row = workspace(db, commit=False)
    if row.revision != body.revision:
        raise HTTPException(409, "路线图已变化，请重新加载后调研。")
    board = find_board(row, body.boardId)
    ensure_no_research(db, body.boardId)
    heights = {n["id"]: min(10000, max(60, body.heights.get(n["id"], n["height"]))) for n in board["nodes"]}
    run = RoadmapRun(id=str(uuid4()), board_id=body.boardId, status="pending",
                     snapshot={"board": deepcopy(board), "heights": heights,
                               "relationPolicy": body.relationPolicy})
    db.add(run)
    db.commit()
    try:
        from routers.jobs import submit_job

        job = submit_job("roadmap", "roadmap:" + body.boardId, {"run_id": run.id}, run.id)
        run.job_id = job["job_id"]
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(RoadmapRun, run.id)
        run.status, run.error = "failed", str(exc)[:3000]
        db.commit()
        raise
    return public_run(run, task_runtime.store())


def find_run(db, run_id):
    run = db.get(RoadmapRun, run_id)
    if not run:
        raise HTTPException(404, "调研方案不存在")
    return run


@router.post("/agent/runs/{run_id}/discard")
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


@router.post("/agent/runs/{run_id}/apply")
def apply(run_id: str, db: Annotated[Session, Depends(get_db)]):
    begin_write(db)
    run = find_run(db, run_id)
    if run.status != "preview" or not run.proposal:
        raise HTTPException(409, "没有可应用的预览")
    row = workspace(db, commit=False)
    state = deepcopy(row.state)
    board = next((b for b in state["boards"] if b["id"] == run.board_id), None)
    if not board or content(board) != content(run.snapshot["board"]):
        raise HTTPException(409, "原路线图已变化，请放弃此方案后重新调研。")
    before = deepcopy(board)
    proposed = Board.model_validate(run.proposal["board"]).model_dump()
    validate_references(proposed, {p[0] for p in db.query(RoadmapPaper.id).all()})
    for metadata in run.proposal.get("_papers", []):
        paper = db.get(RoadmapPaper, metadata["id"])
        paper.data = merge_metadata(paper.data, metadata)
    board.update(nodes=proposed["nodes"], edges=proposed["edges"])
    WorkspaceState.model_validate(state)
    row.state, row.revision = state, row.revision + 1
    run.status, run.proposal = "applied", {"report": run.proposal.get("report", "")}
    db.commit()
    return {"state": row.state, "revision": row.revision, "before": before}


@router.get("/report")
def report(boardId: str, db: Annotated[Session, Depends(get_db)]):
    rows = db.query(RoadmapRun.id, RoadmapRun.status, RoadmapRun.proposal).filter(
        RoadmapRun.board_id == boardId, RoadmapRun.status.in_(("preview", "applied")),
    ).order_by(RoadmapRun.created.desc(), RoadmapRun.id.desc()).all()
    for row in rows:
        markdown = (row.proposal or {}).get("report")
        if markdown:
            from services.roadmap_figures import report_asset_ids

            return {"boardId": boardId, "runId": row.id, "status": row.status, "markdown": markdown,
                    "hasAssets": bool(report_asset_ids(markdown))}
    return None


@router.get("/report-assets/{asset_id}.png")
def report_asset(asset_id: str, db: Annotated[Session, Depends(get_db)]):
    from services.roadmap_figures import asset_path, report_asset_ids

    try:
        path = asset_path(asset_id)
    except ValueError:
        raise HTTPException(404, "报告图片不存在") from None
    # Only images actually referenced by retained reports are exposed; cached
    # images from failed/discarded research are not public static files.
    referenced = any(asset_id in report_asset_ids((proposal or {}).get("report", ""))
                     for (proposal,) in db.query(RoadmapRun.proposal).filter(
                         RoadmapRun.status.in_(("preview", "applied"))).all())
    if not referenced or not path.is_file():
        raise HTTPException(404, "报告图片不存在")
    return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/report/export")
def export_report(boardId: str, db: Annotated[Session, Depends(get_db)]):
    from services.roadmap_figures import ASSET_PREFIX, asset_path, report_asset_ids

    data = report(boardId, db)
    if not data:
        raise HTTPException(404, "暂无可下载的调研报告")
    markdown = data["markdown"]
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for asset_id in report_asset_ids(markdown):
            try:
                path = asset_path(asset_id)
                raw = path.read_bytes()
            except (ValueError, OSError):
                raise HTTPException(409, "报告图片缺失，请重新生成报告后导出") from None
            name = "assets/" + asset_id + ".png"
            archive.writestr(name, raw)
            markdown = markdown.replace(ASSET_PREFIX + asset_id + ".png", name)
        archive.writestr("report.md", markdown.encode("utf-8"))
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="roadmap-report.zip"'})


class ImportRequest(StrictModel):
    boardId: str
    revision: int = Field(ge=1)


@router.post("/imports", status_code=202)
def start_import(body: ImportRequest, db: Annotated[Session, Depends(get_db)]):
    from routers.jobs import submit_job

    row = workspace(db)
    if row.revision != body.revision:
        raise HTTPException(409, "路线图已变化，请重新加载后下载。")
    board = find_board(row, body.boardId)
    ensure_no_research(db, body.boardId)
    ids = sorted({n["paperId"] for n in board["nodes"] if n["kind"] == "paper"})
    registered = db.query(RoadmapPaper).filter(RoadmapPaper.id.in_(ids)).all()
    downloadable = sorted(p.id for p in registered if p.data.get("arxivId"))
    if not downloadable:
        raise HTTPException(422, "当前路线图没有可下载的 arXiv 论文。")
    db.commit()
    return submit_job("roadmap_import", "roadmap_import:" + body.boardId,
                      {"board_id": body.boardId, "paper_ids": downloadable})


@router.get("/imports")
def latest_import(boardId: str):
    from routers.jobs import public_job

    return public_job(task_runtime.store().latest("roadmap_import:" + boardId))
