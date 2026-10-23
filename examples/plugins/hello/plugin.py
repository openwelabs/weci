"""WeCi 示例插件：Hello Plugin。

演示如何编写一个最小可用插件：
  1. 继承 WeCiPlugin
  2. 在 get_tools() 中返回工具实例
  3. 工具继承 weci_sdk.Tool，声明 name / description / args_model / execute
  4. 使用 ToolResult.success / ToolResult.error 返回结构化结果

安装后，Agent 可以看到 hello_world 工具并调用：
    hello_world(name="World")  →  "Hello, World!"
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from weci_sdk import Tool, ToolResult, WeCiPlugin


class HelloArgs(BaseModel):
    name: str = Field(default="World", description="要问候的名字。", max_length=100)


class HelloWorldTool(Tool):
    name = "hello_world"
    description = "向指定名字打招呼。当用户要求打招呼、问候或测试工具调用时使用。"
    args_model = HelloArgs

    async def execute(self, args: HelloArgs, context=None) -> ToolResult:
        name = (args.name or "World").strip() or "World"
        # 推荐返回 ToolResult，Agent 层会统一处理
        return ToolResult.success(f"Hello, {name}!")


class HelloPlugin(WeCiPlugin):
    def on_load(self, context) -> None:
        # 可在此初始化资源、订阅事件
        context.logger.info("Hello plugin loaded.")

    def get_tools(self) -> list[Tool]:
        return [HelloWorldTool()]
