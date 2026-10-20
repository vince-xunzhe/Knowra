"""Full-text research reads bounded material without changing the local library."""

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from models import Base, Paper, RoadmapPaper
from services import roadmap_fulltext as reading


def metadata(pid="classic", **changes):
    return {"id": pid, "title": "A Classic Learning Method", "year": "2017",
            "authors": ["Alice"], "abstract": "A grounded abstract", "arxivId": "1706.03762",
            "doi": None, "sourceUrl": "https://arxiv.org/abs/1706.03762",
            "reason": "Introduced a foundational technique", "branch": "Architecture", **changes}


def pdf_bytes(*texts):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font),
        })})
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(("BT /F1 12 Tf 20 250 Td (" + escaped + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(reading, "SessionLocal", factory)

    def unexpected_download(*args, **kwargs):
        raise AssertionError("Unexpected network access")

    monkeypatch.setattr(reading, "download_arxiv", unexpected_download)
    yield SimpleNamespace(db=factory, root=tmp_path)
    engine.dispose()


def add_local(env, path, *, extracted=None, filename="arxiv_1706.03762.pdf", pid="local", **extra):
    with env.db() as db:
        db.add(Paper(id=pid, filename=filename, filepath=str(path), file_hash=pid,
                     title="A Classic Learning Method", authors=["Alice"],
                     extracted_text=extracted, **extra))
        db.commit()


def fake_download(monkeypatch, content, *, error=None):
    calls = []

    def download(aid, directory):
        root = Path(directory)
        calls.append((aid, root))
        (root / "partial.part").write_bytes(b"partial")
        if error:
            raise error
        path = root / "download.pdf"
        path.write_bytes(content)
        return path

    monkeypatch.setattr(reading, "download_arxiv", download)
    return calls


def test_prefers_matching_local_pdf_and_reports_actual_pages(env):
    path = env.root / "paper.pdf"
    content = pdf_bytes("First page evidence.", "Second page evidence.")
    path.write_bytes(content)
    add_local(env, path, extracted="Older truncated extraction", num_pages=2)
    progress = []
    source = metadata(filepath="/untrusted/model/path.pdf", libraryPaperId="fabricated")
    result = reading.read_key_papers([source], progress=progress.append)
    paper = result["papers"][0]
    assert paper["fullTextStatus"] == "read"
    assert "[PDF 第 1 页]" in paper["fullText"] and "[PDF 第 2 页]" in paper["fullText"]
    assert paper["fullTextPages"] == [{"page": 1, "text": "First page evidence."},
                                       {"page": 2, "text": "Second page evidence."}]
    assert paper["fullTextCoverage"]["source"] == "local_pdf"
    assert paper["fullTextSource"] == "https://arxiv.org/pdf/1706.03762"
    assert paper["fullTextCoverage"]["complete"] is True
    assert paper["fullTextCoverage"]["pagesWithText"] == [1, 2]
    assert result["warnings"] == [] and len(progress) == 1
    assert "fullText" not in source
    assert path.read_bytes() == content
    with env.db() as db:
        local = db.get(Paper, "local")
        assert local.extracted_text == "Older truncated extraction"
        assert local.processed is False and db.query(Paper).count() == 1
        assert db.query(RoadmapPaper).count() == 0


def test_local_saved_text_has_unknown_page_numbers_and_completeness(env):
    add_local(env, env.root / "missing.pdf", extracted="Existing saved body.", num_pages=12)
    result = reading.read_key_papers([metadata()])
    paper = result["papers"][0]
    assert paper["fullTextStatus"] == "read"
    assert paper["fullTextPages"] == [{"page": None, "text": "Existing saved body."}]
    coverage = paper["fullTextCoverage"]
    assert coverage["source"] == "local_text" and coverage["totalPages"] == 12
    assert coverage["complete"] is False and coverage["pagesVisited"] == 0
    assert "不能确认" in coverage["note"] and result["warnings"]


def test_download_is_temporary_and_does_not_adopt_or_process_paper(env, monkeypatch):
    calls = fake_download(monkeypatch, pdf_bytes("Downloaded key paper body."))
    result = reading.read_key_papers([metadata(arxivId="1706.03762v3")])
    paper = result["papers"][0]
    assert paper["fullTextStatus"] == "read"
    assert paper["fullTextSource"] == "https://arxiv.org/pdf/1706.03762"
    assert paper["fullTextCoverage"]["source"] == "arxiv_pdf"
    assert calls[0][0] == "1706.03762" and not calls[0][1].exists()
    with env.db() as db:
        assert db.query(Paper).count() == db.query(RoadmapPaper).count() == 0


def test_doi_only_unavailable_does_not_use_arbitrary_source_or_model_file(env):
    path = env.root / "sensitive.pdf"
    path.write_bytes(pdf_bytes("Not a library paper."))
    result = reading.read_key_papers([metadata(arxivId=None, doi="10.1000/example",
                                              sourceUrl="https://publisher.example/paywall",
                                              filepath=str(path))])
    paper = result["papers"][0]
    assert paper["fullTextStatus"] == "unavailable" and paper["fullText"] == ""
    assert paper["fullTextPages"] == []
    assert "未访问付费" in paper["fullTextError"] and "Not a library" not in str(result)


def test_doi_local_pdf_preserves_publisher_source_and_page_numbers(env):
    path = env.root / "publisher.pdf"
    path.write_bytes(pdf_bytes("A locally available publisher paper."))
    add_local(env, path, filename="publisher.pdf")
    source = "https://doi.org/10.1000/classic"
    paper = reading.read_key_papers([metadata(arxivId=None, sourceUrl=source)])["papers"][0]
    assert paper["fullTextStatus"] == "read" and paper["fullTextSource"] == source
    assert paper["fullTextPages"][0]["page"] == 1


def test_does_not_read_same_title_with_different_author(env):
    path = env.root / "other.pdf"
    path.write_bytes(pdf_bytes("A different author's private local paper."))
    add_local(env, path, filename="unrelated.pdf")
    paper = reading.read_key_papers([metadata(arxivId=None, authors=["Bob"])])["papers"][0]
    assert paper["fullTextStatus"] == "unavailable"


@pytest.mark.parametrize("kind", ["symlink", "traversal"])
def test_rejects_unsafe_local_pdf_paths(env, kind):
    target = env.root / "target.pdf"
    target.write_bytes(pdf_bytes("Do not follow unsafe paths."))
    if kind == "symlink":
        path = env.root / "link.pdf"
        path.symlink_to(target)
    else:
        path = env.root / "subdir" / ".." / "target.pdf"
    add_local(env, path)
    result = reading.read_key_papers([metadata(arxivId=None)])
    assert result["papers"][0]["fullTextStatus"] == "unavailable"
    assert "Do not follow" not in str(result)
    assert target.exists()


def test_per_paper_failure_continues_and_cleans_temporary_directories(env, monkeypatch):
    calls = fake_download(monkeypatch, b"not PDF")
    result = reading.read_key_papers([metadata(), metadata("doi", arxivId=None)])
    assert [p["fullTextStatus"] for p in result["papers"]] == ["unavailable", "unavailable"]
    assert "PDF" in result["papers"][0]["fullTextError"]
    assert not calls[0][1].exists() and len(result["warnings"]) == 2


def test_network_failure_cleans_partial_files(env, monkeypatch):
    calls = fake_download(monkeypatch, b"", error=TimeoutError("Download timeout"))
    result = reading.read_key_papers([metadata()])
    assert "Download timeout" in result["papers"][0]["fullTextError"]
    assert not calls[0][1].exists()


def test_cancel_before_read_stops_without_download(env):
    error = RuntimeError("cancelled")

    def cancelled():
        raise error

    with pytest.raises(RuntimeError) as caught:
        reading.read_key_papers([metadata()], cancelled=cancelled)
    assert caught.value is error


def test_cancel_after_download_propagates_and_cleans_temp_directory(env, monkeypatch):
    calls = fake_download(monkeypatch, pdf_bytes("Not processed after cancel."))

    def cancelled():
        if calls:
            raise RuntimeError("cancelled while downloading")

    with pytest.raises(RuntimeError, match="cancelled while downloading"):
        reading.read_key_papers([metadata()], cancelled=cancelled)
    assert not calls[0][1].exists()


def mock_reader(monkeypatch, pages):
    monkeypatch.setattr("pypdf.PdfReader", lambda *args, **kwargs: SimpleNamespace(
        is_encrypted=False, pages=pages,
    ))


def page(text):
    return SimpleNamespace(extract_text=lambda: text)


def test_bounds_number_of_papers_and_never_claims_unread_pages(env, monkeypatch):
    path = env.root / "long.pdf"
    path.write_bytes(pdf_bytes("placeholder"))
    add_local(env, path)
    mock_reader(monkeypatch, [page("Page text.")] * 51)
    result = reading.read_key_papers([metadata(str(i)) for i in range(10)])
    assert len(result["papers"]) == 8
    assert any("限 8 篇" in warning for warning in result["warnings"])
    for paper in result["papers"]:
        coverage = paper["fullTextCoverage"]
        assert coverage["totalPages"] == 51 and coverage["pagesVisited"] == 50
        assert coverage["truncated"] is True and coverage["complete"] is False
        assert len(paper["fullTextPages"]) == 50
        assert "第 51 页" not in paper["fullText"]


def test_bounds_total_text_and_each_page_without_forging_complete_coverage(env, monkeypatch):
    path = env.root / "dense.pdf"
    path.write_bytes(pdf_bytes("placeholder"))
    add_local(env, path)
    mock_reader(monkeypatch, [page("x" * 20000)] * 10)
    paper = reading.read_key_papers([metadata()])["papers"][0]
    assert len(paper["fullText"]) <= 50000
    assert all(len(item["text"]) <= 10000 for item in paper["fullTextPages"])
    assert paper["fullTextCoverage"]["truncated"] is True
    assert paper["fullTextCoverage"]["complete"] is False


def test_empty_and_failed_pages_remain_visible_in_coverage(env, monkeypatch):
    path = env.root / "partial.pdf"
    path.write_bytes(pdf_bytes("placeholder"))
    add_local(env, path)

    def fail():
        raise ValueError("Cannot extract page")

    mock_reader(monkeypatch, [page("Readable."), page(""), SimpleNamespace(extract_text=fail)])
    paper = reading.read_key_papers([metadata()])["papers"][0]
    assert paper["fullTextStatus"] == "read"
    assert paper["fullTextPages"] == [{"page": 1, "text": "Readable."}]
    coverage = paper["fullTextCoverage"]
    assert coverage["pagesVisited"] == 3 and coverage["emptyPages"] == [2]
    assert coverage["failedPages"] == [3] and coverage["complete"] is False
    assert "未执行 OCR" in coverage["note"]


def test_cancel_between_pages_propagates_without_deleting_library_pdf(env, monkeypatch):
    path = env.root / "local.pdf"
    path.write_bytes(pdf_bytes("placeholder"))
    add_local(env, path)
    extracted = []

    def extract():
        extracted.append(True)
        return "Read this one page."

    mock_reader(monkeypatch, [SimpleNamespace(extract_text=extract), page("Never read")])

    def cancelled():
        if extracted:
            raise RuntimeError("cancel between pages")

    with pytest.raises(RuntimeError, match="cancel between pages"):
        reading.read_key_papers([metadata()], cancelled=cancelled)
    assert len(extracted) == 1 and path.exists()
