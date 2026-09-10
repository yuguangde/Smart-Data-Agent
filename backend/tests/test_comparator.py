"""Tests for the report comparator."""
from __future__ import annotations

import pytest

from app.agent.comparator import _extract_numerical_claims, _structural_diff


def test_extract_numerical_claims_picks_lines_with_numbers():
    report = """整体接通率为 98.30%。
总计呼入量 4,999 通。
日期 2026-09-08 数据完整。"""
    claims = _extract_numerical_claims(report)
    assert len(claims) == 3
    assert claims[0]["numbers"] == ["98.30%"]
    assert claims[1]["numbers"] == ["4,999"]
    assert claims[2]["dates"] == ["2026-09-08"]


def test_structural_diff_flags_matching_mismatches():
    main = "接通率 98.3%，总量 1000。"
    review = "接通率 98.29%，总量 1000。"
    diffs = _structural_diff(main, review)
    assert len(diffs) == 1
    assert diffs[0]["aspect"] == "数值/日期差异 #1"
    assert "98.3%" in diffs[0]["main"]
    assert "98.29%" in diffs[0]["review"]


def test_structural_diff_no_difference():
    text = "接通率 98.3%，总量 1000。"
    diffs = _structural_diff(text, text)
    assert diffs == []


def test_structural_diff_missing_claim():
    main = "接通率 98.3%。"
    review = ""
    diffs = _structural_diff(main, review)
    assert len(diffs) == 1
    assert diffs[0]["aspect"] == "复核报告缺失 #1"


@pytest.mark.asyncio
async def test_compare_reports_calls_judge(monkeypatch):
    """Smoke test: compare_reports should combine structural diff with judge output."""
    from app.agent import comparator

    async def fake_judge(self, messages):
        class FakeMessage:
            content = '{"verdict": "consistent", "summary": "ok", "differences": []}'

        return FakeMessage()

    monkeypatch.setattr(comparator, "get_llm", lambda **kwargs: type("LLM", (), {"ainvoke": fake_judge})())

    result = await comparator.compare_reports(
        "总量 1000，接通率 98.3%。",
        "总量 1000，接通率 98.30%。",
    )
    assert result["verdict"] == "consistent"
    assert result["summary"] == "ok"
