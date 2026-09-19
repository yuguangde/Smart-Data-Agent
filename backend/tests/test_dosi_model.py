"""Tests for OSSIE -> DoSI model combination."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.semantic.dosi_model import build_dosi_model


@pytest.fixture
def ossie_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ossie"
    d.mkdir()
    (d / "a.ossie.yml").write_text(
        "semantic_model:\n  - name: model_a\n    datasets:\n      - name: ds1\n        source: t1\n        dimensions: [x]\n        metrics:\n          - name: m1\n            expression: SUM(x)\n",
        encoding="utf-8",
    )
    (d / "b.ossie.yml").write_text(
        "semantic_model:\n  - name: model_b\n    datasets:\n      - name: ds2\n        source: t2\n        dimensions: [y]\n        metrics:\n          - name: m2\n            expression: SUM(y)\n",
        encoding="utf-8",
    )
    return d


def test_build_dosi_model_combines_files(ossie_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "dosi-model.yaml"
    result = build_dosi_model(ossie_dir=ossie_dir, output_path=output)
    assert result == output
    assert output.exists()

    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert "semantic_model" in data
    assert len(data["semantic_model"]) == 2
    names = {m["name"] for m in data["semantic_model"]}
    assert names == {"model_a", "model_b"}
