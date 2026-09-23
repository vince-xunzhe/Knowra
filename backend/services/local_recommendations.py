"""Workspace-owned recommendations: persistent SQLite and the local library.

No cloud login, cloud database, or HTTP server is required by the worker.
Only the six recommendation tables live here; papers remain in the library DB.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import threading
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from cloud_models import (
    RecBatch,
    RecCandidate,
    RecEvent,
    RecProfile,
    RecUsage,
    RecWorker,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services import recommendation_jobs as jobs

LOCAL_USER = "local-workspace"
TABLES = [
    m.__table__
    for m in (RecProfile, RecCandidate, RecBatch, RecEvent, RecWorker, RecUsage)
]
PAPER_FIELDS = (
    "id",
    "filename",
    "title",
    "authors",
    "paper_category_model",
    "paper_category_override",
    "paper_team_model",
    "paper_team_override",
    "raw_llm_response",
    "created_at",
)


class LocalRecommendationStore:
    def __init__(self, path, library_factory):
        path = Path(path)
        self.worker_lock_path = path.with_suffix(path.suffix + ".worker.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        RecProfile.metadata.create_all(self.engine, tables=TABLES)
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        self.library_factory = library_factory

    def library_rows(self, user_id):
        if user_id != LOCAL_USER:
            raise ValueError("Local recommendations belong to this workspace")
        from models import KnowledgeNode, Paper

        with self.library_factory() as library:
            papers = [
                dict(zip(PAPER_FIELDS, row))
                for row in library.query(
                    *(getattr(Paper, name) for name in PAPER_FIELDS)
                ).all()
            ]
            fields = ("title", "node_type", "source_paper_ids")
            nodes = [
                dict(zip(fields, row))
                for row in library.query(
                    *(getattr(KnowledgeNode, name) for name in fields)
                )
                .filter(KnowledgeNode.hidden.is_(False))
                .all()
            ]
        return papers, nodes

    @contextmanager
    def session(self):
        with self.factory() as db:
            db.info["recommendation_library_loader"] = self.library_rows
            yield db


_store = None
_store_lock = threading.Lock()


def local_store():
    global _store
    with _store_lock:
        if _store is None:
            from database import SessionLocal
            from path_utils import DATA_DIR

            path = os.environ.get("KNOWRA_REC_DB") or str(
                DATA_DIR / "recommendations.db"
            )
            _store = LocalRecommendationStore(path, SessionLocal)
        return _store


def get_local_db():
    with local_store().session() as db:
        yield db


def acquire_worker_lock(store=None):
    """One worker per SQLite file, including multiple development backends."""
    handle = (store or local_store()).worker_lock_path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle  # The OS releases the lock even if its owner crashes.


def worker_is_running():
    handle = acquire_worker_lock()
    if handle is None:
        return True
    handle.close()
    return False


def local_user():
    from model_gateway.auth import AuthenticatedUser

    return AuthenticatedUser(user_id=LOCAL_USER, email=None, role="authenticated")


class LocalWorkerClient:
    """Same validated job protocol as a remote worker, without a network hop."""

    def __init__(self, store=None):
        self.store = store or local_store()
        with self.store.session() as db:
            jobs.upsert(
                db,
                RecWorker,
                {
                    "user_id": LOCAL_USER,
                    "node_id": "desktop-local",
                    "token_hash": hashlib.sha256(uuid4().bytes).hexdigest(),
                    "health": "ready",
                },
                ("user_id", "node_id"),
                ("token_hash", "health"),
            )
            db.commit()

    def post(self, path, body=None):
        from fastapi import HTTPException
        from routers import recommendations as api

        body = body or {}
        with self.store.session() as db:
            worker = (
                db.query(RecWorker)
                .filter_by(user_id=LOCAL_USER, node_id="desktop-local")
                .one()
            )
            try:
                if path == "/heartbeat":
                    return api.heartbeat(api.Heartbeat(**body), db, worker)
                if path == "/claim":
                    return api.claim(db, worker)
                job_id, action = path.strip("/").split("/")
                if action == "reserve":
                    return api.reserve(job_id, api.Reservation(**body), db, worker)
                if action == "complete":
                    return api.complete(job_id, api.Result(**body), db, worker)
                if action == "fail":
                    return api.fail(job_id, api.Failure(**body), db, worker)
                raise ValueError("Unknown recommendation action")
            except HTTPException as exc:
                raise urllib.error.HTTPError(
                    "local", exc.status_code, str(exc.detail), None, None
                ) from exc
