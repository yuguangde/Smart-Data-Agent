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
        'version: "0.2.0.dev0"\n'
        "semantic_model:\n"
        "  - name: model_a\n"
        "    datasets:\n"
        "      - name: ds1\n"
        "        source: t1\n"
        "        primary_key: [id]\n"
        "        fields:\n"
        "          - name: id\n"
        "            expression:\n"
        "              dialects:\n"
        "                - dialect: ANSI_SQL\n"
        "                  expression: ds1.id\n"
        "          - name: x\n"
        "            expression:\n"
        "              dialects:\n"
        "                - dialect: ANSI_SQL\n"
        "                  expression: ds1.x\n"
        "    metrics:\n"
        "      - name: m1\n"
        "        expression:\n"
        "          dialects:\n"
        "            - dialect: ANSI_SQL\n"
        "              expression: SUM(ds1.x)\n",
        encoding="utf-8",
    )
    (d / "b.ossie.yml").write_text(
        'version: "0.2.0.dev0"\n'
        "semantic_model:\n"
        "  - name: model_b\n"
        "    datasets:\n"
        "      - name: ds2\n"
        "        source: t2\n"
        "        primary_key: [id]\n"
        "        fields:\n"
        "          - name: id\n"
        "            expression:\n"
        "              dialects:\n"
        "                - dialect: ANSI_SQL\n"
        "                  expression: ds2.id\n"
        "          - name: y\n"
        "            expression:\n"
        "              dialects:\n"
        "                - dialect: ANSI_SQL\n"
        "                  expression: ds2.y\n"
        "    metrics:\n"
        "      - name: m2\n"
        "        expression:\n"
        "          dialects:\n"
        "            - dialect: ANSI_SQL\n"
        "              expression: SUM(ds2.y)\n",
        encoding="utf-8",
    )
    return d


def test_build_dosi_model_combines_files(ossie_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "dosi-model.yaml"
    result = build_dosi_model(
        ossie_dir=ossie_dir,
        output_path=output,
        file_patterns=["*.ossie.yml"],
    )
    assert result == output
    assert output.exists()

    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert "version" in data
    assert "semantic_model" in data
    # DoSI expects exactly one semantic_model per document, so multiple OSSIE
    # files are merged into one.
    assert len(data["semantic_model"]) == 1
    model = data["semantic_model"][0]
    dataset_names = {d["name"] for d in model["datasets"]}
    metric_names = {m["name"] for m in model["metrics"]}
    assert dataset_names == {"ds1", "ds2"}
    assert metric_names == {"m1", "m2"}
