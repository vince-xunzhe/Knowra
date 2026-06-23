"""Cloud-mode Postgres engine + session factory.

W5 left ``routers.sync.get_cloud_db`` as a tests-only stub (raises
RuntimeError unless overridden). That was fine while we were proving
the router logic in unit tests, but the actual cloud deploy needs a
real engine wired in at boot. This module does that wiring.

Boot flow on Fly.io (KNOWRA_DEPLOY_MODE=cloud):

    main.py
      → cloud_db.init_cloud_engine()       (creates Postgres engine)
      → cloud_db.ensure_cloud_schema()     (idempotent CREATE TABLE)
      → app.dependency_overrides[sync_router.get_cloud_db]
            = cloud_db.get_cloud_db        (wire it for both routers)

Connection URL precedence:
    1. CLOUD_DATABASE_URL          — explicit, full SQLAlchemy URL
    2. KNOWRA_TEST_POSTGRES_URL    — reused when running smoke tests
    3. SUPABASE_DB_URL             — Supabase's "URI" connection string
    4. KNOWRA_CLOUD_SQLITE         — local file-backed fallback for
                                     dev / smoke (the protocol is the
                                     same whether the dialect is
                                     Postgres or SQLite)

If none of those is set we use an in-memory SQLite — useful for the
e2e smoke harness, and a safe default (every restart resets state).
"""
from __future__ import annotations

import os
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cloud_models import init_cloud_schema


_UUID_CASTERS_REGISTERED = False


def _register_uuid_as_text() -> None:
    """Make psycopg2 return Postgres UUID / UUID[] columns as plain
    strings instead of ``uuid.UUID`` instances.

    Why this matters: our ORM models declare ``id = Column(String, …)``
    because the rest of the codebase (Pydantic schemas, dict keys,
    string comparisons, JWT ``sub`` claims) treats IDs as strings.
    Postgres's native UUID type, by default, comes back as
    ``uuid.UUID`` instances regardless of the SQLAlchemy column type.
    That single mismatch caused a string of bugs across sync.py,
    cloud.py, snapshot serialization, bulk upsert dict lookups, etc.
    Each was patched locally with ``str(...)`` casts, but the bug
    kept reappearing at every new boundary.

    Registering a cast at psycopg2's TYPECASTERS table fixes the
    bug class for the whole process: psycopg2 emits strings on
    read, every consumer downstream sees strings, ORM equality
    matches, dict keys match, Pydantic ``str`` schemas accept the
    value without coercion.

    This is a no-op on SQLite (used by tests + smoke harness).
    """
    global _UUID_CASTERS_REGISTERED
    if _UUID_CASTERS_REGISTERED:
        return
    try:
        import psycopg2.extensions  # type: ignore
    except ImportError:
        # No psycopg2 → we're in SQLite-only mode (tests/local). Nothing
        # to do; SQLite stores UUIDs as TEXT and returns str directly.
        _UUID_CASTERS_REGISTERED = True
        return

    # OIDs per https://github.com/postgres/postgres/blob/master/src/include/catalog/pg_type.dat
    UUID_OID = 2950
    UUID_ARRAY_OID = 2951

    def _cast_uuid(value, _cur):
        return value if value is None else str(value)

    uuid_as_text = psycopg2.extensions.new_type(
        (UUID_OID,), "UUID_AS_TEXT", _cast_uuid,
    )
    psycopg2.extensions.register_type(uuid_as_text)
    # new_array_type's 3rd arg is the BASE TYPE object (not the caster
    # function) — it derives the array caster from it.
    uuid_array_as_text = psycopg2.extensions.new_array_type(
        (UUID_ARRAY_OID,), "UUID_ARRAY_AS_TEXT", uuid_as_text,
    )
    psycopg2.extensions.register_type(uuid_array_as_text)
    _UUID_CASTERS_REGISTERED = True


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _resolve_url() -> str:
    for name in ("CLOUD_DATABASE_URL", "KNOWRA_TEST_POSTGRES_URL", "SUPABASE_DB_URL"):
        url = os.environ.get(name)
        if url:
            return url
    sqlite_path = os.environ.get("KNOWRA_CLOUD_SQLITE")
    if sqlite_path:
        return f"sqlite:///{sqlite_path}"
    # In-memory SQLite. With StaticPool the one shared connection
    # persists across sessions so the request thread sees the data the
    # commit thread just wrote — matches the test_sync_router setup.
    return "sqlite:///:memory:"


def init_cloud_engine() -> Engine:
    """Create the cloud engine + session factory if they don't exist
    yet. Idempotent — safe to call multiple times (e.g. tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    url = _resolve_url()
    connect_args: dict = {}
    engine_kwargs: dict = {}
    if url.startswith("sqlite:///"):
        connect_args["check_same_thread"] = False
        # In-memory needs StaticPool to share state across connections.
        if url == "sqlite:///:memory:":
            engine_kwargs["poolclass"] = StaticPool
    else:
        # Postgres path: make UUID columns return as strings (see
        # _register_uuid_as_text docstring for the bug class this kills).
        _register_uuid_as_text()
    _engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def ensure_cloud_schema() -> None:
    """Idempotent ``CREATE TABLE IF NOT EXISTS`` for the cloud rows.
    In real Supabase, the SQL migrations under ``supabase/migrations/``
    are the source of truth (they include RLS + triggers we can't
    declare in SQLAlchemy). This call is for SQLite fallbacks where
    those migrations don't apply — it lets the smoke harness boot
    without an external Postgres."""
    engine = init_cloud_engine()
    init_cloud_schema(engine)


def get_cloud_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a cloud DB session."""
    if _SessionLocal is None:
        init_cloud_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def cloud_session() -> Session:
    """A standalone cloud DB session for background workers (e.g. the
    recommendation scheduler) that run outside a FastAPI request. The caller
    is responsible for closing it."""
    if _SessionLocal is None:
        init_cloud_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def reset_for_tests() -> None:
    """Drop the cached engine + factory so the next ``init_cloud_engine``
    re-reads env vars. Tests only."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
