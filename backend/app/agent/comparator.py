"""Report comparison for the review agent.

Combines a cheap structural diff (numbers/dates/key phrases) with an LLM judge
that decides whether the two reports are factually consistent.
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.factory import get_llm

logger = logging.getLogger(__name__)

# Matches integers, decimals, and percentages (e.g. 1,234  98.3  50%).
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")
# YYYY-MM-DD / YYYY/MM/DD.
_DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def _extract_numerical_claims(report: str) -> list[dict[str, object]]:
    """Return lines that contain numbers, tagged with the numbers found."""
    claims: list[dict[str, object]] = []
    for line in report.splitlines():
        numbers = _NUMBER_RE.findall(line)
        dates = _DATE_RE.findall(line)
        if numbers or dates:
            claims.append(
                {
                    "line": line.strip(),
                    "numbers": numbers,
                    "dates": dates,
                }
            )
    return claims


def _structural_diff(main_report: str, review_report: str) -> list[dict[str, object]]:
    """Align numerical claims between the two reports and surface mismatches."""
    main_claims = _extract_numerical_claims(main_report)
    review_claims = _extract_numerical_claims(review_report)
    diffs: list[dict[str, object]] = []

    max_len = max(len(main_claims), len(review_claims))
    for i in range(max_len):
        mc = main_claims[i] if i < len(main_claims) else None
        rc = review_claims[i] if i < len(review_claims) else None
        if mc and rc:
            if mc.get("numbers") != rc.get("numbers") or mc.get("dates") != rc.get("dates"):
                diffs.append(
                    {
                        "aspect": f"数值/日期差异 #{i + 1}",
                        "main": mc["line"],
                        "review": rc["line"],
                        "severity": "minor",
                    }
                )
        elif mc:
            diffs.append(
                {
                    "aspect": f"复核报告缺失 #{i + 1}",
                    "main": mc["line"],
                    "review": "",
                    "severity": "major",
                }
            )
        elif rc:
            diffs.append(
                {
                    "aspect": f"主报告缺失 #{i + 1}",
                    "main": "",
                    "review": rc["line"],
                    "severity": "major",
                }
            )
    return diffs


_JUDGE_SYSTEM = """\
你是一名严格的内容复核员。用户提出一个问题后，主模型和复核模型分别生成了一份分析报告。
请基于用户问题，判断两份报告是否在事实上一致。

输出要求：
- 只输出 JSON，不要任何解释。
- JSON 格式：
  {
    "verdict": "consistent" | "partial" | "inconsistent",
    "summary": "一句话总结两份报告的关系",
    "differences": [
      {"aspect": "差异点描述", "main": "主报告说法", "review": "复核报告说法", "severity": "cosmetic|minor|major"}
    ]
  }

判断标准：
- consistent：核心结论、关键指标、数值完全一致或只有表述/格式差异。
- partial：核心结论一致，但存在补充信息、口径差异或个别数值不一致。
- inconsistent：核心结论或关键指标相互矛盾。

如果以下结构化 diff 已经列出差异，请结合完整报告判断每个差异的严重性，并纳入 differences。"""


def _build_judge_prompt(
    user_question: str,
    main_report: str,
    review_report: str,
    diffs: list[dict[str, object]],
) -> list[SystemMessage | HumanMessage]:
    return [
        SystemMessage(content=_JUDGE_SYSTEM),
        HumanMessage(
            content=json.dumps(
                {
                    "user_question": user_question,
                    "main_report": main_report,
                    "review_report": review_report,
                    "structured_diff": diffs,
                },
                ensure_ascii=False,
                default=str,
            )
        ),
    ]


def _safe_parse_json(content: str) -> dict[str, object]:
    """Try to extract the first JSON object from the LLM response."""
    # Some models wrap JSON in markdown fences; strip them.
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last fence lines if present.
        if len(lines) > 2:
            text = "\n".join(lines[1:-1])
        else:
            text = text.strip("`")
    return json.loads(text)


async def compare_reports(
    main_report: str,
    review_report: str,
    user_question: str = "",
) -> dict[str, object]:
    """Compare two analysis reports and return a structured verdict.

    Returns:
        {
            "verdict": "consistent" | "partial" | "inconsistent",
            "summary": str,
            "differences": list[dict],
        }
    """
    diffs = _structural_diff(main_report, review_report)

    chat = get_llm(with_tools=None)
    messages = _build_judge_prompt(user_question, main_report, review_report, diffs)
    try:
        response = await chat.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        result = _safe_parse_json(content)
    except Exception as exc:
        logger.warning("LLM judge comparison failed: %s", exc)
        result = {
            "verdict": "partial",
            "summary": "结构化对比完成，但 LLM judge 调用失败。",
            "differences": diffs,
        }

    result.setdefault("verdict", "partial")
    result.setdefault("summary", "")
    result.setdefault("differences", diffs)
    return result


__all__ = ["compare_reports"]
