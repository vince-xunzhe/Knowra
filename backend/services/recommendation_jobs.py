"""Durable per-user recommendation jobs and positive-only feedback."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from cloud_models import (
    CloudKnowledgeNode,
    CloudPaper,
    RecBatch,
    RecCandidate,
    RecEvent,
    RecProfile,
    RecUsage,
    RecWorker,
)
from sqlalchemy import or_

from services.personal_recommendation import (
    ai_shortlist,
    apply_ai,
    aware,
    base_id,
    build_profile,
    normalized_title,
    rank_candidates,
    select_diverse,
    signature,
    terms,
    utcnow,
)

LEASE_MINUTES = 15
MAX_ATTEMPTS = 3
MONTHLY_BUDGET = 30.0
HISTORY_DAYS = 90


def upsert(db, model, values, keys, update=()):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Recommendation storage requires PostgreSQL or SQLite")
    statement = insert(model).values(**values)
    if update:
        statement = statement.on_conflict_do_update(
            index_elements=list(keys),
            set_={k: getattr(statement.excluded, k) for k in update},
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=list(keys))
    db.execute(statement)


def get_profile(db, user_id):
    upsert(db, RecProfile, {"user_id": user_id}, ("user_id",))
    return db.query(RecProfile).filter_by(user_id=user_id).one()


def library_rows(db, user_id):
    loader = db.info.get("recommendation_library_loader")
    if loader is not None:
        return loader(user_id)
    papers = db.query(CloudPaper).filter_by(user_id=user_id).all()
    fields = (
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
    nodes = db.query(CloudKnowledgeNode).filter_by(user_id=user_id, hidden=False).all()
    return (
        [{k: getattr(p, k) for k in fields} for p in papers],
        [
            {k: getattr(n, k) for k in ("title", "node_type", "source_paper_ids")}
            for n in nodes
        ],
    )


def record_event(db, user_id, batch_id, aid, kind, now=None):
    if kind not in {"exposed", "viewed", "requested", "adopted"}:
        raise ValueError("Unknown event")
    batch = (
        db.query(RecBatch)
        .filter_by(id=batch_id, user_id=user_id, status="completed")
        .one_or_none()
    )
    aid = base_id(aid)
    item = next(
        (i for i in (batch.items if batch else []) if i["arxiv_id"] == aid), None
    )
    if not item:
        raise ValueError("论文不属于当前用户的已完成推荐批次")
    previous = (
        db.query(RecEvent)
        .filter_by(user_id=user_id, arxiv_id=aid, kind=kind)
        .one_or_none()
    )
    first_seen = (
        (previous.payload.get("first_seen") or aware(previous.created_at).isoformat())
        if previous
        else (now or utcnow()).isoformat()
    )
    upsert(
        db,
        RecEvent,
        {
            "id": str(uuid4()),
            "user_id": user_id,
            "batch_id": batch_id,
            "arxiv_id": aid,
            "kind": kind,
            "created_at": now or utcnow(),
            "payload": {
                "terms": sorted(terms(item["title"] + " " + item.get("abstract", ""))),
                "title": item["title"],
                "profile_version": batch.snapshot.get("version"),
                "first_seen": first_seen,
            },
        },
        ("user_id", "arxiv_id", "kind"),
        ("created_at", "batch_id", "payload") if kind == "exposed" else (),
    )


def reconcile_adoptions(db, user_id, now=None):
    """Reconcile persisted library rows locally or after sync; clients cannot assert adoption."""
    now = now or utcnow()
    papers, _ = library_rows(db, user_id)
    ids = {base_id(p["filename"]): p for p in papers if base_id(p["filename"])}
    titles = {normalized_title(p["title"]): p for p in papers if p["title"]}
    interactions = (
        db.query(RecEvent)
        .filter(
            RecEvent.user_id == user_id,
            RecEvent.kind.in_(["viewed", "exposed", "requested"]),
        )
        .order_by(RecEvent.created_at)
        .all()
    )
    for event in interactions:
        paper = ids.get(event.arxiv_id) or titles.get(
            normalized_title(event.payload.get("title"))
        )
        if paper and aware(paper["created_at"]) >= (
            aware(event.payload.get("first_seen")) or aware(event.created_at)
        ):
            record_event(
                db,
                user_id,
                event.batch_id,
                event.arxiv_id,
                "adopted",
                now=aware(paper["created_at"]),
            )


def refresh_profile(db, user_id, now=None):
    now = now or utcnow()
    profile = get_profile(db, user_id)
    # Serialize per-user changes (PostgreSQL); SQLite uses its writer lock.
    profile = db.query(RecProfile).filter_by(user_id=user_id).with_for_update().one()
    reconcile_adoptions(db, user_id, now)
    positives = (
        db.query(RecEvent)
        .filter_by(user_id=user_id, kind="adopted")
        .order_by(RecEvent.created_at)
        .all()
    )
    if len(positives) - profile.feedback_count >= 3 and (
        not profile.feedback_at or now - aware(profile.feedback_at) >= timedelta(days=7)
    ):
        counts = Counter()
        for event in positives:
            words = event.payload.get("terms", [])
            for word in words:
                counts[word] += 1 / max(1, len(words))
        total = sum(counts.values()) or 1
        target = {word: value / total for word, value in counts.most_common(250)}
        old = profile.feedback_weights or {}
        profile.feedback_weights = {
            word: 0.8 * old.get(word, 0) + 0.2 * target.get(word, 0)
            for word in set(old) | set(target)
        }
        profile.feedback_count = len(positives)
        profile.feedback_at = now
    papers, nodes = library_rows(db, user_id)
    snapshot = build_profile(
        papers, nodes, profile.current_focus, profile.feedback_weights, now
    )
    if signature(snapshot) != signature(profile.snapshot):
        profile.version += 1
        profile.snapshot = snapshot
        profile.updated_at = now
    return profile


def scheduled_slot(now=None):
    local = (now or utcnow()).astimezone(ZoneInfo("Asia/Shanghai"))
    for days in range(7):
        candidate = (local - timedelta(days=days)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        if candidate.weekday() in {0, 2, 4} and candidate <= local:
            return candidate.isoformat()
    raise ValueError("No schedule slot")


def enqueue(db, user_id, *, manual=False, now=None):
    now = now or utcnow()
    profile = refresh_profile(db, user_id, now)
    if not profile.snapshot.get("paper_count"):
        return None
    active = (
        db.query(RecBatch)
        .filter(RecBatch.user_id == user_id, RecBatch.status.in_(["queued", "running"]))
        .first()
    )
    if active:
        return active
    # Manual requests are limited to one per date, independent of scheduled slots.
    slot = (
        ("manual:" + now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat())
        if manual
        else scheduled_slot(now)
    )
    snapshot = {
        **profile.snapshot,
        "version": profile.version,
        "as_of": now.isoformat(),
    }
    upsert(
        db,
        RecBatch,
        {
            "id": str(uuid4()),
            "user_id": user_id,
            "slot": slot,
            "snapshot": snapshot,
            "created_at": now,
        },
        ("user_id", "slot"),
    )
    return db.query(RecBatch).filter_by(user_id=user_id, slot=slot).one()


def schedule_all(db):
    for (user_id,) in db.query(CloudPaper.user_id).distinct().all():
        enqueue(db, user_id)
    db.commit()


def require_lease(db, worker, job_id, lease, now=None):
    now = now or utcnow()
    # All paths lock profile before batch, including claim and completion.
    get_profile(db, worker.user_id)
    db.query(RecProfile).filter_by(user_id=worker.user_id).with_for_update().one()
    job = (
        db.query(RecBatch)
        .filter_by(id=job_id, user_id=worker.user_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        not job
        or job.status != "running"
        or job.node_id != worker.node_id
        or job.lease_token != lease
        or not job.lease_expires_at
        or aware(job.lease_expires_at) <= now
    ):
        raise ValueError("任务租约失效，请重新领取")
    return job


def claim(db, worker, now=None):
    now = now or utcnow()
    enqueue(db, worker.user_id, now=now)
    expired = db.query(RecBatch).filter(
        RecBatch.user_id == worker.user_id,
        RecBatch.status == "running",
        RecBatch.lease_expires_at <= now,
    )
    for job in expired.all():
        job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
        job.error = (
            "执行超时，已等待重试" if job.status == "queued" else "执行重试次数已达上限"
        )
    db.flush()
    job = (
        db.query(RecBatch)
        .filter_by(user_id=worker.user_id, status="queued")
        .order_by(RecBatch.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not job:
        return None
    lease = str(uuid4())
    updated = (
        db.query(RecBatch)
        .filter_by(id=job.id, status="queued")
        .update(
            {
                "status": "running",
                "lease_token": lease,
                "lease_expires_at": now + timedelta(minutes=LEASE_MINUTES),
                "attempts": RecBatch.attempts + 1,
                "node_id": worker.node_id,
                "error": None,
            },
            synchronize_session=False,
        )
    )
    if not updated:
        return None
    db.flush()
    db.refresh(job)
    excluded = [
        e.arxiv_id
        for e in db.query(RecEvent)
        .filter(
            RecEvent.user_id == worker.user_id,
            or_(
                RecEvent.kind == "adopted",
                RecEvent.created_at >= now - timedelta(days=30),
            ),
        )
        .all()
    ]
    # Freeze ranking inputs with the lease; asynchronous browsing must not make
    # the worker's valid AI result fail validation against a different shortlist.
    profile = refresh_profile(db, worker.user_id, now)
    job.snapshot = {
        **profile.snapshot,
        "version": profile.version,
        "as_of": now.isoformat(),
        "excluded": excluded,
    }
    candidates = [
        c.metadata_json
        for c in db.query(RecCandidate)
        .order_by(RecCandidate.updated_at.desc())
        .limit(3000)
        .all()
    ]
    return {
        "id": job.id,
        "lease": lease,
        "snapshot": job.snapshot,
        "excluded": excluded,
        "candidates": candidates,
    }


def reserve_budget(db, worker, job_id, lease, amount, provider):
    job = require_lease(db, worker, job_id, lease)
    # Per-user row lock serializes reservations across all worker nodes.
    get_profile(db, worker.user_id)
    db.query(RecProfile).filter_by(user_id=worker.user_id).with_for_update().one()
    usage = db.query(RecUsage).filter_by(batch_id=job.id).one_or_none()
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = sum(
        u.reserved_cny
        for u in db.query(RecUsage)
        .filter(RecUsage.user_id == worker.user_id, RecUsage.created_at >= start)
        .all()
    )
    if provider != "codex_cli" and amount <= 0:
        raise ValueError("API 调用必须先配置可核算的费用上界")
    carry = usage.reserved_cny if usage and aware(usage.created_at) < start else 0
    if spent + carry + amount > MONTHLY_BUDGET:
        raise ValueError("本月推荐调用预算不足")
    if usage is None:
        usage = RecUsage(
            user_id=worker.user_id,
            batch_id=job.id,
            provider=provider,
            reserved_cny=0,
            calls=0,
        )
        db.add(usage)
    if usage.calls >= MAX_ATTEMPTS:
        raise ValueError("当前批次调用次数已达上限")
    usage.calls += 1
    usage.reserved_cny += amount
    if carry:
        usage.created_at = (
            now  # Conservatively carry an in-flight month's reservations forward.
        )
    # Reservations intentionally remain charged on interrupted calls: cost is unknown.
    return usage


def complete(db, worker, job_id, lease, candidates, output=None, note=None):
    job = require_lease(db, worker, job_id, lease)
    as_of = aware(job.snapshot["as_of"])
    previous = {
        c.arxiv_id: c
        for c in db.query(RecCandidate)
        .filter(
            RecCandidate.arxiv_id.in_([base_id(c.get("arxiv_id")) for c in candidates])
        )
        .all()
    }
    clean = []
    for candidate in candidates:
        aid = base_id(candidate.get("arxiv_id"))
        if not aid or not isinstance(candidate.get("title"), str):
            raise ValueError("候选元数据无效")
        item = {
            "arxiv_id": aid,
            "title": candidate["title"][:500],
            "abstract": str(candidate.get("abstract") or "")[:12000],
            "authors": [
                a[:200]
                for a in (candidate.get("authors") or [])[:80]
                if isinstance(a, str)
            ],
            "primary_category": str(candidate.get("primary_category") or "")[:100],
            "published": aware(candidate.get("published")).isoformat()
            if aware(candidate.get("published"))
            else None,
        }
        clean.append(item)
        digest = signature(item)
        old = previous.get(aid)
        features = old.features if old and old.content_hash == digest else {}
        upsert(
            db,
            RecCandidate,
            {
                "arxiv_id": aid,
                "metadata_json": item,
                "content_hash": digest,
                "features": features,
                "updated_at": utcnow(),
            },
            ("arxiv_id",),
            ("metadata_json", "content_hash", "features", "updated_at"),
        )
    db.expire_all()  # Core upserts must not leave stale ORM cache entries.
    exclusions = job.snapshot.get("excluded", [])
    ranked = rank_candidates(job.snapshot, clean, excluded=exclusions, now=as_of)[:30]
    if output is not None:
        ranked = apply_ai(ai_shortlist(ranked), output)
        for item in ranked:
            db.query(RecCandidate).filter_by(arxiv_id=item["arxiv_id"]).update(
                {
                    "features": {
                        "dimensions": item["features"],
                        "evidence": item["feature_evidence"],
                        "schema_version": 1,
                    }
                },
                synchronize_session=False,
            )
    else:
        cached_features = {
            c.arxiv_id: c
            for c in db.query(RecCandidate)
            .filter(RecCandidate.arxiv_id.in_([r["arxiv_id"] for r in ranked]))
            .all()
        }
        for item in ranked:
            cached = cached_features.get(item["arxiv_id"])
            if cached and cached.features:
                item["features"] = cached.features.get("dimensions", {})
    # Library may change while the worker is running. Recheck before publishing.
    current = refresh_profile(db, worker.user_id)
    ranked = [
        r
        for r in ranked
        if r["arxiv_id"] not in current.snapshot["library_ids"]
        and normalized_title(r["title"]) not in current.snapshot["library_titles"]
    ]
    job.items = select_diverse(ranked)
    job.status, job.completed_at, job.error = "completed", utcnow(), note
    job.lease_token, job.lease_expires_at = None, None
    # Short-lived cache cleanup does not delete events, profile versions or batches.
    db.query(RecCandidate).filter(
        RecCandidate.updated_at < utcnow() - timedelta(days=180)
    ).delete(synchronize_session=False)
    return job


def metrics(db, user_id, now=None):
    now = now or utcnow()
    events = db.query(RecEvent).filter_by(user_id=user_id).all()
    adopted = {e.arxiv_id: aware(e.created_at) for e in events if e.kind == "adopted"}
    views = [e for e in events if e.kind == "viewed"]
    mature = [e for e in views if aware(e.created_at) <= now - timedelta(days=14)]
    converted = sum(
        aware(e.created_at)
        <= adopted[e.arxiv_id]
        <= aware(e.created_at) + timedelta(days=14)
        for e in mature
        if e.arxiv_id in adopted
    )
    shown = {e.arxiv_id for e in events if e.kind == "exposed"}
    return {
        "viewed": len(views),
        "mature_viewed": len(mature),
        "adopted": len(adopted),
        "exposed": len(shown),
        "adopted_14d": converted,
        "adoption_rate_14d": converted / len(mature) if mature else None,
        "target": 0.3,
    }


def batch_json(batch):
    return {
        "id": batch.id,
        "status": batch.status,
        "error": batch.error,
        "slot": batch.slot,
        "count": len(batch.items),
        "profile_version": batch.snapshot.get("version"),
        "created_at": aware(batch.created_at).isoformat(),
        "completed_at": aware(batch.completed_at).isoformat()
        if batch.completed_at
        else None,
    }


def batch_history(db, user_id, offset=0, limit=20):
    cutoff = utcnow() - timedelta(days=HISTORY_DAYS)
    query = db.query(RecBatch).filter(
        RecBatch.user_id == user_id,
        RecBatch.status == "completed",
        RecBatch.completed_at >= cutoff,
    )
    batches = (
        query.order_by(RecBatch.completed_at.desc(), RecBatch.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    return {
        "batches": [batch_json(batch) for batch in batches[:limit]],
        "retention_days": HISTORY_DAYS,
        "next_offset": offset + limit if len(batches) > limit else None,
    }


def feed(db, user_id, batch_id=None):
    profile = refresh_profile(db, user_id)
    newest = (
        db.query(RecBatch)
        .filter_by(user_id=user_id)
        .order_by(RecBatch.created_at.desc())
        .first()
    )
    latest = (
        db.query(RecBatch)
        .filter(
            RecBatch.user_id == user_id,
            RecBatch.status == "completed",
            RecBatch.completed_at >= utcnow() - timedelta(days=HISTORY_DAYS),
        )
        .order_by(RecBatch.completed_at.desc(), RecBatch.id.desc())
        .first()
    )
    workers = db.query(RecWorker).filter_by(user_id=user_id).all()
    online = [
        w
        for w in workers
        if w.last_seen_at and utcnow() - aware(w.last_seen_at) <= timedelta(minutes=5)
    ]
    worker_status = (
        "not_configured" if not workers else "online" if online else "offline"
    )

    selected = latest
    if batch_id:
        selected = (
            db.query(RecBatch)
            .filter_by(id=batch_id, user_id=user_id, status="completed")
            .filter(RecBatch.completed_at >= utcnow() - timedelta(days=HISTORY_DAYS))
            .one_or_none()
        )
        if selected is None:
            raise ValueError("该期精选不存在或已超过回顾期限，请返回最新一期")
    library_ids = set(profile.snapshot.get("library_ids", []))
    library_titles = set(profile.snapshot.get("library_titles", []))
    items = []
    for item in selected.items if selected else []:
        in_library = (
            item["arxiv_id"] in library_ids
            or normalized_title(item["title"]) in library_titles
        )
        # Explicit batch views preserve the original selection, including adopted papers.
        if batch_id or not in_library:
            items.append({**item, "in_library": in_library})

    requests = db.query(RecEvent).filter_by(user_id=user_id, kind="requested").all()
    adopted = {
        e.arxiv_id
        for e in db.query(RecEvent).filter_by(user_id=user_id, kind="adopted").all()
    }
    return {
        "profile": {
            "version": profile.version,
            "current_focus": profile.current_focus,
            "paper_count": profile.snapshot.get("paper_count", 0),
            "dimensions": profile.snapshot.get("dimensions", {}),
        },
        "batch": batch_json(selected) if selected else None,
        "latest_batch": batch_json(latest) if latest else None,
        "job": batch_json(newest) if newest else None,
        "items": items,
        "workers": [
            {
                "node_id": w.node_id,
                "health": w.health,
                "last_seen_at": aware(w.last_seen_at).isoformat()
                if w.last_seen_at
                else None,
            }
            for w in workers
        ],
        "worker_status": worker_status,
        "metrics": metrics(db, user_id),
        "pending_imports": [
            {
                "arxiv_id": e.arxiv_id,
                "batch_id": e.batch_id,
                "title": e.payload.get("title", ""),
            }
            for e in requests
            if e.arxiv_id not in adopted
        ],
        "budget": {
            "limit_cny": MONTHLY_BUDGET,
            "reserved_cny": sum(
                u.reserved_cny
                for u in db.query(RecUsage)
                .filter(
                    RecUsage.user_id == user_id,
                    RecUsage.created_at
                    >= utcnow().replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    ),
                )
                .all()
            ),
        },
    }
