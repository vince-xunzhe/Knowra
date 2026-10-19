"""Verified bibliographic discovery followed by an editable, evidence-linked preview."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from database import SessionLocal
from models import RoadmapPaper, RoadmapRun

from services.domain_agent import RELATIONS as LEGACY_RELATIONS
from services.roadmap_layout import arrange_tree
from services.roadmap_sources import canonical_arxiv_id, canonical_doi, normalized_title
from services.roadmap_state import Cancelled, check, fail_run, persist_preview

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "shitu-roadmap"
RELATIONS = {**LEGACY_RELATIONS, "evolves_into": "演进", "branches_into": "分化",
             "enables": "突破推动", "unifies": "融合"}


def skill_instructions():
    # The versioned skill is also installable in Codex; runtime uses this same
    # source, rather than a separate prompt that can drift from its workflow.
    root = SKILL_DIR
    return "\n\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in ("SKILL.md", "references/output-contract.md")
    )


def decode(text):
    raw = str(text).strip()
    if len(raw) > 500000:
        raise ValueError("调研结果过大，请重试。")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("调研结果不是有效 JSON 对象。")  # noqa: TRY004 - invalid model output, not a caller type error
    return result


def text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def normalized(value):
    return " ".join(str(value or "").split()).casefold()


def validate_plan(raw):
    queries = raw.get("queries", [])
    seeds = raw.get("papers", [])
    if not isinstance(queries, list) or not isinstance(seeds, list):
        raise ValueError("检索规划缺少有效的查询词或论文列表。")  # noqa: TRY004 - invalid model output
    result = {"queries": [], "papers": []}
    result["researchQuestion"] = text(raw.get("researchQuestion"), 1000)
    result["scope"] = text(raw.get("scope"), 1500)
    for key in ("inclusionCriteria", "exclusionCriteria"):
        values = raw.get(key, [])
        result[key] = [text(v, 500) for v in values[:8] if text(v, 500)] if isinstance(values, list) else []
    for query in queries[:3]:
        if text(query, 200):
            result["queries"].append(text(query, 200))
    for seed in seeds[:35]:
        if isinstance(seed, dict) and text(seed.get("title"), 500):
            result["papers"].append({
                "title": text(seed.get("title"), 500),
                "arxivId": text(seed.get("arxivId"), 80) or None,
                "doi": text(seed.get("doi"), 200) or None,
            })
    if not result["queries"] and not result["papers"]:
        raise ValueError("未生成可核验的检索线索，请重试。")
    return result


def scope_catalog(board_id, incoming, existing):
    """Keep each board's rationale independent while preserving existing IDs."""
    by_url = {p["sourceUrl"]: p for p in existing}
    catalog = {p["id"]: deepcopy(p) for p in existing}
    for item in incoming:
        matches = [p for p in existing if same_identity(p, item)]
        old = by_url.get(item["sourceUrl"]) or (matches[0] if len(matches) == 1 else None)
        pid = old["id"] if old else str(uuid5(NAMESPACE_URL, board_id + ":" + item["sourceUrl"]))
        # A newer resolution may discover an arXiv version, but don't downgrade
        # an already verified arXiv identity to a DOI-only record.
        candidate = dict(item, id=pid)
        if old and old.get("arxivId") and not candidate.get("arxivId"):
            candidate = deepcopy(old)
        elif old:
            # Source resolvers supply bibliographic facts, not canvas branch
            # decisions. Keep saved descriptions for cards not re-selected.
            for key in ("reason", "branch"):
                if not candidate.get(key):
                    candidate[key] = old.get(key, "")
        catalog[pid] = candidate
    return catalog


def same_identity(left, right):
    keys = ("doi", "arxivId")
    if any(left.get(k) and right.get(k) and left[k] != right[k] for k in keys):
        return False
    if any(left.get(k) and left[k] == right.get(k) for k in keys):
        return True
    if normalized_title(left["title"]) != normalized_title(right["title"]):
        return False
    authors = {normalized_title(a) for a in left.get("authors", []) if normalized_title(a)}
    return bool(authors & {normalized_title(a) for a in right.get("authors", []) if normalized_title(a)})


def citation_evidence(source, target, evidence):
    """A shared topic is not a citation: require a reference or explicit mention."""
    title = normalized_title(source["title"])
    identifiers = [source.get("doi"), source.get("arxivId")]
    mentions = [item for item in evidence if item["paperId"] == target["id"] and (
        (len(title) >= 12 and title in normalized_title(item["quote"]))
        or any(value and normalized(value) in normalized(item["quote"]) for value in identifiers))]
    for reference in target.get("references", []):
        for field, parser in (("doi", canonical_doi), ("arxivId", canonical_arxiv_id)):
            if not source.get(field):
                continue
            try:
                if parser(reference) == parser(source[field]):
                    return ([{"paperId": target["id"], "source": "text", "page": None,
                              "locator": target["sourceUrl"], "quote": reference}]
                            + [e for e in mentions
                               if e["page"] is not None or e["locator"].endswith("#fulltext")])
            except (ValueError, TypeError):
                continue
    for item in mentions:
        quote = item["quote"]
        fulltext = target.get("fullText", "")
        if fulltext and normalized(quote) in normalized(fulltext):
            return [item]
        # Require the mention in the citing work's abstract, not merely either
        # paper's own title or a generic passage about the same research topic.
        if normalized(quote) not in normalized(target.get("abstract")):
            continue
        if ((len(title) >= 12 and title in normalized_title(quote))
                or any(value and normalized(value) in normalized(quote) for value in identifiers)):
            return [item]
    return []


def grounded_excerpt(paper, excerpt, page=None):
    for part in paper.get("fullTextPages", []):
        if part.get("page") == page and normalized(excerpt) in normalized(part.get("text")):
            url = paper.get("fullTextSource") or paper["sourceUrl"]
            return {"paperId": paper["id"], "source": "text", "page": page,
                    "locator": url + (f"#page={page}" if page is not None else "#fulltext"), "quote": excerpt}
    if page is None and any(normalized(excerpt) in normalized(paper.get(field)) for field in ("abstract", "title")):
        return {"paperId": paper["id"], "source": "text", "page": None,
                "locator": paper["sourceUrl"], "quote": excerpt}
    return None


def screen_candidates(raw, catalog):
    selected, _, rejected = select_and_ground({"papers": raw.get("papers"), "edges": []}, catalog)
    key_ids = raw.get("keyPaperIds", [])
    key_ids = list(dict.fromkeys(pid for pid in key_ids if isinstance(pid, str) and pid in selected))[:8] if isinstance(key_ids, list) else []
    if not key_ids:
        key_ids = list(selected)[:6]
    excluded = []
    values = raw.get("excluded", [])
    for entry in values if isinstance(values, list) else []:
        if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry["id"] in catalog and entry["id"] not in selected and text(entry.get("reason"), 1000)):
            excluded.append({"id": entry["id"], "reason": text(entry["reason"], 1000)})
    return selected, key_ids, excluded, rejected


def ground_evaluation(raw, paper):
    result = {key: text(raw.get(key), 3000) for key in ("contribution", "method", "evaluation", "limitations", "relevance")}
    result.update(id=paper["id"], evidence=[], include=True)
    if raw.get("include") is False and text(raw.get("screeningReason"), 1000):
        result.update(include=False, screeningReason=text(raw["screeningReason"], 1000))
    for item in raw.get("evidence", [])[:8] if isinstance(raw.get("evidence"), list) else []:
        if not isinstance(item, dict):
            continue
        excerpt = text(item.get("quote"), 2400)
        if len(excerpt) < 12:
            continue
        evidence = grounded_excerpt(paper, excerpt, item.get("page"))
        if evidence and (evidence["page"] is not None or evidence["locator"].endswith("#fulltext")):
            result["evidence"].append(evidence)
    # Unsupported interpretation must not be passed on as a full-text finding.
    if not result["evidence"]:
        return {"id": paper["id"], "error": "全文评估未返回可定位引文，未采用其分析。", "evidence": []}
    return result


def select_and_ground(raw, catalog):
    picks, raw_edges = raw.get("papers"), raw.get("edges", [])
    if not isinstance(picks, list) or not isinstance(raw_edges, list):
        raise ValueError("调研结果缺少论文或连线列表。")  # noqa: TRY004 - invalid model output
    selected, rejected = {}, 0
    for item in picks:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            rejected += 1
            continue
        pid = item["id"]
        reason, branch = text(item.get("reason"), 1500), text(item.get("branch"), 80)
        if pid not in catalog or not reason or not branch:
            rejected += 1
            continue
        if pid not in selected and len(selected) < 30:
            selected[pid] = dict(catalog[pid], reason=reason, branch=branch)
            if isinstance(item.get("milestone"), bool):
                selected[pid]["milestone"] = item["milestone"]
    if not selected:
        raise ValueError("没有可核验的路线图论文；原画布未修改。")
    relations, pairs, successors = [], set(), {}
    for edge in raw_edges[:200]:
        if not isinstance(edge, dict):
            rejected += 1
            continue
        a, b = edge.get("source"), edge.get("target")
        relation, reason = edge.get("relation"), text(edge.get("reason"), 3000)
        if (not isinstance(a, str) or not isinstance(b, str) or a == b
                or a not in selected or b not in selected
                or not isinstance(relation, str) or relation not in RELATIONS
                or not reason or frozenset((a, b)) in pairs):
            rejected += 1
            continue
        ya, yb = str(selected[a].get("year", "")), str(selected[b].get("year", ""))
        if ya.isdigit() and yb.isdigit() and int(ya) > int(yb):
            rejected += 1
            continue
        quotes = edge.get("evidence", [])
        evidence = []
        for quote in quotes[:4] if isinstance(quotes, list) else []:
            if not isinstance(quote, dict):
                continue
            pid, excerpt = quote.get("paperId"), text(quote.get("quote"), 2400)
            if pid not in (a, b) or len(excerpt) < 12:
                continue
            item = grounded_excerpt(selected[pid], excerpt, quote.get("page"))
            if item:
                evidence.append(item)
        if relation == "cites":
            evidence = citation_evidence(selected[a], selected[b], evidence)
        if not evidence:
            rejected += 1
            continue
        reachable, pending = set(), [b]
        while pending:
            current = pending.pop()
            if current not in reachable:
                reachable.add(current)
                pending.extend(successors.get(current, ()))
        if a in reachable:
            # New developmental links must not create circular progression.
            # User-locked cycles are handled separately and remain untouched.
            rejected += 1
            continue
        explicit = edge.get("certainty") == "explicit" and any(
            e["paperId"] == b and (e["page"] is not None or e["locator"].endswith("#fulltext")) for e in evidence)
        relations.append({"source": a, "target": b, "relation": relation,
                          "certainty": "explicit" if explicit else "inferred", "reason": reason, "evidence": evidence})
        pairs.add(frozenset((a, b)))
        successors.setdefault(a, set()).add(b)
    return selected, relations, rejected


def tree_nodes(board, catalog, selected):
    """Create editable topic/branch labels without replacing user-authored nodes."""
    proposed = deepcopy(board)
    existing_ids = {n["paperId"] for n in board["nodes"] if n["kind"] == "paper"}
    for pid, paper in selected.items():
        if pid not in existing_ids:
            proposed["nodes"].append({"id": str(uuid4()), "kind": "paper", "x": 80, "y": 80,
                                      "width": 280, "height": 180, "paperId": pid,
                                      "groupId": None, "text": "", "locked": False,
                                      "roadmapRole": "milestone" if paper.get("milestone") else None})
    node_map = {n["id"]: n for n in proposed["nodes"]}
    for node in proposed["nodes"]:
        paper = selected.get(node.get("paperId"), {})
        group = node_map.get(node.get("groupId"), {})
        if "milestone" in paper and not node["locked"] and not group.get("locked"):
            node["roadmapRole"] = "milestone" if paper["milestone"] else None

    def label(role, value):
        key = str(uuid5(NAMESPACE_URL, board["id"] + ":" + role + ":" + normalized(value)))
        if key in node_map:
            key = str(uuid4())
        item = {"id": key, "kind": "text", "roadmapRole": role, "x": 80, "y": 80,
                "width": 320 if role == "topic" else 280, "height": 90,
                "paperId": None, "groupId": None, "text": value, "locked": False}
        proposed["nodes"].append(item)
        node_map[key] = item
        return item

    topic = next((node for node in proposed["nodes"] if node.get("roadmapRole") == "topic"), None)
    topic = topic or label("topic", board["name"])
    papers = dict(catalog, **selected)
    cards = [node for node in proposed["nodes"] if node["kind"] == "paper"]
    branches, used = {}, set()
    names = list(dict.fromkeys(papers.get(node["paperId"], {}).get("branch") or "其他" for node in cards))
    normalized_names = {normalized(name) for name in names}
    for name in names:
        candidates = [node for node in proposed["nodes"] if node.get("roadmapRole") == "branch" and node["id"] not in used
                      and (normalized(node["text"]) == normalized(name) or normalized(node["text"]) not in normalized_names)]
        match = next((node for node in candidates if normalized(node["text"]) == normalized(name)), None)
        if match is None:
            # A human may have renamed the branch label. Its saved membership
            # identifies the branch without overwriting that edited text.
            member_ids = {node["id"] for node in cards if (papers.get(node["paperId"], {}).get("branch") or "其他") == name
                          and (catalog.get(node["paperId"], {}).get("branch") or "其他") == name}
            match = next((node for node in candidates if any(
                edge.get("roadmapStructure") and edge["source"] == node["id"] and edge["target"] in member_ids
                for edge in board["edges"])), None)
        match = match or label("branch", name)
        branches[name] = match["id"]
        used.add(match["id"])
    return proposed, papers, topic["id"], branches


def instantiate_tree(board, papers, relations, topic_id, branches):
    """Keep organizational links distinct from evidence-backed paper evolution."""
    nodes = {node["id"]: node for node in board["nodes"]}
    by_paper = {}
    for node in board["nodes"]:
        if node["kind"] == "paper":
            by_paper.setdefault(node["paperId"], []).append(node)
    edges = [deepcopy(edge) for edge in board["edges"] if edge["locked"]]
    seen = {frozenset((nodes[edge["source"]]["paperId"], nodes[edge["target"]]["paperId"]))
            for edge in edges if not edge.get("roadmapStructure")}
    for relation in relations:
        pair = frozenset((relation["source"], relation["target"]))
        if pair in seen:
            continue
        source, target = min(((a, b) for a in by_paper[relation["source"]] for b in by_paper[relation["target"]]),
                             key=lambda pair: (pair[0].get("groupId") != pair[1].get("groupId"), pair[0]["id"], pair[1]["id"]))
        edges.append({"id": str(uuid4()), "source": source["id"], "target": target["id"],
                      "label": RELATIONS[relation["relation"]], "locked": False, "roadmapStructure": False,
                      "analysis": {key: value for key, value in relation.items() if key not in ("source", "target")}})
        seen.add(pair)
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    edge_ids = {edge["id"] for edge in edges}

    def connect(source, target, label):
        if (source, target) not in pairs:
            key = str(uuid5(NAMESPACE_URL, board["id"] + ":structure:" + source + ":" + target))
            if key in edge_ids:
                key = str(uuid4())
            edges.append({"id": key,
                          "source": source, "target": target, "label": label, "locked": False,
                          "analysis": None, "roadmapStructure": True})
            pairs.add((source, target))
            edge_ids.add(key)

    for branch in (node for node in board["nodes"] if node.get("roadmapRole") == "branch"):
        connect(topic_id, branch["id"], "研究分支")
    for name, key in branches.items():
        members = {node["id"] for node in board["nodes"] if node["kind"] == "paper"
                   and (papers.get(node["paperId"], {}).get("branch") or "其他") == name}
        successors = {member: set() for member in members}
        incoming = set()
        for edge in edges:
            if edge["source"] in members and edge["target"] in members:
                successors[edge["source"]].add(edge["target"])
                incoming.add(edge["target"])
        reached = set()

        def visit(start, reached, successors):
            stack = [start]
            while stack:
                current = stack.pop()
                if current not in reached:
                    reached.add(current)
                    stack.extend(successors[current])

        roots = sorted(members - incoming)
        for member in roots:
            connect(key, member, "代表论文")
            visit(member, reached, successors)
        # Locked cycles are preserved. Attach each remaining component once so
        # every paper remains reachable without fabricating a scholarly chain.
        for member in sorted(members):
            if member not in reached:
                connect(key, member, "代表论文")
                visit(member, reached, successors)
    return edges


def proposal_for(board, heights, catalog, raw, warnings):
    from routers.domains import Board

    selected, relations, rejected = select_and_ground(raw, catalog)
    proposed, all_papers, topic_id, branches = tree_nodes(board, catalog, selected)
    proposed["edges"] = instantiate_tree(proposed, all_papers, relations, topic_id, branches)
    proposed = arrange_tree(proposed, all_papers, heights, topic_id, branches)
    proposed = Board.model_validate(proposed).model_dump()
    linked = {n["paperId"] for n in proposed["nodes"] if n["kind"] == "paper"
              and any(not e["roadmapStructure"] and n["id"] in (e["source"], e["target"]) for e in proposed["edges"])}
    notes = [{"paperId": "", "message": text(message, 1500)} for message in warnings[:40]]
    if len(selected) < 20:
        notes.append({"paperId": "", "message": f"本次仅筛选出 {len(selected)} 篇可核验论文，未为凑足数量补造条目。"})
    notes.append({"paperId": "", "message": "仅在有可定位全文引文时支持原文明示；其余发展关系为 AI 推断。全文阅读覆盖和筛选缺口见调研报告。"})
    notes.append({"paperId": "", "message": "主题和分支之间的组织连线仅用于导览，不表示论文引用或学术继承关系。"})
    result = {
        "board": proposed, "warnings": notes, "rejected": rejected,
        "unconnected": [{"paperId": p["id"], "title": p["title"]} for p in selected.values() if p["id"] not in linked],
        "added": sum(not e["locked"] and not e["roadmapStructure"] for e in proposed["edges"]),
        "removed": sum(not e["locked"] and not e.get("roadmapStructure") for e in board["edges"]),
        "protected": sum(e["locked"] for e in proposed["edges"]),
    }
    from services.roadmap_state import PaperMetadata

    return result, [{k: v for k, v in p.items() if k in PaperMetadata.model_fields} for p in selected.values()]


def generate(ctx, payload):
    run_id = payload["run_id"]
    try:
        check(run_id)
        with SessionLocal() as db:
            run = db.get(RoadmapRun, run_id)
            snapshot = deepcopy(run.snapshot)
            board = snapshot["board"]
            ids = {n["paperId"] for n in board["nodes"] if n["kind"] == "paper"}
            existing = [dict(p.data, id=p.id) for p in db.query(RoadmapPaper).filter(RoadmapPaper.id.in_(ids)).all()]
        from config import load_config, task_model_id

        from model_gateway.runtime import preflight_task_runtime
        from services.roadmap_fulltext import read_key_papers
        from services.roadmap_report import render_report
        from services.roadmap_sources import collect_candidates
        from services.wiki_compiler import _call_llm

        cfg = load_config()
        preflight_task_runtime(cfg, "roadmap_research")
        model = task_model_id(cfg, "roadmap_research")
        instructions = skill_instructions()

        def ask(phase, data, tokens):
            check(run_id)
            response = _call_llm(None, model, instructions + "\n当前阶段：" + phase,
                                 json.dumps(data, ensure_ascii=False), max_tokens=tokens,
                                 task_id="roadmap_research", timeout_s=600)
            check(run_id)
            return decode(response)

        ctx.phase("1/7 · 规划研究问题、范围与筛选标准")
        plan = ctx.step("research_plan", lambda: validate_plan(ask("plan", {
            "category": board["name"], "targetPapers": 25,
            "researchDate": datetime.now(timezone.utc).date().isoformat(),
            "existingPapers": [p["title"] for p in existing],
        }, 5000)))
        ctx.phase("2/7 · 多库检索并核验候选身份")
        def retrieve():
            result = collect_candidates(plan, board["name"], cancelled=lambda: check(run_id),
                                        progress=lambda label: ctx.phase("2/7 · " + label))
            result["accessedAt"] = datetime.now(timezone.utc).isoformat()
            return result

        resolved = ctx.step("verified_sources", retrieve)
        check(run_id)
        catalog = scope_catalog(board["id"], resolved["papers"], existing)
        if not catalog:
            raise ValueError("未找到可核验的论文来源，请稍后重试或缩小技术方向。")
        ctx.phase(f"3/7 · 去重、标题与摘要筛选 · {len(catalog)} 篇候选")
        screened = ctx.step("research_screen", lambda: ask("screen", {
            "category": board["name"], "plan": plan, "targetPapers": 25,
            "papers": list(catalog.values()),
        }, 8000))
        selected, key_ids, excluded, screen_rejected = screen_candidates(screened, catalog)
        ctx.phase(f"4/7 · 阅读与评估 {len(key_ids)} 篇关键论文全文")
        readings = ctx.step("key_paper_fulltext", lambda: read_key_papers(
            [selected[pid] for pid in key_ids], cancelled=lambda: check(run_id),
            progress=lambda label: ctx.phase("4/7 · " + label),
        ))
        evaluations, warnings = [], list(resolved.get("warnings", [])) + list(readings.get("warnings", []))
        for paper in readings["papers"]:
            check(run_id)
            selected[paper["id"]].update(paper)
            if paper.get("fullTextStatus") != "read":
                continue
            ctx.phase("4/7 · 评估关键论文：" + paper["title"][:80])
            try:
                evaluated = ctx.step("evaluate:" + paper["id"], lambda paper=paper: ground_evaluation(ask("evaluate", {
                    "researchQuestion": plan.get("researchQuestion"), "paper": paper,
                }, 4000), paper))
                evaluations.append(evaluated)
                if evaluated.get("error"):
                    warnings.append(paper["title"] + "：" + evaluated["error"])
                elif evaluated.get("include") is False:
                    selected.pop(paper["id"], None)
                    excluded.append({"id": paper["id"], "reason": "全文筛选：" + evaluated["screeningReason"]})
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - one failed evaluation must not discard verified candidates
                warnings.append(paper["title"] + "：全文评估失败（" + str(exc)[:300] + "），仅使用已核验元数据。")
        # Synthesis uses checked findings rather than re-sending every full PDF.
        if not selected:
            raise ValueError("全文评估后没有符合筛选标准的候选；原画布未修改。")
        synthesis_papers = [{k: v for k, v in p.items() if k not in ("fullText", "fullTextPages")} for p in selected.values()]
        ctx.phase("5/7 · 按主题综合里程碑与发展脉络")
        raw = ctx.step("research_development_tree_v3", lambda: ask("synthesize", {
            "category": board["name"], "targetPapers": 25,
            "plan": plan, "papers": synthesis_papers, "evaluations": evaluations,
            "researchCoverage": {
                "accessedAt": resolved.get("accessedAt"),
                "searchLog": resolved.get("searchLog", [])[:80],
                "screening": resolved.get("screening", {}),
                "candidateCount": len(catalog), "selectedCount": len(selected),
                "excluded": excluded[:60], "warnings": warnings[:40],
                "fullText": [{key: paper.get(key) for key in ("id", "fullTextStatus", "fullTextCoverage")}
                             for paper in readings["papers"]],
            },
        }, 20000))
        check(run_id)
        ctx.phase("6/7 · 复核来源身份、引文页码与连线证据")
        # The final selection cannot reintroduce a candidate excluded by screening.
        final_selected, final_relations, final_rejected = select_and_ground(raw, selected)
        accepted_raw = dict(raw, papers=[{key: p[key] for key in ("id", "reason", "branch", "milestone") if key in p}
                                        for p in final_selected.values()])
        proposal, papers = proposal_for(board, snapshot.get("heights", {}), dict(catalog, **selected), accepted_raw, warnings)
        proposal["rejected"] = screen_rejected + final_rejected
        if proposal["protected"]:
            proposal["warnings"].append({"paperId": "", "message": "已保留用户锁定连线；调研报告中的关系是本轮分析结果，锁定的画布关系仍以用户设置为准。"})
        ctx.phase("7/7 · 生成结构化 Markdown 调研报告")
        proposal["report"] = render_report(board["name"], plan, resolved, catalog, excluded,
                                            readings, evaluations, papers, final_relations,
                                            proposal["warnings"], proposal["rejected"],
                                            narrative=raw.get("narrative"), board=proposal["board"])
        persist_preview(run_id, proposal, papers)
        ctx.phase("路线图预览已就绪，请在识途应用或放弃")
        return {"run_id": run_id, "board_id": board["id"], "preview": True}
    except Cancelled:
        return {"cancelled": True}
    except Exception as exc:
        fail_run(run_id, str(exc)[:3000])
        raise
