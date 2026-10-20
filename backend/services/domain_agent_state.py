"""Library-side preview leases. Writes serialize with ordinary canvas saves."""

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import HTTPException
from models import DomainAgentRun
from sqlalchemy import text

BUSY = ("pending", "preview")


def begin_write(db):
    if db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def content(board):
    # Pan/zoom, active tab and taxonomy names are browsing / system metadata.
    # Canonical defaults keep previews made before optional schema additions
    # comparable to the current workspace (e.g. discussion=None on old edges).
    from routers.domains import Board

    return Board.model_validate(board).model_dump(include={"nodes", "edges", "researchKeywords"})


def refresh_run(db, run):
    if run.status != "pending":
        return
    from services.task_runtime import store

    queue = store()
    job = (
        queue.get(run.job_id) if run.job_id else queue.latest("domain:" + run.board_id)
    )
    if job and job.get("payload", {}).get("run_id") == run.id:
        if not run.job_id:
            run.job_id = job["job_id"]
        if job["status"] in ("failed", "interrupted", "cancelled"):
            run.status = "cancelled" if job["status"] == "cancelled" else "failed"
            run.error = job.get("error") or "任务已中断，请重新生成。"
    elif (
        datetime.now(timezone.utc) - run.created.replace(tzinfo=timezone.utc)
    ).total_seconds() > 60:
        run.status, run.error = "failed", "任务未能提交，请重新生成。"


def guard_readonly(db, before, after):
    old = {b["id"]: b for b in before["boards"]}
    new = {b["id"]: b for b in after["boards"]}
    for run in db.query(DomainAgentRun).filter(DomainAgentRun.status.in_(BUSY)).all():
        refresh_run(db, run)
        if run.status not in BUSY:
            continue
        if (
            run.board_id not in new
            or run.board_id not in old
            or content(old[run.board_id]) != content(new[run.board_id])
            or old[run.board_id]["name"] != new[run.board_id]["name"]
        ):
            raise HTTPException(
                409, "该类别正在生成或预览，仅可浏览。请先应用、放弃或取消。"
            )


def public_run(run, queue=None):
    job = queue.get(run.job_id) if queue and run.job_id else None
    return {
        "id": run.id,
        "boardId": run.board_id,
        "status": run.status,
        "jobId": run.job_id,
        "error": run.error,
        "progress": (job or {}).get("progress") or {},
        "proposal": deepcopy(run.proposal) if run.status == "preview" else None,
    }
