"""Optional durable hard cap for one explicitly scoped provider run."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class ProviderAttemptBudgetError(RuntimeError):
    recoverable = False


class ProviderAttemptLimitExceeded(ProviderAttemptBudgetError):
    pass


class ProviderAttemptReplayBlocked(ProviderAttemptBudgetError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class AttemptReservation:
    number: int
    operation_id: str


class ProviderAttemptBudget:
    def __init__(self, path: Path, *, run_id: str, limit: int) -> None:
        if not run_id.strip():
            raise ProviderAttemptBudgetError("provider attempt run id is empty")
        if limit < 1:
            raise ProviderAttemptBudgetError("provider attempt limit must be positive")
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.run_id = run_id.strip()
        self.limit = limit

    @classmethod
    def from_environment(cls) -> "ProviderAttemptBudget | None":
        raw_path = str(os.environ.get("KNOWRA_PROVIDER_ATTEMPT_LEDGER") or "").strip()
        raw_limit = str(os.environ.get("KNOWRA_PROVIDER_ATTEMPT_LIMIT") or "").strip()
        run_id = str(os.environ.get("KNOWRA_PROVIDER_ATTEMPT_RUN_ID") or "").strip()
        supplied = [bool(raw_path), bool(raw_limit), bool(run_id)]
        if not any(supplied):
            return None
        if not all(supplied):
            raise ProviderAttemptBudgetError(
                "provider attempt guard requires ledger, limit, and run id together"
            )
        try:
            limit = int(raw_limit)
        except ValueError as error:
            raise ProviderAttemptBudgetError("provider attempt limit is not an integer") from error
        return cls(Path(raw_path).expanduser().resolve(), run_id=run_id, limit=limit)

    def _initial(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "limit": self.limit,
            "attempts": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._initial()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise ProviderAttemptBudgetError("provider attempt ledger is unreadable") from error
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProviderAttemptBudgetError("provider attempt ledger schema mismatch")
        if value.get("run_id") != self.run_id or value.get("limit") != self.limit:
            raise ProviderAttemptBudgetError("provider attempt ledger binding mismatch")
        if not isinstance(value.get("attempts"), list):
            raise ProviderAttemptBudgetError("provider attempt ledger attempts are invalid")
        return value

    def _locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        os.chmod(self.lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def reserve(self, operation_id: str, request_sha256: str) -> AttemptReservation:
        operation_id = str(operation_id or "").strip()
        if not operation_id:
            raise ProviderAttemptBudgetError("budgeted provider request has no operation id")
        if len(request_sha256) != 64:
            raise ProviderAttemptBudgetError("budgeted provider request hash is invalid")
        lock = self._locked()
        try:
            value = self._load()
            for item in value["attempts"]:
                if item.get("operation_id") == operation_id:
                    raise ProviderAttemptReplayBlocked(
                        f"provider operation already reserved with status={item.get('status')}"
                    )
            if len(value["attempts"]) >= self.limit:
                raise ProviderAttemptLimitExceeded(
                    f"provider attempt hard cap reached ({self.limit}/{self.limit})"
                )
            number = len(value["attempts"]) + 1
            value["attempts"].append({
                "number": number,
                "operation_id": operation_id,
                "request_sha256": request_sha256,
                "status": "started",
                "started_at": _now(),
            })
            _write_json_atomic(self.path, value)
            return AttemptReservation(number=number, operation_id=operation_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def finish(
        self,
        reservation: AttemptReservation,
        *,
        status: str,
        error_class: str | None = None,
    ) -> None:
        if status not in {"succeeded", "unknown"}:
            raise ValueError("unsupported provider attempt terminal status")
        lock = self._locked()
        try:
            value = self._load()
            match = next(
                (
                    item for item in value["attempts"]
                    if item.get("number") == reservation.number
                    and item.get("operation_id") == reservation.operation_id
                ),
                None,
            )
            if match is None or match.get("status") != "started":
                raise ProviderAttemptBudgetError("provider attempt terminal update is inconsistent")
            match["status"] = status
            match["finished_at"] = _now()
            if error_class:
                match["error_class"] = str(error_class)[:120]
            _write_json_atomic(self.path, value)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()


def request_identity(
    *,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    reasoning_effort: str | None,
    image_paths: list[str] | None,
) -> str:
    value = {
        "model_id": model_id,
        "system": system,
        "user": user,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": reasoning_effort,
        "image_paths": [str(path) for path in image_paths or []],
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
