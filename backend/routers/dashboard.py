"""Dashboard aggregation endpoint.

Single fat GET that the [看板] page consumes in one shot — every widget on
the page reads its slice from this payload. Keeping it server-side means:

  - One round-trip, not 8.
  - All counts come from one consistent snapshot (no widget-vs-widget
    skew while the dashboard is loading).
  - The frontend stays purely presentational; no aggregation logic in
    React.

Everything in here is read-only and free of side effects. Heavy joins
are kept SQLite-friendly: we lean on JSON1 for tag/source-paper lookups
and avoid Cartesian explosions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import KnowledgeEdge, KnowledgeNode, LLMCall, Paper
from services.paper_category_service import PAPER_CATEGORY_OTHER, effective_paper_category
from services.wiki_compiler import compute_freshness_summary


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# Top-N tags considered for the radar's axes. 6 fits the hexagon; we
# request 7 then truncate so a tied 6th/7th tag stays deterministic.
RADAR_AXES = 6
TOP_TAGS_LIMIT = 20
TOP_HUBS_LIMIT = 10
GROWTH_WEEKS = 12


# --- helpers ----------------------------------------------------------


def _week_floor(ts: datetime) -> str:
    """Return ISO date string for the Monday of the week containing ts.

    Used as the bucket key for growth timelines so all three series
    (papers / concepts / edges) line up on identical x-axis ticks even
    when the weeks have differing populations."""
    monday = ts - timedelta(days=ts.weekday())
    return monday.date().isoformat()


def _bucket_by_week(timestamps: list[Optional[datetime]], weeks: int) -> list[dict[str, Any]]:
    """Bucket a list of timestamps into the last `weeks` ISO weeks.

    Returns a list of {"week": "YYYY-MM-DD", "count": int} sorted oldest
    → newest. Missing weeks fill in with count=0 so the line chart has
    contiguous x-axis ticks."""
    now = datetime.now(timezone.utc)
    # Anchor at this week's Monday so the rightmost bucket is "this week"
    # in progress.
    end = now - timedelta(days=now.weekday())
    keys = [
        (end - timedelta(weeks=i)).date().isoformat()
        for i in range(weeks - 1, -1, -1)
    ]
    counts: Counter[str] = Counter()
    for ts in timestamps:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).days > weeks * 7 + 1:
            continue
        counts[_week_floor(ts)] += 1
    return [{"week": k, "count": int(counts.get(k, 0))} for k in keys]


def _safe_json_list(value: Any) -> list[Any]:
    """Tags / source_paper_ids are stored as JSON columns; ORM gives us
    a list already, but legacy rows occasionally hold strings or None.
    Coerce everything to a real list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json as _json

        try:
            decoded = _json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


# --- response shapes --------------------------------------------------


class GrowthSeries(BaseModel):
    weeks: list[str]
    papers: list[int]
    concepts: list[int]
    edges: list[int]


class RadarPoint(BaseModel):
    tag: str
    papers: int
    concepts: int
    # Edge density = (# of edges among nodes carrying this tag) / max(1, n_choose_2).
    # Normalized to [0, 1] for radar comparability.
    edge_density: float


class HubNode(BaseModel):
    # Post-W3.2 ids are UUID strings. Frontend types DashboardHub.id as
    # number (api/client.ts) — we'll let pydantic coerce on the wire
    # for now by typing as string; the frontend dashboard widget only
    # uses id for keying React lists, so the type drift is harmless
    # for rendering. A later cleanup can flip the frontend type to
    # `string` too.
    id: str
    title: str
    node_type: str
    degree: int


class DistributionSlice(BaseModel):
    label: str
    value: int


class CurationCell(BaseModel):
    status: str  # pending / promoted / rejected
    by: str  # human / agent / heuristic / legacy / unset
    count: int


class LintCounts(BaseModel):
    stubs: int
    merges: int
    missing_crosscut: int
    followups: int


class CompileBucket(BaseModel):
    ok: int
    missing: int
    stale: int
    orphan: int
    total: int


class LLMUsageByTask(BaseModel):
    task: str
    calls: int
    total_tokens: int
    avg_latency_ms: Optional[int]


class LLMUsageByModel(BaseModel):
    model: str
    provider: str
    calls: int
    total_tokens: int
    avg_latency_ms: Optional[int]


class DashboardSummary(BaseModel):
    generated_at: str
    overview: dict[str, int]
    radar: list[RadarPoint]
    growth: GrowthSeries
    distribution: dict[str, list[DistributionSlice]]
    top_tags: list[DistributionSlice]
    curation: list[CurationCell]
    pending_age_days: Optional[int]
    network: dict[str, Any]
    compile: dict[str, CompileBucket]
    lint: dict[str, Any]
    llm_usage: dict[str, Any]


# --- endpoints --------------------------------------------------------


@router.get("/summary", response_model=DashboardSummary)
def get_summary(db: Session = Depends(get_db)) -> DashboardSummary:
    nodes = db.query(KnowledgeNode).all()
    edges = db.query(KnowledgeEdge).all()
    papers = db.query(Paper).all()

    # ── 1. Overview counts ─────────────────────────────────────────────
    promoted_count = sum(1 for n in nodes if n.promotion_status == "promoted")
    overview = {
        "papers": len(papers),
        "papers_processed": sum(1 for p in papers if p.processed),
        "papers_unprocessed": sum(1 for p in papers if not p.processed),
        "papers_failed": sum(
            1 for p in papers if (p.processing_status or "").lower() == "failed"
        ),
        "nodes": len(nodes),
        "concepts_promoted": promoted_count,
        "edges": len(edges),
        "unique_tags": len(
            {t for n in nodes for t in _safe_json_list(n.tags) if isinstance(t, str)}
        ),
    }

    # ── 2. Tag aggregation (drives radar + tag cloud) ─────────────────
    # Post-W3.2: node ids are UUID strings. We use them as opaque hashable
    # keys throughout — no need to coerce to int (the legacy code did
    # that, but dict keys / set members work identically on strings).
    tag_counter: Counter[str] = Counter()
    node_tags: dict[str, set[str]] = {}
    tag_nodes: defaultdict[str, set[str]] = defaultdict(set)
    tag_paper_refs: defaultdict[str, set[str]] = defaultdict(set)
    for n in nodes:
        node_id = str(n.id)
        tags = {
            t.strip()
            for t in _safe_json_list(n.tags)
            if isinstance(t, str) and t.strip()
        }
        node_tags[node_id] = tags
        for tag in tags:
            tag_counter[tag] += 1
            tag_nodes[tag].add(node_id)
            for pid in _safe_json_list(n.source_paper_ids):
                if pid is None:
                    continue
                tag_paper_refs[tag].add(str(pid))

    top_tags = [
        DistributionSlice(label=tag, value=count)
        for tag, count in tag_counter.most_common(TOP_TAGS_LIMIT)
    ]

    # Build adjacency for edge density per tag.
    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    for e in edges:
        s, t = str(e.source_id), str(e.target_id)
        adjacency[s].add(t)
        adjacency[t].add(s)

    radar: list[RadarPoint] = []
    for tag, _count in tag_counter.most_common(RADAR_AXES):
        members = tag_nodes[tag]
        n = len(members)
        # Edges where both endpoints belong to this tag bucket.
        internal_edges = 0
        seen: set[tuple[str, str]] = set()
        for a in members:
            for b in adjacency.get(a, ()):
                if b in members and a != b:
                    key = (min(a, b), max(a, b))
                    if key in seen:
                        continue
                    seen.add(key)
                    internal_edges += 1
        # Density = m / C(n, 2). Capped at 1 just in case.
        max_pairs = max(1, n * (n - 1) // 2)
        density = min(1.0, internal_edges / max_pairs) if max_pairs else 0.0
        radar.append(
            RadarPoint(
                tag=tag,
                papers=len(tag_paper_refs[tag]),
                concepts=n,
                edge_density=round(density, 4),
            )
        )

    # ── 3. Growth timelines ───────────────────────────────────────────
    paper_ts = [p.processed_at for p in papers]
    concept_ts = [n.created_at for n in nodes if n.node_type != "paper"]
    edge_ts = [e.created_at for e in edges]
    paper_buckets = _bucket_by_week(paper_ts, GROWTH_WEEKS)
    concept_buckets = _bucket_by_week(concept_ts, GROWTH_WEEKS)
    edge_buckets = _bucket_by_week(edge_ts, GROWTH_WEEKS)
    growth = GrowthSeries(
        weeks=[b["week"] for b in paper_buckets],
        papers=[b["count"] for b in paper_buckets],
        concepts=[b["count"] for b in concept_buckets],
        edges=[b["count"] for b in edge_buckets],
    )

    # ── 4. Distribution pies ──────────────────────────────────────────
    category_counter: Counter[str] = Counter()
    for p in papers:
        # effective_paper_category honors the override → model → derived
        # fallback ladder; same source the graph view uses.
        cat = effective_paper_category(p) or PAPER_CATEGORY_OTHER
        category_counter[cat] += 1
    node_type_counter: Counter[str] = Counter(
        (n.node_type or "其他").strip() for n in nodes
    )
    distribution = {
        "paper_category": [
            DistributionSlice(label=k, value=v)
            for k, v in sorted(
                category_counter.items(), key=lambda x: x[1], reverse=True
            )
        ],
        "node_type": [
            DistributionSlice(label=k, value=v)
            for k, v in sorted(
                node_type_counter.items(), key=lambda x: x[1], reverse=True
            )
        ],
    }

    # ── 5. Curation health (status × promoted_by matrix) ──────────────
    cell_counter: Counter[tuple[str, str]] = Counter()
    pending_oldest: Optional[datetime] = None
    for n in nodes:
        if n.node_type == "paper":
            continue
        status = (n.promotion_status or "pending").strip() or "pending"
        by = (n.promoted_by or "unset").strip() or "unset"
        cell_counter[(status, by)] += 1
        if status == "pending":
            ts = n.last_promotion_eval_at or n.created_at
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if pending_oldest is None or ts < pending_oldest:
                    pending_oldest = ts
    curation = [
        CurationCell(status=status, by=by, count=count)
        for (status, by), count in cell_counter.items()
    ]
    pending_age_days: Optional[int] = None
    if pending_oldest is not None:
        pending_age_days = max(
            0, (datetime.now(timezone.utc) - pending_oldest).days
        )

    # ── 6. Network structure ──────────────────────────────────────────
    # All ids are UUID strings post-W3.2 — keep them opaque, never int().
    degree_counter: Counter[str] = Counter()
    relation_counter: Counter[str] = Counter()
    for e in edges:
        degree_counter[str(e.source_id)] += 1
        degree_counter[str(e.target_id)] += 1
        relation_counter[(e.relation_type or "related").strip() or "related"] += 1
    promoted_node_ids = {str(n.id) for n in nodes if n.promotion_status == "promoted"}
    nodes_by_id = {str(n.id): n for n in nodes}
    # Restrict hub ranking to promoted concepts — pending / rejected
    # clutter the leaderboard with noise.
    hub_candidates = [
        (nid, deg)
        for nid, deg in degree_counter.items()
        if nid in promoted_node_ids and nid in nodes_by_id
    ]
    hub_candidates.sort(key=lambda x: x[1], reverse=True)
    hubs = [
        HubNode(
            id=nid,
            title=nodes_by_id[nid].title or f"#{nid}",
            node_type=nodes_by_id[nid].node_type or "concept",
            degree=deg,
        )
        for nid, deg in hub_candidates[:TOP_HUBS_LIMIT]
    ]
    # Orphans: promoted nodes with no edge connections at all.
    orphans = [
        nid
        for nid in promoted_node_ids
        if nid in nodes_by_id and nid not in degree_counter
    ]
    avg_degree = (
        round(sum(degree_counter.values()) / len(degree_counter), 2)
        if degree_counter
        else 0.0
    )
    network = {
        "hubs": [h.model_dump() for h in hubs],
        "orphan_count": len(orphans),
        "avg_degree": avg_degree,
        "relation_types": [
            DistributionSlice(label=k, value=v).model_dump()
            for k, v in sorted(
                relation_counter.items(), key=lambda x: x[1], reverse=True
            )
        ],
    }

    # ── 7. Wiki freshness + lint ──────────────────────────────────────
    fresh = compute_freshness_summary(db)
    compile_payload: dict[str, CompileBucket] = {}
    for key in ("papers", "concepts"):
        bucket = fresh.get(key, {}) or {}
        compile_payload[key] = CompileBucket(
            ok=int(bucket.get("ok", 0)),
            missing=int(bucket.get("missing_count", 0)),
            stale=int(bucket.get("stale_count", 0)),
            orphan=int(bucket.get("orphan_count", 0)),
            total=int(
                bucket.get("total_processed")
                or bucket.get("total_nodes")
                or 0
            ),
        )
    lint_payload = _read_lint_snapshot()

    # ── 8. LLM usage (drives cost / model-mix widget) ─────────────────
    # 30-day rolling window.
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    usage_rows = (
        db.query(LLMCall).filter(LLMCall.called_at >= cutoff).all()
    )
    by_task: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "total_tokens": 0, "latencies": []}
    )
    by_model: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "total_tokens": 0, "latencies": []}
    )
    for row in usage_rows:
        bucket_t = by_task[row.task or "unknown"]
        bucket_t["calls"] += 1
        bucket_t["total_tokens"] += int(row.total_tokens or 0)
        if row.latency_ms is not None:
            bucket_t["latencies"].append(int(row.latency_ms))
        key_m = (row.model or "unknown", row.provider or "unknown")
        bucket_m = by_model[key_m]
        bucket_m["calls"] += 1
        bucket_m["total_tokens"] += int(row.total_tokens or 0)
        if row.latency_ms is not None:
            bucket_m["latencies"].append(int(row.latency_ms))

    def _avg_latency(latencies: list[int]) -> Optional[int]:
        return int(sum(latencies) / len(latencies)) if latencies else None

    llm_usage = {
        "window_days": 30,
        "total_calls": len(usage_rows),
        "total_tokens": sum(int(r.total_tokens or 0) for r in usage_rows),
        "success_rate": (
            round(sum(1 for r in usage_rows if r.success) / len(usage_rows), 4)
            if usage_rows
            else 1.0
        ),
        "by_task": [
            LLMUsageByTask(
                task=task,
                calls=info["calls"],
                total_tokens=info["total_tokens"],
                avg_latency_ms=_avg_latency(info["latencies"]),
            ).model_dump()
            for task, info in sorted(
                by_task.items(), key=lambda x: x[1]["calls"], reverse=True
            )
        ],
        "by_model": [
            LLMUsageByModel(
                model=model,
                provider=provider,
                calls=info["calls"],
                total_tokens=info["total_tokens"],
                avg_latency_ms=_avg_latency(info["latencies"]),
            ).model_dump()
            for (model, provider), info in sorted(
                by_model.items(), key=lambda x: x[1]["calls"], reverse=True
            )
        ],
    }

    return DashboardSummary(
        generated_at=datetime.now(timezone.utc).isoformat(),
        overview=overview,
        radar=radar,
        growth=growth,
        distribution=distribution,
        top_tags=top_tags,
        curation=curation,
        pending_age_days=pending_age_days,
        network=network,
        compile=compile_payload,
        lint=lint_payload,
        llm_usage=llm_usage,
    )


def _read_lint_snapshot() -> dict[str, Any]:
    """Pull the latest LintResult counts + modified time straight off the
    on-disk report. Falls back to None fields if no report has been
    generated yet — the frontend renders an empty-state in that case."""
    from pathlib import Path

    from path_utils import portable_data_path

    report_path = Path(portable_data_path("data/wiki/lint-report.md"))
    if not report_path.exists():
        return {"exists": False}
    # The report is markdown; we parse a tiny header line that the
    # generator writes (`<!-- counts: stubs=N merges=N ... -->`). If not
    # present we conservatively return existence only.
    text = ""
    try:
        text = report_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {"exists": True}
    counts = LintCounts(stubs=0, merges=0, missing_crosscut=0, followups=0)
    for line in text.splitlines():
        if line.startswith("<!-- counts:"):
            tail = line.split("counts:", 1)[1].rstrip("-> ").strip()
            for chunk in tail.split():
                if "=" not in chunk:
                    continue
                key, value = chunk.split("=", 1)
                value = value.rstrip("-> ")
                try:
                    setattr(counts, key, int(value))
                except (AttributeError, ValueError):
                    continue
            break
    stat = report_path.stat()
    return {
        "exists": True,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "size": stat.st_size,
        "counts": counts.model_dump(),
    }
