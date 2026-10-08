"""Paper-level derivation manifests for the local processing pipeline.

The task queue already checkpoints orchestration steps.  This module adds the
missing *data* checkpoint: which source bytes, prompt, model and builder
produced the extraction, graph, wiki and search artifacts for one paper.

Manifests deliberately live beside the local data directory instead of in the
knowledge database.  They are rebuildable metadata, are written atomically,
and cannot make a database transaction fail.  The knowledge database remains
the source of truth for user content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from services.edge_provenance import EXTRACTION_SCHEMA_VERSION


MANIFEST_SCHEMA_VERSION = 1
GRAPH_BUILDER_VERSION = "graph-mutation-v1"
WIKI_COMPILER_VERSION = "wiki-compiler-v1"
SEARCH_INDEX_VERSION = "wiki-search-v1"
MANIFEST_DIR = Path(__file__).resolve().parents[2] / "data" / "manifests"

_ARXIV_RE = re.compile(
    r"(?<!\d)(?P<base>\d{4}\.\d{4,5})(?P<version>v\d+)?(?!\d)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_hash(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_path(paper_id: str, root: Optional[Path] = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(paper_id))
    return (root or MANIFEST_DIR) / f"{safe}.json"


def empty_manifest(paper_id: str) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "paper_id": str(paper_id),
        "updated_at": now_iso(),
        "checkpoint": {"stage": "untracked", "updated_at": now_iso()},
        "source": {},
        "identity": {},
        "layers": {},
    }


def load_manifest(paper_id: str, *, root: Optional[Path] = None) -> dict[str, Any]:
    path = _manifest_path(paper_id, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_manifest(paper_id)
    if not isinstance(value, dict) or value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return empty_manifest(paper_id)
    value.setdefault("paper_id", str(paper_id))
    value.setdefault("source", {})
    value.setdefault("identity", {})
    value.setdefault("layers", {})
    value.setdefault("checkpoint", {})
    return value


def save_manifest(
    manifest: Mapping[str, Any], *, root: Optional[Path] = None
) -> dict[str, Any]:
    value = dict(manifest)
    value["schema_version"] = MANIFEST_SCHEMA_VERSION
    value["paper_id"] = str(value["paper_id"])
    value["updated_at"] = now_iso()
    directory = root or MANIFEST_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = _manifest_path(value["paper_id"], directory)
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return value


def extract_deterministic_identity(
    *,
    filename: str,
    file_hash: str,
    extraction: Optional[Mapping[str, Any]] = None,
    raw_text: str = "",
) -> dict[str, Any]:
    """Extract stable paper identity without network or model calls.

    DOI/arXiv values are accepted only when they are present in deterministic
    source material (filename or stored extraction text).  File SHA remains the
    final fallback, so every paper always has a reproducible identity.
    """

    extraction = extraction or {}
    filename_text = filename or ""
    structured_text = "\n".join(
        [str(extraction.get("doi") or ""), str(extraction.get("arxiv_id") or "")]
    )
    arxiv_source = next(
        (
            (match, source)
            for match, source in (
                (_ARXIV_RE.search(filename_text), "filename"),
                (_ARXIV_RE.search(structured_text), "structured_metadata"),
                (_ARXIV_RE.search(raw_text or ""), "source_text"),
            )
            if match
        ),
        (None, None),
    )
    doi_source = next(
        (
            (match, source)
            for match, source in (
                (_DOI_RE.search(filename_text), "filename"),
                (_DOI_RE.search(structured_text), "structured_metadata"),
                (_DOI_RE.search(raw_text or ""), "source_text"),
            )
            if match
        ),
        (None, None),
    )
    arxiv, arxiv_origin = arxiv_source
    doi, doi_origin = doi_source
    arxiv_base = arxiv.group("base").lower() if arxiv else None
    arxiv_version = arxiv.group("version").lower() if arxiv and arxiv.group("version") else None
    doi_value = doi.group(0).rstrip(".,;)").lower() if doi else None
    if doi_value:
        canonical_id = f"doi:{doi_value}"
        kind = "doi"
    elif arxiv_base:
        canonical_id = f"arxiv:{arxiv_base}"
        kind = "arxiv"
    else:
        canonical_id = f"sha256:{file_hash}"
        kind = "sha256"
    return {
        "canonical_id": canonical_id,
        "kind": kind,
        "doi": doi_value,
        "arxiv_id": arxiv_base,
        "arxiv_version": arxiv_version,
        "file_sha256": file_hash,
        "provenance": {
            "doi": doi_origin,
            "arxiv_id": arxiv_origin,
            "fallback": "file_sha256",
        },
    }


def desired_extraction_signature(paper, cfg: Mapping[str, Any], model: str) -> dict[str, Any]:
    return {
        "source_sha256": str(paper.file_hash or ""),
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "prompt_hash": stable_hash(str(cfg.get("extraction_prompt") or "")),
        "model": str(model or ""),
    }


def extraction_is_reusable(
    paper,
    cfg: Mapping[str, Any],
    model: str,
    *,
    root: Optional[Path] = None,
) -> bool:
    raw = str(getattr(paper, "raw_llm_response", None) or "")
    if not raw:
        return False
    manifest = load_manifest(str(paper.id), root=root)
    recorded = (manifest.get("layers") or {}).get("extraction") or {}
    desired = desired_extraction_signature(paper, cfg, model)
    return (
        all(recorded.get(key) == value for key, value in desired.items())
        and recorded.get("output_hash") == stable_hash(raw)
        and recorded.get("status") == "complete"
    )


def record_checkpoint(
    paper_id: str,
    stage: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    manifest = load_manifest(paper_id, root=root)
    checkpoint: dict[str, Any] = {"stage": stage, "updated_at": now_iso()}
    if details:
        checkpoint["details"] = dict(details)
    manifest["checkpoint"] = checkpoint
    return save_manifest(manifest, root=root)


def record_extraction(
    paper,
    cfg: Mapping[str, Any],
    model: str,
    extraction: Mapping[str, Any],
    raw: str,
    *,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    manifest = load_manifest(str(paper.id), root=root)
    manifest["source"] = {
        "paper_id": str(paper.id),
        "filename": str(paper.filename or ""),
        "sha256": str(paper.file_hash or ""),
    }
    manifest["identity"] = extract_deterministic_identity(
        filename=str(paper.filename or ""),
        file_hash=str(paper.file_hash or ""),
        extraction=extraction,
        raw_text=str(getattr(paper, "extracted_text", None) or "")[:20000],
    )
    manifest.setdefault("layers", {})["extraction"] = {
        **desired_extraction_signature(paper, cfg, model),
        "output_hash": stable_hash(raw),
        "structured_hash": stable_hash(extraction),
        "status": "complete",
        "completed_at": now_iso(),
    }
    manifest["checkpoint"] = {"stage": "extracted", "updated_at": now_iso()}
    return save_manifest(manifest, root=root)


def record_layer(
    paper_id: str,
    layer: str,
    payload: Mapping[str, Any],
    *,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    manifest = load_manifest(paper_id, root=root)
    manifest.setdefault("layers", {})[layer] = {
        **dict(payload),
        "status": "complete",
        "completed_at": now_iso(),
    }
    manifest["checkpoint"] = {"stage": f"{layer}_complete", "updated_at": now_iso()}
    return save_manifest(manifest, root=root)


def invalidate_from(
    paper_id: str,
    layer: str,
    *,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    order = ["extraction", "graph", "wiki", "search"]
    if layer not in order:
        raise ValueError(f"Unknown manifest layer: {layer}")
    manifest = load_manifest(paper_id, root=root)
    layers = manifest.setdefault("layers", {})
    for name in order[order.index(layer) :]:
        layers.pop(name, None)
    manifest["checkpoint"] = {
        "stage": f"invalidated_from_{layer}",
        "updated_at": now_iso(),
    }
    return save_manifest(manifest, root=root)


def manifest_status(paper_id: str, *, root: Optional[Path] = None) -> dict[str, Any]:
    manifest = load_manifest(paper_id, root=root)
    return {
        "paper_id": str(paper_id),
        "checkpoint": manifest.get("checkpoint") or {},
        "identity": manifest.get("identity") or {},
        "layers": {
            name: {
                "status": value.get("status"),
                "completed_at": value.get("completed_at"),
            }
            for name, value in (manifest.get("layers") or {}).items()
            if isinstance(value, dict)
        },
        "updated_at": manifest.get("updated_at"),
    }
