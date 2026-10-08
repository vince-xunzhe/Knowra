import json

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
from models import Base
from path_utils import portable_data_path, resolve_paper_path

# Mirrors graph_service.AUTO_CONCEPT_NODE_TYPES — duplicated here to keep the
# migration self-contained and avoid an import cycle on cold startup.
_LEGACY_AUTO_CONCEPT_TYPES = {"technique", "dataset", "problem_area", "concept"}

_EDGE_PROVENANCE_COLUMNS = {
    "origin": "VARCHAR",
    "confidence": "FLOAT",
    "source_paper_id": "VARCHAR",
    "source_field": "VARCHAR",
    "evidence": "TEXT",
    "metadata": "JSON",
    "extractor_version": "VARCHAR",
}


def migrate_edge_provenance_columns(conn) -> None:
    """Install and backfill Phase-1 edge provenance columns.

    This helper intentionally accepts a SQLAlchemy connection so tests and
    repair tooling can run it against a copied database.  It is safe to run
    repeatedly.  Historical semantic edges are labelled ``legacy`` rather
    than receiving invented evidence; the only origins that can be inferred
    reliably from the old schema are similarity and curated/manual links.
    """
    rows = conn.execute(text("PRAGMA table_info(knowledge_edges)")).fetchall()
    existing = {row[1] for row in rows}
    if not existing:
        return
    for name, sql_type in _EDGE_PROVENANCE_COLUMNS.items():
        if name not in existing:
            conn.execute(
                text(f"ALTER TABLE knowledge_edges ADD COLUMN {name} {sql_type}")
            )

    conn.execute(
        text(
            "UPDATE knowledge_edges SET origin = CASE "
            "  WHEN relation_type = 'similar' THEN 'embedding' "
            "  WHEN relation_type = 'curated_link' THEN 'manual' "
            "  ELSE 'legacy' END "
            "WHERE origin IS NULL OR TRIM(origin) = ''"
        )
    )
    conn.execute(
        text(
            "UPDATE knowledge_edges SET confidence = weight "
            "WHERE relation_type = 'similar' AND confidence IS NULL "
            "  AND weight >= 0.0 AND weight <= 1.0"
        )
    )


def _drop_finding_nodes(conn) -> None:
    """Remove all `finding` knowledge nodes + edges referencing them.

    These were one-per-paper bullets carved out of `key_findings[]`; they
    bloated the graph without giving cross-paper signal. Idempotent — does
    nothing when the table is already finding-free."""
    finding_ids = [
        row[0]
        for row in conn.execute(
            text("SELECT id FROM knowledge_nodes WHERE node_type = 'finding'")
        ).fetchall()
    ]
    if not finding_ids:
        return
    # SQLite has no IN-clause length cap, but be conservative.
    placeholders = ",".join(str(int(i)) for i in finding_ids)
    conn.execute(
        text(
            f"DELETE FROM knowledge_edges "
            f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})"
        )
    )
    conn.execute(
        text(f"DELETE FROM knowledge_nodes WHERE id IN ({placeholders})")
    )


def _backfill_promotion_status(conn) -> None:
    """One-shot backfill that mirrors the old `is_publishable_concept_node`
    rule into the new `promotion_status` column. Run once when the column is
    first added; later evaluations go through the promotion service."""
    rows = conn.execute(
        text(
            "SELECT id, node_type, node_origin, hidden, source_paper_ids "
            "FROM knowledge_nodes"
        )
    ).mappings().all()
    for row in rows:
        node_type = (row["node_type"] or "").strip()
        origin = (row["node_origin"] or "auto").strip().lower()
        hidden = bool(row["hidden"])
        try:
            ids = json.loads(row["source_paper_ids"]) if row["source_paper_ids"] else []
        except (TypeError, ValueError):
            ids = []
        unique_ids = {int(x) for x in ids if isinstance(x, (int, float, str)) and str(x).strip().lstrip("-").isdigit()}

        if hidden:
            status = "rejected"
        elif origin == "manual":
            status = "promoted" if node_type == "concept" else "pending"
        elif node_type in _LEGACY_AUTO_CONCEPT_TYPES and len(unique_ids) >= 2:
            status = "promoted"
        elif node_type == "paper":
            # Paper nodes don't participate in concept promotion; mark them
            # `promoted` so filters that respect the column still surface
            # papers, and any code path asking "is this a concept
            # candidate?" must additionally check node_type.
            status = "promoted"
        else:
            status = "pending"

        conn.execute(
            text(
                "UPDATE knowledge_nodes "
                "SET promotion_status = :status, "
                "    promoted_by = CASE WHEN :status IN ('promoted','rejected') THEN 'legacy' ELSE NULL END "
                "WHERE id = :id"
            ),
            {"status": status, "id": row["id"]},
        )

DB_PATH = Path(__file__).parent.parent / "data" / "knowledge.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def enable_sqlite_wal(bind) -> None:
    # Run once at startup, outside a write transaction. Readers can keep
    # browsing while a writer commits; SQLite still serializes writers.
    with bind.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode=WAL").scalar()
        if str(mode).lower() != "wal":
            raise RuntimeError("Could not enable WAL for the local database")


def _create_unique_index_if_clean(
    conn,
    index_name: str,
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    """Install a uniqueness guard once legacy duplicates have been repaired.

    Older local DBs may already contain duplicate rows. Failing startup would
    block the repair tool, so we skip index creation until the table is clean.
    """
    cols = ", ".join(columns)
    duplicate = conn.execute(
        text(
            f"SELECT {cols}, COUNT(*) AS c FROM {table_name} "
            f"GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).fetchone()
    if duplicate is not None:
        return
    conn.execute(
        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({cols})")
    )


def _migrate(*, additive_only: bool = False):
    """Idempotent ALTER TABLE migrations for SQLite.

    SQLAlchemy's create_all() doesn't add new columns to existing tables, so
    we patch missing columns here. Each block is a no-op after the first run.
    """
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(papers)")).fetchall()
        existing = {row[1] for row in cols}
        # W3.2 multitenant prep: add user_id / legacy_id as nullable so
        # existing pre-multitenant DBs keep working without running the
        # full migrator. The standalone scripts/migrate_multitenant.py
        # is still required if you actually want to push to the cloud
        # (it rewrites ids and backfills user_id), but the desktop app
        # boots fine without it.
        if "user_id" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN user_id VARCHAR"))
        if "legacy_id" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN legacy_id INTEGER"))
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
        if "paper_category_model" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN paper_category_model VARCHAR"))
        if "paper_category_override" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN paper_category_override VARCHAR"))
        if "paper_team_model" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN paper_team_model VARCHAR"))
        if "paper_team_override" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN paper_team_override VARCHAR"))
        if "learning_status" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN learning_status VARCHAR"))
        if "processing_status" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN processing_status VARCHAR"))
        if "retry_count" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN retry_count INTEGER"))
        if "last_error_stage" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN last_error_stage VARCHAR"))
        if "last_error_reason" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN last_error_reason TEXT"))
        if "last_error_recoverable" not in existing:
            conn.execute(text("ALTER TABLE papers ADD COLUMN last_error_recoverable BOOLEAN"))

        conn.execute(
            text(
                "UPDATE papers "
                "SET processing_status = COALESCE(NULLIF(processing_status, ''), "
                "  CASE "
                "    WHEN processed = 1 THEN 'done' "
                "    WHEN COALESCE(error, '') != '' THEN 'failed' "
                "    ELSE 'scanning' "
                "  END)"
            )
        )
        conn.execute(
            text(
                "UPDATE papers "
                "SET learning_status = 'not_started' "
                "WHERE COALESCE(learning_status, '') NOT IN "
                "('not_started', 'learning', 'completed')"
            )
        )
        conn.execute(
            text(
                "UPDATE papers SET retry_count = COALESCE(retry_count, 0)"
            )
        )
        conn.execute(
            text(
                "UPDATE papers "
                "SET last_error_reason = COALESCE(NULLIF(last_error_reason, ''), error) "
                "WHERE COALESCE(error, '') != ''"
            )
        )
        conn.execute(
            text(
                "UPDATE papers "
                "SET last_error_stage = COALESCE(NULLIF(last_error_stage, ''), 'extracting') "
                "WHERE processing_status = 'failed' AND COALESCE(error, '') != ''"
            )
        )
        _create_unique_index_if_clean(
            conn, "papers_filepath_uniq", "papers", ("filepath",)
        )
        _create_unique_index_if_clean(
            conn, "papers_file_hash_uniq", "papers", ("file_hash",)
        )

        node_cols = conn.execute(text("PRAGMA table_info(knowledge_nodes)")).fetchall()
        node_existing = {row[1] for row in node_cols}
        # W3.2 multitenant prep — same rationale as on papers above.
        if "user_id" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN user_id VARCHAR"))
        if "legacy_id" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN legacy_id INTEGER"))
        if "node_origin" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN node_origin VARCHAR"))
        if "hidden" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN hidden BOOLEAN"))
        # Concept-first promotion lifecycle (see ARCHITECTURE).
        promotion_columns_added = False
        if "promotion_status" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN promotion_status VARCHAR"))
            promotion_columns_added = True
        if "promoted_by" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN promoted_by VARCHAR"))
        if "promotion_reason" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN promotion_reason TEXT"))
        if "last_promotion_eval_at" not in node_existing:
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN last_promotion_eval_at DATETIME"))
        conn.execute(
            text(
                "UPDATE knowledge_nodes "
                "SET node_origin = COALESCE(NULLIF(node_origin, ''), 'auto'), "
                "    hidden = COALESCE(hidden, 0)"
            )
        )
        # Drop finding nodes only during the idle maintenance startup.  The
        # compatibility preflight can run while a surviving worker is active
        # and therefore performs additive schema work only.
        if not additive_only:
            _drop_finding_nodes(conn)
        if promotion_columns_added:
            # Backfill: anything that satisfied the legacy publishable rule
            # (auto concept-eligible types with >= 2 source papers, OR a
            # manual concept) becomes `promoted` so existing wiki pages stay
            # valid; everything else parks at `pending` for the next run.
            _backfill_promotion_status(conn)
        else:
            conn.execute(
                text(
                    "UPDATE knowledge_nodes "
                    "SET promotion_status = COALESCE(NULLIF(promotion_status, ''), 'pending')"
                )
            )

        # W3.2 multitenant prep for the remaining tables.
        for tbl in ("knowledge_edges", "llm_calls"):
            try:
                tbl_cols = conn.execute(text(f"PRAGMA table_info({tbl})")).fetchall()
            except Exception:
                continue
            tbl_existing = {row[1] for row in tbl_cols}
            if not tbl_existing:
                # Table doesn't exist yet (e.g. fresh DB) — create_all()
                # will have already provisioned the right shape, skip.
                continue
            if "user_id" not in tbl_existing:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN user_id VARCHAR"))
            if "legacy_id" not in tbl_existing:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN legacy_id INTEGER"))

        # Phase 1: nullable provenance columns plus conservative, idempotent
        # backfill.  Duplicate consolidation is handled by graph_service
        # after ORM startup so JSON contribution histories can be merged.
        migrate_edge_provenance_columns(conn)

        if additive_only:
            return

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


def schema_preflight_report(bind=engine) -> dict:
    """Verify the ORM columns needed by the paper/graph write path.

    This deliberately checks names rather than trusting ``create_all``:
    SQLAlchemy does not add columns to existing SQLite tables.  A successful
    report is therefore authoritative evidence that a worker can start
    without encountering a late ``no such column`` failure.
    """

    from models import KnowledgeEdge, KnowledgeNode, LLMCall, Paper

    required = {
        model.__tablename__: {column.name for column in model.__table__.columns}
        for model in (Paper, KnowledgeNode, KnowledgeEdge, LLMCall)
    }
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    missing_tables = sorted(set(required) - existing_tables)
    missing_columns: dict[str, list[str]] = {}
    for table, columns in required.items():
        if table not in existing_tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(columns - existing)
        if missing:
            missing_columns[table] = missing
    return {
        "ok": not missing_tables and not missing_columns,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "checked_tables": sorted(required),
    }


def assert_processing_schema(bind=engine) -> dict:
    report = schema_preflight_report(bind)
    if not report["ok"]:
        details = json.dumps(report, ensure_ascii=False, sort_keys=True)
        raise RuntimeError(
            "Database migration preflight failed; processing is disabled until "
            f"the schema is repaired: {details}"
        )
    return report


def ensure_runtime_schema():
    """Install schema changes required by the currently running ORM.

    Keep this preflight deliberately limited to additive, idempotent DDL and
    conservative backfills.  It is safe to run while a surviving task worker
    is idle, and must happen before the API's active-worker startup shortcut:
    otherwise freshly imported ORM models can query columns that an older
    local database does not have yet.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    enable_sqlite_wal(engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        migrate_edge_provenance_columns(conn)
    # Install every currently-known additive column before validating.  The
    # destructive legacy cleanup remains reserved for the idle init_db path.
    _migrate(additive_only=True)
    return assert_processing_schema(engine)


def init_db():
    ensure_runtime_schema()
    _migrate()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
