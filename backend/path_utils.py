from pathlib import Path
from typing import List, Optional, Union


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PAPERS_DIR = DATA_DIR / "papers"
ARTIFACTS_DIR = DATA_DIR / "artifacts"


PathLike = Union[str, Path]


def _normalized_parts(path: PathLike) -> List[str]:
    raw = str(path).replace("\\", "/")
    return [part for part in raw.split("/") if part]


def _tail_after_data_subdir(path: PathLike, subdir: str) -> Optional[Path]:
    parts = _normalized_parts(path)
    for idx in range(len(parts) - 1):
        if parts[idx] == "data" and parts[idx + 1] == subdir:
            tail = parts[idx + 2 :]
            if tail:
                return Path(*tail)
            return Path()
    return None


def portable_data_path(path: PathLike) -> str:
    """Return a project-relative data path when possible.

    Old native runs stored absolute host paths like
    /Users/.../knowledge-wiki/data/papers/a.pdf. Containers and other machines
    need those represented as data/papers/a.pdf instead.
    """
    parts = _normalized_parts(path)
    for idx, part in enumerate(parts):
        if part == "data" and idx + 1 < len(parts):
            return Path(*parts[idx:]).as_posix()

    p = Path(path)
    candidate = p if p.is_absolute() else PROJECT_ROOT / p
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        pass
    return str(path)


def _resolve_under_data_subdir(
    path: PathLike,
    subdir: str,
    base_dir: Path,
) -> Path:
    p = Path(path)
    candidates: List[Path] = []
    preferred: Optional[Path] = None

    tail = _tail_after_data_subdir(path, subdir)
    if tail is not None:
        preferred = base_dir / tail
        candidates.append(preferred)

    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(PROJECT_ROOT / p)
        if len(p.parts) == 1:
            candidates.append(base_dir / p.name)

    if not preferred and p.name:
        preferred = base_dir / p.name
        candidates.append(preferred)

    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate

    return preferred or candidates[0]


def resolve_paper_path(path: PathLike) -> Path:
    return _resolve_under_data_subdir(path, "papers", PAPERS_DIR)


def resolve_artifact_path(path: PathLike) -> Path:
    return _resolve_under_data_subdir(path, "artifacts", ARTIFACTS_DIR)


def resolve_papers_directory(path: PathLike) -> Path:
    p = Path(path)
    tail = _tail_after_data_subdir(path, "papers")
    if tail is not None:
        return PAPERS_DIR / tail
    if p.is_absolute():
        return p
    candidate = PROJECT_ROOT / p
    if candidate.exists():
        return candidate
    return candidate
