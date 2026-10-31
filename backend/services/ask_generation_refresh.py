"""Durable, fail-safe refreshes for the immutable Ask evidence generation.

The refresh path never mutates the library database, PDFs, or Wiki.  It takes
an online SQLite snapshot, copies changed processed PDFs into a private
staging directory, reuses hash-identical evidence, builds a new immutable generation, validates that the
library revision did not change, and only then atomically promotes it.  Any
failure leaves the previous active generation untouched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from services.ask_generation_control import (
    GenerationControlError,
    promote_generation,
    resolve_active_generation,
    rollback_generation,
)
from services.shadow_generation import build_restored_shadow_generation


REFRESH_SCHEMA_VERSION = 1
LIBRARY_REVISION_SCHEMA_VERSION = 2
REFRESH_STATUS_MAX_SECONDS = 1.0
REFRESH_REQUEST_FILENAME = "refresh-request.json"
_source_hash_cache: dict[tuple[str, int, int, int, int, int], tuple[str, str]] = {}
_source_hash_cache_lock = threading.Lock()


class AskGenerationRefreshError(ValueError):
    """A candidate generation could not be proven to match one library revision."""


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


def _source_hashes(path: Path, *, use_cache: bool) -> tuple[str, str]:
    """Return SHA-256 plus legacy MD5; cache only with authoritative stat data."""
    key: tuple[str, int, int, int, int, int] | None = None
    if use_cache:
        stat_result = path.stat()
        key = (
            str(path), stat_result.st_dev, stat_result.st_ino,
            stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_ctime_ns,
        )
        with _source_hash_cache_lock:
            cached = _source_hash_cache.get(key)
        if cached is not None:
            return cached
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha256.update(chunk)
            md5.update(chunk)
    result = (sha256.hexdigest(), md5.hexdigest())
    if key is not None:
        with _source_hash_cache_lock:
            _source_hash_cache[key] = result
    return result


def _birthtime_ns(stat_result: os.stat_result) -> int:
    value = getattr(stat_result, "st_birthtime_ns", None)
    if isinstance(value, int):
        return value
    seconds = getattr(stat_result, "st_birthtime", 0)
    return int(seconds * 1_000_000_000) if seconds else 0


def _persistent_stat_cache_reliable(
    *, source_identity: str, stat_result: os.stat_result
) -> bool:
    """Trust metadata only for precise files in the managed papers root."""
    birthtime_ns = _birthtime_ns(stat_result)
    return (
        source_identity.startswith("root-0/")
        and stat_result.st_dev > 0
        and stat_result.st_ino > 0
        and stat_result.st_ctime_ns > 0
        and birthtime_ns > 0
        # Only ctime is useful as a mutation signal here: callers can restore
        # mtime after replacing a file, while birthtime identifies the inode
        # but does not change on an in-place write.  On filesystems without a
        # precise ctime, rehash instead of trusting metadata across processes.
        and stat_result.st_ctime_ns % 1_000_000 != 0
    )


def _stop_hash_process(process: subprocess.Popen[str]) -> bool:
    """Boundedly reap without calling communicate after its timeout."""
    try:
        if process.poll() is not None:
            return True
    except OSError:
        return False
    for stop in (process.terminate, process.kill):
        try:
            stop()
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=0.2)
            return True
        except (subprocess.TimeoutExpired, OSError):
            continue
    try:
        return process.poll() is not None
    except OSError:
        return False


def _hash_sources_with_deadline(
    paths: Sequence[Path], *, deadline: float
) -> dict[str, tuple[str, str]]:
    remaining = deadline - perf_counter()
    if remaining <= 0:
        raise AskGenerationRefreshError("library revision exceeded its deadline")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("ask_generation_hash_worker.py")),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        close_fds=True,
        env={
            **{
                key: os.environ[key]
                for key in (
                    "PATH", "SYSTEMROOT", "TMPDIR",
                    "KNOWRA_OFFLINE_ROOT", "PYTHONPATH",
                )
                if key in os.environ
            },
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        },
    )
    try:
        remaining = deadline - perf_counter()
        if remaining <= 0:
            cleaned = _stop_hash_process(process)
            detail = "" if cleaned else "; hash worker cleanup incomplete"
            raise AskGenerationRefreshError(
                f"library revision exceeded its deadline{detail}"
            )
        try:
            output, _ = process.communicate(
                json.dumps([str(path) for path in paths]),
                timeout=remaining,
            )
        except subprocess.TimeoutExpired as error:
            cleaned = _stop_hash_process(process)
            detail = "" if cleaned else "; hash worker cleanup incomplete"
            raise AskGenerationRefreshError(
                f"library revision exceeded its deadline{detail}"
            ) from error
        if perf_counter() > deadline:
            raise AskGenerationRefreshError("library revision exceeded its deadline")
        try:
            raw_result = json.loads(output)
        except (TypeError, json.JSONDecodeError) as error:
            raise AskGenerationRefreshError("library paper hashing failed") from error
        if process.returncode != 0 or "error_type" in raw_result:
            raise AskGenerationRefreshError("library paper hashing failed")
        expected = {str(path) for path in paths}
        if (
            not isinstance(raw_result, dict)
            or set(raw_result) != expected
            or any(
                not isinstance(hashes, list)
                or len(hashes) != 2
                or not isinstance(hashes[0], str)
                or len(hashes[0]) != 64
                or not isinstance(hashes[1], str)
                or len(hashes[1]) != 32
                for hashes in raw_result.values()
            )
        ):
            raise AskGenerationRefreshError("library paper hashing failed")
        return {
            path: (hashes[0], hashes[1])
            for path, hashes in raw_result.items()
        }
    finally:
        try:
            still_running = process.poll() is None
        except OSError:
            still_running = True
        if still_running:
            _stop_hash_process(process)
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass


def configured_source_roots(data_root: Path) -> tuple[Path, ...]:
    """Return only the library root and the explicitly configured scan root."""
    roots = [data_root.resolve() / "papers"]
    try:
        from path_utils import DATA_DIR, resolve_papers_directory
        if data_root.resolve() == DATA_DIR.resolve():
            from config import load_config
            configured = resolve_papers_directory(
                load_config().get("scan_directory") or str(DATA_DIR / "papers")
            ).resolve(strict=True)
            roots.append(configured)
    except (OSError, ValueError, KeyError):
        pass
    return tuple(dict.fromkeys(root.resolve() for root in roots))


def _paper_candidate(*, filepath: Any, filename: Any, papers_root: Path) -> Path:
    """Resolve a portable database path without authorizing arbitrary roots."""
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
            return candidate
        elif parts and parts[0] == "papers":
            relative = Path(*parts[1:])
        elif parts and parts[0] != "data":
            relative = Path(*parts)
    if relative is None or not relative.parts:
        fallback = Path(str(filename or ""))
        if len(fallback.parts) != 1 or fallback.name in {"", ".", ".."}:
            raise AskGenerationRefreshError("paper source path is invalid")
        relative = fallback
    if relative.is_absolute() or ".." in relative.parts:
        raise AskGenerationRefreshError("paper source escaped the library directory")
    return papers_root / relative


def _paper_source(
    *, filepath: Any, filename: Any, papers_root: Path,
    allowed_roots: Sequence[Path],
) -> tuple[Path, str]:
    candidate = _paper_candidate(
        filepath=filepath, filename=filename, papers_root=papers_root
    )
    cursor = Path(candidate.anchor) if candidate.is_absolute() else Path()
    components = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
    if any((cursor := cursor / component).is_symlink() for component in components):
        raise AskGenerationRefreshError("paper source path contains a symlink")
    try:
        source = candidate.resolve(strict=True)
    except OSError as error:
        raise AskGenerationRefreshError("library paper source is unavailable") from error
    roots = tuple(root.resolve(strict=True) for root in allowed_roots)
    if not any(source.is_relative_to(root) for root in roots) or not source.is_file():
        raise AskGenerationRefreshError("paper source escaped the library directory")
    identity = next(
        f"root-{index}/{source.relative_to(root).as_posix()}"
        for index, root in enumerate(roots) if source.is_relative_to(root)
    )
    return source, identity


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_stable_json(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def revision_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a privacy-safe digest of every input that affects Ask evidence."""
    entries: list[dict[str, Any]] = []
    for row in rows:
        paper_id = str(row["id"])
        evidence_identity = {
            "filename_hash": _sha256_bytes(str(row.get("filename") or "").encode()),
            "filepath_hash": _sha256_bytes(str(row.get("source_relpath") or "").encode()),
            "recorded_file_hash": str(row.get("file_hash") or ""),
            "source_sha256": str(row.get("source_sha256") or ""),
            "source_size_bytes": str(row.get("source_size_bytes") or ""),
            "source_mtime_ns": str(row.get("source_mtime_ns") or ""),
            "source_ctime_ns": str(row.get("source_ctime_ns") or ""),
        }
        plan_identity = {
            "title_hash": _sha256_bytes(str(row.get("title") or "").encode()),
            "extraction_hash": _sha256_bytes(str(row.get("raw_llm_response") or "").encode()),
            "notes_hash": _sha256_bytes(str(row.get("notes") or "").encode()),
        }
        evidence_digest = _sha256_bytes(_stable_json(evidence_identity).encode())
        plan_digest = _sha256_bytes(_stable_json(plan_identity).encode())
        entries.append({
            "paper_id": paper_id,
            "evidence_digest": evidence_digest,
            "plan_digest": plan_digest,
            "input_digest": _sha256_bytes(f"{evidence_digest}:{plan_digest}".encode()),
            "source_cache": {
                "identity_hash": _sha256_bytes(
                    str(row.get("source_relpath") or "").encode()
                ),
                "source_sha256": str(row.get("source_sha256") or ""),
                "source_md5": str(row.get("source_md5") or ""),
                "recorded_file_hash": str(row.get("file_hash") or "").strip().lower(),
                "size_bytes": int(row.get("source_size_bytes") or 0),
                "mtime_ns": int(row.get("source_mtime_ns") or 0),
                "ctime_ns": int(row.get("source_ctime_ns") or 0),
                "device": int(row.get("source_device") or 0),
                "inode": int(row.get("source_inode") or 0),
                "birthtime_ns": int(row.get("source_birthtime_ns") or 0),
                "stat_reliable": bool(row.get("source_stat_reliable")),
            },
        })
    entries.sort(key=lambda item: item["paper_id"])
    return {
        "schema_version": LIBRARY_REVISION_SCHEMA_VERSION,
        "revision": _sha256_bytes(_stable_json(entries).encode()),
        "paper_count": len(entries),
        "paper_ids_digest": _sha256_bytes(
            _stable_json([entry["paper_id"] for entry in entries]).encode()
        ),
        "entries": entries,
    }


def _public_revision(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in ("schema_version", "revision", "paper_count", "paper_ids_digest")
    }


def library_revision(
    database: Path, *, source_roots: Sequence[Path] | None = None,
    trusted_revision: Mapping[str, Any] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Read the current WAL-aware library revision without writing the DB."""
    if not database.is_file() or database.is_symlink():
        raise AskGenerationRefreshError("library database is unavailable")
    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, filepath, filename, file_hash, title, raw_llm_response, notes FROM papers "
            "WHERE processed = 1 AND raw_llm_response IS NOT NULL ORDER BY id"
        ).fetchall()
        papers_root = database.parent / "papers"
        allowed_roots = tuple(source_roots or configured_source_roots(database.parent))
        trusted_entries = {
            str(entry.get("paper_id")): entry
            for entry in (trusted_revision or {}).get("entries", [])
            if isinstance(entry, Mapping)
        }
        enriched = []
        pending_hashes: list[tuple[dict[str, Any], Path, tuple[int, ...]]] = []
        for row in rows:
            value = dict(row)
            source, relative = _paper_source(
                filepath=value.get("filepath"),
                filename=value.get("filename"),
                papers_root=papers_root,
                allowed_roots=allowed_roots,
            )
            stat_result = source.stat()
            recorded = str(value.get("file_hash") or "").strip().lower()
            cache = trusted_entries.get(str(value["id"]), {}).get("source_cache")
            birthtime_ns = _birthtime_ns(stat_result)
            stat_reliable = _persistent_stat_cache_reliable(
                source_identity=relative, stat_result=stat_result
            )
            stat_identity = (
                stat_result.st_dev,
                stat_result.st_ino,
                stat_result.st_size,
                stat_result.st_mtime_ns,
                stat_result.st_ctime_ns,
                birthtime_ns,
            )
            cache_matches = (
                isinstance(cache, Mapping)
                and cache.get("stat_reliable") is True
                and stat_reliable
                and cache.get("identity_hash") == _sha256_bytes(relative.encode())
                and cache.get("recorded_file_hash") == recorded
                and cache.get("size_bytes") == stat_result.st_size
                and cache.get("mtime_ns") == stat_result.st_mtime_ns
                and cache.get("ctime_ns") == stat_result.st_ctime_ns
                and cache.get("device") == stat_result.st_dev
                and cache.get("inode") == stat_result.st_ino
                and cache.get("birthtime_ns") == birthtime_ns
                and isinstance(cache.get("source_sha256"), str)
                and len(cache["source_sha256"]) == 64
                and isinstance(cache.get("source_md5"), str)
                and len(cache["source_md5"]) == 32
            )
            hash_pending = False
            if cache_matches:
                source_sha256 = cache["source_sha256"]
                source_md5 = cache["source_md5"]
            elif deadline is None:
                source_sha256, source_md5 = _source_hashes(
                    source, use_cache=stat_reliable
                )
            else:
                pending_hashes.append((value, source, stat_identity))
                source_sha256 = source_md5 = ""
                hash_pending = True
            if recorded and recorded not in {source_md5, source_sha256}:
                if not hash_pending:
                    raise AskGenerationRefreshError("library paper hash does not match its source")
            value.update(
                source_relpath=relative,
                source_sha256=source_sha256,
                source_md5=source_md5,
                source_size_bytes=stat_result.st_size,
                source_mtime_ns=stat_result.st_mtime_ns,
                source_ctime_ns=stat_result.st_ctime_ns,
                source_device=stat_result.st_dev,
                source_inode=stat_result.st_ino,
                source_birthtime_ns=birthtime_ns,
                source_stat_reliable=stat_reliable,
            )
            enriched.append(value)
        if pending_hashes:
            hashes = _hash_sources_with_deadline(
                [item[1] for item in pending_hashes], deadline=deadline
            )
            for value, source, stat_identity in pending_hashes:
                current = source.stat()
                if (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                    _birthtime_ns(current),
                ) != stat_identity:
                    raise AskGenerationRefreshError("library paper changed while hashing")
                source_sha256, source_md5 = hashes[str(source)]
                recorded = str(value.get("file_hash") or "").strip().lower()
                if recorded and recorded not in {source_md5, source_sha256}:
                    raise AskGenerationRefreshError("library paper hash does not match its source")
                value["source_sha256"] = source_sha256
                value["source_md5"] = source_md5
        return revision_from_rows(enriched)
    finally:
        connection.close()


def generation_revision(generation_root: Path) -> dict[str, Any] | None:
    path = generation_root / "generation.json"
    if not path.is_file() or path.is_symlink():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    revision = value.get("library_revision")
    if revision is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(revision, str)
        or len(revision) != 64
        or not isinstance(value.get("library_paper_count"), int)
        or value["library_paper_count"] < 1
        or not isinstance(value.get("paper_ids_digest"), str)
        or len(value["paper_ids_digest"]) != 64
        or not isinstance(value.get("library_inventory_relpath"), str)
        or not isinstance(value.get("library_inventory_sha256"), str)
        or len(value["library_inventory_sha256"]) != 64
    ):
        raise AskGenerationRefreshError("generation library revision is invalid")
    inventory_path = (generation_root / value["library_inventory_relpath"]).resolve()
    if not inventory_path.is_relative_to(generation_root.resolve()) or not inventory_path.is_file():
        raise AskGenerationRefreshError("generation library inventory is unavailable")
    if _sha256_file(inventory_path) != value["library_inventory_sha256"]:
        raise AskGenerationRefreshError("generation library inventory changed")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    result = {
        "schema_version": LIBRARY_REVISION_SCHEMA_VERSION,
        "revision": revision,
        "paper_count": value["library_paper_count"],
        "paper_ids_digest": value["paper_ids_digest"],
        "entries": inventory.get("entries"),
    }
    if (
        inventory.get("schema_version") != LIBRARY_REVISION_SCHEMA_VERSION
        or inventory.get("revision") != revision
        or not isinstance(result["entries"], list)
        or len(result["entries"]) != result["paper_count"]
        or any(
            not isinstance(entry, dict)
            or set(entry) != {
                "paper_id", "evidence_digest", "plan_digest", "input_digest",
                "source_cache",
            }
            or not isinstance(entry["paper_id"], str)
            or any(
                not isinstance(entry[field], str) or len(entry[field]) != 64
                for field in ("evidence_digest", "plan_digest", "input_digest")
            )
            or not isinstance(entry["source_cache"], dict)
            or set(entry["source_cache"]) != {
                "identity_hash", "source_sha256", "source_md5",
                "recorded_file_hash", "size_bytes", "mtime_ns", "ctime_ns",
                "device", "inode", "birthtime_ns", "stat_reliable",
            }
            or any(
                not isinstance(entry["source_cache"][field], str)
                or len(entry["source_cache"][field]) != length
                for field, length in (
                    ("identity_hash", 64), ("source_sha256", 64), ("source_md5", 32)
                )
            )
            or not isinstance(entry["source_cache"]["recorded_file_hash"], str)
            or len(entry["source_cache"]["recorded_file_hash"]) not in {0, 32, 64}
            or any(
                not isinstance(entry["source_cache"][field], int)
                or entry["source_cache"][field] < 0
                for field in (
                    "size_bytes", "mtime_ns", "ctime_ns", "device", "inode",
                    "birthtime_ns",
                )
            )
            or not isinstance(entry["source_cache"]["stat_reliable"], bool)
            for entry in result["entries"]
        )
        or _sha256_bytes(_stable_json(result["entries"]).encode()) != revision
        or _sha256_bytes(
            _stable_json([entry["paper_id"] for entry in result["entries"]]).encode()
        ) != result["paper_ids_digest"]
    ):
        raise AskGenerationRefreshError("generation library inventory is invalid")
    return result


def request_refresh(*, state_root: Path, reason: str) -> dict[str, Any]:
    """Persist a coalescing marker before best-effort durable task submission."""
    state_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root.mkdir(exist_ok=True, mode=0o700)
    request = {
        "schema_version": REFRESH_SCHEMA_VERSION,
        "request_id": uuid4().hex,
        "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": str(reason)[:120],
    }
    with _request_lock(state_root):
        _atomic_json(state_root / REFRESH_REQUEST_FILENAME, request)
    return request


@contextmanager
def _request_lock(state_root: Path):
    descriptor = os.open(state_root / "request.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_request(state_root: Path) -> dict[str, Any] | None:
    with _request_lock(state_root):
        path = state_root / REFRESH_REQUEST_FILENAME
        if not path.is_file() or path.is_symlink():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AskGenerationRefreshError("refresh request is unreadable") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != REFRESH_SCHEMA_VERSION
            or not isinstance(value.get("request_id"), str)
            or len(value["request_id"]) != 32
        ):
            raise AskGenerationRefreshError("refresh request is invalid")
        return value


def _ack_request(state_root: Path, request_id: str | None) -> bool:
    with _request_lock(state_root):
        path = state_root / REFRESH_REQUEST_FILENAME
        if request_id is None:
            return not path.is_file()
        if not path.is_file():
            return False
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if current.get("request_id") != request_id:
            return False
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True


def default_refresh_paths(live_data_root: Path) -> dict[str, Path]:
    workspace = live_data_root.resolve().parent
    return {
        "control_root": workspace / ".knowra" / "ask-generation-control",
        "generation_parent": workspace / ".knowra" / "ask-generations",
        "state_root": workspace / ".knowra" / "ask-generation-refresh",
        "production_wiki_root": live_data_root.resolve() / "wiki",
    }


def refresh_status(
    *, live_data_root: Path, control_root: Path,
    source_roots: Sequence[Path] | None = None,
    deadline_seconds: float | None = REFRESH_STATUS_MAX_SECONDS,
) -> dict[str, Any]:
    """Compare revisions within a UI/startup budget; workers may opt out."""
    active_unavailable = False
    try:
        active = resolve_active_generation(
            control_root=control_root, live_data_root=live_data_root
        )
        recorded = generation_revision(Path(active["generation_root"]))
    except (GenerationControlError, AskGenerationRefreshError, OSError, ValueError):
        active_unavailable = True
        recorded = None
    deadline = (
        None if deadline_seconds is None
        else perf_counter() + max(0.0, deadline_seconds)
    )
    try:
        current = library_revision(
            live_data_root / "knowledge.db",
            source_roots=source_roots,
            trusted_revision=recorded,
            deadline=deadline,
        )
    except AskGenerationRefreshError as error:
        return {
            "freshness": "unavailable",
            "reason": (
                "library_revision_timeout"
                if "deadline" in str(error)
                else "library_revision_unavailable"
            ),
            "library_revision": None,
            "paper_count": None,
        }
    except (sqlite3.Error, OSError):
        return {
            "freshness": "unavailable",
            "reason": "library_revision_unavailable",
            "library_revision": None,
            "paper_count": None,
        }
    if active_unavailable:
        return {
            "freshness": "unavailable",
            "reason": "active_generation_unavailable",
            "library_revision": current["revision"],
            "paper_count": current["paper_count"],
        }
    if recorded is None:
        freshness = "unknown"
    elif recorded["revision"] == current["revision"]:
        freshness = "fresh"
    else:
        freshness = "stale"
    return {
        "freshness": freshness,
        "library_revision": current["revision"],
        "generation_revision": recorded["revision"] if recorded else None,
        "paper_count": current["paper_count"],
    }


def mark_and_enqueue_refresh(*, live_data_root: Path, reason: str) -> dict[str, Any]:
    """Persist demand and enqueue once; an active job will coalesce the marker."""
    paths = default_refresh_paths(live_data_root)
    request_refresh(state_root=paths["state_root"], reason=reason)
    return ensure_pending_refresh(live_data_root=live_data_root)


def ensure_pending_refresh(
    *, live_data_root: Path, retry_after_seconds: float = 60.0
) -> dict[str, Any]:
    """Ensure a durable marker eventually owns a queued refresh job."""
    paths = default_refresh_paths(live_data_root)
    marker = paths["state_root"] / REFRESH_REQUEST_FILENAME
    status = None
    if not marker.is_file():
        status = refresh_status(
            live_data_root=live_data_root, control_root=paths["control_root"]
        )
    needs_refresh = (
        marker.is_file()
        or status["freshness"] in {"stale", "unknown"}
        or (
            status["freshness"] == "unavailable"
            and (live_data_root / "knowledge.db").is_file()
            and status.get("paper_count") != 0
        )
    )
    if not needs_refresh:
        return {"requested": False, "reason": "generation_current"}
    if not marker.is_file():
        request_refresh(state_root=paths["state_root"], reason="freshness_reconcile")
    from services import task_runtime
    from services.task_store import TaskConflict

    latest = task_runtime.store().latest("ask_generation_refresh", summary=True)
    if latest and latest["status"] in {"queued", "running"}:
        return {"requested": True, "job_id": latest["job_id"], "coalesced": True}
    if (
        latest
        and latest["status"] in {"failed", "interrupted"}
        and time.time() - float(latest["updated"]) < retry_after_seconds
    ):
        return {"requested": True, "job_id": None, "retry_deferred": True}
    request = _read_request(paths["state_root"])
    try:
        job = task_runtime.submit(
            "ask_generation_refresh",
            "ask_generation_refresh",
            {"request_id": request["request_id"] if request else uuid4().hex},
        )
    except TaskConflict:
        return {"requested": True, "job_id": None, "coalesced": True}
    return {"requested": True, "job_id": job["job_id"]}


def _snapshot_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=30)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        if target_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise AskGenerationRefreshError("snapshot database integrity check failed")
    finally:
        target_connection.close()
        source_connection.close()


def _fsync_generation_tree(root: Path) -> None:
    """Persist every immutable artifact and directory before pointer promotion."""
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AskGenerationRefreshError("generation contains a symlink")
        if path.is_dir():
            directories.append(path)
            continue
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory_path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        descriptor = os.open(directory_path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _snapshot_inputs(
    *, data_root: Path, snapshot_root: Path, copy_paper_ids: set[str] | None = None,
    source_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    database = data_root / "knowledge.db"
    papers_root = data_root / "papers"
    if not papers_root.is_dir() or papers_root.is_symlink():
        raise AskGenerationRefreshError("library papers directory is unavailable")
    snapshot_root.mkdir(mode=0o700)
    snapshot_database = snapshot_root / "knowledge.db"
    _snapshot_database(database, snapshot_database)
    destination = snapshot_root / "papers"
    destination.mkdir(mode=0o700)
    connection = sqlite3.connect(snapshot_database)
    connection.row_factory = sqlite3.Row
    copied: list[dict[str, str]] = []
    allowed_roots = tuple(source_roots or configured_source_roots(data_root))
    try:
        rows = connection.execute(
            "SELECT id, filepath, filename, file_hash, title, raw_llm_response, notes FROM papers "
            "WHERE processed = 1 AND raw_llm_response IS NOT NULL ORDER BY id"
        ).fetchall()
        revision_rows = []
        destinations: set[str] = set()
        for row in rows:
            source, source_identity = _paper_source(
                filepath=row["filepath"],
                filename=row["filename"],
                papers_root=papers_root,
                allowed_roots=allowed_roots,
            )
            stat_result = source.stat()
            stat_reliable = _persistent_stat_cache_reliable(
                source_identity=source_identity, stat_result=stat_result
            )
            source_sha256, source_md5 = _source_hashes(
                source, use_cache=stat_reliable
            )
            recorded = str(row["file_hash"] or "").strip().lower()
            if recorded and recorded not in {source_md5, source_sha256}:
                raise AskGenerationRefreshError("library paper hash does not match its source")
            revision_row = dict(row)
            revision_row.update(
                source_relpath=source_identity,
                source_sha256=source_sha256,
                source_md5=source_md5,
                source_size_bytes=stat_result.st_size,
                source_mtime_ns=stat_result.st_mtime_ns,
                source_ctime_ns=stat_result.st_ctime_ns,
                source_device=stat_result.st_dev,
                source_inode=stat_result.st_ino,
                source_birthtime_ns=_birthtime_ns(stat_result),
                source_stat_reliable=stat_reliable,
            )
            revision_rows.append(revision_row)
            paper_id = str(row["id"])
            snapshot_name = f"{_sha256_bytes(paper_id.encode())}.pdf"
            snapshot_relative = Path("__ask_sources__") / snapshot_name
            connection.execute(
                "UPDATE papers SET filepath=? WHERE id=?",
                (f"data/papers/{snapshot_relative.as_posix()}", row["id"]),
            )
            if copy_paper_ids is None or paper_id in copy_paper_ids:
                key = snapshot_relative.as_posix().casefold()
                if key in destinations:
                    raise AskGenerationRefreshError("paper snapshot paths collide")
                destinations.add(key)
                target = destination / snapshot_relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(source, target)
                source_hash = source_sha256
                target_hash = _sha256_file(target)
                if source_hash != target_hash:
                    raise AskGenerationRefreshError("paper changed while snapshotting")
                copied.append({"paper_id": paper_id, "source_hash": source_hash})
        connection.commit()
    finally:
        connection.close()
    revision = revision_from_rows(revision_rows)
    return {**revision, "copied_papers_digest": _sha256_bytes(_stable_json(copied).encode())}


@contextmanager
def _refresh_lock(state_root: Path):
    descriptor = os.open(state_root / "refresh.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _refresh_generation_locked(
    *,
    data_root: Path,
    production_wiki_root: Path,
    generation_parent: Path,
    control_root: Path,
    state_root: Path,
    source_roots: Sequence[Path] | None,
) -> dict[str, Any]:
    """Build and promote the latest stable revision, preserving old active on failure."""
    data_root = data_root.resolve(strict=True)
    production_wiki_root = production_wiki_root.resolve(strict=True)
    generation_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for output_root in (generation_parent.resolve(), state_root.resolve(), control_root.resolve()):
        if (
            output_root == data_root
            or output_root.is_relative_to(data_root)
            or data_root.is_relative_to(output_root)
        ):
            raise AskGenerationRefreshError("refresh outputs overlap the live data root")
    request_before = _read_request(state_root)
    request_id = request_before.get("request_id") if request_before else None
    revision_before = library_revision(
        data_root / "knowledge.db", source_roots=source_roots
    )
    preserve_generation = False
    active_revision = None
    try:
        active = resolve_active_generation(control_root=control_root, live_data_root=data_root)
    except GenerationControlError:
        active = None
    if active is not None:
        try:
            active_revision = generation_revision(Path(active["generation_root"]))
        except AskGenerationRefreshError:
            active_revision = None
        if active_revision and active_revision["revision"] == revision_before["revision"]:
            acknowledged = _ack_request(state_root, request_id)
            return {
                "status": "current",
                **_public_revision(revision_before),
                "generation_id": active["generation_id"],
                "request_acknowledged": acknowledged,
            }

    current_entries = {
        entry["paper_id"]: entry for entry in revision_before["entries"]
    }
    previous_entries = {
        entry["paper_id"]: entry for entry in (active_revision or {}).get("entries", [])
    }
    reuse_evidence_ids = {
        paper_id for paper_id, entry in current_entries.items()
        if previous_entries.get(paper_id, {}).get("evidence_digest") == entry["evidence_digest"]
    }
    reuse_plan_ids = {
        paper_id for paper_id, entry in current_entries.items()
        if previous_entries.get(paper_id, {}).get("input_digest") == entry["input_digest"]
    }

    token = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{revision_before['revision'][:12]}-{uuid4().hex[:8]}"
    )
    snapshot_root = state_root / f"snapshot-{token}"
    generation_root = generation_parent / f"library-{token}"
    try:
        snapshot_revision = _snapshot_inputs(
            data_root=data_root,
            snapshot_root=snapshot_root,
            copy_paper_ids=set(current_entries) - reuse_evidence_ids,
            source_roots=source_roots,
        )
        if snapshot_revision["revision"] != revision_before["revision"]:
            raise AskGenerationRefreshError("library changed before snapshot completed")
        summary = build_restored_shadow_generation(
            data_root=snapshot_root,
            output_root=generation_root,
            production_wiki_root=production_wiki_root,
            library_revision=revision_before,
            reuse_generation_root=active["generation_root"] if active_revision else None,
            reuse_evidence_ids=reuse_evidence_ids,
            reuse_plan_ids=reuse_plan_ids,
        )
        revision_after = library_revision(
            data_root / "knowledge.db", source_roots=source_roots,
            trusted_revision=revision_before,
        )
        if revision_after["revision"] != revision_before["revision"]:
            raise AskGenerationRefreshError("library changed while generation was building")
        if (
            summary["papers_failed"]
            or summary["evidence_failed_pages"]
            or summary["evidence_empty_pages"]
            or summary["papers_compiled"] != revision_before["paper_count"]
        ):
            raise AskGenerationRefreshError("candidate generation has extraction failures")
        acceptance = {
            "schema_version": REFRESH_SCHEMA_VERSION,
            "library_revision": _public_revision(revision_before),
            "summary": summary,
            "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "policy": "automatic-zero-failure-v1",
        }
        _atomic_json(generation_root / "automatic-acceptance.json", acceptance)
        acceptance_sha256 = _sha256_file(generation_root / "automatic-acceptance.json")
        _fsync_generation_tree(generation_root)
        active_sha = active["active_sha256"] if active is not None else None
        # A post-replace fsync failure can make switch outcome uncertain.
        # Keep this immutable candidate once promotion starts so an active
        # pointer can never be left referencing a deleted generation.
        preserve_generation = True
        switched = promote_generation(
            generation_root=generation_root,
            control_root=control_root,
            live_data_root=data_root,
            promotion_id=f"auto-{token}",
            reviewer="automatic-zero-failure-v1",
            reviewed_at=acceptance["validated_at"],
            acceptance_sha256=acceptance_sha256,
            accepted_failed_pages=0,
            expected_active_sha256=active_sha,
        )
        try:
            revision_after_promotion = library_revision(
                data_root / "knowledge.db", source_roots=source_roots,
                trusted_revision=revision_before,
            )
            promotion_is_current = (
                revision_after_promotion["revision"] == revision_before["revision"]
            )
        except (AskGenerationRefreshError, sqlite3.Error, OSError):
            promotion_is_current = False
        if not promotion_is_current:
            marker_error: BaseException | None = None
            try:
                request_refresh(
                    state_root=state_root,
                    reason="post_promotion_library_change",
                )
            except (AskGenerationRefreshError, OSError) as error:
                marker_error = error
            if active is not None:
                try:
                    rollback_generation(
                        control_root=control_root,
                        live_data_root=data_root,
                        target_promotion_id=active["promotion_id"],
                        expected_active_sha256=switched["active_sha256"],
                    )
                except (GenerationControlError, OSError) as rollback_error:
                    raise AskGenerationRefreshError(
                        "library changed during generation promotion; rollback is uncertain"
                    ) from rollback_error
            if marker_error is not None and active is None:
                raise AskGenerationRefreshError(
                    "library changed during initial generation promotion; "
                    "no previous active generation exists and refresh marker failed"
                ) from marker_error
            if marker_error is not None:
                raise AskGenerationRefreshError(
                    "library changed during generation promotion; rolled back but refresh marker failed"
                ) from marker_error
            if active is None:
                raise AskGenerationRefreshError(
                    "library changed during initial generation promotion; "
                    "no previous active generation exists"
                )
            raise AskGenerationRefreshError("library changed during generation promotion")
        acknowledged = _ack_request(state_root, request_id)
        return {
            "status": "promoted",
            **_public_revision(revision_before),
            "generation_id": switched["generation_id"],
            "previous_generation_id": active["generation_id"] if active else None,
            "request_acknowledged": acknowledged,
        }
    finally:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        if not preserve_generation:
            shutil.rmtree(generation_root, ignore_errors=True)


def refresh_generation(
    *,
    data_root: Path,
    production_wiki_root: Path,
    generation_parent: Path,
    control_root: Path,
    state_root: Path,
    source_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Serialize refreshes even when multiple callers discover staleness."""
    resolved_data = data_root.resolve(strict=True)
    for candidate in (generation_parent, control_root, state_root):
        resolved = candidate.resolve()
        if (
            resolved == resolved_data
            or resolved.is_relative_to(resolved_data)
            or resolved_data.is_relative_to(resolved)
        ):
            raise AskGenerationRefreshError("refresh outputs overlap the live data root")
    state_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_root.mkdir(exist_ok=True, mode=0o700)
    with _refresh_lock(state_root):
        return _refresh_generation_locked(
            data_root=data_root,
            production_wiki_root=production_wiki_root,
            generation_parent=generation_parent,
            control_root=control_root,
            state_root=state_root,
            source_roots=source_roots,
        )
