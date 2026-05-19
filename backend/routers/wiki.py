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
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import load_config, task_model_id
from database import get_db
from models import KnowledgeNode, Paper
from services.wiki_graph_service import build_wiki_graph
from services import wiki_search as wiki_search_service
from services import wiki_index
from services import wiki_lint_service
from services.wiki_compiler import (
    backfill_obsidian_aliases,
    count_publishable_concepts,
    compile_all_concept_pages,
    compile_all_paper_pages,
    compile_concept_page,
    compile_paper_page,
    compute_freshness_summary,
    list_concept_pages,
    list_paper_pages,
    read_concept_page,
    read_paper_page,
    reconcile_concept_pages_dir,
    reconcile_paper_pages_dir,
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
    "current_item_id": None,
    "current_item_kind": None,  # "paper" | "concept" | None
    "failed_items": [],  # recent failed items for single-item retry UX
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


def _set_current(label: str, item_id: Optional[int] = None, item_kind: Optional[str] = None) -> None:
    with _state_lock:
        compile_state["current"] = label
        compile_state["current_item_id"] = item_id
        compile_state["current_item_kind"] = item_kind


def _tick(success: bool, err: Optional[BaseException] = None) -> None:
    with _state_lock:
        compile_state["done"] += 1
        if not success:
            compile_state["errors"] += 1
            if err is not None:
                compile_state["last_error"] = f"{type(err).__name__}: {err}"


def _record_failure(
    item_kind: str,
    item_id: int,
    label: str,
    err: BaseException,
) -> None:
    with _state_lock:
        failures = compile_state.get("failed_items")
        if not isinstance(failures, list):
            failures = []
        failures.append({
            "kind": item_kind,
            "id": item_id,
            "label": label,
            "error": f"{type(err).__name__}: {err}",
            "failed_at": _now_iso(),
        })
        compile_state["failed_items"] = failures[-200:]
        compile_state["last_error"] = failures[-1]["error"]


def _finish() -> None:
    with _state_lock:
        compile_state["running"] = False
        compile_state["current"] = ""
        compile_state["current_item_id"] = None
        compile_state["current_item_kind"] = None
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
            "current_item_id": None,
            "current_item_kind": None,
            "failed_items": [],
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


@router.get("/graph")
def get_wiki_graph(db: Session = Depends(get_db)):
    with _state_lock:
        active_kind = compile_state.get("current_item_kind")
        active_id = compile_state.get("current_item_id")
    return build_wiki_graph(db, active_kind=active_kind, active_id=active_id)


# --- search (Phase 2A) ------------------------------------------------------

@router.get("/search")
def wiki_search(q: str = "", limit: int = 20):
    """Full-text search over the LLM-compiled wiki layer.
    Zero token cost — pure SQLite FTS5 / bm25 ranking on disk."""
    hits = wiki_search_service.search(q, limit=limit) if q.strip() else []
    if hits:
        paper_meta_by_filename = {
            item.get("filename"): item
            for item in list_paper_pages()
            if item.get("filename")
        }
        concept_meta_by_filename = {
            item.get("filename"): item
            for item in list_concept_pages()
            if item.get("filename")
        }
        enriched = []
        for hit in hits:
            item = dict(hit)
            if item.get("kind") == "paper":
                meta = paper_meta_by_filename.get(item.get("filename"))
                item["paper_id"] = meta.get("paper_id") if meta else None
            elif item.get("kind") == "concept":
                meta = concept_meta_by_filename.get(item.get("filename"))
                item["concept_id"] = meta.get("concept_id") if meta else None
            enriched.append(item)
        hits = enriched
    return {"query": q, "hits": hits}


@router.get("/search/stats")
def wiki_search_stats():
    return wiki_search_service.index_stats()


@router.post("/reindex")
def wiki_reindex():
    """Rebuild the FTS index from disk. Auto-runs after each compile, but
    exposed manually for diagnostics."""
    return wiki_search_service.rebuild_index()


@router.post("/backfill_aliases")
def wiki_backfill_aliases():
    """One-shot, no-LLM pass that adds Obsidian-resolvable `aliases`
    frontmatter to every already-compiled wiki .md, so the custom
    `[[paper:N]]` / `[[concept:N]]` markup links resolve to real notes
    in an Obsidian vault. Idempotent — safe to call repeatedly."""
    return backfill_obsidian_aliases()


# --- P1: content lint / health-check ----------------------------------------


class LintRunRequest(BaseModel):
    use_llm: bool = True


@router.post("/lint/run")
def wiki_lint_run(
    body: LintRunRequest = LintRunRequest(),
    db: Session = Depends(get_db),
):
    """Run the content health-check: stub detection, merge candidates,
    missing cross-cutting concepts, and (LLM) follow-up questions.
    Writes data/wiki/lint-report.md and returns the structured payload."""
    try:
        return wiki_lint_service.run_lint(db, use_llm=body.use_llm)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/lint/status")
def wiki_lint_status():
    return wiki_lint_service.lint_report_status()


@router.get("/lint/report")
def wiki_lint_report():
    text = wiki_lint_service.read_lint_report()
    if text is None:
        raise HTTPException(status_code=404, detail="lint-report.md 还没生成")
    return {"text": text, "status": wiki_lint_service.lint_report_status()}


class LintAcceptRequest(BaseModel):
    concept_id: int


@router.post("/lint/accept")
def wiki_lint_accept(body: LintAcceptRequest, db: Session = Depends(get_db)):
    """Mark a thin single-source concept as acceptable-as-is so future
    health-checks skip it (tags the node, no LLM)."""
    result = wiki_lint_service.accept_stub(db, body.concept_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return result


# --- background drivers -----------------------------------------------------

def _drive_concept_recompile():
    from database import SessionLocal
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    model = task_model_id(cfg, "wiki_compile")
    db = SessionLocal()
    try:
        cleanup = reconcile_concept_pages_dir(db, prune_orphans=True)
        if cleanup["removed_count"] > 0:
            print(
                "[wiki] concept reconcile removed "
                f"{cleanup['removed_count']} files "
                f"(duplicates={cleanup['duplicate_removed']}, "
                f"orphans={cleanup['orphan_removed']})"
            )
        # We pre-count to seed the progress bar; the helper will re-query
        # but the count is stable within this background run.
        total = count_publishable_concepts(db)
        with _state_lock:
            compile_state.update({"total": total, "model": model})

        def on_progress(idx, total, node, path, err):
            _set_current(
                f"概念 [{idx}/{total}] · {node.title}",
                item_id=node.id,
                item_kind="concept",
            )
            _tick(success=(err is None), err=err)
            if err is not None:
                _record_failure("concept", node.id, node.title or f"concept-{node.id}", err)

        compile_all_concept_pages(db, api_key, model, on_progress=on_progress)
    except Exception as e:
        with _state_lock:
            compile_state["last_error"] = f"{type(e).__name__}: {e}"
        import traceback as _tb
        _tb.print_exc()
    finally:
        db.close()
        _finish()
        try:
            wiki_index.refresh_index()
        except Exception as ix_err:
            with _state_lock:
                compile_state["last_error"] = f"{type(ix_err).__name__}: {ix_err}"
            print(f"[wiki_index] post-compile refresh failed: {ix_err}")
        try:
            wiki_search_service.rebuild_index()
        except Exception as ix_err:
            print(f"[wiki_search] post-compile reindex failed: {ix_err}")


def _drive_paper_recompile():
    from database import SessionLocal
    from models import Paper
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    model = task_model_id(cfg, "wiki_compile")
    db = SessionLocal()
    try:
        cleanup = reconcile_paper_pages_dir(db, prune_orphans=True)
        if cleanup["removed_count"] > 0:
            print(
                "[wiki] paper reconcile removed "
                f"{cleanup['removed_count']} files "
                f"(duplicates={cleanup['duplicate_removed']}, "
                f"orphans={cleanup['orphan_removed']})"
            )
        total = db.query(Paper).filter(Paper.processed.is_(True)).count()
        with _state_lock:
            compile_state.update({"total": total, "model": model})

        def on_progress(idx, total, paper, path, err):
            label = paper.title or paper.filename or f"paper-{paper.id}"
            _set_current(
                f"论文 [{idx}/{total}] · {label}",
                item_id=paper.id,
                item_kind="paper",
            )
            _tick(success=(err is None), err=err)
            if err is not None:
                _record_failure("paper", paper.id, label, err)

        compile_all_paper_pages(db, api_key, model, on_progress=on_progress)
    except Exception as e:
        with _state_lock:
            compile_state["last_error"] = f"{type(e).__name__}: {e}"
        import traceback as _tb
        _tb.print_exc()
    finally:
        db.close()
        _finish()
        try:
            wiki_index.refresh_index()
        except Exception as ix_err:
            with _state_lock:
                compile_state["last_error"] = f"{type(ix_err).__name__}: {ix_err}"
            print(f"[wiki_index] post-compile refresh failed: {ix_err}")
        try:
            wiki_search_service.rebuild_index()
        except Exception as ix_err:
            print(f"[wiki_search] post-compile reindex failed: {ix_err}")


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
    model = task_model_id(cfg, "wiki_compile")
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
    model = task_model_id(cfg, "wiki_compile")
    if not _try_acquire("papers", total=0, model=model):
        raise HTTPException(status_code=409, detail="Wiki compile already running")
    _spawn(_drive_paper_recompile)
    return {"message": "Paper page compile started"}


@router.post("/papers/{paper_id}/recompile")
def recompile_one_paper(paper_id: int, db: Session = Depends(get_db)):
    """Single-paper wiki recompile — used by the graph drawer's "重编译此页"
    button so the user can refresh one paper without retriggering the
    full extraction pipeline."""
    cfg = load_config()
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper.processed or not paper.raw_llm_response:
        raise HTTPException(
            status_code=400,
            detail="Paper hasn't been processed yet — run extraction first",
        )
    try:
        path = compile_paper_page(
            paper, cfg.get("openai_api_key") or "", task_model_id(cfg, "wiki_compile")
        )
    except Exception as e:
        _record_failure("paper", paper_id, paper.title or paper.filename or f"paper-{paper_id}", e)
        raise HTTPException(status_code=500, detail=str(e))
    if not path:
        raise HTTPException(status_code=400, detail="Nothing to compile")
    warnings: list[str] = []
    try:
        wiki_index.refresh_index()
    except Exception as ix_err:
        warnings.append(f"index_refresh_failed: {ix_err}")
    try:
        wiki_search_service.rebuild_index()
    except Exception as ix_err:
        warnings.append(f"search_reindex_failed: {ix_err}")
    resp = {"path": str(path), "filename": path.name}
    if warnings:
        resp["warnings"] = warnings
    return resp


@router.post("/concepts/{concept_id}/recompile")
def recompile_one_concept(concept_id: int, db: Session = Depends(get_db)):
    cfg = load_config()
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == concept_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Concept not found")
    try:
        path = compile_concept_page(
            node, db, cfg.get("openai_api_key") or "", task_model_id(cfg, "wiki_compile")
        )
    except Exception as e:
        _record_failure("concept", concept_id, node.title or f"concept-{concept_id}", e)
        raise HTTPException(status_code=500, detail=str(e))
    if not path:
        raise HTTPException(
            status_code=400,
            detail="Concept has no processed source papers yet",
        )
    warnings: list[str] = []
    try:
        wiki_index.refresh_index()
    except Exception as ix_err:
        warnings.append(f"index_refresh_failed: {ix_err}")
    try:
        wiki_search_service.rebuild_index()
    except Exception as ix_err:
        warnings.append(f"search_reindex_failed: {ix_err}")
    resp = {"path": str(path), "filename": path.name}
    if warnings:
        resp["warnings"] = warnings
    return resp


class RetryFailedItemInput(BaseModel):
    kind: str
    item_id: int


class RecompileDirtyInput(BaseModel):
    include_missing: bool = True
    include_stale: bool = True
    paper_ids: list[int] = Field(default_factory=list)
    concept_ids: list[int] = Field(default_factory=list)


class RecompileByIdsInput(BaseModel):
    paper_ids: list[int] = Field(default_factory=list)
    concept_ids: list[int] = Field(default_factory=list)


def _normalize_id_list(values: Optional[list[int]]) -> list[int]:
    out: list[int] = []
    for raw in values or []:
        try:
            ident = int(raw)
        except (TypeError, ValueError):
            continue
        if ident > 0:
            out.append(ident)
    return sorted(set(out))


def _dirty_ids_from_freshness(
    freshness: dict,
    include_missing: bool,
    include_stale: bool,
) -> tuple[list[int], list[int]]:
    paper_ids: set[int] = set()
    concept_ids: set[int] = set()

    paper_section = freshness.get("papers") if isinstance(freshness, dict) else {}
    concept_section = freshness.get("concepts") if isinstance(freshness, dict) else {}
    if not isinstance(paper_section, dict):
        paper_section = {}
    if not isinstance(concept_section, dict):
        concept_section = {}

    if include_missing:
        for row in paper_section.get("missing") or []:
            if isinstance(row, dict) and isinstance(row.get("paper_id"), int):
                paper_ids.add(int(row["paper_id"]))
        for row in concept_section.get("missing") or []:
            if isinstance(row, dict) and isinstance(row.get("concept_id"), int):
                concept_ids.add(int(row["concept_id"]))

    if include_stale:
        for row in paper_section.get("stale") or []:
            if isinstance(row, dict) and isinstance(row.get("paper_id"), int):
                paper_ids.add(int(row["paper_id"]))
        for row in concept_section.get("stale") or []:
            if isinstance(row, dict) and isinstance(row.get("concept_id"), int):
                concept_ids.add(int(row["concept_id"]))

    return sorted(paper_ids), sorted(concept_ids)


def _run_incremental_recompile(
    *,
    db: Session,
    paper_ids: list[int],
    concept_ids: list[int],
    include_missing: bool,
    include_stale: bool,
    freshness_before: Optional[dict] = None,
) -> dict:
    cfg = load_config()
    model = task_model_id(cfg, "wiki_compile")
    api_key = cfg.get("openai_api_key") or ""
    total_targets = len(paper_ids) + len(concept_ids)
    if not _try_acquire("dirty", total=total_targets, model=model):
        raise HTTPException(status_code=409, detail="Wiki compile already running")

    if freshness_before is None:
        freshness_before = compute_freshness_summary(db)

    compiled_papers: list[dict] = []
    compiled_concepts: list[dict] = []
    skipped_items: list[dict] = []
    failed_items: list[dict] = []
    warnings: list[str] = []

    try:
        for paper_id in paper_ids:
            paper = db.query(Paper).filter(Paper.id == paper_id).first()
            if not paper:
                skipped_items.append({"kind": "paper", "id": paper_id, "reason": "paper_not_found"})
                _tick(success=True)
                continue
            label = paper.title or paper.filename or f"paper-{paper_id}"
            _set_current(
                f"增量论文 · {label}",
                item_id=paper_id,
                item_kind="paper",
            )
            if not paper.processed or not paper.raw_llm_response:
                skipped_items.append({"kind": "paper", "id": paper_id, "reason": "paper_not_processed"})
                _tick(success=True)
                continue
            try:
                path = compile_paper_page(paper, api_key, model)
            except Exception as e:
                _tick(success=False, err=e)
                _record_failure("paper", paper_id, label, e)
                failed_items.append({
                    "kind": "paper",
                    "id": paper_id,
                    "label": label,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            if path is None:
                skipped_items.append({"kind": "paper", "id": paper_id, "reason": "nothing_to_compile"})
                _tick(success=True)
                continue
            compiled_papers.append({
                "paper_id": paper_id,
                "filename": path.name,
                "path": str(path),
            })
            _tick(success=True)

        for concept_id in concept_ids:
            node = db.query(KnowledgeNode).filter(KnowledgeNode.id == concept_id).first()
            if not node:
                skipped_items.append({"kind": "concept", "id": concept_id, "reason": "concept_not_found"})
                _tick(success=True)
                continue
            label = node.title or f"concept-{concept_id}"
            _set_current(
                f"增量概念 · {label}",
                item_id=concept_id,
                item_kind="concept",
            )
            try:
                path = compile_concept_page(node, db, api_key, model)
            except Exception as e:
                _tick(success=False, err=e)
                _record_failure("concept", concept_id, label, e)
                failed_items.append({
                    "kind": "concept",
                    "id": concept_id,
                    "label": label,
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            if path is None:
                skipped_items.append({"kind": "concept", "id": concept_id, "reason": "concept_not_publishable"})
                _tick(success=True)
                continue
            compiled_concepts.append({
                "concept_id": concept_id,
                "filename": path.name,
                "path": str(path),
            })
            _tick(success=True)

        cleanup: dict = {}
        try:
            cleanup["papers"] = reconcile_paper_pages_dir(db, prune_orphans=True)
        except Exception as e:
            warnings.append(f"paper_reconcile_failed: {type(e).__name__}: {e}")
        try:
            cleanup["concepts"] = reconcile_concept_pages_dir(db, prune_orphans=True)
        except Exception as e:
            warnings.append(f"concept_reconcile_failed: {type(e).__name__}: {e}")

        try:
            wiki_index.refresh_index()
        except Exception as ix_err:
            warnings.append(f"index_refresh_failed: {ix_err}")
        try:
            wiki_search_service.rebuild_index()
        except Exception as ix_err:
            warnings.append(f"search_reindex_failed: {ix_err}")

        freshness_after = compute_freshness_summary(db)
        resp = {
            "requested": {
                "include_missing": include_missing,
                "include_stale": include_stale,
                "paper_ids": paper_ids,
                "concept_ids": concept_ids,
            },
            "compiled": {
                "papers": len(compiled_papers),
                "concepts": len(compiled_concepts),
            },
            "compiled_items": {
                "papers": compiled_papers,
                "concepts": compiled_concepts,
            },
            "failed": {
                "count": len(failed_items),
                "items": failed_items,
            },
            "skipped": {
                "count": len(skipped_items),
                "items": skipped_items,
            },
            "cleanup": cleanup,
            "freshness_before": freshness_before,
            "freshness_after": freshness_after,
        }
        if warnings:
            resp["warnings"] = warnings
        return resp
    finally:
        _finish()


@router.post("/recompile/dirty")
def recompile_dirty_items(
    body: Optional[RecompileDirtyInput] = None,
    db: Session = Depends(get_db),
):
    """Incremental compile using freshness markers instead of full rebuilds.

    Targets come from `freshness.missing/stale` and optional manual IDs.
    Each item is compiled independently so one failure won't block others.
    """
    req = body or RecompileDirtyInput()
    freshness_before = compute_freshness_summary(db)
    dirty_papers, dirty_concepts = _dirty_ids_from_freshness(
        freshness_before,
        include_missing=req.include_missing,
        include_stale=req.include_stale,
    )
    paper_ids = sorted(set(dirty_papers + _normalize_id_list(req.paper_ids)))
    concept_ids = sorted(set(dirty_concepts + _normalize_id_list(req.concept_ids)))
    if not paper_ids and not concept_ids:
        raise HTTPException(
            status_code=400,
            detail="No incremental targets (freshness clean and no manual ids provided)",
        )
    return _run_incremental_recompile(
        db=db,
        paper_ids=paper_ids,
        concept_ids=concept_ids,
        include_missing=req.include_missing,
        include_stale=req.include_stale,
        freshness_before=freshness_before,
    )


@router.post("/recompile/by_ids")
def recompile_by_ids(
    body: RecompileByIdsInput,
    db: Session = Depends(get_db),
):
    """Incremental compile by explicit paper/concept ids only."""
    paper_ids = _normalize_id_list(body.paper_ids)
    concept_ids = _normalize_id_list(body.concept_ids)
    if not paper_ids and not concept_ids:
        raise HTTPException(status_code=400, detail="At least one paper_id or concept_id is required")
    return _run_incremental_recompile(
        db=db,
        paper_ids=paper_ids,
        concept_ids=concept_ids,
        include_missing=False,
        include_stale=False,
    )


@router.post("/retry_failed_item")
def retry_failed_item(body: RetryFailedItemInput, db: Session = Depends(get_db)):
    kind = (body.kind or "").strip().lower()
    if kind == "paper":
        return recompile_one_paper(body.item_id, db)
    if kind == "concept":
        return recompile_one_concept(body.item_id, db)
    raise HTTPException(status_code=400, detail="kind must be 'paper' or 'concept'")
