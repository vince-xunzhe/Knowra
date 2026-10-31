"""Default-off production bridge for a validated Ask shadow generation.

The bridge is observation-only: it never builds an index, writes a generation,
calls a model/provider, or changes the user-visible answer/citations.  A caller
must explicitly select ``observe`` and an absolute generation root outside the
live data tree.  Candidate records remain retrieval candidates, never evidence
of entailment or production eligibility.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from services.hybrid_claim_retrieval import (
    HybridClaimRetriever,
    HybridRetrievalError,
    PLAN_TIMEOUT_MS,
    build_retrieval_plan,
    execute_api_plan,
    execute_cli_plan,
)


GENERATION_SCHEMA_VERSION = 1
MAX_GENERATION_MANIFEST_BYTES = 32_000
MAX_SHADOW_INDEX_BYTES = 1_073_741_824
MAX_OBSERVATION_MS = 5_000
MAX_OBSERVATION_CANDIDATES = 6
MAX_OBSERVATION_CHARACTERS = 24_000
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MULTI_SOURCE_MARKERS = (
    "比较", "对比", "演进", "变化", "经历", "跨论文", "多篇",
    "compare", "comparison", "evolution", "changes", "across papers",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_DATA_ROOT = PROJECT_ROOT / "data"
LIVE_WIKI_ROOT = LIVE_DATA_ROOT / "wiki"


class AskShadowIntegrationError(ValueError):
    """A generation cannot be observed without crossing a safety boundary."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _disabled() -> dict[str, Any]:
    return {
        "schema_version": GENERATION_SCHEMA_VERSION,
        "status": "disabled",
        "mode": "off",
        "production_eligible": False,
        "generation_id": None,
        "candidates": [],
        "candidate_locators": [],
        "coverage": None,
        "budget": None,
        "reason": "shadow_mode_off",
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "schema_version": GENERATION_SCHEMA_VERSION,
        "status": "unavailable",
        "mode": "observe",
        "production_eligible": False,
        "generation_id": None,
        "candidates": [],
        "candidate_locators": [],
        "coverage": None,
        "budget": None,
        "reason": reason,
    }


def _safe_generation_root(raw_root: str, *, live_data_root: Path) -> Path:
    candidate = Path(raw_root)
    if not candidate.is_absolute():
        raise AskShadowIntegrationError("generation root must be absolute")
    cursor = Path(candidate.anchor)
    try:
        for component in candidate.parts[1:]:
            cursor = cursor / component
            if stat.S_ISLNK(os.lstat(cursor).st_mode):
                raise AskShadowIntegrationError(
                    "generation root must not contain a symlink"
                )
    except OSError as error:
        raise AskShadowIntegrationError("generation root is unavailable") from error
    try:
        root = candidate.resolve(strict=True)
        live = live_data_root.resolve(strict=True)
    except OSError as error:
        raise AskShadowIntegrationError("generation or live data root is unavailable") from error
    if not root.is_dir():
        raise AskShadowIntegrationError("generation root must be a directory")
    if root == live or root.is_relative_to(live) or live.is_relative_to(root):
        raise AskShadowIntegrationError("generation root overlaps live data")
    return root


def _load_manifest(root: Path) -> tuple[dict[str, Any], Path]:
    path = root / "generation.json"
    if path.is_symlink() or not path.is_file():
        raise AskShadowIntegrationError("generation manifest is missing or linked")
    size = path.stat().st_size
    if size < 2 or size > MAX_GENERATION_MANIFEST_BYTES:
        raise AskShadowIntegrationError("generation manifest exceeds its budget")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AskShadowIntegrationError("generation manifest is unreadable") from error
    required = {
        "schema_version", "generation_id", "status", "index_relpath",
        "source_digest", "dense_index_digest", "dense_model_id",
        "index_sha256", "index_bytes", "production_eligible",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise AskShadowIntegrationError("generation manifest shape is invalid")
    if manifest.get("schema_version") != GENERATION_SCHEMA_VERSION:
        raise AskShadowIntegrationError("generation manifest schema is unsupported")
    generation_id = manifest.get("generation_id")
    if not isinstance(generation_id, str) or not _GENERATION_ID.fullmatch(generation_id):
        raise AskShadowIntegrationError("generation id is invalid")
    if manifest.get("status") != "ready" or manifest.get("production_eligible") is not False:
        raise AskShadowIntegrationError("generation is not a ready shadow artifact")
    for field in ("source_digest", "dense_index_digest"):
        value = manifest.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise AskShadowIntegrationError("generation digest is invalid")
    index_sha256 = manifest.get("index_sha256")
    index_bytes = manifest.get("index_bytes")
    if not isinstance(index_sha256, str) or not _SHA256.fullmatch(index_sha256):
        raise AskShadowIntegrationError("generation index digest is invalid")
    if (
        isinstance(index_bytes, bool)
        or not isinstance(index_bytes, int)
        or not 1 <= index_bytes <= MAX_SHADOW_INDEX_BYTES
    ):
        raise AskShadowIntegrationError("generation index size is invalid")
    dense_model_id = manifest.get("dense_model_id")
    if dense_model_id is not None and (
        not isinstance(dense_model_id, str)
        or "@" not in dense_model_id
        or len(dense_model_id) > 200
    ):
        raise AskShadowIntegrationError("generation dense model id is invalid")
    relpath = manifest.get("index_relpath")
    if not isinstance(relpath, str) or not relpath or Path(relpath).is_absolute():
        raise AskShadowIntegrationError("generation index path is invalid")
    if ".." in Path(relpath).parts:
        raise AskShadowIntegrationError("generation index path contains traversal")
    raw_index_path = root / relpath
    cursor = root
    try:
        for component in Path(relpath).parts:
            cursor = cursor / component
            if stat.S_ISLNK(os.lstat(cursor).st_mode):
                raise AskShadowIntegrationError(
                    "generation index path must not contain a symlink"
                )
    except OSError as error:
        raise AskShadowIntegrationError("generation index is unavailable") from error
    index_path = raw_index_path.resolve(strict=True)
    if not index_path.is_relative_to(root) or not index_path.is_file():
        raise AskShadowIntegrationError("generation index escaped its root or is unavailable")
    return manifest, index_path


def _index_identity(path: Path, *, deadline: float) -> dict[str, Any]:
    """Hash one bounded physical index and detect changes during the read."""
    before = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode)
        or not 1 <= before.st_size <= MAX_SHADOW_INDEX_BYTES
    ):
        raise AskShadowIntegrationError("generation index is not a bounded file")
    digest = hashlib.sha256()
    read_bytes = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if perf_counter() >= deadline:
                raise AskShadowIntegrationError(
                    "generation index hashing exceeded the observation budget"
                )
            digest.update(chunk)
            read_bytes += len(chunk)
    after = os.stat(path, follow_symlinks=False)
    stable_fields = (
        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"
    )
    if read_bytes != before.st_size or any(
        getattr(before, field) != getattr(after, field) for field in stable_fields
    ):
        raise AskShadowIntegrationError("generation index changed while hashing")
    return {
        "sha256": digest.hexdigest(),
        "bytes": read_bytes,
        "device": before.st_dev,
        "inode": before.st_ino,
        "mtime_ns": before.st_mtime_ns,
        "ctime_ns": before.st_ctime_ns,
    }


def _requires_multiple_sources(question: str) -> bool:
    lower = (question or "").casefold()
    return any(marker in lower for marker in _MULTI_SOURCE_MARKERS)


def _compact_observation(
    manifest: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    observation_elapsed_ms: float,
    index_bytes: int,
) -> dict[str, Any]:
    candidates = []
    candidate_locators = []
    for result in response.get("results", [])[:MAX_OBSERVATION_CANDIDATES]:
        candidates.append({
            "id": result["id"],
            "paper_id": result["paper_id"],
            "kind": result["kind"],
            "support_status": result["support_status"],
            "channels": result["channels"],
        })
        if result["kind"] == "evidence":
            locator = result.get("locator") or {}
            candidate_locators.append({
                "evidence_id": result["id"],
                "paper_id": result["paper_id"],
                "page_number": locator.get("page_number"),
                "section_kind": locator.get("section_kind"),
                "section_heading": locator.get("section_heading"),
                "manifest_id": locator.get("manifest_id"),
                "source_file_hash": locator.get("source_file_hash"),
            })
    observation = {
        "schema_version": GENERATION_SCHEMA_VERSION,
        "status": "ready",
        "mode": "observe",
        "production_eligible": False,
        "generation_id": manifest["generation_id"],
        "candidates": candidates,
        "candidate_locators": candidate_locators,
        "coverage": response["coverage"],
        "budget": {
            **response["budget"],
            "max_observation_elapsed_ms": MAX_OBSERVATION_MS,
            "observation_elapsed_ms": round(observation_elapsed_ms, 3),
            "index_integrity_bytes": index_bytes * 2,
            "max_index_integrity_bytes": MAX_SHADOW_INDEX_BYTES * 2,
        },
        "reason": response["answerability_reason"],
    }
    if len(_stable_json(observation)) > MAX_OBSERVATION_CHARACTERS:
        raise AskShadowIntegrationError("shadow observation exceeds its output budget")
    return observation


def observe_question(
    question: str,
    *,
    adapter: str,
    env: Mapping[str, str] | None = None,
    live_data_root: Path = LIVE_DATA_ROOT,
    production_wiki_root: Path = LIVE_WIKI_ROOT,
    query_vector: Sequence[float] | None = None,
    dense_model_id: str | None = None,
) -> dict[str, Any]:
    """Read one immutable generation and return candidate-only telemetry.

    Errors become a compact unavailable observation so an explicitly enabled
    shadow observer cannot break the existing production answer path.
    """
    values = os.environ if env is None else env
    mode = str(values.get("KNOWRA_ASK_SHADOW_MODE", "off")).strip().casefold()
    if mode == "off":
        return _disabled()
    if mode != "observe":
        return _unavailable("shadow_mode_invalid")
    if adapter not in {"cli", "api"}:
        return _unavailable("shadow_adapter_invalid")
    raw_root = str(values.get("KNOWRA_ASK_SHADOW_ROOT", "")).strip()
    if not raw_root:
        return _unavailable("shadow_root_missing")
    started = perf_counter()
    deadline = started + MAX_OBSERVATION_MS / 1_000
    try:
        root = _safe_generation_root(raw_root, live_data_root=live_data_root)
        manifest, index_path = _load_manifest(root)
        hash_started = perf_counter()
        identity_before = _index_identity(index_path, deadline=deadline)
        first_hash_seconds = perf_counter() - hash_started
        if (
            identity_before["sha256"] != manifest["index_sha256"]
            or identity_before["bytes"] != manifest["index_bytes"]
        ):
            raise AskShadowIntegrationError(
                "generation manifest does not match the index file"
            )
        if perf_counter() + PLAN_TIMEOUT_MS / 1_000 + first_hash_seconds >= deadline:
            raise AskShadowIntegrationError(
                "generation integrity and retrieval exceed the observation budget"
            )
        retriever = HybridClaimRetriever(
            index_path,
            shadow_root=root,
            production_wiki_root=production_wiki_root,
        )
        if (
            retriever.source_digest != manifest["source_digest"]
            or retriever.dense_index_digest != manifest["dense_index_digest"]
            or (None if retriever.dense_model_id == "unavailable" else retriever.dense_model_id)
            != manifest["dense_model_id"]
        ):
            raise AskShadowIntegrationError("generation manifest does not match the index")
        route: dict[str, Any] = {"query": question}
        if query_vector is not None:
            route["query_vector"] = list(query_vector)
            route["dense_model_id"] = dense_model_id
        required = 2 if _requires_multiple_sources(question) else 1
        plan = build_retrieval_plan(
            question=question,
            routes=[route],
            required_papers=required,
            required_source_records=required,
            limit=MAX_OBSERVATION_CANDIDATES,
        )
        execute: Callable[..., dict[str, Any]] = (
            execute_cli_plan if adapter == "cli" else execute_api_plan
        )
        response = execute(retriever, plan)
        identity_after = _index_identity(index_path, deadline=deadline)
        if identity_after != identity_before:
            raise AskShadowIntegrationError("generation index changed during observation")
        return _compact_observation(
            manifest,
            response,
            observation_elapsed_ms=(perf_counter() - started) * 1_000,
            index_bytes=identity_before["bytes"],
        )
    except (
        AskShadowIntegrationError,
        HybridRetrievalError,
        OSError,
        sqlite3.Error,
        ArithmeticError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return _unavailable("shadow_generation_invalid")


def observation_trace_payload(observation: Mapping[str, Any]) -> str:
    """Serialize telemetry without candidate text, quotes, or model prompts."""
    safe = {
        "schema_version": observation.get("schema_version"),
        "status": observation.get("status"),
        "mode": observation.get("mode"),
        "production_eligible": False,
        "generation_id": observation.get("generation_id"),
        "candidate_count": len(observation.get("candidates") or []),
        "source_locator_count": len(observation.get("candidate_locators") or []),
        "coverage": observation.get("coverage"),
        "budget": observation.get("budget"),
        "reason": observation.get("reason"),
    }
    return _stable_json(safe)
