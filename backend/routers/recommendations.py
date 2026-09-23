"""Personal feed plus a narrowly scoped, per-user worker protocol."""

# Pydantic evaluates these annotations at runtime on the supported Python 3.9.
# ruff: noqa: FA100
import hashlib
import secrets
from typing import Annotated, Literal, Optional

from auth_deps import current_user
from cloud_models import RecBatch, RecEvent, RecWorker
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from services import recommendation_jobs as jobs
from services.personal_recommendation import utcnow
from sqlalchemy.orm import Session

from model_gateway.auth import AuthenticatedUser
from routers.sync import get_cloud_db

router = APIRouter(
    prefix="/api/cloud/personal-recommendations", tags=["personal-recommendations"]
)


DB = Annotated[Session, Depends(get_cloud_db)]
User = Annotated[AuthenticatedUser, Depends(current_user)]


def worker_identity(db: DB, authorization: str = Header(default="")):
    if not authorization.startswith("Bearer krw_"):
        raise HTTPException(401, "需要推荐节点凭证")
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    worker = db.query(RecWorker).filter_by(token_hash=digest).one_or_none()
    if worker is None:
        raise HTTPException(401, "推荐节点凭证无效或已撤销")
    return worker


Worker = Annotated[RecWorker, Depends(worker_identity)]


@router.get("")
def personal_feed(db: DB, user: User):
    result = jobs.feed(db, user.user_id)
    db.commit()
    return result


class Focus(BaseModel):
    current_focus: str = Field(max_length=2000)


@router.put("/focus")
def focus(body: Focus, db: DB, user: User):
    profile = jobs.get_profile(db, user.user_id)
    profile.current_focus = body.current_focus.strip()
    db.flush()
    jobs.refresh_profile(db, user.user_id)
    db.commit()
    return {"updated": True}


@router.post("/refresh")
def refresh(db: DB, user: User):
    job = jobs.enqueue(db, user.user_id, manual=True)
    db.commit()
    return {
        "id": job.id if job else None,
        "status": job.status if job else "empty_library",
    }


@router.get("/profile/versions")
def versions(db: DB, user: User):
    batches = (
        db.query(RecBatch)
        .filter_by(user_id=user.user_id, status="completed")
        .order_by(RecBatch.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "versions": [
            {
                "batch_id": b.id,
                "version": b.snapshot.get("version"),
                "created_at": b.created_at,
            }
            for b in batches
        ]
    }


class Rollback(BaseModel):
    batch_id: str = Field(max_length=80)


@router.post("/profile/rollback")
def rollback(body: Rollback, db: DB, user: User):
    profile = jobs.get_profile(db, user.user_id)
    batch = (
        db.query(RecBatch)
        .filter_by(id=body.batch_id, user_id=user.user_id, status="completed")
        .one_or_none()
    )
    if not batch:
        raise HTTPException(404, "未找到该用户的历史画像版本")
    # Restore learned parameters, while respecting current library and manual focus.
    profile.feedback_weights = batch.snapshot.get("feedback", {})
    profile.feedback_count = (
        db.query(RecEvent).filter_by(user_id=user.user_id, kind="adopted").count()
    )
    profile.feedback_at = utcnow()
    db.flush()
    jobs.refresh_profile(db, user.user_id)
    db.commit()
    return {"version": profile.version}


class Event(BaseModel):
    batch_id: str = Field(max_length=80)
    arxiv_id: str = Field(max_length=80)
    kind: Literal["exposed", "viewed", "requested"]


@router.post("/events")
def event(body: Event, db: DB, user: User):
    try:
        jobs.record_event(db, user.user_id, body.batch_id, body.arxiv_id, body.kind)
        db.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"recorded": True}


class Register(BaseModel):
    node_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


@router.post("/workers")
def register(body: Register, db: DB, user: User):
    token = "krw_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    jobs.upsert(
        db,
        RecWorker,
        {
            "user_id": user.user_id,
            "node_id": body.node_id,
            "token_hash": digest,
            "health": "registered",
            "last_seen_at": None,
        },
        ("user_id", "node_id"),
        ("token_hash", "health", "last_seen_at"),
    )
    db.commit()
    return {"node_id": body.node_id, "token": token}


@router.delete("/workers/{node_id}")
def revoke(node_id: str, db: DB, user: User):
    db.query(RecWorker).filter_by(user_id=user.user_id, node_id=node_id).delete()
    db.commit()
    return {"revoked": True}


class Heartbeat(BaseModel):
    health: Literal["ready", "busy", "model_unavailable", "rate_limited", "failed"] = (
        "ready"
    )


@router.post("/worker/heartbeat")
def heartbeat(body: Heartbeat, db: DB, worker: Worker):
    worker.last_seen_at, worker.health = utcnow(), body.health
    db.commit()
    return {"ok": True}


@router.post("/worker/claim")
def claim(db: DB, worker: Worker):
    worker.last_seen_at = utcnow()
    result = jobs.claim(db, worker)
    db.commit()
    return {"job": result}


class Reservation(BaseModel):
    lease: str = Field(max_length=80)
    amount_cny: float = Field(ge=0, le=30, allow_inf_nan=False)
    provider: Literal["codex_cli", "api"]


@router.post("/worker/{job_id}/reserve")
def reserve(job_id: str, body: Reservation, db: DB, worker: Worker):
    try:
        usage = jobs.reserve_budget(
            db, worker, job_id, body.lease, body.amount_cny, body.provider
        )
        db.commit()
        return {"reserved_cny": usage.reserved_cny, "calls": usage.calls}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class Candidate(BaseModel):
    arxiv_id: str = Field(max_length=80)
    title: str = Field(max_length=500)
    abstract: str = Field(default="", max_length=12000)
    authors: list[str] = Field(default_factory=list, max_length=80)
    primary_category: Optional[str] = Field(default=None, max_length=100)
    published: Optional[str] = Field(default=None, max_length=80)


class Result(BaseModel):
    lease: str = Field(max_length=80)
    candidates: list[Candidate] = Field(max_length=600)
    ai_output: Optional[dict] = None
    note: Optional[
        Literal[
            "基础排序：未配置可核算的 API 价格",
            "基础排序：模型暂不可用",
            "基础排序：本月预算不足",
            "基础排序：无可用模型",
            "基础排序：已关闭 AI",
            "部分检索失败，使用已获取元数据",
        ]
    ] = None


@router.post("/worker/{job_id}/complete")
def complete(job_id: str, body: Result, db: DB, worker: Worker):
    try:
        job = jobs.complete(
            db,
            worker,
            job_id,
            body.lease,
            [c.model_dump() for c in body.candidates],
            body.ai_output,
            body.note,
        )
        db.commit()
        return {"id": job.id, "status": job.status, "count": len(job.items)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


class Failure(BaseModel):
    lease: str = Field(max_length=80)
    reason: Literal[
        "model_unavailable", "retrieval_failed", "invalid_output", "worker_error"
    ]


@router.post("/worker/{job_id}/fail")
def fail(job_id: str, body: Failure, db: DB, worker: Worker):
    try:
        job = jobs.require_lease(db, worker, job_id, body.lease)
        job.status = "queued" if job.attempts < jobs.MAX_ATTEMPTS else "failed"
        job.error = body.reason
        job.lease_token, job.lease_expires_at = None, None
        db.commit()
        return {"status": job.status}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
