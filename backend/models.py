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
    # Which OpenAI model produced the most recent raw_llm_response. Useful in
    # the Review page so the user can attribute extraction quality to model
    # vs. prompt when re-reading later.
    extraction_model = Column(String, nullable=True)
    # The paper's lane/category predicted by the extraction model. This is
    # stored separately from raw_llm_response so users can override it
    # without mutating the original model output.
    paper_category_model = Column(String, nullable=True)
    # Optional human override for the paper's lane/category. When present,
    # wiki graph grouping prefers this over the model-predicted value.
    paper_category_override = Column(String, nullable=True)
    raw_llm_response = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)  # user-authored markdown notes
    error = Column(Text, nullable=True)
    # OpenAI Files API — cached file_id so we don't re-upload the same PDF
    openai_file_id = Column(String, nullable=True)
    # Cached vector store for Responses API + file_search.
    openai_vector_store_id = Column(String, nullable=True)
    # OpenAI Assistants API — persisted thread for follow-up Q&A after extraction.
    # Threads expire on OpenAI's side after ~60 days; thread_created_at drives the
    # frontend countdown. chat_history keeps a local copy so the UI can render
    # past turns even after the remote thread is gone.
    openai_thread_id = Column(String, nullable=True)
    thread_created_at = Column(DateTime, nullable=True)
    chat_history = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    node_type = Column(String, default="concept")  # paper/technique/dataset/problem/concept/entity
    node_origin = Column(String, default="auto")   # auto/manual
    hidden = Column(Boolean, default=False)
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
