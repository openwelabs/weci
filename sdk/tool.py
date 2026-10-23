"""WeCi Plugin SDK · 工具基类。

这是第三方插件与内置插件**共同**依赖的稳定 Tool API。插件只需继承 ``Tool``
并提供 ``name`` / ``description`` / ``args_model`` / ``execute``。

设计要点：
  - 用 Pydantic 的 ``args_model`` 同时作为「OpenAI parameters Schema」与
    「运行时参数校验」的单一真源，避免手写 schema 与实际校验不一致。
  - ``execute(args, context)`` 接收**已校验**的参数模型与 ``PluginContext``；
    工具异常由 ``run`` 捕获并转换成结构化结果，绝不抛给 Agent Loop。
  - ``get_schema()`` 返回 OpenAI function calling 所需的 parameters JSON Schema。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Type

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from sdk.context import PluginContext


@dataclass
class ToolExecutionResult:
    """工具执行的结构化结果，Registry 永远返回它而不是抛异常。"""

    ok: bool
    output: str
    error: str | None = None
    duration_ms: float = 0.0

    def to_observation(self) -> str:
        """供模型读取的观察字符串。"""
        if self.ok:
            return self.output
        return f"[工具执行失败] {self.error}"


class Tool(ABC):
    """所有工具的基类。

    子类只需提供：
      - ``name``: 工具名（全局唯一，建议 ``plugin_name.tool_name`` 或短名）
      - ``description``: 给模型看的描述
      - ``args_model``: Pydantic BaseModel，声明参数
      - ``execute(args, context)``: 异步执行，返回字符串观察值
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
        """向后兼容别名。"""
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
        self, args: BaseModel, context: "PluginContext | None" = None
    ) -> str:
        """真正的工具逻辑，返回字符串观察值。"""
        raise NotImplementedError

    async def run(
        self,
        arguments: str | dict[str, Any] | None,
        context: "PluginContext | None" = None,
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
            output = await self.execute(args, context)
            output = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 — 工具异常必须收敛，不让 Agent 崩
            return ToolExecutionResult(
                ok=False,
                output="",
                error=_safe_error_message(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        return ToolExecutionResult(
            ok=True,
            output=output,
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def _safe_error_message(exc: Exception) -> str:
    """把工具异常转成不泄露服务器敏感信息的可读文案。"""
    msg = str(exc).strip()
    if not msg:
        return "工具执行时发生未知错误。"
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return msg
