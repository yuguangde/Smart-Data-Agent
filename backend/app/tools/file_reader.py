"""Read local project files for the agent.

Restricted to the project root to prevent path-traversal attacks.
Reads are limited to a configurable max character count to avoid
accidentally dumping huge logs or data files into the context.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

# Resolve to the repository root so callers can pass paths like
# "backend/ossie/mypa_service.ossie.yml".
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
ALLOWED_SUFFIXES = {".yml", ".yaml", ".md", ".txt", ".json", ".sql", ".py", ".cfg", ".ini"}


@tool
def read_file(path: str, max_chars: int = 8000) -> str:
    """Read a text file under the project directory and return its contents.

    Use this to load configuration, semantic-layer definitions, or small
    documentation files that the agent needs to answer questions. The path
    can be relative to the project root (e.g. "backend/ossie/mypa_service.ossie.yml")
    or absolute, but must stay inside the project.

    Args:
        path: File path, relative to the project root or absolute.
        max_chars: Maximum characters to return. Longer files are truncated
            and a "[truncated]" note is appended.
    """
    try:
        target = Path(path)
        if not target.is_absolute():
            target = BASE_DIR / target
        target = target.resolve()
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error: invalid path {path!r}: {exc}"

    # Prevent directory traversal outside the project.
    if BASE_DIR not in target.parents and target != BASE_DIR:
        return (
            f"Error: access denied. "
            f"The path {str(target)!r} is outside the project root {str(BASE_DIR)!r}."
        )

    if not target.exists():
        return f"Error: file not found: {str(target)!r}"
    if not target.is_file():
        return f"Error: {str(target)!r} is not a file"

    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        return (
            f"Error: reading {target.suffix!r} files is not allowed. "
            f"Allowed suffixes: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: {str(target)!r} is not a UTF-8 text file"
    except Exception as exc:
        return f"Error: could not read {str(target)!r}: {exc}"

    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[truncated]"
    return content


__all__ = ["read_file"]
