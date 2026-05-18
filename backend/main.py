from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import load_config
from database import init_db, SessionLocal
from logging_utils import configure_app_logging
from models import Paper
from routers import papers, graph, config, prompt, note_images, wiki, promotion, ask
from services.graph_service import repair_merged_paper_nodes
from services.paper_category_service import sync_paper_category_fields
from services.vlm_service import parse_extraction_response

configure_app_logging()

app = FastAPI(title="Knowra API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(papers.router)
app.include_router(graph.router)
app.include_router(config.router)
app.include_router(prompt.router)
app.include_router(note_images.router)
app.include_router(wiki.router)
app.include_router(promotion.router)
app.include_router(ask.router)


@app.on_event("startup")
def startup():
    init_db()
    db = None
    try:
        db = SessionLocal()
        changed = False
        for paper in db.query(Paper).all():
            extraction = None
            if paper.raw_llm_response:
                try:
                    extraction = parse_extraction_response(paper.raw_llm_response)
                except Exception:
                    extraction = None
            if sync_paper_category_fields(paper, extraction):
                changed = True
        if changed:
            db.commit()
    except Exception as e:
        print(f"[paper_category] startup backfill failed: {e}")
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    db = None
    try:
        db = SessionLocal()
        cfg = load_config()
        repaired = repair_merged_paper_nodes(
            db,
            similarity_threshold=cfg.get("similarity_threshold", 0.6),
        )
        if repaired:
            print(f"[graph_repair] repaired {repaired} merged paper node(s)")
    except Exception as e:
        print(f"[graph_repair] startup repair failed: {e}")
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    # Phase 2A: keep the wiki FTS index warm. Cheap at this scale (<1s for
    # ~500 .md files); failures are non-fatal so a missing wiki/ directory
    # doesn't take the API down.
    try:
        from services.wiki_search import rebuild_index
        rebuild_index()
    except Exception as e:
        print(f"[wiki_search] startup index failed: {e}")


@app.get("/")
def root():
    return {"message": "Knowra API is running"}
