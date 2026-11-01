"""Build a private, non-production Ask shadow generation from a restored copy.

The builder is deliberately offline.  It opens the copied knowledge database
immutable/read-only, parses copied PDFs locally, and writes only to a new
shadow root that must not overlap either the input copy or production Wiki.
It never reads application configuration or calls a model/provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from services.hybrid_claim_retrieval import build_shadow_index
from services.paper_evidence_service import (
    build_pdf_evidence,
    load_evidence_manifest,
    write_evidence_manifest,
)
from services.wiki_shadow_compiler import build_paper_compile_plan, validate_compile_plan


class ShadowGenerationError(ValueError):
    """The requested generation would cross an isolation boundary."""


def _paper_source(*, papers_root: Path, filepath: Any, filename: Any) -> Path:
    raw = str(filepath or "").replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    relative: Path | None = None
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == ["data", "papers"]:
            relative = Path(*parts[index + 2:])
            break
    if relative is None and raw:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                relative = candidate.resolve().relative_to(papers_root.resolve())
            except (OSError, ValueError):
                relative = None
        elif parts and parts[0] == "papers":
            relative = Path(*parts[1:])
        elif parts and parts[0] != "data":
            relative = Path(*parts)
    if relative is None or not relative.parts:
        fallback = Path(str(filename or ""))
        if len(fallback.parts) != 1:
            raise ShadowGenerationError("paper source path is invalid")
        relative = fallback
    if relative.is_absolute() or ".." in relative.parts:
        raise ShadowGenerationError("paper source escaped the restored papers directory")
    candidate = papers_root / relative
    if any(
        (papers_root / Path(*relative.parts[:index])).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise ShadowGenerationError("paper source path contains a symlink")
    try:
        pdf = candidate.resolve(strict=True)
    except OSError as error:
        raise ShadowGenerationError("paper source is unavailable") from error
    if not pdf.is_relative_to(papers_root) or not pdf.is_file():
        raise ShadowGenerationError("paper source escaped the restored papers directory")
    return pdf


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_stable_json(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _isolated_roots(data_root: Path, output_root: Path, production_wiki_root: Path) -> tuple[Path, Path, Path]:
    data = data_root.resolve(strict=True)
    production = production_wiki_root.resolve(strict=True)
    output_parent = output_root.parent.resolve(strict=True)
    output = output_parent / output_root.name
    if output.exists():
        raise ShadowGenerationError("output root already exists")
    for left, right in ((data, output), (production, output)):
        if left == right or left.is_relative_to(right) or right.is_relative_to(left):
            raise ShadowGenerationError("shadow output overlaps an input or production root")
    if data == production or data.is_relative_to(production):
        raise ShadowGenerationError("restored input must not be the production Wiki")
    return data, output, production


def _read_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build_restored_shadow_generation(
    *,
    data_root: str | Path,
    output_root: str | Path,
    production_wiki_root: str | Path,
    compiler_model_label: str = "existing-structured-extraction",
    locale: str = "zh",
    library_revision: dict[str, Any] | None = None,
    reuse_generation_root: str | Path | None = None,
    reuse_evidence_ids: set[str] | None = None,
    reuse_plan_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build evidence, claim plans and a lexical shadow index from a restore."""
    data, output, production = _isolated_roots(
        Path(data_root), Path(output_root), Path(production_wiki_root)
    )
    database = data / "knowledge.db"
    papers_root = (data / "papers").resolve(strict=True)
    if not database.is_file() or database.is_symlink() or not papers_root.is_dir():
        raise ShadowGenerationError("restored database or papers directory is unavailable")
    output.mkdir(mode=0o700)
    reuse_root = Path(reuse_generation_root).resolve(strict=True) if reuse_generation_root else None
    evidence_reuse = set(reuse_evidence_ids or ())
    plan_reuse = set(reuse_plan_ids or ())
    if not plan_reuse.issubset(evidence_reuse):
        raise ShadowGenerationError("compile plan reuse requires evidence reuse")
    if (evidence_reuse or plan_reuse) and reuse_root is None:
        raise ShadowGenerationError("reuse ids require a previous generation")

    manifests: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    failures: dict[str, int] = {}
    rows_seen = 0
    connection = _read_connection(database)
    try:
        rows = connection.execute(
            "SELECT id, filepath, filename, file_hash, title, raw_llm_response, notes "
            "FROM papers WHERE processed = 1 AND raw_llm_response IS NOT NULL ORDER BY id"
        )
        for row in rows:
            rows_seen += 1
            try:
                paper_id = str(row["id"])
                extraction = json.loads(str(row["raw_llm_response"] or ""))
                if not isinstance(extraction, dict):
                    raise ShadowGenerationError("structured extraction is not an object")
                title = str(row["title"] or extraction.get("title") or "").strip()
                if not title:
                    raise ShadowGenerationError("paper title is unavailable")
                recorded_hash = str(row["file_hash"] or "").strip().lower()
                expected_hash = recorded_hash if len(recorded_hash) == 64 else None
                if paper_id in evidence_reuse:
                    manifest = load_evidence_manifest(reuse_root / "evidence" / f"{paper_id}.json")
                else:
                    pdf = _paper_source(
                        papers_root=papers_root,
                        filepath=row["filepath"],
                        filename=row["filename"],
                    )
                    manifest = build_pdf_evidence(
                        pdf, paper_id=paper_id, source_file_hash=expected_hash
                    )
                if paper_id in plan_reuse:
                    plan = json.loads(
                        (reuse_root / "plans" / f"{paper_id}.json").read_text(encoding="utf-8")
                    )
                else:
                    plan = build_paper_compile_plan(
                        paper_id=paper_id,
                        title=title,
                        extraction=extraction,
                        notes=str(row["notes"] or ""),
                        model=compiler_model_label,
                        locale=locale,
                        source_file_hash=manifest["source"]["file_hash_sha256"],
                        evidence_manifest=manifest,
                    )
                validate_compile_plan(plan)
                manifests.append(manifest)
                plans.append(plan)
                write_evidence_manifest(manifest, output / "evidence" / f"{paper_id}.json")
                _atomic_json(output / "plans" / f"{paper_id}.json", plan)
            except Exception as error:  # per-paper failure is measured, never printed with content/path
                name = type(error).__name__
                failures[name] = failures.get(name, 0) + 1
    finally:
        connection.close()

    if not plans or not manifests:
        raise ShadowGenerationError("no valid paper generation inputs were produced")
    _atomic_json(output / "build-state.json", {
        "schema_version": 1,
        "rows_seen": rows_seen,
        "papers_compiled": len(plans),
        "papers_failed": rows_seen - len(plans),
        "failure_types": dict(sorted(failures.items())),
    })
    index_path = output / "indexes" / "claims.sqlite"
    index = build_shadow_index(
        index_path=index_path,
        shadow_root=output,
        production_wiki_root=production,
        compile_plans=plans,
        evidence_manifests=manifests,
    )
    summary = {
        "schema_version": 1,
        "rows_seen": rows_seen,
        "papers_compiled": len(plans),
        "papers_failed": rows_seen - len(plans),
        "failure_types": dict(sorted(failures.items())),
        "evidence_pages": sum(item["coverage"]["attempted_pages"] for item in manifests),
        "evidence_failed_pages": sum(item["coverage"]["failed_pages"] for item in manifests),
        "evidence_empty_pages": sum(item["coverage"]["empty_pages"] for item in manifests),
        "evidence_chunks": sum(item["coverage"]["chunk_count"] for item in manifests),
        "claims": index["claims"],
        "index_records": index["records"],
        "index_bytes": index["bytes"],
        "source_digest": index["source_digest"],
        "dense_index_digest": index["dense_index_digest"],
        "dense_model_id": None,
    }
    _atomic_json(output / "summary.json", summary)
    generation_manifest = {
        "schema_version": 1,
        "generation_id": output.name,
        "status": "ready",
        "index_relpath": "indexes/claims.sqlite",
        "source_digest": index["source_digest"],
        "dense_index_digest": index["dense_index_digest"],
        "dense_model_id": None,
        "index_sha256": _sha256_file(index_path),
        "index_bytes": index_path.stat().st_size,
        "production_eligible": False,
    }
    if library_revision is not None:
        inventory_path = output / "library-inventory.json"
        _atomic_json(inventory_path, {
            "schema_version": library_revision["schema_version"],
            "revision": library_revision["revision"],
            "entries": library_revision["entries"],
        })
        generation_manifest.update({
            "library_revision": library_revision["revision"],
            "library_paper_count": library_revision["paper_count"],
            "paper_ids_digest": library_revision["paper_ids_digest"],
            "library_inventory_relpath": "library-inventory.json",
            "library_inventory_sha256": _sha256_file(inventory_path),
        })
    _atomic_json(output / "generation.json", generation_manifest)
    return summary


def finalize_shadow_generation(
    *, output_root: str | Path, production_wiki_root: str | Path
) -> dict[str, Any]:
    """Validate persisted per-paper artifacts and finish an interrupted build."""
    output = Path(output_root).resolve(strict=True)
    production = Path(production_wiki_root).resolve(strict=True)
    if output == production or output.is_relative_to(production) or production.is_relative_to(output):
        raise ShadowGenerationError("shadow output overlaps production Wiki")
    if (output / "generation.json").exists():
        raise ShadowGenerationError("generation is already finalized")
    plan_paths = sorted((output / "plans").glob("*.json"))
    evidence_paths = sorted((output / "evidence").glob("*.json"))
    plan_names = {path.stem for path in plan_paths}
    evidence_names = {path.stem for path in evidence_paths}
    state_path = output / "build-state.json"
    if not state_path.is_file() or state_path.is_symlink():
        raise ShadowGenerationError("persisted build state is unavailable")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        not plan_paths
        or plan_names != evidence_names
        or state.get("schema_version") != 1
        or state.get("papers_compiled") != len(plan_paths)
        or state.get("rows_seen") != state.get("papers_compiled", 0) + state.get("papers_failed", 0)
        or not isinstance(state.get("failure_types"), dict)
    ):
        raise ShadowGenerationError("persisted paper artifacts are incomplete")
    plans = [json.loads(path.read_text(encoding="utf-8")) for path in plan_paths]
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in evidence_paths]
    index_path = output / "indexes" / "claims.sqlite"
    index = build_shadow_index(
        index_path=index_path,
        shadow_root=output,
        production_wiki_root=production,
        compile_plans=plans,
        evidence_manifests=manifests,
    )
    summary = {
        "schema_version": 1,
        "rows_seen": state["rows_seen"],
        "papers_compiled": len(plans),
        "papers_failed": state["papers_failed"],
        "failure_types": state["failure_types"],
        "evidence_pages": sum(item["coverage"]["attempted_pages"] for item in manifests),
        "evidence_failed_pages": sum(item["coverage"]["failed_pages"] for item in manifests),
        "evidence_empty_pages": sum(item["coverage"]["empty_pages"] for item in manifests),
        "evidence_chunks": sum(item["coverage"]["chunk_count"] for item in manifests),
        "claims": index["claims"],
        "index_records": index["records"],
        "index_bytes": index["bytes"],
        "source_digest": index["source_digest"],
        "dense_index_digest": index["dense_index_digest"],
        "dense_model_id": None,
    }
    _atomic_json(output / "summary.json", summary)
    _atomic_json(output / "generation.json", {
        "schema_version": 1,
        "generation_id": output.name,
        "status": "ready",
        "index_relpath": "indexes/claims.sqlite",
        "source_digest": index["source_digest"],
        "dense_index_digest": index["dense_index_digest"],
        "dense_model_id": None,
        "index_sha256": _sha256_file(index_path),
        "index_bytes": index_path.stat().st_size,
        "production_eligible": False,
    })
    return summary
