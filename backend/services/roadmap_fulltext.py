"""Read a bounded set of key papers without adopting or retaining downloaded PDFs."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from database import SessionLocal
from models import Paper, RoadmapPaper
from path_utils import resolve_paper_path
from sqlalchemy.orm import load_only

from services.roadmap_import import MAX_PDF_BYTES, canonical_arxiv_id, download_arxiv
from services.roadmap_state import library_index, library_match

MAX_KEY_PAPERS = 8
MAX_PAGES = 50
MAX_CHARACTERS = 50000
MAX_PAGE_CHARACTERS = 10000


class _CancelledRead(Exception):
    def __init__(self, original):
        self.original = original


def _clean(text):
    return "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in str(text or "")).strip()


def _coverage(source, total_pages=None):
    return {"source": source, "totalPages": total_pages, "pagesVisited": 0,
            "pagesWithText": [], "emptyPages": [], "failedPages": [],
            "truncated": False, "complete": False, "characters": 0, "note": ""}


def _local_materials(metadata):
    """Paths come only from a matching library row, never source/model metadata."""
    with SessionLocal() as db:
        catalog = db.get(RoadmapPaper, metadata.get("id")) if metadata.get("id") else None
        local = library_match(SimpleNamespace(
            data=metadata, library_paper_id=catalog.library_paper_id if catalog else None,
        ), library_index(db))
        if local is None:
            return None
        paper = db.query(Paper).options(load_only(
            Paper.id, Paper.filepath, Paper.extracted_text, Paper.num_pages,
        )).filter_by(id=local.id).one_or_none()
        if paper is None:
            return None
        return {"filepath": paper.filepath, "text": paper.extracted_text or "",
                "pages": paper.num_pages}


def _pdf_path(path):
    candidate = Path(path)
    if candidate.suffix.lower() != ".pdf" or candidate.is_symlink() or not candidate.is_file():
        raise ValueError("论文文件不是可读取的常规 PDF")
    if not 5 < candidate.stat().st_size <= MAX_PDF_BYTES:
        raise ValueError("PDF 为空或超过 80 MB 读取限制")
    with candidate.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError("论文文件内容不是 PDF")
    return candidate


def _read_pdf(path, source, check):
    from pypdf import PdfReader

    check()
    safe_path = _pdf_path(path)
    reader = PdfReader(str(safe_path))
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，未尝试破解或绕过访问限制")
    total = len(reader.pages)
    coverage = _coverage(source, total)
    if not total:
        raise ValueError("PDF 不含可读取页面")
    parts, pages, length = [], [], 0
    for index in range(min(total, MAX_PAGES)):
        check()
        marker = f"[PDF 第 {index + 1} 页]\n"
        available = MAX_CHARACTERS - length - len(marker) - 2
        if available <= 0:
            coverage["truncated"] = True
            break
        coverage["pagesVisited"] += 1
        try:
            text = _clean(reader.pages[index].extract_text() or "")
        except Exception:  # noqa: BLE001 - a damaged page must not discard other pages
            coverage["failedPages"].append(index + 1)
            continue
        check()
        if not text:
            coverage["emptyPages"].append(index + 1)
            continue
        cap = min(MAX_PAGE_CHARACTERS, available)
        if len(text) > cap:
            coverage["truncated"] = True
            text = text[:cap]
        fragment = marker + text
        parts.append(fragment)
        pages.append({"page": index + 1, "text": text})
        length += len(fragment) + 2
        coverage["pagesWithText"].append(index + 1)
    coverage["truncated"] = coverage["truncated"] or coverage["pagesVisited"] < total
    full_text = "\n\n".join(parts)
    coverage["characters"] = len(full_text)
    coverage["complete"] = not coverage["truncated"] and not coverage["emptyPages"] and not coverage["failedPages"]
    coverage["note"] = (
        f"文档共 {total} 页；检查 {coverage['pagesVisited']} 页，提取到 {len(coverage['pagesWithText'])} 页文字。"
        + (f"{len(coverage['emptyPages'])} 页无可提取文字；未执行 OCR。" if coverage["emptyPages"] else "")
        + (f"{len(coverage['failedPages'])} 页提取失败。" if coverage["failedPages"] else "")
        + ("已按页数或字符上限截取，不能视为完整全文阅读。" if coverage["truncated"] else "")
    )
    return full_text, coverage, pages


def _stored_text(local):
    text = _clean(local["text"])
    coverage = _coverage("local_text", local.get("pages"))
    marker = "[论文库已保存原文；页码与完整性未知]\n"
    capped = text[:max(0, MAX_CHARACTERS - len(marker))]
    full_text = marker + capped if capped else ""
    coverage.update(characters=len(full_text), truncated=len(capped) < len(text),
                    note="使用论文库已有提取文本；没有逐页对应关系，不能确认是否覆盖全文。"
                         + ("已按字符上限截取。" if len(capped) < len(text) else ""))
    return full_text, coverage, [{"page": None, "text": capped}] if capped else []


def _read_one(metadata, check):
    problems, coverage, pages = [], _coverage("unavailable"), []
    local = _local_materials(metadata)
    if local:
        check()
        try:
            # Reject traversal in persisted relative paths. Absolute library
            # paths remain supported for user-selected external PDF folders.
            if ".." in Path(local["filepath"]).parts:
                raise ValueError("本地论文路径包含不安全的相对段")
            text, coverage, pages = _read_pdf(resolve_paper_path(local["filepath"]), "local_pdf", check)
            if text:
                source = ("https://arxiv.org/pdf/" + canonical_arxiv_id(metadata["arxivId"])
                          if metadata.get("arxivId") else metadata.get("sourceUrl", ""))
                return text, source, coverage, pages, problems
            problems.append("本地 PDF 未提取到可读文字；未执行 OCR。")
        except _CancelledRead:
            raise
        except Exception as exc:  # noqa: BLE001 - fall back to stored text after any PDF parser failure
            problems.append("本地 PDF 不可读：" + str(exc)[:300])
        check()
        if local["text"].strip():
            text, coverage, pages = _stored_text(local)
            if text:
                return text, metadata.get("sourceUrl", ""), coverage, pages, problems
    check()
    if metadata.get("arxivId"):
        aid = canonical_arxiv_id(metadata["arxivId"])
        # This directory is separate from the library and removed on every exit,
        # including cancellation, parser errors and network failures.
        with TemporaryDirectory(prefix="knowra-roadmap-read-") as directory:
            check()
            path = download_arxiv(aid, directory)
            check()
            text, coverage, pages = _read_pdf(path, "arxiv_pdf", check)
            if text:
                return text, "https://arxiv.org/pdf/" + aid, coverage, pages, problems
            problems.append("arXiv PDF 未提取到可读文字；未执行 OCR。")
    else:
        problems.append("未找到可用的本地全文或 arXiv 版本；未访问付费或受限全文。")
    return "", metadata.get("sourceUrl", ""), coverage, pages, problems


def read_key_papers(papers, cancelled=lambda: None, progress=lambda label: None):
    """Return up to eight source-backed text readings, with explicit coverage.

    Temporary arXiv downloads do not create Paper rows, extraction records, Wiki
    pages or persisted PDFs. Cancellation propagates; a failure for one paper
    does not prevent reading later papers. Blocking HTTP obeys the downloader's
    bounded timeout; cancellation is checked before/after it and between pages.
    """
    results, warnings = [], []
    inputs = list(papers)
    selected = inputs[:MAX_KEY_PAPERS]
    if len(inputs) > MAX_KEY_PAPERS:
        warnings.append(f"本轮全文阅读限 {MAX_KEY_PAPERS} 篇关键论文，其余论文未执行全文读取。")

    def check():
        try:
            cancelled()
        except Exception as exc:
            raise _CancelledRead(exc) from exc

    try:
        check()
        for index, metadata in enumerate(selected):
            check()
            title = metadata.get("title") or metadata.get("id", "未命名论文")
            progress(f"读取关键论文全文 {index + 1}/{len(selected)} · {title[:80]}")
            result = deepcopy(metadata)
            try:
                text, source, coverage, pages, problems = _read_one(metadata, check)
                result.update(fullText=text, fullTextSource=source, fullTextCoverage=coverage,
                              fullTextPages=pages,
                              fullTextStatus="read" if text else "unavailable")
                if not text:
                    result["fullTextError"] = "；".join(problems) or "没有可用的全文内容。"
                if problems:
                    warnings.append(f"《{title}》：" + "；".join(problems))
                if text and not coverage["complete"]:
                    warnings.append(f"《{title}》：" + coverage["note"])
            except _CancelledRead:
                raise
            except Exception as exc:  # noqa: BLE001 - each paper reports its own reading failure
                message = str(exc)[:1000] or "全文读取失败"
                result.update(fullText="", fullTextSource=metadata.get("sourceUrl", ""),
                              fullTextPages=[],
                              fullTextCoverage=_coverage("unavailable"),
                              fullTextStatus="unavailable", fullTextError=message)
                warnings.append(f"《{title}》：{message}")
            check()
            results.append(result)
    except _CancelledRead as exc:
        raise exc.original
    return {"papers": results, "warnings": warnings}
