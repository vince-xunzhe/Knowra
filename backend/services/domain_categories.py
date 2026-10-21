"""Keep category-backed canvases aligned with the shared paper taxonomy."""

from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from models import DomainWorkspace, RoadmapWorkspace
from sqlalchemy.orm import Session

from services.paper_category_service import (
    PAPER_CATEGORY_OTHER,
    get_active_categories,
    set_active_categories,
)


def reconcile_categories(state: dict, categories: list[str]) -> dict:
    result = deepcopy(state)
    remaining = result["boards"]
    inherited = []
    for category in categories:
        board = next((b for b in remaining if b.get("category") == category), None)
        if board is None:
            # Adopt a matching legacy canvas, preserving its ID and all edits.
            board = next(
                (
                    b
                    for b in remaining
                    if not b.get("category") and b["name"] == category
                ),
                None,
            )
        if board is None:
            board = {
                "id": str(uuid4()),
                "name": category,
                "nodes": [],
                "edges": [],
                "viewport": {"x": 80, "y": 80, "zoom": 1},
            }
        else:
            remaining.remove(board)
        board.update(name=category, category=category)
        inherited.append(board)
    custom = []
    for board in remaining:
        obsolete = board.get("category") or (
            board["id"] == "3d" and board["name"] == "三维重建"
        )
        if obsolete and not board["nodes"] and not board["edges"]:
            continue
        board["category"] = None
        custom.append(board)
    result["boards"] = inherited + custom
    if result.get("activeId") not in {b["id"] for b in result["boards"]}:
        result["activeId"] = result["boards"][0]["id"] if result["boards"] else None
    return result


def promote_workspace_categories(db: Session) -> list[str]:
    """Promote legacy page-local boards into the shared paper taxonomy."""
    active = get_active_categories()
    additions: list[str] = []
    known = set(active)
    for model in (DomainWorkspace, RoadmapWorkspace):
        row = db.get(model, 1)
        if row is None or not isinstance(row.state, dict):
            continue
        for board in row.state.get("boards", []):
            if not isinstance(board, dict):
                continue
            name = str(board.get("name") or "").strip()
            if name and name not in known:
                known.add(name)
                additions.append(name)
    if additions:
        set_active_categories(
            [category for category in active if category != PAPER_CATEGORY_OTHER]
            + additions
            + ([PAPER_CATEGORY_OTHER] if PAPER_CATEGORY_OTHER in active else [])
        )
    return get_active_categories()


def rename_category_canvas(db: Session, old: str, new: str) -> None:
    """Join the caller's category-rename transaction; never create a workspace."""
    for model in (DomainWorkspace, RoadmapWorkspace):
        _rename_workspace(db, model, old, new)


def _rename_workspace(db, model, old, new):
    row = db.get(model, 1)
    if row is None:
        return
    state = deepcopy(row.state)
    board = next((b for b in state["boards"] if b.get("category") == old), None)
    if board is None:
        board = next(
            (b for b in state["boards"] if not b.get("category") and b["name"] == old),
            None,
        )
    if board is not None:
        board.update(name=new, category=new)
        # Row version is incremented with the change so an open editor cannot
        # overwrite the rename with a stale autosave.
        updated = (
            db.query(model)
            .filter_by(id=1, revision=row.revision)
            .update(
                {"state": state, "revision": row.revision + 1},
                synchronize_session=False,
            )
        )
        if not updated:
            db.rollback()
            raise HTTPException(
                409, "Canvas changed during category rename; please retry"
            )
        db.expire(row)


def delete_category_canvas(db: Session, name: str) -> None:
    """Remove one global category and its canvases from both workspaces."""
    for model in (DomainWorkspace, RoadmapWorkspace):
        row = db.get(model, 1)
        if row is None:
            continue
        state = deepcopy(row.state)
        boards = [
            board
            for board in state["boards"]
            if not (
                board.get("category") == name
                or (not board.get("category") and board.get("name") == name)
            )
        ]
        if len(boards) == len(state["boards"]):
            continue
        state["boards"] = boards
        if state.get("activeId") not in {board["id"] for board in boards}:
            state["activeId"] = boards[0]["id"] if boards else None
        updated = (
            db.query(model)
            .filter_by(id=1, revision=row.revision)
            .update(
                {"state": state, "revision": row.revision + 1},
                synchronize_session=False,
            )
        )
        if not updated:
            db.rollback()
            raise HTTPException(
                409, "Canvas changed during category deletion; please retry"
            )
        db.expire(row)
