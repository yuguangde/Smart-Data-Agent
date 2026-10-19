"""Semantic-layer context tool for the agent.

This tool does **not** call any LLM.  It only reads the registered semantic layer
and returns a concise plain-text summary so the Agent LLM can produce valid
MetricQuery DSL JSON.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.query.registry import SemanticRegistry


@tool
def get_semantic_context() -> str:
    """Return the available datasets, metrics, and dimensions from the semantic layer.

    本工具不调用 LLM，只读取已注册的语义层。当用户提出指标统计、趋势分析、
    按维度聚合类问题，需要写 MetricQuery DSL 时，先调用本工具获取上下文。

    Returns:
        包含数据集、指标、维度的可读文本；若语义层为空则提示缺少配置。
    """
    registry = SemanticRegistry.get()
    context = registry.context_for_llm()
    if not context.strip():
        return (
            "当前未加载任何语义层配置（缺少 .ossie.yml 文件或文件为空）。"
            "无法使用 execute_metric_dsl，请先补充语义层定义，或使用 execute_sql 直接查询。"
        )
    return context


__all__ = ["get_semantic_context"]
