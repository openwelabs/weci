"""WeCi Plugin SDK · 工具基类与工具结果。

这是第三方插件与内置插件**共同**依赖的稳定 Tool API。插件只需继承 ``Tool``
并提供 ``name`` / ``description`` / ``args_model`` / ``execute``。

设计要点：
  - 用 Pydantic 的 ``args_model`` 同时作为「OpenAI parameters Schema」与
    「运行时参数校验」的单一真源，避免手写 schema 与实际校验不一致。
  - ``execute(args, context)`` 接收**已校验**的参数模型与 ``ToolContext``；
    返回值可以是 ``str``、``dict`` 或 ``ToolResult``，由 ``run`` 统一归一化。
  - ``get_schema()`` 返回 OpenAI function calling 所需的 parameters JSON Schema。
  - 工具异常由 ``run`` 捕获并转换成结构化结果，绝不抛给 Agent Loop。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Type, Union

from pydantic import BaseModel, ValidationError

# 无参数工具使用的空参数模型（BaseModel 本身不能直接实例化）
class _NoArgs(BaseModel):
    pass

if TYPE_CHECKING:
    from weci_sdk.context import ToolContext


# ---------------------------------------------------------------------------
# ToolResult —— 工具统一返回类型
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """工具执行的统一返回结果。

    插件工具的 ``execute`` 可以直接返回 ``str`` / ``dict``，也可以显式构造
    ``ToolResult``。推荐使用工厂方法::

        return ToolResult.success("Hello, World!")
        return ToolResult.error("something went wrong")

    Agent 层始终拿到结构化结果，便于统一处理。
    """

    success: bool
    content: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: Any = None, **metadata: Any) -> "ToolResult":
        return cls(success=True, content=content, metadata=metadata)

    @classmethod
    def error(cls, error: str, **metadata: Any) -> "ToolResult":
        return cls(success=False, error=error, metadata=metadata)

    def to_text(self) -> str:
        """供模型读取的观察字符串。"""
        if self.success:
            if self.content is None:
                return ""
            if isinstance(self.content, str):
                return self.content
            try:
                return json.dumps(self.content, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(self.content)
        return f"[工具执行失败] {self.error}"


# ---------------------------------------------------------------------------
# ToolExecutionResult —— Registry 层返回类型（向后兼容）
# ---------------------------------------------------------------------------
@dataclass
class ToolExecutionResult:
    """ToolRegistry 执行工具后返回的结构化结果。

    由 ``Tool.run`` 把 ``ToolResult`` 归一化为此类型，保证 Agent Loop
    永远拿到统一结构，不会因工具异常而断裂。
    """

    ok: bool
    output: str
    error: str | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_observation(self) -> str:
        """供模型读取的观察字符串。"""
        if self.ok:
            return self.output
        return f"[工具执行失败] {self.error}"


# ---------------------------------------------------------------------------
# Tool 基类
# ---------------------------------------------------------------------------
class Tool(ABC):
    """所有工具的基类。

    子类只需提供：
      - ``name``: 工具名（全局唯一，建议 ``plugin_name.tool_name`` 或短名）
      - ``description``: 给模型看的描述
      - ``args_model``: Pydantic BaseModel，声明参数
      - ``execute(args, context)``: 异步执行，返回 ``str`` / ``dict`` / ``ToolResult``
    """

    name: str
    description: str
    args_model: Type[BaseModel]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def get_schema(self) -> dict[str, Any]:
        """OpenAI function calling 所需的 parameters JSON Schema。

        对于无参数工具（``args_model`` 直接为 ``BaseModel``），返回空参数 schema。
        """
        if self.args_model is BaseModel:
            return {"type": "object", "properties": {}, "required": []}
        return self.args_model.model_json_schema()

    @property
    def parameters(self) -> dict[str, Any]:
        """``get_schema`` 的别名。"""
        return self.get_schema()

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_schema(),
            },
        }

    # ------------------------------------------------------------------
    # 执行入口（供 Registry 调用）
    # ------------------------------------------------------------------
    @abstractmethod
    async def execute(
        self, args: BaseModel, context: "ToolContext | None" = None
    ) -> Union[str, dict[str, Any], ToolResult]:
        """真正的工具逻辑。

        返回值会被 ``run`` 归一化为 ``ToolExecutionResult``：
          - ``str`` → 成功，output 为该字符串
          - ``dict`` → 成功，output 为 JSON 字符串
          - ``ToolResult`` → 按其 success/content/error 转换
        """
        raise NotImplementedError

    async def run(
        self,
        arguments: str | dict[str, Any] | None,
        context: "ToolContext | None" = None,
    ) -> ToolExecutionResult:
        """解析参数 → 校验 → 执行 → 结构化结果。永远不抛异常。"""
        started = time.perf_counter()

        args_dict: dict[str, Any]
        if arguments is None:
            args_dict = {}
        elif isinstance(arguments, dict):
            args_dict = arguments
        else:
            try:
                parsed = json.loads(arguments)
                args_dict = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return ToolExecutionResult(
                    ok=False,
                    output="",
                    error="工具参数不是合法的 JSON 对象。",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

        # 无参数工具（args_model 直接为 BaseModel）跳过校验
        if self.args_model is BaseModel:
            args = _NoArgs()
        else:
            try:
                args = self.args_model.model_validate(args_dict)
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or '参数'}: {e['msg']}"
                    for e in exc.errors()
                )
                return ToolExecutionResult(
                    ok=False,
                    output="",
                    error=f"参数校验失败：{details or '格式不正确'}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

        try:
            raw = await self.execute(args, context)
        except Exception as exc:  # noqa: BLE001 — 工具异常必须收敛，不让 Agent 崩
            return ToolExecutionResult(
                ok=False,
                output="",
                error=_safe_error_message(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        # 归一化返回值
        result: ToolResult
        if isinstance(raw, ToolResult):
            result = raw
        elif isinstance(raw, str):
            result = ToolResult.success(raw)
        elif isinstance(raw, dict):
            result = ToolResult.success(raw)
        elif raw is None:
            result = ToolResult.success("")
        else:
            try:
                result = ToolResult.success(str(raw))
            except Exception as exc:  # noqa: BLE001
                return ToolExecutionResult(
                    ok=False,
                    output="",
                    error=_safe_error_message(exc),
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

        return ToolExecutionResult(
            ok=result.success,
            output=result.to_text(),
            error=result.error,
            duration_ms=(time.perf_counter() - started) * 1000,
            metadata=dict(result.metadata),
        )


def _safe_error_message(exc: Exception) -> str:
    """把工具异常转成不泄露服务器敏感信息的可读文案。"""
    msg = str(exc).strip()
    if not msg:
        return "工具执行时发生未知错误。"
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return msg


__all__ = ["Tool", "ToolResult", "ToolExecutionResult"]
