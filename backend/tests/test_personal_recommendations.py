import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from auth_deps import current_user
from cloud_models import (
    CloudPaper,
    RecBatch,
    RecEvent,
    RecUsage,
    RecWorker,
    init_cloud_schema,
)
from routers.recommendations import router
from routers.sync import get_cloud_db
from services import recommendation_jobs as jobs
from services.personal_recommendation import (
    apply_ai,
    base_id,
    build_profile,
    rank_candidates,
    select_diverse,
)

from model_gateway.auth import AuthenticatedUser
from model_gateway.config import ensure_model_gateway_config
from model_gateway.recommendations import infer, price_upper_bound, resolve_route

NOW = datetime(2026, 9, 23, 8, tzinfo=timezone.utc)


def paper(**extra):
    return {
        "id": "seed",
        "title": "Gaussian splatting reconstruction",
        "authors": ["Alice"],
        "filename": "arxiv_2401.00001.pdf",
        "paper_category_model": "reconstruction",
        "paper_team_model": "lab-a",
        "created_at": NOW.isoformat(),
        **extra,
    }


def candidates(n=12, historical=False):
    return [
        {
            "arxiv_id": f"2609.{i:05d}",
            "title": f"Gaussian splatting reconstruction technique {i}",
            "abstract": "We study efficient Gaussian splatting reconstruction with generalization.",
            "authors": [f"Author {i}"],
            "published": (NOW - timedelta(days=100 if historical else 1)).isoformat(),
            "primary_category": "cs.CV",
        }
        for i in range(n)
    ]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_cloud_schema(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()
    engine.dispose()


def seed(db, user="alice"):
    row = CloudPaper(
        id=user + "-seed",
        user_id=user,
        filename="arxiv_2401.00001.pdf",
        filepath="seed.pdf",
        file_hash=user + "-hash",
        title="Gaussian splatting reconstruction",
        authors=["Alice"],
        created_at=NOW - timedelta(days=40),
    )
    db.add(row)
    token = "krw_test-" + user
    worker = RecWorker(
        user_id=user,
        node_id="node",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    db.add(worker)
    db.commit()
    return worker, token


def finished(db, worker):
    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        db.commit()
        jobs.complete(db, worker, claim["id"], claim["lease"], candidates())
        db.commit()
        return db.get(RecBatch, claim["id"])


def test_canonical_ids_cover_versions_urls_and_legacy_filenames():
    assert base_id("https://arxiv.org/abs/2609.12345v3") == "2609.12345"
    assert base_id("arxiv_hep-th_9701001.pdf") == "hep-th/9701001"
    assert base_id("not-an-id") is None


def test_equal_paper_weight_and_deduplicated_nodes():
    p = paper(raw_llm_response=json.dumps({"tags": ["splatting"] * 30}))
    profile = build_profile(
        [p, paper(id="b", title="Robotics control", paper_team_model=None)], now=NOW
    )
    assert sum(profile["long_term"].values()) == pytest.approx(2)
    assert profile["paper_count"] == 2


def test_manual_focus_overrides_recent_but_preserves_long_term():
    profile = build_profile([paper()], focus="robotics planning", now=NOW)
    assert set(profile["recent"]) == {"robotics", "planning"}
    assert "splatting" in profile["long_term"]


def test_private_notes_and_full_text_never_enter_profile():
    profile = build_profile(
        [
            paper(
                notes="SECRETNOTES",
                extracted_text="SECRETFULLTEXT",
                chat_history="SECRETCHAT",
            )
        ],
        now=NOW,
    )
    assert "SECRET" not in json.dumps(profile)


def test_unknown_team_and_dataset_are_not_invented():
    profile = build_profile([paper(paper_team_model=None)], now=NOW)
    assert profile["dimensions"]["team"] == {}
    assert profile["dimensions"]["dataset"] == {}


def test_rank_deduplicates_versions_excludes_library_and_unrelated():
    profile = build_profile([paper()], now=NOW)
    rows = candidates(2)
    rows += [
        {**rows[0], "arxiv_id": rows[0]["arxiv_id"] + "v3"},
        {**rows[0], "arxiv_id": "2401.00001"},
        {
            **rows[0],
            "arxiv_id": "2601.54321",
            "title": "Cooking bread",
            "abstract": "Bread recipes",
        },
    ]
    ranked = rank_candidates(profile, rows, now=NOW)
    assert {r["arxiv_id"] for r in ranked} == {"2609.00000", "2609.00001"}


def test_history_is_capped_and_total_is_bounded():
    profile = build_profile([paper()], now=NOW)
    historical = rank_candidates(profile, candidates(12, historical=True), now=NOW)
    assert len(select_diverse(historical)) == 2
    assert len(select_diverse(rank_candidates(profile, candidates(20), now=NOW))) == 10


def test_ai_cannot_add_ids_or_hallucinate_evidence():
    rows = rank_candidates(build_profile([paper()], now=NOW), candidates(1), now=NOW)
    item = {
        "arxiv_id": rows[0]["arxiv_id"],
        "relevance": 0.8,
        "reason": "与你的三维重建兴趣有关",
        "evidence": "Gaussian splatting",
    }
    assert apply_ai(rows, {"items": [item]})[0]["ai"]
    for change in (
        {"arxiv_id": "9999.99999"},
        {"relevance": float("nan")},
        {"evidence": "invented dataset"},
    ):
        with pytest.raises(ValueError):
            apply_ai(rows, {"items": [{**item, **change}]})


def test_schedule_uses_shanghai_nine_am_and_catches_up():
    assert jobs.scheduled_slot(
        datetime(2026, 9, 23, 0, tzinfo=timezone.utc)
    ).startswith("2026-09-21T09")
    assert jobs.scheduled_slot(NOW).startswith("2026-09-23T09")
    assert jobs.scheduled_slot(NOW + timedelta(days=1)).startswith("2026-09-23T09")


def test_job_idempotency_and_no_double_claim(db):
    worker, _ = seed(db)
    a = jobs.enqueue(db, "alice", now=NOW)
    b = jobs.enqueue(db, "alice", now=NOW)
    assert a.id == b.id
    first = jobs.claim(db, worker, NOW)
    db.commit()
    assert first
    assert jobs.claim(db, worker, NOW) is None


def test_expired_lease_is_reclaimed_and_late_result_rejected(db):
    worker, _ = seed(db)
    first = jobs.claim(db, worker, NOW)
    db.commit()
    second = jobs.claim(db, worker, NOW + timedelta(minutes=16))
    db.commit()
    assert first["id"] == second["id"] and first["lease"] != second["lease"]
    with pytest.raises(ValueError):
        jobs.require_lease(
            db, worker, first["id"], first["lease"], NOW + timedelta(minutes=17)
        )


def test_worker_cannot_complete_another_users_job(db):
    alice, _ = seed(db)
    bob, _ = seed(db, "bob")
    first = jobs.claim(db, alice, NOW)
    db.commit()
    with pytest.raises(ValueError):
        jobs.require_lease(db, bob, first["id"], first["lease"], NOW)


def test_download_and_request_do_not_count_as_adoption(db):
    worker, _ = seed(db)
    batch = finished(db, worker)
    aid = batch.items[0]["arxiv_id"]
    jobs.record_event(db, "alice", batch.id, aid, "requested", now=NOW)
    jobs.reconcile_adoptions(db, "alice", NOW)
    assert db.query(RecEvent).filter_by(kind="adopted").count() == 0
    db.add(
        CloudPaper(
            user_id="alice",
            filename=f"arxiv_{aid}v2.pdf",
            filepath="new.pdf",
            file_hash="new",
            title=batch.items[0]["title"],
            created_at=NOW + timedelta(hours=1),
        )
    )
    db.flush()
    jobs.reconcile_adoptions(db, "alice", NOW + timedelta(hours=2))
    jobs.reconcile_adoptions(db, "alice", NOW + timedelta(hours=2))
    assert db.query(RecEvent).filter_by(kind="adopted").count() == 1


def test_unviewed_is_not_negative_and_feedback_waits_for_three_positives(db):
    worker, _ = seed(db)
    batch = finished(db, worker)
    for item in batch.items[:2]:
        jobs.record_event(db, "alice", batch.id, item["arxiv_id"], "viewed", now=NOW)
    p = jobs.refresh_profile(db, "alice", NOW)
    assert p.feedback_weights == {} and p.feedback_count == 0
    for item in batch.items[:3]:
        jobs.record_event(db, "alice", batch.id, item["arxiv_id"], "adopted", now=NOW)
    p = jobs.refresh_profile(db, "alice", NOW)
    assert p.feedback_count == 3 and p.feedback_weights


def test_metrics_wait_fourteen_days_and_accept_delayed_adoption(db):
    worker, _ = seed(db)
    batch = finished(db, worker)
    aid = batch.items[0]["arxiv_id"]
    jobs.record_event(db, "alice", batch.id, aid, "viewed", now=NOW)
    jobs.record_event(
        db, "alice", batch.id, aid, "adopted", now=NOW + timedelta(days=3)
    )
    assert (
        jobs.metrics(db, "alice", NOW + timedelta(days=4))["adoption_rate_14d"] is None
    )
    assert jobs.metrics(db, "alice", NOW + timedelta(days=15))["adoption_rate_14d"] == 1


def test_worker_offline_is_visible_and_last_result_retained(db):
    worker, _ = seed(db)
    batch = finished(db, worker)
    worker.last_seen_at = NOW - timedelta(minutes=6)
    db.commit()
    with patch.object(jobs, "utcnow", return_value=NOW):
        data = jobs.feed(db, "alice")
    assert data["worker_status"] == "offline"
    assert data["batch"]["id"] == batch.id and data["items"]


def test_budget_reservations_cannot_exceed_monthly_limit(db):
    worker, _ = seed(db)
    with patch.object(jobs, "utcnow", return_value=NOW):
        job = jobs.claim(db, worker, NOW)
        db.commit()
        jobs.reserve_budget(db, worker, job["id"], job["lease"], 20, "api")
        db.commit()
        with pytest.raises(ValueError):
            jobs.reserve_budget(db, worker, job["id"], job["lease"], 11, "api")
        assert db.query(RecUsage).one().cost_cny is None


def test_explicit_api_prices_required():
    with pytest.raises(ValueError):
        price_upper_bound("example", 0, 0)
    assert price_upper_bound("example", 10, 30) > 0.18


def test_model_binding_inherits_configured_cli_model():
    cfg = ensure_model_gateway_config(
        {"model_gateway": {"task_bindings": {"wiki_compile": "codex-cli/gpt-5.5"}}}
    )
    assert (
        cfg["model_gateway"]["task_bindings"]["recommend_rank"]["model_id"]
        == "codex-cli/gpt-5.5"
    )


def test_cli_default_does_not_fallback_to_paid_api():
    cfg = {"model_gateway": {"task_bindings": {"recommend_rank": "openai/gpt-4o"}}}
    with pytest.raises(Exception, match="不会自动切换"):
        resolve_route(cfg)


def test_cli_is_tool_restricted_and_schema_constrained(tmp_path):
    cfg = {
        "model_gateway": {
            "providers": [
                {"id": "codex-cli", "provider_type": "codex_cli", "enabled": True}
            ]
        }
    }

    def run(args, **kwargs):
        if args[1:] == ["login", "status"]:
            return type(
                "Completed",
                (),
                {"returncode": 0, "stdout": "Logged in using ChatGPT", "stderr": ""},
            )()
        Path(args[args.index("--output-last-message") + 1]).write_text('{"items": []}')
        assert kwargs["cwd"] != str(ROOT)
        assert "--ignore-user-config" in args and "--output-schema" in args
        assert "shell_tool" in args and "plugins" in args and "apps" in args
        return type("Completed", (), {"returncode": 0})()

    with patch("model_gateway.recommendations.subprocess.run", side_effect=run):
        assert infer(cfg, {}, []) == {"items": []}


def test_http_contract_enforces_tenant_and_adoption_boundary(db):
    _, token = seed(db)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_cloud_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        user_id="alice", email="alice@example.test", role="authenticated"
    )
    client = TestClient(app)
    root = "/api/cloud/personal-recommendations"
    assert (
        client.post(
            root + "/events",
            json={"batch_id": "x", "arxiv_id": "2609.00001", "kind": "adopted"},
        ).status_code
        == 422
    )
    assert client.post(root + "/worker/claim").status_code == 401
    response = client.post(
        root + "/worker/heartbeat",
        headers={"Authorization": "Bearer " + token},
        json={"health": "ready"},
    )
    assert response.status_code == 200
    assert client.get(root).json()["worker_status"] == "online"
    client.delete(root + "/workers/node")
    assert (
        client.post(
            root + "/worker/claim", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_api_text_path_has_no_tools_and_reserves_before_call():
    cfg = {"model_gateway": {"task_bindings": {"recommend_rank": "openai/gpt-4o"}}}
    from unittest.mock import MagicMock

    client = MagicMock()
    client.with_options.return_value = client
    client.responses.create.return_value.output_text = '{"items": []}'
    order = []
    client.responses.create.side_effect = lambda **kw: (
        order.append("call") or type("Response", (), {"output_text": '{"items": []}'})()
    )
    with patch(
        "model_gateway.recommendations.create_openai_client_for_model",
        return_value=(client, "gpt-4o", {}, {}),
    ):
        infer(
            cfg,
            {},
            [],
            provider_mode="api",
            reserve=lambda *_: order.append("reserve"),
            input_rate=1,
            output_rate=2,
        )
    assert order == ["reserve", "call"]
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["max_output_tokens"] == 9000 and "tools" not in kwargs


def test_cli_api_key_login_cannot_bypass_paid_budget():
    cfg = {
        "model_gateway": {
            "providers": [
                {"id": "codex-cli", "provider_type": "codex_cli", "enabled": True}
            ]
        }
    }
    response = type(
        "Completed",
        (),
        {"returncode": 0, "stdout": "Logged in using an API key", "stderr": ""},
    )()
    with patch(
        "model_gateway.recommendations.subprocess.run", return_value=response
    ) as run:
        with pytest.raises(Exception, match="API"):
            infer(cfg, {}, [])
        assert run.call_count == 1


def test_retrieval_pages_metadata_and_recovers_from_cache():
    from scripts.recommendation_worker import fetch_candidates

    profile = build_profile([paper()], now=NOW)
    profile["as_of"] = NOW.isoformat()
    calls = []

    def search(query, **kwargs):
        calls.append(kwargs)
        return [
            {**c, "full_text": "DO NOT SEND"}
            for c in candidates(40 if kwargs["max_results"] == 40 else 1)
        ]

    rows, failures = fetch_candidates(
        {"snapshot": profile}, search=search, pause=lambda _: None
    )
    assert not failures and rows and any(c["start"] == 40 for c in calls)
    assert all("full_text" not in r and "pdf_url" not in r for r in rows)

    def offline(*args, **kwargs):
        raise OSError("offline")

    recovered, failures = fetch_candidates(
        {"snapshot": profile, "candidates": candidates(1)},
        search=offline,
        pause=lambda _: None,
    )
    assert recovered and failures


def test_ai_feature_evidence_and_cache_invalidation(db):
    from cloud_models import RecCandidate

    worker, _ = seed(db)
    with patch.object(jobs, "utcnow", return_value=NOW):
        first = jobs.claim(db, worker, NOW)
        db.commit()
        output = {
            "items": [
                {
                    "arxiv_id": "2609.00000",
                    "relevance": 0.9,
                    "reason": "匹配重建研究",
                    "evidence": "Gaussian splatting",
                    "features": [
                        {
                            "dimension": "method",
                            "label": "Gaussian splatting",
                            "evidence": "Gaussian splatting",
                        }
                    ],
                }
            ]
        }
        jobs.complete(db, worker, first["id"], first["lease"], candidates(1), output)
        db.commit()
        cached = db.get(RecCandidate, "2609.00000")
        assert cached.features["dimensions"]["method"] == ["Gaussian splatting"]
        jobs.enqueue(db, "alice", manual=True, now=NOW)
        second = jobs.claim(db, worker, NOW)
        db.commit()
        changed = [
            {
                **candidates(1)[0],
                "abstract": "Gaussian splatting reconstruction. Revised abstract.",
            }
        ]
        jobs.complete(db, worker, second["id"], second["lease"], changed)
        db.commit()
        assert db.get(RecCandidate, "2609.00000").features == {}
    ranked = rank_candidates(build_profile([paper()], now=NOW), candidates(1), now=NOW)
    output["items"][0]["features"][0]["evidence"] = "invented dataset"
    with pytest.raises(ValueError, match="依据"):
        apply_ai(ranked, output)


def test_completion_freezes_inputs_but_rechecks_new_imports(db):
    worker, _ = seed(db)
    with patch.object(jobs, "utcnow", return_value=NOW):
        first = jobs.claim(db, worker, NOW)
        db.commit()
        output = {
            "items": [
                {
                    "arxiv_id": c["arxiv_id"],
                    "relevance": 0.9,
                    "reason": "相关",
                    "evidence": "Gaussian splatting",
                }
                for c in candidates(2)
            ]
        }
        db.add(
            CloudPaper(
                user_id="alice",
                filename="arxiv_2609.00000.pdf",
                filepath="new.pdf",
                file_hash="new",
                title=candidates(2)[0]["title"],
                created_at=NOW,
            )
        )
        db.commit()
        result = jobs.complete(
            db, worker, first["id"], first["lease"], candidates(2), output
        )
        assert [i["arxiv_id"] for i in result.items] == ["2609.00001"]


def test_local_worker_lifecycle_and_invalid_origins(monkeypatch):
    from unittest.mock import MagicMock

    from routers import recommendation_local as local

    monkeypatch.setattr("config.is_cloud_mode", lambda: False)
    monkeypatch.setattr(
        "services.local_recommendations.worker_is_running", lambda: False
    )
    monkeypatch.setattr(local, "_process", None)
    process = MagicMock(pid=12345)
    process.poll.return_value = None
    app = FastAPI()
    app.include_router(local.router)
    client = TestClient(app)
    root = "/api/recommendations/worker"
    token = "krw_" + "x" * 32
    with (
        patch.object(local.subprocess, "Popen", return_value=process) as spawn,
        patch.object(local.os, "killpg") as kill,
    ):
        assert (
            client.post(
                root + "/start",
                json={"url": "http://untrusted.example", "token": token},
            ).status_code
            == 400
        )
        response = client.post(
            root + "/start", json={"url": "https://cloud.example", "token": token}
        )
        assert response.status_code == 200 and token not in response.text
        assert spawn.call_args.kwargs["env"]["KNOWRA_REC_WORKER_TOKEN"] == token
        assert token not in str(spawn.call_args.args)
        assert client.get(root).json()["running"]
        assert (
            client.post(
                root + "/start", json={"url": "https://cloud.example", "token": token}
            ).status_code
            == 409
        )
        assert client.post(root + "/stop").status_code == 200
        kill.assert_called_once()


def test_import_creates_real_paper_and_is_idempotent(tmp_path, monkeypatch):
    from models import Base, Paper
    from routers import papers as routes

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    filename = "arxiv_2609.00001.pdf"
    (tmp_path / filename).write_bytes(b"fake-pdf-for-import-contract")
    monkeypatch.setattr("config.is_cloud_mode", lambda: False)
    monkeypatch.setattr(
        routes, "load_config", lambda: {"scan_directory": str(tmp_path)}
    )
    monkeypatch.setattr(
        routes, "download_recommendation", lambda *args: {"filename": filename}
    )
    body = routes.RecImportInput(arxiv_id="2609.00001v2", title="Gaussian splatting")
    try:
        first = routes.import_recommendation(body, session)
        second = routes.import_recommendation(body, session)
        assert (
            first["paper_id"] == second["paper_id"]
            and session.query(Paper).count() == 1
        )
        assert not session.query(Paper).one().processed
    finally:
        session.close()
        engine.dispose()


def test_rollback_uses_only_owned_completed_snapshot(db):
    alice, _ = seed(db)
    bob, _ = seed(db, "bob")
    own, other = finished(db, alice), finished(db, bob)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_cloud_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        user_id="alice", email="alice@example.test", role="authenticated"
    )
    client = TestClient(app)
    path = "/api/cloud/personal-recommendations/profile"
    profile = jobs.get_profile(db, "alice")
    profile.feedback_weights = {"wrong-term": 1}
    db.commit()
    assert (
        client.post(path + "/rollback", json={"batch_id": other.id}).status_code == 404
    )
    assert client.post(path + "/rollback", json={"batch_id": own.id}).status_code == 200
    assert jobs.get_profile(db, "alice").feedback_weights == {}
    assert all(
        v["batch_id"] != other.id
        for v in client.get(path + "/versions").json()["versions"]
    )


def test_ai_rejects_irrelevant_candidate_and_private_labels():
    ranked = rank_candidates(build_profile([paper()], now=NOW), candidates(1), now=NOW)
    result = {
        "arxiv_id": "2609.00000",
        "relevance": 0,
        "reason": "主题不符",
        "evidence": "Gaussian splatting",
        "features": [],
    }
    assert apply_ai(ranked, {"items": [result]}) == []
    result.update(
        relevance=0.8,
        features=[
            {
                "dimension": "team",
                "label": "PRIVATE USER LABEL",
                "evidence": "Gaussian splatting",
            }
        ],
    )
    with pytest.raises(ValueError, match="依据"):
        apply_ai(ranked, {"items": [result]})


def test_exposure_refresh_keeps_first_seen_for_delayed_sync(db):
    worker, _ = seed(db)
    batch = finished(db, worker)
    aid = batch.items[0]["arxiv_id"]
    jobs.record_event(db, "alice", batch.id, aid, "exposed", now=NOW)
    db.commit()
    jobs.record_event(
        db, "alice", batch.id, aid, "exposed", now=NOW + timedelta(days=3)
    )
    db.commit()
    db.add(
        CloudPaper(
            user_id="alice",
            filename=f"arxiv_{aid}.pdf",
            filepath="new.pdf",
            file_hash="new",
            title=batch.items[0]["title"],
            created_at=NOW + timedelta(days=1),
        )
    )
    db.flush()
    jobs.reconcile_adoptions(db, "alice")
    assert db.query(RecEvent).filter_by(kind="adopted").count() == 1


def test_retry_crossing_month_keeps_reservations_in_current_cap(db):
    worker, _ = seed(db)
    before = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=2)
    with patch.object(jobs, "utcnow", return_value=before):
        job = jobs.claim(db, worker, before)
        db.commit()
        usage = jobs.reserve_budget(db, worker, job["id"], job["lease"], 20, "api")
        usage.created_at = before
        db.commit()
    with patch.object(jobs, "utcnow", return_value=after):
        with pytest.raises(ValueError, match="预算"):
            jobs.reserve_budget(db, worker, job["id"], job["lease"], 11, "api")
        jobs.reserve_budget(db, worker, job["id"], job["lease"], 5, "api")
        db.commit()
        assert db.query(RecUsage).one().reserved_cny == 25
        assert jobs.aware(db.query(RecUsage).one().created_at) == after


def test_arxiv_filename_does_not_create_spurious_interests():
    profile = build_profile(
        [
            paper(
                title="1711.00937v2.pdf",
                filename="1711.00937v2.pdf",
                paper_category_model=None,
                paper_team_model=None,
            )
        ],
        now=NOW,
    )
    assert profile["paper_count"] == 1
    assert not profile["long_term"] and not profile["recent"]
    assert profile["library_ids"] == ["1711.00937"]


def test_bounded_ai_shortlist_matches_worker_and_publisher(db):
    from scripts.recommendation_worker import process

    worker, _ = seed(db)
    rows = candidates(40)
    seen = []

    def model(cfg, profile, shortlist, **kwargs):
        seen.extend(c["arxiv_id"] for c in shortlist)
        return {
            "items": [
                {
                    "arxiv_id": c["arxiv_id"],
                    "relevance": 0.9,
                    "reason": "相关",
                    "evidence": "Gaussian splatting",
                    "features": [],
                }
                for c in shortlist
            ]
        }

    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        db.commit()
        with patch("scripts.recommendation_worker.infer", side_effect=model):
            result = process(claim, {}, candidates=rows)
        assert len(seen) == 12 and result["note"] is None
        published = jobs.complete(
            db, worker, claim["id"], claim["lease"], rows, result["ai_output"]
        )
        assert len(published.items) == 10
        assert all(c["ai"] and c["arxiv_id"] in seen for c in published.items)


def test_explanation_refresh_preserves_selection_scores_and_order():
    from services.personal_recommendation import replace_explanations

    original = rank_candidates(
        build_profile([paper()], now=NOW), candidates(2), now=NOW
    )
    output = {
        "items": [
            {
                "arxiv_id": item["arxiv_id"],
                "relevance": 0.01,
                "reason": "这篇工作研究重建中的泛化方法。它与你的高斯表示兴趣相关。值得关注其效率与泛化之间的取舍。",
                "evidence": "Gaussian splatting",
                "features": [],
            }
            for item in reversed(original)
        ]
    }
    updated = replace_explanations(original, output)
    assert len(updated) == len(original)
    for before, after in zip(original, updated):
        assert before["reason"] != after["reason"]
        assert {k: v for k, v in before.items() if k not in {"reason", "evidence"}} == {
            k: v for k, v in after.items() if k not in {"reason", "evidence"}
        }
    output["items"][0]["evidence"] = "unsupported claim"
    with pytest.raises(ValueError, match="原文"):
        replace_explanations(original, output)


def valid_group(group):
    return {
        "items": [
            {
                "arxiv_id": row["arxiv_id"],
                "relevance": 0.9,
                "reason": "与研究方向相关",
                "evidence": "Gaussian splatting",
                "features": [],
            }
            for row in group
        ]
    }


def test_group_timeout_keeps_successes_and_publishes_mixed_cards(db):
    import subprocess

    from routers.recommendations import Result
    from scripts.recommendation_worker import process

    worker, _ = seed(db)
    groups = []

    def model(cfg, profile, group, **kwargs):
        groups.append(group)
        assert len(group) <= 3
        assert kwargs["timeout"] <= 240
        if len(groups) == 2:
            raise subprocess.TimeoutExpired("codex", 240, stderr="SECRET-NEVER-LOG")
        return valid_group(group)

    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        with patch("scripts.recommendation_worker.infer", side_effect=model):
            result = process(claim, {}, candidates=candidates(40))
        assert len(groups) == 4
        assert "9/12" in result["note"] and "超时" in result["note"]
        assert "SECRET" not in result["note"]
        assert len(result["ai_output"]["fallback_ids"]) == 3
        body = Result(**result)
        published = jobs.complete(
            db,
            worker,
            claim["id"],
            claim["lease"],
            candidates(40),
            body.ai_output,
            body.note,
        )
        assert sum(item["ai"] for item in published.items) == 9
        assert len(published.items) == 10
        assert any(not item["ai"] for item in published.items)


def test_bad_evidence_only_invalidates_its_group(db):
    from scripts.recommendation_worker import process
    from services.personal_recommendation import ai_shortlist, apply_ai_batches

    worker, _ = seed(db)
    groups = []

    def model(cfg, profile, group, **kwargs):
        groups.append(group)
        output = valid_group(group)
        if len(groups) == 1:
            output["items"][0]["evidence"] = "Fabricated evidence"
        return output

    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        with patch("scripts.recommendation_worker.infer", side_effect=model):
            result = process(claim, {}, candidates=candidates())
        assert "校验未通过" in result["note"] and len(groups) == 4
        shortlist = ai_shortlist(
            rank_candidates(claim["snapshot"], candidates(), now=NOW)
        )
        combined = apply_ai_batches(shortlist, result["ai_output"])
        assert sum(item["ai"] for item in combined) == 9
        malformed = {**result["ai_output"], "fallback_ids": ["unknown"]}
        with pytest.raises(ValueError):
            apply_ai_batches(shortlist, malformed)
        malformed = {
            **result["ai_output"],
            "fallback_ids": result["ai_output"]["fallback_ids"] * 2,
        }
        with pytest.raises(ValueError):
            apply_ai_batches(shortlist, malformed)
        with pytest.raises(ValueError, match="缺少"):
            apply_ai_batches(shortlist, {"items": [], "fallback_ids": []})


def test_overall_deadline_preserves_first_group_and_stops_calls(db):
    from scripts.recommendation_worker import process

    worker, _ = seed(db)
    clock = [0.0]

    def model(cfg, profile, group, **kwargs):
        clock[0] = 661
        return valid_group(group)

    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        with (
            patch(
                "scripts.recommendation_worker.time.monotonic",
                side_effect=lambda: clock[0],
            ),
            patch("scripts.recommendation_worker.infer", side_effect=model) as run,
        ):
            result = process(claim, {}, candidates=candidates())
        assert run.call_count == 1
        assert "3/12" in result["note"] and "时间已达上限" in result["note"]
        assert len(result["ai_output"]["fallback_ids"]) == 9


@pytest.mark.parametrize(
    "code, expected", [("auth", "登录"), ("quota", "额度"), ("config", "配置")]
)
def test_non_retryable_model_failures_stop_further_groups(db, code, expected):
    from scripts.recommendation_worker import process

    from model_gateway.recommendations import RecommendationInferenceError

    worker, _ = seed(db)
    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        with patch(
            "scripts.recommendation_worker.infer",
            side_effect=RecommendationInferenceError(code, "private diagnostic"),
        ) as run:
            result = process(claim, {}, candidates=candidates())
        assert run.call_count == 1
        assert result["ai_output"] is None
        assert expected in result["note"] and "private" not in result["note"]


def test_four_groups_can_reserve_but_total_call_limit_still_applies(db):
    worker, _ = seed(db)
    with patch.object(jobs, "utcnow", return_value=NOW):
        claim = jobs.claim(db, worker, NOW)
        for _ in range(jobs.MAX_MODEL_CALLS):
            usage = jobs.reserve_budget(
                db, worker, claim["id"], claim["lease"], 0, "codex_cli"
            )
            db.flush()
        assert usage.calls == 12
        with pytest.raises(ValueError, match="次数已达上限"):
            jobs.reserve_budget(db, worker, claim["id"], claim["lease"], 0, "codex_cli")


def test_cli_timeout_and_stderr_have_safe_error_codes():
    import subprocess

    from model_gateway.recommendations import (
        RecommendationInferenceError,
        cli_failure,
        run_cli,
    )

    with patch(
        "model_gateway.recommendations.subprocess.run",
        side_effect=subprocess.TimeoutExpired("codex", 240, stderr="secret"),
    ), pytest.raises(RecommendationInferenceError) as error:
        run_cli(["codex"], timeout=240)
    assert error.value.code == "timeout" and "secret" not in str(error.value)
    assert cli_failure("401 authentication failed secret").code == "auth"
    assert cli_failure("429 rate limit secret").code == "quota"
    assert cli_failure("unexpected argument secret").code == "cli_config"
    assert "secret" not in str(cli_failure("other failure secret"))
