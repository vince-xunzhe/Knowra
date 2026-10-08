"""Structured knowledge-edge writes and provenance lifecycle management.

One database row represents one logical ``(source, target, relation)`` edge.
Independent sources are retained as entries in ``metadata.provenance`` rather
than as duplicate rows.  The scalar provenance columns mirror the strongest
contribution for simple clients; the contribution list is authoritative for
targeted invalidation and inspection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from models import KnowledgeEdge


EDGE_ORIGINS = {"explicit", "inferred", "embedding", "manual", "legacy"}
MAX_EDGE_EVIDENCE_CHARS = 1000
EXTRACTION_SCHEMA_VERSION = "paper-extraction-v1"

_ORIGIN_PRIORITY = {
    "manual": 5,
    "explicit": 4,
    "inferred": 3,
    "embedding": 2,
    "legacy": 1,
}
_SYMMETRIC_RELATIONS = {"similar", "related", "contrasts_with"}


def _bounded_evidence(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    if not cleaned:
        return None
    return cleaned[:MAX_EDGE_EVIDENCE_CHARS]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        # Round-trip to detach mutable ORM values and reject non-JSON data at
        # the write boundary instead of failing later during commit.
        return json.loads(json.dumps(value, ensure_ascii=False))
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


@dataclass(frozen=True)
class EdgeSpec:
    """Complete description required for every new logical edge write."""

    source_id: Any
    target_id: Any
    relation_type: str
    origin: str
    weight: float = 0.0
    confidence: Optional[float] = None
    source_paper_id: Optional[Any] = None
    source_field: Optional[str] = None
    evidence: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    extractor_version: Optional[str] = None
    user_id: Optional[str] = None

    def __post_init__(self) -> None:
        origin = str(self.origin or "").strip().lower()
        if origin not in EDGE_ORIGINS:
            raise ValueError(f"Unsupported edge origin: {self.origin!r}")
        relation = str(self.relation_type or "").strip().lower()
        if not relation:
            raise ValueError("relation_type is required")
        confidence = self.confidence
        if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("edge confidence must be between 0 and 1")
        metadata = _json_dict(self.metadata)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "relation_type", relation)
        object.__setattr__(self, "weight", float(self.weight or 0.0))
        object.__setattr__(
            self,
            "confidence",
            None if confidence is None else float(confidence),
        )
        object.__setattr__(
            self,
            "source_paper_id",
            None if self.source_paper_id in (None, "") else str(self.source_paper_id),
        )
        object.__setattr__(
            self,
            "source_field",
            (str(self.source_field).strip() or None) if self.source_field is not None else None,
        )
        object.__setattr__(self, "evidence", _bounded_evidence(self.evidence))
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True)
class EdgeWriteResult:
    edge: Optional[KnowledgeEdge]
    created: bool = False
    provenance_added: bool = False


def _contribution(
    *,
    origin: str,
    confidence: Optional[float],
    source_paper_id: Optional[Any],
    source_field: Optional[str],
    evidence: Optional[str],
    extractor_version: Optional[str],
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"origin": origin}
    if confidence is not None:
        item["confidence"] = float(confidence)
    if source_paper_id not in (None, ""):
        item["source_paper_id"] = str(source_paper_id)
    if source_field:
        item["source_field"] = str(source_field)
    bounded = _bounded_evidence(evidence)
    if bounded:
        item["evidence"] = bounded
    if extractor_version:
        item["extractor_version"] = str(extractor_version)
    if metadata:
        item["metadata"] = _json_dict(metadata)
    return item


def contribution_from_spec(spec: EdgeSpec) -> dict[str, Any]:
    return _contribution(
        origin=spec.origin,
        confidence=spec.confidence,
        source_paper_id=spec.source_paper_id,
        source_field=spec.source_field,
        evidence=spec.evidence,
        extractor_version=spec.extractor_version,
        metadata=spec.metadata,
    )


def edge_contributions(edge: KnowledgeEdge) -> list[dict[str, Any]]:
    root = _json_dict(getattr(edge, "edge_metadata", None))
    raw = root.get("provenance")
    if isinstance(raw, list):
        out = [_json_dict(item) for item in raw]
        out = [item for item in out if item.get("origin") in EDGE_ORIGINS]
        if out:
            return out

    origin = str(getattr(edge, "origin", None) or "").strip().lower()
    if origin not in EDGE_ORIGINS:
        relation = str(getattr(edge, "relation_type", None) or "")
        origin = "embedding" if relation == "similar" else (
            "manual" if relation == "curated_link" else "legacy"
        )
    confidence = getattr(edge, "confidence", None)
    if confidence is None and origin == "embedding":
        weight = getattr(edge, "weight", None)
        if weight is not None and 0.0 <= float(weight) <= 1.0:
            confidence = float(weight)
    return [
        _contribution(
            origin=origin,
            confidence=confidence,
            source_paper_id=getattr(edge, "source_paper_id", None),
            source_field=getattr(edge, "source_field", None),
            evidence=getattr(edge, "evidence", None),
            extractor_version=getattr(edge, "extractor_version", None),
        )
    ]


def _contribution_key(item: dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _merge_contributions(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            item = _json_dict(raw)
            if item.get("origin") not in EDGE_ORIGINS:
                continue
            item["evidence"] = _bounded_evidence(item.get("evidence"))
            if item.get("evidence") is None:
                item.pop("evidence", None)
            key = _contribution_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _primary_contribution(contributions: list[dict[str, Any]]) -> dict[str, Any]:
    def confidence_rank(item: dict[str, Any]) -> float:
        value = item.get("confidence")
        return float(value) if value is not None else -1.0

    return max(
        contributions,
        key=lambda item: (
            _ORIGIN_PRIORITY.get(str(item.get("origin")), 0),
            confidence_rank(item),
            _contribution_key(item),
        ),
    )


def apply_contributions(edge: KnowledgeEdge, contributions: list[dict[str, Any]]) -> None:
    contributions = _merge_contributions(contributions)
    if not contributions:
        raise ValueError("an edge must retain at least one provenance contribution")
    primary = _primary_contribution(contributions)
    edge.origin = primary["origin"]
    edge.confidence = primary.get("confidence")
    edge.source_paper_id = primary.get("source_paper_id")
    edge.source_field = primary.get("source_field")
    edge.evidence = primary.get("evidence")
    edge.extractor_version = primary.get("extractor_version")
    root = _json_dict(getattr(edge, "edge_metadata", None))
    root["provenance"] = contributions
    edge.edge_metadata = root


def add_edge(db: Session, spec: EdgeSpec) -> EdgeWriteResult:
    """Create or merge a logical edge without losing independent sources."""
    if str(spec.source_id) == str(spec.target_id):
        return EdgeWriteResult(edge=None)
    db.flush()
    existing = (
        db.query(KnowledgeEdge)
        .filter(
            KnowledgeEdge.source_id == spec.source_id,
            KnowledgeEdge.target_id == spec.target_id,
            KnowledgeEdge.relation_type == spec.relation_type,
        )
        .first()
    )
    if existing is None and spec.relation_type in _SYMMETRIC_RELATIONS:
        existing = (
            db.query(KnowledgeEdge)
            .filter(
                KnowledgeEdge.source_id == spec.target_id,
                KnowledgeEdge.target_id == spec.source_id,
                KnowledgeEdge.relation_type == "similar",
            )
            .first()
        )

    incoming = contribution_from_spec(spec)
    if existing is not None:
        before = edge_contributions(existing)
        merged = _merge_contributions(before, [incoming])
        apply_contributions(existing, merged)
        existing.weight = max(float(existing.weight or 0.0), spec.weight)
        return EdgeWriteResult(
            edge=existing,
            created=False,
            provenance_added=len(merged) > len(before),
        )

    edge = KnowledgeEdge(
        source_id=spec.source_id,
        target_id=spec.target_id,
        relation_type=spec.relation_type,
        weight=spec.weight,
        user_id=spec.user_id,
        created_at=datetime.now(timezone.utc),
    )
    apply_contributions(edge, [incoming])
    db.add(edge)
    return EdgeWriteResult(edge=edge, created=True, provenance_added=True)


def specs_from_edge(
    edge: KnowledgeEdge,
    *,
    source_id: Any,
    target_id: Any,
) -> list[EdgeSpec]:
    """Copy all provenance contributions while changing only endpoints."""
    return [
        EdgeSpec(
            source_id=source_id,
            target_id=target_id,
            relation_type=edge.relation_type or "related",
            origin=item["origin"],
            weight=float(edge.weight or 0.0),
            confidence=item.get("confidence"),
            source_paper_id=item.get("source_paper_id"),
            source_field=item.get("source_field"),
            evidence=item.get("evidence"),
            metadata=_json_dict(item.get("metadata")),
            extractor_version=item.get("extractor_version"),
            user_id=getattr(edge, "user_id", None),
        )
        for item in edge_contributions(edge)
    ]


def remove_paper_edge_provenance(db: Session, paper_id: Any) -> dict[str, int]:
    """Remove only expirable contributions produced from one paper.

    Manual, inferred, embedding and legacy contributions never expire merely
    because a paper is reprocessed.  An explicit edge row is deleted only when
    its final supporting paper contribution is removed.
    """
    paper_key = str(paper_id)
    removed_contributions = 0
    deleted_edges = 0
    updated_edges = 0
    for edge in db.query(KnowledgeEdge).all():
        before = edge_contributions(edge)
        after = [
            item
            for item in before
            if not (
                item.get("origin") == "explicit"
                and str(item.get("source_paper_id") or "") == paper_key
            )
        ]
        delta = len(before) - len(after)
        if not delta:
            continue
        removed_contributions += delta
        if not after:
            db.delete(edge)
            deleted_edges += 1
            continue
        apply_contributions(edge, after)
        updated_edges += 1
    db.flush()
    return {
        "removed_contributions": removed_contributions,
        "updated_edges": updated_edges,
        "deleted_edges": deleted_edges,
    }


def backfill_and_merge_edge_provenance(db: Session) -> dict[str, int]:
    """Idempotently backfill origins and collapse duplicate logical edges."""
    edges = list(db.query(KnowledgeEdge).all())
    groups: dict[tuple[str, str, str], KnowledgeEdge] = {}
    merged_rows = 0
    backfilled_rows = 0

    def group_key(edge: KnowledgeEdge) -> tuple[str, str, str]:
        source, target = str(edge.source_id), str(edge.target_id)
        relation = str(edge.relation_type or "related")
        if relation == "similar" and target < source:
            source, target = target, source
        return source, target, relation

    def winner_order(edge: KnowledgeEdge) -> tuple[str, str]:
        created_at = getattr(edge, "created_at", None)
        created_key = created_at.isoformat() if created_at is not None else "9999"
        return created_key, str(edge.id)

    for edge in sorted(edges, key=winner_order):
        prior_origin = str(getattr(edge, "origin", None) or "").strip().lower()
        contributions = edge_contributions(edge)
        apply_contributions(edge, contributions)
        if prior_origin not in EDGE_ORIGINS:
            backfilled_rows += 1
        key = group_key(edge)
        winner = groups.get(key)
        if winner is None:
            groups[key] = edge
            continue
        apply_contributions(
            winner,
            _merge_contributions(edge_contributions(winner), contributions),
        )
        winner.weight = max(float(winner.weight or 0.0), float(edge.weight or 0.0))
        db.delete(edge)
        merged_rows += 1

    db.flush()
    # Desktop SQLite is one logical tenant.  This guard makes future direct
    # writers obey the same logical-edge rule as add_edge().
    db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS knowledge_edges_logical_uniq "
            "ON knowledge_edges (source_id, target_id, relation_type)"
        )
    )
    db.commit()
    return {
        "edges_seen": len(edges),
        "backfilled_rows": backfilled_rows,
        "merged_rows": merged_rows,
        "edges_after": len(edges) - merged_rows,
    }


def serialize_edge_provenance(edge: KnowledgeEdge) -> dict[str, Any]:
    """Additive API/sync representation shared by graph serializers."""
    return {
        "origin": getattr(edge, "origin", None) or "legacy",
        "confidence": getattr(edge, "confidence", None),
        "source_paper_id": (
            str(edge.source_paper_id)
            if getattr(edge, "source_paper_id", None) not in (None, "")
            else None
        ),
        "source_field": getattr(edge, "source_field", None),
        "evidence": getattr(edge, "evidence", None),
        "metadata": _json_dict(getattr(edge, "edge_metadata", None)),
        "extractor_version": getattr(edge, "extractor_version", None),
    }
