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
              "compares": "对比", "cites": "引用"}
_SCREENING = (
    ("suggestedSeeds", "进入核验的种子线索"), ("invalidSeeds", "无效种子线索"),
    ("verifiedSeeds", "身份核验通过的种子"), ("unverifiedSeeds", "未能核验的种子"),
    ("candidatesConsidered", "参与合并的候选记录"), ("duplicatesRemoved", "合并的重复记录"),
    ("excludedByLimit", "因候选数量上限未纳入"), ("candidateCount", "本轮来源解析后候选"),
)
_ASSESSMENTS = (("contribution", "核心贡献"), ("method", "方法"),
                ("evaluation", "实验与评估"), ("limitations", "局限"), ("relevance", "与研究问题的关联"))


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


def render_report(category, plan, resolved, catalog, excluded, readings, evaluations, papers, relations, warnings, rejected):
    """Render seven research stages without inventing claims or access dates.

    ``resolved.accessedAt`` (ISO timestamp) or ``accessDate`` supplies the source
    access date. If absent, it is explicitly unrecorded rather than today's date.
    Only whitelisted coverage/evaluation fields are rendered, never fullText or
    fullTextPages. The same inputs always produce the same Markdown string.
    """
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
    lines = [f"# 识途调研报告：{_inline(category, 200) or '未指定方向'}", "",
             "来源访问日期：" + _access_date(resolved), "",
             ("本报告依据已记录的检索、筛选与阅读结果整理，用于安排技术阅读路线，不构成系统综述。"
              "并非所有论文均已读取全文；具体覆盖与限制见第 4 节。"), "",
             "## 1. 研究问题、范围与纳排标准", "",
             "**研究问题**：" + (_inline(plan.get("researchQuestion"), 1500) or "未记录"), "",
             "**范围**：" + (_inline(plan.get("scope"), 2000) or "未记录"), ""]
    _criteria(lines, "纳入标准", plan.get("inclusionCriteria"))
    _criteria(lines, "排除标准", plan.get("exclusionCriteria"))

    lines.extend(["## 2. 多库检索记录与去重统计", "",
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
                  "## 3. 标题、摘要与全文筛选记录", "",
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
                  "## 4. 关键论文全文覆盖与评估", ""])
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
                lines.extend(["**全文筛选决定**：保留进入后续综合；最终入选情况见第 5 节。", ""])
            for key, label in _ASSESSMENTS:
                lines.extend([f"**{label}**：" + (_inline(assessment.get(key), 2000) or "未记录"), ""])
            _evidence(lines, evidence)

    lines.extend(["## 5. 分支、里程碑与发展脉络", "",
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

    lines.extend(["## 6. 来源身份与输出校验", "",
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

    lines.extend(["## 7. 缺口与建议阅读顺序", ""])
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
