"""Trace bibliography entries to citation contexts without a model or network."""

import re
import unicodedata

REFERENCE_HEADING = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(?:references|bibliography|参考文献)\s*[:：]?\s*$"
)
APPENDIX_HEADING = re.compile(
    r"(?im)^\s*(?:appendix|appendices|supplementary material|附录)\b[^\n]*$"
)
ENTRY = re.compile(r"(?m)^\s*(?:\[(\d{1,4})\]|(\d{1,4})\.)\s+")
CITATION = re.compile(r"\[\s*(\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*)\s*\]")


def compact(text):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())


def title_span(text, title):
    normalized = compact(text)
    target = compact(title)
    start = normalized.find(target)
    if start < 0:
        return None
    offsets = []
    for index, char in enumerate(text):
        offsets.extend(
            index
            for c in unicodedata.normalize("NFKC", char).casefold()
            if re.match(r"\w", c)
        )
    return offsets[start], offsets[start + len(target) - 1] + 1


def title_aliases(title):
    aliases = [title] if len(compact(title)) >= 10 else []
    prefix = re.split(r"[:：]", title)[0].strip()
    # Named methods/models are useful in prose; generic title prefixes are not.
    if (
        prefix != title
        and len(prefix) >= 5
        and re.search(r"[A-Z].*(?:[A-Z]|\d)|-", prefix)
    ):
        aliases.append(prefix)
    return aliases


def document_sections(pages):
    """Retain physical pages and split reference/body boundaries on each page."""
    sections, in_references = [], False
    for page in pages:
        text = page["text"]
        boundaries = sorted(
            [(m.start(), m.end(), True) for m in REFERENCE_HEADING.finditer(text)]
            + [(m.start(), m.end(), False) for m in APPENDIX_HEADING.finditer(text)]
        )
        offset = 0
        for start, end, references in boundaries:
            if start > offset:
                sections.append(
                    {
                        **page,
                        "text": text[offset:start],
                        "section": "references" if in_references else "body",
                    }
                )
            in_references = references
            offset = end
        if offset < len(text):
            tail = text[offset:]
            # Some extractors omit the heading but preserve a numbered list.
            entries = list(ENTRY.finditer(tail))
            looks_like_bibliography = (
                len(entries) >= 2 and len(re.findall(r"\b(?:19|20)\d{2}\b", tail)) >= 2
            )
            if not in_references and looks_like_bibliography:
                split = entries[0].start()
                if split:
                    sections.append({**page, "text": tail[:split], "section": "body"})
                sections.append({**page, "text": tail[split:], "section": "references"})
            else:
                sections.append(
                    {
                        **page,
                        "text": tail,
                        "section": "references" if in_references else "body",
                    }
                )
    return sections


def includes_number(group, number):
    for part in re.split(r"[,;]", group):
        bounds = re.split(r"[–—-]", part.strip())
        if len(bounds) == 1 and int(bounds[0]) == number:
            return True
        if len(bounds) == 2 and int(bounds[0]) <= number <= int(bounds[1]):
            return True
    return False


def trace_citations(citing, cited, limit=12):
    """Return page-backed body windows plus the matched bibliography entry.

    All returned windows are literal substrings of the extracted document.
    Bibliography matches are used only as locators, never as author opinions.
    """
    sections = citing.get("document", [])
    aliases = title_aliases(cited["title"])
    if not sections or not aliases:
        return {
            "sources": [],
            "bodyCount": 0,
            "truncated": False,
            "referenceFound": False,
        }
    entries, numbers, author_patterns = [], set(), []
    for section in sections:
        if section["section"] != "references":
            continue
        text = section["text"]
        starts = list(ENTRY.finditer(text))
        spans = [
            (m.start(), starts[i + 1].start() if i + 1 < len(starts) else len(text), m)
            for i, m in enumerate(starts)
        ]
        if not spans:
            breaks = [0] + [m.end() for m in re.finditer(r"\n[ \t]*\n", text)]
            if len(breaks) == 1:
                author_starts = [
                    m.start()
                    for m in re.finditer(
                        r"(?m)^[A-ZÀ-ÿ][^\n]{0,100}\b(?:19|20)\d{2}[a-z]?[.)]", text
                    )
                ]
                breaks = sorted(set(breaks + author_starts))
            spans = [
                (a, breaks[i + 1] if i + 1 < len(breaks) else len(text), None)
                for i, a in enumerate(breaks)
            ]
        for start, end, marker in spans:
            entry = text[start:end]
            match = title_span(entry, cited["title"])
            if not match:
                continue
            excerpt_start = start + max(0, match[0] - 180)
            entries.append((section, excerpt_start, min(end, excerpt_start + 2200)))
            if marker:
                numbers.add(int(marker.group(1) or marker.group(2)))
            # Author-year styles: bind the author's name to the entry's year.
            clean = ENTRY.sub("", entry, count=1)
            years = re.findall(r"\b((?:19|20)\d{2}[a-z]?)\b", clean)
            first = re.split(
                r"[,;]|\band\b|\bet al\b|\b(?:19|20)\d{2}", clean, maxsplit=1
            )[0]
            names = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ-]{2,}", first)[:4]
            prefix = entry[: match[0]]
            # An unsegmented bibliography may contain several works. Never
            # use another entry's author/year to locate a body citation.
            if (
                years
                and len(re.findall(r"\b(?:19|20)\d{2}\b", prefix)) <= 1
                and len(prefix) < 500
            ):
                year = years[0]
                for name in names:
                    author_patterns.append(
                        re.compile(
                            r"\b"
                            + re.escape(name)
                            + r"\b(?:\s+et\s+al\.?)?[^\n.;]{0,70}?\b"
                            + re.escape(year)
                            + r"\b",
                            re.IGNORECASE,
                        )
                    )
    body = []
    for section in sections:
        if section["section"] != "body":
            continue
        text = section["text"]
        hits = [
            (m.start(), m.end())
            for m in CITATION.finditer(text)
            if any(includes_number(m.group(1), n) for n in numbers)
        ]
        for alias in aliases:
            # Allow whitespace and punctuation changes caused by PDF extraction.
            words = re.findall(r"[\w]+", alias)
            pattern = (
                r"(?<!\w)"
                + r"[\s\W_]*".join(re.escape(w) for w in words)
                + r"(?!\w|[.-]\d)"
            )
            hits.extend(
                (m.start(), m.end()) for m in re.finditer(pattern, text, re.IGNORECASE)
            )
        for pattern in author_patterns:
            hits.extend((m.start(), m.end()) for m in pattern.finditer(text))
        windows = []
        for start, end in sorted(set(hits)):
            left, right = max(0, start - 700), min(len(text), end + 1000)
            # Merge nearby hits, while keeping each excerpt bounded.
            if windows and left <= windows[-1][1] and right - windows[-1][0] <= 2400:
                windows[-1] = (windows[-1][0], right)
            else:
                windows.append((left, right))
        body.extend((section, a, b) for a, b in windows)
    # A paragraph can continue on the next physical page. Include its opening
    # as another literal page-backed source, unless that page is already covered.
    body_pages = {section["page"] for section, _, _ in body}
    continuations = []
    for section, _, end in body:
        page = section["page"]
        if (
            not isinstance(page, int)
            or end < len(section["text"])
            or page + 1 in body_pages
        ):
            continue
        following = next(
            (s for s in sections if s["page"] == page + 1 and s["section"] == "body"),
            None,
        )
        if following:
            continuations.append((following, 0, min(1000, len(following["text"]))))
            body_pages.add(page + 1)
    body.extend(continuations)
    # Prefer diversity of pages before returning a second context on one page.
    counts = {}
    ranked = []
    for item in body:
        page = item[0]["page"]
        ordinal = counts.get(page, 0)
        counts[page] = ordinal + 1
        ranked.append((ordinal, item))
    body = [item for _, item in sorted(ranked, key=lambda v: v[0])]
    selected = body[:limit] + entries[:2]
    sources = []
    for index, (section, start, end) in enumerate(selected):
        page = section["page"]
        sources.append(
            {
                "id": f"{citing['id']}:citation:{cited['id']}:{index}",
                "paperId": citing["id"],
                "source": "pdf" if page else "text",
                "locator": f"PDF 第 {page} 页" if page else "已保存原文（页码未知）",
                "page": page,
                "text": section["text"][start:end],
                "section": section["section"],
            }
        )
    return {
        "sources": sources,
        "bodyCount": len(body),
        "truncated": len(body) > limit,
        "referenceFound": bool(entries),
    }
