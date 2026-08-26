"""Tests for the pure tools (no LLM needed)."""
from __future__ import annotations

import pytest

from app.tools.calculator import calculator
from app.tools.datetime_tool import get_current_time


@pytest.mark.parametrize(
    "expr,expect",
    [
        ("1 + 2", 3),
        ("10 - 4", 6),
        ("3 * 7", 21),
        ("8 / 2", 4),
        ("2 ** 10", 1024),
        ("(3 + 4) * 2", 14),
    ],
)
def test_calculator_arithmetic(expr: str, expect: float) -> None:
    assert float(calculator.invoke({"expression": expr})) == pytest.approx(expect)


def test_calculator_rejects_unsafe() -> None:
    out = calculator.invoke({"expression": "__import__('os').system('echo unsafe')"})
    assert "Error" in out


def test_calculator_div_by_zero() -> None:
    out = calculator.invoke({"expression": "1/0"})
    assert "division by zero" in out


def test_datetime_tool_returns_string() -> None:
    s = get_current_time.invoke({"timezone": "UTC"})
    assert isinstance(s, str)
    assert len(s) >= 10


def test_datetime_tool_unknown_tz() -> None:
    s = get_current_time.invoke({"timezone": "Not/AZone"})
    assert "Unknown" in s