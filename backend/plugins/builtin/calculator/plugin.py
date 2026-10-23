"""内置 Calculator 插件。

演示内置插件与第三方插件使用**完全相同**的 Plugin API。
工具实现沿用原安全 AST 计算器（不使用 eval）。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from pydantic import BaseModel, Field

from sdk import Tool, WeCiPlugin

_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Any] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS: dict[str, Any] = {
    "sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
    "round": round, "log": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "exp": math.exp,
    "min": min, "max": max,
}
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}
_MAX_EXPR_LENGTH = 200
_MAX_POWER = 1000


class CalculatorArgs(BaseModel):
    expression: str = Field(
        ..., description="要计算的数学表达式，例如 '2 + 3 * (4 - 1)' 或 'sqrt(16)'。"
    )


def _safe_eval(expr: str) -> float | int:
    if len(expr) > _MAX_EXPR_LENGTH:
        raise ValueError(f"表达式过长（上限 {_MAX_EXPR_LENGTH} 字符）。")
    tree = ast.parse(expr, mode="eval")

    def _eval(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                raise ValueError("不支持布尔值。")
            if isinstance(value, (int, float)):
                return value
            raise ValueError("只允许数字常量。")
        if isinstance(node, ast.BinOp):
            op_func = _BIN_OPS.get(type(node.op))
            if op_func is None:
                raise ValueError(f"不支持的运算符：{type(node.op).__name__}")
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)):
                if abs(right) > _MAX_POWER:
                    raise ValueError("幂运算指数过大。")
            try:
                return op_func(left, right)
            except ZeroDivisionError:
                raise ValueError("除数不能为零。") from None
            except OverflowError:
                raise ValueError("计算结果溢出。") from None
        if isinstance(node, ast.UnaryOp):
            op_func = _UNARY_OPS.get(type(node.op))
            if op_func is None:
                raise ValueError(f"不支持的一元运算符：{type(node.op).__name__}")
            return op_func(_eval(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("不支持的函数调用形式。")
            fn = _FUNCTIONS.get(node.func.id)
            if fn is None:
                raise ValueError(f"未知函数：{node.func.id}")
            if node.keywords:
                raise ValueError("不支持关键字参数。")
            args = [_eval(arg) for arg in node.args]
            try:
                return fn(*args)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"函数 {node.func.id} 调用失败：{exc}") from exc
        if isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ValueError(f"未知标识符：{node.id}")
        raise ValueError("表达式包含不支持的语法。")

    return _eval(tree)


class CalculatorTool(Tool):
    name = "calculator"
    description = (
        "安全地计算数学表达式。支持加减乘除、括号、幂、取模，以及 "
        "sqrt/abs/floor/ceil/round/log/sin/cos/tan/min/max 等函数和 pi/e 常量。"
        "不要传入包含字母变量或代码的表达式。"
    )
    args_model = CalculatorArgs

    async def execute(self, args: CalculatorArgs, context=None) -> str:
        expr = args.expression.strip()
        if not expr:
            return "表达式为空。"
        try:
            result = _safe_eval(expr)
        except ValueError as exc:
            return f"无法计算：{exc}"
        if isinstance(result, float):
            if result.is_integer() and abs(result) < 1e15:
                return str(int(result))
            return f"{result:.10g}"
        return str(result)


class CalculatorPlugin(WeCiPlugin):
    def get_tools(self) -> list[Tool]:
        return [CalculatorTool()]
