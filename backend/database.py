from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
from models import Base
from path_utils import portable_data_path, resolve_paper_path

DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _migrate():
    """Idempotent ALTER TABLE migrations for SQLite.

    SQLAlchemy's create_all() doesn't add new columns to existing tables, so
    we patch missing columns here. Each block is a no-op after the first run.
    """
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(papers)")).fetchall()
        existing = {row[1] for row in cols}
        if "openai_file_id" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN openai_file_id VARCHAR"))
        if "openai_vector_store_id" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN openai_vector_store_id VARCHAR"))
        if "notes" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN notes TEXT"))
        if "openai_thread_id" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN openai_thread_id VARCHAR"))
        if "thread_created_at" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN thread_created_at DATETIME"))
        if "chat_history" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN chat_history JSON"))
        if "extraction_model" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN extraction_model VARCHAR"))

        node_cols = conn.execute(text("PRAGMA table_info(knowledge_nodes)")).fetchall()
        node_existing = {row[1] for row in node_cols}
        if "node_origin" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN node_origin VARCHAR"))
        if "hidden" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN hidden BOOLEAN"))
        conn.execute(
            text(
                "UPDATE knowledge_nodes "
                "SET node_origin = COALESCE(NULLIF(node_origin, ''), 'auto'), "
                "    hidden = COALESCE(hidden, 0)"
            )
        )

        rows = conn.execute(
            text("SELECT id, filepath, first_page_image_path, error FROM papers")
        ).mappings().all()
        for row in rows:
            updates = {}

            portable_filepath = portable_data_path(row["filepath"])
            if portable_filepath != row["filepath"]:
                conflict = conn.execute(
                    text(
                        "SELECT id FROM papers "
                        "WHERE filepath = :filepath AND id != :id LIMIT 1"
                    ),
                    {"filepath": portable_filepath, "id": row["id"]},
                ).fetchone()
                if not conflict:
                    updates["filepath"] = portable_filepath

            first_page_image_path = row["first_page_image_path"]
            if first_page_image_path:
                portable_image_path = portable_data_path(first_page_image_path)
                if portable_image_path != first_page_image_path:
                    updates["first_page_image_path"] = portable_image_path

            error = row["error"] or ""
            effective_filepath = updates.get("filepath", row["filepath"])
            if (
                error.startswith("PDF not found:")
                and resolve_paper_path(effective_filepath).exists()
            ):
                updates["error"] = None

            if updates:
                assignments = ", ".join(f"{key} = :{key}" for key in updates)
                conn.execute(
                    text(f"UPDATE papers SET {assignments} WHERE id = :id"),
                    {**updates, "id": row["id"]},
                )


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
