"""WeCi Plugin Template.

复制本目录开始开发你的插件：

    cp -r examples/plugins/template my-plugin
    cd my-plugin
    # 修改 manifest.json 中的 id / name / version / author
    # 实现 plugin.py 中的 Tool

校验：

    weci plugin validate ./my-plugin

安装：

    weci plugin install ./my-plugin
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from weci_sdk import Tool, ToolResult, WeCiPlugin


# ---------------------------------------------------------------------------
# 1. 定义工具参数（Pydantic 模型）
# ---------------------------------------------------------------------------
class MyToolArgs(BaseModel):
    """工具的输入参数。Pydantic 会自动生成 OpenAI schema 并校验。"""

    query: str = Field(..., description="输入内容。", max_length=1000)


# ---------------------------------------------------------------------------
# 2. 定义工具
# ---------------------------------------------------------------------------
class MyTool(Tool):
    """工具类。每个工具提供一个 Agent 可调用的能力。"""

    name = "my_tool"
    description = "工具描述（告诉模型什么时候该调用这个工具）。"
    args_model = MyToolArgs

    async def execute(self, args: MyToolArgs, context=None) -> ToolResult:
        """工具逻辑。返回 ToolResult.success 或 ToolResult.error。"""
        # TODO: 实现你的工具逻辑
        return ToolResult.success(f"收到输入：{args.query}")


# ---------------------------------------------------------------------------
# 3. 定义插件
# ---------------------------------------------------------------------------
class MyPlugin(WeCiPlugin):
    """插件类。返回它提供的工具列表。"""

    def on_load(self, context) -> None:
        """插件加载时调用。可初始化资源、订阅事件。"""
        context.logger.info("My plugin loaded.")

    def on_unload(self) -> None:
        """插件卸载时调用。释放资源。"""

    def get_tools(self) -> list[Tool]:
        """返回插件提供的所有工具。"""
        return [MyTool()]
