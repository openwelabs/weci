"""工具注册表：注册、查询、带权限地执行。

职责：
  - 显式注册工具（不在 import 时副作用注册）
  - 提供给模型的 tools schema 列表
  - 执行前走 Permission Check
  - 把工具所属插件的 ``PluginContext`` 透传给 ``tool.run``
  - 永远返回 ToolExecutionResult（未知工具 / 权限拒绝 / 校验失败 / 执行异常
    都收敛为结构化结果），保证 Agent Loop 不会因工具问题断裂

Agent 永远只通过本 Registry 与工具交互，不感知具体工具属于哪个插件。
"""

from __future__ import annotations

from typing import Any

from weci_sdk.context import PluginContext
from weci_sdk.tool import Tool, ToolExecutionResult
from backend.tools.permission import AllowListPermission, ToolPermission


class ToolRegistry:
    def __init__(self, permission: ToolPermission | None = None) -> None:
        # name -> (tool, context)
        self._tools: dict[str, tuple[Tool, PluginContext | None]] = {}
        # 默认允许已注册的全部工具：因为插件本身已经过 manifest + 权限声明校验，
        # 且工具只来自显式注册的插件。工具级白名单可由 Permission 进一步收紧。
        self._permission: ToolPermission = permission or _RegisteredToolPermission(self)

    # ------------------------------------------------------------------
    # 注册 / 查询
    # ------------------------------------------------------------------
    def register(self, tool: Tool, context: PluginContext | None = None) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册：{tool.name}")
        self._tools[tool.name] = (tool, context)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """返回 OpenAI function calling 格式的 tools 列表。"""
        return [tool.to_openai_schema() for tool, _ in self._tools.values()]

    # ------------------------------------------------------------------
    # 执行（统一入口）
    # ------------------------------------------------------------------
    async def execute(
        self, name: str, arguments: str | dict[str, Any] | None
    ) -> ToolExecutionResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolExecutionResult(
                ok=False, output="", error=f"未知工具：{name}"
            )
        tool, context = entry

        args_for_check = self._coerce_args(arguments)
        allowed = await self._permission.check(name, args_for_check)
        if not allowed:
            return ToolExecutionResult(
                ok=False,
                output="",
                error=f"工具 {name} 未被授权执行。",
            )

        return await tool.run(arguments, context)

    @staticmethod
    def _coerce_args(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return arguments
        try:
            import json

            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}


class _RegisteredToolPermission(ToolPermission):
    """默认权限：允许所有已注册的工具执行。

    插件级权限（network/storage 等）由 PluginContext 在能力调用时单独校验；
    工具能否被 Agent 调用，取决于它是否被显式注册到本 Registry。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return tool_name in self._registry._tools  # noqa: SLF001
