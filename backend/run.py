"""Convenience launcher: `python run.py`.

For local development. In production use gunicorn/uvicorn CLI directly.
"""
from __future__ import annotations

from app.config import get_settings

if __name__ == "__main__":
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        reload=True,
        http="h11",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )