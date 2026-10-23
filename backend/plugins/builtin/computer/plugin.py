"""Computer Control 内置插件。

提供执行命令、读写文件、剪贴板等基础电脑控制能力。
需要 process / filesystem.read / filesystem.write / clipboard 权限。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sdk import PluginContext, Tool, WeCiPlugin


# --------------------------------------------------------------------------
# run_command
# --------------------------------------------------------------------------
class RunCommandArgs(BaseModel):
    command: str = Field(..., description="要执行的 shell 命令。", max_length=4000)
    cwd: str | None = Field(default=None, description="工作目录，默认当前目录。")
    timeout: float = Field(default=30.0, description="超时秒数，默认 30。", ge=1, le=600)


class RunCommandTool(Tool):
    name = "run_command"
    description = "在本机执行 shell 命令，返回 stdout / stderr / 返回码。"
    args_model = RunCommandArgs

    async def execute(self, args: RunCommandArgs, context: PluginContext | None = None) -> dict[str, Any]:
        if context is None:
            return {"returncode": -1, "stdout": "", "stderr": "no plugin context"}
        result = await context.run_command(
            args.command, cwd=args.cwd, timeout=args.timeout
        )
        return result


# --------------------------------------------------------------------------
# read_file
# --------------------------------------------------------------------------
class ReadFileArgs(BaseModel):
    path: str = Field(..., description="要读取的文件绝对路径或相对路径。")


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取文本文件内容。"
    args_model = ReadFileArgs

    async def execute(self, args: ReadFileArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        return context.read_file(args.path)


# --------------------------------------------------------------------------
# write_file
# --------------------------------------------------------------------------
class WriteFileArgs(BaseModel):
    path: str = Field(..., description="要写入的文件路径。")
    content: str = Field(..., description="要写入的文本内容（覆盖）。")


class WriteFileTool(Tool):
    name = "write_file"
    description = "向文件写入文本内容（覆盖已有文件）。"
    args_model = WriteFileArgs

    async def execute(self, args: WriteFileArgs, context: PluginContext | None = None) -> str:
        if context is None:
            raise RuntimeError("no plugin context")
        context.write_file(args.path, args.content)
        return f"已写入 {args.path}（{len(args.content)} 字符）"


# --------------------------------------------------------------------------
# list_dir
# --------------------------------------------------------------------------
class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="要列出的目录路径，默认当前目录。")


class ListDirTool(Tool):
    name = "list_dir"
    description = "列出目录下的文件和子目录。"
    args_model = ListDirArgs

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
            ListDirTool(),
            ClipboardGetTool(),
            ClipboardSetTool(),
        ]
