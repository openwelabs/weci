"""插件加载、卸载、工具注册、损坏插件处理测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.plugins.loader import PluginLoader
from backend.plugins.registry import LifecycleState, PluginRegistry
from backend.tools.registry import ToolRegistry


def _make_loader(tmp_path: Path, user_dir: Path) -> PluginLoader:
    tool_registry = ToolRegistry()
    plugin_registry = PluginRegistry(tool_registry=tool_registry)
    # 用一个空的 builtin 目录，避免测试受内置插件干扰
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    state_file = tmp_path / "state.json"
    storage_dir = tmp_path / "storage"
    return PluginLoader(
        plugin_registry,
        builtin_dir=builtin_dir,
        user_dir=user_dir,
        state_file=state_file,
        storage_dir=storage_dir,
    )


def _write_plugin(d: Path, plugin_id: str, body: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "id": plugin_id, "name": plugin_id, "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py", "permissions": [],
    }), encoding="utf-8")
    (d / "plugin.py").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# 插件加载
# ---------------------------------------------------------------------------
def test_load_plugin_success(tmp_path: Path, make_plugin):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    body = '''
from weci_sdk import Tool, WeCiPlugin
from pydantic import BaseModel

class Args(BaseModel):
    pass

class T(Tool):
    name = "t1"
    description = "test"
    args_model = Args
    async def execute(self, args, context=None):
        return "ok"

class P(WeCiPlugin):
    def get_tools(self):
        return [T()]
'''
    pdir = make_plugin(user_dir, "mytest", plugin_body=body)
    loader = _make_loader(tmp_path, user_dir)
    entry = loader.load_plugin(pdir)
    assert entry is not None
    assert entry.id == "mytest"
    assert entry.status == "loaded"
    assert entry.lifecycle_state == LifecycleState.READY.value
    assert "t1" in entry.tools


def test_load_plugin_registers_tool_in_registry(tmp_path: Path, make_plugin):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    body = '''
from weci_sdk import Tool, WeCiPlugin
from pydantic import BaseModel

class Args(BaseModel):
    pass

class T(Tool):
    name = "t2"
    description = "test"
    args_model = Args
    async def execute(self, args, context=None):
        return "ok"

class P(WeCiPlugin):
    def get_tools(self):
        return [T()]
'''
    make_plugin(user_dir, "regtest", plugin_body=body)
    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    assert "t2" in loader.registry.list_tools()
    assert "t2" in loader.registry.tool_registry.names()


# ---------------------------------------------------------------------------
# 插件卸载
# ---------------------------------------------------------------------------
def test_unload_plugin_removes_tools(tmp_path: Path, make_plugin):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    body = '''
from weci_sdk import Tool, WeCiPlugin
from pydantic import BaseModel

class Args(BaseModel):
    pass

class T(Tool):
    name = "t_unload"
    description = "test"
    args_model = Args
    async def execute(self, args, context=None):
        return "ok"

class P(WeCiPlugin):
    def get_tools(self):
        return [T()]
'''
    make_plugin(user_dir, "unloadtest", plugin_body=body)
    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    assert "t_unload" in loader.registry.list_tools()

    assert loader.unload_plugin("unloadtest") is True
    assert "t_unload" not in loader.registry.list_tools()
    assert loader.get_plugin("unloadtest") is None


# ---------------------------------------------------------------------------
# 损坏插件处理
# ---------------------------------------------------------------------------
def test_broken_manifest_does_not_crash(tmp_path: Path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    d = user_dir / "broken"
    d.mkdir()
    (d / "manifest.json").write_text("{invalid json")

    loader = _make_loader(tmp_path, user_dir)
    # load_all 不应抛异常
    loader.load_all()
    entry = loader.get_plugin("broken")
    # 失败的插件仍会被记录（用目录名作为 id）
    assert entry is not None
    assert entry.status == "failed"
    assert entry.lifecycle_state == LifecycleState.FAILED.value
    assert entry.error is not None


def test_missing_entry_file_marks_failed(tmp_path: Path, make_plugin):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    d = make_plugin(user_dir, "noentry")
    # 删除入口文件
    (d / "plugin.py").unlink()

    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    entry = loader.get_plugin("noentry")
    assert entry is not None
    assert entry.status == "failed"
    assert "入口文件" in entry.error


def test_plugin_module_import_error_marks_failed(tmp_path: Path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    d = user_dir / "badmod"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "id": "badmod", "name": "badmod", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    }))
    (d / "plugin.py").write_text("raise ImportError('boom')\n")

    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    entry = loader.get_plugin("badmod")
    assert entry is not None
    assert entry.status == "failed"


def test_on_load_exception_marks_failed(tmp_path: Path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    body = '''
from weci_sdk import WeCiPlugin

class P(WeCiPlugin):
    def on_load(self, context):
        raise RuntimeError("on_load boom")
    def get_tools(self):
        return []
'''
    d = user_dir / "onloadfail"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "id": "onloadfail", "name": "onloadfail", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    }))
    (d / "plugin.py").write_text(body)

    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    entry = loader.get_plugin("onloadfail")
    assert entry is not None
    assert entry.status == "failed"
    assert "on_load" in entry.error


# ---------------------------------------------------------------------------
# API version 不兼容
# ---------------------------------------------------------------------------
def test_unsupported_api_version_fails_gracefully(tmp_path: Path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    d = user_dir / "oldapi"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "id": "oldapi", "name": "oldapi", "version": "0.1.0",
        "api_version": "9.9", "entry": "plugin.py",
    }))
    (d / "plugin.py").write_text("from weci_sdk import WeCiPlugin\nclass P(WeCiPlugin):\n    pass\n")

    loader = _make_loader(tmp_path, user_dir)
    loader.load_all()
    entry = loader.get_plugin("oldapi")
    assert entry is not None
    assert entry.status == "failed"
    assert "api_version" in entry.error
