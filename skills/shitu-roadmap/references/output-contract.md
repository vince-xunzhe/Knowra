# Runtime output contract

Return one JSON object without Markdown fences. The caller tells you which phase is active. All prose follows the user's configured language; publication titles and identifiers remain in their source form.

## plan

Input: `category`, `targetPapers`, existing paper titles.

```json
{
  "researchQuestion": "What foundational ideas and branches explain this field's development?",
  "scope": "Historical foundations through representative current directions",
  "inclusionCriteria": ["Primary work with a concrete contribution relevant to the question"],
  "exclusionCriteria": ["Off-topic work or duplicate versions of the same paper"],
  "queries": ["English field or branch keywords", "field survey"],
  "papers": [{"title": "Exact candidate title", "arxivId": null, "doi": null}]
}
```

Use up to 3 distinct queries and up to 35 seed titles across historical stages and subfields. Include an arXiv ID or DOI only when reasonably known; the resolver verifies it independently. Null identifiers are useful: title search can find and verify them. Do not restrict candidates to arXiv.

## screen

Input: the plan and verified candidate catalog. Apply the inclusion/exclusion criteria to titles and abstracts. Do not claim to have read full texts in this phase.

```json
{
  "papers": [{"id": "catalog ID", "reason": "Why this paper belongs", "branch": "Technical branch"}],
  "keyPaperIds": ["up to 8 selected IDs whose full texts should be evaluated"],
  "excluded": [{"id": "other catalog ID", "reason": "Specific exclusion reason"}]
}
```

Select approximately 20–30 papers, maximum 30, and usually 4–8 key works for full-text evaluation; when fewer candidates exist, use the actual available count, never invent additions. Cover foundational and branch-defining works rather than only papers easiest to download. Missing arXiv is not an exclusion criterion. Missing abstracts may justify provisional retention for full-text screening; clearly state that pending judgment in the reason rather than presenting an abstract review that never occurred.

## evaluate

Input: research question and one key paper with `fullText`, `fullTextPages`, `fullTextCoverage`, and metadata. Read the provided text and respect reported coverage limitations. The server skips this model phase when `fullTextStatus` is `unavailable`. A `read` status only means some text was extracted: `fullTextCoverage.complete`, page counts and truncation determine whether the whole text was available. The server preserves those coverage facts in the report; never override them or describe a partial reading as complete.

```json
{
  "include": true,
  "screeningReason": "Whether full-text findings still satisfy the inclusion criteria",
  "contribution": "Main contribution",
  "method": "Concrete method and its assumptions",
  "evaluation": "Experiments and what they establish",
  "limitations": "Stated limitations and cautiously separated analyst observations",
  "relevance": "Role in answering the research question",
  "evidence": [{"quote": "Continuous excerpt from the supplied full text", "page": 3}]
}
```

Use 1–8 supporting excerpts with exact provided page numbers; use null only when the supplied extracted full text has no known page mapping. Tie claims to these excerpts; do not invent quotations or generalize beyond the text actually available. Evaluation with no verifiable full-text evidence is rejected by the server.

Use `include: false` with a specific `screeningReason` when the actual text shows that a paper does not meet the research criteria. Lack of full text is recorded as a coverage gap, not proof that a classic is irrelevant.

## synthesize

Input: the plan, screened `papers` catalog, checked `evaluations` from key full texts, and `researchCoverage` (actual search log, screening counts, exclusions, warnings and per-paper reading coverage). Use these recorded facts for the methods section; an absent record is unknown, not a successful search. Only catalog IDs may be selected or connected. Sources may omit abstracts or full text; do not fill gaps with invented quotations. Use the evaluation excerpts with their page numbers when they support a relationship. An explicit development relationship requires a statement in the later/target paper; the earlier work's own description cannot establish how later work builds on it.

```json
{
  "papers": [{"id": "catalog ID", "reason": "Why this work is a useful milestone", "branch": "Technical branch", "milestone": true}],
  "edges": [{
    "source": "earlier catalog ID",
    "target": "later catalog ID",
    "relation": "evolves_into",
    "certainty": "inferred",
    "reason": "Concrete relationship and limits of the available evidence",
    "evidence": [{"paperId": "catalog ID", "quote": "Continuous source excerpt", "page": null}]
  }]
}
```

Select approximately 20–30 papers (maximum 30 selections per run, including existing papers), fewer if the catalog does not support that many relevant milestones. Existing canvas cards remain even when not selected in this run. Choose 3–6 meaningful branch labels where possible. Prefer development relations: `evolves_into`, `branches_into`, `enables`, `unifies`. Legacy `builds_on`, `improves`, `inspired_by`, `compares`, `cites` remain accepted, but a citation list is not the target product. Use `milestone: true` sparingly for paradigm-changing work and explain the concrete change in its reason. The server creates the category topic and branch nodes plus structural membership edges; do not invent pseudo-paper IDs for those nodes. Each edge has 1–4 excerpts from its two endpoints. The server reconstructs source URLs and validates quotations, page numbers and IDs. Use `explicit` only for relationships directly stated in a verified full-text excerpt; metadata-only relationships remain `inferred`. Do not output source URLs, coordinates, local-library IDs or invented verification flags.

For `cites`, the later/target paper must have the earlier/source paper's verified DOI or arXiv ID in its `references`, or its abstract/full text must explicitly mention the earlier paper's full title or identifier. Generic topic overlap does not establish a citation. When a verified reference matches, the server includes that reference identifier as evidence; otherwise quote the explicit mention or relevant full-text citation context. If neither is available, omit the citation edge.

Also include `narrative` in this same JSON object. A paragraph P is `{"text":"Connected prose explaining a technical point", "paperIds":["verified selected paper ID"]}`. Every technical paragraph requires nonempty source IDs, all from your final selected papers; unknown IDs invalidate the entire paragraph. Inline prose is plain text; the server adds numbered source links. Do not add raw HTML, Mermaid, image URLs, or local paths.

```json
{
  "narrative": {
    "abstract": [{"text":"Scope, major development stages and present challenges", "paperIds":["selected ID"]}],
    "introduction": {
      "background": [], "scope": [], "problem": [], "significance": []
    },
    "methods": [],
    "problemOverview": [],
    "developmentHistory": [{"title":"Stage and paradigm shift", "paragraphs":[]}],
    "currentBranches": [{"title":"A current technical branch", "paragraphs":[]}],
    "openProblems": [],
    "conclusion": []
  }
}
```

Each empty array above is a placeholder for 1-4 P paragraphs when supplied evidence supports them (up to 8 development stages / current branches). Prefer synthesis across papers over listing one abstract after another. Explain prior limitations, the new approach, changed assumptions, resulting capabilities and remaining limits. Cite the key work in the paragraph where its architecture figure belongs. Methods must describe the actual supplied research coverage and criteria, not invent a systematic-review protocol. Do not copy a source abstract wholesale into the review. When evidence is missing, use fewer paragraphs and state the specific gap; do not fill sections with generic claims.

The server validates identities, excerpts, page numbers, narrative source IDs and graph endpoints. It derives the Markdown development diagram from the validated canvas; source-ID validation does not prove a paragraph's scientific claims. Clearly mark your inference and limited full-text coverage. Real architecture figures, if supplied in `papers[].figures`, are server-created assets with `figureLabel`, `caption`, `page`, `sourceUrl`, `kind` (`figure` or whole PDF `page`) and `imageUrl`. Their captions support locating an illustration, not claims about its performance. Do not invent figures for papers without assets.

The reader-facing report is a narrative review. Seven-step retrieval logs, screening decisions, evidence checks and reading coverage remain in the audit appendix, and key-paper assessments remain in the full-text appendix. No separate free-form report call may introduce new papers or unsupported claims.
