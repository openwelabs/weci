"""Computer Control 内置插件。

提供执行命令、读写文件、剪贴板等基础电脑控制能力。
文件与命令不经过 WeCi 的路径 / 权限拦截，直接交给操作系统执行；
工具声明的权限仅用于记录与审计。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from weci_sdk import PluginContext, Tool, ToolResult, WeCiPlugin


# --------------------------------------------------------------------------
# run_command
# --------------------------------------------------------------------------
class RunCommandArgs(BaseModel):
    command: str = Field(..., description="要执行的 shell 命令。", max_length=4000)
    cwd: str | None = Field(
        default=None,
        description="工作目录（默认 Workspace 根目录；支持绝对路径与 ..，不做范围限制）。",
    )
    timeout: float = Field(default=30.0, description="超时秒数，默认 30。", ge=1, le=600)


class RunCommandTool(Tool):
    name = "run_command"
    description = "在本机执行 shell 命令，返回 stdout / stderr / 返回码。"
    args_model = RunCommandArgs
    permissions = ("command:execute",)

    async def execute(self, args: RunCommandArgs, context: PluginContext | None = None) -> ToolResult:
        if context is None:
            return ToolResult.error("no plugin context")
        result = await context.run_command(
            args.command, cwd=args.cwd, timeout=args.timeout
        )
        # 把关键执行事实放进 metadata，供 Agent Run / UI 审计（内容不重复进模型上下文）
        return ToolResult.success(
            result,
            exit_code=result.get("exit_code"),
            cwd=result.get("cwd"),
            timed_out=result.get("timed_out"),
            command=args.command,
        )


# --------------------------------------------------------------------------
# read_file
# --------------------------------------------------------------------------
class ReadFileArgs(BaseModel):
    path: str = Field(..., description="要读取的文件路径（相对或绝对，可为任意路径）。")


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取 Workspace 内文本文件的内容。"
    args_model = ReadFileArgs
    permissions = ("workspace:read",)
    risk = "safe"

    async def execute(self, args: ReadFileArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        return context.read_file(args.path)


# --------------------------------------------------------------------------
# write_file
# --------------------------------------------------------------------------
class WriteFileArgs(BaseModel):
    path: str = Field(..., description="要写入的文件路径（相对或绝对，可为任意路径）。")
    content: str = Field(..., description="要写入的文本内容（覆盖）。")


class WriteFileTool(Tool):
    name = "write_file"
    description = "向 Workspace 内的文件写入文本内容（覆盖已有文件）。"
    args_model = WriteFileArgs
    permissions = ("workspace:write",)

    async def execute(self, args: WriteFileArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        context.write_file(args.path, args.content)
        return f"已写入 {args.path}（{len(args.content)} 字符）"


# --------------------------------------------------------------------------
# delete_file
# --------------------------------------------------------------------------
class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="要删除的文件路径（相对或绝对，可为任意路径）。")


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "删除 Workspace 内的一个文件。"
    args_model = DeleteFileArgs
    permissions = ("workspace:delete",)
    risk = "dangerous"

    async def execute(self, args: DeleteFileArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        context.delete_file(args.path)
        return f"已删除 {args.path}"


# --------------------------------------------------------------------------
# list_dir
# --------------------------------------------------------------------------
class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="要列出的目录路径（相对或绝对），默认 Workspace 根目录。")


class ListDirTool(Tool):
    name = "list_dir"
    description = "列出 Workspace 内某个目录下的文件和子目录。"
    args_model = ListDirArgs
    permissions = ("workspace:read",)
    risk = "safe"

    async def execute(self, args: ListDirArgs, context: PluginContext | None = None) -> list[dict[str, Any]]:
        if context is None:
            raise RuntimeError("no plugin context")
        return context.list_dir(args.path)


# --------------------------------------------------------------------------
# clipboard_get
# --------------------------------------------------------------------------
class ClipboardGetTool(Tool):
    name = "clipboard_get"
    description = "读取系统剪贴板的文本内容。"
    args_model = BaseModel
    permissions = ("clipboard:read",)

    async def execute(self, args: BaseModel, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        return context.clipboard_get()


# --------------------------------------------------------------------------
# clipboard_set
# --------------------------------------------------------------------------
class ClipboardSetArgs(BaseModel):
    text: str = Field(..., description="要写入剪贴板的文本。", max_length=100000)


class ClipboardSetTool(Tool):
    name = "clipboard_set"
    description = "将文本写入系统剪贴板。"
    args_model = ClipboardSetArgs
    permissions = ("clipboard:write",)

    async def execute(self, args: ClipboardSetArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        context.clipboard_set(args.text)
        return "已写入剪贴板"


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------
class ComputerPlugin(WeCiPlugin):
    def on_load(self, context: PluginContext) -> None:
        context.logger.warning(
            "Computer Control 插件已启用：Agent 现在可以执行命令、读写文件、访问剪贴板。"
        )

    def get_tools(self) -> list[Tool]:
        return [
            RunCommandTool(),
            ReadFileTool(),
            WriteFileTool(),
            DeleteFileTool(),
            ListDirTool(),
            ClipboardGetTool(),
            ClipboardSetTool(),
        ]
