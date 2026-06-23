"""Minimal arXiv API client — stdlib only (urllib + xml.etree), so the cloud
image needs no extra HTTP dependency.

arXiv Atom API:
  http://export.arxiv.org/api/query?search_query=...&start=0&max_results=N
    &sortBy=submittedDate&sortOrder=descending
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_API = "http://export.arxiv.org/api/query"


def _parse_dt(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _arxiv_id(entry_id: str) -> str:
    # entry id looks like http://arxiv.org/abs/2401.01234v2
    return entry_id.rstrip("/").split("/abs/")[-1].strip()


def search_arxiv(
    query: str,
    *,
    max_results: int = 40,
    since: Optional[datetime] = None,
) -> list[dict]:
    """Run an arXiv search (newest first). Returns paper dicts. When ``since``
    is given, only entries published after it are kept."""
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Knowra/1.0 (+arxiv recommender)"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
        raw = resp.read()

    root = ET.fromstring(raw)
    out: list[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        aid = _arxiv_id((entry.findtext(f"{_ATOM}id") or "").strip())
        if not aid:
            continue
        published = _parse_dt(entry.findtext(f"{_ATOM}published") or "")
        if since is not None and published is not None and published <= since:
            continue
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        abstract = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        authors = [
            (a.findtext(f"{_ATOM}name") or "").strip()
            for a in entry.findall(f"{_ATOM}author")
        ]
        authors = [a for a in authors if a]
        pcat = entry.find(f"{_ARXIV}primary_category")
        out.append({
            "arxiv_id": aid,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "pdf_url": f"https://arxiv.org/pdf/{aid}",
            "primary_category": pcat.get("term") if pcat is not None else None,
            "published": published,
        })
    return out
