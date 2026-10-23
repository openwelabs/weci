"""工具注册、执行、ToolResult、ToolRegistry 测试。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from weci_sdk import Tool, ToolExecutionResult, ToolResult
from backend.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(default="", description="text to echo")


class EchoTool(Tool):
    name = "echo"
    description = "echo text"
    args_model = EchoArgs

    async def execute(self, args: EchoArgs, context=None):
        return ToolResult.success(f"echo: {args.text}")


class StrReturnTool(Tool):
    name = "str_tool"
    description = "returns str"
    args_model = BaseModel

    async def execute(self, args, context=None) -> str:
        return "plain string"


class DictReturnTool(Tool):
    name = "dict_tool"
    description = "returns dict"
    args_model = BaseModel

    async def execute(self, args, context=None) -> dict:
        return {"key": "value"}


class FailingTool(Tool):
    name = "failing"
    description = "always fails"
    args_model = BaseModel

    async def execute(self, args, context=None):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------
def test_tool_result_success():
    r = ToolResult.success("hello", count=1)
    assert r.success is True
    assert r.content == "hello"
    assert r.metadata == {"count": 1}
    assert r.to_text() == "hello"


def test_tool_result_error():
    r = ToolResult.error("something wrong")
    assert r.success is False
    assert r.error == "something wrong"
    assert "[工具执行失败]" in r.to_text()


def test_tool_result_dict_content_serializes():
    r = ToolResult.success({"a": 1})
    assert '"a"' in r.to_text()


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------
def test_tool_schema_structure():
    tool = EchoTool()
    schema = tool.get_schema()
    assert schema["type"] == "object"
    assert "text" in schema["properties"]


def test_tool_openai_schema():
    tool = EchoTool()
    s = tool.to_openai_schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "echo"
    assert s["function"]["parameters"] == tool.get_schema()


# ---------------------------------------------------------------------------
# ToolRegistry 注册 / 查询
# ---------------------------------------------------------------------------
def test_register_and_list():
    reg = ToolRegistry()
    reg.register(EchoTool())
    assert reg.get("echo") is not None
    assert "echo" in reg.names()
    assert len(reg.schemas()) == 1


def test_register_duplicate_raises():
    reg = ToolRegistry()
    reg.register(EchoTool())
    with pytest.raises(ValueError):
        reg.register(EchoTool())


def test_unregister():
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.unregister("echo")
    assert reg.get("echo") is None
    assert "echo" not in reg.names()


# ---------------------------------------------------------------------------
# Tool 执行
# ---------------------------------------------------------------------------
def test_execute_success_str_return():
    reg = ToolRegistry()
    reg.register(StrReturnTool())
    result = asyncio.run(reg.execute("str_tool", None))
    assert isinstance(result, ToolExecutionResult)
    assert result.ok is True
    assert result.output == "plain string"


def test_execute_success_dict_return():
    reg = ToolRegistry()
    reg.register(DictReturnTool())
    result = asyncio.run(reg.execute("dict_tool", None))
    assert result.ok is True
    assert "key" in result.output


def test_execute_success_tool_result():
    reg = ToolRegistry()
    reg.register(EchoTool())
    result = asyncio.run(reg.execute("echo", {"text": "hi"}))
    assert result.ok is True
    assert result.output == "echo: hi"


def test_execute_unknown_tool_returns_error():
    reg = ToolRegistry()
    result = asyncio.run(reg.execute("nope", None))
    assert result.ok is False
    assert "未知工具" in result.error


def test_execute_invalid_args_returns_error():
    reg = ToolRegistry()
    reg.register(EchoTool())
    # text 是 str，传 int 应校验失败
    result = asyncio.run(reg.execute("echo", {"text": 123}))
    # pydantic 会把 int 转 str，所以这里用非法 JSON
    result = asyncio.run(reg.execute("echo", "{bad json"))
    assert result.ok is False
    assert result.error is not None


def test_execute_tool_exception_does_not_raise():
    reg = ToolRegistry()
    reg.register(FailingTool())
    # 不应抛异常，而是返回 ok=False
    result = asyncio.run(reg.execute("failing", None))
    assert result.ok is False
    assert "boom" in result.error
