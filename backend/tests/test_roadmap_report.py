"""Reports expose research gaps and evidence, without leaking extracted bodies."""

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.roadmap_report import render_report, validate_narrative


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
    assert len(re.findall(r"^### A[1-7]\. ", report, re.MULTILINE)) == 7
    for value in ("来源访问日期：2026-10-19", "具有可核验来源", "HTTP 429", "本轮来源解析后候选",
                  "没有可用本地全文", "内容已截断", "已检查 3 / 总计 10 页", "提取文字页码：1、3",
                  "无文字页：2", "不构成系统综述", "未登记 DOI", "未找到已核验 arXiv 版本"):
        assert value in report
    assert "不代表图表解析或全文理解已完整" not in report


def test_report_records_the_user_focus_keywords_in_summary_and_audit():
    args = inputs()
    args["plan"]["focusKeywords"] = ["3DGS", "4DGS"]
    report = render_report(**args)
    assert "本轮关注关键词：3DGS、4DGS" in report
    assert "**用户关注关键词**：3DGS、4DGS" in report


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


def narrative():
    def paragraph(text, ids=None):
        return {"text": text, "paperIds": ids or ["a"]}

    return {
        "abstract": [paragraph("从已有研究可以归纳出本文关注的研究线索。", ["a", "b"])],
        "introduction": {
            "background": [paragraph("已登记的技术背景。")],
            "scope": [paragraph("所讨论的方法范围。")],
            "problem": [paragraph("文献所描述的问题。")],
            "significance": [paragraph("来源中阐述的研究意义。")],
        },
        "methods": [paragraph("对文献中方法的有据综合。")],
        "problemOverview": [paragraph("问题与前提条件的综合描述。")],
        "developmentHistory": [{"title": "范式里程碑", "paragraphs": [paragraph("已经核验的历史分析。")]}],
        "currentBranches": [{"title": "表征学习", "paragraphs": [paragraph("已有分支的技术分析。")]}],
        "openProblems": [paragraph("文献支持的开放问题。")],
        "conclusion": [paragraph("在所列材料范围内得到的综合结论。")],
    }


def final_board():
    return {
        "nodes": [{"id": "topic", "kind": "text", "text": "世界模型", "roadmapRole": "topic"},
                  {"id": "branch", "kind": "text", "text": "表征学习", "roadmapRole": "branch"},
                  {"id": "pa", "kind": "paper", "paperId": "a", "roadmapRole": "milestone"},
                  {"id": "pb", "kind": "paper", "paperId": "b", "roadmapRole": None}],
        "edges": [{"id": "e1", "source": "topic", "target": "branch", "roadmapStructure": True},
                  {"id": "e2", "source": "branch", "target": "pa", "roadmapStructure": True},
                  {"id": "e3", "source": "pb", "target": "pa", "analysis": {
                      "relation": "evolves_into", "certainty": "inferred"}}],
    }


def figure(asset="a", **overrides):
    return {"assetId": asset * 64, "imageUrl": f"/api/roadmaps/report-assets/{asset * 64}.png",
            "paperId": "a", "figureLabel": "Figure 2", "caption": "Recorded architecture diagram.",
            "sourceUrl": "https://arxiv.org/pdf/1706.03762", "page": 3, "kind": "figure", **overrides}


def test_review_narrative_precedes_the_complete_audit_and_uses_numbered_verified_sources():
    args = inputs()
    args.update(narrative=narrative(), board=final_board())
    report = render_report(**args)
    assert len(re.findall(r"^## [1-9]\. ", report, re.MULTILINE)) == 9
    assert report.index("## 9. 参考文献") < report.index("## 附录 A.")
    assert "已经核验的历史分析" in report and "尚缺跨论文综合分析" not in report
    assert "[2](https://arxiv.org/abs/1706.03762)" in report
    assert "[1](https://doi.org/10.1038/323533a0)" in report
    assert "SECRET_" not in report and "HTTP 429" in report


def test_narrative_rejects_entire_unknown_or_missing_citation_paragraphs_and_invalid_urls():
    args = inputs()
    raw = narrative()
    raw["abstract"] = [{"text": "UNKNOWN_PAPER_CLAIM", "paperIds": ["a", "ghost"]},
                       {"text": "UNCITED_CLAIM", "paperIds": []},
                       {"text": "UNTYPED_CLAIM", "paperIds": "a"},
                       {"text": "Valid repeated citation.", "paperIds": ["a", "a"]}]
    raw["mermaid"] = "MODEL_SUPPLIED_MERMAID_MUST_NOT_BE_USED"
    before = copy.deepcopy(raw)
    clean, warnings = validate_narrative(raw, args["papers"])
    assert clean["abstract"] == [{"text": "Valid repeated citation.", "paperIds": ["a"]}]
    assert warnings and raw == before
    args["narrative"] = raw
    report = render_report(**args)
    for value in ("UNKNOWN_PAPER_CLAIM", "UNCITED_CLAIM", "UNTYPED_CLAIM", "MODEL_SUPPLIED_MERMAID"):
        assert value not in report
    assert "有段落缺少正文或已核验来源引用，未采用" in report
    clean, warnings = validate_narrative(raw, [paper(sourceUrl="javascript:alert(1)")])
    assert not clean["abstract"] and warnings


def test_old_outputs_use_only_recorded_grounded_material_without_inventing_review_claims():
    args = inputs()
    report = render_report(**args)
    main = report.split("## 附录 A.", 1)[0]
    assert "尚缺跨论文综合分析" in main and "本节尚缺有来源支持的综述内容" in main
    assert "可定位证据支持的贡献" in main and "登记入选理由" in main
    assert "是否仍属开放问题需进一步核验" in main
    args["evaluations"][0]["evidence"] = []
    main = render_report(**args).split("## 附录 A.", 1)[0]
    assert "可定位证据支持的贡献" not in main
    assert "未提供可用的最终画布" in main and "```mermaid" not in main


def test_mermaid_is_derived_from_real_board_with_structure_distinct_from_paper_evolution():
    args = inputs()
    args["board"] = final_board()
    report = render_report(**args)
    graph = report.split("```mermaid\n", 1)[1].split("```", 1)[0]
    assert graph.startswith("flowchart TD\n")
    assert 'n0("世界模型")' in graph and 'n1{"表征学习"}' in graph
    assert 'n0 -.->|"研究分支"| n1' in graph
    assert 'n1 -.->|"代表论文"| n2' in graph
    assert 'n3 -->|"推断：演进"| n2' in graph
    assert "n1 -.->" in graph and "n1 -->" not in graph
    assert "1986 · An Earlier Classic" in graph
    assert 'n2["★ 2017 · A Verified Milestone"]' in graph
    assert "★ 标记画布中的里程碑论文" in report


def test_mermaid_escapes_code_and_preserves_unknown_papers_without_inventing_their_titles():
    args = inputs()
    args["board"] = final_board()
    args["board"]["nodes"][0]["text"] = 'Topic "]\n```\nclick n0 "javascript:bad" <script> %%'
    args["board"]["nodes"].append({"id": "injected", "kind": "paper", "paperId": "ghost"})
    args["board"]["edges"] += [{"source": "injected", "target": "pa"},
                              {"source": "pa", "target": "branch", "roadmapStructure": True}]
    report = render_report(**args)
    graph = report.split("```mermaid\n", 1)[1].split("```", 1)[0]
    assert "<script>" not in graph and "%%" not in graph and '\nclick ' not in graph
    assert "injected" not in graph and "n2 -.->" not in graph
    assert 'n4["既有论文 · ghost"]' in graph
    assert "＂" in graph and "｀｀｀" in graph
    assert report.count("```mermaid") == 1


def test_tree_keeps_catalog_only_old_papers_and_edges_but_narrative_references_stay_final_only():
    args = inputs()
    args["catalog"]["old"] = paper("old", title="Retained Earlier Paper", year="2000")
    args["board"] = final_board()
    args["board"]["nodes"].append({"id": "old-node", "kind": "paper", "paperId": "old"})
    args["board"]["edges"].append({"source": "old-node", "target": "pa", "analysis": {
        "relation": "enables", "certainty": "explicit"}})
    args["narrative"] = narrative()
    args["narrative"]["conclusion"].append({"text": "OLD_PAPER_UNSELECTED_CLAIM", "paperIds": ["old"]})
    report = render_report(**args)
    graph = report.split("```mermaid\n", 1)[1].split("```", 1)[0]
    assert 'n4["2000 · Retained Earlier Paper"]' in graph
    assert 'n4 -->|"突破推动"| n2' in graph
    assert "包含保留的既有卡片；正文分析针对本轮入选论文" in report
    assert "OLD_PAPER_UNSELECTED_CLAIM" not in report
    references = report.split("## 9. 参考文献", 1)[1].split("## 附录 A.", 1)[0]
    assert "Retained Earlier Paper" not in references


def test_verified_figures_are_placed_by_cited_paper_deduplicated_and_provenance_is_visible():
    args = inputs()
    args["narrative"] = narrative()
    args["readings"]["papers"][0]["figures"] = [figure(), figure(), figure("b", kind="page", page=5)]
    report = render_report(**args)
    first_url = f"/api/roadmaps/report-assets/{'a' * 64}.png"
    assert report.count(first_url) == 1
    assert report.index(first_url) < report.index("## 6. 当前研究分支")
    assert "PDF 整页图（包含图示，并非裁剪的原图）" in report
    assert "[PDF 第 5 页](https://arxiv.org/pdf/1706.03762#page=5)" in report
    assert "原图说明：Recorded architecture diagram" in report
    assert "## 附录 B." not in report


def test_figures_require_matching_asset_hash_paper_and_safe_sources_and_unused_go_to_appendix():
    args = inputs()
    args["narrative"] = {"abstract": [{"text": "A sourced abstract.", "paperIds": ["a"]}]}
    args["readings"]["papers"][0]["figures"] = [
        figure("a", imageUrl="https://evil.example/tracker.png"),
        figure("b", imageUrl=f"/api/roadmaps/report-assets/{'c' * 64}.png"),
        figure("c", paperId="ghost"), figure("d", sourceUrl="javascript:alert(1)"),
        figure("e", caption="<img src=x onerror=alert(1)>"),
    ]
    report = render_report(**args)
    assert "evil.example" not in report and "<img" not in report
    assert "## 附录 B. 关键论文原图与架构图" in report
    assert report.count("![") == 1
    assert f"/api/roadmaps/report-assets/{'e' * 64}.png" in report
    assert "&lt;img" in report
