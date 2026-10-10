"""Keep category-backed canvases aligned with the shared paper taxonomy."""

from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from models import DomainWorkspace
from sqlalchemy.orm import Session


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


def rename_category_canvas(db: Session, old: str, new: str) -> None:
    """Join the caller's category-rename transaction; never create a workspace."""
    row = db.get(DomainWorkspace, 1)
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
            db.query(DomainWorkspace)
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
