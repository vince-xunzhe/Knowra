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


def collect_category_domain_canvases(db: Session, name: str) -> dict:
    """Preview 索骥 (Domain) canvases that delete_category_canvas would remove.

    Association matches delete_category_canvas: ``board.category == name``, or a
    legacy board with no category field whose name is exactly this taxonomy
    category. Custom boards are left alone.
    """
    from services.roadmap_category_cleanup import board_belongs_to_category

    items = []
    revision = None
    row = db.get(DomainWorkspace, 1)
    if row is not None and isinstance(getattr(row, "state", None), dict):
        revision = getattr(row, "revision", None)
        for board in row.state.get("boards") or []:
            if not isinstance(board, dict) or not board_belongs_to_category(board, name):
                continue
            nodes = board.get("nodes") or []
            edges = board.get("edges") or []
            node_count = len(nodes) if isinstance(nodes, list) else 0
            edge_count = len(edges) if isinstance(edges, list) else 0
            contentful = bool(node_count or edge_count)
            will_delete = []
            if contentful:
                will_delete.append(
                    f"索骥画布「{board.get('name') or name}」及其 {node_count} 个节点、{edge_count} 条连线（含手工连线）"
                )
            else:
                will_delete.append(
                    f"索骥占位画布「{board.get('name') or name}」也会被删除，并非只删除大类名称"
                )
            items.append({
                "board_id": str(board.get("id") or ""),
                "name": str(board.get("name") or name),
                "kind": "domain",
                "nodes": node_count,
                "edges": edge_count,
                "contentful": contentful,
                "placeholder": not contentful,
                "will_delete": will_delete,
            })
    return {
        "category": name,
        "count": len(items),
        "contentful_count": sum(1 for item in items if item["contentful"]),
        "revision": revision,
        "items": items,
        "board_ids": [item["board_id"] for item in items if item["board_id"]],
    }
