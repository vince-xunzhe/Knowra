"""Team/lab classification — a second grouping axis parallel to paper_category.

Unlike category (which comes from the LLM's own semantic judgement), a *team*
can only be known by who wrote the paper: world-class labs (Kaiming He, Yann
LeCun, Fei-Fei Li, Saining Xie, …) are identified by their core authors. So a
paper is auto-assigned to a team by matching its extracted ``authors`` against
an editable registry of "team name → core authors". Manual override wins; a
paper that matches no team falls back to ``others``.

Mirrors services.paper_category_service (model value + override, config-backed
editable list, cached) so the desktop / mobile grouping UI can treat the two
dimensions symmetrically.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# Structural fallback for papers that match no team. Never a stored entry.
TEAM_OTHER = "others"

# Built-in seed teams. The *active* list is user-editable and persisted in
# config under "paper_teams"; it seeds from these defaults.
DEFAULT_TEAMS: list[dict] = [
    {"name": "Kaiming He", "authors": ["Kaiming He"]},
    {"name": "Yann LeCun", "authors": ["Yann LeCun"]},
    {"name": "Fei-Fei Li", "authors": ["Fei-Fei Li", "Li Fei-Fei"]},
    {"name": "Saining Xie", "authors": ["Saining Xie"]},
]

_active_cache: Optional[list] = None


def _name_key(value: Any) -> str:
    """Normalise an author name for matching: lowercase, drop spaces/punct."""
    return re.sub(r"[\s.\-_,]+", "", str(value or "").strip().lower())


def _clean_team_list(raw: Any) -> list:
    """Normalise a registry into a list of {name, authors[]}, deduped by name.
    ``others`` is structural and never stored as a team."""
    out: list = []
    seen: set = set()
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            authors_raw = item.get("authors") or []
        else:
            name = str(item or "").strip()
            authors_raw = []
        if not name or name.lower() == TEAM_OTHER or name in seen:
            continue
        seen.add(name)
        authors: list = []
        aseen: set = set()
        for a in authors_raw:
            s = str(a or "").strip()
            k = _name_key(s)
            if s and k and k not in aseen:
                aseen.add(k)
                authors.append(s)
        out.append({"name": name, "authors": authors})
    return out


def get_active_teams() -> list:
    """Active, user-editable team registry (config-backed, cached). Falls back
    to DEFAULT_TEAMS. Call refresh_active_teams() after mutating config."""
    global _active_cache
    if _active_cache is not None:
        return _active_cache
    teams = None
    try:
        from config import load_config

        raw = load_config().get("paper_teams")
        if isinstance(raw, list) and raw:
            teams = _clean_team_list(raw)
    except Exception:
        teams = None
    if teams is None:
        teams = _clean_team_list(DEFAULT_TEAMS)
    _active_cache = teams
    return teams


def get_active_team_names() -> list:
    return [t["name"] for t in get_active_teams()]


def refresh_active_teams() -> None:
    global _active_cache
    _active_cache = None


def set_active_teams(teams: list) -> list:
    """Persist a new team registry to config; returns the cleaned list."""
    cleaned = _clean_team_list(teams)
    from config import save_config

    save_config({"paper_teams": cleaned})
    refresh_active_teams()
    return get_active_teams()


def normalize_team(value: Any) -> Optional[str]:
    """Resolve a raw value to a currently-active team name, or None. ``others``
    resolves to None (it's the fallback, not an assignable team)."""
    raw = str(value or "").strip()
    if not raw or raw.lower() == TEAM_OTHER:
        return None
    names = get_active_team_names()
    if raw in names:
        return raw
    low = raw.lower()
    for name in names:
        if name.lower() == low:
            return name
    return None


def _author_names(obj: Any) -> list:
    """Tolerantly pull author-name strings from a paper.authors / extraction
    value (list of strings, or list of {name|author|full_name})."""
    names: list = []
    if isinstance(obj, list):
        for a in obj:
            if isinstance(a, str):
                s = a.strip()
            elif isinstance(a, dict):
                s = str(a.get("name") or a.get("author") or a.get("full_name") or "").strip()
            else:
                s = str(a or "").strip()
            if s:
                names.append(s)
    return names


def _paper_author_keys(paper: Any, extraction: Optional[dict]) -> set:
    names = _author_names(getattr(paper, "authors", None))
    if not names and isinstance(extraction, dict):
        names = _author_names(extraction.get("authors"))
    return {_name_key(n) for n in names if _name_key(n)}


def derive_model_paper_team(paper: Any, extraction: Optional[dict[str, Any]]) -> Optional[str]:
    """Auto-assign a team by matching the paper's authors against each team's
    core-author list. First team with any author overlap wins. No match → None
    (effective team then falls back to ``others``)."""
    author_keys = _paper_author_keys(paper, extraction)
    if not author_keys:
        return None
    for team in get_active_teams():
        for a in team.get("authors") or []:
            if _name_key(a) in author_keys:
                return team["name"]
    return None


def effective_paper_team(paper: Any, extraction: Optional[dict[str, Any]] = None) -> str:
    override = normalize_team(getattr(paper, "paper_team_override", None))
    if override:
        return override
    model_value = normalize_team(getattr(paper, "paper_team_model", None))
    if model_value:
        return model_value
    derived = derive_model_paper_team(paper, extraction)
    return derived or TEAM_OTHER


def sync_paper_team_fields(
    paper: Any,
    extraction: Optional[dict[str, Any]] = None,
    overwrite_model: bool = False,
) -> bool:
    """Recompute the stored model team from authors. Mirrors
    sync_paper_category_fields: override is normalised; model is (re)derived
    when missing, or always when overwrite_model=True (used by a manual
    'recompute teams' after the registry changes)."""
    changed = False

    normalized_override = normalize_team(getattr(paper, "paper_team_override", None))
    if getattr(paper, "paper_team_override", None) != normalized_override:
        paper.paper_team_override = normalized_override
        changed = True

    derived_model = derive_model_paper_team(paper, extraction)
    current_model = normalize_team(getattr(paper, "paper_team_model", None))
    next_model = current_model
    if overwrite_model:
        next_model = derived_model
    elif current_model is None and derived_model is not None:
        next_model = derived_model

    if getattr(paper, "paper_team_model", None) != next_model:
        paper.paper_team_model = next_model
        changed = True

    return changed


def paper_team_source(paper: Any) -> str:
    if normalize_team(getattr(paper, "paper_team_override", None)):
        return "manual"
    if normalize_team(getattr(paper, "paper_team_model", None)):
        return "model"
    return "none"
