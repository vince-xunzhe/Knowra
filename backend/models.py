from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    filepath = Column(String, unique=True, nullable=False)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)

    # PDF-derived metadata
    num_pages = Column(Integer, nullable=True)
    extracted_text = Column(Text, nullable=True)       # full plain text
    first_page_image_path = Column(String, nullable=True)  # rendered PNG path

    # VLM-extracted metadata (for quick list display)
    title = Column(String, nullable=True)
    authors = Column(JSON, default=list)

    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    raw_llm_response = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)  # user-authored markdown notes
    error = Column(Text, nullable=True)
    # OpenAI Files API — cached file_id so we don't re-upload the same PDF
    openai_file_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    node_type = Column(String, default="concept")  # paper/technique/dataset/problem/concept/entity
    tags = Column(JSON, default=list)
    embedding = Column(JSON, nullable=True)
    source_paper_ids = Column(JSON, default=list)  # list of Paper IDs
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, nullable=False)
    target_id = Column(Integer, nullable=False)
    relation_type = Column(String, default="related")
    weight = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
