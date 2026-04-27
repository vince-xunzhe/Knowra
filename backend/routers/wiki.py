"""Router for the LLM-compiled wiki layer.

Exposes the concept pages produced by services.wiki_compiler so the frontend
can list them and read individual entries. Compile timestamps come straight
from each page's YAML frontmatter — no DB round-trip.

Also maintains a tiny in-memory ``compile_state`` mirrored to the frontend
via GET /api/wiki/status, so the user gets a live progress bar instead of
"the button did nothing".
"""
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from config import load_config
from database import get_db
from models import KnowledgeNode
from services.wiki_compiler import (
    compile_all_concept_pages,
    compile_all_paper_pages,
    compile_concept_page,
    compute_freshness_summary,
    list_concept_pages,
    list_paper_pages,
    read_concept_page,
    read_paper_page,
)


router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# Shared state for both "compile all papers" and "recompile all concepts".
# Only one job at a time — the route returns 409 if you try to start a
# second one while the first is still running. State is in-memory and
# resets on backend restart, which is fine for a personal-tool use case.
_state_lock = threading.Lock()
compile_state: dict = {
    "running": False,
    "kind": None,        # "papers" | "concepts" | None
    "total": 0,
    "done": 0,
    "errors": 0,
    "current": "",       # human-readable label of the in-flight item
    "started_at": None,  # ISO string
    "finished_at": None,
    "last_error": None,  # most recent error message, for surfacing in UI
    "model": None,       # which compile model is being used
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _begin(kind: str, total: int, model: str) -> None:
    with _state_lock:
        compile_state.update({
            "running": True,
            "kind": kind,
            "total": total,
            "done": 0,
            "errors": 0,
            "current": "",
            "started_at": _now_iso(),
            "finished_at": None,
            "last_error": None,
            "model": model,
        })


def _set_current(label: str) -> None:
    with _state_lock:
        compile_state["current"] = label


def _tick(success: bool, err: Optional[BaseException] = None) -> None:
    with _state_lock:
        compile_state["done"] += 1
        if not success:
            compile_state["errors"] += 1
            if err is not None:
                compile_state["last_error"] = f"{type(err).__name__}: {err}"


def _finish() -> None:
    with _state_lock:
        compile_state["running"] = False
        compile_state["current"] = ""
        compile_state["finished_at"] = _now_iso()


def _try_acquire(kind: str, total: int, model: str) -> bool:
    """Atomically transition state from idle → running. Returns False if
    another compile is already in progress."""
    with _state_lock:
        if compile_state["running"]:
            return False
        compile_state.update({
            "running": True,
            "kind": kind,
            "total": total,
            "done": 0,
            "errors": 0,
            "current": "",
            "started_at": _now_iso(),
            "finished_at": None,
            "last_error": None,
            "model": model,
        })
        return True


# --- listing / reading ------------------------------------------------------

@router.get("/concepts")
def list_concepts():
    return {"items": list_concept_pages()}


@router.get("/concepts/{filename}")
def get_concept(filename: str):
    page = read_concept_page(filename)
    if not page:
        raise HTTPException(status_code=404, detail="Concept page not found")
    return page


@router.get("/concepts/{filename}/raw")
def get_concept_raw(filename: str):
    page = read_concept_page(filename)
    if not page:
        raise HTTPException(status_code=404, detail="Concept page not found")
    return PlainTextResponse(page["raw"], media_type="text/markdown; charset=utf-8")


# Paper pages mirror the concept endpoints. The recompile entry points
# stay /papers/recompile (already defined below); per-paper recompilation
# happens implicitly via the Papers page → "重新处理" flow.

@router.get("/papers")
def list_papers():
    return {"items": list_paper_pages()}


@router.get("/papers/{filename}")
def get_paper_page(filename: str):
    page = read_paper_page(filename)
    if not page:
        raise HTTPException(status_code=404, detail="Paper page not found")
    return page


@router.get("/papers/{filename}/raw")
def get_paper_page_raw(filename: str):
    page = read_paper_page(filename)
    if not page:
        raise HTTPException(status_code=404, detail="Paper page not found")
    return PlainTextResponse(page["raw"], media_type="text/markdown; charset=utf-8")


# --- live status ------------------------------------------------------------

@router.get("/status")
def get_status():
    with _state_lock:
        return dict(compile_state)


@router.get("/freshness")
def get_freshness(db: Session = Depends(get_db)):
    """Tells the UI which wiki pages are out-of-date relative to the raw
    layer (DB). The frontend uses this to surface a "X items need
    recompiling" banner so the user doesn't have to track raw-layer
    changes manually."""
    return compute_freshness_summary(db)


# --- background drivers -----------------------------------------------------

def _drive_concept_recompile():
    from database import SessionLocal
    cfg = load_config()
    api_key = cfg.get("openai_api_key")
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
    db = SessionLocal()
    try:
        # We pre-count to seed the progress bar; the helper will re-query
        # but the count is stable within this background run.
        total = db.query(KnowledgeNode).count()
        with _state_lock:
            compile_state.update({"total": total, "model": model})

        def on_progress(idx, total, node, path, err):
            _set_current(f"概念 [{idx}/{total}] · {node.title}")
            _tick(success=(err is None), err=err)

        compile_all_concept_pages(db, api_key, model, on_progress=on_progress)
    except Exception as e:
        with _state_lock:
            compile_state["last_error"] = f"{type(e).__name__}: {e}"
        import traceback as _tb
        _tb.print_exc()
    finally:
        db.close()
        _finish()


def _drive_paper_recompile():
    from database import SessionLocal
    from models import Paper
    cfg = load_config()
    api_key = cfg.get("openai_api_key")
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
    db = SessionLocal()
    try:
        total = db.query(Paper).filter(Paper.processed.is_(True)).count()
        with _state_lock:
            compile_state.update({"total": total, "model": model})

        def on_progress(idx, total, paper, path, err):
            label = paper.title or paper.filename or f"paper-{paper.id}"
            _set_current(f"论文 [{idx}/{total}] · {label}")
            _tick(success=(err is None), err=err)

        compile_all_paper_pages(db, api_key, model, on_progress=on_progress)
    except Exception as e:
        with _state_lock:
            compile_state["last_error"] = f"{type(e).__name__}: {e}"
        import traceback as _tb
        _tb.print_exc()
    finally:
        db.close()
        _finish()


# --- recompile endpoints ----------------------------------------------------

def _spawn(target) -> None:
    """Run a long-lived compile job on its own daemon thread, OUTSIDE the
    FastAPI/anyio threadpool. The threadpool is shared by every sync HTTP
    handler — if we hand multi-hour tasks to it via BackgroundTasks, the
    handlers for /api/papers, /api/graph, etc. start queueing behind the
    compile and the UI feels frozen on page switches.

    Daemon=True so the thread doesn't block process shutdown when uvicorn
    reloads.
    """
    threading.Thread(target=target, daemon=True).start()


@router.post("/concepts/recompile")
def recompile_all_concepts():
    """Force a full re-compile of every concept page in the background.
    Returns immediately; poll /api/wiki/status for live progress."""
    cfg = load_config()
    if not cfg.get("openai_api_key"):
        raise HTTPException(status_code=400, detail="OpenAI API key not configured")
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
    if not _try_acquire("concepts", total=0, model=model):
        raise HTTPException(status_code=409, detail="Wiki compile already running")
    _spawn(_drive_concept_recompile)
    return {"message": "Concept recompile started"}


@router.post("/papers/recompile")
def recompile_all_paper_pages():
    """One-shot: compile a wiki/papers/{id}.md for every already-processed
    paper. Useful when adopting Phase 1 against an existing corpus —
    `_process_single` only auto-compiles for newly-processed papers."""
    cfg = load_config()
    if not cfg.get("openai_api_key"):
        raise HTTPException(status_code=400, detail="OpenAI API key not configured")
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
    if not _try_acquire("papers", total=0, model=model):
        raise HTTPException(status_code=409, detail="Wiki compile already running")
    _spawn(_drive_paper_recompile)
    return {"message": "Paper page compile started"}


@router.post("/concepts/{concept_id}/recompile")
def recompile_one_concept(concept_id: int, db: Session = Depends(get_db)):
    cfg = load_config()
    if not cfg.get("openai_api_key"):
        raise HTTPException(status_code=400, detail="OpenAI API key not configured")
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == concept_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Concept not found")
    try:
        path = compile_concept_page(
            node, db, cfg["openai_api_key"], cfg["wiki_compile_model"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not path:
        raise HTTPException(
            status_code=400,
            detail="Concept has no processed source papers yet",
        )
    return {"path": str(path), "filename": path.name}
