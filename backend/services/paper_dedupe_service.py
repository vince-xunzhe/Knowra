"""Repair duplicate local Paper rows that point at the same PDF content.

The desktop treats a PDF's file_hash as its durable identity. Older DBs may
contain two Paper rows for the same file/hash after concurrent scans. This
module collapses those rows and rewrites every local reference to the retained
canonical paper id.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from models import KnowledgeEdge, KnowledgeNode, Paper
from path_utils import DATA_DIR
from services import wiki_index, wiki_search
from services.paper_record_service import record_path_for_paper, sync_record_from_paper
from services.wiki_compiler import (
    WIKI_DIR as DEFAULT_WIKI_DIR,
    WIKI_PAPERS_DIR as DEFAULT_WIKI_PAPERS_DIR,
    _parse_frontmatter,
    _render_frontmatter,
)


@dataclass
class PaperDedupeSummary:
    duplicate_groups: int = 0
    removed_papers: list[str] = field(default_factory=list)
    canonical_by_removed: dict[str, str] = field(default_factory=dict)
    updated_nodes: int = 0
    merged_paper_nodes: int = 0
    deleted_edges: int = 0
    deleted_wiki_pages: list[str] = field(default_factory=list)
    updated_wiki_pages: int = 0
    deleted_record_pages: list[str] = field(default_factory=list)
    refreshed_index: bool = False
    rebuilt_search_index: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "duplicate_groups": self.duplicate_groups,
            "removed_papers": self.removed_papers,
            "canonical_by_removed": self.canonical_by_removed,
            "updated_nodes": self.updated_nodes,
            "merged_paper_nodes": self.merged_paper_nodes,
            "deleted_edges": self.deleted_edges,
            "deleted_wiki_pages": self.deleted_wiki_pages,
            "updated_wiki_pages": self.updated_wiki_pages,
            "deleted_record_pages": self.deleted_record_pages,
            "refreshed_index": self.refreshed_index,
            "rebuilt_search_index": self.rebuilt_search_index,
        }


def repair_duplicate_papers(
    db: Session,
    *,
    wiki_dir: Path = DEFAULT_WIKI_DIR,
    wiki_papers_dir: Path = DEFAULT_WIKI_PAPERS_DIR,
    records_dir: Path = DATA_DIR / "paper_records",
    refresh_indexes: bool = True,
    sync_records: bool = True,
) -> PaperDedupeSummary:
    """Collapse duplicate Paper rows and rewrite local references.

    The canonical row is the most complete/latest processed row in each
    file_hash group. All removed ids are rewritten to that canonical id.
    """
    summary = PaperDedupeSummary()
    groups = _duplicate_groups(db)
    summary.duplicate_groups = len(groups)
    if not groups:
        return summary

    aliases: dict[str, str] = {}
    canonical_rows: dict[str, Paper] = {}
    removed_rows: list[Paper] = []

    for rows in groups:
        canonical = _choose_canonical(rows)
        canonical_rows[str(canonical.id)] = canonical
        for paper in rows:
            if paper is canonical:
                continue
            aliases[str(paper.id)] = str(canonical.id)
            summary.canonical_by_removed[str(paper.id)] = str(canonical.id)
            summary.removed_papers.append(str(paper.id))
            removed_rows.append(paper)
            _merge_paper_fields(canonical, paper)

    summary.updated_nodes = _rewrite_node_source_papers(db, aliases)
    summary.merged_paper_nodes, deleted_edges = _merge_duplicate_paper_nodes(db)
    summary.deleted_edges += deleted_edges
    summary.deleted_edges += _dedupe_edges(db)

    for paper in removed_rows:
        db.delete(paper)

    db.flush()

    wiki_changes = _rewrite_wiki_files(
        wiki_dir=wiki_dir,
        wiki_papers_dir=wiki_papers_dir,
        aliases=aliases,
    )
    summary.deleted_wiki_pages = wiki_changes["deleted"]
    summary.updated_wiki_pages = wiki_changes["updated"]

    summary.deleted_record_pages = _rewrite_record_files(
        db,
        aliases=aliases,
        canonical_rows=canonical_rows,
        records_dir=records_dir,
        sync_records=sync_records,
    )

    db.commit()

    if refresh_indexes:
        try:
            wiki_index.refresh_index()
            summary.refreshed_index = True
        except Exception:
            summary.refreshed_index = False
        try:
            wiki_search.rebuild_index()
            summary.rebuilt_search_index = True
        except Exception:
            summary.rebuilt_search_index = False

    return summary


def _duplicate_groups(db: Session) -> list[list[Paper]]:
    by_hash: dict[str, list[Paper]] = defaultdict(list)
    for paper in db.query(Paper).all():
        if paper.file_hash:
            by_hash[str(paper.file_hash)].append(paper)
    return [rows for rows in by_hash.values() if len(rows) > 1]


def _choose_canonical(rows: list[Paper]) -> Paper:
    return max(rows, key=_paper_score)


def _paper_score(paper: Paper) -> tuple:
    return (
        1 if paper.processed else 0,
        _dt_score(paper.processed_at),
        len(paper.raw_llm_response or ""),
        len(paper.extracted_text or ""),
        _dt_score(paper.created_at),
        str(paper.id),
    )


def _dt_score(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _merge_paper_fields(canonical: Paper, duplicate: Paper) -> None:
    for field_name in (
        "user_id",
        "legacy_id",
        "filepath",
        "filename",
        "file_hash",
        "num_pages",
        "extracted_text",
        "first_page_image_path",
        "title",
        "extraction_model",
        "paper_category_model",
        "paper_category_override",
        "paper_team_model",
        "paper_team_override",
        "raw_llm_response",
        "openai_file_id",
        "openai_vector_store_id",
        "openai_thread_id",
        "thread_created_at",
    ):
        if _blank(getattr(canonical, field_name, None)) and not _blank(getattr(duplicate, field_name, None)):
            setattr(canonical, field_name, getattr(duplicate, field_name))

    canonical.processed = bool(canonical.processed or duplicate.processed)
    canonical.processed_at = _max_dt(canonical.processed_at, duplicate.processed_at)
    canonical.created_at = _min_dt(canonical.created_at, duplicate.created_at)
    canonical.retry_count = max(int(canonical.retry_count or 0), int(duplicate.retry_count or 0))

    canonical.authors = _merge_lists(canonical.authors, duplicate.authors)
    canonical.chat_history = _merge_messages(canonical.chat_history, duplicate.chat_history)
    canonical.notes = _merge_notes(canonical.notes, duplicate.notes)

    if canonical.processed:
        canonical.error = canonical.error if not _blank(canonical.error) and not _blank(duplicate.error) else None
        canonical.last_error_stage = None
        canonical.last_error_reason = None
        canonical.last_error_recoverable = None
        canonical.processing_status = "done"
    elif _blank(canonical.processing_status) and not _blank(duplicate.processing_status):
        canonical.processing_status = duplicate.processing_status


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _max_dt(a: Any, b: Any) -> Any:
    return b if _dt_score(b) > _dt_score(a) else a


def _min_dt(a: Any, b: Any) -> Any:
    if _blank(a):
        return b
    if _blank(b):
        return a
    return b if _dt_score(b) < _dt_score(a) else a


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return parsed if isinstance(parsed, list) else [parsed]
    if value is None:
        return []
    return [value]


def _merge_lists(a: Any, b: Any) -> list:
    out = []
    seen = set()
    for item in [*_as_list(a), *_as_list(b)]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_messages(a: Any, b: Any) -> list:
    out = []
    seen = set()
    for item in [*_as_list(a), *_as_list(b)]:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("role") or ""), str(item.get("content") or ""), str(item.get("ts") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_notes(a: Any, b: Any) -> str | None:
    left = str(a or "").strip()
    right = str(b or "").strip()
    if not left:
        return right or None
    if not right or right == left:
        return left
    return f"{left}\n\n---\n\n{right}"


def _rewrite_node_source_papers(db: Session, aliases: dict[str, str]) -> int:
    changed = 0
    for node in db.query(KnowledgeNode).all():
        rewritten = _rewrite_id_list(node.source_paper_ids, aliases)
        if rewritten != _as_str_list(node.source_paper_ids):
            node.source_paper_ids = rewritten
            changed += 1
    return changed


def _as_str_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item)]


def _rewrite_id_list(value: Any, aliases: dict[str, str]) -> list[str]:
    out = []
    seen = set()
    for item in _as_str_list(value):
        mapped = aliases.get(item, item)
        if mapped in seen:
            continue
        seen.add(mapped)
        out.append(mapped)
    return out


def _merge_duplicate_paper_nodes(db: Session) -> tuple[int, int]:
    nodes_by_paper: dict[str, list[KnowledgeNode]] = defaultdict(list)
    for node in db.query(KnowledgeNode).filter(KnowledgeNode.node_type == "paper").all():
        ids = _as_str_list(node.source_paper_ids)
        if len(ids) == 1:
            nodes_by_paper[ids[0]].append(node)

    merged = 0
    deleted_edges = 0
    for nodes in nodes_by_paper.values():
        if len(nodes) < 2:
            continue
        keep = max(nodes, key=lambda n: (len(n.content or ""), _dt_score(n.created_at), str(n.id)))
        for node in nodes:
            if node is keep:
                continue
            keep.tags = _merge_lists(keep.tags, node.tags)
            if len(node.content or "") > len(keep.content or ""):
                keep.content = node.content
            for edge in db.query(KnowledgeEdge).filter(KnowledgeEdge.source_id == node.id).all():
                edge.source_id = keep.id
            for edge in db.query(KnowledgeEdge).filter(KnowledgeEdge.target_id == node.id).all():
                edge.target_id = keep.id
            db.delete(node)
            merged += 1
    db.flush()
    for edge in db.query(KnowledgeEdge).filter(KnowledgeEdge.source_id == KnowledgeEdge.target_id).all():
        db.delete(edge)
        deleted_edges += 1
    return merged, deleted_edges


def _dedupe_edges(db: Session) -> int:
    deleted = 0
    seen: dict[tuple[str, str, str], KnowledgeEdge] = {}
    for edge in db.query(KnowledgeEdge).all():
        key = (str(edge.source_id), str(edge.target_id), edge.relation_type or "related")
        existing = seen.get(key)
        if existing is None:
            seen[key] = edge
            continue
        keep = max(
            (existing, edge),
            key=lambda e: (float(e.weight or 0), _dt_score(e.created_at), str(e.id)),
        )
        drop = edge if keep is existing else existing
        seen[key] = keep
        db.delete(drop)
        deleted += 1
    return deleted


def _rewrite_wiki_files(
    *,
    wiki_dir: Path,
    wiki_papers_dir: Path,
    aliases: dict[str, str],
) -> dict[str, Any]:
    deleted: list[str] = []
    updated = 0

    if wiki_papers_dir.exists():
        for path in sorted(wiki_papers_dir.glob("*.md")):
            meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
            paper_id = str(meta.get("paper_id") or "")
            if paper_id in aliases:
                path.unlink()
                deleted.append(str(path))

    if wiki_dir.exists():
        for path in sorted(wiki_dir.rglob("*.md")):
            if str(path) in deleted or not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            if not meta:
                new_text = _replace_ids(text, aliases)
            else:
                new_meta = _rewrite_meta(meta, aliases)
                new_body = _replace_ids(body, aliases)
                new_text = _render_frontmatter(new_meta) + new_body
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                updated += 1

    return {"deleted": deleted, "updated": updated}


def _rewrite_meta(meta: dict, aliases: dict[str, str]) -> dict:
    new_meta: dict[str, Any] = {}
    for key, value in meta.items():
        if key == "source_paper_ids":
            new_meta[key] = _rewrite_id_list(value, aliases)
        elif key == "paper_id" and str(value) in aliases:
            new_meta[key] = aliases[str(value)]
        elif key == "aliases":
            new_meta[key] = _dedupe_strings(_rewrite_value(value, aliases))
        else:
            new_meta[key] = _rewrite_value(value, aliases)
    return new_meta


def _rewrite_value(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_rewrite_value(item, aliases) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_value(item, aliases) for key, item in value.items()}
    if isinstance(value, str):
        return _replace_ids(value, aliases)
    return value


def _replace_ids(text: str, aliases: dict[str, str]) -> str:
    out = text
    for old, new in aliases.items():
        out = out.replace(old, new)
    return out


def _dedupe_strings(value: Any) -> list:
    out = []
    seen = set()
    for item in _as_list(value):
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _rewrite_record_files(
    db: Session,
    *,
    aliases: dict[str, str],
    canonical_rows: dict[str, Paper],
    records_dir: Path,
    sync_records: bool,
) -> list[str]:
    deleted: list[str] = []
    if records_dir.exists():
        old_prefixes = {old.replace("-", "")[:8] for old in aliases}
        for path in sorted(records_dir.glob("*.md")):
            if path.name.split("-", 1)[0] in old_prefixes:
                path.unlink()
                deleted.append(str(path))

    for canonical_id in sorted(set(aliases.values())):
        paper = canonical_rows.get(canonical_id) or db.get(Paper, canonical_id)
        if paper is None:
            continue
        if not sync_records:
            continue
        sync_record_from_paper(paper, event="dedupe")
        expected = record_path_for_paper(paper)
        if expected.exists() and str(expected) in deleted:
            deleted.remove(str(expected))
    return deleted
