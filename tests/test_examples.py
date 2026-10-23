"""示例插件与模板插件测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELLO_DIR = PROJECT_ROOT / "examples" / "plugins" / "hello"
TEMPLATE_DIR = PROJECT_ROOT / "examples" / "plugins" / "template"


def _load_module(dir_path: Path, name: str):
    path = dir_path / "plugin.py"
    sys.path.insert(0, str(dir_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# Example: hello
# ---------------------------------------------------------------------------
def test_hello_manifest_valid():
    from backend.plugins.manifest import load_manifest

    m = load_manifest(HELLO_DIR)
    assert m.id == "hello"
    assert m.api_version == "0.1"
    assert m.permissions == []


def test_hello_plugin_provides_tool():
    mod = _load_module(HELLO_DIR, "hello_plugin_test")
    plugin_cls = mod.HelloPlugin
    plugin = plugin_cls()
    tools = plugin.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "hello_world"


def test_hello_tool_schema():
    mod = _load_module(HELLO_DIR, "hello_schema_test")
    tool = mod.HelloWorldTool()
    schema = tool.get_schema()
    assert "name" in schema["properties"]


def test_hello_tool_returns_tool_result():
    import asyncio

    mod = _load_module(HELLO_DIR, "hello_result_test")
    from weci_sdk import ToolResult

    tool = mod.HelloWorldTool()
    result = asyncio.run(tool.execute(mod.HelloArgs(name="World"), context=None))
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert "World" in result.content


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------
def test_template_manifest_valid():
    from backend.plugins.manifest import load_manifest

    m = load_manifest(TEMPLATE_DIR)
    assert m.id == "my-plugin"
    assert m.api_version == "0.1"


def test_template_plugin_provides_tool():
    mod = _load_module(TEMPLATE_DIR, "template_plugin_test")
    plugin_cls = mod.MyPlugin
    plugin = plugin_cls()
    tools = plugin.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "my_tool"


def test_template_tool_returns_tool_result():
    import asyncio

    mod = _load_module(TEMPLATE_DIR, "template_result_test")
    from weci_sdk import ToolResult

    tool = mod.MyTool()
    result = asyncio.run(tool.execute(mod.MyToolArgs(query="test"), context=None))
    assert isinstance(result, ToolResult)
    assert result.success is True


# ---------------------------------------------------------------------------
# hello 插件自带的 tests 也能跑通
# ---------------------------------------------------------------------------
def test_hello_plugin_tests_pass():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(HELLO_DIR / "tests"), "-q"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
