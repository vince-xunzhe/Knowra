"""Local-first behavior: no cloud auth or sync required for recommendation feedback."""

import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from auth_deps import current_user
from cloud_models import RecEvent, RecProfile
from models import Base, Paper
from routers.recommendation_workspace import create_workspace_app
from routers.sync import get_cloud_db
from services.local_recommendations import (
    LOCAL_USER,
    LocalRecommendationStore,
    LocalWorkerClient,
)
from services.personal_recommendation import utcnow


@pytest.fixture
def local(tmp_path):
    library_engine = create_engine(f"sqlite:///{tmp_path / 'library.db'}")
    Base.metadata.create_all(library_engine)
    factory = sessionmaker(bind=library_engine)
    with factory() as library:
        library.add(
            Paper(
                filename="arxiv_2401.00001.pdf",
                filepath="seed.pdf",
                file_hash="seed",
                title="Gaussian splatting reconstruction",
                created_at=utcnow() - timedelta(days=40),
                notes="PRIVATE NOTE",
                extracted_text="PRIVATE FULL TEXT",
            )
        )
        library.commit()
    store = LocalRecommendationStore(tmp_path / "recommendations.db", factory)
    yield store, factory, tmp_path
    store.engine.dispose()
    library_engine.dispose()


def client_for(store, address="127.0.0.1"):
    app = create_workspace_app()

    def dependency():
        with store.session() as db:
            yield db

    app.dependency_overrides[get_cloud_db] = dependency

    async def with_address(scope, receive, send):
        await app({**scope, "client": (address, 12345)}, receive, send)

    return TestClient(with_address)


def test_local_feed_works_without_cloud_login_and_uses_only_metadata(local):
    store, _, _ = local
    client = client_for(store)
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("No cloud calls allowed")
    ):
        response = client.get("/personal")
    assert response.status_code == 200
    assert response.json()["profile"]["paper_count"] == 1
    assert (
        client.put("/personal/focus", json={"current_focus": "robotics"}).status_code
        == 200
    )
    with store.session() as db:
        assert "PRIVATE" not in str(db.get(RecProfile, LOCAL_USER).snapshot)
    assert current_user not in FastAPI().dependency_overrides


def test_worker_and_adoption_complete_locally_without_sync(local):
    store, factory, _ = local
    client = client_for(store)
    worker = LocalWorkerClient(store)
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("No HTTP queue transport")
    ):
        worker.post("/heartbeat", {"health": "ready"})
        job = worker.post("/claim")["job"]
        assert job
        result = worker.post(
            f"/{job['id']}/complete",
            {
                "lease": job["lease"],
                "candidates": [
                    {
                        "arxiv_id": "2609.00001",
                        "title": "Efficient Gaussian splatting",
                        "abstract": "Gaussian splatting reconstruction",
                        "published": utcnow().isoformat(),
                        "authors": ["Alice"],
                    }
                ],
                "note": "基础排序：已关闭 AI",
            },
        )
    assert result["count"] == 1
    data = client.get("/personal").json()
    assert data["worker_status"] == "online"
    assert (
        client.post(
            "/personal/events",
            json={
                "batch_id": result["id"],
                "arxiv_id": "2609.00001",
                "kind": "requested",
            },
        ).status_code
        == 200
    )
    assert client.get("/personal").json()["metrics"]["adopted"] == 0
    with factory() as library:
        library.add(
            Paper(
                filename="arxiv_2609.00001.pdf",
                filepath="adopted.pdf",
                file_hash="new",
                title="Efficient Gaussian splatting",
            )
        )
        library.commit()
    data = client.get("/personal").json()
    assert data["metrics"]["adopted"] == 1 and data["items"] == []
    assert data["pending_imports"] == []
    client.get("/personal")
    with store.session() as db:
        assert db.query(RecEvent).filter_by(kind="adopted").count() == 1


def test_profile_and_job_survive_store_restart(local):
    store, factory, path = local
    client = client_for(store)
    client.put("/personal/focus", json={"current_focus": "persistent focus"})
    job = client.post("/personal/refresh").json()
    store.engine.dispose()
    recovered = LocalRecommendationStore(path / "recommendations.db", factory)
    try:
        data = client_for(recovered).get("/personal").json()
        assert data["profile"]["current_focus"] == "persistent focus"
        assert data["job"]["id"] == job["id"]
    finally:
        recovered.engine.dispose()


def test_local_api_is_not_exposed_to_lan_clients(local):
    store, _, _ = local
    app = create_workspace_app()
    client = client_for(store, address="192.168.1.88")
    assert client.get("/personal").status_code == 403
    assert app.dependency_overrides[current_user] is not current_user


def test_local_lifecycle_needs_neither_cloud_url_nor_worker_token(monkeypatch, local):
    from routers import recommendation_local as lifecycle

    store, _, _ = local
    monkeypatch.setattr("config.is_cloud_mode", lambda: False)
    monkeypatch.setattr(
        "services.local_recommendations.worker_is_running", lambda: False
    )
    monkeypatch.setattr(lifecycle, "_process", None)
    process = MagicMock(pid=1234)
    process.poll.return_value = None
    with (
        patch("services.local_recommendations.local_store", return_value=store),
        patch.object(lifecycle.subprocess, "Popen", return_value=process) as spawn,
    ):
        assert lifecycle.start_local() == {"running": True, "mode": "local"}
        assert lifecycle.start_local()["running"]
        spawn.assert_called_once()
        args = spawn.call_args.args[0]
        assert "--local" in args and "--url" not in args and "--provider" in args
        assert "env" not in spawn.call_args.kwargs


def test_worker_lock_prevents_duplicate_instances_and_releases(local):
    from services.local_recommendations import acquire_worker_lock

    store, _, _ = local
    first = acquire_worker_lock(store)
    assert first is not None
    try:
        assert acquire_worker_lock(store) is None
    finally:
        first.close()
    recovered = acquire_worker_lock(store)
    assert recovered is not None
    recovered.close()


def test_lifecycle_reports_external_local_worker_without_starting_another(monkeypatch):
    from fastapi import HTTPException
    from routers import recommendation_local as lifecycle

    monkeypatch.setattr("config.is_cloud_mode", lambda: False)
    monkeypatch.setattr(lifecycle, "_process", None)
    monkeypatch.setattr("services.local_recommendations.local_store", lambda: None)
    monkeypatch.setattr(
        "services.local_recommendations.worker_is_running", lambda: True
    )
    with patch.object(lifecycle.subprocess, "Popen") as spawn:
        assert lifecycle.start_local()["running"]
        assert lifecycle.status()["running"]
        spawn.assert_not_called()
        with pytest.raises(HTTPException) as exc:
            lifecycle.stop()
        assert exc.value.status_code == 409
        lifecycle.shutdown_worker()  # Other backends must not kill its owner.


def seed_history(store):
    from cloud_models import RecBatch

    now = utcnow()
    item = {
        "arxiv_id": "2609.00001",
        "title": "Historical Gaussian splatting",
        "abstract": "Original abstract",
        "authors": ["Alice"],
        "reason": "Original recommendation reason",
        "sources": [],
    }
    with store.session() as db:
        for index, (bid, status, user) in enumerate(
            [
                ("monday", "completed", LOCAL_USER),
                ("wednesday", "completed", LOCAL_USER),
                ("running", "running", LOCAL_USER),
                ("other-user", "completed", "another-user"),
            ]
        ):
            db.add(
                RecBatch(
                    id=bid,
                    user_id=user,
                    slot=bid,
                    status=status,
                    items=[{**item, "reason": f"{bid} reason"}],
                    snapshot={"version": index},
                    created_at=now - timedelta(days=4 - index),
                    completed_at=now - timedelta(days=4 - index)
                    if status == "completed"
                    else None,
                )
            )
        db.commit()


def test_history_preserves_old_batch_and_paginates_with_user_isolation(local):
    store, _, _ = local
    seed_history(store)
    client = client_for(store)
    latest = client.get("/personal").json()
    assert latest["batch"]["id"] == "wednesday"
    assert latest["job"]["status"] == "running"
    first = client.get("/personal/batches?limit=1").json()
    assert [b["id"] for b in first["batches"]] == ["wednesday"]
    assert first["next_offset"] == 1
    second = client.get("/personal/batches?limit=1&offset=1").json()
    assert [b["id"] for b in second["batches"]] == ["monday"]
    assert second["next_offset"] is None
    old = client.get("/personal?batch_id=monday").json()
    assert old["batch"]["id"] == "monday"
    assert old["latest_batch"]["id"] == "wednesday"
    assert old["items"][0]["reason"] == "monday reason"
    assert old["items"][0]["in_library"] is False
    for bid in ("missing", "running", "other-user"):
        assert client.get(f"/personal?batch_id={bid}").status_code == 404
    assert client.get("/personal/batches?offset=-1").status_code == 422
    assert client.get("/personal/batches?limit=101").status_code == 422


def test_history_adoption_remains_visible_and_feedback_survives(local):
    from cloud_models import RecBatch

    store, factory, _ = local
    seed_history(store)
    client = client_for(store)
    assert (
        client.post(
            "/personal/events",
            json={
                "batch_id": "monday",
                "arxiv_id": "2609.00001",
                "kind": "requested",
            },
        ).status_code
        == 200
    )
    with factory() as db:
        db.add(
            Paper(
                filename="arxiv_2609.00001.pdf",
                filepath="old.pdf",
                file_hash="old-recommendation",
                title="Historical Gaussian splatting",
            )
        )
        db.commit()
    old = client.get("/personal?batch_id=monday").json()
    assert old["items"][0]["in_library"] is True
    assert old["pending_imports"] == []
    assert old["metrics"]["adopted"] == 1
    assert client.get("/personal").json()["items"] == []
    assert (
        client.get("/personal?batch_id=wednesday").json()["items"][0]["in_library"]
        is True
    )
    with store.session() as db:
        assert db.get(RecBatch, "monday").items[0]["reason"] == "monday reason"
        assert "in_library" not in db.get(RecBatch, "monday").items[0]
        assert (
            db.query(RecEvent).filter_by(kind="adopted", batch_id="monday").count() == 1
        )


def test_empty_completed_batch_can_be_revisited(local):
    from cloud_models import RecBatch

    store, _, _ = local
    with store.session() as db:
        db.add(
            RecBatch(
                id="empty",
                user_id=LOCAL_USER,
                slot="empty",
                status="completed",
                completed_at=utcnow(),
                items=[],
            )
        )
        db.commit()
    client = client_for(store)
    assert client.get("/personal/batches").json()["batches"][0]["count"] == 0
    assert client.get("/personal?batch_id=empty").json()["items"] == []


def test_history_expiration_boundary_preserves_records_and_feedback(local):
    from cloud_models import RecBatch
    from services.recommendation_jobs import HISTORY_DAYS

    store, _, _ = local
    seed_history(store)
    client = client_for(store)
    assert (
        client.post(
            "/personal/events",
            json={
                "batch_id": "monday",
                "arxiv_id": "2609.00001",
                "kind": "viewed",
            },
        ).status_code
        == 200
    )
    now = utcnow()
    cutoff = now - timedelta(days=HISTORY_DAYS)
    with store.session() as db:
        db.get(RecBatch, "monday").completed_at = cutoff - timedelta(seconds=1)
        db.get(RecBatch, "wednesday").completed_at = cutoff
        db.commit()
    with patch("services.recommendation_jobs.utcnow", return_value=now):
        history = client.get("/personal/batches").json()
        assert history["retention_days"] == 90
        assert [batch["id"] for batch in history["batches"]] == ["wednesday"]
        assert client.get("/personal?batch_id=monday").status_code == 404
        assert client.get("/personal?batch_id=wednesday").status_code == 200
    with patch(
        "services.recommendation_jobs.utcnow", return_value=now + timedelta(seconds=1)
    ):
        assert client.get("/personal/batches").json()["batches"] == []
        assert client.get("/personal").json()["batch"] is None
    with store.session() as db:
        assert db.get(RecBatch, "monday").items[0]["reason"] == "monday reason"
        assert (
            db.query(RecEvent).filter_by(batch_id="monday", kind="viewed").count() == 1
        )
    assert client.get("/personal").json()["profile"]["paper_count"] == 1


def test_local_storage_cleanup_reclaims_cache_and_preserves_library_and_feedback(local):
    from cloud_models import RecBatch, RecCandidate, RecUsage

    store, factory, path = local
    seed_history(store)
    client = client_for(store)
    assert (
        client.post(
            "/personal/events",
            json={
                "batch_id": "monday",
                "arxiv_id": "2609.00001",
                "kind": "requested",
            },
        ).status_code
        == 200
    )
    old = utcnow() - timedelta(days=100)
    with store.session() as db:
        for bid in ("monday", "wednesday", "running", "other-user"):
            batch = db.get(RecBatch, bid)
            batch.created_at = old
            if batch.status == "completed":
                batch.completed_at = old
        db.add(
            RecBatch(
                id="recent",
                user_id=LOCAL_USER,
                slot="recent",
                status="completed",
                completed_at=utcnow(),
                items=[],
            )
        )
        db.add(
            RecCandidate(
                arxiv_id="stale",
                content_hash="old",
                updated_at=old,
                metadata_json={"abstract": "long obsolete abstract " * 10000},
            )
        )
        db.add(
            RecCandidate(arxiv_id="fresh", content_hash="fresh", updated_at=utcnow())
        )
        db.add(
            RecUsage(
                user_id=LOCAL_USER, batch_id="wednesday", provider="cli", reserved_cny=1
            )
        )
        db.commit()
    pdf = path / "library-paper.pdf"
    pdf.write_bytes(b"Existing user paper")
    with patch("routers.recommendation_workspace.local_store", return_value=store):
        preview = client.get("/personal/storage").json()
        assert preview["expired_batches"] == 1
        assert preview["expired_candidates"] == 1
        assert preview["protected_batches"] == 1
        assert preview["estimated_payload_bytes"] > 100000
        result = client.post("/personal/storage/cleanup").json()
        assert result["deleted_batches"] == 1
        assert result["deleted_candidates"] == 1
        assert result["compacted"] is True
        assert result["reclaimed_bytes"] > 0
        again = client.post("/personal/storage/cleanup").json()
        assert again["deleted_batches"] == again["deleted_candidates"] == 0
    with store.session() as db:
        assert db.get(RecBatch, "running") is not None
        assert db.get(RecBatch, "recent") is not None
        assert db.get(RecBatch, "monday") is not None
        assert db.get(RecBatch, "wednesday") is None
        assert db.get(RecBatch, "other-user") is not None
        assert db.get(RecCandidate, "fresh") is not None
        assert db.query(RecUsage).count() == 1
        assert db.query(RecEvent).count() == 1
    assert pdf.read_bytes() == b"Existing user paper"
    # An old pending import still reconciles after its batch leaves the review window.
    with factory() as db:
        assert db.query(Paper).count() == 1
        db.add(
            Paper(
                filename="arxiv_2609.00001.pdf",
                filepath=str(pdf),
                file_hash="late",
                title="Historical Gaussian splatting",
            )
        )
        db.commit()
    assert client.get("/personal").json()["metrics"]["adopted"] == 1
    assert client.get("/personal").json()["pending_imports"] == []


def test_storage_cleanup_is_loopback_only(local):
    store, _, _ = local
    client = client_for(store, address="192.168.1.88")
    assert client.get("/personal/storage").status_code == 403
    assert client.post("/personal/storage/cleanup").status_code == 403
