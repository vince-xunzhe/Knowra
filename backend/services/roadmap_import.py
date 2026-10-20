"""Bounded official-arXiv downloads and resumable local library adoption."""

import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

from database import SessionLocal
from models import Paper, RoadmapPaper

from services.personal_recommendation import base_id
from services.roadmap_state import library_index, library_match

MAX_PDF_BYTES = 80 * 1024 * 1024
_ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v[1-9]\d*)?", re.IGNORECASE)
_OFFICIAL_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_request_lock = threading.Lock()
_last_request = None
logger = logging.getLogger(__name__)


def wait_for_arxiv_slot():
    """Respect arXiv's serial request cadence, including failed requests."""
    global _last_request
    with _request_lock:
        now = time.monotonic()
        if _last_request is not None:
            time.sleep(max(0, 3.0 - (now - _last_request)))
        _last_request = time.monotonic()


def canonical_arxiv_id(value):
    raw = str(value or "").strip()
    if raw.lower().startswith(("https://", "http://")):
        url = urlsplit(raw)
        if url.hostname not in _OFFICIAL_HOSTS or url.username or url.query or url.fragment:
            raise ValueError("无效 arXiv ID")
        match = re.fullmatch(r"/(?:abs|pdf)/(.+)", url.path)
        raw = unquote(match.group(1)) if match else ""
        if raw.lower().endswith(".pdf"):
            raw = raw[:-4]
    elif raw.lower().startswith("arxiv:"):
        raw = raw[6:].strip()
    if not _ARXIV.fullmatch(raw):
        raise ValueError("无效 arXiv ID")
    return re.sub(r"v\d+$", "", raw, flags=re.IGNORECASE).lower()


def _official_url(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https" and parsed.hostname in _OFFICIAL_HOSTS
        and parsed.port in (None, 443) and not parsed.username and not parsed.password
    )


class ArxivRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _official_url(newurl):
            raise ValueError("arXiv 下载跳转到了非官方地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _valid_pdf(path):
    try:
        if path.is_symlink() or not 5 < path.stat().st_size <= MAX_PDF_BYTES:
            return False
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                return False
        from pypdf import PdfReader

        # A header alone accepts truncated downloads. Reading the page tree
        # checks the xref/trailer and catalog without running extraction/OCR.
        reader = PdfReader(str(path))
        return not reader.is_encrypted and len(reader.pages) > 0
    except Exception:  # noqa: BLE001 - any PDF parser error makes this file invalid
        return False


def download_arxiv(arxiv_id, directory):
    """Never accept a model/user URL, write partial downloads, or replace a PDF."""
    aid = canonical_arxiv_id(arxiv_id)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ("arxiv_" + aid.replace("/", "_") + ".pdf")
    for existing in directory.glob("*.pdf"):
        if base_id(existing.name) == aid and _valid_pdf(existing):
            return existing
    if destination.exists() or destination.is_symlink():
        raise ValueError("同名文件存在但不是有效 PDF，请检查论文目录后重试。")
    request = urllib.request.Request("https://arxiv.org/pdf/" + aid,
                                     headers={"User-Agent": "Knowra/1.0 (roadmap research)"})
    opener = urllib.request.build_opener(ArxivRedirectHandler())
    temporary = None
    try:
        wait_for_arxiv_slot()
        with opener.open(request, timeout=60) as response:
            if not _official_url(response.geturl()):
                raise ValueError("arXiv 下载来源不是官方地址")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_PDF_BYTES:
                raise ValueError("PDF 超过 80 MB 下载限制")
            with tempfile.NamedTemporaryFile(prefix=".roadmap-", suffix=".part", dir=directory, delete=False) as target:
                temporary = Path(target.name)
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, MAX_PDF_BYTES - total + 1))
                    if not chunk:
                        break
                    if total == 0 and not chunk.startswith(b"%PDF-"):
                        raise ValueError("下载内容不是 PDF")
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise ValueError("PDF 超过 80 MB 下载限制")
                    target.write(chunk)
            if declared and total != int(declared):
                raise ValueError("PDF 下载不完整，请重试")
            if not _valid_pdf(temporary):
                raise ValueError("下载内容不是完整 PDF 文件")
        # Atomic, no-overwrite publication; another local downloader may win.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not _valid_pdf(destination):
                raise ValueError("论文目录出现同名无效文件，请检查后重试。")
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_paper(paper_id):
    from config import load_config
    from path_utils import PAPERS_DIR, portable_data_path, resolve_papers_directory
    from sqlalchemy.exc import IntegrityError

    from services.paper_record_service import sync_record_from_paper
    from services.pdf_service import compute_hash

    with SessionLocal() as db:
        catalog = db.get(RoadmapPaper, paper_id)
        if catalog is None:
            raise ValueError("路线图论文已不存在")
        metadata = deepcopy(catalog.data)
        aid = canonical_arxiv_id(metadata.get("arxivId"))
        existing = library_match(catalog, library_index(db))
        if existing is not None:
            catalog.library_paper_id = str(existing.id)
            db.commit()
            return {"status": "skipped", "libraryPaperId": str(existing.id)}
    cfg = load_config()
    directory = resolve_papers_directory(cfg.get("scan_directory") or str(PAPERS_DIR))
    downloaded = download_arxiv(aid, directory)
    digest = compute_hash(str(downloaded))
    storage_path = portable_data_path(downloaded)
    with SessionLocal() as db:
        catalog = db.get(RoadmapPaper, paper_id)
        if catalog is None:
            raise ValueError("路线图论文已不存在，PDF 已保存在论文目录")
        existing = library_match(catalog, library_index(db))
        if existing is None:
            existing = db.query(Paper).filter_by(file_hash=digest).first()
        created = existing is None
        paper = db.get(Paper, existing.id) if existing is not None else Paper(
            filepath=storage_path, filename=downloaded.name, file_hash=digest,
            title=metadata["title"][:500], authors=metadata.get("authors", [])[:80],
            processed=False, processing_status="scanning",
        )
        db.add(paper)
        try:
            db.flush()
            catalog.library_paper_id = str(paper.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            paper = db.query(Paper).filter_by(filepath=storage_path).first()
            if paper is None:
                paper = db.query(Paper).filter_by(file_hash=digest).first()
            if paper is None:
                raise ValueError("论文入库冲突，请稍后重试")
            created = False
            db.get(RoadmapPaper, paper_id).library_paper_id = str(paper.id)
            db.commit()
        paper_id = str(paper.id)
        if created:
            # Markdown export failure must not misreport a committed local PDF.
            try:
                sync_record_from_paper(paper, event="roadmap_import")
            except Exception:
                logger.exception("Could not export the committed roadmap library record")
        return {"status": "imported" if created else "skipped", "libraryPaperId": paper_id}


def import_board(ctx, payload):
    ids = list(dict.fromkeys(payload["paper_ids"]))
    with SessionLocal() as db:
        rows = {p.id: deepcopy(p.data) for p in db.query(RoadmapPaper).filter(RoadmapPaper.id.in_(ids)).all()}
    items = [{"paperId": pid, "arxivId": rows.get(pid, {}).get("arxivId"),
              "status": "queued", "libraryPaperId": None, "error": None} for pid in ids]
    state = {"label": "准备下载路线图论文", "total": len(items), "done": 0,
             "imported": 0, "skipped": 0, "failed": 0, "items": items}

    def publish():
        ctx.progress = deepcopy(state)
        ctx.queue.update(ctx.job["job_id"], progress=state)

    publish()
    for item in items:
        pid = item["paperId"]
        state["label"] = f"下载论文 {state['done'] + 1}/{len(items)} · {rows.get(pid, {}).get('title', pid)[:65]}"
        item["status"] = "running"
        publish()
        try:
            # Recheck the actual library on resume. A checkpoint must never mark
            # a paper imported after the user deleted it from the library.
            with SessionLocal() as db:
                catalog = db.get(RoadmapPaper, pid)
                exists = library_match(catalog, library_index(db)) if catalog else None
            checkpoint = "import:" + pid
            if exists is None:
                ctx.checkpoints.pop(checkpoint, None)
            result = ctx.step(checkpoint, lambda pid=pid: import_paper(pid))
            item.update(result)
            state[item["status"]] += 1
        except Exception as exc:  # noqa: BLE001 - partial failures are recorded per downloaded paper
            item.update(status="failed", error=str(exc)[:1000])
            state["failed"] += 1
        state["done"] += 1
        publish()
    state["label"] = f"下载完成：{state['imported']} 篇入库，{state['skipped']} 篇已存在，{state['failed']} 篇失败"
    publish()
    # Completed with item-level errors lets retry skip successes and run only
    # missing PDFs, rather than treating the whole batch as lost.
    return deepcopy(state)


def download_states(queue):
    """Newest per-paper download attempt, without reading model checkpoints."""
    result = {}
    with queue.connect() as db:
        jobs = db.execute("SELECT payload,status,progress,error FROM jobs WHERE kind='roadmap_import' ORDER BY created DESC").fetchall()
    for job in jobs:
        payload = json.loads(job["payload"] or "{}")
        progress = json.loads(job["progress"] or "{}")
        items = {item["paperId"]: item for item in progress.get("items", [])}
        for pid in payload.get("paper_ids", []):
            if pid in result:
                continue
            item = items.get(pid, {})
            status = item.get("status")
            if status in ("imported", "skipped"):
                result[pid] = {}
            elif job["status"] in ("failed", "interrupted", "cancelled"):
                result[pid] = {"status": "failed", "error": item.get("error") or job["error"] or "下载已中断，请重试。"}
            elif status == "failed":
                result[pid] = {"status": "failed", "error": item.get("error")}
            elif job["status"] in ("queued", "running"):
                result[pid] = {"status": "running" if status == "running" else "queued", "error": None}
            else:
                result[pid] = {}
    return result
