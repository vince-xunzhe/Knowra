from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
from models import Base

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
        if "notes" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN notes TEXT"))


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
