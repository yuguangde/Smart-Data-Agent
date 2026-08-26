"""Safe calculator: evaluate math expressions with a restricted AST."""
from __future__ import annotations

import ast
import math
import operator

from langchain_core.tools import tool

# Whitelisted AST operators.
_BIN_OPS: dict[type, object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, object] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Allow math functions/constants, nothing else.
_MATH_NAMES: dict[str, object] = {
    name: getattr(math, name)
    for name in (
        "pi", "e", "tau", "inf", "nan",
        "sqrt", "log", "log2", "log10",
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh",
        "ceil", "floor", "fabs", "factorial",
        "gcd", "lcm", "exp", "pow",
        "degrees", "radians",
    )
}


def _safe_eval(expr: str) -> float | int:
    tree = ast.parse(expr, mode="eval")

    def _eval(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval(node.body)  # type: ignore[return-value]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value  # type: ignore[return-value]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left = _eval(node.left)
            right = _eval(node.right)
            return _BIN_OPS[type(node.op)](left, right)  # type: ignore[operator,return-value]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))  # type: ignore[operator,return-value]
        if isinstance(node, ast.Name) and node.id in _MATH_NAMES:
            return _MATH_NAMES[node.id]  # type: ignore[return-value]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _MATH_NAMES:
                raise ValueError(f"Unsupported function: {ast.dump(node.func)}")
            if node.keywords:
                raise ValueError("Keyword arguments are not allowed")
            args = [_eval(a) for a in node.args]
            return _MATH_NAMES[node.func.id](*args)  # type: ignore[operator,return-value]
        raise ValueError(f"Unsupported expression node: {ast.dump(node)}")

    return _eval(tree)


@tool
def calculator(expression: str) -> str:
    """Safely evaluate a math expression and return its numeric result.

    Supports + - * / // % ** parentheses and math functions like sqrt, log, sin, cos, tan, pi, e.
    Example: "2 * (3 + 4) ** 2"
    """
    try:
        result = _safe_eval(expression)
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:  # ast errors, unsupported nodes, etc.
        return f"Error: {exc}"

    if isinstance(result, float):
        # Strip trailing .0 for whole floats
        if result.is_integer() and abs(result) < 1e16:
            return str(int(result))
        return f"{result:.10g}"
    return str(result)


__all__ = ["calculator"]
