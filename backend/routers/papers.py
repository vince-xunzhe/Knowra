from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import Paper, KnowledgeNode
from config import load_config, save_config
from path_utils import (
    portable_data_path,
    resolve_artifact_path,
    resolve_paper_path,
)
from services.scanner_service import scan_directory
from services.pdf_service import extract_text, render_first_page
from services.vlm_service import (
    extract_knowledge_from_paper,
    model_uses_responses_api,
    parse_extraction_response,
    run_chat_turn,
    PaperExtractionError,
)
from services.graph_service import add_nodes_from_paper_extraction, remove_nodes_for_paper
from services.note_images_gc import gc_on_notes_update
from services.paper_record_service import (
    record_path_for_paper,
    record_relpath_for_paper,
    record_url_for_paper,
    sync_paper_from_record,
    sync_record_from_paper,
)


def _safe_parse(raw):
    if not raw:
        return None
    try:
        return parse_extraction_response(raw)
    except Exception:
        return None


router = APIRouter(prefix="/api", tags=["papers"])

processing_state = {"running": False, "total": 0, "done": 0, "errors": 0, "current": ""}


class RawResponseUpdate(BaseModel):
    raw_llm_response: str


class NotesUpdate(BaseModel):
    notes: str


class ChatMessageInput(BaseModel):
    message: str


# OpenAI Assistants threads expire ~60 days after last activity. Mirror that in
# the UI countdown so the user knows when follow-up chat stops working.
THREAD_TTL_DAYS = 60


def _nodes_for_paper(db: Session, paper_id: int):
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
    created = p.thread_created_at
    expires_at = None
    days_remaining = None
    if created is not None:
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
        "ready": bool(p.openai_file_id) and p.processed,
    }


def _serialize_paper_detail(p: Paper, db: Session) -> dict:
    nodes = _nodes_for_paper(db, p.id)
    has_first_page_image = bool(
        p.first_page_image_path
        and resolve_artifact_path(p.first_page_image_path).exists()
    )
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


@router.get("/papers")
def list_papers(db: Session = Depends(get_db)):
    papers = db.query(Paper).order_by(Paper.created_at.desc()).all()
    changed = False
    for paper in papers:
        changed = _sync_bulk_record_state(paper) or changed
    if changed:
        db.commit()
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
            "error": p.error,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in papers
    ]


def _sync_bulk_record_state(p: Paper) -> bool:
    return sync_paper_from_record(p)


@router.get("/papers/{paper_id}")
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    return _serialize_paper_detail(p, db)


@router.get("/papers/{paper_id}/file")
def serve_pdf(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    pdf_path = resolve_paper_path(p.filepath)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=p.filename)


@router.get("/papers/{paper_id}/record")
def serve_paper_record(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    path = record_path_for_paper(p)
    if not path.exists():
        sync_record_from_paper(p, event="bootstrap")
    return FileResponse(str(path), media_type="text/markdown; charset=utf-8", filename=path.name)


@router.get("/papers/{paper_id}/first_page")
def serve_first_page(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p or not p.first_page_image_path:
        raise HTTPException(status_code=404, detail="First page image not found")
    image_path = resolve_artifact_path(p.first_page_image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="First page image not found")
    return FileResponse(str(image_path), media_type="image/png")


def _process_single(paper_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        p = db.query(Paper).filter(Paper.id == paper_id).first()
        if not p or p.processed:
            return
        cfg = load_config()
        if not cfg.get("openai_api_key"):
            p.error = "OpenAI API key not configured"
            db.commit()
            sync_record_from_paper(p, event="process_error")
            processing_state["errors"] += 1
            return

        processing_state["current"] = p.filename
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

        # Local preprocessing — no longer fed to the LLM, but still useful:
        #   - extracted_text: shown in the frontend debug drawer, fallback search
        #   - first_page_image: UI thumbnails (papers grid, node detail cards)
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

        # Call LLM via Assistants API + file_search.
        # The PDF itself is uploaded; cached file_id + assistant_id get reused.
        cached_file_id = p.openai_file_id
        cached_assistant_id = cfg.get("openai_assistant_id") or None
        cached_vector_store_id = p.openai_vector_store_id

        extraction, raw, new_file_id, new_assistant_id, new_thread_id, new_vector_store_id = extract_knowledge_from_paper(
            pdf_filepath=str(pdf_path),
            prompt=cfg["extraction_prompt"],
            api_key=cfg["openai_api_key"],
            model=cfg["vlm_model"],
            cached_file_id=cached_file_id,
            cached_assistant_id=cached_assistant_id,
            cached_vector_store_id=cached_vector_store_id,
        )

        # Persist the cached identifiers so subsequent runs skip the upload /
        # assistant creation round-trips.
        if new_file_id and new_file_id != cached_file_id:
            p.openai_file_id = new_file_id
        if new_vector_store_id and new_vector_store_id != cached_vector_store_id:
            p.openai_vector_store_id = new_vector_store_id
        if new_assistant_id and new_assistant_id != cached_assistant_id:
            save_config({"openai_assistant_id": new_assistant_id})
        # Keep the thread so follow-up chat can reuse it. Reset history since
        # a reprocess implicitly starts a fresh conversation.
        if new_thread_id:
            p.openai_thread_id = new_thread_id
            p.thread_created_at = datetime.now(timezone.utc)
            p.chat_history = []

        p.raw_llm_response = raw
        p.title = (extraction.get("title") or p.filename)[:200]
        p.authors = extraction.get("authors") or []

        add_nodes_from_paper_extraction(
            extraction,
            p.id,
            cfg["openai_api_key"],
            cfg["embedding_model"],
            cfg["similarity_threshold"],
            db,
        )

        p.processed = True
        p.processed_at = datetime.now(timezone.utc)
        p.error = None
        db.commit()
        sync_record_from_paper(p, event="process")
        processing_state["done"] += 1

        # Phase 1 wiki compile. Failures here must not break processing —
        # the raw layer is already saved and the user can manually retry
        # via the Wiki page.
        try:
            from services.wiki_compiler import (
                compile_paper_page,
                compile_concept_pages_for_paper,
            )
            compile_model = cfg["wiki_compile_model"]
            compile_paper_page(p, cfg["openai_api_key"], compile_model)
            compile_concept_pages_for_paper(
                p.id, db, cfg["openai_api_key"], compile_model
            )
        except Exception as compile_err:
            print(f"[wiki] compile after process failed for paper {p.id}: {compile_err}")
    except PaperExtractionError as e:
        p = db.query(Paper).filter(Paper.id == paper_id).first()
        if p:
            if e.raw:
                p.raw_llm_response = e.raw
            if e.file_id and not p.openai_file_id:
                p.openai_file_id = e.file_id
            p.error = str(e)[:500]
            db.commit()
            sync_record_from_paper(p, event="process_error")
        if e.assistant_id:
            try:
                save_config({"openai_assistant_id": e.assistant_id})
            except Exception:
                pass
        processing_state["errors"] += 1
    except Exception as e:
        p = db.query(Paper).filter(Paper.id == paper_id).first()
        if p:
            p.error = str(e)[:500]
            db.commit()
            sync_record_from_paper(p, event="process_error")
        processing_state["errors"] += 1
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
        processing_state["total"] = len(ids)
        processing_state["done"] = 0
        processing_state["errors"] = 0
        processing_state["running"] = True
    finally:
        db.close()

    for pid in ids:
        _process_single(pid)

    processing_state["running"] = False
    processing_state["current"] = ""


def _process_one_background(paper_id: int):
    processing_state["total"] = 1
    processing_state["done"] = 0
    processing_state["errors"] = 0
    processing_state["running"] = True
    try:
        _process_single(paper_id)
    finally:
        processing_state["running"] = False
        processing_state["current"] = ""


def _prepare_reprocess(db: Session, p: Paper):
    remove_nodes_for_paper(db, p.id)
    p.processed = False
    p.processed_at = None
    p.raw_llm_response = None
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


@router.post("/process")
def process_all(background_tasks: BackgroundTasks):
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    background_tasks.add_task(_process_all_background)
    return {"message": "Processing started"}


@router.post("/papers/{paper_id}/process")
def process_one(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    background_tasks.add_task(_process_one_background, paper_id)
    return {"message": f"Processing started for {p.filename}"}


@router.post("/papers/{paper_id}/retry")
def retry_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Clear error and retry processing."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    if processing_state["running"]:
        return {"message": "Processing already running", **processing_state}
    p.error = None
    p.processed = False
    db.commit()
    sync_record_from_paper(p, event="retry_prepare")
    background_tasks.add_task(_process_one_background, paper_id)
    return {"message": f"Retry started for {p.filename}"}


@router.post("/papers/{paper_id}/reprocess")
def reprocess_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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
    paper_id: int,
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

    cfg = load_config()
    remove_nodes_for_paper(db, p.id)
    p.raw_llm_response = body.raw_llm_response
    p.title = (extraction.get("title") or p.filename)[:200]
    p.authors = extraction.get("authors") or []
    p.processed = True
    p.processed_at = datetime.now(timezone.utc)
    p.error = None
    db.flush()

    add_nodes_from_paper_extraction(
        extraction,
        p.id,
        cfg.get("openai_api_key", ""),
        cfg.get("embedding_model", "text-embedding-3-small"),
        cfg.get("similarity_threshold", 0.6),
        db,
    )
    db.commit()
    sync_record_from_paper(p, event="manual_response_edit")
    db.refresh(p)
    return _serialize_paper_detail(p, db)


@router.put("/papers/{paper_id}/notes")
def update_paper_notes(
    paper_id: int,
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


@router.get("/papers/{paper_id}/chat")
def get_chat(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    return _chat_state(p)


@router.post("/papers/{paper_id}/chat")
def post_chat(paper_id: int, body: ChatMessageInput, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    _sync_paper_from_record_if_needed(db, p)
    if not p.processed or not p.openai_file_id:
        raise HTTPException(status_code=400, detail="论文尚未完成处理，无法追问")

    cfg = load_config()
    api_key = cfg.get("openai_api_key")
    assistant_id = cfg.get("openai_assistant_id")
    model = cfg.get("vlm_model", "gpt-4o")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI 未配置")
    if not model_uses_responses_api(model) and not assistant_id:
        raise HTTPException(status_code=400, detail="assistant 未创建")

    user_text = (body.message or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    try:
        reply, thread_id, was_recreated, vector_store_id = run_chat_turn(
            api_key=api_key,
            model=model,
            assistant_id=assistant_id,
            file_id=p.openai_file_id,
            user_message=user_text,
            cached_vector_store_id=p.openai_vector_store_id,
            cached_thread_id=p.openai_thread_id,
            chat_history=p.chat_history,
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
def reset_chat(paper_id: int, db: Session = Depends(get_db)):
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
