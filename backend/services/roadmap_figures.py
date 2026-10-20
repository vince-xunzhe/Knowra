"""Source-backed architecture illustrations; no generated or model-supplied URLs."""

import hashlib
import io
import re
import threading
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from path_utils import DATA_DIR

from services.roadmap_import import canonical_arxiv_id, wait_for_arxiv_slot

ASSET_DIR = DATA_DIR / "roadmap_assets"
ASSET_PREFIX = "/api/roadmaps/report-assets/"
ASSET_ID = re.compile(r"[a-f0-9]{64}")
ASSET_URL = re.compile(re.escape(ASSET_PREFIX) + r"([a-f0-9]{64})\.png")
MAX_FIGURES = 2
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ARCHITECTURE = re.compile(r"\b(?:architecture|framework|pipeline|model overview|overview of (?:our|the) (?:model|method|system))\b|网络架构|模型架构|整体框架", re.IGNORECASE)
_RESULT_CAPTION = re.compile(r"\b(?:results?|performance|accuracy|ablation|benchmark|comparison)\b|实验结果|消融|准确率|性能对比", re.IGNORECASE)
_CAPTION = re.compile(r"(?:^|\n)\s*((?:Figure|Fig\.)\s*\d+[a-z]?|图\s*\d+)\s*[:.：]\s*(.{0,1600})", re.IGNORECASE)
_pdf_lock = threading.Lock()


def _architecture_caption(caption):
    title = re.sub(r"^(?:Figure|Fig\.|图)\s*\d+[a-z]?\s*[:.：]?\s*", "", caption, flags=re.IGNORECASE)
    # A results montage may mention a 'learning framework' in its explanation;
    # require the caption's title sentence to identify the architecture itself.
    title = re.split(r"(?<=[.!?])\s+", title, maxsplit=1)[0]
    return bool(_ARCHITECTURE.search(title)) and not bool(_RESULT_CAPTION.search(title))


class _CancelledFigure(BaseException):
    def __init__(self, original):
        self.original = original


def asset_path(asset_id):
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        raise ValueError("Invalid report asset ID")
    root = Path(ASSET_DIR)
    if root.is_symlink():
        raise ValueError("Report asset directory cannot be a symlink")
    path = root / (asset_id + ".png")
    if path.is_symlink():
        raise ValueError("Report image cannot be a symlink")
    return path


def _store_image(image):
    from PIL import Image

    if image.width < 50 or image.height < 30 or image.width * image.height > 24000000:
        raise ValueError("Figure dimensions are outside supported bounds")
    image.thumbnail((1800, 1800))
    output = io.BytesIO()
    # Paper diagrams often use transparent backgrounds; dropping alpha turns
    # black lines on transparent black pixels into an unreadable solid image.
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    Image.alpha_composite(background, rgba).convert("RGB").save(output, format="PNG")
    raw = output.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Figure exceeds the image size limit")
    asset_id = hashlib.sha256(raw).hexdigest()
    destination = asset_path(asset_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Content addressing avoids overwriting an existing image or following links.
    try:
        with destination.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != asset_id:
            raise ValueError("Report image cache is inconsistent") from None
    return asset_id


class _Figures(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.figures = []
        self.current = None
        self.depth = 0
        self.caption = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "figure":
            self.depth += 1
            if self.depth == 1:
                self.current = {"images": [], "caption": [], "anchor": attrs.get("id", "")}
        if self.current is None:
            return
        if tag == "figcaption":
            self.caption = True
        if tag == "img" and attrs.get("src"):
            self.current["images"].append(attrs["src"])

    def handle_endtag(self, tag):
        if tag == "figcaption":
            self.caption = False
        if tag == "figure" and self.depth:
            self.depth -= 1
            if not self.depth and self.current:
                self.figures.append(self.current)
                self.current = None

    def handle_data(self, data):
        if self.current is not None and self.caption:
            self.current["caption"].append(data)


def _same_paper_url(url, aid):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "arxiv.org"
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and ".." not in unquote(parsed.path).split("/")
            and bool(re.match(r"^/html/" + re.escape(aid) + r"(?:v[1-9]\d*)?(?:/|$)", parsed.path)))


def _fetch(url, aid, limit):
    if not _same_paper_url(url, aid):
        raise ValueError("Figure URL does not belong to the verified arXiv paper")

    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _same_paper_url(newurl, aid):
                raise ValueError("Figure redirected outside its verified arXiv paper")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    request = urllib.request.Request(url, headers={"User-Agent": "Knowra/1.0 (roadmap figures)"})
    wait_for_arxiv_slot()
    with urllib.request.build_opener(Redirect()).open(request, timeout=20) as response:
        final_url = response.geturl()
        if not _same_paper_url(final_url, aid):
            raise ValueError("Unexpected figure source")
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Figure source exceeds the download limit")
        return raw, final_url


def _html_figures(metadata, check):
    from PIL import Image

    aid = canonical_arxiv_id(metadata["arxivId"])
    raw, url = _fetch("https://arxiv.org/html/" + aid, aid, 3 * 1024 * 1024)
    check()
    parser = _Figures()
    parser.feed(raw.decode("utf-8", errors="replace"))
    figures = []
    for entry in parser.figures[:80]:
        caption = " ".join(" ".join(entry["caption"]).split())[:1600]
        label = re.match(r"((?:Figure|Fig\.)\s*\d+[a-z]?|图\s*\d+)", caption, re.IGNORECASE)
        if not label or not _architecture_caption(caption) or len(entry["images"]) != 1:
            continue
        image_url = urljoin(url.rstrip("/") + "/", entry["images"][0])
        # Vector and multipart figures fall back to a complete original PDF page.
        if not re.search(r"\.(?:png|jpe?g|webp)$", urlsplit(image_url).path, re.IGNORECASE):
            continue
        check()
        image_bytes, _ = _fetch(image_url, aid, MAX_IMAGE_BYTES)
        check()
        with Image.open(io.BytesIO(image_bytes)) as picture:
            asset_id = _store_image(picture)
        anchor = entry["anchor"]
        source = url + ("#" + anchor if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", anchor) else "")
        figures.append({"assetId": asset_id, "imageUrl": ASSET_PREFIX + asset_id + ".png",
                        "paperId": metadata["id"], "figureLabel": label[1], "caption": caption,
                        "sourceUrl": source, "page": None, "kind": "figure"})
        if len(figures) >= MAX_FIGURES:
            break
    return figures


def _page_candidates(pages):
    found = []
    for page in pages:
        if not isinstance(page.get("page"), int):
            continue
        # Only a figure caption starting on its own line is a candidate, not a
        # body-text mention such as 'as shown in Figure 2'.
        body = page.get("text", "")
        for match in _CAPTION.finditer(body):
            # Captions often wrap over several extracted lines. Retain a small
            # paragraph, stopping at blank lines rather than copying the page.
            fragment = body[match.start():match.start() + 1600].strip().split("\n\n", 1)[0]
            caption = " ".join(fragment.splitlines()[:6])[:1600]
            if _architecture_caption(caption):
                found.append({"page": page["page"], "figureLabel": match[1], "caption": caption})
                break
        if len(found) >= MAX_FIGURES:
            break
    return found


def extract_figures(path, metadata, pages, check=lambda: None):
    """Use original HTML figures, or clearly identified whole PDF pages.

    Read only papers with an architecture caption in the supplied text. A
    missing or failed image never invalidates otherwise usable paper text.
    """
    def cancelled():
        try:
            check()
        except Exception as exc:
            raise _CancelledFigure(exc) from exc

    try:
        return _extract_figures(path, metadata, pages, cancelled)
    except _CancelledFigure as exc:
        raise exc.original


def _extract_figures(path, metadata, pages, check):
    candidates = _page_candidates(pages)
    if not candidates:
        return [], []
    figures, warnings = [], []
    check()
    if metadata.get("arxivId"):
        try:
            figures = _html_figures(metadata, check)
        except Exception as exc:  # noqa: BLE001 - PDF-page fallback after bounded HTML failure
            # Cancellation callbacks use a private wrapper; re-check outside the
            # catch so cancellation cannot become an image-download warning.
            warnings.append("架构原图未取得，将尝试保留原文图所在页：" + str(exc)[:180])
        check()
    if figures:
        return figures, warnings
    try:
        import pypdfium2 as pdfium

        with _pdf_lock:
            document = pdfium.PdfDocument(str(path))
            try:
                for candidate in candidates:
                    check()
                    page_number = candidate["page"]
                    if not 1 <= page_number <= len(document):
                        continue
                    page = document[page_number - 1]
                    try:
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            continue
                        bitmap = page.render(scale=min(2, 1800 / max(width, height)))
                        try:
                            asset_id = _store_image(bitmap.to_pil())
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    source = ("https://arxiv.org/pdf/" + canonical_arxiv_id(metadata["arxivId"])
                              if metadata.get("arxivId") else metadata["sourceUrl"])
                    figures.append(dict(candidate, assetId=asset_id, imageUrl=ASSET_PREFIX + asset_id + ".png",
                                        paperId=metadata["id"], sourceUrl=source + f"#page={page_number}", kind="page"))
            finally:
                document.close()
    except Exception as exc:  # noqa: BLE001 - figures are optional even when text is usable
        warnings.append("架构图所在页未能渲染：" + str(exc)[:180])
    check()
    return figures, warnings


def report_asset_ids(markdown):
    return list(dict.fromkeys(ASSET_URL.findall(markdown or "")))[:32]
