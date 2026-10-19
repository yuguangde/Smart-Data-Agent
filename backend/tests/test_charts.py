"""Tests for the chart proxy endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_proxy_chart_happy_path(client: TestClient) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write("<html><body>Hello Plotly</body></html>")
        tmp_path = f.name

    try:
        resp = client.get("/api/charts/proxy", params={"url": f"file://{tmp_path}"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert resp.text == "<html><body>Hello Plotly</body></html>"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_proxy_chart_rejects_non_file_scheme(client: TestClient) -> None:
    resp = client.get(
        "/api/charts/proxy",
        params={"url": "http://evil.com/chart.html"},
    )
    assert resp.status_code == 400


def test_proxy_chart_rejects_non_html_extension(client: TestClient) -> None:
    resp = client.get(
        "/api/charts/proxy",
        params={"url": "file:///tmp/chart.txt"},
    )
    assert resp.status_code == 400


def test_proxy_chart_rejects_path_traversal(client: TestClient) -> None:
    resp = client.get(
        "/api/charts/proxy",
        params={"url": "file:///etc/passwd.html"},
    )
    assert resp.status_code == 403


def test_proxy_chart_rejects_outside_allowed_dir(client: TestClient) -> None:
    tmpdir = tempfile.mkdtemp(dir="/tmp")
    try:
        chart_path = Path(tmpdir) / "chart.html"
        chart_path.write_text("<html></html>")
        resp = client.get(
            "/api/charts/proxy",
            params={"url": f"file://{chart_path}"},
        )
        assert resp.status_code == 403
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_proxy_chart_returns_404_for_missing_file(client: TestClient) -> None:
    missing = Path(tempfile.gettempdir()) / "starrocks_chart_missing.html"
    resp = client.get(
        "/api/charts/proxy",
        params={"url": f"file://{missing}"},
    )
    assert resp.status_code == 404
