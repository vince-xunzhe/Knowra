from __future__ import annotations

from typing import Any, Optional


PAPER_CATEGORY_LLM = "LLM"
PAPER_CATEGORY_VLM = "VLM"
PAPER_CATEGORY_VLA = "VLA"
PAPER_CATEGORY_STATIC_3D = "三维重建-静态"
PAPER_CATEGORY_DYNAMIC_3D = "三维重建-动态"
PAPER_CATEGORY_WORLD_MODEL = "世界模型"
PAPER_CATEGORY_OTHER = "其他"

PAPER_CATEGORY_OPTIONS = [
    PAPER_CATEGORY_LLM,
    PAPER_CATEGORY_VLM,
    PAPER_CATEGORY_VLA,
    PAPER_CATEGORY_STATIC_3D,
    PAPER_CATEGORY_DYNAMIC_3D,
    PAPER_CATEGORY_WORLD_MODEL,
    PAPER_CATEGORY_OTHER,
]

# The built-in defaults stay fixed. The *active* list is user-editable and
# persisted in config under "paper_categories"; it seeds from these defaults.
DEFAULT_PAPER_CATEGORY_OPTIONS = list(PAPER_CATEGORY_OPTIONS)

_active_cache: Optional[list] = None


def _clean_category_list(raw: Any) -> list:
    out: list = []
    seen: set = set()
    for item in raw or []:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    # "其他" is the structural fallback — always present, always last.
    out = [c for c in out if c != PAPER_CATEGORY_OTHER]
    out.append(PAPER_CATEGORY_OTHER)
    return out


def get_active_categories() -> list:
    """Active, user-editable category list (config-backed, cached).

    Falls back to the built-in defaults when nothing custom is stored.
    Call refresh_active_categories() after mutating config."""
    global _active_cache
    if _active_cache is not None:
        return _active_cache
    opts = None
    try:
        from config import load_config

        raw = load_config().get("paper_categories")
        if isinstance(raw, list) and raw:
            opts = _clean_category_list(raw)
    except Exception:
        opts = None
    if not opts:
        opts = _clean_category_list(DEFAULT_PAPER_CATEGORY_OPTIONS)
    _active_cache = opts
    return opts


def refresh_active_categories() -> None:
    global _active_cache
    _active_cache = None


def set_active_categories(categories: list) -> list:
    """Persist a new active category list to config; returns the cleaned list."""
    return persist_taxonomy(categories)


# Synonym table for the model's OWN category label — e.g. the LLM may write
# "Vision-Language-Action" or "自动驾驶" where we want "VLA". This only
# normalises the model's chosen *label*; it does NOT scan paper text for
# keywords to guess a category.
_CATEGORY_ALIASES = {
    "llm": PAPER_CATEGORY_LLM,
    "largelanguagemodel": PAPER_CATEGORY_LLM,
    "languagemodel": PAPER_CATEGORY_LLM,
    "大语言模型": PAPER_CATEGORY_LLM,
    "语言模型": PAPER_CATEGORY_LLM,
    "nlp": PAPER_CATEGORY_LLM,
    "vlm": PAPER_CATEGORY_VLM,
    "visionlanguagemodel": PAPER_CATEGORY_VLM,
    "visionlanguage": PAPER_CATEGORY_VLM,
    "multimodal": PAPER_CATEGORY_VLM,
    "多模态": PAPER_CATEGORY_VLM,
    "多模态vlm": PAPER_CATEGORY_VLM,
    "vla": PAPER_CATEGORY_VLA,
    "visionlanguageaction": PAPER_CATEGORY_VLA,
    "自动驾驶": PAPER_CATEGORY_VLA,
    "自动驾驶vla": PAPER_CATEGORY_VLA,
    "三维重建静态": PAPER_CATEGORY_STATIC_3D,
    "三维重建-静态": PAPER_CATEGORY_STATIC_3D,
    "3d重建静态": PAPER_CATEGORY_STATIC_3D,
    "3dreconstructionstatic": PAPER_CATEGORY_STATIC_3D,
    "static3dreconstruction": PAPER_CATEGORY_STATIC_3D,
    "三维重建动态": PAPER_CATEGORY_DYNAMIC_3D,
    "三维重建-动态": PAPER_CATEGORY_DYNAMIC_3D,
    "动态三维重建": PAPER_CATEGORY_DYNAMIC_3D,
    "3d重建动态": PAPER_CATEGORY_DYNAMIC_3D,
    "dynamic3dreconstruction": PAPER_CATEGORY_DYNAMIC_3D,
    "世界模型": PAPER_CATEGORY_WORLD_MODEL,
    "worldmodel": PAPER_CATEGORY_WORLD_MODEL,
    "其他": PAPER_CATEGORY_OTHER,
    "other": PAPER_CATEGORY_OTHER,
}


def _category_key(value: Any) -> str:
    import re

    return re.sub(r"[\s_/\-]+", "", str(value or "").strip().lower())


def normalize_paper_category(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    active = get_active_categories()
    # Exact match against the active list — this is what lets user-added
    # custom categories validate (they have no alias entry).
    if raw in active:
        return raw
    # Built-in alias mapping, but only resolve to a category still active
    # (so a removed default doesn't sneak back in via an alias).
    aliased = _CATEGORY_ALIASES.get(_category_key(raw))
    if aliased and aliased in active:
        return aliased
    # Retired or renamed labels follow a persisted redirect so a later
    # extraction cannot revive a deleted category name.
    redirected = follow_category_redirect(raw, active=active)
    if redirected:
        return redirected
    if aliased:
        redirected = follow_category_redirect(aliased, active=active)
        if redirected:
            return redirected
    return None


def derive_model_paper_category(paper: Any, extraction: Optional[dict[str, Any]]) -> Optional[str]:
    """The model's category = the LLM's own ``paper_category`` field, normalised
    to a currently-active category.

    There are deliberately NO keyword / text-scanning heuristics: the category
    comes from the model's semantic judgement (or the user's manual override).
    If the model didn't provide a usable category we leave it unset, and the
    effective category falls back to 其他."""
    if not isinstance(extraction, dict):
        return None
    return normalize_paper_category(extraction.get("paper_category"))


def effective_paper_category(paper: Any, extraction: Optional[dict[str, Any]] = None) -> str:
    override = normalize_paper_category(getattr(paper, "paper_category_override", None))
    if override:
        return override
    model_value = normalize_paper_category(getattr(paper, "paper_category_model", None))
    if model_value:
        return model_value
    derived = derive_model_paper_category(paper, extraction)
    return derived or PAPER_CATEGORY_OTHER


def sync_paper_category_fields(
    paper: Any,
    extraction: Optional[dict[str, Any]] = None,
    overwrite_model: bool = False,
) -> bool:
    changed = False

    normalized_override = normalize_paper_category(getattr(paper, "paper_category_override", None))
    if getattr(paper, "paper_category_override", None) != normalized_override:
        paper.paper_category_override = normalized_override
        changed = True

    derived_model = derive_model_paper_category(paper, extraction)
    current_model = normalize_paper_category(getattr(paper, "paper_category_model", None))
    next_model = current_model
    if overwrite_model:
        next_model = derived_model
    elif current_model is None and derived_model is not None:
        next_model = derived_model

    if getattr(paper, "paper_category_model", None) != next_model:
        paper.paper_category_model = next_model
        changed = True

    return changed


def paper_category_source(paper: Any) -> str:
    if normalize_paper_category(getattr(paper, "paper_category_override", None)):
        return "manual"
    if normalize_paper_category(getattr(paper, "paper_category_model", None)):
        return "model"
    return "none"


class PaperCategoryEditError(Exception):
    """Authoritative taxonomy edit failure with an HTTP-like status code."""

    def __init__(self, status_code: int, detail: str, details: Optional[dict] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.details = details or {}


def _parse_extraction(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        import json

        data = json.loads(raw) if isinstance(raw, str) else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_category_redirects() -> dict[str, str]:
    try:
        from config import load_config

        raw = load_config(resolve_presentation=False).get("paper_category_redirects")
        if isinstance(raw, dict):
            out: dict[str, str] = {}
            for key, value in raw.items():
                src = str(key or "").strip()
                dest = str(value or "").strip()
                if src and dest and src != dest:
                    out[src] = dest
            return out
    except Exception:
        pass
    return {}


def follow_category_redirect(
    value: Any,
    *,
    active: Optional[list] = None,
    redirects: Optional[dict[str, str]] = None,
) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    live = list(active if active is not None else get_active_categories())
    mapping = dict(redirects if redirects is not None else get_category_redirects())
    seen: set[str] = set()
    current = raw
    while current in mapping and current not in seen:
        seen.add(current)
        current = mapping[current]
        if current in live:
            return current
    return current if current in live else None


def _sanitize_redirects(redirects: dict[str, str], active: list[str]) -> dict[str, str]:
    live = set(active)
    cleaned: dict[str, str] = {}
    for src, dest in (redirects or {}).items():
        source = str(src or "").strip()
        if not source or source in live:
            continue
        final = follow_category_redirect(dest, active=active, redirects=redirects)
        if final and final != source:
            cleaned[source] = final
    return cleaned


def persist_taxonomy(categories: list, redirects: Optional[dict[str, str]] = None) -> list:
    """Write the active list and redirects together so a delete cannot leave a ghost name."""
    cleaned = _clean_category_list(categories)
    mapping = _sanitize_redirects(
        get_category_redirects() if redirects is None else redirects,
        cleaned,
    )
    from config import save_config

    save_config({"paper_categories": cleaned, "paper_category_redirects": mapping})
    refresh_active_categories()
    return get_active_categories()


def redirect_category_name(redirects: dict[str, str], old: str, new: str) -> dict[str, str]:
    updated: dict[str, str] = {}
    for src, dest in (redirects or {}).items():
        if src == new or src == old:
            continue
        updated[src] = new if dest == old else dest
    if old and new and old != new:
        updated[old] = new
    return updated


def find_category_conflict(
    name: str,
    active: Optional[list] = None,
    *,
    exclude: Optional[str] = None,
) -> Optional[str]:
    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    live = list(active if active is not None else get_active_categories())
    key = _category_key(cleaned)
    for existing in live:
        if exclude is not None and existing == exclude:
            continue
        if existing == cleaned or _category_key(existing) == key:
            return existing
    aliased = _CATEGORY_ALIASES.get(key)
    if aliased and aliased in live and aliased != exclude:
        return aliased
    return None


def papers_assigned_to_category(db: Any, name: str) -> list:
    assigned = []
    for paper in db.query(_paper_model()).all():
        extraction = _parse_extraction(getattr(paper, "raw_llm_response", None))
        if (
            (getattr(paper, "paper_category_override", None) or "") == name
            or (getattr(paper, "paper_category_model", None) or "") == name
            or effective_paper_category(paper, extraction) == name
        ):
            assigned.append(paper)
    return assigned


def _paper_model():
    from models import Paper

    return Paper


def retarget_paper_categories(db: Any, old: str, new: str) -> list:
    """Move override and/or model labels. Source kind is preserved per field."""
    touched = []
    for paper in db.query(_paper_model()).all():
        changed = False
        if (getattr(paper, "paper_category_override", None) or "") == old:
            paper.paper_category_override = new
            changed = True
        if (getattr(paper, "paper_category_model", None) or "") == old:
            paper.paper_category_model = new
            changed = True
        if changed:
            touched.append(paper)
    return touched


def add_taxonomy_category(name: str) -> list:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise PaperCategoryEditError(400, "分类名不能为空")
    active = get_active_categories()
    conflict = find_category_conflict(cleaned, active)
    if conflict:
        raise PaperCategoryEditError(400, f"分类已存在：{conflict}")
    redirects = get_category_redirects()
    redirects.pop(cleaned, None)
    return persist_taxonomy(
        [item for item in active if item != PAPER_CATEGORY_OTHER] + [cleaned],
        redirects,
    )


def rename_taxonomy_category(
    db: Any,
    name: str,
    new_name: str,
    *,
    rename_canvas,
    sync_record=None,
) -> dict[str, Any]:
    old = str(name or "")
    cleaned_new = str(new_name or "").strip()
    active = get_active_categories()
    if old == PAPER_CATEGORY_OTHER:
        raise PaperCategoryEditError(400, "“其他”是保留分类，不能重命名")
    if not cleaned_new:
        raise PaperCategoryEditError(400, "新分类名不能为空")
    if old not in active:
        leftover = papers_assigned_to_category(db, old)
        redirects = get_category_redirects()
        if cleaned_new in active and not leftover and redirects.get(old) == cleaned_new:
            return {"migrated": 0, "already_applied": True}
        raise PaperCategoryEditError(404, f"分类不存在：{old}")
    if cleaned_new == old:
        return {"migrated": 0, "already_applied": True}
    conflict = find_category_conflict(cleaned_new, active, exclude=old)
    if conflict:
        leftover = papers_assigned_to_category(db, old)
        # Retry after a successful paper move but a failed config write:
        # the old name is empty and the new name already exists.
        if conflict == cleaned_new and not leftover:
            rename_canvas(db, old, cleaned_new)
            db.commit()
            persist_taxonomy(
                [cleaned_new if item == old else item for item in active],
                redirect_category_name(get_category_redirects(), old, cleaned_new),
            )
            return {"migrated": 0, "already_applied": True}
        raise PaperCategoryEditError(400, f"目标分类已存在：{conflict}")

    rename_canvas(db, old, cleaned_new)
    touched = retarget_paper_categories(db, old, cleaned_new)
    db.commit()
    persist_taxonomy(
        [cleaned_new if item == old else item for item in active],
        redirect_category_name(get_category_redirects(), old, cleaned_new),
    )
    if sync_record:
        for paper in touched:
            sync_record(paper, event="category_rename")
    return {"migrated": len(touched), "already_applied": False}


def category_deletion_preview(db: Any, name: str, *, store=None) -> dict[str, Any]:
    import hashlib
    import json

    from services.domain_categories import collect_category_domain_canvases
    from services.roadmap_category_cleanup import collect_category_research

    assigned = papers_assigned_to_category(db, name)
    research = collect_category_research(db, name, store=store)
    canvases = collect_category_domain_canvases(db, name)
    payload = {
        "category": name,
        "paper_ids": sorted(str(getattr(paper, "id")) for paper in assigned),
        "research_fingerprint": research["fingerprint"],
        "research_board_ids": research.get("board_ids") or [],
        "canvas_revision": canvases.get("revision"),
        "canvas_boards": [
            {
                "board_id": item["board_id"],
                "name": item["name"],
                "nodes": item["nodes"],
                "edges": item["edges"],
                "contentful": item["contentful"],
            }
            for item in canvases.get("items") or []
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "name": name,
        "exists": name in get_active_categories(),
        "papers": {"count": len(assigned), "ids": payload["paper_ids"]},
        "research": research,
        "canvases": canvases,
        "fingerprint": fingerprint,
    }


def _preview_has_associations(preview: dict[str, Any]) -> bool:
    canvases = preview.get("canvases") or {}
    return bool(
        preview["research"]["count"]
        or preview["papers"]["count"]
        or canvases.get("count")
    )


def _require_deletion_fingerprint(preview: dict[str, Any], research_fingerprint: Optional[str]) -> None:
    if _preview_has_associations(preview):
        if research_fingerprint != preview["fingerprint"]:
            raise PaperCategoryEditError(
                409,
                "大类关联的论文、识途调研或索骥画布已变化，请重新确认后再删除。不会静默扩大删除范围。",
                details={"code": "research_changed", "preview": preview},
            )


def _research_plan_or_raise(
    db: Any,
    name: str,
    *,
    confirm_research: bool,
    research_fingerprint: Optional[str],
    store=None,
) -> dict[str, Any]:
    from services.roadmap_category_cleanup import RoadmapCleanupError, plan_category_research_cleanup

    try:
        return plan_category_research_cleanup(
            db,
            name,
            confirm=confirm_research,
            fingerprint=research_fingerprint,
            store=store,
        )
    except RoadmapCleanupError as error:
        details = {"code": error.code, **error.details}
        if error.code in {"research_changed", "research_confirmation_required"}:
            details["preview"] = category_deletion_preview(db, name, store=store)
        status = 409 if error.code in {
            "research_changed",
            "research_confirmation_required",
        } else 400
        raise PaperCategoryEditError(status, error.args[0], details=details) from error


def _finish_committed_cleanup(
    db: Any,
    *,
    plan: dict[str, Any],
    pending: Optional[dict[str, Any]],
    store=None,
    data_root=None,
) -> dict[str, Any]:
    from services.roadmap_category_cleanup import (
        finish_pending_file_cleanup,
        find_pending_cleanup_for_category,
    )

    record = pending
    if record is None and plan.get("category"):
        record = find_pending_cleanup_for_category(plan["category"], data_root=data_root)
    if record is None:
        return {
            "cleanup_status": "complete",
            "pending_cleanup": None,
            "deleted_assets": [],
            "skipped_assets": [],
            "failed_assets": [],
            "cancelled_jobs": [],
            "warnings": plan.get("warnings") or [],
            "retryable": False,
        }
    finished = finish_pending_file_cleanup(db, record, store=store, data_root=data_root)
    warnings = list(plan.get("warnings") or []) + list(finished.get("warnings") or [])
    finished["warnings"] = warnings
    return finished


def _commit_delete_and_cleanup(
    db: Any,
    *,
    name: str,
    plan: dict[str, Any],
    store=None,
    data_root=None,
    after_prepare,
    persist,
) -> dict[str, Any]:
    """Apply DB mutations, persist config, then unlink exclusive files.

    Irreversible file cleanup runs only after canvas CAS, commit, and taxonomy
    persistence succeed. A failed commit or canvas conflict leaves every
    original report image in place. Tombstones written as late-write barriers
    are removed if the transaction rolls back.
    """
    from services.roadmap_category_cleanup import (
        apply_category_research_db,
        build_pending_cleanup_record,
        remove_pending_cleanup,
        revert_research_barriers,
        write_pending_cleanup,
        write_research_barriers,
    )

    barriers: list = []
    pending = None
    pending_written = False
    try:
        barriers = write_research_barriers(plan, data_root=data_root)
        apply_category_research_db(db, plan)
        result = after_prepare()
        pending = build_pending_cleanup_record(plan, category=name)
        if pending is not None:
            write_pending_cleanup(pending, data_root=data_root)
            pending_written = True
        db.commit()
    except Exception:
        db.rollback()
        revert_research_barriers(barriers)
        if pending_written and pending is not None:
            remove_pending_cleanup(pending["id"], data_root=data_root)
        raise

    try:
        persist()
    except Exception:
        # Category change is already committed. Keep tombstones and the
        # pending file-cleanup record; do not delete any original images.
        raise

    finished = _finish_committed_cleanup(
        db,
        plan=plan,
        pending=pending,
        store=store,
        data_root=data_root,
    )
    result = dict(result)
    result["research"] = {**plan, **{k: finished[k] for k in finished}}
    result["cleanup_status"] = finished["cleanup_status"]
    result["pending_cleanup"] = finished.get("pending_cleanup")
    result["warnings"] = finished.get("warnings") or []
    return result


def delete_taxonomy_category(
    db: Any,
    name: str,
    target: Optional[str] = None,
    *,
    delete_canvas,
    sync_record=None,
    confirm_research: bool = False,
    confirm_canvases: bool = False,
    research_fingerprint: Optional[str] = None,
    store=None,
    data_root=None,
) -> dict[str, Any]:
    old = str(name or "")
    target_name = str(target or "").strip() or None
    active = get_active_categories()
    if old == PAPER_CATEGORY_OTHER:
        raise PaperCategoryEditError(400, "“其他”是保留分类，不能删除")

    assigned = papers_assigned_to_category(db, old)
    alternatives = [item for item in active if item != old]
    preview = category_deletion_preview(db, old, store=store)
    canvases = preview.get("canvases") or {}
    if preview["research"]["count"] and not confirm_research:
        raise PaperCategoryEditError(
            409,
            "该大类关联了识途调研工作。请确认后一并删除调研结果及本地副本；取消则保留大类，本次不做任何修改。",
            details={"code": "research_confirmation_required", "preview": preview},
        )
    if canvases.get("contentful_count") and not confirm_canvases:
        raise PaperCategoryEditError(
            409,
            "该大类关联了有内容的索骥画布（可能含手工连线）。请确认后一并删除这些画布；取消则保留大类和画布。不会删除资料库论文。",
            details={"code": "canvas_confirmation_required", "preview": preview},
        )

    def _run(already_deleted: bool, destination: Optional[str], persist_list: list):
        plan = _research_plan_or_raise(
            db,
            old,
            confirm_research=confirm_research,
            research_fingerprint=preview["research"]["fingerprint"] if confirm_research else research_fingerprint,
            store=store,
        )
        plan["category"] = old

        def after_prepare():
            if already_deleted and not assigned:
                delete_canvas(db, old)
                return {"migrated": 0, "already_deleted": True}
            delete_canvas(db, old)
            touched = retarget_paper_categories(db, old, destination) if assigned else []
            if sync_record:
                # Defer until after successful commit via closure result.
                pass
            return {"migrated": len(touched), "already_deleted": already_deleted, "touched": touched}

        def persist():
            if already_deleted and not assigned:
                return
            persist_taxonomy(
                persist_list,
                redirect_category_name(get_category_redirects(), old, destination or PAPER_CATEGORY_OTHER),
            )

        result = _commit_delete_and_cleanup(
            db,
            name=old,
            plan=plan,
            store=store,
            data_root=data_root,
            after_prepare=after_prepare,
            persist=persist,
        )
        if sync_record:
            for paper in result.pop("touched", []) or []:
                sync_record(paper, event="category_delete")
        else:
            result.pop("touched", None)
        return result

    if old not in active:
        if assigned:
            if not target_name or target_name not in get_active_categories() or target_name == old:
                raise PaperCategoryEditError(
                    409,
                    "分类已从列表移除，但仍有论文使用旧名称。请指定一个现有大类以完成转移。",
                )
        _require_deletion_fingerprint(preview, research_fingerprint)
        return _run(
            True,
            target_name,
            get_active_categories(),
        )

    if assigned:
        if not alternatives:
            raise PaperCategoryEditError(
                400,
                "没有其他可用大类，请先新增大类后再删除。不会自动创建目标大类。",
            )
        if not target_name:
            raise PaperCategoryEditError(
                400,
                f"该大类下有 {len(assigned)} 篇论文，请选择另一个已有大类用于转移后再删除。论文不会被删除。",
            )
        if target_name == old:
            raise PaperCategoryEditError(400, "不能将论文转移到被删除的大类自身")
        if target_name not in active:
            raise PaperCategoryEditError(
                409,
                f"目标大类不存在或已消失：{target_name}。请选择另一个已有大类，不会自动创建。",
            )
        destination = target_name
    else:
        if target_name:
            if target_name == old:
                raise PaperCategoryEditError(400, "不能将论文转移到被删除的大类自身")
            if target_name not in active:
                raise PaperCategoryEditError(
                    409,
                    f"目标大类不存在或已消失：{target_name}。请选择另一个已有大类，不会自动创建。",
                )
            destination = target_name
        else:
            destination = PAPER_CATEGORY_OTHER

    _require_deletion_fingerprint(preview, research_fingerprint)
    return _run(False, destination, [item for item in active if item != old])


def retry_pending_category_cleanup(
    db: Any,
    cleanup_id: str,
    *,
    store=None,
    data_root=None,
) -> dict[str, Any]:
    """Retry remaining local file cleanup only. Never repeats category or paper deletion."""
    from services.roadmap_category_cleanup import finish_pending_file_cleanup, load_pending_cleanup

    record = load_pending_cleanup(cleanup_id, data_root=data_root)
    if record is None:
        raise PaperCategoryEditError(404, "没有可重试的本地文件清理任务。")
    return finish_pending_file_cleanup(db, record, store=store, data_root=data_root)
