"""Bounded shadow retrieval over signed claims and validated source chunks.

This is deliberately not wired to the production Ask path. Index building
requires validated shadow compile plans and/or evidence manifests, and writes
only below an explicit non-production root. Optional vectors are supplied by
the caller; this module never calls an embedding or model provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote

from services.paper_evidence_service import EvidenceValidationError, validate_evidence_manifest
from services.wiki_claims import ClaimPackageError, validate_claim_catalog
from services.wiki_shadow_compiler import ShadowCompileError, validate_compile_plan


INDEX_SCHEMA_VERSION = 1
INDEX_APPLICATION_ID = "knowra-shadow-claim-retrieval-v1"
PLAN_SCHEMA_VERSION = 1
RRF_K = 60
MAX_ROUTES = 4
MAX_CHANNEL_RESULTS = 20
MAX_FINAL_RESULTS = 10
MAX_QUERY_CHARACTERS = 1_000
MAX_VECTOR_DIMENSION = 4_096
MAX_FILTER_PAPERS = 50
MAX_FILTER_ID_CHARACTERS = 200
MAX_RECORD_ID_CHARACTERS = 128
MAX_RECORD_TITLE_CHARACTERS = 1_000
MAX_FIELD_PATH_CHARACTERS = 500
MAX_DENSE_MODEL_ID_CHARACTERS = 200
MAX_INDEX_TEXT_CHARACTERS = 12_000
MAX_INDEX_RECORDS = 100_000
MAX_RESULT_TEXT_CHARACTERS = 2_000
MAX_RESULT_CHARACTERS = MAX_FINAL_RESULTS * MAX_RESULT_TEXT_CHARACTERS
MAX_LOCATIONS_PER_RECORD = 128
MAX_RECORD_METADATA_CHARACTERS = 20_000
MAX_SERIALIZED_SEARCH_CHARACTERS = 50_000
MAX_SERIALIZED_PLAN_CHARACTERS = 80_000
SEARCH_TIMEOUT_MS = 500
PLAN_TIMEOUT_MS = 2_000
MIN_DENSE_SIMILARITY = 0.35
_TERM_SPLIT = re.compile(r"[^\w一-鿿぀-ヿ]+", re.UNICODE)
_CJK_RUN = re.compile(r"[一-鿿぀-ヿ]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HybridRetrievalError(ValueError):
    """Raised when shadow provenance, an index, or a plan is invalid."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialized_size_with_counter(value: dict[str, Any], counter: dict[str, Any]) -> int:
    """Set an included size counter to the stable JSON fixed point."""
    counter["serialized_characters"] = 0
    for _ in range(8):
        actual = len(_stable_json(value))
        if counter["serialized_characters"] == actual:
            return actual
        counter["serialized_characters"] = actual
    return len(_stable_json(value))


def _record_provenance_characters(record: Mapping[str, Any]) -> int:
    return len(_stable_json({
        "source_signatures": record["source_signatures"],
        "locations": record["locations"],
        "locator": record["locator"],
    }))


def _vector(value: Any, *, field: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise HybridRetrievalError(f"{field} must be a non-empty vector")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise HybridRetrievalError(f"{field} contains a non-numeric value")
    result = [float(item) for item in value]
    if len(result) > MAX_VECTOR_DIMENSION:
        raise HybridRetrievalError(f"{field} exceeds the vector dimension budget")
    if not all(math.isfinite(item) for item in result):
        raise HybridRetrievalError(f"{field} contains a non-finite value")
    if math.sqrt(sum(item * item for item in result)) == 0:
        raise HybridRetrievalError(f"{field} must not be a zero vector")
    return result


def _safe_shadow_target(
    index_path: Path, *, shadow_root: Path, production_wiki_root: Path
) -> tuple[Path, Path]:
    if not all(isinstance(item, Path) for item in (index_path, shadow_root, production_wiki_root)):
        raise HybridRetrievalError("index and root paths must be explicit Paths")
    root = shadow_root.resolve()
    production = production_wiki_root.resolve()
    if root == production or root.is_relative_to(production) or production.is_relative_to(root):
        raise HybridRetrievalError("shadow root must not overlap production Wiki")
    root.mkdir(parents=True, exist_ok=True)
    if shadow_root.is_symlink() or index_path.is_symlink():
        raise HybridRetrievalError("shadow index paths must not be symlinks")
    resolved = index_path.resolve()
    if not resolved.is_relative_to(root):
        raise HybridRetrievalError("shadow index must remain below shadow root")
    cursor = root
    for component in resolved.relative_to(root).parts[:-1]:
        cursor = cursor / component
        if cursor.is_symlink():
            raise HybridRetrievalError("shadow index parent must not be a symlink")
    if resolved == production or resolved.is_relative_to(production):
        raise HybridRetrievalError("shadow index must not overlap production Wiki")
    return resolved, root


def _validated_claim_records(plans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for plan in plans:
        try:
            validate_compile_plan(plan)
        except ShadowCompileError as error:
            raise HybridRetrievalError("compile plan failed validation") from error
        payload = plan.get("bounded_payload")
        claims = payload.get("retrieval_claims") if isinstance(payload, Mapping) else None
        if not isinstance(claims, list):
            raise HybridRetrievalError("compile plan lacks its signed retrieval claim ledger")
        source_by_id = {
            source.get("source_id"): source
            for source in plan["sources"]
            if isinstance(source, Mapping)
        }
        catalogs = plan.get("claim_catalogs")
        if not isinstance(catalogs, list):
            raise HybridRetrievalError("compile plan lacks claim catalogs")
        if plan["kind"] == "paper":
            signed_entity_id = payload.get("paper_id")
            signed_title = payload.get("title")
            identity_entity_id = plan["source_identity"].get("paper_id")
            expected_catalog_signatures = {
                plan["source_identity"].get("claim_catalog_signature")
            }
            paper_titles = {str(signed_entity_id): signed_title}
        else:
            signed_entity_id = payload.get("concept_id")
            signed_title = payload.get("title")
            identity_entity_id = plan["source_identity"].get("concept_id")
            capsules = plan["bounded_payload"].get("source_capsules")
            if not isinstance(capsules, list):
                raise HybridRetrievalError("concept plan lacks signed source capsules")
            expected_catalog_signatures = {
                capsule.get("catalog_signature")
                for capsule in capsules
                if isinstance(capsule, Mapping)
            }
            paper_titles = {
                str(capsule.get("paper_id")): capsule.get("title")
                for capsule in capsules
                if isinstance(capsule, Mapping)
            }
        if (
            not isinstance(signed_entity_id, str)
            or not signed_entity_id.strip()
            or not isinstance(signed_title, str)
            or not signed_title.strip()
            or str(plan.get("entity_id")) != signed_entity_id
            or plan.get("title") != signed_title
            or identity_entity_id != signed_entity_id
        ):
            raise HybridRetrievalError("compile plan identity conflicts with signed payload")
        catalog_claims: dict[str, Mapping[str, Any]] = {}
        actual_catalog_signatures: set[str] = set()
        for catalog in catalogs:
            if not isinstance(catalog, Mapping):
                raise HybridRetrievalError("compile plan claim catalog must be an object")
            try:
                validate_claim_catalog(catalog)
            except ClaimPackageError as error:
                raise HybridRetrievalError("compile plan claim catalog failed validation") from error
            actual_catalog_signatures.add(catalog["catalog_signature"])
            for catalog_claim in catalog["claims"]:
                previous = catalog_claims.get(catalog_claim["id"])
                if previous is not None and previous != catalog_claim:
                    raise HybridRetrievalError("claim catalogs conflict for one claim id")
                catalog_claims[catalog_claim["id"]] = catalog_claim
        if actual_catalog_signatures != expected_catalog_signatures:
            raise HybridRetrievalError("claim catalogs do not match signed compile inputs")
        ledger_ids = {
            claim.get("id") for claim in claims if isinstance(claim, Mapping)
        }
        signed_claim_ids = {
            identifier
            for identifier in source_by_id
            if isinstance(identifier, str) and identifier.startswith("extraction-claim:")
        }
        if ledger_ids != signed_claim_ids:
            raise HybridRetrievalError("retrieval ledger does not match signed claim sources")
        for claim in claims:
            if not isinstance(claim, Mapping):
                raise HybridRetrievalError("retrieval claims must be objects")
            identifier = claim.get("id")
            source = source_by_id.get(identifier)
            if not isinstance(source, Mapping):
                raise HybridRetrievalError("retrieval claim is not present in signed sources")
            catalog_claim = catalog_claims.get(identifier)
            if not isinstance(catalog_claim, Mapping):
                raise HybridRetrievalError("retrieval claim is absent from validated catalogs")
            if claim.get("paper_title") != paper_titles.get(str(claim.get("paper_id"))):
                raise HybridRetrievalError("retrieval claim title conflicts with signed compile input")
            for ledger_key, catalog_key in (
                ("id", "id"),
                ("paper_id", "paper_id"),
                ("field_path", "field_path"),
                ("root_field", "root_field"),
                ("text", "text"),
                ("retained_text_hash", "retained_text_hash"),
                ("source_text_hash", "source_text_hash"),
                ("support_level", "support_level"),
                ("evidence_status", "evidence_status"),
                ("candidate_evidence_ids", "candidate_evidence_ids"),
            ):
                if claim.get(ledger_key) != catalog_claim.get(catalog_key):
                    raise HybridRetrievalError("retrieval claim conflicts with validated catalog")
            text = claim.get("text")
            if (
                not isinstance(identifier, str)
                or not isinstance(text, str)
                or not text.strip()
                or not _SHA256.fullmatch(str(claim.get("retained_text_hash") or ""))
                or _sha256(text) != claim["retained_text_hash"]
            ):
                raise HybridRetrievalError("retrieval claim text or hash is invalid")
            if source.get("text_hash") != claim.get("source_text_hash"):
                raise HybridRetrievalError("retrieval claim does not match signed source metadata")
            for claim_key, source_key in (
                ("paper_id", "paper_id"),
                ("field_path", "field_path"),
                ("support_level", "support_level"),
                ("evidence_status", "evidence_status"),
                ("candidate_evidence_ids", "candidate_evidence_ids"),
            ):
                if claim.get(claim_key) != source.get(source_key):
                    raise HybridRetrievalError("retrieval claim conflicts with signed source metadata")
            if claim.get("support_level") != "extraction_attribution":
                raise HybridRetrievalError("retrieval claim has an unsupported attribution level")
            status = claim.get("evidence_status")
            if status not in {"unavailable", "candidate_evidence"}:
                raise HybridRetrievalError("shadow claims cannot assert verified entailment")
            records.append({
                "id": identifier,
                "paper_id": claim.get("paper_id"),
                "kind": "claim",
                "title": claim.get("paper_title"),
                "text": text,
                "field_path": claim.get("field_path"),
                "support_status": status,
                "source_signatures": [plan["source_signature_v2"]],
                "locations": [{
                    "source_kind": "compile_plan",
                    "plan_kind": plan["kind"],
                    "entity_id": signed_entity_id,
                    "source_signature": plan["source_signature_v2"],
                }],
                "locator": {
                    "field_path": claim.get("field_path"),
                    "candidate_evidence_ids": claim.get("candidate_evidence_ids", []),
                },
            })
    return records


def _validated_evidence_records(manifests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for manifest in manifests:
        if not isinstance(manifest, dict):
            raise HybridRetrievalError("evidence manifest must be an object")
        try:
            validate_evidence_manifest(manifest)
        except EvidenceValidationError as error:
            raise HybridRetrievalError("evidence manifest failed validation") from error
        for chunk in manifest["chunks"]:
            records.append({
                "id": chunk["id"],
                "paper_id": chunk["paper_id"],
                "kind": "evidence",
                "title": chunk.get("section_heading") or f"paper {chunk['paper_id']}",
                "text": chunk["text"],
                "field_path": f"page:{chunk['page_number']}/{chunk['section_kind']}",
                # The bytes and locator are validated; entailment is not.
                "support_status": "source_text",
                "source_signatures": [manifest["manifest_id"]],
                "locations": [{
                    "source_kind": "evidence_manifest",
                    "paper_id": manifest["paper_id"],
                    "source_signature": manifest["manifest_id"],
                }],
                "locator": {
                    "page_number": chunk["page_number"],
                    "page_char_start": chunk["page_char_start"],
                    "page_char_end": chunk["page_char_end"],
                    "section_kind": chunk["section_kind"],
                    "section_heading": chunk["section_heading"],
                    "text_hash": chunk["text_hash"],
                    "manifest_id": manifest["manifest_id"],
                    "source_file_hash": manifest["source"]["file_hash_sha256"],
                },
            })
    return records


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "id", "paper_id", "kind", "title", "text", "field_path",
        "support_status",
    )
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in required):
        raise HybridRetrievalError("index record fields must be non-empty strings")
    if (
        len(record["id"]) > MAX_RECORD_ID_CHARACTERS
        or len(record["paper_id"]) > MAX_FILTER_ID_CHARACTERS
        or len(record["title"]) > MAX_RECORD_TITLE_CHARACTERS
        or len(record["field_path"]) > MAX_FIELD_PATH_CHARACTERS
    ):
        raise HybridRetrievalError("index record metadata exceeds the character budget")
    if record["kind"] not in {"claim", "evidence"}:
        raise HybridRetrievalError("record kind must be claim or evidence")
    if record["support_status"] not in {"unavailable", "candidate_evidence", "source_text"}:
        raise HybridRetrievalError("invalid support status")
    if len(record["text"]) > MAX_INDEX_TEXT_CHARACTERS:
        raise HybridRetrievalError("index record text exceeds the character budget")
    signatures = record.get("source_signatures")
    if (
        not isinstance(signatures, list)
        or not signatures
        or any(not isinstance(item, str) or not item for item in signatures)
    ):
        raise HybridRetrievalError("record source signatures must be non-empty strings")
    locations = record.get("locations")
    if not isinstance(locations, list) or not locations or any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("source_signature"), str)
        or item["source_signature"] not in signatures
        for item in locations
    ):
        raise HybridRetrievalError("record locations must match source signatures")
    if not isinstance(record.get("locator"), Mapping):
        raise HybridRetrievalError("record locator must be an object")
    if (
        len(signatures) > MAX_LOCATIONS_PER_RECORD
        or len(locations) > MAX_LOCATIONS_PER_RECORD
        or _record_provenance_characters(record) > MAX_RECORD_METADATA_CHARACTERS
    ):
        raise HybridRetrievalError("record provenance exceeds the metadata budget")
    return dict(record)


def build_shadow_index(
    *,
    index_path: Path,
    shadow_root: Path,
    production_wiki_root: Path,
    compile_plans: Sequence[Mapping[str, Any]] = (),
    evidence_manifests: Sequence[Mapping[str, Any]] = (),
    vectors_by_id: Mapping[str, list[float]] | None = None,
    dense_model_id: str | None = None,
) -> dict[str, Any]:
    """Atomically build a provenance-checked, explicitly shadow-only index."""
    path, root = _safe_shadow_target(
        index_path, shadow_root=shadow_root, production_wiki_root=production_wiki_root
    )
    records = [
        _validate_record(record)
        for record in _validated_claim_records(compile_plans) + _validated_evidence_records(evidence_manifests)
    ]
    if not records:
        raise HybridRetrievalError("shadow index requires at least one validated record")
    if len(records) > MAX_INDEX_RECORDS:
        raise HybridRetrievalError("shadow index exceeds the record budget")
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        previous = by_id.get(record["id"])
        if previous is None:
            by_id[record["id"]] = record
            continue
        location_fields = {"source_signatures", "locations"}
        comparable_previous = {key: value for key, value in previous.items() if key not in location_fields}
        comparable_record = {key: value for key, value in record.items() if key not in location_fields}
        if comparable_previous != comparable_record:
            raise HybridRetrievalError("record id has conflicting provenance")
        previous["source_signatures"] = sorted(set(
            previous["source_signatures"] + record["source_signatures"]
        ))
        previous["locations"] = sorted(
            { _stable_json(item): item for item in previous["locations"] + record["locations"] }.values(),
            key=_stable_json,
        )
        if (
            len(previous["source_signatures"]) > MAX_LOCATIONS_PER_RECORD
            or len(previous["locations"]) > MAX_LOCATIONS_PER_RECORD
            or _record_provenance_characters(previous) > MAX_RECORD_METADATA_CHARACTERS
        ):
            raise HybridRetrievalError("canonical claim provenance exceeds its budget")
    supplied_vectors = dict(vectors_by_id or {})
    if set(supplied_vectors) - set(by_id):
        raise HybridRetrievalError("a vector references an unknown record")
    vectors = {
        identifier: _vector(value, field=f"vectors_by_id[{identifier}]")
        for identifier, value in supplied_vectors.items()
    }
    dimensions = {len(vector) for vector in vectors.values() if vector is not None}
    if len(dimensions) > 1:
        raise HybridRetrievalError("dense vectors must share one dimension")
    if vectors and (
        not isinstance(dense_model_id, str)
        or len(dense_model_id) > MAX_DENSE_MODEL_ID_CHARACTERS
        or not re.fullmatch(r"[A-Za-z0-9._/-]+@[A-Za-z0-9._/-]+", dense_model_id)
    ):
        raise HybridRetrievalError("dense vectors require an explicit model and revision")
    if not vectors and dense_model_id is not None:
        raise HybridRetrievalError("dense model metadata requires vectors")

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(root):
        raise HybridRetrievalError("shadow index parent escaped its root")
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    temporary = Path(name)
    started = perf_counter()
    try:
        conn = sqlite3.connect(temporary)
        try:
            with conn:
                conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "CREATE TABLE records (id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, "
                    "kind TEXT NOT NULL, support_status TEXT NOT NULL, title TEXT NOT NULL, "
                    "text TEXT NOT NULL, field_path TEXT NOT NULL, source_signatures TEXT NOT NULL, "
                    "locations TEXT NOT NULL, locator TEXT NOT NULL, vector TEXT)"
                )
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE search USING fts5("
                        "id UNINDEXED, title, text, field_path, tokenize='trigram')"
                    )
                    tokenizer = "trigram"
                except sqlite3.OperationalError:
                    conn.execute(
                        "CREATE VIRTUAL TABLE search USING fts5("
                        "id UNINDEXED, title, text, field_path, tokenize='unicode61')"
                    )
                    tokenizer = "unicode61"
                source_digest = _sha256(_stable_json([
                    {key: value for key, value in record.items() if key != "text"}
                    | {"text_hash": _sha256(record["text"])}
                    for record in sorted(by_id.values(), key=lambda item: item["id"])
                ]))
                dense_index_digest = _sha256(_stable_json({
                    "source_digest": source_digest,
                    "dense_model_id": dense_model_id or "unavailable",
                    "dimension": next(iter(dimensions), 0),
                    "minimum_similarity": MIN_DENSE_SIMILARITY,
                    "tokenizer": tokenizer,
                    "index_schema_version": INDEX_SCHEMA_VERSION,
                    "vectors": {
                        identifier: _sha256(_stable_json(vector))
                        for identifier, vector in sorted(vectors.items())
                    },
                }))
                metadata = {
                    "application_id": INDEX_APPLICATION_ID,
                    "schema_version": str(INDEX_SCHEMA_VERSION),
                    "tokenizer": tokenizer,
                    "vector_dimension": str(next(iter(dimensions), 0)),
                    "dense_model_id": dense_model_id.strip() if dense_model_id else "unavailable",
                    "source_digest": source_digest,
                    "dense_index_digest": dense_index_digest,
                }
                conn.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
                for record in sorted(by_id.values(), key=lambda item: item["id"]):
                    vector = vectors.get(record["id"])
                    conn.execute(
                        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            record["id"], record["paper_id"], record["kind"], record["support_status"],
                            record["title"], record["text"], record["field_path"], _stable_json(record["source_signatures"]),
                            _stable_json(record["locations"]), _stable_json(record["locator"]),
                            _stable_json(vector) if vector is not None else None,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO search VALUES (?,?,?,?)",
                        (record["id"], record["title"], record["text"], record["field_path"]),
                    )
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise HybridRetrievalError("shadow index integrity check failed")
        finally:
            conn.close()
        if path.exists() and path.is_symlink():
            raise HybridRetrievalError("shadow index target became a symlink")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "records": len(by_id),
        "claims": sum(record["kind"] == "claim" for record in by_id.values()),
        "evidence_chunks": sum(record["kind"] == "evidence" for record in by_id.values()),
        "vector_dimension": next(iter(dimensions), None),
        "dense_model_id": dense_model_id,
        "bytes": path.stat().st_size,
        "duration_ms": round((perf_counter() - started) * 1_000, 3),
        "source_digest": source_digest,
        "dense_index_digest": dense_index_digest,
    }


def _lexical_terms(query: str, *, tokenizer: str) -> list[str]:
    parts = [part for part in _TERM_SPLIT.split(query) if part.strip()]
    terms: list[str] = []
    for part in parts:
        if tokenizer == "trigram" and _CJK_RUN.fullmatch(part):
            width = 3 if len(part) >= 3 else len(part)
            if width:
                terms.extend(part[index:index + width] for index in range(len(part) - width + 1))
        else:
            terms.append(part)
    return list(dict.fromkeys(term for term in terms if term.strip()))[:48]


def _fts_query(query: str, *, tokenizer: str) -> str:
    """Build a bounded high-recall lexical query for Latin and CJK text.

    FTS5's trigram tokenizer can index continuous Chinese text, but quoting an
    entire natural-language question turns it into an accidental exact-phrase
    search.  Generate language-agnostic CJK trigrams instead; BM25 rewards
    records matching several terms, while the answer evidence gate remains
    responsible for support.  The unicode61 fallback retains its prior token
    behavior because it cannot match arbitrary CJK substrings.
    """
    unique = _lexical_terms(query, tokenizer=tokenizer)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise HybridRetrievalError("query vector dimension mismatch")
    return sum(a * b for a, b in zip(left, right)) / (
        math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    )


def _normalized_set(value: Any, *, allowed: set[str], field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)) or any(item not in allowed for item in value):
        raise HybridRetrievalError(f"{field} contains an unsupported value")
    return sorted(set(value))


def _read_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=0)
    try:
        conn.execute("PRAGMA query_only=ON")
        metadata = dict(conn.execute("SELECT key,value FROM metadata"))
    except sqlite3.DatabaseError as error:
        conn.close()
        raise HybridRetrievalError("not a shadow retrieval index") from error
    if metadata.get("application_id") != INDEX_APPLICATION_ID or metadata.get("schema_version") != str(INDEX_SCHEMA_VERSION):
        conn.close()
        raise HybridRetrievalError("not a compatible shadow retrieval index")
    return conn


def _safe_shadow_read_path(
    index_path: Path, *, shadow_root: Path, production_wiki_root: Path
) -> Path:
    if not all(isinstance(item, Path) for item in (index_path, shadow_root, production_wiki_root)):
        raise HybridRetrievalError("index and root paths must be explicit Paths")
    if not shadow_root.is_dir() or shadow_root.is_symlink():
        raise HybridRetrievalError("shadow read root must be an existing physical directory")
    root = shadow_root.resolve()
    production = production_wiki_root.resolve()
    path = index_path.resolve()
    if (
        root == production
        or root.is_relative_to(production)
        or production.is_relative_to(root)
        or not path.is_relative_to(root)
        or path == production
        or path.is_relative_to(production)
    ):
        raise HybridRetrievalError("shadow read path crosses the production boundary")
    cursor = root
    for component in path.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise HybridRetrievalError("shadow read path contains a symlink")
    return path


class HybridClaimRetriever:
    def __init__(
        self,
        index_path: Path,
        *,
        shadow_root: Path,
        production_wiki_root: Path,
        expected_source_digest: str | None = None,
        expected_dense_index_digest: str | None = None,
    ):
        safe_path = _safe_shadow_read_path(
            index_path,
            shadow_root=shadow_root,
            production_wiki_root=production_wiki_root,
        )
        if not safe_path.is_file() or safe_path.is_symlink():
            raise HybridRetrievalError("shadow index does not exist or is unsafe")
        self.index_path = safe_path
        self.shadow_root = shadow_root.resolve()
        self.production_wiki_root = production_wiki_root.resolve()
        conn = _read_connection(self.index_path)
        try:
            metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            self.vector_dimension = int(metadata["vector_dimension"])
            self.dense_model_id = metadata["dense_model_id"]
            self.tokenizer = metadata["tokenizer"]
            self.source_digest = metadata["source_digest"]
            self.dense_index_digest = metadata["dense_index_digest"]
            if expected_source_digest is not None and expected_source_digest != self.source_digest:
                raise HybridRetrievalError("shadow retrieval corpus signature is stale")
            if (
                expected_dense_index_digest is not None
                and expected_dense_index_digest != self.dense_index_digest
            ):
                raise HybridRetrievalError("shadow dense index signature is stale")
        finally:
            conn.close()

    def search(
        self,
        query: str,
        *,
        query_vector: list[float] | None = None,
        dense_model_id: str | None = None,
        kinds: Sequence[str] | None = None,
        support_statuses: Sequence[str] | None = None,
        paper_ids: Sequence[str] | None = None,
        limit: int = 5,
        timeout_ms: int = SEARCH_TIMEOUT_MS,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARACTERS:
            raise HybridRetrievalError("query must be non-empty and bounded")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FINAL_RESULTS:
            raise HybridRetrievalError("limit exceeds retrieval budget")
        dense_query = _vector(query_vector, field="query_vector")
        if dense_query is not None and len(dense_query) != self.vector_dimension:
            raise HybridRetrievalError("query vector dimension mismatch")
        if dense_query is not None and dense_model_id != self.dense_model_id:
            raise HybridRetrievalError("query vector model does not match the index")
        if dense_query is None and dense_model_id is not None:
            raise HybridRetrievalError("dense model metadata requires a query vector")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= SEARCH_TIMEOUT_MS:
            raise HybridRetrievalError("search timeout exceeds the latency budget")
        kind_filter = _normalized_set(kinds, allowed={"claim", "evidence"}, field="kinds")
        status_filter = _normalized_set(
            support_statuses,
            allowed={"unavailable", "candidate_evidence", "source_text"},
            field="support_statuses",
        )
        if paper_ids is None:
            paper_filter: list[str] = []
        elif not isinstance(paper_ids, (list, tuple, set)) or any(
            not isinstance(item, str) or not item.strip() for item in paper_ids
        ):
            raise HybridRetrievalError("paper_ids must contain non-empty strings")
        else:
            paper_filter = sorted(set(paper_ids))
        if (
            len(paper_filter) > MAX_FILTER_PAPERS
            or any(len(identifier) > MAX_FILTER_ID_CHARACTERS for identifier in paper_filter)
        ):
            raise HybridRetrievalError("paper_ids exceed the filter budget")

        current_path = _safe_shadow_read_path(
            self.index_path,
            shadow_root=self.shadow_root,
            production_wiki_root=self.production_wiki_root,
        )
        conn = _read_connection(current_path)
        started = perf_counter()
        deadline = started + timeout_ms / 1_000
        conn.set_progress_handler(lambda: 1 if perf_counter() >= deadline else 0, 1_000)
        try:
            current_metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            if (
                current_metadata.get("source_digest") != self.source_digest
                or current_metadata.get("dense_index_digest") != self.dense_index_digest
                or current_metadata.get("dense_model_id") != self.dense_model_id
                or int(current_metadata.get("vector_dimension", "-1")) != self.vector_dimension
            ):
                raise HybridRetrievalError("shadow index changed after retriever initialization")
            conditions: list[str] = []
            parameters: list[Any] = []
            for column, values in (
                ("r.kind", kind_filter), ("r.support_status", status_filter), ("r.paper_id", paper_filter)
            ):
                if values:
                    conditions.append(f"{column} IN ({','.join('?' for _ in values)})")
                    parameters.extend(values)
            where = (" AND " + " AND ".join(conditions)) if conditions else ""
            lexical: list[str] = []
            safe = _fts_query(query, tokenizer=self.tokenizer)
            if safe:
                rows = conn.execute(
                    "SELECT s.id FROM search s JOIN records r ON r.id=s.id "
                    f"WHERE search MATCH ?{where} "
                    "ORDER BY bm25(search,0.0,5.0,1.0,2.0),s.id LIMIT ?",
                    [safe, *parameters, MAX_CHANNEL_RESULTS],
                ).fetchall()
                lexical = [row[0] for row in rows]
            rows = conn.execute(
                "SELECT id,paper_id,kind,support_status,title,text,field_path,source_signatures,locations,locator,vector "
                "FROM records r" + ((" WHERE " + " AND ".join(conditions)) if conditions else ""),
                parameters,
            ).fetchall()
            payloads: dict[str, dict[str, Any]] = {}
            dense_scores: list[tuple[str, float]] = []
            for row in rows:
                if perf_counter() >= deadline:
                    raise HybridRetrievalError("search exceeded the latency budget")
                try:
                    record = _validate_record({
                        "id": row[0], "paper_id": row[1], "kind": row[2],
                        "support_status": row[3], "title": row[4], "text": row[5],
                        "field_path": row[6],
                        "source_signatures": json.loads(row[7]),
                        "locations": json.loads(row[8]),
                        "locator": json.loads(row[9]),
                    })
                except (json.JSONDecodeError, TypeError) as error:
                    raise HybridRetrievalError(
                        "shadow index record is malformed"
                    ) from error
                full_text = record["text"]
                payloads[row[0]] = {
                    "id": record["id"], "paper_id": record["paper_id"],
                    "kind": record["kind"], "support_status": record["support_status"],
                    "title": record["title"],
                    "text": full_text[:MAX_RESULT_TEXT_CHARACTERS],
                    "text_hash": _sha256(full_text),
                    "text_characters": len(full_text),
                    "text_truncated": len(full_text) > MAX_RESULT_TEXT_CHARACTERS,
                    "field_path": record["field_path"],
                    "source_signatures": record["source_signatures"],
                    "locations": record["locations"],
                    "locator": record["locator"],
                }
                stored_vector = None
                if row[10] is not None:
                    try:
                        stored_vector = _vector(
                            json.loads(row[10]), field="stored vector"
                        )
                    except (json.JSONDecodeError, TypeError) as error:
                        raise HybridRetrievalError(
                            "shadow index vector is malformed"
                        ) from error
                    if stored_vector is None:
                        raise HybridRetrievalError("shadow index vector is missing")
                    if len(stored_vector) != self.vector_dimension:
                        raise HybridRetrievalError(
                            "stored vector dimension does not match the index"
                        )
                if dense_query is not None and stored_vector is not None:
                    score = _cosine(dense_query, stored_vector)
                    if score >= MIN_DENSE_SIMILARITY:
                        dense_scores.append((row[0], score))
            dense_scores.sort(key=lambda item: (-item[1], item[0]))
            lexical_terms = _lexical_terms(query, tokenizer=self.tokenizer)
            minimum_matches = 2 if len(lexical_terms) >= 2 and any(
                _CJK_RUN.search(term) for term in lexical_terms
            ) else 1
            lexical = [
                identifier for identifier in lexical
                if sum(
                    term.casefold() in (
                        payloads[identifier]["title"] + " " + payloads[identifier]["text"]
                    ).casefold()
                    for term in lexical_terms
                ) >= minimum_matches
            ]
            dense_scores = dense_scores[:MAX_CHANNEL_RESULTS]
            fused: dict[str, dict[str, Any]] = {}
            for channel, ranked in (
                ("lexical", [(identifier, None) for identifier in lexical]), ("dense", dense_scores)
            ):
                for rank, (identifier, raw_score) in enumerate(ranked, start=1):
                    entry = fused.setdefault(
                        identifier,
                        {**payloads[identifier], "score": 0.0, "channels": [], "channel_ranks": {}},
                    )
                    entry["score"] += 1 / (RRF_K + rank)
                    entry["channels"].append(channel)
                    entry["channel_ranks"][channel] = rank
                    if raw_score is not None:
                        entry["dense_similarity"] = round(raw_score, 6)
            ranked_results = sorted(fused.values(), key=lambda item: (-item["score"], item["id"]))[:limit]
            for item in ranked_results:
                item["score"] = round(item["score"], 9)
            response = {
                "query": query,
                "channels_used": (["lexical"] if lexical else []) + (["dense"] if dense_scores else []),
                "results": ranked_results,
                "lexical_candidates": len(lexical),
                "dense_candidates": len(dense_scores),
                "dense_status": "available" if self.vector_dimension else "unavailable",
                "dense_model_id": self.dense_model_id,
                "corpus_signature": self.source_digest,
                "dense_index_signature": self.dense_index_digest,
                "result_characters": sum(len(item["text"]) for item in ranked_results),
                "elapsed_ms": round((perf_counter() - started) * 1_000, 3),
            }
            actual_size = _serialized_size_with_counter(response, response)
            if actual_size > MAX_SERIALIZED_SEARCH_CHARACTERS:
                raise HybridRetrievalError("search response exceeded the serialized output budget")
            return response
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error).lower():
                raise HybridRetrievalError("search exceeded the latency budget") from error
            raise
        finally:
            conn.close()

    def paper_years(self, paper_ids: Sequence[str]) -> dict[str, int]:
        """Return bounded structured-extraction years for candidate papers."""
        normalized = sorted(set(str(item).strip() for item in paper_ids if str(item).strip()))
        if len(normalized) > MAX_FILTER_PAPERS:
            raise HybridRetrievalError("paper year lookup exceeds the filter budget")
        if not normalized:
            return {}
        current_path = _safe_shadow_read_path(
            self.index_path,
            shadow_root=self.shadow_root,
            production_wiki_root=self.production_wiki_root,
        )
        conn = _read_connection(current_path)
        try:
            metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            if (
                metadata.get("source_digest") != self.source_digest
                or metadata.get("dense_index_digest") != self.dense_index_digest
                or metadata.get("dense_model_id") != self.dense_model_id
                or int(metadata.get("vector_dimension", "-1")) != self.vector_dimension
            ):
                raise HybridRetrievalError("shadow index changed after retriever initialization")
            rows = conn.execute(
                "SELECT paper_id,text FROM records WHERE kind='claim' AND field_path='year' "
                f"AND paper_id IN ({','.join('?' for _ in normalized)}) ORDER BY paper_id,id",
                normalized,
            ).fetchall()
        finally:
            conn.close()
        years: dict[str, int] = {}
        for paper_id, text in rows:
            match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(text))
            if match and paper_id not in years:
                years[paper_id] = int(match.group())
        return years


def _normalize_route(route: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(route, Mapping):
        raise HybridRetrievalError(f"{field} must be an object")
    query = route.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARACTERS:
        raise HybridRetrievalError(f"{field} query is invalid")
    kinds = _normalized_set(route.get("kinds"), allowed={"claim", "evidence"}, field=f"{field}.kinds")
    statuses = _normalized_set(
        route.get("support_statuses"),
        allowed={"unavailable", "candidate_evidence", "source_text"},
        field=f"{field}.support_statuses",
    )
    paper_ids = route.get("paper_ids") or []
    if not isinstance(paper_ids, (list, tuple, set)) or any(
        not isinstance(item, str) or not item.strip() for item in paper_ids
    ):
        raise HybridRetrievalError(f"{field}.paper_ids is invalid")
    if (
        len(set(paper_ids)) > MAX_FILTER_PAPERS
        or any(len(identifier) > MAX_FILTER_ID_CHARACTERS for identifier in paper_ids)
    ):
        raise HybridRetrievalError(f"{field}.paper_ids exceed the filter budget")
    query_vector = _vector(route.get("query_vector"), field=f"{field}.query_vector")
    dense_model_id = route.get("dense_model_id")
    if query_vector is not None and (
        not isinstance(dense_model_id, str) or not dense_model_id.strip()
    ):
        raise HybridRetrievalError(f"{field} query vector requires its model id")
    if query_vector is None and dense_model_id is not None:
        raise HybridRetrievalError(f"{field} model id requires a query vector")
    return {
        "query": query.strip(),
        "query_vector": query_vector,
        "dense_model_id": dense_model_id.strip() if dense_model_id else None,
        "kinds": kinds,
        "support_statuses": statuses,
        "paper_ids": sorted(set(paper_ids)),
    }


def validate_retrieval_plan(plan: Mapping[str, Any]) -> None:
    if not isinstance(plan, Mapping) or plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise HybridRetrievalError("unsupported retrieval plan")
    required_keys = {
        "schema_version", "question", "routes", "gap_route", "required_papers",
        "required_source_records", "limit", "budgets", "plan_id",
    }
    if set(plan) != required_keys:
        raise HybridRetrievalError("retrieval plan shape is invalid")
    rebuilt = build_retrieval_plan(
        question=plan["question"], routes=plan["routes"], gap_route=plan["gap_route"],
        required_papers=plan["required_papers"], required_source_records=plan["required_source_records"],
        limit=plan["limit"],
    )
    if rebuilt != dict(plan):
        raise HybridRetrievalError("retrieval plan was altered")


def build_retrieval_plan(
    *,
    question: str,
    routes: Sequence[Mapping[str, Any]],
    gap_route: Mapping[str, Any] | None = None,
    required_papers: int = 1,
    required_source_records: int = 1,
    limit: int = 5,
) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUERY_CHARACTERS:
        raise HybridRetrievalError("invalid retrieval question")
    if (
        isinstance(required_papers, bool) or not isinstance(required_papers, int)
        or not 1 <= required_papers <= MAX_FINAL_RESULTS
        or isinstance(required_source_records, bool) or not isinstance(required_source_records, int)
        or not 0 <= required_source_records <= MAX_FINAL_RESULTS
        or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FINAL_RESULTS
        or required_papers > limit or required_source_records > limit
    ):
        raise HybridRetrievalError("invalid retrieval thresholds")
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)) or not 1 <= len(routes) <= MAX_ROUTES:
        raise HybridRetrievalError("route count exceeds retrieval budget")
    normalized = [_normalize_route(route, field=f"routes[{index}]") for index, route in enumerate(routes)]
    route_identities = [_stable_json(route) for route in normalized]
    if len(route_identities) != len(set(route_identities)):
        raise HybridRetrievalError("retrieval routes must be unique")
    normalized_gap = _normalize_route(gap_route, field="gap_route") if gap_route is not None else None
    if normalized_gap is not None and _stable_json(normalized_gap) in set(route_identities):
        raise HybridRetrievalError("gap route must add a distinct search")
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "question": question.strip(),
        "routes": normalized,
        "gap_route": normalized_gap,
        "required_papers": required_papers,
        "required_source_records": required_source_records,
        "limit": limit,
        "budgets": {
            "max_primary_routes": MAX_ROUTES,
            "max_gap_routes": 1,
            "max_channel_results": MAX_CHANNEL_RESULTS,
            "max_final_results": MAX_FINAL_RESULTS,
        },
    }
    plan["plan_id"] = _sha256(_stable_json(plan))
    return plan


def execute_retrieval_plan(retriever: HybridClaimRetriever, plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_retrieval_plan(plan)
    started = perf_counter()
    aggregate: dict[str, dict[str, Any]] = {}
    calls = 0
    route_traces: list[dict[str, Any]] = []
    deadline = started + PLAN_TIMEOUT_MS / 1_000

    def select_results() -> list[dict[str, Any]]:
        ranked = sorted(
            aggregate.values(), key=lambda item: (-item["plan_score"], item["id"])
        )
        selected_ids: set[str] = set()
        selected: list[dict[str, Any]] = []

        def add(item: dict[str, Any]) -> None:
            if item["id"] not in selected_ids and len(selected) < plan["limit"]:
                selected_ids.add(item["id"])
                selected.append(item)

        # Jointly maximize unmet source and paper coverage. The candidate pool
        # is bounded to at most five routes × ten results, so this deterministic
        # greedy pass has a small fixed worst case.
        while len(selected) < plan["limit"]:
            covered_papers = {item["paper_id"] for item in selected}
            source_count = sum(
                item["support_status"] == "source_text" for item in selected
            )
            if (
                len(covered_papers) >= plan["required_papers"]
                and source_count >= plan["required_source_records"]
            ):
                break
            candidates = [item for item in ranked if item["id"] not in selected_ids]
            if not candidates:
                break
            def coverage_gain(item: Mapping[str, Any]) -> tuple[int, int, int]:
                source_gain = int(
                    source_count < plan["required_source_records"]
                    and item["support_status"] == "source_text"
                )
                paper_gain = int(
                    len(covered_papers) < plan["required_papers"]
                    and item["paper_id"] not in covered_papers
                )
                return (source_gain + paper_gain, paper_gain, source_gain)
            best = max(
                enumerate(candidates),
                key=lambda pair: (*coverage_gain(pair[1]), -ranked.index(pair[1])),
            )[1]
            if coverage_gain(best)[0] == 0:
                break
            add(best)
        for item in ranked:
            add(item)
        selected.sort(key=lambda item: (-item["plan_score"], item["id"]))
        return selected

    def coverage(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        paper_count = len({item["paper_id"] for item in results})
        source_count = sum(item["support_status"] == "source_text" for item in results)
        unmet = []
        if paper_count < plan["required_papers"]:
            unmet.append("independent_papers")
        if source_count < plan["required_source_records"]:
            unmet.append("source_text_records")
        return {
            "papers": paper_count,
            "required_papers": plan["required_papers"],
            "source_text_records": source_count,
            "required_source_text_records": plan["required_source_records"],
            "met": not unmet,
            "unmet": unmet,
        }

    def run(route: Mapping[str, Any], name: str) -> None:
        nonlocal calls
        remaining_ms = int((deadline - perf_counter()) * 1_000)
        if remaining_ms < 1:
            raise HybridRetrievalError("retrieval plan exceeded the latency budget")
        result = retriever.search(
            route["query"], query_vector=route["query_vector"],
            dense_model_id=route["dense_model_id"], kinds=route["kinds"],
            support_statuses=route["support_statuses"], paper_ids=route["paper_ids"], limit=plan["limit"],
            timeout_ms=min(SEARCH_TIMEOUT_MS, remaining_ms),
        )
        calls += 1
        route_traces.append({
            "name": name,
            "query": result["query"],
            "channels_used": result["channels_used"],
            "lexical_candidates": result["lexical_candidates"],
            "dense_candidates": result["dense_candidates"],
            "dense_status": result["dense_status"],
            "dense_model_id": result["dense_model_id"],
            "corpus_signature": result["corpus_signature"],
            "dense_index_signature": result["dense_index_signature"],
            "result_characters": result["result_characters"],
            "elapsed_ms": result["elapsed_ms"],
            "results": [{
                "id": item["id"],
                "paper_id": item["paper_id"],
                "kind": item["kind"],
                "support_status": item["support_status"],
                "score": item["score"],
                "channels": item["channels"],
                "channel_ranks": item["channel_ranks"],
            } for item in result["results"]],
        })
        for rank, item in enumerate(result["results"], start=1):
            entry = aggregate.setdefault(item["id"], {**item, "plan_score": 0.0, "routes": []})
            # Each route contributes at most once to a stable record id.
            # Query variants do not earn repeated votes for the same record.
            entry["plan_score"] = max(entry["plan_score"], 1 / (RRF_K + rank))
            entry["routes"].append(name)

    for index, route in enumerate(plan["routes"]):
        run(route, f"route:{index}")
    used_gap = False
    provisional_coverage = coverage(select_results())
    if plan["gap_route"] is not None and not provisional_coverage["met"]:
        run(plan["gap_route"], "gap:0")
        used_gap = True
    results = select_results()
    for item in results:
        item["plan_score"] = round(item["plan_score"], 9)
    final_coverage = coverage(results)
    result_characters = sum(len(item["text"]) for item in results)
    if result_characters > MAX_RESULT_CHARACTERS:
        raise HybridRetrievalError("retrieval output exceeded the character budget")
    response = {
        "plan_id": plan["plan_id"],
        "results": results,
        "routes": route_traces,
        "budget": {
            "search_calls": calls, "gap_used": used_gap,
            "max_search_calls": MAX_ROUTES + 1,
            "result_characters": result_characters,
            "max_result_characters": MAX_RESULT_CHARACTERS,
            "max_elapsed_ms": PLAN_TIMEOUT_MS,
            "elapsed_ms": round((perf_counter() - started) * 1_000, 3),
        },
        "coverage": final_coverage,
        "corpus_signature": retriever.source_digest,
        "dense_model_id": retriever.dense_model_id,
        "dense_index_signature": retriever.dense_index_digest,
        "answerable": False,
        "answerability_reason": (
            "retrieval_candidates_only" if final_coverage["met"]
            else "retrieval_coverage_incomplete"
        ),
        "evidence_semantics": (
            "source_text verifies bytes and locator only; claim candidate status is preserved; "
            "retrieval never asserts entailment"
        ),
    }
    response["budget"]["max_serialized_characters"] = MAX_SERIALIZED_PLAN_CHARACTERS
    actual_size = _serialized_size_with_counter(response, response["budget"])
    if actual_size > MAX_SERIALIZED_PLAN_CHARACTERS:
        raise HybridRetrievalError("retrieval plan output exceeded the serialized budget")
    return response


def execute_cli_plan(retriever: HybridClaimRetriever, plan: Mapping[str, Any]) -> dict[str, Any]:
    return execute_retrieval_plan(retriever, plan)


def execute_api_plan(retriever: HybridClaimRetriever, plan: Mapping[str, Any]) -> dict[str, Any]:
    return execute_retrieval_plan(retriever, plan)
