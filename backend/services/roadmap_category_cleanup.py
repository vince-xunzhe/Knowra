"""Identify and remove 识途 research that belongs to one paper category.

Association is exact and structural: a RoadmapWorkspace board is included
only when ``board.category == name``, or when a legacy board has no category
field but its name is exactly this taxonomy category. Custom boards with a
different or empty category are left alone, even if the title is similar.

Library papers and their PDFs are never deleted here. Catalog rows and report
images are removed only when no remaining 识途 board or run still references
them. External paths and outbound symlinks are retained.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models import RoadmapPaper, RoadmapRun, RoadmapWorkspace
from path_utils import DATA_DIR
from services.paper_deletion_service import is_managed_data_path, safe_unlink
from services.roadmap_figures import ASSET_DIR, ASSET_ID, ASSET_URL, asset_path


_TOMBSTONE_DIRNAME = "deleted_roadmap_boards"
_DATA_ROOT_OVERRIDE: Optional[Path] = None


class RoadmapCleanupError(Exception):
    def __init__(self, message: str, *, code: str = "cleanup_failed", details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def set_cleanup_data_root(path: Optional[Path]) -> None:
    """Test hook so official/unit runs never touch the daily data directory."""
    global _DATA_ROOT_OVERRIDE
    _DATA_ROOT_OVERRIDE = Path(path) if path is not None else None


def cleanup_data_root(data_root: Optional[Path] = None) -> Path:
    return Path(data_root or _DATA_ROOT_OVERRIDE or DATA_DIR)


def tombstone_dir(data_root: Optional[Path] = None) -> Path:
    return cleanup_data_root(data_root) / _TOMBSTONE_DIRNAME


def tombstone_path(board_id: str, data_root: Optional[Path] = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(board_id))
    return tombstone_dir(data_root) / f"{safe}.json"


def write_board_tombstone(
    board_id: str,
    payload: Optional[dict] = None,
    data_root: Optional[Path] = None,
) -> Path:
    path = tombstone_path(board_id, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "board_id": str(board_id),
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        **(payload or {}),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def has_board_tombstone(board_id: str, data_root: Optional[Path] = None) -> bool:
    try:
        return tombstone_path(board_id, data_root).is_file()
    except OSError:
        return False


def should_skip_deleted_roadmap_board(board_id: str, data_root: Optional[Path] = None) -> bool:
    """Workers must consult this before writing previews, catalog rows, or files."""
    return bool(board_id) and has_board_tombstone(board_id, data_root)


def board_belongs_to_category(board: dict, name: str) -> bool:
    if not isinstance(board, dict) or not name:
        return False
    if board.get("category") == name:
        return True
    # Legacy boards adopted into the shared taxonomy keep the category name
    # but may not yet have the category field written.
    return (not board.get("category")) and board.get("name") == name


def _workspace_boards(db) -> list[dict]:
    row = db.get(RoadmapWorkspace, 1)
    if row is None or not isinstance(getattr(row, "state", None), dict):
        return []
    boards = row.state.get("boards") or []
    return [board for board in boards if isinstance(board, dict)]


def _paper_ids_from_board(board: dict) -> set[str]:
    ids: set[str] = set()
    for node in board.get("nodes") or []:
        if isinstance(node, dict) and node.get("kind") == "paper" and node.get("paperId"):
            ids.add(str(node["paperId"]))
    return ids


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _asset_ids_from_value(value: Any) -> set[str]:
    found: set[str] = set()
    for text in _walk_strings(value):
        found.update(ASSET_URL.findall(text))
        if ASSET_ID.fullmatch(text):
            found.add(text)
    return found


def _run_references(run) -> tuple[set[str], set[str]]:
    papers: set[str] = set()
    assets: set[str] = set()
    proposal = getattr(run, "proposal", None) or {}
    snapshot = getattr(run, "snapshot", None) or {}
    if isinstance(proposal, dict):
        papers.update(item.get("id") for item in proposal.get("_papers") or [] if isinstance(item, dict) and item.get("id"))
        papers |= _paper_ids_from_board(proposal.get("board") or {})
        assets |= _asset_ids_from_value(proposal)
    if isinstance(snapshot, dict):
        papers |= _paper_ids_from_board(snapshot.get("board") or {})
        assets |= _asset_ids_from_value(snapshot)
    return {str(item) for item in papers if item}, assets


def _jobs_for_board(board_id: str, store=None) -> list[dict]:
    if store is None:
        try:
            from services.task_runtime import store as runtime_store

            store = runtime_store()
        except Exception:
            return []
    found: list[dict] = []
    for prefix, kind in (("roadmap:", "research"), ("roadmap_import:", "import")):
        try:
            job = store.latest(prefix + board_id)
        except Exception:
            job = None
        if job and job.get("status") in ("queued", "running"):
            found.append({
                "job_id": job.get("job_id"),
                "kind": kind,
                "status": job.get("status"),
                "channel": prefix + board_id,
            })
    return found


def _research_present(board: dict, runs: list, jobs: list) -> bool:
    if board.get("nodes") or board.get("edges") or board.get("researchKeywords"):
        return True
    if runs or jobs:
        return True
    return False


def collect_category_research(db, name: str, *, store=None) -> dict[str, Any]:
    """Authoritative preview of 识途 research tied to this paper category."""
    boards = [board for board in _workspace_boards(db) if board_belongs_to_category(board, name)]
    all_boards = _workspace_boards(db)
    other_ids = {str(board.get("id")) for board in all_boards if board not in boards and board.get("id")}
    other_runs = []
    if other_ids:
        other_runs = db.query(RoadmapRun).filter(RoadmapRun.board_id.in_(list(other_ids))).all()
    reserved_papers: set[str] = set()
    reserved_assets: set[str] = set()
    for board in all_boards:
        if board in boards:
            continue
        reserved_papers |= _paper_ids_from_board(board)
    for run in other_runs:
        papers, assets = _run_references(run)
        reserved_papers |= papers
        reserved_assets |= assets

    items = []
    for board in boards:
        board_id = str(board.get("id") or "")
        runs = db.query(RoadmapRun).filter_by(board_id=board_id).all() if board_id else []
        jobs = _jobs_for_board(board_id, store=store) if board_id else []
        if not _research_present(board, runs, jobs):
            continue
        catalog_ids = _paper_ids_from_board(board)
        asset_ids: set[str] = set()
        run_summaries = []
        for run in runs:
            papers, assets = _run_references(run)
            catalog_ids |= papers
            asset_ids |= assets
            created = getattr(run, "created", None)
            run_summaries.append({
                "id": run.id,
                "status": run.status,
                "created": created.isoformat() if created is not None else None,
            })
        exclusive_papers = sorted(catalog_ids - reserved_papers)
        shared_papers = sorted(catalog_ids & reserved_papers)
        exclusive_assets = sorted(asset_ids - reserved_assets)
        shared_assets = sorted(asset_ids & reserved_assets)
        keywords = [str(item) for item in (board.get("researchKeywords") or []) if str(item).strip()]
        card_count = len(_paper_ids_from_board(board))
        will_delete = ["识途画布、关注关键词和该方向上的论文卡片布局"]
        if run_summaries:
            will_delete.append("该方向的调研任务、预览方案和已应用报告快照")
        if exclusive_assets:
            will_delete.append(f"仅被该方向引用的报告插图 {len(exclusive_assets)} 张")
        if exclusive_papers:
            will_delete.append(f"仅被该方向使用的识途来源目录条目 {len(exclusive_papers)} 条")
        if jobs:
            will_delete.append("将取消该方向正在进行或排队中的调研/下载任务，并阻止迟到写回")
        retained = ["资料库已入库论文及其原始 PDF", "其他识途方向仍在使用的来源条目和报告插图", "管理目录外的外部源文件"]
        if shared_papers:
            retained.append(f"其他方向仍引用的来源条目 {len(shared_papers)} 条")
        if shared_assets:
            retained.append(f"其他方向仍引用的报告插图 {len(shared_assets)} 张")
        items.append({
            "board_id": board_id,
            "name": str(board.get("name") or name),
            "keywords": keywords,
            "paper_cards": card_count,
            "runs": run_summaries,
            "jobs": jobs,
            "exclusive_catalog_ids": exclusive_papers,
            "shared_catalog_ids": shared_papers,
            "exclusive_asset_ids": exclusive_assets,
            "shared_asset_ids": shared_assets,
            "will_delete": will_delete,
            "will_retain": retained,
        })

    canonical = {
        "category": name,
        "items": [
            {
                "board_id": item["board_id"],
                "name": item["name"],
                "keywords": item["keywords"],
                "paper_cards": item["paper_cards"],
                "run_ids": [run["id"] for run in item["runs"]],
                "job_ids": [job.get("job_id") for job in item["jobs"]],
                "exclusive_catalog_ids": item["exclusive_catalog_ids"],
                "exclusive_asset_ids": item["exclusive_asset_ids"],
            }
            for item in items
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "category": name,
        "count": len(items),
        "items": items,
        "fingerprint": fingerprint,
        "board_ids": [item["board_id"] for item in items],
        "placeholder_board_ids": [
            str(board.get("id"))
            for board in boards
            if board.get("id") and str(board.get("id")) not in {item["board_id"] for item in items}
        ],
    }


def _cancel_jobs(jobs: list[dict], store=None) -> list[str]:
    cancelled: list[str] = []
    if store is None:
        try:
            from services.task_runtime import store as runtime_store

            store = runtime_store()
        except Exception:
            return cancelled
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue
        try:
            status = job.get("status")
            if status == "queued" and hasattr(store, "cancel_queued"):
                try:
                    store.cancel_queued(job_id)
                except Exception:
                    store.update(job_id, status="cancelled", error="技术方向已删除，已取消任务。")
            else:
                store.update(job_id, status="cancelled", error="技术方向已删除，已取消任务。")
            cancelled.append(job_id)
        except Exception:
            continue
    return cancelled


def _delete_exclusive_assets(asset_ids: list[str], data_root: Path) -> dict[str, Any]:
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    asset_root = Path(data_root) / "roadmap_assets"
    for asset_id in asset_ids:
        try:
            path = asset_root / f"{asset_id}.png"
            # Reuse the official path constructor when the default asset root
            # matches, but still honor an isolated test data_root.
            if data_root == Path(DATA_DIR) or data_root == Path(ASSET_DIR).parent:
                try:
                    path = asset_path(asset_id)
                except ValueError as exc:
                    skipped.append({"id": asset_id, "reason": str(exc)})
                    continue
            if path.is_symlink():
                skipped.append({"id": asset_id, "reason": "symlink_retained"})
                continue
            if not is_managed_data_path(path, data_root):
                skipped.append({"id": asset_id, "reason": "unmanaged_path"})
                continue
            result = safe_unlink(path, data_root)
            if result == "deleted" or result == "missing":
                if result == "deleted":
                    deleted.append(asset_id)
            else:
                skipped.append({"id": asset_id, "reason": result})
        except Exception as exc:
            raise RoadmapCleanupError(
                f"无法删除识途报告插图：{exc}",
                code="file_delete_failed",
                details={"asset_id": asset_id, "error": str(exc)},
            ) from exc
    return {"deleted": deleted, "skipped": skipped}


def cleanup_category_research(
    db,
    name: str,
    *,
    confirm: bool,
    fingerprint: Optional[str],
    store=None,
    data_root: Optional[Path] = None,
    require_confirm_if_present: bool = True,
) -> dict[str, Any]:
    """Delete confirmed 识途 research for one category, or refuse safely."""
    preview = collect_category_research(db, name, store=store)
    if preview["count"] == 0:
        return {
            "cleaned": 0,
            "already_clean": True,
            "fingerprint": preview["fingerprint"],
            "deleted_boards": preview.get("placeholder_board_ids") or [],
            "warnings": [],
            "retained": ["资料库已入库论文及其原始 PDF"],
        }
    if require_confirm_if_present and not confirm:
        raise RoadmapCleanupError(
            f"该大类关联 {preview['count']} 项识途调研。请确认删除这些调研及其本地副本后再删除大类；取消将保留大类和调研。不会删除资料库论文。",
            code="research_confirmation_required",
            details={"preview": preview},
        )
    if not fingerprint or fingerprint != preview["fingerprint"]:
        raise RoadmapCleanupError(
            "识途调研关联已变化，请重新确认后再删除。不会静默扩大删除范围。",
            code="research_changed",
            details={"preview": preview},
        )

    root = cleanup_data_root(data_root)
    exclusive_assets: list[str] = []
    exclusive_catalog: list[str] = []
    run_ids: list[str] = []
    jobs: list[dict] = []
    for item in preview["items"]:
        exclusive_assets.extend(item["exclusive_asset_ids"])
        exclusive_catalog.extend(item["exclusive_catalog_ids"])
        run_ids.extend(run["id"] for run in item["runs"])
        jobs.extend(item["jobs"])
        if item["board_id"]:
            write_board_tombstone(item["board_id"], {"category": name}, data_root=root)
    for board_id in preview.get("placeholder_board_ids") or []:
        write_board_tombstone(board_id, {"category": name, "placeholder": True}, data_root=root)

    cancelled_jobs = _cancel_jobs(jobs, store=store)
    for run in db.query(RoadmapRun).filter(RoadmapRun.id.in_(run_ids or ["__none__"])).all():
        if run.status in ("pending", "preview"):
            run.status = "cancelled"
            run.error = "技术方向已删除，调研已取消。"

    file_result = _delete_exclusive_assets(sorted(set(exclusive_assets)), root)

    if run_ids:
        db.query(RoadmapRun).filter(RoadmapRun.id.in_(run_ids)).delete(synchronize_session=False)
    if exclusive_catalog:
        db.query(RoadmapPaper).filter(RoadmapPaper.id.in_(exclusive_catalog)).delete(synchronize_session=False)

    return {
        "cleaned": preview["count"],
        "already_clean": False,
        "fingerprint": preview["fingerprint"],
        "deleted_boards": [item["board_id"] for item in preview["items"]] + list(preview.get("placeholder_board_ids") or []),
        "deleted_runs": run_ids,
        "deleted_catalog": exclusive_catalog,
        "deleted_assets": file_result["deleted"],
        "skipped_assets": file_result["skipped"],
        "cancelled_jobs": cancelled_jobs,
        "warnings": [
            f"保留无法确认归属或仍被共享的资源：{item['id']}（{item['reason']}）"
            for item in file_result["skipped"]
        ],
        "retained": ["资料库已入库论文及其原始 PDF", "其他识途方向的调研"],
    }
