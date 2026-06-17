from datetime import datetime, timedelta, timezone
import hashlib
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from logging_utils import get_logger
from models import Paper, KnowledgeNode
from config import load_config, save_config, task_model_id, task_model_name, task_reasoning_effort
from model_gateway import get_model_entry, get_provider_entry, task_context
from path_utils import (
    portable_data_path,
    resolve_artifact_path,
    resolve_paper_path,
    resolve_papers_directory,
)
from services.scanner_service import scan_directory
from services.pdf_service import extract_text, render_first_page
from services.vlm_service import (
    extract_knowledge_from_paper,
    extraction_has_critical_issues,
    extraction_quality_issues,
    model_uses_responses_api,
    parse_extraction_response,
    run_chat_turn,
    PaperExtractionError,
)
from services.graph_service import add_nodes_from_paper_extraction, remove_nodes_for_paper
from services.note_images_gc import gc_on_notes_update
from services.paper_category_service import (
    PAPER_CATEGORY_OPTIONS,
    PAPER_CATEGORY_OTHER,
    DEFAULT_PAPER_CATEGORY_OPTIONS,
    derive_model_paper_category,
    effective_paper_category,
    get_active_categories,
    set_active_categories,
    paper_category_source,
    sync_paper_category_fields,
    normalize_paper_category,
)
from services.paper_team_service import (
    TEAM_OTHER,
    DEFAULT_TEAMS,
    derive_model_paper_team,
    effective_paper_team,
    get_active_teams,
    get_active_team_names,
    set_active_teams,
    paper_team_source,
    sync_paper_team_fields,
    normalize_team,
)
from services.paper_record_service import (
    record_path_for_paper,
    record_relpath_for_paper,
    record_url_for_paper,
    sync_paper_from_record,
    sync_record_from_paper,
)
from services.paper_pipeline_service import (
    PIPELINE_STATUS_DONE,
    PIPELINE_STATUS_EXTRACTING,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_GRAPHING,
    PIPELINE_STATUS_PARSING,
    PIPELINE_STATUS_SCANNING,
    compute_backoff_seconds,
    is_recoverable_error,
    short_error_reason,
)


def _safe_parse(raw):
    if not raw:
        return None
    try:
        return parse_extraction_response(raw)
    except Exception:
        return None


def _paper_pub_year(p: Paper) -> Optional[int]:
    """Publication year from the paper's extraction (or None)."""
    ext = _safe_parse(p.raw_llm_response)
    raw = ext.get("year") if isinstance(ext, dict) else None
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())[:4]
    if len(digits) == 4:
        year = int(digits)
        if 1900 <= year <= 2100:
            return year
    return None


def _hydrate_paper_metadata_from_raw(p: Paper) -> bool:
    parsed = _safe_parse(p.raw_llm_response)
    if not parsed:
        return False

    changed = False
    parsed_title = (parsed.get("title") or "").strip()
    parsed_authors = parsed.get("authors") or []

    if parsed_title and ((not p.title) or p.title == p.filename):
        p.title = parsed_title[:200]
        changed = True
    if parsed_authors and not (p.authors or []):
        p.authors = parsed_authors
        changed = True
    if sync_paper_category_fields(p, parsed):
        changed = True
    if sync_paper_team_fields(p, parsed):
        changed = True
    return changed


def _reconcile_processed_paper(p: Paper) -> bool:
    changed = _hydrate_paper_metadata_from_raw(p)
    if not p.processed or not p.raw_llm_response:
        if p.processed and p.processing_status != PIPELINE_STATUS_DONE:
            p.processing_status = PIPELINE_STATUS_DONE
            changed = True
        if p.processed and (
            p.last_error_stage is not None
            or p.last_error_reason is not None
            or p.last_error_recoverable is not None
        ):
            p.last_error_stage = None
            p.last_error_reason = None
            p.last_error_recoverable = None
            changed = True
        return changed
    parsed = _safe_parse(p.raw_llm_response)
    if not parsed:
        if p.processed and p.processing_status != PIPELINE_STATUS_DONE:
            p.processing_status = PIPELINE_STATUS_DONE
            changed = True
        if p.processed and (
            p.last_error_stage is not None
            or p.last_error_reason is not None
            or p.last_error_recoverable is not None
        ):
            p.last_error_stage = None
            p.last_error_reason = None
            p.last_error_recoverable = None
            changed = True
        return changed
    issues = set(extraction_quality_issues(parsed))
    # Auto-heal only the clear "ghost-success" shape we observed in the DB:
    # processed=True, but both the paper identity and graph payload are empty.
    if "title 为空" not in issues or "图谱关键字段全空" not in issues:
        if p.processing_status != PIPELINE_STATUS_DONE:
            p.processing_status = PIPELINE_STATUS_DONE
            changed = True
        if (
            p.last_error_stage is not None
            or p.last_error_reason is not None
            or p.last_error_recoverable is not None
        ):
            p.last_error_stage = None
            p.last_error_reason = None
            p.last_error_recoverable = None
            changed = True
        return changed

    if p.processed:
        p.processed = False
        changed = True
    if p.processed_at is not None:
        p.processed_at = None
        changed = True
    next_error = (
        "检测到空壳抽取结果：模型返回了结构完整但内容为空的 JSON。"
        "请重试；若仍失败，建议重新处理以重建 OpenAI 文件索引。"
    )
    if p.error != next_error:
        p.error = next_error
        changed = True
    if p.processing_status != PIPELINE_STATUS_FAILED:
        p.processing_status = PIPELINE_STATUS_FAILED
        changed = True
    if p.last_error_stage != PIPELINE_STATUS_PARSING:
        p.last_error_stage = PIPELINE_STATUS_PARSING
        changed = True
    if p.last_error_reason != next_error:
        p.last_error_reason = next_error
        changed = True
    if p.last_error_recoverable is not True:
        p.last_error_recoverable = True
        changed = True
    return changed


router = APIRouter(prefix="/api", tags=["papers"])
logger = get_logger("papers")

processing_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "current": "",
    "succeeded": 0,
    "failed_papers": [],
    "max_retries": 0,
}


def _retry_settings(cfg: dict) -> tuple[int, float, float]:
    try:
        max_retries = int(cfg.get("paper_process_max_retries", 3))
    except (TypeError, ValueError):
        max_retries = 3
    max_retries = min(max(max_retries, 1), 8)

    try:
        base_seconds = float(cfg.get("paper_process_backoff_base_seconds", 1.5))
    except (TypeError, ValueError):
        base_seconds = 1.5
    base_seconds = min(max(base_seconds, 0.0), 60.0)

    try:
        max_seconds = float(cfg.get("paper_process_backoff_max_seconds", 20.0))
    except (TypeError, ValueError):
        max_seconds = 20.0
    max_seconds = min(max(max_seconds, base_seconds), 300.0)
    return max_retries, base_seconds, max_seconds


def _set_pipeline_state(
    db: Session,
    paper: Paper,
    *,
    status: str,
    retry_count: Optional[int] = None,
    error: Optional[str] = None,
    error_stage: Optional[str] = None,
    error_recoverable: Optional[bool] = None,
    clear_error: bool = False,
) -> None:
    paper.processing_status = status
    if retry_count is not None:
        paper.retry_count = max(0, int(retry_count))
    if clear_error:
        paper.error = None
        paper.last_error_stage = None
        paper.last_error_reason = None
        paper.last_error_recoverable = None
    if error is not None:
        paper.error = error
        paper.last_error_reason = error
    if error_stage is not None:
        paper.last_error_stage = error_stage
    if error_recoverable is not None:
        paper.last_error_recoverable = bool(error_recoverable)
    db.commit()


def _paper_failure_item(p: Paper) -> dict:
    return {
        "id": p.id,
        "filename": p.filename,
        "stage": p.last_error_stage or p.processing_status,
        "reason": p.last_error_reason or p.error,
        "recoverable": bool(p.last_error_recoverable),
        "retry_count": int(p.retry_count or 0),
    }


class RawResponseUpdate(BaseModel):
    raw_llm_response: str
    # When False, save the JSON but DON'T rebuild the knowledge graph /
    # re-embed (a heavy OpenAI round-trip). Used for light edits — e.g.
    # fixing a formula's LaTeX — that don't change the graph structure.
    rebuild_graph: bool = True


class NotesUpdate(BaseModel):
    notes: str


class CategoryUpdate(BaseModel):
    # Optional[str] instead of `str | None` — the project's runtime is
    # Python 3.9, which does not support PEP 604 union syntax outside of
    # `from __future__ import annotations` files.
    category: Optional[str] = None


class TeamUpdate(BaseModel):
    team: Optional[str] = None


class ChatMessageInput(BaseModel):
    message: str


# OpenAI Assistants threads expire ~60 days after last activity. Mirror that in
# the UI countdown so the user knows when follow-up chat stops working.
THREAD_TTL_DAYS = 60


def _nodes_for_paper(db: Session, paper_id: str):
    nodes = db.query(KnowledgeNode).all()
    matches = []
    for node in nodes:
        raw_ids = node.source_paper_ids or []
        ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        if any(str(source_id) == str(paper_id) for source_id in ids):
            matches.append(node)
    return matches


def _chat_state(p: Paper) -> dict:
    """Chat-related fields surfaced to the frontend. `days_remaining` and
    `expires_at` are derived from `thread_created_at` so the UI can show a
    countdown without guessing the TTL."""
    cfg = load_config()
    paper_chat_model = task_model_name(cfg, "paper_chat")
    model_entry = get_model_entry(cfg, paper_chat_model)
    provider = get_provider_entry(cfg, model_entry.get("provider_id", "")) if model_entry else None
    provider_type = str(provider.get("provider_type") or "openai") if provider else "openai"
    uses_local_context = provider_type == "codex_cli"

    created = p.thread_created_at
    expires_at = None
    days_remaining = None
    if created is not None and not uses_local_context:
        # Treat naive datetimes as UTC (SQLite round-trips lose tzinfo).
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires = created + timedelta(days=THREAD_TTL_DAYS)
        expires_at = expires.isoformat()
        delta = expires - datetime.now(timezone.utc)
        days_remaining = max(0, delta.days + (1 if delta.seconds > 0 else 0))
    return {
        "messages": list(p.chat_history or []),
        "thread_created_at": created.isoformat() if created else None,
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "ttl_days": THREAD_TTL_DAYS,
        "ready": p.processed and (uses_local_context or bool(p.openai_file_id)),
    }


def _serialize_paper_detail(p: Paper, db: Session) -> dict:
    nodes = _nodes_for_paper(db, p.id)
    has_first_page_image = bool(
        p.first_page_image_path
        and resolve_artifact_path(p.first_page_image_path).exists()
    )
    category = effective_paper_category(p, _safe_parse(p.raw_llm_response))
    team = effective_paper_team(p, _safe_parse(p.raw_llm_response))
    return {
        "id": p.id,
        "filename": p.filename,
        "filepath": p.filepath,
        "title": p.title,
        "authors": p.authors or [],
        "num_pages": p.num_pages,
        "extracted_text": p.extracted_text,
        "processed": p.processed,
        "processed_at": p.processed_at.isoformat() if p.processed_at else None,
        "processing_status": p.processing_status,
        "retry_count": p.retry_count or 0,
        "last_error_stage": p.last_error_stage,
        "last_error_reason": p.last_error_reason,
        "last_error_recoverable": p.last_error_recoverable,
        "extraction_model": p.extraction_model,
        "paper_category": category,
        "paper_category_model": normalize_paper_category(p.paper_category_model),
        "paper_category_override": normalize_paper_category(p.paper_category_override),
        "paper_category_source": paper_category_source(p),
        "paper_team": team,
        "paper_team_model": normalize_team(p.paper_team_model),
        "paper_team_override": normalize_team(p.paper_team_override),
        "paper_team_source": paper_team_source(p),
        "raw_llm_response": p.raw_llm_response,
        "extraction": _safe_parse(p.raw_llm_response),
        "notes": p.notes or "",
        "error": p.error,
        "has_first_page_image": has_first_page_image,
        "record_markdown_path": record_relpath_for_paper(p),
        "record_markdown_url": record_url_for_paper(p),
        "chat": _chat_state(p),
        "knowledge_nodes": [
            {"id": n.id, "title": n.title, "node_type": n.node_type, "tags": n.tags or []}
            for n in nodes
        ],
    }


def _sync_paper_from_record_if_needed(db: Session, p: Paper):
    if sync_paper_from_record(p):
        db.commit()
        db.refresh(p)


@router.post("/scan")
def scan_papers(db: Session = Depends(get_db)):
    cfg = load_config()
    try:
        return scan_directory(cfg["scan_directory"], db)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/papers/upload")
async def upload_papers(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Copy user-selected PDFs into the scan directory (./papers), then
    register them like a scan.

    The browser only hands us bytes + a filename (never a path), so the
    "don't copy ./papers into ./papers" rule is enforced as: skip any file
    whose name OR content already lives in ./papers (no overwrite, no
    duplicate). Newly-saved files are then run through scan_directory, which
    applies the same path / arXiv-id / content-hash de-dup before they enter
    the DB. PDFs never leave this machine."""
    cfg = load_config()
    papers_dir = resolve_papers_directory(cfg.get("scan_directory") or "data/papers")
    papers_dir.mkdir(parents=True, exist_ok=True)

    existing_names = {p.name for p in papers_dir.glob("*.pdf")} | {
        p.name for p in papers_dir.glob("*.PDF")
    }
    existing_hashes = {row.file_hash for row in db.query(Paper.file_hash).all()}

    saved = 0
    skipped_existing = 0
    rejected: list[str] = []
    for f in files:
        name = Path(f.filename or "").name  # basename only — no path traversal
        try:
            if not name or not name.lower().endswith(".pdf"):
                rejected.append(name or "(空文件名)")
                continue
            if name in existing_names:
                # Already in ./papers — exactly the papers→papers no-op we refuse.
                skipped_existing += 1
                continue
            data = await f.read()
            if not data[:5].startswith(b"%PDF"):
                rejected.append(name)  # not a real PDF
                continue
            digest = hashlib.md5(data).hexdigest()
            if digest in existing_hashes:
                skipped_existing += 1  # same content already present under another name
                continue
            (papers_dir / name).write_bytes(data)
            existing_names.add(name)
            existing_hashes.add(digest)
            saved += 1
        finally:
            await f.close()

    if saved:
        scan_result = scan_directory(str(papers_dir), db)
    else:
        scan_result = {
            "new_found": 0,
            "duplicates": 0,
            "total": db.query(Paper).count(),
            "unprocessed": db.query(Paper).filter(Paper.processed == False).count(),
        }
    return {
        "saved": saved,
        "skipped_existing": skipped_existing,
        "rejected": rejected,
        **scan_result,
    }


@router.get("/papers")
def list_papers(db: Session = Depends(get_db)):
    papers = db.query(Paper).order_by(Paper.created_at.desc()).all()
    changed = False
    healed_papers: list[Paper] = []
    for paper in papers:
        if not processing_state["running"]:
            changed = _sync_bulk_record_state(paper) or changed
        if _reconcile_processed_paper(paper):
            healed_papers.append(paper)
            changed = True
    if changed:
        db.commit()
    for paper in healed_papers:
        sync_record_from_paper(paper, event="auto_repair")
    return [
        {
            "id": p.id,
            "filename": p.filename,
            "filepath": p.filepath,
            "title": p.title,
            "authors": p.authors or [],
            "num_pages": p.num_pages,
            "processed": p.processed,
            "processed_at": p.processed_at.isoformat() if p.processed_at else None,
            "processing_status": p.processing_status,
            "retry_count": p.retry_count or 0,
            "last_error_stage": p.last_error_stage,
            "last_error_reason": p.last_error_reason,
            "last_error_recoverable": p.last_error_recoverable,
            "paper_category": effective_paper_category(p, _safe_parse(p.raw_llm_response)),
            "paper_category_model": normalize_paper_category(p.paper_category_model),
            "paper_category_override": normalize_paper_category(p.paper_category_override),
            "paper_category_source": paper_category_source(p),
            "paper_team": effective_paper_team(p, _safe_parse(p.raw_llm_response)),
            "paper_team_model": normalize_team(p.paper_team_model),
            "paper_team_override": normalize_team(p.paper_team_override),
            "paper_team_source": paper_team_source(p),
            "year": _paper_pub_year(p),
            "error": p.error,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in papers
    ]


def _sync_bulk_record_state(p: Paper) -> bool:
    return sync_paper_from_record(p)


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not processing_state["running"]:
        _sync_paper_from_record_if_needed(db, p)
    if _reconcile_processed_paper(p):
        db.commit()
        sync_record_from_paper(p, event="auto_repair")
    return _serialize_paper_detail(p, db)


@router.get("/papers/{paper_id}/file")
def serve_pdf(paper_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    pdf_path = resolve_paper_path(p.filepath)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=p.filename)


@router.get("/papers/{paper_id}/record")
def serve_paper_record(paper_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    path = record_path_for_paper(p)
    if not path.exists():
        sync_record_from_paper(p, event="bootstrap")
    return FileResponse(str(path), media_type="text/markdown; charset=utf-8", filename=path.name)


@router.get("/papers/{paper_id}/first_page")
def serve_first_page(paper_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p or not p.first_page_image_path:
        raise HTTPException(status_code=404, detail="First page image not found")
    image_path = resolve_artifact_path(p.first_page_image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="First page image not found")
    return FileResponse(str(image_path), media_type="image/png")


def _run_wiki_compile_phase(p: Paper, db: Session, cfg: dict) -> None:
    # Phase 1 wiki compile. Failures here must not break processing — the raw
    # layer is already saved and the user can manually retry via the Wiki page.
    try:
        from services.wiki_compiler import (
            compile_paper_page,
            compile_concept_pages_for_paper,
            reconcile_concept_pages_dir,
        )

        compile_model = task_model_id(cfg, "wiki_compile")
        compile_paper_page(p, cfg["openai_api_key"], compile_model)
        compile_concept_pages_for_paper(
            p.id, db, cfg["openai_api_key"], compile_model
        )
        reconcile_concept_pages_dir(db, prune_orphans=True)
        try:
            from services import wiki_index

            wiki_index.refresh_index()
        except Exception as idx_err:
            print(f"[wiki_index] refresh after paper {p.id} failed: {idx_err}")
        # Refresh the FTS index so the new wiki page is searchable immediately.
        try:
            from services.wiki_search import rebuild_index

            rebuild_index()
        except Exception as ix_err:
            print(f"[wiki_search] reindex after paper {p.id} failed: {ix_err}")
    except Exception as compile_err:
        print(f"[wiki] compile after process failed for paper {p.id}: {compile_err}")


def _process_single(paper_id: str):
    from database import SessionLocal

    db = SessionLocal()
    try:
        p = db.query(Paper).filter(Paper.id == paper_id).first()
        if not p or p.processed:
            return

        cfg = load_config()
        max_retries, backoff_base_s, backoff_max_s = _retry_settings(cfg)
        processing_state["max_retries"] = max_retries

        if not cfg.get("openai_api_key"):
            _set_pipeline_state(
                db,
                p,
                status=PIPELINE_STATUS_FAILED,
                retry_count=0,
                error="OpenAI API key not configured",
                error_stage=PIPELINE_STATUS_EXTRACTING,
                error_recoverable=False,
            )
            sync_record_from_paper(p, event="process_error")
            processing_state["errors"] += 1
            processing_state["failed_papers"].append(_paper_failure_item(p))
            return

        processing_state["current"] = p.filename
        attempt = 1
        while attempt <= max_retries:
            stage = PIPELINE_STATUS_SCANNING
            try:
                _set_pipeline_state(
                    db,
                    p,
                    status=PIPELINE_STATUS_SCANNING,
                    retry_count=attempt - 1,
                    clear_error=(attempt == 1),
                )

                pdf_path = resolve_paper_path(p.filepath)
                if not pdf_path.exists():
                    raise FileNotFoundError(f"PDF not found: {pdf_path}")

                portable_filepath = portable_data_path(pdf_path)
                path_conflict = db.query(Paper).filter(
                    Paper.filepath == portable_filepath,
                    Paper.id != p.id,
                ).first()
                if p.filepath != portable_filepath and not path_conflict:
                    p.filepath = portable_filepath
                    db.commit()

                # Remove leftovers from any prior failed attempt so this run
                # starts from a clean graph slice for the current paper.
                remove_nodes_for_paper(db, p.id)
                p.processed = False
                p.processed_at = None
                db.commit()

                # Local preprocessing — no longer fed to the LLM, but still
                # useful for debug and fallback context.
                if not p.extracted_text or not p.num_pages:
                    try:
                        text, num_pages = extract_text(str(pdf_path))
                        p.extracted_text = text
                        p.num_pages = num_pages
                        db.commit()
                    except Exception:
                        pass

                if not p.first_page_image_path:
                    img_path = render_first_page(str(pdf_path), p.file_hash)
                    if img_path:
                        p.first_page_image_path = img_path
                        db.commit()

                stage = PIPELINE_STATUS_EXTRACTING
                _set_pipeline_state(
                    db,
                    p,
                    status=PIPELINE_STATUS_EXTRACTING,
                    retry_count=attempt - 1,
                )

                cached_file_id = p.openai_file_id
                cached_assistant_id = cfg.get("openai_assistant_id") or None
                cached_vector_store_id = p.openai_vector_store_id
                # Tag every LLM call (extraction + category + any nested
                # fallback) with the logical task so dashboard cost
                # breakdowns attribute correctly.
                with task_context("paper_extract"):
                    extraction, raw, new_file_id, new_assistant_id, new_thread_id, new_vector_store_id = extract_knowledge_from_paper(
                        pdf_filepath=str(pdf_path),
                        prompt=cfg["extraction_prompt"],
                        api_key=cfg["openai_api_key"],
                        model=task_model_name(cfg, "paper_extract"),
                        reasoning_effort=task_reasoning_effort(cfg, "paper_extract"),
                        cached_file_id=cached_file_id,
                        cached_assistant_id=cached_assistant_id,
                        cached_vector_store_id=cached_vector_store_id,
                        fallback_text=p.extracted_text or "",
                        first_page_image_path=(
                            str(resolve_artifact_path(p.first_page_image_path))
                            if cfg.get("use_first_page_image") and p.first_page_image_path
                            else None
                        ),
                        file_hash=p.file_hash,
                    )

                if not raw or not raw.strip():
                    raise PaperExtractionError(
                        "模型返回了空响应（可能是 file_search 没命中 PDF，或网络中断）。请重试，或检查 PDF 是否可读。",
                        raw=raw or "",
                        file_id=new_file_id or "",
                        assistant_id=new_assistant_id or "",
                    )

                stage = PIPELINE_STATUS_PARSING
                _set_pipeline_state(
                    db,
                    p,
                    status=PIPELINE_STATUS_PARSING,
                    retry_count=attempt - 1,
                )
                extraction = parse_extraction_response(raw)
                if not isinstance(extraction, dict):
                    raise PaperExtractionError(
                        "抽取响应不是 JSON 对象",
                        raw=raw,
                        file_id=new_file_id or "",
                        assistant_id=new_assistant_id or "",
                    )
                if extraction_has_critical_issues(extraction):
                    issues = "；".join(extraction_quality_issues(extraction))
                    raise PaperExtractionError(
                        f"抽取结果缺少关键内容: {issues}",
                        raw=raw,
                        file_id=new_file_id or "",
                        assistant_id=new_assistant_id or "",
                    )

                stage = PIPELINE_STATUS_GRAPHING
                _set_pipeline_state(
                    db,
                    p,
                    status=PIPELINE_STATUS_GRAPHING,
                    retry_count=attempt - 1,
                )

                if new_file_id and new_file_id != cached_file_id:
                    p.openai_file_id = new_file_id
                if new_vector_store_id and new_vector_store_id != cached_vector_store_id:
                    p.openai_vector_store_id = new_vector_store_id
                if new_assistant_id and new_assistant_id != cached_assistant_id:
                    save_config({"openai_assistant_id": new_assistant_id})
                if new_thread_id:
                    p.openai_thread_id = new_thread_id
                    p.thread_created_at = datetime.now(timezone.utc)
                    p.chat_history = []

                p.raw_llm_response = raw
                p.extraction_model = task_model_name(cfg, "paper_extract")
                p.title = (extraction.get("title") or p.filename)[:200]
                p.authors = extraction.get("authors") or []
                p.paper_category_model = derive_model_paper_category(p, extraction)
                p.paper_team_model = derive_model_paper_team(p, extraction)

                add_nodes_from_paper_extraction(
                    extraction,
                    p.id,
                    cfg["openai_api_key"],
                    task_model_id(cfg, "embedding"),
                    cfg["similarity_threshold"],
                    db,
                )

                p.processed = True
                p.processed_at = datetime.now(timezone.utc)
                p.processing_status = PIPELINE_STATUS_DONE
                p.retry_count = attempt - 1
                p.error = None
                p.last_error_stage = None
                p.last_error_reason = None
                p.last_error_recoverable = None
                db.commit()
                sync_record_from_paper(p, event="process")
                processing_state["done"] += 1
                processing_state["succeeded"] += 1
                _run_wiki_compile_phase(p, db, cfg)
                return
            except PaperExtractionError as e:
                if e.raw:
                    p.raw_llm_response = e.raw
                if e.file_id and not p.openai_file_id:
                    p.openai_file_id = e.file_id
                if e.assistant_id:
                    try:
                        save_config({"openai_assistant_id": e.assistant_id})
                    except Exception:
                        pass
                reason = short_error_reason(e)
                recoverable = is_recoverable_error(e)
                logger.exception(
                    "paper extraction failed paper_id=%s filename=%s stage=%s attempt=%s/%s",
                    paper_id,
                    p.filename if p else None,
                    stage,
                    attempt,
                    max_retries,
                )
            except Exception as e:
                reason = short_error_reason(e)
                recoverable = is_recoverable_error(e)
                logger.exception(
                    "paper processing crashed paper_id=%s filename=%s stage=%s attempt=%s/%s",
                    paper_id,
                    p.filename if p else None,
                    stage,
                    attempt,
                    max_retries,
                )

            p.processed = False
            p.processed_at = None
            if attempt < max_retries and recoverable:
                _set_pipeline_state(
                    db,
                    p,
                    status=stage,
                    retry_count=attempt,
                    error=reason,
                    error_stage=stage,
                    error_recoverable=True,
                )
                sync_record_from_paper(p, event="process_retry")
                delay = compute_backoff_seconds(
                    attempt,
                    base_seconds=backoff_base_s,
                    max_seconds=backoff_max_s,
                )
                if delay > 0:
                    time.sleep(delay)
                attempt += 1
                continue

            _set_pipeline_state(
                db,
                p,
                status=PIPELINE_STATUS_FAILED,
                retry_count=attempt - 1,
                error=reason,
                error_stage=stage,
                error_recoverable=recoverable,
            )
            sync_record_from_paper(p, event="process_error")
            processing_state["errors"] += 1
            processing_state["failed_papers"].append(_paper_failure_item(p))
            return
    finally:
        db.close()


def _process_all_background():
    from database import SessionLocal
    db = SessionLocal()
    try:
        pending = db.query(Paper).filter(
            Paper.processed == False, Paper.error == None
        ).all()
        ids = [p.id for p in pending]
    finally:
        db.close()

    _process_many_background(ids)


def _process_one_background(paper_id: str):
    _process_many_background([paper_id])


def _process_many_background(paper_ids: list[int]):
    processing_state["total"] = len(paper_ids)
    processing_state["done"] = 0
    processing_state["succeeded"] = 0
    processing_state["errors"] = 0
    processing_state["failed_papers"] = []
    processing_state["max_retries"] = 0
    processing_state["running"] = True
    try:
        for paper_id in paper_ids:
            _process_single(paper_id)
    finally:
        processing_state["running"] = False
        processing_state["current"] = ""


def _prepare_reprocess(db: Session, p: Paper):
    remove_nodes_for_paper(db, p.id)
    p.processed = False
    p.processed_at = None
    p.processing_status = PIPELINE_STATUS_SCANNING
    p.retry_count = 0
    p.last_error_stage = None
    p.last_error_reason = None
    p.last_error_recoverable = None
    p.raw_llm_response = None
    p.extraction_model = None
    p.paper_category_model = None
    p.paper_team_model = None
    p.error = None
    # Drop cached OpenAI handles so reprocess re-uploads the PDF and opens a
    # fresh thread. Reusing a stale file_id against a new per-thread vector
    # store often returns zero file_search hits → model answers with "{}".
    p.openai_file_id = None
    p.openai_vector_store_id = None
    p.openai_thread_id = None
    p.thread_created_at = None
    db.commit()
    sync_record_from_paper(p, event="reprocess_prepare")


def _reconcile_failed_papers(db: Session) -> list[Paper]:
    papers = db.query(Paper).order_by(Paper.id.asc()).all()
    healed: list[Paper] = []
    failed: list[Paper] = []

    for paper in papers:
        if _reconcile_processed_paper(paper):
            healed.append(paper)
        if (
            not paper.processed
            and (
                paper.processing_status == PIPELINE_STATUS_FAILED
                or bool(paper.error)
                or bool(paper.last_error_reason)
            )
        ):
            failed.append(paper)

    if healed:
        db.commit()
        for paper in healed:
            sync_record_from_paper(paper, event="auto_repair")

    return failed


@router.post("/process")
def process_all(background_tasks: BackgroundTasks):
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    background_tasks.add_task(_process_all_background)
    return {"message": "Processing started", **processing_state}


@router.post("/papers/{paper_id}/process")
def process_one(paper_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    background_tasks.add_task(_process_one_background, paper_id)
    return {"message": f"Processing started for {p.filename}", **processing_state}


@router.post("/papers/{paper_id}/retry")
def retry_paper(paper_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Clear error and retry processing."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    _prepare_reprocess(db, p)
    background_tasks.add_task(_process_one_background, paper_id)
    return {"message": f"Retry started for {p.filename}", **processing_state}


@router.post("/papers/retry_failed")
def retry_failed_papers(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Retry all failed papers in one batch."""
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}

    failed = _reconcile_failed_papers(db)
    if not failed:
        return {
            "message": "No failed papers to retry",
            "retried": 0,
            "failed_papers": [],
            **processing_state,
        }

    ids: list[int] = []
    failed_items: list[dict] = []
    for paper in failed:
        failed_items.append(_paper_failure_item(paper))
        _prepare_reprocess(db, paper)
        ids.append(paper.id)

    background_tasks.add_task(_process_many_background, ids)
    return {
        "message": f"Retry started for {len(ids)} failed papers",
        "retried": len(ids),
        "failed_papers": failed_items,
        **processing_state,
    }


@router.post("/papers/{paper_id}/reprocess")
def reprocess_paper(paper_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Clear this paper's extracted graph and run LLM extraction again."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    _prepare_reprocess(db, p)
    background_tasks.add_task(_process_one_background, paper_id)
    return {"message": f"Reprocessing started for {p.filename}"}


@router.put("/papers/{paper_id}/response")
def update_paper_response(
    paper_id: str,
    body: RawResponseUpdate,
    db: Session = Depends(get_db),
):
    """Save a manually repaired model response and rebuild this paper's graph."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)

    raw = body.raw_llm_response.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Response 不能为空")

    try:
        extraction = parse_extraction_response(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Response 仍然不是合法 JSON：{e}")

    if not isinstance(extraction, dict):
        raise HTTPException(status_code=400, detail="Response JSON 必须是对象")
    if extraction_has_critical_issues(extraction):
        issues = "；".join(extraction_quality_issues(extraction))
        raise HTTPException(status_code=400, detail=f"Response 缺少关键内容：{issues}")

    cfg = load_config()
    # A light edit (e.g. fixing a formula) leaves the graph structure
    # untouched, so skip the expensive node rebuild + re-embedding.
    if body.rebuild_graph:
        remove_nodes_for_paper(db, p.id)
    p.raw_llm_response = body.raw_llm_response
    p.title = (extraction.get("title") or p.filename)[:200]
    p.authors = extraction.get("authors") or []
    sync_paper_category_fields(p, extraction, overwrite_model=True)
    sync_paper_team_fields(p, extraction, overwrite_model=True)
    if body.rebuild_graph:
        # A full repair re-marks the paper as freshly processed.
        p.processed = True
        p.processed_at = datetime.now(timezone.utc)
        p.processing_status = PIPELINE_STATUS_DONE
        p.retry_count = 0
        p.error = None
        p.last_error_stage = None
        p.last_error_reason = None
        p.last_error_recoverable = None
    db.flush()

    if body.rebuild_graph:
        add_nodes_from_paper_extraction(
            extraction,
            p.id,
            cfg.get("openai_api_key", ""),
            task_model_id(cfg, "embedding"),
            cfg.get("similarity_threshold", 0.6),
            db,
        )
    db.commit()
    sync_record_from_paper(p, event="manual_response_edit")
    db.refresh(p)
    return _serialize_paper_detail(p, db)


@router.put("/papers/{paper_id}/notes")
def update_paper_notes(
    paper_id: str,
    body: NotesUpdate,
    db: Session = Depends(get_db),
):
    """Save user-authored markdown notes for a paper."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    old_notes = p.notes or ""
    p.notes = body.notes
    db.commit()
    # Clean up pasted images that got removed from this paper's notes and
    # aren't referenced by any other paper. Runs after commit so a GC failure
    # can't take down the save.
    try:
        gc_on_notes_update(db, p.id, old_notes, body.notes)
    except Exception:
        pass
    sync_record_from_paper(p, event="notes_update")
    db.refresh(p)
    return _serialize_paper_detail(p, db)


@router.put("/papers/{paper_id}/category")
def update_paper_category(
    paper_id: str,
    body: CategoryUpdate,
    db: Session = Depends(get_db),
):
    """Save or clear a human override for the paper's lane/category."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)

    raw_value = (body.category or "").strip()
    if raw_value:
        # normalize_paper_category already validates against the ACTIVE list
        # (incl. user-added custom categories), so a truthy result is valid.
        normalized = normalize_paper_category(raw_value)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法分类：{raw_value}。允许值：{', '.join(get_active_categories())}",
            )
        p.paper_category_override = normalized
    else:
        p.paper_category_override = None

    db.commit()
    sync_record_from_paper(p, event="category_override_update")
    db.refresh(p)
    return _serialize_paper_detail(p, db)


# ── paper-category taxonomy management ─────────────────────────────────
#
# The category list is user-editable and persisted in config. Renames/deletes
# also migrate affected papers' override + model fields so the change is
# reflected everywhere (lanes, mobile grouping after re-sync).


class CategoryNameInput(BaseModel):
    name: str


class CategoryRenameInput(BaseModel):
    new_name: str


class BulkCategoryInput(BaseModel):
    # Paper IDs are UUID strings (legacy INT tolerated).
    paper_ids: list
    category: Optional[str] = None


def _category_payload(db: Session) -> dict:
    active = get_active_categories()
    counts = {c: 0 for c in active}
    for p in db.query(Paper).all():
        cat = effective_paper_category(p, _safe_parse(p.raw_llm_response))
        counts[cat] = counts.get(cat, 0) + 1
    return {
        "categories": [
            {
                "name": c,
                "builtin": c in DEFAULT_PAPER_CATEGORY_OPTIONS,
                "removable": c != PAPER_CATEGORY_OTHER,
                "count": counts.get(c, 0),
            }
            for c in active
        ]
    }


@router.get("/paper-categories")
def list_paper_categories(db: Session = Depends(get_db)):
    return _category_payload(db)


@router.post("/paper-categories")
def add_paper_category(body: CategoryNameInput, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名不能为空")
    active = get_active_categories()
    if name in active:
        raise HTTPException(status_code=400, detail=f"分类已存在：{name}")
    # Keep "其他" last.
    set_active_categories([c for c in active if c != PAPER_CATEGORY_OTHER] + [name])
    return _category_payload(db)


@router.put("/paper-categories/{name}")
def rename_paper_category(
    name: str, body: CategoryRenameInput, db: Session = Depends(get_db)
):
    new_name = (body.new_name or "").strip()
    active = get_active_categories()
    if name == PAPER_CATEGORY_OTHER:
        raise HTTPException(status_code=400, detail="“其他”是保留分类，不能重命名")
    if name not in active:
        raise HTTPException(status_code=404, detail=f"分类不存在：{name}")
    if not new_name:
        raise HTTPException(status_code=400, detail="新分类名不能为空")
    if new_name != name and new_name in active:
        raise HTTPException(status_code=400, detail=f"目标分类已存在：{new_name}")
    if new_name == name:
        return {**_category_payload(db), "migrated": 0}

    set_active_categories([new_name if c == name else c for c in active])
    # Migrate both manual override and model-assigned values so the rename
    # carries through regardless of how a paper got that category.
    migrated = 0
    for p in db.query(Paper).all():
        changed = False
        if (p.paper_category_override or "") == name:
            p.paper_category_override = new_name
            changed = True
        if (p.paper_category_model or "") == name:
            p.paper_category_model = new_name
            changed = True
        if changed:
            migrated += 1
    db.commit()
    for p in db.query(Paper).all():
        if (p.paper_category_override or "") == new_name or (
            p.paper_category_model or ""
        ) == new_name:
            sync_record_from_paper(p, event="category_rename")
    return {**_category_payload(db), "migrated": migrated}


@router.delete("/paper-categories/{name}")
def delete_paper_category(name: str, db: Session = Depends(get_db)):
    active = get_active_categories()
    if name == PAPER_CATEGORY_OTHER:
        raise HTTPException(status_code=400, detail="“其他”是保留分类，不能删除")
    if name not in active:
        raise HTTPException(status_code=404, detail=f"分类不存在：{name}")
    set_active_categories([c for c in active if c != name])
    # Affected papers fall back (override/model cleared → effective re-derives).
    migrated = 0
    affected: list = []
    for p in db.query(Paper).all():
        changed = False
        if (p.paper_category_override or "") == name:
            p.paper_category_override = None
            changed = True
        if (p.paper_category_model or "") == name:
            p.paper_category_model = None
            changed = True
        if changed:
            migrated += 1
            affected.append(p.id)
    db.commit()
    for p in db.query(Paper).filter(Paper.id.in_([str(i) for i in affected])).all():
        sync_record_from_paper(p, event="category_delete")
    return {**_category_payload(db), "migrated": migrated}


@router.post("/papers/bulk-category")
def bulk_set_paper_category(body: BulkCategoryInput, db: Session = Depends(get_db)):
    ids = [str(x) for x in (body.paper_ids or []) if str(x).strip()]
    if not ids:
        return {"updated": 0, "category": None}
    raw = (body.category or "").strip()
    normalized = None
    if raw:
        normalized = normalize_paper_category(raw)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法分类：{raw}。允许值：{', '.join(get_active_categories())}",
            )
    updated = 0
    touched: list = []
    for p in db.query(Paper).filter(Paper.id.in_(ids)).all():
        p.paper_category_override = normalized  # None ⇒ follow model
        updated += 1
        touched.append(p)
    db.commit()
    for p in touched:
        sync_record_from_paper(p, event="bulk_category")
    return {"updated": updated, "category": normalized}


# ── team override (per paper) ──────────────────────────────────────────


@router.put("/papers/{paper_id}/team")
def update_paper_team(paper_id: str, body: TeamUpdate, db: Session = Depends(get_db)):
    """Save or clear a human override for the paper's team."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)

    raw_value = (body.team or "").strip()
    if raw_value and raw_value.lower() != TEAM_OTHER:
        normalized = normalize_team(raw_value)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法团队：{raw_value}。允许值：{', '.join(get_active_team_names())}",
            )
        p.paper_team_override = normalized
    else:
        p.paper_team_override = None

    db.commit()
    sync_record_from_paper(p, event="team_override_update")
    db.refresh(p)
    return _serialize_paper_detail(p, db)


# ── paper-team registry management ─────────────────────────────────────
#
# Teams differ from categories: each carries a core-author list that drives
# auto-assignment. Editing a team's authors re-derives existing papers, so the
# mutating endpoints all call _recompute_team_models afterwards.


class TeamInput(BaseModel):
    name: str
    authors: Optional[list] = None


class TeamRenameInput(BaseModel):
    new_name: Optional[str] = None
    authors: Optional[list] = None


class BulkTeamInput(BaseModel):
    paper_ids: list
    team: Optional[str] = None


def _recompute_team_models(db: Session) -> int:
    """Re-derive every paper's model-assigned team from its authors against the
    current registry. Used after the team list / authors change."""
    changed = 0
    touched: list = []
    for p in db.query(Paper).all():
        if sync_paper_team_fields(p, _safe_parse(p.raw_llm_response), overwrite_model=True):
            changed += 1
            touched.append(p)
    if touched:
        db.commit()
        for p in touched:
            sync_record_from_paper(p, event="team_recompute")
    return changed


def _team_payload(db: Session) -> dict:
    teams = get_active_teams()
    counts: dict = {t["name"]: 0 for t in teams}
    counts[TEAM_OTHER] = 0
    for p in db.query(Paper).all():
        t = effective_paper_team(p, _safe_parse(p.raw_llm_response))
        counts[t] = counts.get(t, 0) + 1
    seed_names = {t["name"] for t in DEFAULT_TEAMS}
    return {
        "teams": [
            {
                "name": t["name"],
                "authors": list(t.get("authors") or []),
                "builtin": t["name"] in seed_names,
                "count": counts.get(t["name"], 0),
            }
            for t in teams
        ],
        "others_count": counts.get(TEAM_OTHER, 0),
    }


@router.get("/paper-teams")
def list_paper_teams(db: Session = Depends(get_db)):
    return _team_payload(db)


@router.post("/paper-teams")
def add_paper_team(body: TeamInput, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="团队名不能为空")
    if name.lower() == TEAM_OTHER:
        raise HTTPException(status_code=400, detail="“others”是保留团队")
    teams = get_active_teams()
    if any(t["name"] == name for t in teams):
        raise HTTPException(status_code=400, detail=f"团队已存在：{name}")
    set_active_teams(teams + [{"name": name, "authors": body.authors or []}])
    _recompute_team_models(db)  # new team may match existing papers
    return _team_payload(db)


@router.put("/paper-teams/{name}")
def rename_paper_team(name: str, body: TeamRenameInput, db: Session = Depends(get_db)):
    teams = get_active_teams()
    target = next((t for t in teams if t["name"] == name), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"团队不存在：{name}")
    new_name = (body.new_name or name).strip() or name
    if new_name.lower() == TEAM_OTHER:
        raise HTTPException(status_code=400, detail="“others”是保留团队")
    if new_name != name and any(t["name"] == new_name for t in teams):
        raise HTTPException(status_code=400, detail=f"目标团队已存在：{new_name}")

    new_authors = target.get("authors") if body.authors is None else body.authors
    set_active_teams([
        {
            "name": new_name if t["name"] == name else t["name"],
            "authors": new_authors if t["name"] == name else t.get("authors"),
        }
        for t in teams
    ])
    # Carry a rename through stored override/model values.
    if new_name != name:
        for p in db.query(Paper).all():
            if (p.paper_team_override or "") == name:
                p.paper_team_override = new_name
            if (p.paper_team_model or "") == name:
                p.paper_team_model = new_name
        db.commit()
    _recompute_team_models(db)  # author edits change who matches
    return _team_payload(db)


@router.delete("/paper-teams/{name}")
def delete_paper_team(name: str, db: Session = Depends(get_db)):
    teams = get_active_teams()
    if not any(t["name"] == name for t in teams):
        raise HTTPException(status_code=404, detail=f"团队不存在：{name}")
    set_active_teams([t for t in teams if t["name"] != name])
    for p in db.query(Paper).all():
        if (p.paper_team_override or "") == name:
            p.paper_team_override = None
        if (p.paper_team_model or "") == name:
            p.paper_team_model = None
    db.commit()
    _recompute_team_models(db)
    return _team_payload(db)


@router.post("/paper-teams/recompute")
def recompute_paper_teams(db: Session = Depends(get_db)):
    changed = _recompute_team_models(db)
    return {**_team_payload(db), "recomputed": changed}


@router.post("/papers/bulk-team")
def bulk_set_paper_team(body: BulkTeamInput, db: Session = Depends(get_db)):
    ids = [str(x) for x in (body.paper_ids or []) if str(x).strip()]
    if not ids:
        return {"updated": 0, "team": None}
    raw = (body.team or "").strip()
    normalized = None
    if raw and raw.lower() != TEAM_OTHER:
        normalized = normalize_team(raw)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail=f"非法团队：{raw}。允许值：{', '.join(get_active_team_names())}",
            )
    updated = 0
    touched: list = []
    for p in db.query(Paper).filter(Paper.id.in_(ids)).all():
        p.paper_team_override = normalized  # None ⇒ follow model
        updated += 1
        touched.append(p)
    db.commit()
    for p in touched:
        sync_record_from_paper(p, event="bulk_team")
    return {"updated": updated, "team": normalized}


@router.get("/papers/{paper_id}/chat")
def get_chat(paper_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    return _chat_state(p)


@router.post("/papers/{paper_id}/chat")
def post_chat(paper_id: str, body: ChatMessageInput, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    if not p.processed:
        raise HTTPException(status_code=400, detail="论文尚未完成处理，无法追问")

    cfg = load_config()
    api_key = cfg.get("openai_api_key")
    assistant_id = cfg.get("openai_assistant_id")
    model = task_model_name(cfg, "paper_chat")
    model_entry = get_model_entry(cfg, model)
    provider = get_provider_entry(cfg, model_entry.get("provider_id", "")) if model_entry else None
    provider_type = str(provider.get("provider_type") or "openai") if provider else "openai"
    uses_local_context = provider_type == "codex_cli"
    if not uses_local_context and not p.openai_file_id:
        raise HTTPException(status_code=400, detail="论文缺少远程文件索引，无法追问")
    if not uses_local_context and not api_key:
        raise HTTPException(status_code=400, detail="OpenAI 未配置")
    if not uses_local_context and not model_uses_responses_api(model) and not assistant_id:
        raise HTTPException(status_code=400, detail="assistant 未创建")

    user_text = (body.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    try:
        with task_context("paper_chat"):
            reply, thread_id, was_recreated, vector_store_id = run_chat_turn(
                api_key=api_key or "",
                model=model,
                assistant_id=assistant_id or "",
                file_id=p.openai_file_id or "",
                user_message=user_text,
                reasoning_effort=task_reasoning_effort(cfg, "paper_chat"),
                cached_vector_store_id=p.openai_vector_store_id,
                cached_thread_id=p.openai_thread_id,
                chat_history=p.chat_history,
                paper_title=p.title or p.filename,
                paper_notes=p.notes or "",
                paper_raw_llm_response=p.raw_llm_response or "",
                paper_extracted_text=p.extracted_text or "",
                first_page_image_path=(
                    str(resolve_artifact_path(p.first_page_image_path))
                    if cfg.get("use_first_page_image") and p.first_page_image_path
                    else None
                ),
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"对话失败: {e}")

    now = datetime.now(timezone.utc)
    history = list(p.chat_history or [])
    if was_recreated:
        # Old thread lost — server-side memory reset. Flag it in history so
        # the UI can render a divider.
        history.append({"role": "system", "content": "会话已过期，已开启新会话。", "ts": now.isoformat()})
        p.thread_created_at = now
    history.append({"role": "user", "content": user_text, "ts": now.isoformat()})
    history.append({"role": "assistant", "content": reply, "ts": datetime.now(timezone.utc).isoformat()})

    p.openai_thread_id = thread_id
    if vector_store_id:
        p.openai_vector_store_id = vector_store_id
    p.chat_history = history
    if p.thread_created_at is None:
        p.thread_created_at = now
    db.commit()
    sync_record_from_paper(p, event="chat_turn")
    db.refresh(p)
    return _chat_state(p)


@router.delete("/papers/{paper_id}/chat")
def reset_chat(paper_id: str, db: Session = Depends(get_db)):
    """Drop the local history and forget the thread id. A fresh thread will
    be created on the next chat turn."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    p.openai_thread_id = None
    p.thread_created_at = None
    p.chat_history = []
    db.commit()
    sync_record_from_paper(p, event="chat_reset")
    db.refresh(p)
    return _chat_state(p)


@router.get("/status")
def get_status():
    return processing_state
