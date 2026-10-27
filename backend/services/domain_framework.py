"""Read saved local roadmaps as bounded, immutable analysis context.

These models are local-only singleton workspaces (not cloud/user tables). This
service never reconciles categories, refreshes tasks or writes the roadmap.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from models import DomainWorkspace, RoadmapPaper, RoadmapRun, RoadmapWorkspace
from pydantic import ValidationError

from routers.domains import Board


SUMMARY_KEYS = ("boardId", "name", "category", "version", "revision", "capturedAt", "truncated")


def ensure_local():
    from config import is_cloud_mode

    if is_cloud_mode():
        raise HTTPException(404, "识途参考框架仅在本地研究工作区可用。")


def _boards(row):
    if row is None:
        return []
    if not isinstance(row.state, dict) or not isinstance(row.state.get("boards"), list) or any(
        not isinstance(board, dict) for board in row.state["boards"]
    ):
        raise HTTPException(422, "研究工作区无法读取，请修复并重新加载，或不使用框架继续。")
    return row.state["boards"]


def domain_board(db, board_id):
    ensure_local()
    row = db.get(DomainWorkspace, 1)
    board = next((b for b in _boards(row) if b.get("id") == board_id), None)
    if not board:
        raise HTTPException(404, "索骥类别不存在，请重新加载画布。")
    return board


def _saved(db, board_id):
    ensure_local()
    row = db.get(RoadmapWorkspace, 1)
    board = next((b for b in _boards(row) if b.get("id") == board_id), None)
    if not board:
        raise HTTPException(404, "参考框架已删除或不可读取，请重新选择，或不使用框架继续。")
    try:
        board = Board.model_validate(board).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, "参考框架无法读取，请在识途修复并保存，或不使用框架继续。") from None
    nodes = [n for n in board["nodes"] if n["kind"] == "paper" or n.get("roadmapRole") in ("topic", "branch")]
    if not any(n["kind"] == "paper" or n["text"].strip() for n in nodes):
        raise HTTPException(422, "参考框架为空，请先在识途保存研究内容，或不使用框架继续。")
    paper_ids = {n["paperId"] for n in nodes if n["kind"] == "paper"}
    papers = {p.id: p.data for p in db.query(RoadmapPaper).filter(RoadmapPaper.id.in_(paper_ids)).all()}
    if paper_ids - papers.keys() or any(not isinstance(p, dict) for p in papers.values()):
        raise HTTPException(422, "参考框架的论文来源已不可读取，请在识途修复，或不使用框架继续。")
    from services.roadmap_state import PaperMetadata

    try:
        papers = {pid: PaperMetadata.model_validate(data).model_dump() for pid, data in papers.items()}
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, "参考框架的论文来源无法读取，请在识途修复，或不使用框架继续。") from None
    if any(data["id"] != pid for pid, data in papers.items()):
        raise HTTPException(422, "参考框架的论文来源标识不一致，请在识途修复，或不使用框架继续。")
    # Never borrow a newer research preview: it has not been applied to this board.
    report_row = db.query(RoadmapRun.id, RoadmapRun.created, RoadmapRun.proposal).filter_by(
        board_id=board_id, status="applied"
    ).order_by(RoadmapRun.created.desc(), RoadmapRun.id.desc()).first()
    if report_row and report_row.proposal is not None and not isinstance(report_row.proposal, dict):
        raise HTTPException(422, "参考框架报告无法读取，请在识途修复，或不使用框架继续。")
    report = (report_row.proposal or {}).get("report", "") if report_row else ""
    if not isinstance(report, str):
        raise HTTPException(422, "参考框架报告无法读取，请在识途修复，或不使用框架继续。")
    semantic = {
        "boardId": board["id"], "name": board["name"], "category": board["category"],
        "keywords": board["researchKeywords"],
        "nodes": [{"id": n["id"], "kind": n["kind"], "role": n["roadmapRole"],
                   "text": n["text"], "paperId": n["paperId"]} for n in nodes],
        "relations": [{"id": e["id"], "source": e["source"], "target": e["target"],
                       "label": e["label"], "structural": e["roadmapStructure"],
                       "analysis": e["analysis"]} for e in board["edges"]],
        "papers": [{"id": pid, **{k: p.get(k) for k in (
            "title", "year", "branch", "reason", "sourceUrl", "arxivId", "doi")}}
                   for pid, p in sorted(papers.items())],
        "report": {"runId": report_row.id if report_row else None,
                   "createdAt": report_row.created.isoformat() if report_row else None, "text": report},
    }
    version = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return row, semantic, version


def same_category(first, second):
    def key(board):
        return str(board.get("category") or board.get("name") or "").strip().casefold()
    return key(first) == key(second)


def choices(db, board_id):
    target = domain_board(db, board_id)
    row = db.get(RoadmapWorkspace, 1)
    result = []
    for board in _boards(row):
        try:
            _, semantic, version = _saved(db, board["id"])
        except (HTTPException, KeyError, TypeError):
            continue
        result.append({"boardId": board["id"], "name": semantic["name"],
                       "category": semantic["category"], "version": version,
                       "sameCategory": same_category(target, semantic),
                       "paperCount": len(semantic["papers"]), "nodeCount": len(semantic["nodes"])})
    return sorted(result, key=lambda item: (not item["sameCategory"], item["name"], item["boardId"]))


def _report_excerpt(section, limit=1800):
    """Keep a paragraph's complete source links when shortening its prose."""
    links_pattern = re.compile(r"\[[^\]\n]+\]\(https?://[^\s)]+\)")
    parts, size, truncated = [], 0, False
    for paragraph in section.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        separator = 2 if parts else 0
        available = limit - size - separator
        if len(paragraph) > available:
            truncated = True
            links = links_pattern.findall(paragraph)
            suffix = " ".join(links)
            # Reserve every exact citation before shortening the prose. If the
            # references alone cannot fit, omit the whole paragraph.
            prose_budget = available - len(suffix) - (3 if suffix else 1)
            if prose_budget <= 0:
                continue
            prose = links_pattern.sub("", paragraph).rstrip(" 、")
            paragraph = prose[:prose_budget].rstrip() + "…" + (" " + suffix if suffix else "")
        parts.append(paragraph)
        size += separator + len(paragraph)
    return "\n\n".join(parts), truncated


def capture(db, target, reference):
    row, semantic, version = _saved(db, reference.boardId)
    if reference.version != version:
        raise HTTPException(409, "参考框架已变化，请刷新框架列表并重新选择，或不使用框架继续。")
    truncated = False

    def clip(value, limit):
        nonlocal truncated
        value = value if isinstance(value, str) else ""
        truncated |= len(value) > limit
        return value[:limit]

    def bounded(records, budget):
        nonlocal truncated
        result, size = [], 0
        for record in records:
            length = len(json.dumps(record, ensure_ascii=False))
            if size + length > budget:
                truncated = True
                continue
            result.append(record)
            size += length
        return result

    nodes = bounded([{**n, "text": clip(n["text"], 600)} for n in semantic["nodes"]], 6500)
    paper_ids = {n["paperId"] for n in nodes if n["paperId"]}
    # Source identifiers and locators remain exact; if a complete record cannot
    # fit, bounded() drops it and its node instead of corrupting the citation.
    papers = bounded([{**p, "title": clip(p["title"], 500), "reason": clip(p["reason"], 600)}
                      for p in semantic["papers"] if p["id"] in paper_ids], 9500)
    kept_papers = {p["id"] for p in papers}
    # If a record does not fit, omit its node too; never leave dangling sources.
    nodes = [n for n in nodes if not n["paperId"] or n["paperId"] in kept_papers]
    kept_nodes = {n["id"] for n in nodes}
    relations = []
    for edge in semantic["relations"]:
        if edge["source"] not in kept_nodes or edge["target"] not in kept_nodes:
            truncated = True
            continue
        analysis = edge["analysis"] or {}
        evidence = analysis.get("evidence", [])
        truncated |= len(evidence) > 2
        relations.append({k: edge[k] for k in ("id", "source", "target", "structural")} | {
            "label": clip(edge["label"], 200), "relation": analysis.get("relation"),
            "reason": clip(analysis.get("reason"), 600),
            "evidence": [{"paperId": e.get("paperId"), "source": e.get("source"),
                          "locator": e.get("locator"), "page": e.get("page"),
                          "quote": clip(e.get("quote"), 350)} for e in evidence[:2]],
        })
    relations = bounded(relations, 5500)
    report = semantic["report"]["text"]

    def recorded(label):
        match = re.search(r"^\*\*" + label + r"\*\*：([^\n]*)", report, re.MULTILINE)
        return clip(match.group(1) if match else "", 1200)

    sections = []
    for heading in ("## 5. 发展历史与范式里程碑", "## 6. 当前研究分支", "## 7. 开放问题"):
        start = report.find(heading)
        if start >= 0:
            end = report.find("\n## ", start + len(heading))
            section = report[start:end if end >= 0 else len(report)]
            # The graph is provided separately with original node/source IDs.
            section = re.sub(r"```mermaid\n.*?```", "", section, flags=re.DOTALL)
            excerpt, shortened = _report_excerpt(section)
            truncated |= shortened
            sections.append({"heading": heading, "text": excerpt})
    context = {k: semantic[k] for k in ("boardId", "name", "category", "keywords")}
    context.update(nodes=nodes, papers=papers, relations=relations,
                   researchQuestion=recorded("研究问题"), scope=recorded("范围"),
                   report={"runId": semantic["report"]["runId"], "createdAt": semantic["report"]["createdAt"], "excerpts": sections})
    warnings = []
    if not same_category(target, semantic):
        warnings.append("所选识途框架与当前索骥类别不同；不匹配或无法归类的论文应保持独立。")
    if report:
        warnings.append("报告来自该框架最近一次已应用调研；后续手工编辑以当前保存画布为准，报告仅辅助分析。")
    if truncated:
        warnings.append("框架上下文已按容量截断，保留条目的来源；未包含的分支或阶段不代表不存在。")
    return {"boardId": semantic["boardId"], "name": semantic["name"], "category": semantic["category"],
            "version": version, "revision": row.revision,
            "capturedAt": datetime.now(timezone.utc).isoformat(), "truncated": truncated,
            "context": context, "warnings": warnings}


def summary(snapshot):
    framework = (snapshot or {}).get("framework")
    return {k: framework[k] for k in SUMMARY_KEYS} if framework else None
