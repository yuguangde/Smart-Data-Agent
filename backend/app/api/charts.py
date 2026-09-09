"""Proxy endpoint for local Plotly HTML files produced by MCP tools.

MCP chart tools (e.g. ``starrocks_query_and_plotly_chart``) write self-contained
HTML files to the local temp directory and return a ``file://`` URL. Browsers
forbid web pages from loading ``file://`` resources, so this endpoint lets the
frontend request the same file through an HTTP URL.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["charts"])

MAX_CHART_BYTES = 8 * 1024 * 1024  # 8 MB


def _allowed_roots() -> set[Path]:
    """Filesystem roots we are willing to serve local chart files from."""
    roots = {Path(tempfile.gettempdir()).resolve()}
    settings = get_settings()
    extra = getattr(settings, "mcp_chart_allowed_roots", "")
    if extra:
        for part in str(extra).split(","):
            p = Path(part.strip()).expanduser().resolve()
            if p.exists() and p.is_dir():
                roots.add(p)
    return roots


@router.get("/charts/proxy")
async def proxy_chart(
    url: str = Query(..., description="file:// URL of the generated chart"),
) -> Response:
    """Return the contents of a local chart HTML file."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise HTTPException(status_code=400, detail="only file:// URLs are supported")

    raw_path = unquote(parsed.path)
    if not raw_path.endswith(".html"):
        raise HTTPException(status_code=400, detail="only .html files are allowed")

    target = Path(raw_path).resolve()
    allowed = _allowed_roots()
    if not any(target.is_relative_to(root) for root in allowed):
        logger.warning("Rejected chart proxy outside allowed roots: %s", target)
        raise HTTPException(status_code=403, detail="path outside allowed roots")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="chart file not found")

    size = target.stat().st_size
    if size > MAX_CHART_BYTES:
        raise HTTPException(status_code=413, detail="chart file too large")

    try:
        content = target.read_bytes()
    except Exception as exc:
        logger.warning("Failed to read chart %s: %s", target, exc)
        raise HTTPException(status_code=500, detail="failed to read chart file") from exc

    return Response(content=content, media_type="text/html")
