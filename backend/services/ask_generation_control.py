"""Explicit, auditable activation control for immutable Ask generations.

Shadow generations remain byte-for-byte immutable and non-production artifacts.
Promotion writes a separate reviewed record, then atomically replaces a small
active pointer.  Importing this module never creates or activates anything.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

from services.hybrid_claim_retrieval import INDEX_APPLICATION_ID, INDEX_SCHEMA_VERSION


CONTROL_SCHEMA_VERSION = 1
GENERATION_SCHEMA_VERSION = 1
MAX_JSON_BYTES = 32_000
MAX_INDEX_BYTES = 1_073_741_824
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GenerationControlError(ValueError):
    """A generation cannot be reviewed or activated safely."""


class GenerationSwitchUncertain(GenerationControlError):
    """The active pointer changed but durable directory sync was not confirmed."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (_stable_json(value) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive_json(path: Path, value: Any) -> None:
    payload = _json_bytes(value)
    if len(payload) > MAX_JSON_BYTES:
        raise GenerationControlError("control record exceeds its size budget")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise GenerationControlError("immutable control record already exists") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    payload = _json_bytes(value)
    if len(payload) > MAX_JSON_BYTES:
        raise GenerationControlError("active pointer exceeds its size budget")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            _fsync_directory(path.parent)
        except OSError as error:
            raise GenerationSwitchUncertain(
                "active pointer was replaced but directory sync failed; inspect status"
            ) from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise GenerationControlError(f"{label} is missing or linked")
    size = path.stat().st_size
    if not 2 <= size <= MAX_JSON_BYTES:
        raise GenerationControlError(f"{label} exceeds its size budget")
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GenerationControlError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise GenerationControlError(f"{label} must be an object")
    return value, _sha256_bytes(payload)


def _no_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            cursor = cursor / component
            if stat.S_ISLNK(os.lstat(cursor).st_mode):
                raise GenerationControlError("control paths must not contain symlinks")
    except OSError as error:
        raise GenerationControlError("control path is unavailable") from error


def _existing_directory(path: str | Path, *, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise GenerationControlError(f"{label} must be absolute")
    _no_symlink_components(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GenerationControlError(f"{label} is unavailable") from error
    if not resolved.is_dir():
        raise GenerationControlError(f"{label} must be a directory")
    return resolved


def _separate(path: Path, *forbidden: Path) -> None:
    for other in forbidden:
        resolved = other.resolve(strict=True)
        if path == resolved or path.is_relative_to(resolved) or resolved.is_relative_to(path):
            raise GenerationControlError("generation control overlaps a protected root")


def _control_for_write(
    path: str | Path, *, generation_root: Path, live_data_root: Path
) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise GenerationControlError("control root must be absolute")
    parent = _existing_directory(candidate.parent, label="control parent")
    control = parent / candidate.name
    _separate(control, generation_root, live_data_root)
    if control.exists():
        resolved = _existing_directory(control, label="control root")
    else:
        control.mkdir(mode=0o700)
        _fsync_directory(parent)
        resolved = control.resolve(strict=True)
    _control_layout(resolved, create=True)
    return resolved


def _control_layout(control: Path, *, create: bool) -> None:
    for name in ("promotions", "transitions"):
        directory = control / name
        if not directory.exists() and create:
            directory.mkdir(mode=0o700)
            _fsync_directory(control)
        if directory.is_symlink() or not directory.is_dir():
            raise GenerationControlError("control directory layout is unsafe")
        if not directory.resolve(strict=True).is_relative_to(control):
            raise GenerationControlError("control directory escaped its root")
    for name in ("active.json", "switch.lock"):
        path = control / name
        if path.exists() and (
            path.is_symlink() or not stat.S_ISREG(os.lstat(path).st_mode)
        ):
            raise GenerationControlError("control file layout is unsafe")


def _generation_manifest(
    root: Path, *, verify_index_hash: bool = True
) -> tuple[dict[str, Any], Path, str]:
    manifest, manifest_hash = _read_json(root / "generation.json", label="generation manifest")
    required = {
        "schema_version", "generation_id", "status", "index_relpath",
        "source_digest", "dense_index_digest", "dense_model_id",
        "index_sha256", "index_bytes", "production_eligible",
    }
    if set(manifest) != required or manifest.get("schema_version") != GENERATION_SCHEMA_VERSION:
        raise GenerationControlError("generation manifest shape is invalid")
    if (
        manifest.get("status") != "ready"
        or manifest.get("production_eligible") is not False
        or not isinstance(manifest.get("generation_id"), str)
        or not _IDENTIFIER.fullmatch(manifest["generation_id"])
    ):
        raise GenerationControlError("generation is not a ready immutable shadow artifact")
    for field in ("source_digest", "dense_index_digest", "index_sha256"):
        if not isinstance(manifest.get(field), str) or not _SHA256.fullmatch(manifest[field]):
            raise GenerationControlError("generation digest is invalid")
    size = manifest.get("index_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_INDEX_BYTES:
        raise GenerationControlError("generation index size is invalid")
    dense_model_id = manifest.get("dense_model_id")
    if dense_model_id is not None and (
        not isinstance(dense_model_id, str)
        or "@" not in dense_model_id
        or len(dense_model_id) > 200
    ):
        raise GenerationControlError("generation dense model id is invalid")
    relpath = manifest.get("index_relpath")
    if (
        not isinstance(relpath, str)
        or not relpath
        or Path(relpath).is_absolute()
        or ".." in Path(relpath).parts
    ):
        raise GenerationControlError("generation index path is invalid")
    raw_index = root / relpath
    _no_symlink_components(raw_index)
    index = raw_index.resolve(strict=True)
    if not index.is_relative_to(root) or not index.is_file():
        raise GenerationControlError("generation index escaped its root")
    if index.stat().st_size != size or (
        verify_index_hash and _sha256_file(index) != manifest["index_sha256"]
    ):
        raise GenerationControlError("generation index does not match its manifest")
    return manifest, index, manifest_hash


def _index_identity(index: Path) -> dict[str, int]:
    value = os.stat(index, follow_symlinks=False)
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _locator_audit(index: Path, manifest: Mapping[str, Any]) -> dict[str, int]:
    uri = f"file:{quote(str(index))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if (
                metadata.get("application_id") != INDEX_APPLICATION_ID
                or metadata.get("schema_version") != str(INDEX_SCHEMA_VERSION)
            ):
                raise GenerationControlError("generation index schema is incompatible")
            rows = connection.execute(
                "SELECT support_status,text,source_signatures,locator "
                "FROM records WHERE kind='evidence'"
            )
            checked = 0
            for support_status, text, raw_signatures, raw_locator in rows:
                checked += 1
                locator = json.loads(raw_locator)
                signatures = json.loads(raw_signatures)
                page = locator.get("page_number") if isinstance(locator, dict) else None
                start = locator.get("page_char_start") if isinstance(locator, dict) else None
                end = locator.get("page_char_end") if isinstance(locator, dict) else None
                source_hash = locator.get("source_file_hash") if isinstance(locator, dict) else None
                manifest_id = locator.get("manifest_id") if isinstance(locator, dict) else None
                text_hash = locator.get("text_hash") if isinstance(locator, dict) else None
                if (
                    support_status != "source_text"
                    or not isinstance(text, str)
                    or isinstance(page, bool)
                    or not isinstance(page, int)
                    or page < 1
                    or isinstance(start, bool)
                    or not isinstance(start, int)
                    or isinstance(end, bool)
                    or not isinstance(end, int)
                    or not 0 <= start < end
                    or end - start != len(text)
                    or not isinstance(source_hash, str)
                    or not _SHA256.fullmatch(source_hash)
                    or not isinstance(manifest_id, str)
                    or not manifest_id
                    or not isinstance(signatures, list)
                    or manifest_id not in signatures
                    or not isinstance(text_hash, str)
                    or text_hash != _sha256_bytes(text.encode("utf-8"))
                ):
                    raise GenerationControlError(
                        "generation evidence lacks a complete source locator"
                    )
            if checked < 1:
                raise GenerationControlError("generation contains no source evidence")
            if (
                metadata.get("source_digest") != manifest["source_digest"]
                or metadata.get("dense_index_digest") != manifest["dense_index_digest"]
            ):
                raise GenerationControlError("generation metadata does not match its manifest")
        finally:
            connection.close()
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as error:
        raise GenerationControlError("generation locator audit failed") from error
    return {"evidence_records": checked, "missing_locators": 0, "missing_source_hashes": 0}


def _reviewed_at(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise GenerationControlError("reviewed_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GenerationControlError("reviewed_at is invalid") from error
    if parsed.tzinfo is None:
        raise GenerationControlError("reviewed_at must include a timezone")
    return value


def _current_active(control: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = control / "active.json"
    if not path.exists():
        return None, None
    return _read_json(path, label="active pointer")


def _expect_active(actual: str | None, expected: str | None) -> None:
    if actual != expected:
        raise GenerationControlError("active generation changed before the requested switch")


@contextmanager
def _switch_lock(control: Path):
    path = control / "switch.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GenerationControlError("switch lock is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def promote_generation(
    *,
    generation_root: str | Path,
    control_root: str | Path,
    live_data_root: str | Path,
    promotion_id: str,
    reviewer: str,
    reviewed_at: str,
    acceptance_sha256: str,
    expected_active_sha256: str | None,
) -> dict[str, Any]:
    """Audit one immutable generation and explicitly switch the active pointer."""
    generation = _existing_directory(generation_root, label="generation root")
    live = _existing_directory(live_data_root, label="live data root")
    _separate(generation, live)
    if not isinstance(promotion_id, str) or not _IDENTIFIER.fullmatch(promotion_id):
        raise GenerationControlError("promotion id is invalid")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 120:
        raise GenerationControlError("reviewer is invalid")
    if not isinstance(acceptance_sha256, str) or not _SHA256.fullmatch(acceptance_sha256):
        raise GenerationControlError("acceptance digest is invalid")
    reviewed_at = _reviewed_at(reviewed_at)
    manifest, index, manifest_hash = _generation_manifest(generation)
    identity_before = _index_identity(index)
    audit = _locator_audit(index, manifest)
    identity_after = _index_identity(index)
    if (
        identity_after != identity_before
        or _sha256_file(index) != manifest["index_sha256"]
    ):
        raise GenerationControlError("generation changed during promotion review")
    control = _control_for_write(
        control_root, generation_root=generation, live_data_root=live
    )
    promotion = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "promotion_id": promotion_id,
        "generation_id": manifest["generation_id"],
        "generation_root": str(generation),
        "generation_manifest_sha256": manifest_hash,
        "index_sha256": manifest["index_sha256"],
        "index_identity": identity_after,
        "source_digest": manifest["source_digest"],
        "locator_audit": audit,
        "review": {
            "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at,
            "acceptance_sha256": acceptance_sha256,
            "approved": True,
        },
    }
    promotion_path = control / "promotions" / f"{promotion_id}.json"
    with _switch_lock(control):
        _, current_hash = _current_active(control)
        _expect_active(current_hash, expected_active_sha256)
        if promotion_path.exists():
            existing, existing_hash = _read_json(
                promotion_path, label="promotion record"
            )
            if existing != promotion:
                raise GenerationControlError(
                    "promotion id is already bound to another review"
                )
            promotion_hash = existing_hash
        else:
            _write_exclusive_json(promotion_path, promotion)
            promotion_hash = _sha256_file(promotion_path)
        return _switch_active(
            control=control,
            promotion=promotion,
            promotion_hash=promotion_hash,
            previous_active_sha256=current_hash,
            action="promote",
        )


def _switch_active(
    *,
    control: Path,
    promotion: Mapping[str, Any],
    promotion_hash: str,
    previous_active_sha256: str | None,
    action: str,
) -> dict[str, Any]:
    transition_id = uuid4().hex
    switched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    active = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "promotion_id": promotion["promotion_id"],
        "promotion_sha256": promotion_hash,
        "generation_id": promotion["generation_id"],
        "generation_manifest_sha256": promotion["generation_manifest_sha256"],
        "index_sha256": promotion["index_sha256"],
        "previous_active_sha256": previous_active_sha256,
        "transition_id": transition_id,
        "action": action,
        "switched_at": switched_at,
    }
    _write_exclusive_json(control / "transitions" / f"{transition_id}.json", {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "transition_id": transition_id,
        "action": action,
        "from_active_sha256": previous_active_sha256,
        "to_promotion_id": promotion["promotion_id"],
        "to_promotion_sha256": promotion_hash,
        "switched_at": switched_at,
    })
    _atomic_json(control / "active.json", active)
    return {**active, "active_sha256": _sha256_file(control / "active.json")}


def _promotion_record(control: Path, promotion_id: str) -> tuple[dict[str, Any], str]:
    if not isinstance(promotion_id, str) or not _IDENTIFIER.fullmatch(promotion_id):
        raise GenerationControlError("promotion id is invalid")
    record, digest = _read_json(
        control / "promotions" / f"{promotion_id}.json", label="promotion record"
    )
    required = {
        "schema_version", "promotion_id", "generation_id", "generation_root",
        "generation_manifest_sha256", "index_sha256", "index_identity", "source_digest",
        "locator_audit", "review",
    }
    review = record.get("review")
    audit = record.get("locator_audit")
    identity = record.get("index_identity")
    if (
        set(record) != required
        or record.get("schema_version") != CONTROL_SCHEMA_VERSION
        or record.get("promotion_id") != promotion_id
        or not isinstance(record.get("generation_id"), str)
        or not _IDENTIFIER.fullmatch(record["generation_id"])
        or not isinstance(record.get("generation_root"), str)
        or not isinstance(record.get("generation_manifest_sha256"), str)
        or not _SHA256.fullmatch(record["generation_manifest_sha256"])
        or not isinstance(record.get("index_sha256"), str)
        or not _SHA256.fullmatch(record["index_sha256"])
        or not isinstance(record.get("source_digest"), str)
        or not _SHA256.fullmatch(record["source_digest"])
        or not isinstance(review, dict)
        or set(review) != {"reviewer", "reviewed_at", "acceptance_sha256", "approved"}
        or review.get("approved") is not True
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
        or not isinstance(review.get("acceptance_sha256"), str)
        or not _SHA256.fullmatch(review["acceptance_sha256"])
        or not isinstance(review.get("reviewed_at"), str)
        or not isinstance(audit, dict)
        or set(audit) != {
            "evidence_records", "missing_locators", "missing_source_hashes"
        }
        or isinstance(audit.get("evidence_records"), bool)
        or not isinstance(audit.get("evidence_records"), int)
        or audit.get("evidence_records", 0) < 1
        or audit.get("missing_locators") != 0
        or audit.get("missing_source_hashes") != 0
        or not isinstance(identity, dict)
        or set(identity) != {"device", "inode", "size", "mtime_ns", "ctime_ns"}
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in identity.values())
    ):
        raise GenerationControlError("promotion record is invalid")
    _reviewed_at(review["reviewed_at"])
    return record, digest


def resolve_active_generation(
    *, control_root: str | Path, live_data_root: str | Path
) -> dict[str, Any]:
    """Resolve an active reviewed generation without changing control state."""
    control = _existing_directory(control_root, label="control root")
    _control_layout(control, create=False)
    live = _existing_directory(live_data_root, label="live data root")
    _separate(control, live)
    active, active_hash = _current_active(control)
    if active is None or active_hash is None:
        raise GenerationControlError("active generation is unavailable")
    required = {
        "schema_version", "promotion_id", "promotion_sha256", "generation_id",
        "generation_manifest_sha256", "index_sha256", "previous_active_sha256",
        "transition_id", "action", "switched_at",
    }
    if (
        set(active) != required
        or active.get("schema_version") != CONTROL_SCHEMA_VERSION
        or not isinstance(active.get("promotion_id"), str)
        or not _IDENTIFIER.fullmatch(active["promotion_id"])
        or not isinstance(active.get("promotion_sha256"), str)
        or not _SHA256.fullmatch(active["promotion_sha256"])
        or not isinstance(active.get("generation_id"), str)
        or not _IDENTIFIER.fullmatch(active["generation_id"])
        or not isinstance(active.get("generation_manifest_sha256"), str)
        or not _SHA256.fullmatch(active["generation_manifest_sha256"])
        or not isinstance(active.get("index_sha256"), str)
        or not _SHA256.fullmatch(active["index_sha256"])
        or active.get("previous_active_sha256") is not None
        and (
            not isinstance(active["previous_active_sha256"], str)
            or not _SHA256.fullmatch(active["previous_active_sha256"])
        )
        or not isinstance(active.get("transition_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", active["transition_id"])
        or active.get("action") not in {"promote", "rollback"}
        or not isinstance(active.get("switched_at"), str)
    ):
        raise GenerationControlError("active pointer is invalid")
    _reviewed_at(active["switched_at"])
    promotion, promotion_hash = _promotion_record(control, active.get("promotion_id", ""))
    if (
        active.get("promotion_sha256") != promotion_hash
        or active.get("generation_id") != promotion.get("generation_id")
        or active.get("generation_manifest_sha256")
        != promotion.get("generation_manifest_sha256")
        or active.get("index_sha256") != promotion.get("index_sha256")
    ):
        raise GenerationControlError("active pointer does not match its promotion")
    generation = _existing_directory(
        promotion.get("generation_root", ""), label="generation root"
    )
    _separate(generation, live, control)
    manifest, index, manifest_hash = _generation_manifest(
        generation, verify_index_hash=False
    )
    if (
        manifest_hash != promotion["generation_manifest_sha256"]
        or manifest["generation_id"] != promotion["generation_id"]
        or manifest["index_sha256"] != promotion["index_sha256"]
        or manifest["source_digest"] != promotion["source_digest"]
        or _index_identity(index) != promotion.get("index_identity")
    ):
        raise GenerationControlError("promoted generation changed after review")
    return {
        "generation_root": generation,
        "generation_id": manifest["generation_id"],
        "promotion_id": promotion["promotion_id"],
        "active_sha256": active_hash,
        "production_eligible": True,
    }


def rollback_generation(
    *,
    control_root: str | Path,
    live_data_root: str | Path,
    target_promotion_id: str,
    expected_active_sha256: str,
) -> dict[str, Any]:
    """Atomically repoint to an already reviewed immutable generation."""
    control = _existing_directory(control_root, label="control root")
    _control_layout(control, create=False)
    live = _existing_directory(live_data_root, label="live data root")
    _separate(control, live)
    with _switch_lock(control):
        _, actual_hash = _current_active(control)
        _expect_active(actual_hash, expected_active_sha256)
        promotion, promotion_hash = _promotion_record(control, target_promotion_id)
        generation = _existing_directory(
            promotion.get("generation_root", ""), label="generation root"
        )
        _separate(generation, live, control)
        manifest, index, manifest_hash = _generation_manifest(generation)
        if (
            manifest_hash != promotion["generation_manifest_sha256"]
            or manifest["index_sha256"] != promotion["index_sha256"]
            or _index_identity(index) != promotion.get("index_identity")
        ):
            raise GenerationControlError("rollback generation changed after review")
        return _switch_active(
            control=control,
            promotion=promotion,
            promotion_hash=promotion_hash,
            previous_active_sha256=actual_hash,
            action="rollback",
        )
