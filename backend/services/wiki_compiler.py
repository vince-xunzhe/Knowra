"""Phase 1 — LLM-compiled wiki layer.

Reads the raw layer (Paper rows + paper_records/*.md) and writes:
  data/wiki/papers/{id:04d}-{slug}.md     — per-paper "encyclopedia" entry
  data/wiki/concepts/{id:04d}-{slug}.md   — per-concept aggregated article

Each .md begins with a YAML-ish frontmatter block. ``compiled_at`` in the
frontmatter is the source of truth for "last compile time" surfaced to the
UI — no DB column is required, and the wiki layer remains rebuildable from
disk alone.

Compile model is intentionally decoupled from cfg["vlm_model"] (which is
tied to the Assistants/Responses + file_search pipeline). Wiki compilation
is a plain summarization task; default to a fast/cheap chat-completions
model. Override via the ``WIKI_COMPILE_MODEL`` env var.
"""
import hashlib
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from openai import OpenAI
from sqlalchemy.orm import Session

from models import KnowledgeNode, Paper
from path_utils import DATA_DIR
from services.graph_service import (
    is_publishable_concept_node,
    node_origin,
    normalize_source_paper_ids,
)
from services.paper_category_service import effective_paper_category
from services.vlm_service import model_uses_responses_api


def _log(msg: str) -> None:
    """Stderr + flush=True so uvicorn shows messages immediately. Compile
    runs in a background thread; without flush you'd see nothing until
    the worker exits."""
    print(f"[wiki] {msg}", file=sys.stderr, flush=True)


WIKI_DIR = DATA_DIR / "wiki"
WIKI_PAPERS_DIR = WIKI_DIR / "papers"
WIKI_CONCEPTS_DIR = WIKI_DIR / "concepts"


# Allow CJK in slugs so 中文 concept names stay readable on disk.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._一-鿿-]+")


def _slugify(text: str, fallback: str = "untitled") -> str:
    if not text:
        return fallback
    cleaned = _SLUG_RE.sub("-", text).strip("-").lower()
    return cleaned or fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(value: Any) -> str:
    return hashlib.sha1(_stable_json(value).encode("utf-8")).hexdigest()


# --- frontmatter ------------------------------------------------------------

# Strict: frontmatter must start at the very first byte of the file.
_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _render_frontmatter(meta: dict) -> str:
    """Tiny YAML-ish dumper. We only emit scalars and flat string lists,
    so we avoid pulling in PyYAML for one helper."""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {json.dumps(item, ensure_ascii=False)}")
        elif v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Pair of _render_frontmatter. Returns ({}, original_text) on miss."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    meta: dict = {}
    current_list_key: Optional[str] = None
    for line in m.group(1).splitlines():
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key is not None:
            raw = line[4:]
            try:
                meta[current_list_key].append(json.loads(raw))
            except Exception:
                meta[current_list_key].append(raw.strip())
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not raw:
            current_list_key = key
            meta[key] = []
            continue
        current_list_key = None
        try:
            meta[key] = json.loads(raw)
        except Exception:
            if raw == "null":
                meta[key] = None
            elif raw in ("true", "false"):
                meta[key] = raw == "true"
            else:
                meta[key] = raw
    return meta, body


def _read_frontmatter_from_path(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _ = _parse_frontmatter(text)
    return meta


def _pick_preferred_page(entries: List[dict], expected_name: Optional[str] = None) -> Optional[dict]:
    if not entries:
        return None
    if expected_name:
        for entry in entries:
            if entry.get("filename") == expected_name:
                return entry
    return max(
        entries,
        key=lambda entry: (
            str(entry.get("compiled_at") or ""),
            str(entry.get("filename") or ""),
        ),
    )


def _normalize_int_list(values: Any) -> List[int]:
    raw = values or []
    if not isinstance(raw, list):
        raw = [raw]
    out: List[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _paper_compile_signature(paper: Paper, model: Optional[str] = None) -> str:
    return _hash_payload({
        "paper_id": paper.id,
        "title": paper.title or paper.filename,
        "authors": list(paper.authors or []),
        "processed_at": paper.processed_at.isoformat() if paper.processed_at else None,
        "extraction_model": getattr(paper, "extraction_model", None),
        "wiki_compile_model": model,
        "raw_llm_response": paper.raw_llm_response or "",
        "notes": paper.notes or "",
    })


def _published_concept_source_papers(node: KnowledgeNode, db: Session) -> List[Paper]:
    paper_ids = normalize_source_paper_ids(node.source_paper_ids)
    if not paper_ids:
        return []
    papers = db.query(Paper).filter(Paper.id.in_(paper_ids), Paper.processed.is_(True)).all()
    paper_by_id = {paper.id: paper for paper in papers}
    ordered: List[Paper] = []
    for pid in paper_ids:
        paper = paper_by_id.get(pid)
        if paper is not None:
            ordered.append(paper)
    return ordered


def _concept_compile_signature(
    node: KnowledgeNode,
    papers: List[Paper],
    model: Optional[str] = None,
) -> str:
    return _hash_payload({
        "concept_id": node.id,
        "title": node.title,
        "content": node.content or "",
        "node_type": node.node_type,
        "node_origin": node_origin(node),
        "wiki_compile_model": model,
        "tags": sorted(set(node.tags or [])),
        "source_paper_ids": [paper.id for paper in papers],
        "papers": [
            {
                "id": paper.id,
                "title": paper.title or paper.filename,
                "processed_at": paper.processed_at.isoformat() if paper.processed_at else None,
                "extraction_model": getattr(paper, "extraction_model", None),
            }
            for paper in papers
        ],
    })


def list_publishable_concept_nodes(db: Session) -> List[KnowledgeNode]:
    processed_ids = {
        row[0] for row in db.query(Paper.id).filter(Paper.processed.is_(True)).all()
    }
    nodes = db.query(KnowledgeNode).all()
    return [node for node in nodes if is_publishable_concept_node(node, processed_ids)]


def count_publishable_concepts(db: Session) -> int:
    return len(list_publishable_concept_nodes(db))


# --- LLM call ---------------------------------------------------------------

def _call_llm(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1800,
) -> str:
    """Route to the right OpenAI surface based on the model. Responses-only
    models (GPT-5.4/5.5) can't be reached via chat.completions; everything
    else uses chat.completions for simplicity. No JSON mode — output is
    markdown."""
    if model_uses_responses_api(model):
        resp = client.responses.create(
            model=model,
            instructions=system,
            input=user,
        )
        return (getattr(resp, "output_text", "") or "").strip()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# --- compile_paper_page -----------------------------------------------------

PAPER_PAGE_SYSTEM = (
    "你是个人 LLM wiki 的编译器。给定一篇论文的抽取 JSON 与用户笔记，"
    "用中文写一篇结构化的 wiki 条目。要求：\n"
    "1. 禁止编造，只能基于输入材料；缺失字段直接省略对应小节，不要写「暂无」。\n"
    "2. 用 markdown，所有正文小节从 ## 二级标题开始；不要再写一级标题。\n"
    "3. 涉及到的概念/方法/数据集名称用 [[名称]] 标记，方便后续 backlink。\n"
    "4. 末尾不要写 Sources 段；frontmatter 已记录 paper_id。"
)


def _paper_user_prompt(paper: Paper, extraction: dict) -> str:
    extraction_text = json.dumps(extraction, ensure_ascii=False, indent=2)
    parts = [
        f"# 论文文件名: {paper.filename}",
        f"# 论文标题: {paper.title or '(未抽取)'}",
        f"# 作者: {', '.join(paper.authors or []) or '(未抽取)'}",
        "",
        "## 抽取 JSON",
        "```json",
        extraction_text[:8000],
        "```",
    ]
    if paper.notes:
        parts += ["", "## 用户笔记", paper.notes[:4000]]
    parts += [
        "",
        "请输出 wiki 条目。建议章节（仅在材料覆盖时才写，没有就跳过）：",
        "## 一句话定位 / ## 核心贡献 / ## 方法 / ## 实验与结论 / ## 限制与待解 / ## 涉及概念",
    ]
    return "\n".join(parts)


def _paper_page_title(paper: Paper) -> str:
    return paper.title or Path(paper.filename or f"paper-{paper.id}").stem


def _paper_page_path(paper: Paper) -> Path:
    slug = _slugify(_paper_page_title(paper), fallback=f"paper-{paper.id}")
    return WIKI_PAPERS_DIR / f"{paper.id:04d}-{slug}.md"


def _concept_page_path(node: KnowledgeNode) -> Path:
    slug = _slugify(node.title, fallback=f"concept-{node.id}")
    return WIKI_CONCEPTS_DIR / f"{node.id:04d}-{slug}.md"


def _cleanup_same_id_pages(
    base_dir: Path,
    id_key: str,
    ident: int,
    keep_path: Path,
) -> List[str]:
    removed: List[str] = []
    if not base_dir.exists():
        return removed

    for candidate in base_dir.glob("*.md"):
        if candidate == keep_path:
            continue
        meta = _read_frontmatter_from_path(candidate)
        if not meta or meta.get(id_key) != ident:
            continue
        try:
            candidate.unlink()
            removed.append(candidate.name)
        except OSError:
            continue
    return removed


def _reconcile_generated_pages(
    base_dir: Path,
    id_key: str,
    expected_paths_by_id: dict[int, Path],
    prune_orphans: bool = True,
) -> dict:
    """Clean stale wiki files on disk without calling the LLM.

    Rules:
      - If an id no longer exists in the raw layer, remove every page for it.
      - If multiple files share the same id, keep the current expected slug if
        present; otherwise keep the newest compiled_at file.
      - If exactly one legacy file remains for a still-valid id but its slug no
        longer matches the current expected path, keep it for now. The next
        successful compile for that id will write the new file and self-clean.
    """
    removed: List[str] = []
    duplicate_removed = 0
    orphan_removed = 0
    if not base_dir.exists():
        return {
            "removed": removed,
            "removed_count": 0,
            "duplicate_removed": 0,
            "orphan_removed": 0,
        }

    grouped: dict[int, List[Tuple[Path, dict]]] = {}
    for path in base_dir.glob("*.md"):
        meta = _read_frontmatter_from_path(path)
        if not meta:
            continue
        ident = meta.get(id_key)
        if not isinstance(ident, int):
            continue
        grouped.setdefault(ident, []).append((path, meta))

    for ident, entries in grouped.items():
        expected_path = expected_paths_by_id.get(ident)
        if expected_path is None:
            if not prune_orphans:
                continue
            for path, _ in entries:
                try:
                    path.unlink()
                    removed.append(path.name)
                    orphan_removed += 1
                except OSError:
                    continue
            continue

        if len(entries) == 1 and entries[0][0].name != expected_path.name:
            continue

        keep_path: Optional[Path] = None
        for path, _ in entries:
            if path.name == expected_path.name:
                keep_path = path
                break
        if keep_path is None:
            keep_path = max(
                entries,
                key=lambda item: (
                    str(item[1].get("compiled_at") or ""),
                    item[0].name,
                ),
            )[0]

        for path, _ in entries:
            if path == keep_path:
                continue
            try:
                path.unlink()
                removed.append(path.name)
                duplicate_removed += 1
            except OSError:
                continue

    return {
        "removed": removed,
        "removed_count": len(removed),
        "duplicate_removed": duplicate_removed,
        "orphan_removed": orphan_removed,
    }


def reconcile_paper_pages_dir(db: Session, prune_orphans: bool = True) -> dict:
    processed = db.query(Paper).filter(Paper.processed.is_(True)).all()
    expected = {paper.id: _paper_page_path(paper) for paper in processed}
    return _reconcile_generated_pages(
        WIKI_PAPERS_DIR,
        "paper_id",
        expected,
        prune_orphans=prune_orphans,
    )


def reconcile_concept_pages_dir(db: Session, prune_orphans: bool = True) -> dict:
    nodes = list_publishable_concept_nodes(db)
    expected = {node.id: _concept_page_path(node) for node in nodes}
    return _reconcile_generated_pages(
        WIKI_CONCEPTS_DIR,
        "concept_id",
        expected,
        prune_orphans=prune_orphans,
    )


def compile_paper_page(paper: Paper, api_key: str, model: str) -> Optional[Path]:
    """Generate / refresh wiki/papers/{id}-{slug}.md from this paper's
    extraction + notes. Returns None if there is nothing to compile yet
    (e.g. paper is unprocessed)."""
    if not paper.processed or not paper.raw_llm_response:
        return None

    try:
        extraction = json.loads(paper.raw_llm_response)
    except (TypeError, json.JSONDecodeError):
        extraction = None
    if not isinstance(extraction, dict):
        extraction = {"_raw": str(paper.raw_llm_response)[:4000]}

    WIKI_PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    path = _paper_page_path(paper)
    signature = _paper_compile_signature(paper, model)
    if path.is_file():
        existing_meta = _read_frontmatter_from_path(path) or {}
        if existing_meta.get("source_signature") == signature:
            _cleanup_same_id_pages(WIKI_PAPERS_DIR, "paper_id", paper.id, path)
            return path

    client = OpenAI(api_key=api_key)
    body = _call_llm(
        client,
        model,
        PAPER_PAGE_SYSTEM,
        _paper_user_prompt(paper, extraction),
    )

    title = _paper_page_title(paper)
    slug = _slugify(title, fallback=f"paper-{paper.id}")

    record_stem = _slugify(Path(paper.filename or "").stem, fallback=f"paper-{paper.id}")
    meta = {
        "kind": "paper",
        "title": title,
        "paper_id": paper.id,
        "slug": slug,
        "authors": list(paper.authors or []),
        "paper_category": effective_paper_category(paper, extraction if isinstance(extraction, dict) else None),
        "compiled_at": _now_iso(),
        "compile_model": model,
        "source_signature": signature,
        "source_record": f"data/paper_records/{paper.id:04d}-{record_stem}.md",
    }
    page = _render_frontmatter(meta) + f"\n# {title}\n\n" + body.strip() + "\n"
    path.write_text(page, encoding="utf-8")
    removed = _cleanup_same_id_pages(WIKI_PAPERS_DIR, "paper_id", paper.id, path)
    if removed:
        _log(f"paper={paper.id} pruned stale wiki files: {', '.join(removed)}")
    return path


# --- compile_concept_pages --------------------------------------------------

CONCEPT_PAGE_SYSTEM = (
    "你是个人 LLM wiki 的编译器。给定一个概念名称、它的简介，以及多篇论文中"
    "对它的描述片段，用中文写一篇综述式的概念条目。要求：\n"
    "1. 不要逐篇复述每篇论文；做横向综合：共识、分歧、未解。\n"
    "2. 引用某篇论文时使用内联标记 [[paper:{id}]]，例如 “…… [[paper:42]] 提出 ……”。\n"
    "3. 用 markdown，正文小节从 ## 二级标题开始。\n"
    "4. 如果只有 1 篇论文，直接做精简总结，不要硬凑结构。\n"
    "5. 不要编造未在输入材料中出现的内容。"
)


def _concept_user_prompt(node: KnowledgeNode, snippets: List[Tuple[int, str]]) -> str:
    parts = [
        f"# 概念名称: {node.title}",
        f"# 概念类型: {node.node_type}",
        f"# 简介: {(node.content or '')[:1500]}",
        "",
        f"# 涉及该概念的论文 ({len(snippets)} 篇):",
    ]
    for paper_id, snippet in snippets:
        parts.append(f"\n## paper:{paper_id}\n{snippet[:2500]}")
    parts.append(
        "\n请输出概念条目。建议章节（仅在材料覆盖时才写）：## 定义 / ## 不同视角 / ## 共识与分歧 / ## 进一步阅读"
    )
    return "\n".join(parts)


def _node_paper_ids(node: KnowledgeNode) -> List[int]:
    return _normalize_int_list(node.source_paper_ids)


def _snippet_for_paper(paper: Paper) -> str:
    """Compact representation of a paper used as input to concept compile.
    Prefer high-signal extraction fields; fall back to title."""
    if paper.raw_llm_response:
        try:
            ex = json.loads(paper.raw_llm_response)
        except (TypeError, json.JSONDecodeError):
            ex = None
        if isinstance(ex, dict):
            bits = []
            for key in (
                "title",
                "problem_area",
                "abstract_summary",
                "core_contribution",
                "principle",
                "method",
                "contributions",
                "key_findings",
            ):
                val = ex.get(key)
                if val:
                    bits.append(f"{key}: {json.dumps(val, ensure_ascii=False)[:600]}")
            if bits:
                return "\n".join(bits)
    return f"title: {paper.title or paper.filename}"


def compile_concept_page(
    node: KnowledgeNode,
    db: Session,
    api_key: str,
    model: str,
) -> Optional[Path]:
    processed_ids = {
        row[0] for row in db.query(Paper.id).filter(Paper.processed.is_(True)).all()
    }
    if not is_publishable_concept_node(node, processed_ids):
        return None

    papers = _published_concept_source_papers(node, db)
    snippets = [(p.id, _snippet_for_paper(p)) for p in papers]
    if not snippets:
        return None

    WIKI_CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _concept_page_path(node)
    slug = _slugify(node.title, fallback=f"concept-{node.id}")
    signature = _concept_compile_signature(node, papers, model)
    if path.is_file():
        existing_meta = _read_frontmatter_from_path(path) or {}
        if existing_meta.get("source_signature") == signature:
            _cleanup_same_id_pages(WIKI_CONCEPTS_DIR, "concept_id", node.id, path)
            return path

    client = OpenAI(api_key=api_key)
    body = _call_llm(
        client,
        model,
        CONCEPT_PAGE_SYSTEM,
        _concept_user_prompt(node, snippets),
        max_tokens=1500,
    )

    meta = {
        "kind": "concept",
        "title": node.title,
        "concept_id": node.id,
        "slug": slug,
        "node_type": node.node_type,
        "concept_origin": node_origin(node),
        "tags": list(node.tags or []),
        "source_paper_ids": [pid for pid, _ in snippets],
        "compiled_at": _now_iso(),
        "compile_model": model,
        "source_signature": signature,
    }
    page = _render_frontmatter(meta) + f"\n# {node.title}\n\n" + body.strip() + "\n"
    path.write_text(page, encoding="utf-8")
    removed = _cleanup_same_id_pages(WIKI_CONCEPTS_DIR, "concept_id", node.id, path)
    if removed:
        _log(f"concept={node.id} pruned stale wiki files: {', '.join(removed)}")
    return path


PaperProgress = Any  # Callable[[int, int, Paper, Optional[Path], Optional[BaseException]], None]
ConceptProgress = Any  # Callable[[int, int, KnowledgeNode, Optional[Path], Optional[BaseException]], None]


def compile_concept_pages_for_paper(
    paper_id: int,
    db: Session,
    api_key: str,
    model: str,
) -> List[Path]:
    """Recompile every concept page that references this paper. Used as the
    incremental hook after a paper finishes processing."""
    nodes = [n for n in list_publishable_concept_nodes(db) if paper_id in _node_paper_ids(n)]
    written: List[Path] = []
    for node in nodes:
        try:
            p = compile_concept_page(node, db, api_key, model)
            if p:
                written.append(p)
        except Exception as e:
            _log(f"concept compile failed for node {node.id}: {e!r}")
            traceback.print_exc(file=sys.stderr)
    return written


def compile_all_paper_pages(
    db: Session,
    api_key: str,
    model: str,
    on_progress: Optional[PaperProgress] = None,
) -> List[Path]:
    """Compile a wiki page for every paper that has been processed.

    on_progress(idx, total, paper, path_or_None, exc_or_None) is called once
    per paper — used by the router to surface live status to the frontend.
    """
    papers = db.query(Paper).filter(Paper.processed.is_(True)).all()
    total = len(papers)
    _log(f"compile_all_paper_pages start: {total} papers, model={model}")
    written: List[Path] = []
    for idx, paper in enumerate(papers, start=1):
        path: Optional[Path] = None
        err: Optional[BaseException] = None
        try:
            path = compile_paper_page(paper, api_key, model)
            if path:
                written.append(path)
                _log(f"[{idx}/{total}] paper={paper.id} -> {path.name}")
            else:
                _log(f"[{idx}/{total}] paper={paper.id} skipped (no extraction)")
        except Exception as e:
            err = e
            _log(f"[{idx}/{total}] paper={paper.id} FAILED: {e!r}")
            traceback.print_exc(file=sys.stderr)
        if on_progress is not None:
            try:
                on_progress(idx, total, paper, path, err)
            except Exception as cb_err:
                _log(f"on_progress callback raised: {cb_err!r}")
    _log(f"compile_all_paper_pages done: {len(written)}/{total}")
    return written


def compile_all_concept_pages(
    db: Session,
    api_key: str,
    model: str,
    on_progress: Optional[ConceptProgress] = None,
) -> List[Path]:
    nodes = list_publishable_concept_nodes(db)
    total = len(nodes)
    _log(f"compile_all_concept_pages start: {total} concepts, model={model}")
    written: List[Path] = []
    for idx, node in enumerate(nodes, start=1):
        path: Optional[Path] = None
        err: Optional[BaseException] = None
        try:
            path = compile_concept_page(node, db, api_key, model)
            if path:
                written.append(path)
                _log(f"[{idx}/{total}] concept={node.id} -> {path.name}")
            else:
                _log(f"[{idx}/{total}] concept={node.id} skipped (no source papers)")
        except Exception as e:
            err = e
            _log(f"[{idx}/{total}] concept={node.id} FAILED: {e!r}")
            traceback.print_exc(file=sys.stderr)
        if on_progress is not None:
            try:
                on_progress(idx, total, node, path, err)
            except Exception as cb_err:
                _log(f"on_progress callback raised: {cb_err!r}")
    _log(f"compile_all_concept_pages done: {len(written)}/{total}")
    return written


# --- listing / reading (used by router) ------------------------------------

# Common subset of frontmatter fields surfaced to the UI for both kinds.
# Kind-specific extras (concept_id / paper_id / authors / node_type / tags /
# source_paper_ids) are passed through if present so callers can pick what
# they need without us hardcoding two near-identical readers.
_PASSTHROUGH_META_KEYS = (
    "kind", "title", "slug", "compiled_at", "compile_model",
    "concept_id", "paper_id", "node_type", "tags",
    "source_paper_ids", "authors", "source_record",
    "concept_origin", "source_signature",
)


def _summarize_page(path: Path, rel_dir: str) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _ = _parse_frontmatter(text)
    summary: dict = {
        "filename": path.name,
        # Project-relative path, matches the `data/...` convention used by
        # source_record / paper.filepath so the user sees a consistent prefix.
        "path": f"data/{rel_dir}/{path.name}",
        # Absolute filesystem path — copy-pastable into Finder / Obsidian /
        # `cat` without ambiguity.
        "disk_path": str(path.resolve()),
        "title": meta.get("title") or path.stem,
        "size": path.stat().st_size,
    }
    for key in _PASSTHROUGH_META_KEYS:
        if key in meta and key not in summary:
            summary[key] = meta[key]
    summary.setdefault("tags", meta.get("tags") or [])
    summary.setdefault("source_paper_ids", meta.get("source_paper_ids") or [])
    return summary


def _list_pages(base_dir: Path, rel_dir: str) -> List[dict]:
    if not base_dir.exists():
        return []
    out: List[dict] = []
    for path in base_dir.glob("*.md"):
        s = _summarize_page(path, rel_dir)
        if s:
            out.append(s)
    out.sort(key=lambda x: x.get("compiled_at") or "", reverse=True)
    return out


def _read_page(base_dir: Path, rel_dir: str, filename: str) -> Optional[dict]:
    safe = Path(filename).name
    if safe != filename or not safe:
        return None
    path = base_dir / safe
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    detail: dict = {
        "filename": safe,
        "path": f"data/{rel_dir}/{safe}",
        "disk_path": str(path.resolve()),
        "size": path.stat().st_size,
        "title": meta.get("title") or path.stem,
        "frontmatter": meta,
        "body": body.strip(),
        "raw": text,
    }
    for key in _PASSTHROUGH_META_KEYS:
        if key in meta and key not in detail:
            detail[key] = meta[key]
    detail.setdefault("tags", meta.get("tags") or [])
    detail.setdefault("source_paper_ids", meta.get("source_paper_ids") or [])
    return detail


def list_concept_pages() -> List[dict]:
    return _list_pages(WIKI_CONCEPTS_DIR, "wiki/concepts")


def list_paper_pages() -> List[dict]:
    return _list_pages(WIKI_PAPERS_DIR, "wiki/papers")


def read_concept_page(filename: str) -> Optional[dict]:
    return _read_page(WIKI_CONCEPTS_DIR, "wiki/concepts", filename)


def read_paper_page(filename: str) -> Optional[dict]:
    return _read_page(WIKI_PAPERS_DIR, "wiki/papers", filename)


# --- freshness ---------------------------------------------------------------

# Caps on how many entries we surface in each category. Counts are always
# accurate; the lists are for UI rendering (banner + per-row dots) and don't
# need to be exhaustive once you've got 500+ items in any single bucket.
_FRESHNESS_LIST_CAP = 200


def _to_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return _to_aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def compute_freshness_summary(db: Session) -> dict:
    """Diff the wiki layer against the raw layer and return per-kind buckets.

    A wiki page is considered:
      - missing : raw record (paper / KnowledgeNode) exists but no .md on disk
      - stale   : .md exists but its compiled_at predates the source's last
                  meaningful update (paper.processed_at, or — for concept
                  pages — the newest processed_at among the concept's source
                  papers).
      - orphan  : .md on disk references an id that no longer exists in the DB
      - ok      : everything else (counts only, no list)

    Used by the Wiki UI to surface a "what needs recompiling" banner so the
    user doesn't have to remember whether they reprocessed papers since the
    last compile run.
    """
    papers = db.query(Paper).filter(Paper.processed.is_(True)).all()
    nodes = list_publishable_concept_nodes(db)

    paper_by_id = {p.id: p for p in papers}
    node_ids = {n.id for n in nodes}

    paper_pages = list_paper_pages()
    paper_pages_by_id: dict[int, List[dict]] = {}
    for pp in paper_pages:
        pid = pp.get("paper_id")
        if isinstance(pid, int):
            paper_pages_by_id.setdefault(pid, []).append(pp)

    concept_pages = list_concept_pages()
    concept_pages_by_id: dict[int, List[dict]] = {}
    for cp in concept_pages:
        cid = cp.get("concept_id")
        if isinstance(cid, int):
            concept_pages_by_id.setdefault(cid, []).append(cp)

    # --- paper page freshness ---
    paper_missing: List[dict] = []
    paper_stale: List[dict] = []
    paper_ok = 0
    for paper in papers:
        title = paper.title or paper.filename or f"paper-{paper.id}"
        expected_name = _paper_page_path(paper).name
        current_signature = _paper_compile_signature(paper)
        candidates = paper_pages_by_id.get(paper.id) or []
        wiki = _pick_preferred_page(candidates, expected_name=expected_name)
        if not wiki:
            paper_missing.append({
                "paper_id": paper.id,
                "title": title,
                "processed_at": _to_aware(paper.processed_at).isoformat() if paper.processed_at else None,
            })
            continue
        compiled_at = _parse_iso(wiki.get("compiled_at"))
        processed_at = _to_aware(paper.processed_at)
        reasons: List[str] = []
        if len(candidates) > 1:
            reasons.append("duplicate_files")
        if wiki.get("title") != title or wiki.get("filename") != expected_name:
            reasons.append("identity_changed")
        if wiki.get("source_signature") != current_signature:
            reasons.append("source_signature_changed")
        if processed_at and compiled_at and processed_at > compiled_at:
            reasons.append("source_newer")
        if reasons:
            paper_stale.append({
                "paper_id": paper.id,
                "title": title,
                "filename": wiki.get("filename"),
                "processed_at": processed_at.isoformat(),
                "compiled_at": compiled_at.isoformat() if compiled_at else None,
                "reasons": reasons,
            })
            continue
        paper_ok += 1

    paper_orphan: List[dict] = []
    for pp in paper_pages:
        pid = pp.get("paper_id")
        if not isinstance(pid, int) or pid not in paper_by_id:
            paper_orphan.append({
                "filename": pp["filename"],
                "title": pp.get("title"),
            })

    # --- concept page freshness ---
    concept_missing: List[dict] = []
    concept_stale: List[dict] = []
    concept_ok = 0
    for node in nodes:
        expected_name = _concept_page_path(node).name
        source_papers = _published_concept_source_papers(node, db)
        current_signature = _concept_compile_signature(node, source_papers)
        candidates = concept_pages_by_id.get(node.id) or []
        wiki = _pick_preferred_page(candidates, expected_name=expected_name)
        if not wiki:
            concept_missing.append({
                "concept_id": node.id,
                "title": node.title,
                "node_type": node.node_type,
            })
            continue
        compiled_at = _parse_iso(wiki.get("compiled_at"))
        # Newest processed_at among source papers — that's the "last time
        # the raw evidence under this concept changed".
        source_ids_raw = node.source_paper_ids or []
        if not isinstance(source_ids_raw, list):
            source_ids_raw = [source_ids_raw]
        newest: Optional[datetime] = None
        for sid in source_ids_raw:
            try:
                pid = int(sid)
            except (TypeError, ValueError):
                continue
            paper = paper_by_id.get(pid)
            if paper is None:
                continue
            ts = _to_aware(paper.processed_at)
            if ts is not None and (newest is None or ts > newest):
                newest = ts
        current_source_ids = sorted(
            pid for pid in _normalize_int_list(node.source_paper_ids) if pid in paper_by_id
        )
        wiki_source_ids = sorted(_normalize_int_list(wiki.get("source_paper_ids")))
        reasons: List[str] = []
        if len(candidates) > 1:
            reasons.append("duplicate_files")
        if (
            wiki.get("title") != node.title
            or wiki.get("filename") != expected_name
            or wiki.get("node_type") != node.node_type
        ):
            reasons.append("identity_changed")
        if wiki.get("source_signature") != current_signature:
            reasons.append("source_signature_changed")
        if current_source_ids != wiki_source_ids:
            reasons.append("source_papers_changed")
        if compiled_at and newest and newest > compiled_at:
            reasons.append("source_newer")
        if reasons:
            concept_stale.append({
                "concept_id": node.id,
                "title": node.title,
                "filename": wiki.get("filename"),
                "compiled_at": compiled_at.isoformat() if compiled_at else None,
                "newest_source_processed_at": newest.isoformat() if newest else None,
                "reasons": reasons,
            })
            continue
        concept_ok += 1

    concept_orphan: List[dict] = []
    for cp in concept_pages:
        cid = cp.get("concept_id")
        if not isinstance(cid, int) or cid not in node_ids:
            concept_orphan.append({
                "filename": cp["filename"],
                "title": cp.get("title"),
            })

    def _cap(items: List[dict]) -> List[dict]:
        return items[:_FRESHNESS_LIST_CAP]

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "papers": {
            "ok": paper_ok,
            "total_processed": len(papers),
            "missing_count": len(paper_missing),
            "stale_count": len(paper_stale),
            "orphan_count": len(paper_orphan),
            "missing": _cap(paper_missing),
            "stale": _cap(paper_stale),
            "orphan": _cap(paper_orphan),
        },
        "concepts": {
            "ok": concept_ok,
            "total_nodes": len(nodes),
            "missing_count": len(concept_missing),
            "stale_count": len(concept_stale),
            "orphan_count": len(concept_orphan),
            "missing": _cap(concept_missing),
            "stale": _cap(concept_stale),
            "orphan": _cap(concept_orphan),
        },
    }
