from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Paper, KnowledgeNode
from config import load_config, save_config
from services.scanner_service import scan_directory
from services.pdf_service import extract_text, render_first_page
from services.vlm_service import extract_knowledge_from_paper, PaperExtractionError
from services.graph_service import add_nodes_from_paper_extraction

router = APIRouter(prefix="/api", tags=["papers"])

processing_state = {"running": False, "total": 0, "done": 0, "errors": 0, "current": ""}


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


@router.get("/papers/{paper_id}")
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")

    nodes = db.query(KnowledgeNode).filter(
        KnowledgeNode.source_paper_ids.contains(str(paper_id))
    ).all()

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
        "error": p.error,
        "has_first_page_image": bool(p.first_page_image_path),
        "knowledge_nodes": [
            {"id": n.id, "title": n.title, "node_type": n.node_type, "tags": n.tags or []}
            for n in nodes
        ],
    }


@router.get("/papers/{paper_id}/file")
def serve_pdf(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    return FileResponse(p.filepath, media_type="application/pdf")


@router.get("/papers/{paper_id}/first_page")
def serve_first_page(paper_id: int, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p or not p.first_page_image_path:
        raise HTTPException(status_code=404, detail="First page image not found")
    return FileResponse(p.first_page_image_path, media_type="image/png")


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
            return

        processing_state["current"] = p.filename

        # Local preprocessing — no longer fed to the LLM, but still useful:
        #   - extracted_text: shown in the frontend debug drawer, fallback search
        #   - first_page_image: UI thumbnails (papers grid, node detail cards)
        if not p.extracted_text or not p.num_pages:
            try:
                text, num_pages = extract_text(p.filepath)
                p.extracted_text = text
                p.num_pages = num_pages
                db.commit()
            except Exception:
                pass

        if not p.first_page_image_path:
            img_path = render_first_page(p.filepath, p.file_hash)
            if img_path:
                p.first_page_image_path = img_path
                db.commit()

        # Call LLM via Assistants API + file_search.
        # The PDF itself is uploaded; cached file_id + assistant_id get reused.
        cached_file_id = p.openai_file_id
        cached_assistant_id = cfg.get("openai_assistant_id") or None

        extraction, raw, new_file_id, new_assistant_id = extract_knowledge_from_paper(
            pdf_filepath=p.filepath,
            prompt=cfg["extraction_prompt"],
            api_key=cfg["openai_api_key"],
            model=cfg["vlm_model"],
            cached_file_id=cached_file_id,
            cached_assistant_id=cached_assistant_id,
        )

        # Persist the cached identifiers so subsequent runs skip the upload /
        # assistant creation round-trips.
        if new_file_id and new_file_id != cached_file_id:
            p.openai_file_id = new_file_id
        if new_assistant_id and new_assistant_id != cached_assistant_id:
            save_config({"openai_assistant_id": new_assistant_id})

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
        processing_state["done"] += 1
    except PaperExtractionError as e:
        p = db.query(Paper).filter(Paper.id == paper_id).first()
        if p:
            if e.raw:
                p.raw_llm_response = e.raw
            if e.file_id and not p.openai_file_id:
                p.openai_file_id = e.file_id
            p.error = str(e)[:500]
            db.commit()
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
    background_tasks.add_task(_process_single, paper_id)
    return {"message": f"Processing started for {p.filename}"}


@router.post("/papers/{paper_id}/retry")
def retry_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Clear error and retry processing."""
    p = db.query(Paper).filter(Paper.id == paper_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Paper not found")
    p.error = None
    p.processed = False
    db.commit()
    background_tasks.add_task(_process_single, paper_id)
    return {"message": f"Retry started for {p.filename}"}


@router.get("/status")
def get_status():
    return processing_state
