"""Verify roadmap candidates against official metadata APIs, without downloading PDFs.

Model suggestions are search hints, never bibliographic evidence. A hinted work
must have an exact normalized title match even when its DOI/arXiv ID resolves.
Within this process, roadmap arXiv requests are serialized across runs and spaced
by at least three seconds; Crossref requests also share a single connection slot.
These locks do not coordinate other API clients or separate server processes.
"""

import html
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 80
MAX_REFERENCES = 200
REQUEST_TIMEOUT = 20
ARXIV_INTERVAL = 3.0
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 15
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v[1-9]\d*)?", re.IGNORECASE)
_DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"?#]+", re.IGNORECASE)
_arxiv_lock = threading.Lock()
_crossref_lock = threading.Lock()
_arxiv_last_request = None


class SourceError(Exception):
    """A source failed safely; other candidates may still be verified."""


def canonical_arxiv_id(value):
    raw = str(value or "").strip()
    if raw.lower().startswith(("https://", "http://")):
        url = urllib.parse.urlsplit(raw)
        if (url.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
                or url.username or url.password or url.port not in (None, 80, 443)
                or url.query or url.fragment):
            raise ValueError("无效 arXiv ID")
        match = re.fullmatch(r"/(?:abs|pdf)/(.+)", url.path)
        raw = urllib.parse.unquote(match.group(1)) if match else ""
        if raw.lower().endswith(".pdf"):
            raw = raw[:-4]
    elif raw.lower().startswith("arxiv:"):
        raw = raw[6:].strip()
    if not _ARXIV_ID.fullmatch(raw):
        raise ValueError("无效 arXiv ID")
    return re.sub(r"v\d+$", "", raw, flags=re.IGNORECASE).lower()


def canonical_doi(value):
    raw = str(value or "").strip()
    if raw.lower().startswith(("https://", "http://")):
        url = urllib.parse.urlsplit(raw)
        if (url.hostname not in {"doi.org", "dx.doi.org"}
                or url.username or url.password or url.port not in (None, 80, 443)
                or url.query or url.fragment):
            raise ValueError("无效 DOI")
        raw = urllib.parse.unquote(url.path.lstrip("/"))
    elif raw.lower().startswith("doi:"):
        raw = raw[4:].strip()
    if len(raw) > 300 or not _DOI.fullmatch(raw):
        raise ValueError("无效 DOI")
    return raw.lower()


def _optional_identifier(parser, value):
    try:
        return parser(value)
    except (ValueError, TypeError):
        return None


def _text(value, limit=20000):
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())[:limit]


def normalized_title(value):
    return "".join(c for c in unicodedata.normalize("NFKC", _text(value)).casefold() if c.isalnum())


def _metadata(title, year, authors, abstract, arxiv_id=None, doi=None, references=None):
    source = ("https://arxiv.org/abs/" + arxiv_id if arxiv_id else
              "https://doi.org/" + urllib.parse.quote(doi, safe="/():;"))
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, source)),
        "title": _text(title, 1000), "year": year,
        "authors": [_text(name, 500) for name in authors[:100] if _text(name, 500)],
        "abstract": _text(abstract), "arxivId": arxiv_id, "doi": doi,
        "sourceUrl": source, "reason": "", "branch": "",
        "references": list(dict.fromkeys(references or []))[:MAX_REFERENCES],
    }


def _parse_arxiv(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise SourceError("arXiv 返回不支持的 XML 内容")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceError("arXiv 元数据格式无效") from exc
    if root.tag != _ATOM + "feed":
        raise SourceError("arXiv 未返回论文元数据")
    papers = []
    for entry in root.findall(_ATOM + "entry")[:MAX_CANDIDATES]:
        entry_id = entry.findtext(_ATOM + "id") or ""
        aid = _optional_identifier(canonical_arxiv_id, entry_id)
        if not aid:
            # The API represents invalid queries as an Atom entry with an error ID.
            if "/api/errors" in entry_id:
                raise SourceError("arXiv 无法完成此检索")
            continue
        title = _text(entry.findtext(_ATOM + "title"), 1000)
        if not title:
            continue
        year = re.match(r"^\d{4}", entry.findtext(_ATOM + "published") or "")
        papers.append(_metadata(
            title, year.group() if year else "",
            [author.findtext(_ATOM + "name") or "" for author in entry.findall(_ATOM + "author")],
            entry.findtext(_ATOM + "summary") or "", arxiv_id=aid,
            doi=_optional_identifier(canonical_doi, entry.findtext(_ARXIV + "doi")),
        ))
    return papers


def _crossref_metadata(item):
    if not isinstance(item, dict):
        return None
    doi = _optional_identifier(canonical_doi, item.get("DOI"))
    titles = item.get("title")
    title = next((_text(t, 1000) for t in titles if _text(t, 1000)), "") if isinstance(titles, list) else ""
    if not doi or not title:
        return None
    years = []
    for field in ("published", "published-print", "published-online", "issued"):
        date = item.get(field)
        parts = date.get("date-parts") if isinstance(date, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
            if isinstance(year, int) and 1000 <= year <= 2200:
                years.append(year)
    authors = []
    raw_authors = item.get("author") if isinstance(item.get("author"), list) else []
    for author in raw_authors[:100]:
        if isinstance(author, dict):
            authors.append(author.get("name") or " ".join(
                _text(author.get(field), 250) for field in ("given", "family")
                if _text(author.get(field), 250)))
    references = []
    raw_references = item.get("reference") if isinstance(item.get("reference"), list) else []
    for ref in raw_references[:MAX_REFERENCES]:
        if isinstance(ref, dict):
            reference = _optional_identifier(canonical_doi, ref.get("DOI"))
            if reference:
                references.append(reference)
    return _metadata(title, str(min(years)) if years else "", authors,
                     item.get("abstract") or "", doi=doi, references=references)


def _allowed_api_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return False
        return ((parsed.hostname == "export.arxiv.org" and parsed.path == "/api/query")
                or (parsed.hostname == "api.crossref.org"
                    and (parsed.path == "/works" or parsed.path.startswith("/works/"))))
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never follow server-provided URLs; that would bypass pacing and host checks.
        raise SourceError("检索接口返回跳转，已停止核验")


def _pause(seconds, cancelled):
    remaining = seconds
    while remaining > 0:
        cancelled()
        interval = min(0.25, remaining)
        time.sleep(interval)
        remaining -= interval
    cancelled()


@contextmanager
def _arxiv_slot(cancelled):
    global _arxiv_last_request
    while not _arxiv_lock.acquire(timeout=0.25):
        cancelled()
    try:
        cancelled()
        if _arxiv_last_request is not None:
            _pause(max(0, ARXIV_INTERVAL - (time.monotonic() - _arxiv_last_request)), cancelled)
        _arxiv_last_request = time.monotonic()
        yield
    finally:
        _arxiv_lock.release()


@contextmanager
def _crossref_slot(cancelled):
    # Crossref's unauthenticated public pool permits one simultaneous request.
    while not _crossref_lock.acquire(timeout=0.25):
        cancelled()
    try:
        cancelled()
        yield
    finally:
        _crossref_lock.release()


def _retry_after_seconds(value):
    if not isinstance(value, str):
        return 0
    value = value.strip()
    if len(value) > 100:
        return 0
    if value.isdigit():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0


class _Client:
    def __init__(self, cancelled, warnings):
        self.cancelled = cancelled
        self.warnings = warnings
        self.cache = {}
        self.unavailable = {}
        self.search_log = []
        self.request_attempts = {}
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _read(self, url):
        self.cancelled()
        request = urllib.request.Request(url, headers={
            "User-Agent": "Knowra/1.0 (roadmap research; official metadata only)",
            "Accept-Encoding": "identity",
        })
        self.request_attempts[url] = self.request_attempts.get(url, 0) + 1
        with self.opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            if not _allowed_api_url(response.geturl()):
                raise SourceError("检索结果不来自官方元数据接口")
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    size = int(declared)
                except ValueError as exc:
                    raise SourceError("元数据响应长度无效") from exc
                if size < 0 or size > MAX_RESPONSE_BYTES:
                    raise SourceError("元数据超过大小限制")
            chunks, total = [], 0
            while True:
                self.cancelled()
                chunk = response.read(min(65536, MAX_RESPONSE_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise SourceError("元数据超过大小限制")
                chunks.append(chunk)
            if declared and total != int(declared):
                raise SourceError("元数据响应不完整")
            return b"".join(chunks)

    def fetch(self, url):
        self.cancelled()
        if not _allowed_api_url(url):
            raise SourceError("仅允许使用官方元数据接口")
        if url in self.cache:
            value = self.cache[url]
            if isinstance(value, SourceError):
                raise value
            return value
        source = "arXiv" if url.startswith(ARXIV_API) else "Crossref"
        if source in self.unavailable:
            raise self.unavailable[source]
        error = None
        unavailable = False
        for attempt in range(MAX_ATTEMPTS):
            self.cancelled()
            delay = 2 ** attempt
            try:
                if source == "arXiv":
                    with _arxiv_slot(self.cancelled):
                        raw = self._read(url)
                else:
                    with _crossref_slot(self.cancelled):
                        raw = self._read(url)
                self.cache[url] = raw
                return raw
            except urllib.error.HTTPError as exc:
                retry = exc.code == 429 or 500 <= exc.code <= 599
                unavailable = retry
                error = SourceError(f"{source} 核验失败（HTTP {exc.code}）")
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
                exc.close()
                # Never truncate a server-requested cooldown and retry too soon.
                # Keep this research run bounded; the other source can continue.
                if not retry or retry_after > MAX_RETRY_WAIT:
                    break
                delay = max(delay, retry_after)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                unavailable = True
                error = SourceError(f"{source} 暂时无法连接")
                error.__cause__ = exc
            except SourceError as exc:
                unavailable = False
                error = exc
                break
            if attempt + 1 < MAX_ATTEMPTS:
                _pause(delay, self.cancelled)
        if unavailable:
            error = SourceError(f"{error}；本次已暂停该来源，请稍后重试。")
            self.unavailable[source] = error
        self.cache[url] = error
        raise error

    def _lookup(self, source, url, query_type, query, parse):
        cached = url in self.cache
        skipped = source in self.unavailable and not cached
        before = self.request_attempts.get(url, 0)
        error = ""
        try:
            papers = parse(self.fetch(url))
        except SourceError as exc:
            error = str(exc)
            self.warn(error)
            papers = []
        self.search_log.append({
            "source": source.lower(), "query": query, "queryType": query_type,
            "status": "skipped" if skipped else "failed" if error else "ok",
            "matchedCount": len(papers), "error": error, "cached": cached,
            "attempts": self.request_attempts.get(url, 0) - before,
        })
        return papers

    def arxiv(self, **params):
        params.update(start=0, sortBy="relevance", sortOrder="descending")
        url = ARXIV_API + "?" + urllib.parse.urlencode(params)
        query = params.get("id_list") or params.get("search_query", "")
        query_type = "identifier" if params.get("id_list") else "title" if query.startswith("ti:") else "keywords"
        return self._lookup("arXiv", url, query_type, query, _parse_arxiv)

    def crossref(self, doi=None, title=None):
        url = CROSSREF_API + ("/" + urllib.parse.quote(doi, safe="") if doi else
                              "?" + urllib.parse.urlencode({"query.title": title, "rows": 5}))
        def parse(raw):
            try:
                response = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise SourceError("Crossref 元数据格式无效") from exc
            message = response.get("message") if isinstance(response, dict) else None
            if not isinstance(message, dict):
                raise SourceError("Crossref 元数据格式无效")
            items = [message] if doi else message.get("items")
            if not isinstance(items, list):
                raise SourceError("Crossref 未返回论文元数据")
            papers = [_crossref_metadata(item) for item in items[:5]]
            return [paper for paper in papers if paper]
        return self._lookup("Crossref", url, "identifier" if doi else "title", doi or title, parse)

    def warn(self, warning):
        if warning not in self.warnings:
            self.warnings.append(warning)


def _query(text, field="all"):
    # Model text is keywords, not executable arXiv query syntax or a URL.
    words = re.findall(r"\w+(?:[-']\w+)*", text, flags=re.UNICODE)[:30]
    return " AND ".join(f'{field}:"{word}"' for word in words)


def _exact_title(papers, title, arxiv_id=None, doi=None):
    wanted = normalized_title(title)
    matches = [paper for paper in papers if wanted and normalized_title(paper["title"]) == wanted
               and (not arxiv_id or paper["arxivId"] == arxiv_id)
               and (not doi or paper["doi"] == doi)]
    # Identical short titles can name different works. Do not guess between them.
    unique = {paper["sourceUrl"]: paper for paper in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _same_record(left, right):
    if left["sourceUrl"] == right["sourceUrl"] or (left["doi"] and left["doi"] == right["doi"]):
        return True
    if left["doi"] and right["doi"]:
        return False
    if normalized_title(left["title"]) != normalized_title(right["title"]):
        return False
    # A generic title alone must not attach another work's DOI to a preprint.
    left_authors = {normalized_title(name) for name in left["authors"] if normalized_title(name)}
    right_authors = {normalized_title(name) for name in right["authors"] if normalized_title(name)}
    return bool(left_authors & right_authors)


def collect_candidates(plan, category, cancelled=lambda: None, progress=lambda label: None):
    """Return verified papers, warnings, searchLog and screening counts; no PDFs.

    ``cancelled`` should raise the caller's cancellation exception. Exact title
    matching is deliberately conservative: unverified suggestions are omitted.
    ``references`` contains only Crossref-registered DOI identifiers, if present.
    Search logs distinguish actual queries, cached results and skipped requests;
    ``matchedCount`` is the API result count, before exact-title seed validation.
    Screening counts cover metadata identity checks and candidate deduplication,
    not full-text reading or methodological-quality assessment.
    """
    warnings, papers = [], []
    client = _Client(cancelled, warnings)
    plan = plan if isinstance(plan, dict) else {}
    suggestions = plan.get("papers") if isinstance(plan.get("papers"), list) else []
    screening = {
        "suggestedSeeds": min(35, len(suggestions)), "invalidSeeds": 0,
        "verifiedSeeds": 0, "unverifiedSeeds": 0, "candidatesConsidered": 0,
        "duplicatesRemoved": 0, "excludedByLimit": 0, "candidateCount": 0,
    }
    seeds = []
    for seed in suggestions[:35]:
        if not isinstance(seed, dict) or not _text(seed.get("title"), 1000):
            screening["invalidSeeds"] += 1
            client.warn("已跳过缺少有效标题的候选论文。")
            continue
        seeds.append({"title": _text(seed["title"], 1000),
                      "arxivId": _optional_identifier(canonical_arxiv_id, seed.get("arxivId")),
                      "doi": _optional_identifier(canonical_doi, seed.get("doi"))})
    ids = list(dict.fromkeys(seed["arxivId"] for seed in seeds if seed["arxivId"]))
    cancelled()
    exact_ids = []
    if ids:
        progress("核验 arXiv 论文标识")
        exact_ids = client.arxiv(id_list=",".join(ids), max_results=len(ids))

    def add(paper):
        if not paper:
            return
        screening["candidatesConsidered"] += 1
        for old in papers:
            if _same_record(old, paper):
                screening["duplicatesRemoved"] += 1
                # Prefer the verified preprint so a published version stays downloadable.
                if not old["arxivId"] and paper["arxivId"]:
                    paper = dict(paper)
                    paper["doi"] = paper["doi"] or old["doi"]
                    paper["references"] = list(dict.fromkeys(old["references"] + paper["references"]))[:MAX_REFERENCES]
                    papers[papers.index(old)] = paper
                else:
                    old["doi"] = old["doi"] or paper["doi"]
                    old["references"] = list(dict.fromkeys(old["references"] + paper["references"]))[:MAX_REFERENCES]
                return
        if len(papers) < MAX_CANDIDATES:
            papers.append(paper)
        else:
            screening["excludedByLimit"] += 1

    for index, seed in enumerate(seeds):
        cancelled()
        title = seed["title"]
        progress(f"核验经典论文 {index + 1}/{len(seeds)}：{title[:80]}")
        found = _exact_title(exact_ids, title, arxiv_id=seed["arxivId"]) if seed["arxivId"] else None
        if not found:
            found = _exact_title(client.arxiv(search_query=_query(title, "ti"), max_results=5), title)
        if not found and seed["doi"]:
            found = _exact_title(client.crossref(doi=seed["doi"]), title, doi=seed["doi"])
        if not found:
            found = _exact_title(client.crossref(title=title), title)
        if found:
            screening["verifiedSeeds"] += 1
            add(found)
        else:
            screening["unverifiedSeeds"] += 1
            client.warn(f"未能核验论文《{title[:160]}》，已从候选列表排除。")

    focus = plan.get("focusKeywords") if isinstance(plan.get("focusKeywords"), list) else []
    focus = list(dict.fromkeys(_text(q, 80) for q in focus[:6] if _text(q, 80)))
    if focus:
        # User focus terms are a deliberate scope boundary. Seed titles can
        # still recover foundations. Prefer a planned query that explicitly
        # retains the focus term; otherwise search the term itself. Appending a
        # localized category name would make an English arXiv query return no
        # matches for categories such as “三维重建-静态”.
        planned = plan.get("queries") if isinstance(plan.get("queries"), list) else []
        planned = list(dict.fromkeys(_text(q, 200) for q in planned[:3] if _text(q, 200)))
        queries = []
        for keyword in focus:
            key = keyword.casefold()
            queries.append(next((query for query in planned if key in query.casefold()), keyword))
        queries = list(dict.fromkeys(queries))
    else:
        queries = plan.get("queries") if isinstance(plan.get("queries"), list) else []
        queries = list(dict.fromkeys(_text(q, 200) for q in queries[:3] if _text(q, 200)))
        if not queries and _text(category, 200):
            queries = [_text(category, 200)]
    for index, query in enumerate(queries):
        cancelled()
        if len(papers) >= MAX_CANDIDATES:
            break
        expression = _query(query)
        if not expression:
            continue
        progress(f"检索方向资料 {index + 1}/{len(queries)}：{query[:80]}")
        # Relevance and surveys supplement model-suggested historical milestones;
        # never use a newest-first feed to construct a development roadmap.
        for paper in client.arxiv(search_query=expression, max_results=15):
            add(paper)
        for paper in client.arxiv(search_query=f"({expression}) AND (ti:survey OR ti:review)", max_results=5):
            add(paper)
    cancelled()
    screening["candidateCount"] = len(papers)
    return {"papers": papers, "warnings": warnings, "searchLog": client.search_log, "screening": screening}
