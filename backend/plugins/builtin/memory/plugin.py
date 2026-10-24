"""内置 Memory 插件：让 Agent 在用户明确要求时保存 / 删除长期记忆。

设计原则（与 Core 的 Memory 一致）：
  - 只在用户明确说「记住 / 忘记」时调用，绝不能自动保存聊天内容
  - Memory 是少量长期事实，不是聊天记录
  - scope 决定归属：user（跨会话）、workspace、conversation（仅当前会话）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from weci_sdk import Tool, WeCiPlugin
from weci_sdk.tool import ToolResult

MemoryScopeArg = Literal["user", "workspace", "conversation"]

_SCOPE_DESCRIPTION = (
    "记忆归属范围：user = 该用户的长期偏好/事实（跨会话，默认）；"
    "workspace = 当前 Workspace 的约定；"
    "conversation = 仅当前会话。"
)


class SaveMemoryArgs(BaseModel):
    key: str = Field(
        description="记忆的简短标识，例如 operating_system、editor、language。使用小写英文与下划线。"
    )
    content: str = Field(
        description="要记住的内容，例如 Fedora、neovim、中文。保持简短（最多 500 字）。"
    )
    scope: MemoryScopeArg = Field(default="user", description=_SCOPE_DESCRIPTION)


class DeleteMemoryArgs(BaseModel):
    key: str = Field(description="要删除的记忆标识，例如 operating_system。")
    scope: MemoryScopeArg = Field(default="user", description=_SCOPE_DESCRIPTION)


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = (
        "保存一条长期记忆，供之后的对话使用。"
        "仅当用户明确要求记住某事时调用（例如“记住我使用 Fedora”），"
        "不要根据聊天内容自行推测或自动保存。"
        "同一个 key 再次保存会更新原有记忆。"
    )
    args_model = SaveMemoryArgs
    permissions = ("memory:write",)

    async def execute(self, args: SaveMemoryArgs, context=None) -> ToolResult:
        if context is None:
            return ToolResult.error("当前没有可用的插件上下文，无法保存记忆。")
        entry = context.memory.save(args.key, args.content, scope=args.scope)
        return ToolResult.success(
            f"已保存记忆（scope={entry['scope']}）：{entry['key']} = {entry['content']}"
        )


class DeleteMemoryTool(Tool):
    name = "delete_memory"
    description = (
        "删除一条已保存的长期记忆。"
        "仅当用户明确要求忘记某事时调用（例如“忘掉我的系统信息”），"
        "不要自行删除记忆。"
    )
    args_model = DeleteMemoryArgs
    permissions = ("memory:write",)

    async def execute(self, args: DeleteMemoryArgs, context=None) -> ToolResult:
        if context is None:
            return ToolResult.error("当前没有可用的插件上下文，无法删除记忆。")
        deleted = context.memory.delete(args.key, scope=args.scope)
        if deleted:
            return ToolResult.success(
                f"已删除记忆（scope={args.scope}）：{args.key}"
            )
        return ToolResult.success(
            f"没有找到要删除的记忆（scope={args.scope}）：{args.key}"
        )


class MemoryPlugin(WeCiPlugin):
    def get_tools(self) -> list[Tool]:
        return [SaveMemoryTool(), DeleteMemoryTool()]
