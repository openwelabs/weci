"""Hello Plugin 自身测试。

演示插件开发者如何为自己的插件写测试。
这些测试不依赖 WeCi Core，只验证工具逻辑。
"""

import asyncio
import sys
from pathlib import Path

import pytest

# 确保能导入本插件的 plugin.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugin import HelloArgs, HelloPlugin, HelloWorldTool  # noqa: E402


def test_tool_name_and_description():
    tool = HelloWorldTool()
    assert tool.name == "hello_world"
    assert "hello" in tool.description.lower() or "招呼" in tool.description


def test_tool_schema_has_name_param():
    tool = HelloWorldTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "name" in schema["properties"]


def test_args_validation_default():
    args = HelloArgs()
    assert args.name == "World"


def test_args_validation_custom():
    args = HelloArgs(name="Alice")
    assert args.name == "Alice"


def test_execute_returns_tool_result():
    tool = HelloWorldTool()
    result = asyncio.run(tool.execute(HelloArgs(name="Bob"), context=None))
    assert result.success is True
    assert "Bob" in result.content


def test_execute_default_name():
    tool = HelloWorldTool()
    result = asyncio.run(tool.execute(HelloArgs(), context=None))
    assert result.success is True
    assert result.content == "Hello, World!"


def test_plugin_provides_tool():
    plugin = HelloPlugin()
    tools = plugin.get_tools()
    assert len(tools) == 1
    assert isinstance(tools[0], HelloWorldTool)


def test_run_returns_structured_result():
    """通过 Tool.run 走完整的参数解析 → 校验 → 执行链路。"""
    tool = HelloWorldTool()
    result = asyncio.run(tool.run({"name": "Test"}, context=None))
    assert result.ok is True
    assert result.output == "Hello, Test!"


def test_run_invalid_args_returns_error():
    tool = HelloWorldTool()
    # name 超过 max_length=100
    result = asyncio.run(tool.run({"name": "x" * 200}, context=None))
    assert result.ok is False
    assert result.error is not None
