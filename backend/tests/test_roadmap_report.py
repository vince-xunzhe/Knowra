"""Reports expose research gaps and evidence, without leaking extracted bodies."""

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.roadmap_report import render_report


def paper(pid="a", **changes):
    return {"id": pid, "title": "A Verified Milestone", "year": "2017", "authors": ["Alice"],
            "sourceUrl": "https://arxiv.org/abs/1706.03762", "arxivId": "1706.03762", "doi": None,
            "branch": "表征学习", "reason": "记录中的入选理由。", **changes}


def inputs():
    first = paper()
    second = paper("b", title="An Earlier Classic", year="1986", arxivId=None,
                   doi="10.1038/323533a0", sourceUrl="https://doi.org/10.1038/323533a0")
    return {
        "category": "世界模型", "plan": {
            "researchQuestion": "该方向如何发展？", "scope": "基础方法与主要分支",
            "inclusionCriteria": ["具有可核验来源", "覆盖关键技术分支"],
            "exclusionCriteria": ["无法核验身份"],
        },
        "resolved": {"papers": [first, second], "accessedAt": "2026-10-19T08:30:00Z",
                     "searchLog": [{"source": "arxiv", "query": "world models", "queryType": "keywords",
                                    "status": "failed", "matchedCount": 0, "attempts": 3,
                                    "cached": False, "error": "HTTP 429；本次暂停该来源"}],
                     "screening": {"suggestedSeeds": 3, "invalidSeeds": 0, "verifiedSeeds": 2,
                                   "unverifiedSeeds": 1, "candidatesConsidered": 3, "duplicatesRemoved": 1,
                                   "excludedByLimit": 0, "candidateCount": 2}},
        "catalog": {"a": first, "b": second}, "excluded": [],
        "readings": {"papers": [{**first, "fullTextStatus": "read", "fullText": "SECRET_FULLTEXT_BODY",
                                   "fullTextSource": "https://arxiv.org/pdf/1706.03762",
                                   "fullTextPages": [{"page": 1, "text": "SECRET_PAGE_TEXT"}],
                                   "fullTextCoverage": {"source": "arxiv_pdf", "totalPages": 10,
                                                        "pagesVisited": 3, "pagesWithText": [1, 3],
                                                        "emptyPages": [2], "failedPages": [],
                                                        "truncated": True, "complete": False}},
                                  {**second, "fullTextStatus": "unavailable", "fullText": "SECRET_FAILURE_TEXT",
                                   "fullTextError": "没有可用本地全文；arXiv 版本未找到",
                                   "fullTextCoverage": {"source": "unavailable", "complete": False}}]},
        "evaluations": [{"id": "a", "contribution": "可定位证据支持的贡献。", "method": "已记录的方法。",
                         "evaluation": "已记录的实验。", "limitations": "已记录的局限。", "relevance": "关联研究问题。",
                         "evidence": [{"quote": "This short passage is the supporting evidence.", "page": 3,
                                       "locator": "https://arxiv.org/pdf/1706.03762#page=3"}]}],
        "papers": [first, second], "relations": [],
        "warnings": [{"paperId": "b", "message": "全文不可用，仅使用元数据。"}], "rejected": 2,
    }


def test_seven_stages_failed_search_missing_fulltext_and_coverage_are_visible():
    report = render_report(**inputs())
    assert len(re.findall(r"^## [1-7]\. ", report, re.MULTILINE)) == 7
    for value in ("来源访问日期：2026-10-19", "具有可核验来源", "HTTP 429", "本轮来源解析后候选",
                  "没有可用本地全文", "内容已截断", "已检查 3 / 总计 10 页", "提取文字页码：1、3",
                  "无文字页：2", "不构成系统综述", "未登记 DOI", "未找到已核验 arXiv 版本"):
        assert value in report
    assert "不代表图表解析或全文理解已完整" not in report


def test_report_links_short_evidence_and_never_dumps_full_text_or_pages():
    report = render_report(**inputs())
    assert "This short passage is the supporting evidence" in report
    assert "[PDF 第 3 页](https://arxiv.org/pdf/1706.03762#page=3)" in report
    assert "https://arxiv.org/abs/1706.03762#page=" not in report
    assert "可定位证据支持的贡献" in report
    assert "SECRET_" not in report
    assert "fullTextPages" not in report and "fullText" not in report


def test_table_title_html_and_markdown_are_escaped_without_inventing_links():
    args = inputs()
    malicious = paper("evil", title='<script>alert(1)</script> | [click](javascript:bad)\n## injected',
                      branch="Branch | injected", reason="<img src=x onerror=alert(1)>",
                      sourceUrl="javascript:alert(1)")
    args["catalog"]["evil"] = malicious
    args["papers"].append(malicious)
    args["excluded"] = [{"id": "unknown", "reason": "<svg onload=alert(1)> | injected"}]
    args["warnings"].append({"paperId": "evil", "message": "[click](javascript:bad)"})
    report = render_report(**args)
    assert "<script" not in report and "<img" not in report and "<svg" not in report
    assert "&lt;script&gt;" in report
    assert "\\|" in report and "\\[click\\]\\(javascript:bad\\)" in report
    assert "\n## injected" not in report
    assert "](javascript:" not in report
    assert "缺少可用来源链接" in report


def test_unsafe_evidence_locator_cannot_replace_catalog_source():
    args = inputs()
    args["evaluations"][0]["evidence"][0]["locator"] = "https://evil.example/steal"
    args["relations"] = [{"source": "b", "target": "a", "relation": "builds_on", "certainty": "inferred",
                          "reason": "记录中的关系推断。",
                          "evidence": [{"paperId": "a", "quote": "A recorded passage supports this inference.",
                                        "page": 3, "locator": "javascript:alert(1)"}]}]
    report = render_report(**args)
    assert "evil.example" not in report and "javascript:" not in report
    assert "https://arxiv.org/pdf/1706.03762#page=3" in report
    assert "记录中的关系推断" in report and "；推断" in report


def test_metadata_evidence_uses_catalog_source_even_when_fulltext_is_available():
    args = inputs()
    args["evaluations"] = []
    args["relations"] = [{"source": "b", "target": "a", "relation": "cites", "certainty": "explicit",
                          "reason": "已核验来源中的引用元数据。",
                          "evidence": [{"paperId": "a", "quote": "Verified reference metadata.",
                                        "page": None, "locator": "https://arxiv.org/abs/1706.03762"}]}]
    report = render_report(**args)
    assert "[来源元数据](https://arxiv.org/abs/1706.03762)" in report
    assert "原文位置未记录" not in report
    assert "https://arxiv.org/pdf/1706.03762" not in report


def test_fulltext_evidence_without_page_uses_reading_locator_without_leaking_fields():
    args = inputs()
    args["evaluations"][0]["evidence"][0].update(
        page=None, locator="https://arxiv.org/pdf/1706.03762#fulltext")
    args["readings"]["papers"][0]["title"] = "READING_TITLE_MUST_NOT_REPLACE_METADATA"
    before = copy.deepcopy(args)
    report = render_report(**args)
    assert "[原文文本（无逐页定位）](https://arxiv.org/pdf/1706.03762#fulltext)" in report
    assert "READING_TITLE_MUST_NOT_REPLACE_METADATA" not in report and "SECRET_" not in report
    assert args == before


def test_unsafe_reading_fulltext_url_does_not_enter_evidence_links():
    args = inputs()
    args["readings"]["papers"][0]["fullTextSource"] = "javascript:alert(1)"
    report = render_report(**args)
    assert "](javascript:" not in report
    assert "https://arxiv.org/abs/1706.03762" in report


def test_url_parentheses_are_encoded_and_credentials_or_line_breaks_rejected():
    args = inputs()
    special = paper("special", title="Special DOI", arxivId=None, doi="10.1000/a(b)",
                    sourceUrl="https://doi.org/10.1000/a(b)")
    args["papers"] += [special, paper("credentials", sourceUrl="https://user:secret@example.com/paper"),
                       paper("newline", sourceUrl="https://example.com/paper\n[bad](javascript:bad)")]
    report = render_report(**args)
    assert "https://doi.org/10.1000/a%28b%29" in report
    assert "user:secret" not in report and "](javascript:" not in report


def test_missing_date_is_explicit_and_report_is_deterministic_without_mutating_inputs():
    args = inputs()
    del args["resolved"]["accessedAt"]
    before = copy.deepcopy(args)
    first = render_report(**args)
    second = render_report(**args)
    assert first == second and args == before
    assert "来源访问日期：未记录" in first


def test_empty_inputs_and_unrecorded_fulltext_do_not_claim_completed_research():
    args = inputs()
    args.update(plan={}, resolved={}, catalog={}, excluded=[], readings={}, evaluations=[],
                papers=[], relations=[], warnings=[], rejected=0)
    report = render_report(**args)
    assert "未提供检索日志" in report
    assert "未提供关键论文全文读取记录" in report
    assert "没有足够的已登记论文生成阅读顺序" in report
    assert "未记录具体标准" in report


def test_failed_or_unanchored_assessments_are_not_rendered_as_scientific_findings():
    args = inputs()
    args["evaluations"] = [{"id": "a", "contribution": "UNSUPPORTED_CLAIM", "evidence": []},
                           {"id": "b", "contribution": "FAILED_CLAIM", "error": "读取失败，评估未采用。",
                            "evidence": []}]
    report = render_report(**args)
    assert "UNSUPPORTED_CLAIM" not in report and "FAILED_CLAIM" not in report
    assert "未提供可定位来源的证据" in report and "评估未采用" in report


def test_reading_order_uses_branch_and_year_only_with_unknown_year_last():
    args = inputs()
    unknown = paper("unknown", title="A Paper of Unknown Date", year="", branch="表征学习")
    args["papers"].insert(0, unknown)
    report = render_report(**args)
    order = report.split("### 建议阅读顺序", 1)[1]
    assert order.index("An Earlier Classic") < order.index("A Verified Milestone") < order.index("A Paper of Unknown Date")
    assert "顺序本身不证明方法继承关系" in order
    assert "未记录全文读取尝试" in report


def test_excluded_candidates_and_missing_reasons_remain_auditable():
    args = inputs()
    args["catalog"]["c"] = paper("c", title="Excluded Example")
    args["catalog"]["d"] = paper("d", title="Unexplained Example")
    args["excluded"] = [{"id": "c", "reason": "与本次研究问题关联不足。"}]
    report = render_report(**args)
    assert "Excluded Example" in report and "与本次研究问题关联不足" in report
    assert "另有 1 篇候选未进入最终清单" in report
    assert "模型条目剔除数可能包含论文与连线" in report


def test_fulltext_screening_exclusions_remain_visible_outside_final_reading_route():
    args = inputs()
    args["evaluations"][0].update(include=False, screeningReason="全文评估发现研究目标不属于本次范围。")
    args["excluded"] = [{"id": "a", "reason": "全文筛选：全文评估发现研究目标不属于本次范围。"}]
    args["papers"] = [args["papers"][1]]
    report = render_report(**args)
    assert "全文复筛" in report and "**全文筛选决定**：排除" in report
    assert "全文评估发现研究目标不属于本次范围" in report
    assert "PDF 第 3 页" in report
    assert "A Verified Milestone" not in report.split("## 5.", 1)[1].split("## 6.", 1)[0]
    assert "A Verified Milestone" not in report.split("### 建议阅读顺序", 1)[1]


def test_complete_flag_without_consistent_page_coverage_never_claims_complete_extraction():
    args = inputs()
    args["readings"]["papers"][0]["fullTextCoverage"] = {"complete": True, "source": "local_text"}
    report = render_report(**args)
    assert "逐页文字提取完成" not in report
    assert "完整性未确认" in report
