"""Versioned extraction fragments and transactional graph mutation plans.

The resolver is read-only: it turns model output plus a database snapshot into
a serializable plan.  The writer is the only mutating boundary and applies the
whole plan under one savepoint.  This keeps old graph data visible when a new
extraction or graph write fails and makes dry-run/debugging deterministic.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session, defer

from models import KnowledgeEdge, KnowledgeNode
from services.edge_provenance import (
    EXTRACTION_SCHEMA_VERSION,
    EdgeSpec,
    add_edge,
    edge_contributions,
    remove_paper_edge_provenance,
)
from services.pipeline_manifest import GRAPH_BUILDER_VERSION, stable_hash
from services.vlm_service import cosine_similarity


AUTO_CONCEPT_TYPES = {"technique", "dataset", "problem_area", "concept"}
AUTO_ORIGIN = "auto"
MANUAL_ORIGIN = "manual"
MAX_TITLE_LEN = 24


class SourceDescriptor(BaseModel):
    paper_id: str
    source_sha256: str = ""


class ExtractedNode(BaseModel):
    key: str
    title: str
    content: str
    node_type: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_field: str


class ExtractedEdge(BaseModel):
    source_key: str
    target_key: str
    relation_type: str
    source_field: str
    evidence: Optional[str] = None


class ExtractionFragment(BaseModel):
    source: SourceDescriptor
    schema_version: str = EXTRACTION_SCHEMA_VERSION
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)


class NodeMutation(BaseModel):
    action: Literal["create", "update", "noop"]
    node_id: str
    key: str
    title: str
    content: str
    node_type: str
    tags: list[str] = Field(default_factory=list)
    source_paper_ids: list[str] = Field(default_factory=list)
    embedding: Optional[list[float]] = None
    promotion_status: str


class EdgeMutation(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    origin: str
    weight: float = 1.0
    confidence: Optional[float] = None
    source_paper_id: Optional[str] = None
    source_field: Optional[str] = None
    evidence: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extractor_version: Optional[str] = None


class GraphMutationPlan(BaseModel):
    version: str = GRAPH_BUILDER_VERSION
    paper_id: str
    fragment_hash: str
    create_nodes: list[NodeMutation] = Field(default_factory=list)
    update_nodes: list[NodeMutation] = Field(default_factory=list)
    unchanged_nodes: list[NodeMutation] = Field(default_factory=list)
    detach_node_ids: list[str] = Field(default_factory=list)
    delete_node_ids: list[str] = Field(default_factory=list)
    expire_provenance_edge_ids: list[str] = Field(default_factory=list)
    delete_similarity_edge_ids: list[str] = Field(default_factory=list)
    create_edges: list[EdgeMutation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _dump(model: BaseModel) -> dict[str, Any]:
    method = getattr(model, "model_dump", None)
    return method(mode="json") if method else model.dict()


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _title(value: Any, node_type: str) -> str:
    raw = str(value or "").strip()
    if node_type == "paper" or len(raw) <= MAX_TITLE_LEN:
        return raw
    return raw[: MAX_TITLE_LEN - 1] + "…"


def _node_key(node_type: str, title: str) -> str:
    return f"{node_type}:{_norm(title)}"


def _unique_strings(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def fragment_from_extraction(
    extraction: Mapping[str, Any],
    *,
    paper_id: str,
    source_sha256: str = "",
) -> ExtractionFragment:
    """Normalize the existing prompt output without changing that prompt."""

    nodes: dict[str, ExtractedNode] = {}
    edges: list[ExtractedEdge] = []
    keywords = _unique_strings(extraction.get("keywords") or [])

    def add_node(
        node_type: str,
        title: str,
        content: str,
        source_field: str,
        *,
        aliases=None,
    ) -> Optional[str]:
        display = _title(title, node_type)
        if not display:
            return None
        key = _node_key(node_type, display)
        incoming_aliases = _unique_strings(aliases or [])
        prior = nodes.get(key)
        if prior is None:
            nodes[key] = ExtractedNode(
                key=key,
                title=display,
                content=str(content or display).strip(),
                node_type=node_type,
                aliases=incoming_aliases,
                tags=_unique_strings([*keywords, *incoming_aliases]),
                source_field=source_field,
            )
        else:
            prior.aliases = _unique_strings([*prior.aliases, *incoming_aliases])
            prior.tags = _unique_strings([*prior.tags, *keywords, *incoming_aliases])
            if len(str(content or "")) > len(prior.content):
                prior.content = str(content)
        return key

    paper_title = str(extraction.get("title") or "").strip()
    abstract = str(extraction.get("abstract_summary") or "").strip()
    venue = str(extraction.get("venue") or "").strip()
    year = extraction.get("year")
    prefix = " ".join(part for part in (venue, str(year or "").strip()) if part)
    paper_content = f"[{prefix}] {abstract}".strip() if prefix else abstract
    paper_key = add_node("paper", paper_title, paper_content or paper_title, "title")

    area = str(extraction.get("problem_area") or "").strip()
    area_key = add_node("problem_area", area, f"研究领域: {area}", "problem_area")
    if paper_key and area_key:
        edges.append(ExtractedEdge(
            source_key=paper_key,
            target_key=area_key,
            relation_type="belongs_to",
            source_field="problem_area",
            evidence=area,
        ))

    technique_keys: dict[str, str] = {}
    for item in extraction.get("techniques") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        role = str(item.get("role") or "").strip()
        key = add_node(
            "technique",
            name,
            name + (f"（{role}）" if role else ""),
            "techniques",
            aliases=item.get("aliases") or [],
        )
        if not key:
            continue
        technique_keys[_norm(name)] = key
        for alias in item.get("aliases") or []:
            technique_keys[_norm(alias)] = key
        if paper_key:
            edges.append(ExtractedEdge(
                source_key=paper_key,
                target_key=key,
                relation_type="uses",
                source_field="techniques",
                evidence=role or name,
            ))

    for item in extraction.get("techniques") or []:
        if not isinstance(item, Mapping):
            continue
        source_key = technique_keys.get(_norm(item.get("name")))
        if not source_key:
            continue
        for dependency in item.get("builds_on") or []:
            target_key = technique_keys.get(_norm(dependency))
            if target_key and target_key != source_key:
                edges.append(ExtractedEdge(
                    source_key=source_key,
                    target_key=target_key,
                    relation_type="builds_on",
                    source_field="techniques[].builds_on",
                    evidence=f"{item.get('name')} builds on {dependency}",
                ))

    for item in extraction.get("datasets") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        key = add_node(
            "dataset",
            name,
            f"数据集: {name}" + (f"（{purpose}）" if purpose else ""),
            "datasets",
        )
        if paper_key and key:
            relation = "trained_on" if "train" in purpose.lower() or "训练" in purpose else "evaluated_on"
            edges.append(ExtractedEdge(
                source_key=paper_key,
                target_key=key,
                relation_type=relation,
                source_field="datasets",
                evidence=purpose or name,
            ))

    for baseline in extraction.get("baselines") or []:
        if not isinstance(baseline, str) or not baseline.strip():
            continue
        key = technique_keys.get(_norm(baseline))
        if key is None:
            key = add_node("technique", baseline, f"Baseline: {baseline}", "baselines")
        if paper_key and key:
            edges.append(ExtractedEdge(
                source_key=paper_key,
                target_key=key,
                relation_type="compared_to",
                source_field="baselines",
                evidence=baseline.strip(),
            ))

    # Stable ordering makes fragment hashes and dry-run output reproducible.
    ordered_nodes = sorted(nodes.values(), key=lambda item: item.key)
    ordered_edges = sorted(
        edges,
        key=lambda item: (
            item.source_key,
            item.target_key,
            item.relation_type,
            item.source_field,
            item.evidence or "",
        ),
    )
    return ExtractionFragment(
        source=SourceDescriptor(paper_id=str(paper_id), source_sha256=source_sha256),
        nodes=ordered_nodes,
        edges=ordered_edges,
    )


def _source_ids(node: KnowledgeNode) -> list[str]:
    raw = node.source_paper_ids or []
    if not isinstance(raw, list):
        raw = [raw]
    return _unique_strings(str(value) for value in raw if value is not None)


def _node_origin(node: KnowledgeNode) -> str:
    return str(getattr(node, "node_origin", None) or AUTO_ORIGIN).strip().lower()


def _deterministic_node_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://knowra.local/graph/{key}"))


def _existing_lookup(nodes: list[KnowledgeNode], paper_id: str) -> dict[str, KnowledgeNode]:
    lookup: dict[str, KnowledgeNode] = {}
    for node in nodes:
        if _node_origin(node) == MANUAL_ORIGIN:
            continue
        lookup.setdefault(_node_key(str(node.node_type or "concept"), str(node.title or "")), node)
        for tag in node.tags or []:
            key = _norm(tag)
            if key and node.node_type != "paper":
                lookup.setdefault(f"{node.node_type}:{key}", node)
        if node.node_type == "paper" and str(paper_id) in _source_ids(node):
            lookup["paper:__source__"] = node
    return lookup


def _owned_explicit_edge_ids(edges: list[KnowledgeEdge], paper_id: str) -> list[str]:
    result = []
    for edge in edges:
        if any(
            item.get("origin") == "explicit"
            and str(item.get("source_paper_id") or "") == str(paper_id)
            for item in edge_contributions(edge)
        ):
            result.append(str(edge.id))
    return sorted(result)


def resolve_mutation_plan(
    db: Session,
    fragment: ExtractionFragment,
    *,
    embeddings: Mapping[tuple[str, str], Optional[list[float]]],
    similarity_threshold: float,
) -> GraphMutationPlan:
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Resolve a mutation plan from a clean database session")

    paper_id = str(fragment.source.paper_id)
    existing_nodes = list(
        db.query(KnowledgeNode).options(defer(KnowledgeNode.embedding)).all()
    )
    # Keep the vector-table read bounded to one query per paper.  Loading the
    # general node snapshot with embedding deferred prevents N lazy vector
    # reads while preserving the browsing-during-processing invariant.
    embedding_nodes = list(
        db.query(KnowledgeNode)
        .filter(KnowledgeNode.embedding.isnot(None))
        .all()
    )
    existing_embeddings = {
        str(node.id): node.embedding
        for node in embedding_nodes
        if isinstance(node.embedding, list) and node.embedding
    }
    existing_edges = list(db.query(KnowledgeEdge).all())
    lookup = _existing_lookup(existing_nodes, paper_id)
    resolved: dict[str, NodeMutation] = {}

    for item in fragment.nodes:
        existing = lookup.get("paper:__source__") if item.node_type == "paper" else None
        existing = existing or lookup.get(item.key)
        node_id = str(existing.id) if existing is not None else _deterministic_node_id(item.key)
        incoming_embedding = embeddings.get((item.node_type, item.title))
        if existing is None:
            mutation = NodeMutation(
                action="create",
                node_id=node_id,
                key=item.key,
                title=item.title,
                content=item.content,
                node_type=item.node_type,
                tags=_unique_strings([*item.tags, *item.aliases]),
                source_paper_ids=[paper_id],
                embedding=incoming_embedding,
                promotion_status="pending" if item.node_type in AUTO_CONCEPT_TYPES else "promoted",
            )
        else:
            ids = _unique_strings([*_source_ids(existing), paper_id])
            tags = _unique_strings([*(existing.tags or []), *item.tags, *item.aliases])
            content = item.content if len(item.content) > len(existing.content or "") else (existing.content or item.content)
            prior_embedding = existing_embeddings.get(str(existing.id))
            embedding = prior_embedding or incoming_embedding
            changed = any(
                (
                    existing.title != item.title,
                    existing.content != content,
                    list(existing.tags or []) != tags,
                    _source_ids(existing) != ids,
                    prior_embedding != embedding,
                )
            )
            mutation = NodeMutation(
                action="update" if changed else "noop",
                node_id=node_id,
                key=item.key,
                title=item.title if item.node_type == "paper" else (existing.title or item.title),
                content=content,
                node_type=str(existing.node_type or item.node_type),
                tags=tags,
                source_paper_ids=ids,
                embedding=embedding,
                promotion_status=str(getattr(existing, "promotion_status", None) or "pending"),
            )
        resolved[item.key] = mutation

    desired_ids = {mutation.node_id for mutation in resolved.values()}
    detach_node_ids: list[str] = []
    delete_node_ids: list[str] = []
    for node in existing_nodes:
        if _node_origin(node) == MANUAL_ORIGIN or paper_id not in _source_ids(node):
            continue
        node_id = str(node.id)
        if node_id in desired_ids:
            continue
        remaining = [value for value in _source_ids(node) if value != paper_id]
        (detach_node_ids if remaining else delete_node_ids).append(node_id)

    edge_mutations: list[EdgeMutation] = []
    for edge in fragment.edges:
        source = resolved.get(edge.source_key)
        target = resolved.get(edge.target_key)
        if source is None or target is None or source.node_id == target.node_id:
            continue
        edge_mutations.append(EdgeMutation(
            source_id=source.node_id,
            target_id=target.node_id,
            relation_type=edge.relation_type,
            origin="explicit",
            weight=1.0,
            source_paper_id=paper_id,
            source_field=edge.source_field,
            evidence=edge.evidence,
            metadata={"evidence_kind": "structured_extraction"},
            extractor_version=fragment.schema_version,
        ))

    # Similarity edges are also planned deterministically.  Existing incident
    # similarity rows are replaced inside the same savepoint.
    touched_ids = set(desired_ids)
    future_embeddings: dict[str, list[float]] = {}
    future_embeddings.update(existing_embeddings)
    for mutation in resolved.values():
        if isinstance(mutation.embedding, list) and mutation.embedding:
            future_embeddings[mutation.node_id] = mutation.embedding

    delete_similarity = sorted(
        str(edge.id)
        for edge in existing_edges
        if edge.relation_type == "similar"
        and (str(edge.source_id) in touched_ids or str(edge.target_id) in touched_ids)
    )
    similarity_pairs: set[tuple[str, str]] = set()
    for source_id in sorted(touched_ids):
        source_vector = future_embeddings.get(source_id)
        if not source_vector:
            continue
        for target_id, target_vector in sorted(future_embeddings.items()):
            if target_id == source_id or target_id in delete_node_ids:
                continue
            pair = tuple(sorted((source_id, target_id)))
            if pair in similarity_pairs:
                continue
            similarity_pairs.add(pair)
            try:
                similarity = cosine_similarity(source_vector, target_vector)
            except Exception:
                continue
            if similarity < similarity_threshold:
                continue
            rounded = round(float(similarity), 4)
            edge_mutations.append(EdgeMutation(
                source_id=pair[0],
                target_id=pair[1],
                relation_type="similar",
                origin="embedding",
                weight=rounded,
                confidence=rounded,
                source_field="embedding",
                metadata={
                    "context": "mutation_plan",
                    "threshold": similarity_threshold,
                },
            ))

    fragment_hash = stable_hash(_dump(fragment))
    ordered_edges = sorted(
        edge_mutations,
        key=lambda item: (item.source_id, item.target_id, item.relation_type, item.origin),
    )
    return GraphMutationPlan(
        paper_id=paper_id,
        fragment_hash=fragment_hash,
        create_nodes=sorted(
            [item for item in resolved.values() if item.action == "create"],
            key=lambda item: item.node_id,
        ),
        update_nodes=sorted(
            [item for item in resolved.values() if item.action == "update"],
            key=lambda item: item.node_id,
        ),
        unchanged_nodes=sorted(
            [item for item in resolved.values() if item.action == "noop"],
            key=lambda item: item.node_id,
        ),
        detach_node_ids=sorted(detach_node_ids),
        delete_node_ids=sorted(delete_node_ids),
        expire_provenance_edge_ids=_owned_explicit_edge_ids(existing_edges, paper_id),
        delete_similarity_edge_ids=delete_similarity,
        create_edges=ordered_edges,
    )


def plan_summary(plan: GraphMutationPlan) -> dict[str, Any]:
    explicit = sum(1 for edge in plan.create_edges if edge.origin == "explicit")
    similarity = sum(1 for edge in plan.create_edges if edge.origin == "embedding")
    return {
        "version": plan.version,
        "paper_id": plan.paper_id,
        "fragment_hash": plan.fragment_hash,
        "create_nodes": len(plan.create_nodes),
        "update_nodes": len(plan.update_nodes),
        "unchanged_nodes": len(plan.unchanged_nodes),
        "detach_nodes": len(plan.detach_node_ids),
        "delete_nodes": len(plan.delete_node_ids),
        "expire_explicit_edges": len(plan.expire_provenance_edge_ids),
        "replace_similarity_edges": len(plan.delete_similarity_edge_ids),
        "create_explicit_edges": explicit,
        "create_similarity_edges": similarity,
        "plan_hash": stable_hash(_dump(plan)),
        "warnings": list(plan.warnings),
    }


def apply_mutation_plan(
    db: Session,
    plan: GraphMutationPlan,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a validated plan atomically; caller retains commit ownership."""

    summary = plan_summary(plan)
    if dry_run:
        return {**summary, "dry_run": True, "applied": False}

    try:
        provenance = remove_paper_edge_provenance(db, plan.paper_id)

        if plan.delete_similarity_edge_ids:
            db.query(KnowledgeEdge).filter(
                KnowledgeEdge.id.in_(plan.delete_similarity_edge_ids)
            ).delete(synchronize_session=False)

        delete_ids = set(plan.delete_node_ids)
        if delete_ids:
            db.query(KnowledgeEdge).filter(
                or_(
                    KnowledgeEdge.source_id.in_(delete_ids),
                    KnowledgeEdge.target_id.in_(delete_ids),
                )
            ).delete(synchronize_session=False)
            db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(delete_ids)).delete(
                synchronize_session=False
            )

        for node_id in plan.detach_node_ids:
            node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
            if node is None or _node_origin(node) == MANUAL_ORIGIN:
                continue
            node.source_paper_ids = [
                value for value in _source_ids(node) if value != plan.paper_id
            ]

        for mutation in [*plan.create_nodes, *plan.update_nodes]:
            node = db.query(KnowledgeNode).filter(KnowledgeNode.id == mutation.node_id).first()
            if node is None:
                node = KnowledgeNode(
                    id=mutation.node_id,
                    node_origin=AUTO_ORIGIN,
                    hidden=False,
                )
                db.add(node)
            elif _node_origin(node) == MANUAL_ORIGIN:
                raise RuntimeError(f"mutation plan attempted to overwrite manual node {node.id}")
            node.title = mutation.title
            node.content = mutation.content
            node.node_type = mutation.node_type
            node.tags = list(mutation.tags)
            node.source_paper_ids = list(mutation.source_paper_ids)
            node.embedding = mutation.embedding
            node.promotion_status = mutation.promotion_status

        db.flush()
        created_edges = 0
        merged_edges = 0
        for mutation in plan.create_edges:
            result = add_edge(db, EdgeSpec(
                source_id=mutation.source_id,
                target_id=mutation.target_id,
                relation_type=mutation.relation_type,
                origin=mutation.origin,
                weight=mutation.weight,
                confidence=mutation.confidence,
                source_paper_id=mutation.source_paper_id,
                source_field=mutation.source_field,
                evidence=mutation.evidence,
                metadata=mutation.metadata,
                extractor_version=mutation.extractor_version,
            ))
            if result.edge is None:
                continue
            if result.created:
                created_edges += 1
            else:
                merged_edges += 1
        db.flush()
    except BaseException:
        # The resolver refuses dirty sessions, so rolling back the caller-owned
        # transaction cannot discard unrelated user changes.  Avoiding a
        # top-level SQLite SAVEPOINT also preserves the caller's ability to
        # inspect and explicitly rollback a successful uncommitted plan.
        db.rollback()
        raise

    return {
        **summary,
        "dry_run": False,
        "applied": True,
        "created_edges": created_edges,
        "merged_edges": merged_edges,
        "provenance": provenance,
    }


def build_and_apply_extraction(
    db: Session,
    extraction: Mapping[str, Any],
    *,
    paper_id: str,
    source_sha256: str,
    embeddings: Mapping[tuple[str, str], Optional[list[float]]],
    similarity_threshold: float,
    dry_run: bool = False,
) -> tuple[GraphMutationPlan, dict[str, Any]]:
    fragment = fragment_from_extraction(
        extraction,
        paper_id=paper_id,
        source_sha256=source_sha256,
    )
    plan = resolve_mutation_plan(
        db,
        fragment,
        embeddings=embeddings,
        similarity_threshold=similarity_threshold,
    )
    return plan, apply_mutation_plan(db, plan, dry_run=dry_run)
