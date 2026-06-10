"""Phase 1 W3: SQLite INT → UUID + user_id one-shot migration.

This module ships the *logic* of the desktop-side multitenant migration
plus thorough tests. It is **not wired into init_db** — it must be
invoked explicitly via :mod:`backend.scripts.migrate_multitenant` or by
setting the env var ``KNOWRA_RUN_MULTITENANT_MIGRATION=1`` before
startup. That gate exists because the migration is a one-way structural
change that requires the SQLAlchemy models + several routers to be
updated *together* (W3.2). Running it on a database whose models still
declare ``Column(Integer, primary_key=True)`` will not corrupt the
data (SQLite is duck-typed), but it will cause SQLAlchemy to fail when
it tries to coerce UUID strings back to int.

Migration steps (see docs/SCHEMA-MIGRATION.md §7 for the full picture):

  1. Confirm not already migrated (idempotency check against ``_meta``).
  2. Generate UUIDs for every existing row in papers / knowledge_nodes /
     knowledge_edges / llm_calls. Store the (legacy_id → new_uuid)
     pairs in temp remap tables so foreign references can be rewritten.
  3. For each main table:
       a. Rename the existing table to ``<name>_legacy``.
       b. Create the new table with ``id TEXT PRIMARY KEY``, plus
          ``user_id TEXT NULL`` and ``legacy_id INTEGER`` columns.
       c. Copy data row-by-row, substituting UUIDs and (for
          ``knowledge_edges``) rewriting source/target FKs.
       d. Drop the renamed legacy table.
  4. Rewrite ``knowledge_nodes.source_paper_ids`` JSON arrays of INT
     → JSON arrays of UUID via the paper remap.
  5. Record the migration in ``_meta`` so subsequent calls are no-ops.

The whole thing runs inside a single transaction; any error rolls back
the lot and leaves the database exactly as it was.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)

# A marker stored in _meta after successful migration. Bumping this
# constant in a future version would *not* re-run the migration; it's a
# single-shot. Future schema changes go in their own _migrate_*
# functions with their own marker keys.
MULTITENANT_MARKER = "multitenant_v1"

# Default user_id assigned to all existing rows during migration. Real
# value comes from KNOWRA_LOCAL_USER_ID at the call site; this is just
# a constant fallback used by tests.
DEFAULT_LOCAL_USER_ID = "00000000-0000-0000-0000-000000000000"


# ── meta / idempotency ────────────────────────────────────────────────


def _ensure_meta_table(conn: Connection) -> None:
    """Create _meta if missing. Idempotent."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS _meta (
                key   TEXT PRIMARY KEY,
                value TEXT,
                set_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )


def is_multitenant_migrated(conn: Connection) -> bool:
    """Return True iff this DB has already been multitenant-migrated."""
    # _meta might not exist yet on virgin DBs.
    row = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_meta'"
        )
    ).fetchone()
    if not row:
        return False
    found = conn.execute(
        text("SELECT value FROM _meta WHERE key = :k"),
        {"k": MULTITENANT_MARKER},
    ).fetchone()
    return found is not None


def _mark_done(conn: Connection, *, row_counts: dict[str, int]) -> None:
    """Persist the migration marker + row counts for forensics."""
    conn.execute(
        text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES (:k, :v)"
        ),
        {"k": MULTITENANT_MARKER, "v": json.dumps(row_counts)},
    )


# ── helpers ──────────────────────────────────────────────────────────


def _table_exists(conn: Connection, name: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"
            ),
            {"n": name},
        ).fetchone()
    )


def _id_is_text(conn: Connection, table: str) -> bool:
    """Detect whether a table's ``id`` column is already TEXT (post-migration).

    Used as an extra safety net independent of the _meta marker."""
    info = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    for row in info:
        # PRAGMA columns: cid, name, type, notnull, dflt_value, pk
        if row[1] == "id":
            return row[2].upper() in ("TEXT", "VARCHAR")
    return False


def _columns_of(conn: Connection, table: str) -> list[str]:
    info = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [row[1] for row in info]


def _build_remap(
    conn: Connection,
    *,
    src_table: str,
    remap_table: str,
) -> dict[int, str]:
    """Generate UUIDs for every row in src_table; persist (legacy_id, new_id)
    in remap_table; return the same mapping for Python-side use."""
    conn.execute(text(f"DROP TABLE IF EXISTS {remap_table}"))
    conn.execute(
        text(
            f"""
            CREATE TABLE {remap_table} (
                legacy_id INTEGER PRIMARY KEY,
                new_id    TEXT NOT NULL
            )
            """
        )
    )
    legacy_ids = [
        row[0]
        for row in conn.execute(text(f"SELECT id FROM {src_table}")).fetchall()
    ]
    mapping: dict[int, str] = {lid: str(uuid.uuid4()) for lid in legacy_ids}
    if mapping:
        conn.execute(
            text(
                f"INSERT INTO {remap_table} (legacy_id, new_id) VALUES (:lid, :nid)"
            ),
            [{"lid": k, "nid": v} for k, v in mapping.items()],
        )
    return mapping


def _create_papers_v2(conn: Connection) -> None:
    """Create the post-migration papers table.

    Note: we intentionally preserve all existing columns (including
    ``extracted_text`` and ``chat_history``) at the local SQLite layer.
    The cloud-side schema is the one that drops them; the desktop keeps
    them because they support local-only features (per-paper chat,
    fulltext fallback)."""
    conn.execute(
        text(
            """
            CREATE TABLE papers (
                id                       TEXT PRIMARY KEY,
                user_id                  TEXT,
                legacy_id                INTEGER,

                filepath                 TEXT NOT NULL,
                filename                 TEXT NOT NULL,
                file_hash                TEXT NOT NULL,
                num_pages                INTEGER,
                extracted_text           TEXT,
                first_page_image_path    TEXT,
                title                    TEXT,
                authors                  TEXT,
                processed                BOOLEAN DEFAULT 0,
                processed_at             DATETIME,
                extraction_model         TEXT,
                paper_category_model     TEXT,
                paper_category_override  TEXT,
                raw_llm_response         TEXT,
                notes                    TEXT,
                error                    TEXT,
                processing_status        TEXT DEFAULT 'scanning',
                retry_count              INTEGER DEFAULT 0,
                last_error_stage         TEXT,
                last_error_reason        TEXT,
                last_error_recoverable   BOOLEAN,
                openai_file_id           TEXT,
                openai_vector_store_id   TEXT,
                openai_thread_id         TEXT,
                thread_created_at        DATETIME,
                chat_history             TEXT,
                created_at               DATETIME
            )
            """
        )
    )


def _create_knowledge_nodes_v2(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE knowledge_nodes (
                id                      TEXT PRIMARY KEY,
                user_id                 TEXT,
                legacy_id               INTEGER,

                title                   TEXT NOT NULL,
                content                 TEXT NOT NULL,
                node_type               TEXT DEFAULT 'concept',
                node_origin             TEXT DEFAULT 'auto',
                hidden                  BOOLEAN DEFAULT 0,
                promotion_status        TEXT DEFAULT 'pending',
                promoted_by             TEXT,
                promotion_reason        TEXT,
                last_promotion_eval_at  DATETIME,
                tags                    TEXT,
                embedding               TEXT,
                source_paper_ids        TEXT,
                created_at              DATETIME
            )
            """
        )
    )


def _create_knowledge_edges_v2(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE knowledge_edges (
                id              TEXT PRIMARY KEY,
                user_id         TEXT,
                legacy_id       INTEGER,

                source_id       TEXT NOT NULL,
                target_id       TEXT NOT NULL,
                relation_type   TEXT DEFAULT 'related',
                weight          REAL DEFAULT 0.0,
                created_at      DATETIME
            )
            """
        )
    )


def _create_llm_calls_v2(conn: Connection) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE llm_calls (
                id                TEXT PRIMARY KEY,
                user_id           TEXT,
                legacy_id         INTEGER,

                called_at         DATETIME,
                task              TEXT NOT NULL,
                provider          TEXT NOT NULL,
                model             TEXT NOT NULL,
                surface           TEXT,
                prompt_tokens     INTEGER,
                completion_tokens INTEGER,
                total_tokens      INTEGER,
                latency_ms        INTEGER,
                success           BOOLEAN DEFAULT 1,
                error_class       TEXT
            )
            """
        )
    )


def _rewrite_paper_ids_in_json(raw: Any, papers_remap: dict[int, str]) -> str:
    """Translate one ``source_paper_ids`` value (JSON array of int) into
    a JSON array of UUID strings via ``papers_remap``."""
    if raw is None or raw == "":
        return "[]"
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return "[]"
    if not isinstance(decoded, list):
        return "[]"
    out: list[str] = []
    for old in decoded:
        try:
            old_int = int(old)
        except (TypeError, ValueError):
            continue
        if old_int in papers_remap:
            out.append(papers_remap[old_int])
        # else: orphan reference; silently dropped
    return json.dumps(out)


def _migrate_papers(
    conn: Connection,
    *,
    user_id: str,
    papers_remap: dict[int, str],
) -> int:
    """Rebuild papers with TEXT id, user_id, legacy_id columns."""
    conn.execute(text("ALTER TABLE papers RENAME TO papers_legacy"))
    _create_papers_v2(conn)
    legacy_cols = _columns_of(conn, "papers_legacy")
    rows = conn.execute(
        text(f"SELECT {', '.join(legacy_cols)} FROM papers_legacy")
    ).mappings().all()
    insert_cols = ["id", "user_id", "legacy_id"] + [
        c for c in legacy_cols if c != "id"
    ]
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    insert_sql = text(
        f"INSERT INTO papers ({', '.join(insert_cols)}) VALUES ({placeholders})"
    )
    payload = []
    for r in rows:
        new_id = papers_remap[int(r["id"])]
        rec = {**dict(r), "id": new_id, "user_id": user_id, "legacy_id": int(r["id"])}
        payload.append(rec)
    if payload:
        conn.execute(insert_sql, payload)
    conn.execute(text("DROP TABLE papers_legacy"))
    return len(payload)


def _migrate_knowledge_nodes(
    conn: Connection,
    *,
    user_id: str,
    nodes_remap: dict[int, str],
    papers_remap: dict[int, str],
) -> int:
    conn.execute(text("ALTER TABLE knowledge_nodes RENAME TO knowledge_nodes_legacy"))
    _create_knowledge_nodes_v2(conn)
    legacy_cols = _columns_of(conn, "knowledge_nodes_legacy")
    rows = conn.execute(
        text(f"SELECT {', '.join(legacy_cols)} FROM knowledge_nodes_legacy")
    ).mappings().all()
    insert_cols = ["id", "user_id", "legacy_id"] + [
        c for c in legacy_cols if c != "id"
    ]
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    insert_sql = text(
        f"INSERT INTO knowledge_nodes ({', '.join(insert_cols)}) VALUES ({placeholders})"
    )
    payload = []
    for r in rows:
        legacy_id = int(r["id"])
        new_id = nodes_remap[legacy_id]
        rec = {
            **dict(r),
            "id": new_id,
            "user_id": user_id,
            "legacy_id": legacy_id,
        }
        # Rewrite the source_paper_ids JSON array of INT → UUID strings.
        rec["source_paper_ids"] = _rewrite_paper_ids_in_json(
            r.get("source_paper_ids"), papers_remap
        )
        payload.append(rec)
    if payload:
        conn.execute(insert_sql, payload)
    conn.execute(text("DROP TABLE knowledge_nodes_legacy"))
    return len(payload)


def _migrate_knowledge_edges(
    conn: Connection,
    *,
    user_id: str,
    edges_remap: dict[int, str],
    nodes_remap: dict[int, str],
) -> int:
    conn.execute(text("ALTER TABLE knowledge_edges RENAME TO knowledge_edges_legacy"))
    _create_knowledge_edges_v2(conn)
    legacy_cols = _columns_of(conn, "knowledge_edges_legacy")
    rows = conn.execute(
        text(f"SELECT {', '.join(legacy_cols)} FROM knowledge_edges_legacy")
    ).mappings().all()
    insert_cols = ["id", "user_id", "legacy_id"] + [
        c for c in legacy_cols if c != "id"
    ]
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    insert_sql = text(
        f"INSERT INTO knowledge_edges ({', '.join(insert_cols)}) VALUES ({placeholders})"
    )
    payload = []
    skipped = 0
    for r in rows:
        legacy_id = int(r["id"])
        src_legacy = int(r["source_id"]) if r["source_id"] is not None else None
        tgt_legacy = int(r["target_id"]) if r["target_id"] is not None else None
        if src_legacy not in nodes_remap or tgt_legacy not in nodes_remap:
            # Orphan edge — node was deleted but edge wasn't cleaned up.
            # Drop it rather than carrying a broken reference forward.
            skipped += 1
            continue
        rec = {
            **dict(r),
            "id": edges_remap[legacy_id],
            "user_id": user_id,
            "legacy_id": legacy_id,
            "source_id": nodes_remap[src_legacy],
            "target_id": nodes_remap[tgt_legacy],
        }
        payload.append(rec)
    if payload:
        conn.execute(insert_sql, payload)
    conn.execute(text("DROP TABLE knowledge_edges_legacy"))
    if skipped:
        log.warning("multitenant migration: dropped %d orphan edges", skipped)
    return len(payload)


def _migrate_llm_calls(
    conn: Connection,
    *,
    user_id: str,
    calls_remap: dict[int, str],
) -> int:
    if not _table_exists(conn, "llm_calls"):
        return 0
    conn.execute(text("ALTER TABLE llm_calls RENAME TO llm_calls_legacy"))
    _create_llm_calls_v2(conn)
    legacy_cols = _columns_of(conn, "llm_calls_legacy")
    rows = conn.execute(
        text(f"SELECT {', '.join(legacy_cols)} FROM llm_calls_legacy")
    ).mappings().all()
    insert_cols = ["id", "user_id", "legacy_id"] + [
        c for c in legacy_cols if c != "id"
    ]
    placeholders = ", ".join(f":{c}" for c in insert_cols)
    insert_sql = text(
        f"INSERT INTO llm_calls ({', '.join(insert_cols)}) VALUES ({placeholders})"
    )
    payload = []
    for r in rows:
        legacy_id = int(r["id"])
        rec = {
            **dict(r),
            "id": calls_remap[legacy_id],
            "user_id": user_id,
            "legacy_id": legacy_id,
        }
        payload.append(rec)
    if payload:
        conn.execute(insert_sql, payload)
    conn.execute(text("DROP TABLE llm_calls_legacy"))
    return len(payload)


# ── orchestrator ──────────────────────────────────────────────────────


def migrate_to_multitenant(
    conn: Connection,
    *,
    user_id: str = DEFAULT_LOCAL_USER_ID,
) -> dict[str, int]:
    """One-shot SQLite migration. Idempotent.

    Returns a dict of {table_name: row_count_migrated}; empty dict if
    already migrated (idempotency short-circuit).

    Must run inside a transaction owned by the caller. Use
    ``with engine.begin() as conn: migrate_to_multitenant(conn, ...)``.
    """
    _ensure_meta_table(conn)
    if is_multitenant_migrated(conn):
        log.info("multitenant migration already applied; skipping")
        return {}

    # Safety net: if the id column is already TEXT for any table, we
    # bail out rather than risk corruption. This catches the case where
    # _meta was somehow lost but the data was already migrated.
    for table in ("papers", "knowledge_nodes", "knowledge_edges"):
        if _table_exists(conn, table) and _id_is_text(conn, table):
            log.warning(
                "multitenant migration aborted: %s.id is already TEXT but "
                "_meta marker is missing — refusing to corrupt data",
                table,
            )
            return {}

    log.info("starting multitenant migration (user_id=%s)", user_id)

    # Step 1: pre-compute all remaps so FK rewrites can refer to them
    # before the source tables are renamed.
    papers_remap = (
        _build_remap(conn, src_table="papers", remap_table="_papers_remap")
        if _table_exists(conn, "papers") else {}
    )
    nodes_remap = (
        _build_remap(conn, src_table="knowledge_nodes", remap_table="_nodes_remap")
        if _table_exists(conn, "knowledge_nodes") else {}
    )
    edges_remap = (
        _build_remap(conn, src_table="knowledge_edges", remap_table="_edges_remap")
        if _table_exists(conn, "knowledge_edges") else {}
    )
    calls_remap = (
        _build_remap(conn, src_table="llm_calls", remap_table="_llm_calls_remap")
        if _table_exists(conn, "llm_calls") else {}
    )

    counts: dict[str, int] = {}

    if papers_remap:
        counts["papers"] = _migrate_papers(
            conn, user_id=user_id, papers_remap=papers_remap
        )
    if nodes_remap:
        counts["knowledge_nodes"] = _migrate_knowledge_nodes(
            conn,
            user_id=user_id,
            nodes_remap=nodes_remap,
            papers_remap=papers_remap,
        )
    if edges_remap:
        counts["knowledge_edges"] = _migrate_knowledge_edges(
            conn,
            user_id=user_id,
            edges_remap=edges_remap,
            nodes_remap=nodes_remap,
        )
    if calls_remap:
        counts["llm_calls"] = _migrate_llm_calls(
            conn, user_id=user_id, calls_remap=calls_remap
        )

    _mark_done(conn, row_counts=counts)
    log.info("multitenant migration done: %s", counts)
    return counts


def verify_post_migration(conn: Connection) -> dict[str, Any]:
    """Spot-check the migrated database. Used by the CLI after running.

    Returns a dict of diagnostics:
      - id_types: {table: "TEXT" | "INTEGER" | "MISSING"}
      - row_counts: {table: int}
      - all_user_id_set: {table: bool}
      - orphan_node_refs: int (knowledge_nodes.source_paper_ids pointing
                                to non-existent paper)
      - orphan_edges: int (edges whose source/target node doesn't exist)
    """
    out: dict[str, Any] = {
        "id_types": {},
        "row_counts": {},
        "all_user_id_set": {},
    }
    for t in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
        if not _table_exists(conn, t):
            out["id_types"][t] = "MISSING"
            out["row_counts"][t] = 0
            out["all_user_id_set"][t] = True
            continue
        info = conn.execute(text(f"PRAGMA table_info({t})")).fetchall()
        id_type = next((r[2] for r in info if r[1] == "id"), "?")
        out["id_types"][t] = id_type
        cnt = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        out["row_counts"][t] = int(cnt)
        nulls = conn.execute(
            text(f"SELECT COUNT(*) FROM {t} WHERE user_id IS NULL")
        ).scalar()
        out["all_user_id_set"][t] = int(nulls) == 0

    # FK integrity: knowledge_edges.source_id / target_id should exist
    # in knowledge_nodes.
    if _table_exists(conn, "knowledge_edges") and _table_exists(conn, "knowledge_nodes"):
        orphans = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM knowledge_edges e
                WHERE NOT EXISTS (SELECT 1 FROM knowledge_nodes n WHERE n.id = e.source_id)
                   OR NOT EXISTS (SELECT 1 FROM knowledge_nodes n WHERE n.id = e.target_id)
                """
            )
        ).scalar()
        out["orphan_edges"] = int(orphans or 0)

    # source_paper_ids integrity (Python-side: parse JSON, look up each).
    if _table_exists(conn, "knowledge_nodes") and _table_exists(conn, "papers"):
        paper_ids = {
            r[0]
            for r in conn.execute(text("SELECT id FROM papers")).fetchall()
        }
        orphan_refs = 0
        for r in conn.execute(
            text("SELECT source_paper_ids FROM knowledge_nodes")
        ).fetchall():
            raw = r[0]
            if not raw:
                continue
            try:
                arr = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(arr, list):
                continue
            for pid in arr:
                if pid not in paper_ids:
                    orphan_refs += 1
        out["orphan_node_refs"] = orphan_refs

    return out
