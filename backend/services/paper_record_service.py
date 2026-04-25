import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models import Paper
from path_utils import DATA_DIR, portable_data_path, resolve_paper_path


RECORDS_DIR = DATA_DIR / "paper_records"
RECORD_MARKER_START = "<!-- knowra:paper-record:start -->"
RECORD_MARKER_END = "<!-- knowra:paper-record:end -->"


def _slugify(stem: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    return slug or "paper"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_dt(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def record_path_for_paper(paper: Paper) -> Path:
    stem = Path(paper.filename or f"paper-{paper.id}").stem
    name = f"{paper.id:04d}-{_slugify(stem)}.md"
    return RECORDS_DIR / name


def record_relpath_for_paper(paper: Paper) -> str:
    return portable_data_path(record_path_for_paper(paper))


def record_url_for_paper(paper: Paper) -> str:
    return f"/api/papers/{paper.id}/record"


def _normalize_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    out: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "")
        ts = str(item.get("ts") or "")
        if not role or not content:
            continue
        out.append({"role": role, "content": content, "ts": ts})
    return out


def _load_existing_payload(paper: Paper) -> Optional[dict[str, Any]]:
    path = record_path_for_paper(paper)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(
        re.escape(RECORD_MARKER_START) + r"\s*(\{.*?\})\s*" + re.escape(RECORD_MARKER_END),
        text,
        re.S,
    )
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _payload_from_paper(
    paper: Paper,
    previous: Optional[dict[str, Any]] = None,
    event: str = "snapshot",
) -> dict[str, Any]:
    previous = previous or {}
    prev_extraction = previous.get("extraction") if isinstance(previous.get("extraction"), dict) else {}
    initial_raw = prev_extraction.get("initial_raw_llm_response")
    current_raw = prev_extraction.get("current_raw_llm_response")
    revisions = prev_extraction.get("revisions")
    if not isinstance(revisions, list):
        revisions = []

    raw = paper.raw_llm_response or ""
    now = datetime.now(timezone.utc).isoformat()

    if raw and not initial_raw:
        initial_raw = raw
    if raw:
        if raw != current_raw:
            revisions = [
                *revisions,
                {
                    "event": event,
                    "ts": now,
                    "raw_llm_response": raw,
                },
            ]
        current_raw = raw
    else:
        # paper.raw_llm_response was cleared (e.g. reprocess). Mirror that
        # into the record so stale content doesn't linger in the .md.
        current_raw = ""

    resolved_pdf = resolve_paper_path(paper.filepath)
    source = {
        "kind": "pdf",
        "name": paper.filename,
        "path": paper.filepath,
        "resolved_path": str(resolved_pdf),
        "url": f"/api/papers/{paper.id}/file",
        "file_hash": paper.file_hash,
    }

    return {
        "version": 1,
        "paper": {
            "id": paper.id,
            "filename": paper.filename,
            "filepath": paper.filepath,
            "file_hash": paper.file_hash,
            "num_pages": paper.num_pages,
            "title": paper.title,
            "authors": list(paper.authors or []),
            "processed": bool(paper.processed),
            "processed_at": _iso(paper.processed_at),
            "error": paper.error,
            "openai_file_id": paper.openai_file_id,
            "openai_vector_store_id": paper.openai_vector_store_id,
            "openai_thread_id": paper.openai_thread_id,
            "thread_created_at": _iso(paper.thread_created_at),
            "created_at": _iso(paper.created_at),
        },
        "source": source,
        "extraction": {
            "initial_raw_llm_response": initial_raw or "",
            "current_raw_llm_response": current_raw or "",
            "revisions": revisions,
        },
        "notes": {
            "markdown": paper.notes or "",
        },
        "chat": {
            "messages": _normalize_messages(paper.chat_history),
        },
    }


def _fenced_block(content: str, language: str = "") -> str:
    if not content.strip():
        return "_Empty_\n"
    fence = "````"
    lang = language.strip()
    opening = f"{fence}{lang}".rstrip()
    return f"{opening}\n{content.rstrip()}\n{fence}\n"


def _render_messages(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "_No chat turns yet._\n"

    parts: list[str] = []
    for idx, message in enumerate(messages, start=1):
        role = str(message.get("role") or "message").upper()
        ts = str(message.get("ts") or "").strip()
        heading = f"### {idx}. {role}"
        if ts:
            heading += f" · {ts}"
        parts.append(heading)
        parts.append("")
        parts.append(str(message.get("content") or "").rstrip() or "_Empty_")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_record_markdown(payload: dict[str, Any]) -> str:
    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}

    record_json = json.dumps(payload, ensure_ascii=False, indent=2)
    initial_raw = str(extraction.get("initial_raw_llm_response") or "")
    current_raw = str(extraction.get("current_raw_llm_response") or "")
    note_markdown = str(notes.get("markdown") or "").strip()
    chat_messages = _normalize_messages(chat.get("messages"))
    revisions = extraction.get("revisions") if isinstance(extraction.get("revisions"), list) else []

    lines = [
        f"# {paper.get('title') or paper.get('filename') or 'Knowra Paper Record'}",
        "",
        "> This markdown file is the durable knowledge record for this paper inside Knowra.",
        "",
        RECORD_MARKER_START,
        record_json,
        RECORD_MARKER_END,
        "",
        "## Source",
        "",
        f"- Paper ID: `{paper.get('id')}`",
        f"- File name: `{source.get('name') or paper.get('filename') or ''}`",
        f"- Relative path: `{source.get('path') or paper.get('filepath') or ''}`",
        f"- Resolved path: `{source.get('resolved_path') or ''}`",
        f"- Source URL: `{source.get('url') or ''}`",
        f"- File hash: `{source.get('file_hash') or paper.get('file_hash') or ''}`",
        f"- Pages: `{paper.get('num_pages') if paper.get('num_pages') is not None else ''}`",
        f"- Processed: `{paper.get('processed')}`",
        f"- Processed at: `{paper.get('processed_at') or ''}`",
        "",
        "## First File Search Response",
        "",
        _fenced_block(initial_raw, "json").rstrip(),
        "",
        "## Current Working Response",
        "",
        _fenced_block(current_raw, "json").rstrip(),
        "",
        "## User Notes",
        "",
        note_markdown or "_No notes yet._",
        "",
        "## Follow-up Chat Log",
        "",
        _render_messages(chat_messages).rstrip(),
        "",
        "## Response Revision Log",
        "",
    ]

    if revisions:
        for item in revisions:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('ts') or ''}` · `{item.get('event') or 'snapshot'}`"
            )
    else:
        lines.append("_No revisions yet._")

    lines.append("")
    return "\n".join(lines)


def sync_record_from_paper(paper: Paper, event: str = "snapshot") -> Path:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    previous = _load_existing_payload(paper)
    payload = _payload_from_paper(paper, previous=previous, event=event)
    path = record_path_for_paper(paper)
    path.write_text(render_record_markdown(payload), encoding="utf-8")
    return path


def sync_paper_from_record(paper: Paper) -> bool:
    payload = _load_existing_payload(paper)
    if not payload:
        sync_record_from_paper(paper, event="bootstrap")
        return False

    changed = False
    paper_block = payload.get("paper") if isinstance(payload.get("paper"), dict) else {}
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}

    current_raw = extraction.get("current_raw_llm_response") or extraction.get("initial_raw_llm_response") or None
    next_notes = notes.get("markdown") or None
    next_history = _normalize_messages(chat.get("messages"))

    scalar_updates = {
        "filename": paper_block.get("filename") or paper.filename,
        "filepath": paper_block.get("filepath") or paper.filepath,
        "file_hash": paper_block.get("file_hash") or paper.file_hash,
        "num_pages": paper_block.get("num_pages"),
        "title": paper_block.get("title"),
        "authors": paper_block.get("authors") if isinstance(paper_block.get("authors"), list) else (paper.authors or []),
        "processed": bool(paper_block.get("processed")),
        "error": paper_block.get("error"),
        "openai_file_id": paper_block.get("openai_file_id"),
        "openai_vector_store_id": paper_block.get("openai_vector_store_id"),
        "openai_thread_id": paper_block.get("openai_thread_id"),
        "raw_llm_response": current_raw,
        "notes": next_notes,
        "chat_history": next_history,
    }

    for field, value in scalar_updates.items():
        if getattr(paper, field) != value:
            setattr(paper, field, value)
            changed = True

    processed_at = _parse_dt(paper_block.get("processed_at"))
    if paper.processed_at != processed_at:
        paper.processed_at = processed_at
        changed = True

    thread_created_at = _parse_dt(paper_block.get("thread_created_at"))
    if paper.thread_created_at != thread_created_at:
        paper.thread_created_at = thread_created_at
        changed = True

    return changed
