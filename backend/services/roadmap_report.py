"""Deterministic, source-linked Markdown from recorded roadmap research stages.

This renderer does not fetch sources, evaluate science, or include extracted
full-text bodies. Missing dates, coverage and evidence remain explicit gaps.
"""

import html
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

_RELATIONS = {"builds_on": "基于", "improves": "改进", "inspired_by": "借鉴",
              "compares": "对比", "cites": "引用", "evolves_into": "演进",
              "branches_into": "分化", "enables": "突破推动", "unifies": "融合"}
_SCREENING = (
    ("suggestedSeeds", "进入核验的种子线索"), ("invalidSeeds", "无效种子线索"),
    ("verifiedSeeds", "身份核验通过的种子"), ("unverifiedSeeds", "未能核验的种子"),
    ("candidatesConsidered", "参与合并的候选记录"), ("duplicatesRemoved", "合并的重复记录"),
    ("excludedByLimit", "因候选数量上限未纳入"), ("candidateCount", "本轮来源解析后候选"),
)
_ASSESSMENTS = (("contribution", "核心贡献"), ("method", "方法"),
                ("evaluation", "实验与评估"), ("limitations", "局限"), ("relevance", "与研究问题的关联"))
_PARAGRAPH_SECTIONS = ("abstract", "methods", "problemOverview", "openProblems", "conclusion")
_INTRODUCTION = (("background", "背景"), ("scope", "研究范围"), ("problem", "研究问题"), ("significance", "研究意义"))
_GROUP_SECTIONS = ("developmentHistory", "currentBranches")


def _plain(value, limit=3000):
    if not isinstance(value, str):
        return ""
    clean = "".join(" " if char.isspace() else char for char in value
                    if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    clean = " ".join(clean.split())
    return clean[:limit] + ("…" if len(clean) > limit else "")


def _inline(value, limit=3000):
    """Keep untrusted prose inside a Markdown inline/table cell."""
    value = html.escape(_plain(value, limit), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", value)


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 2000:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password):
            return None
        if parsed.port == 0:  # Also validates malformed or out-of-range ports.
            return None
    except ValueError:
        return None
    # Encode brackets, parentheses and HTML delimiters so a URL cannot break out
    # of its Markdown destination. Existing percent-encoded identifiers survive.
    return quote(value, safe=":/?#@!$&'*+,;=%")


def _records(value):
    values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return [entry for entry in values if isinstance(entry, dict)]


def _index(value):
    return {entry["id"]: entry for entry in _records(value) if isinstance(entry.get("id"), str)}


def _count(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "未记录"


def _paper_name(paper, fallback="未登记论文"):
    return _plain(paper.get("title"), 1000) or fallback


def _paper_link(paper, fallback="未登记论文"):
    label = _inline(_paper_name(paper, fallback), 1000)
    url = _url(paper.get("sourceUrl"))
    return f"[{label}]({url})" if url else label + "（缺少可用来源链接）"


def _year(paper):
    value = paper.get("year", "")
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else _plain(value, 30)


def _paper_order(paper):
    year = _year(paper)
    return (int(year) if re.fullmatch(r"\d{4}", year) else 99999,
            _paper_name(paper).casefold(), _plain(paper.get("id"), 100))


def _access_date(resolved):
    value = resolved.get("accessedAt") or resolved.get("accessDate")
    if not isinstance(value, str):
        return "未记录（本次输入未提供来源访问时间）"
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is not None:
            date = date.astimezone(timezone.utc)
        return date.date().isoformat() + "（UTC；来自检索记录）"
    except ValueError:
        return "未记录（来源访问时间格式无效）"


def _table(lines, headers, rows):
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    lines.append("")


def _criteria(lines, label, values):
    lines.extend([f"### {label}", ""])
    values = [_inline(value, 1000) for value in values if _plain(value)] if isinstance(values, list) else []
    lines.extend(["- " + value for value in values] or ["未记录具体标准。"])
    lines.append("")


def _page_list(value):
    pages = sorted({page for page in value if isinstance(page, int) and not isinstance(page, bool) and page > 0}) if isinstance(value, list) else []
    if not pages:
        return ""
    # Compact consecutive page ranges while preserving a useful audit trail.
    ranges, start, end = [], pages[0], pages[0]
    for page in pages[1:]:
        if page == end + 1:
            end = page
        else:
            ranges.append(str(start) if start == end else f"{start}–{end}")
            start = end = page
    ranges.append(str(start) if start == end else f"{start}–{end}")
    return "、".join(ranges)


def _coverage(reading):
    coverage = reading.get("fullTextCoverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    source = {"arxiv_pdf": "arXiv PDF", "local_pdf": "论文库 PDF", "local_text": "论文库已有提取文本",
              "unavailable": "未取得全文"}.get(coverage.get("source"), "读取来源未记录")
    pages = f"已检查 {_count(coverage.get('pagesVisited'))} / 总计 {_count(coverage.get('totalPages'))} 页"
    text_pages = _page_list(coverage.get("pagesWithText"))
    if text_pages:
        pages += "；提取文字页码：" + text_pages
    limits = []
    total, visited = coverage.get("totalPages"), coverage.get("pagesVisited")
    complete_pages = (isinstance(total, int) and not isinstance(total, bool) and total > 0
                      and isinstance(visited, int) and not isinstance(visited, bool) and visited >= total
                      and not coverage.get("emptyPages") and not coverage.get("failedPages"))
    if coverage.get("truncated") is True:
        limits.append("内容已截断，不能视为完整全文阅读")
    elif coverage.get("complete") is True and complete_pages:
        limits.append("记录显示逐页文字提取完成；不代表图表解析或全文理解已完整")
    else:
        limits.append("完整性未确认")
    for key, label in (("emptyPages", "无文字页"), ("failedPages", "提取失败页")):
        values = _page_list(coverage.get(key))
        if values:
            limits.append(label + "：" + values)
    if _plain(coverage.get("note")):
        limits.append(_plain(coverage["note"], 800))
    if _plain(reading.get("fullTextError")):
        limits.append("读取说明：" + _plain(reading["fullTextError"], 1500))
    return source, pages, "；".join(limits)


def _evidence_rows(values, catalog, fallback_id=None):
    """Use catalog URLs only; an evidence locator can add a known page fragment."""
    result = []
    for item in _records(values)[:8]:
        pid = item.get("paperId") or fallback_id
        paper = catalog.get(pid) if isinstance(pid, str) else None
        excerpt = _plain(item.get("quote"), 800)
        if not paper or not excerpt:
            continue
        source_url = _url(paper.get("sourceUrl"))
        fulltext_url = _url(paper.get("fullTextSource"))
        page = item.get("page")
        page = page if isinstance(page, int) and not isinstance(page, bool) and 0 < page <= 10000 else None
        locator = _url(item.get("locator"))
        text_locator = False
        if locator:
            supplied = urlsplit(locator)
            text_locator = supplied.fragment == "fulltext" and any(
                supplied[:4] == urlsplit(url)[:4] for url in (source_url, fulltext_url) if url)
        base = (fulltext_url or source_url) if page is not None or text_locator else source_url
        if not base:
            continue
        parts = urlsplit(base)
        fragment, label = "", "来源元数据"
        if page is not None:
            fragment, label = f"page={page}", f"PDF 第 {page} 页"
        elif text_locator:
            fragment, label = "fulltext", "原文文本（无逐页定位）"
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, fragment))
        result.append((excerpt, f"{_paper_link(paper)} · [{label}]({url})"))
    return result


def _evidence(lines, evidence):
    for excerpt, locator in evidence:
        lines.extend(["> " + _inline(excerpt, 850), "", "证据位置：" + locator, ""])


def validate_narrative(narrative, finalpapers):
    """Return (whitelisted narrative, warnings), rejecting uncited claims.

    Paragraphs are ``{text: str, paperIds: [verified final paper id, ...]}``.
    Every cited id must resolve to a final paper with a safe source URL. Keeping
    only the valid subset would make a partly unsupported statement look valid,
    so an invalid citation rejects the entire paragraph. This checks provenance,
    not whether the cited source semantically entails a model's interpretation.
    """
    known = _index(finalpapers)
    warnings = []
    supplied = narrative if isinstance(narrative, dict) else {}

    def paragraphs(value, location):
        if value is None:
            return []
        if not isinstance(value, list):
            warnings.append(f"综述 {location} 不是段落列表，未采用。")
            return []
        accepted = []
        for item in value[:40]:
            text = _plain(item.get("text"), 6000) if isinstance(item, dict) else ""
            ids = item.get("paperIds") if isinstance(item, dict) else None
            if (not text or not isinstance(ids, list) or not ids
                    or any(not isinstance(pid, str) or pid not in known
                           or not _url(known[pid].get("sourceUrl")) for pid in ids)):
                warnings.append(f"综述 {location} 有段落缺少正文或已核验来源引用，未采用。")
                continue
            accepted.append({"text": text, "paperIds": list(dict.fromkeys(ids))})
        if len(value) > 40:
            warnings.append(f"综述 {location} 超过 40 段，超出部分未采用。")
        return accepted

    clean = {key: paragraphs(supplied.get(key), key) for key in _PARAGRAPH_SECTIONS}
    intro = supplied.get("introduction")
    intro = intro if isinstance(intro, dict) else {}
    clean["introduction"] = {key: paragraphs(intro.get(key), f"introduction.{key}") for key, _ in _INTRODUCTION}
    for key in _GROUP_SECTIONS:
        clean[key] = []
        groups = supplied.get(key, [])
        if not isinstance(groups, list):
            warnings.append(f"综述 {key} 不是分组列表，未采用。")
            continue
        for group in groups[:20]:
            if not isinstance(group, dict):
                continue
            title = _plain(group.get("title"), 200)
            content = paragraphs(group.get("paragraphs"), key)
            if title and content:
                clean[key].append({"title": title, "paragraphs": content})
    return clean, list(dict.fromkeys(warnings))


def _has_narrative(narrative):
    return (any(narrative[key] for key in _PARAGRAPH_SECTIONS + _GROUP_SECTIONS)
            or any(narrative["introduction"].values()))


def _fallback_narrative(final, readings, evaluations):
    """Summarize recorded material; do not invent cross-paper conclusions."""
    clean, _ = validate_narrative({}, final)
    reading_map = _index(readings.get("papers")) if isinstance(readings, dict) else {}
    assessment_map = _index(evaluations)
    branches = defaultdict(list)
    for paper in sorted(final.values(), key=_paper_order):
        pid = paper["id"]
        if not _url(paper.get("sourceUrl")):
            continue
        name = _paper_name(paper)
        assessment = assessment_map.get(pid, {})
        reading = reading_map.get(pid, {})
        grounded = (not assessment.get("error") and assessment.get("include") is not False
                    and reading.get("fullTextStatus") == "read"
                    and bool(_evidence_rows(assessment.get("evidence"), {pid: paper}, pid)))
        contribution = _plain(assessment.get("contribution"), 2000) if grounded else ""
        reason = _plain(paper.get("reason"), 2000)
        if contribution:
            clean["developmentHistory"].append({
                "title": (_year(paper) or "年份未记录") + " · " + name,
                "paragraphs": [{"text": f"该论文的已记录全文评估指出：{contribution}", "paperIds": [pid]}],
            })
        if reason:
            branches[_plain(paper.get("branch"), 200) or "未标注分支"].append({
                "text": f"《{name}》的登记入选理由：{reason}", "paperIds": [pid],
            })
        if grounded:
            for key, section, label in (("method", "methods", "已记录的方法"),
                                        ("limitations", "openProblems", "已记录的局限；是否仍属开放问题需进一步核验")):
                value = _plain(assessment.get(key), 2000)
                if value:
                    clean[section].append({"text": f"《{name}》{label}：{value}", "paperIds": [pid]})
    clean["currentBranches"] = [{"title": title, "paragraphs": rows} for title, rows in branches.items()]
    return clean


def _mermaid_label(value):
    # Labels never become Mermaid syntax: no quotes, HTML, entities, fences or
    # control characters survive. Node IDs are generated locally as n0, n1, ...
    replacements = {'"': '＂', "'": "＇", "`": "｀", "<": "＜", ">": "＞", "&": "＆",
                    "[": "［", "]": "］", "{": "｛", "}": "｝", "|": "｜", "\\": "＼",
                    "#": "＃", "%": "％"}
    return _plain(value, 160).translate(str.maketrans(replacements))


def _development_tree(board, known):
    """Render only actual validated board membership and edges, never a guess."""
    if not isinstance(board, dict):
        return []
    nodes = {}
    for node in _records(board.get("nodes")):
        nid, kind, role = node.get("id"), node.get("kind"), node.get("roadmapRole")
        if not isinstance(nid, str) or nid in nodes:
            continue
        if kind == "paper" and isinstance(node.get("paperId"), str):
            paper = known.get(node["paperId"])
            label = ((_year(paper) or "年份未记录") + " · " + _paper_name(paper) if paper
                     else "既有论文 · " + _plain(node["paperId"], 24))
            if role == "milestone":
                label = "★ " + label
        elif kind == "text" and role in {"topic", "branch"}:
            label = _plain(node.get("text"), 200)
        else:
            continue
        if label:
            nodes[nid] = {"kind": kind, "role": role, "label": label}
    if not nodes:
        return []
    ids = {nid: f"n{index}" for index, nid in enumerate(nodes)}
    lines = ["```mermaid", "flowchart TD"]
    for nid, node in nodes.items():
        label = _mermaid_label(node["label"])
        if node["role"] == "topic":
            lines.append(f'  {ids[nid]}("{label}")')
        elif node["role"] == "branch":
            lines.append(f'  {ids[nid]}{{"{label}"}}')
        else:
            lines.append(f'  {ids[nid]}["{label}"]')
    seen = set()
    for edge in _records(board.get("edges")):
        source, target = edge.get("source"), edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in nodes or target not in nodes:
            continue
        first, second = nodes[source], nodes[target]
        structural = edge.get("roadmapStructure") is True
        if structural:
            valid = ((first["role"] == "topic" and second["role"] == "branch")
                     or (first["role"] == "branch" and second["kind"] == "paper"))
            if not valid:
                continue
            connector, label = "-.->", "研究分支" if second["role"] == "branch" else "代表论文"
        elif first["kind"] == "paper" and second["kind"] == "paper":
            analysis = edge.get("analysis") if isinstance(edge.get("analysis"), dict) else {}
            kind = _RELATIONS.get(analysis.get("relation"))
            label = (("推断：" if analysis.get("certainty") != "explicit" else "") + kind
                     if kind else "用户连线：" + (_plain(edge.get("label"), 100) or "未标注"))
            connector = "-->"
        else:
            continue
        row = f'  {ids[source]} {connector}|"{_mermaid_label(label)}"| {ids[target]}'
        if row not in seen:
            lines.append(row)
            seen.add(row)
    return lines + ["```", ""]


def _verified_figures(readings, final):
    figures, seen = [], set()
    reading_map = _index(readings.get("papers")) if isinstance(readings, dict) else {}
    for pid, reading in reading_map.items():
        if pid not in final:
            continue
        accepted = 0
        for figure in _records(reading.get("figures")):
            asset = figure.get("assetId")
            if (accepted >= 2 or not isinstance(asset, str) or not re.fullmatch(r"[a-f0-9]{64}", asset)
                    or figure.get("imageUrl") != f"/api/roadmaps/report-assets/{asset}.png"
                    or figure.get("paperId") != pid or asset in seen or figure.get("kind") not in {"figure", "page"}
                    or not _url(figure.get("sourceUrl"))):
                continue
            figures.append({key: figure.get(key) for key in (
                "assetId", "imageUrl", "paperId", "figureLabel", "caption", "sourceUrl", "page", "kind")})
            seen.add(asset)
            accepted += 1
    return figures


def _figure_rows(lines, figures, final, selected_ids, used):
    for figure in figures:
        pid, asset = figure["paperId"], figure["assetId"]
        if pid not in selected_ids or asset in used:
            continue
        used.add(asset)
        kind = "原文图" if figure["kind"] == "figure" else "PDF 整页图（包含图示，并非裁剪的原图）"
        label = _plain(figure.get("figureLabel"), 100) or "图号未记录"
        page = figure.get("page")
        page = page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None
        location = f"PDF 第 {page} 页" if page else "页码未记录"
        source = _url(figure["sourceUrl"])
        if page:
            parts = urlsplit(source)
            source = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, f"page={page}"))
        lines.extend([f"![{_inline(label + ' · ' + kind)}]({figure['imageUrl']})", "",
                      f"{kind} · {_paper_link(final[pid])} · {_inline(label)} · [{location}]({source})", ""])
        caption = _inline(figure.get("caption"), 1500)
        if caption:
            lines.extend(["原图说明：" + caption, ""])


def render_report(category, plan, resolved, catalog, excluded, readings, evaluations, papers, relations, warnings, rejected,
                  narrative=None, board=None):
    """Render a cited literature review with its seven-stage evidence appendix.

    Narrative paragraphs carry ``text`` and nonempty ``paperIds``; only final
    verified papers may be cited. Optional legacy calls use recorded assessments
    and inclusion reasons as explicitly limited source summaries. Access dates
    come from ``resolved.accessedAt`` / ``accessDate``, never a wall clock. The
    only image URLs accepted are hash-matched, same-origin report assets.
    """
    final = _index(papers)
    graph_papers = dict(_index(catalog), **final)
    plan = plan if isinstance(plan, dict) else {}
    resolved = resolved if isinstance(resolved, dict) else {}
    clean, narrative_warnings = validate_narrative(narrative, final)
    fallback = not _has_narrative(clean)
    if fallback:
        clean = _fallback_narrative(final, readings, evaluations)
    ordered = sorted(final.values(), key=_paper_order)
    numbers = {paper["id"]: index + 1 for index, paper in enumerate(ordered)}
    figures, used_figures = _verified_figures(readings, final), set()
    lines = [f"# 识途文献综述：{_inline(category, 200) or '未指定方向'}", "",
             "来源访问日期：" + _access_date(resolved), "",
             "本报告用于组织技术文献阅读，不构成系统综述。并非所有论文均已读取全文；关键论文的阅读覆盖、证据与失败记录见附录 A4。", ""]
    if fallback:
        lines.extend(["本轮未生成通过来源校验的独立综述正文。下文可用内容仅整理自已登记入选理由和有证据的全文评估；尚缺跨论文综合分析，未补写新的技术结论。", ""])

    def paragraphs(values):
        if not values:
            lines.extend(["本节尚缺有来源支持的综述内容。", ""])
            return set()
        cited = set()
        for paragraph in values:
            links = []
            for pid in paragraph["paperIds"]:
                cited.add(pid)
                links.append(f"[{numbers[pid]}]({_url(final[pid]['sourceUrl'])})")
            lines.extend([_inline(paragraph["text"], 6100) + " " + "、".join(links), ""])
        return cited

    def groups(values):
        if not values:
            paragraphs([])
        for group in values:
            lines.extend(["### " + _inline(group["title"], 250), ""])
            cited = paragraphs(group["paragraphs"])
            _figure_rows(lines, figures, final, cited, used_figures)

    lines.extend(["## 1. 摘要", ""])
    paragraphs(clean["abstract"])
    lines.extend(["## 2. 引言", ""])
    for key, label in _INTRODUCTION:
        lines.extend(["### " + label, ""])
        if key in {"scope", "problem"}:
            recorded = _inline(plan.get("scope" if key == "scope" else "researchQuestion"), 2000)
            if recorded:
                lines.extend(["本次调研设定：" + recorded, ""])
        paragraphs(clean["introduction"][key])
    for heading, key in (("3. 研究方法", "methods"), ("4. 问题概述", "problemOverview")):
        lines.extend(["## " + heading, ""])
        paragraphs(clean[key])
    lines.extend(["## 5. 发展历史与范式里程碑", ""])
    groups(clean["developmentHistory"])
    lines.extend(["### 发展树", "",
                  "此图由最终可编辑画布生成，包含保留的既有卡片；正文分析针对本轮入选论文。圆角节点为主题，菱形为分支，矩形为论文；★ 标记画布中的里程碑论文。虚线表示主题、分支与代表论文的组织归属；实线保留画布中的论文关系，推断关系已标注。组织归属与年份本身不证明学术继承。", ""])
    tree = _development_tree(board, graph_papers)
    lines.extend(tree or ["未提供可用的最终画布，未依据年份自动补造发展树。", ""])
    lines.extend(["## 6. 当前研究分支", ""])
    groups(clean["currentBranches"])
    for heading, key in (("7. 开放问题", "openProblems"), ("8. 结论", "conclusion")):
        lines.extend(["## " + heading, ""])
        paragraphs(clean[key])
    lines.extend(["## 9. 参考文献", ""])
    for paper in ordered:
        authors = "、".join(_plain(author, 200) for author in paper.get("authors", []) if isinstance(author, str))
        lines.extend([(f"{numbers[paper['id']]}. {_paper_link(paper)}。{_inline(authors) or '作者未记录'}。"
                       f"{_inline(_year(paper)) or '年份未记录'}。"), ""])
    if not ordered:
        lines.extend(["本次没有可列出的最终入选文献。", ""])
    audit_warnings = _records(warnings) + [{"paperId": "", "message": message} for message in narrative_warnings]
    lines.append(_render_audit(category, plan, resolved, catalog, excluded, readings, evaluations,
                               papers, relations, audit_warnings, rejected))
    remaining = [figure for figure in figures if figure["assetId"] not in used_figures]
    if remaining:
        lines.extend(["## 附录 B. 关键论文原图与架构图", ""])
        _figure_rows(lines, remaining, final, set(final), used_figures)
    return "\n".join(lines).rstrip() + "\n"


def _render_audit(category, plan, resolved, catalog, excluded, readings, evaluations, papers, relations, warnings, rejected):
    """Retain the seven-stage audit separately from the literature narrative."""
    plan = plan if isinstance(plan, dict) else {}
    resolved = resolved if isinstance(resolved, dict) else {}
    readings = readings if isinstance(readings, dict) else {}
    known = _index(catalog)
    final = _index(papers)
    for pid, paper in final.items():
        known[pid] = dict(known.get(pid, {}), **paper)
    ordered = sorted(final.values(), key=_paper_order)
    reading_map = _index(readings.get("papers"))
    for pid, reading in reading_map.items():
        fulltext_source = _url(reading.get("fullTextSource"))
        if pid in known and fulltext_source:
            # Full text is stripped from catalog/final metadata before reporting.
            # Carry only its validated locator, never extracted text or pages.
            known[pid] = dict(known[pid], fullTextSource=fulltext_source)
    assessment_map = _index(evaluations)
    lines = ["## 附录 A. 检索、筛选与关键论文全文记录", "",
             "### A1. 研究问题、范围与纳排标准", "",
             "**研究问题**：" + (_inline(plan.get("researchQuestion"), 1500) or "未记录"), "",
             "**范围**：" + (_inline(plan.get("scope"), 2000) or "未记录"), ""]
    _criteria(lines, "纳入标准", plan.get("inclusionCriteria"))
    _criteria(lines, "排除标准", plan.get("exclusionCriteria"))

    lines.extend(["### A2. 多库检索记录与去重统计", "",
                  "下表区分实际请求、缓存结果、失败与跳过；命中数为接口结果数，尚不代表种子身份核验通过。", ""])
    search_log = _records(resolved.get("searchLog"))
    if search_log:
        rows = []
        for entry in search_log:
            source = {"arxiv": "arXiv", "crossref": "Crossref"}.get(entry.get("source"), _plain(entry.get("source")) or "未记录")
            kind = {"identifier": "标识符", "title": "标题", "keywords": "关键词"}.get(entry.get("queryType"), "未记录")
            state = {"ok": "成功", "failed": "失败", "skipped": "本轮跳过"}.get(entry.get("status"), "未记录")
            note = ("缓存结果；" if entry.get("cached") is True else "") + _plain(entry.get("error"), 1000)
            rows.append([_inline(source), _inline(kind + "：" + _plain(entry.get("query"), 1200), 1300), state,
                         _count(entry.get("matchedCount")), _count(entry.get("attempts")), _inline(note) or "—"])
        _table(lines, ["来源", "检索方式与检索词", "状态", "命中数", "网络尝试次数", "说明"], rows)
    else:
        lines.extend(["未提供检索日志，无法据此确认本次多库检索的覆盖范围。", ""])
    screening = resolved.get("screening") if isinstance(resolved.get("screening"), dict) else {}
    _table(lines, ["元数据核验与去重指标", "数量"], [[label, _count(screening.get(key))] for key, label in _SCREENING])
    lines.extend(["以上统计仅覆盖来源身份核验与候选合并，不代表全文筛选或研究质量评估。", "",
                  "### A3. 标题、摘要与全文筛选记录", "",
                  f"候选目录包含 {len(known)} 篇；最终路线图入选 {len(final)} 篇。以下排除理由来自本次筛选记录。", ""])
    excluded = _records(excluded)
    if excluded:
        _table(lines, ["论文", "筛选阶段", "排除理由"], [
            [_paper_link(known.get(item.get("id"), {}), "未登记候选 " + _plain(item.get("id"), 100)),
             "全文复筛" if _plain(item.get("reason")).startswith("全文筛选") else "标题与摘要筛选",
             _inline(item.get("reason"), 1500) or "未记录"] for item in excluded])
    else:
        lines.extend(["未提供逐篇排除理由。", ""])
    unexplained = set(known) - set(final) - {item.get("id") for item in excluded if isinstance(item.get("id"), str)}
    if unexplained:
        lines.extend([f"另有 {len(unexplained)} 篇候选未进入最终清单，记录中未给出逐篇排除理由。", ""])
    lines.extend(["本节不代表逐篇完成全文筛选；全文读取范围单独记录如下。", "",
                  "### A4. 关键论文全文覆盖与评估", ""])
    if reading_map:
        readable = sum(p.get("fullTextStatus") == "read" for p in reading_map.values())
        lines.extend([f"记录了 {len(reading_map)} 篇关键论文的读取尝试，其中 {readable} 篇取得可用文本。", ""])
        rows = []
        for pid, reading in reading_map.items():
            source, pages, limits = _coverage(reading)
            status = "已读取可用文本" if reading.get("fullTextStatus") == "read" else "全文不可用或读取失败"
            rows.append([_paper_link(known.get(pid, {}), "未登记论文 " + pid), status,
                         _inline(source), _inline(pages), _inline(limits, 2500)])
        _table(lines, ["论文", "读取状态", "读取来源", "页数覆盖", "限制与失败说明"], rows)
    else:
        lines.extend(["未提供关键论文全文读取记录，不能声称本轮完成了全文阅读。", ""])
    unread = [paper for paper in ordered if paper["id"] not in reading_map]
    if unread:
        lines.extend([f"{len(unread)} 篇入选论文未记录全文读取尝试：" + "；".join(_paper_link(p) for p in unread) + "。", ""])
    if not assessment_map:
        lines.extend(["未提供有定位证据的全文评估结果。", ""])
    for pid, assessment in assessment_map.items():
        paper = known.get(pid, {})
        lines.extend(["### " + _inline(_paper_name(paper, "未登记论文 " + pid)), ""])
        error = _plain(assessment.get("error"), 1500)
        evidence = _evidence_rows(assessment.get("evidence"), known, pid)
        if error:
            lines.extend(["评估未采用：" + _inline(error), ""])
        elif reading_map.get(pid, {}).get("fullTextStatus") != "read":
            lines.extend(["未提供对应的可用全文读取记录，本条不作为全文评估结论展示。", ""])
        elif not evidence:
            lines.extend(["未提供可定位来源的证据，本条不作为全文评估结论展示。", ""])
        else:
            if assessment.get("include") is False:
                lines.extend(["**全文筛选决定**：排除。", "",
                              "**排除理由**：" + (_inline(assessment.get("screeningReason"), 1500) or "未记录"), ""])
            elif assessment.get("include") is True:
                lines.extend(["**全文筛选决定**：保留进入后续综合；最终入选情况见附录 A5。", ""])
            for key, label in _ASSESSMENTS:
                lines.extend([f"**{label}**：" + (_inline(assessment.get(key), 2000) or "未记录"), ""])
            _evidence(lines, evidence)

    lines.extend(["### A5. 分支、里程碑与发展脉络依据", "",
                  "入选理由和关系解释来自调研记录；其证据强度以所列来源和定位为限。", ""])
    branches = defaultdict(list)
    for paper in ordered:
        branches[_plain(paper.get("branch"), 200) or "未标注分支"].append(paper)
    branch_order = sorted(branches, key=lambda branch: (_paper_order(branches[branch][0])[0], branch))
    if not ordered:
        lines.extend(["本次没有可展示的最终入选论文。", ""])
    for branch in branch_order:
        lines.extend(["### " + _inline(branch), ""])
        _table(lines, ["年份", "论文与来源", "入选理由"], [
            [_inline(_year(paper)) or "未记录", _paper_link(paper), _inline(paper.get("reason"), 2000) or "未记录"]
            for paper in branches[branch]])
    lines.extend(["### 连线及证据", ""])
    rendered_relations = 0
    for relation in _records(relations):
        source, target = relation.get("source"), relation.get("target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in known or target not in known:
            continue
        rendered_relations += 1
        kind = _RELATIONS.get(relation.get("relation"), _plain(relation.get("relation")) or "未标注关系")
        certainty = "原文支持（以所列证据为限）" if relation.get("certainty") == "explicit" else "推断"
        lines.extend([(f"{rendered_relations}. {_paper_link(known[source])} → {_paper_link(known[target])}"
                       f"；{_inline(kind)}；{certainty}"), "",
                      _inline(relation.get("reason"), 3000) or "未记录连线说明。", ""])
        evidence = _evidence_rows(relation.get("evidence"), {source: known[source], target: known[target]})
        if evidence:
            _evidence(lines, evidence)
        else:
            lines.extend(["未提供可定位证据；此报告不能补证该关系。", ""])
    if not rendered_relations:
        lines.extend(["未记录通过校验的连线；保留独立节点，未补造继承关系。", ""])

    lines.extend(["### A6. 来源身份与输出校验", "",
                  "下列标识来自候选目录和最终论文记录；报告生成不会额外进行网络核验。", ""])
    _table(lines, ["校验记录", "数量"], [
        ["本轮来源解析返回论文", str(len(_records(resolved.get("papers"))))],
        ["候选目录论文", str(len(known))], ["最终入选论文", str(len(final))],
        ["未通过结构或证据校验的模型条目", _count(rejected)],
    ])
    lines.extend(["模型条目剔除数可能包含论文与连线，不能作为文献排除总数。", ""])
    if ordered:
        _table(lines, ["论文与来源", "arXiv 标识", "DOI"], [
            [_paper_link(paper), _inline(paper.get("arxivId"), 100) or "未找到已核验 arXiv 版本",
             _inline(paper.get("doi"), 300) or "未登记 DOI"] for paper in ordered])

    lines.extend(["### A7. 缺口与建议阅读顺序", ""])
    messages = []
    for item in _records(warnings):
        message = _inline(item.get("message"), 2000)
        if not message:
            continue
        pid = item.get("paperId")
        label = _paper_link(known[pid]) + "：" if isinstance(pid, str) and pid in known else ""
        entry = label + message
        if entry not in messages:
            messages.append(entry)
    lines.extend(["- " + message for message in messages] or ["未提供额外缺口说明；这不代表研究覆盖完备。"])
    lines.extend(["", "### 建议阅读顺序", "",
                  "以下顺序只按分支和出版年份组织，年份未知的条目排在该分支末尾；顺序本身不证明方法继承关系。", ""])
    for branch in branch_order:
        sequence = " → ".join((_inline(_year(paper)) or "年份未记录") + " · " + _paper_link(paper)
                              for paper in branches[branch])
        lines.extend(["**" + _inline(branch) + "**：" + sequence, ""])
    if not branch_order:
        lines.extend(["没有足够的已登记论文生成阅读顺序。", ""])
    return "\n".join(lines).rstrip() + "\n"
