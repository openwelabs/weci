"""权限系统与插件存储隔离测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.plugins.context import PluginContextImpl
from weci_sdk.permissions import KNOWN_PERMISSIONS, PermissionDenied


# ---------------------------------------------------------------------------
# 权限拒绝
# ---------------------------------------------------------------------------
def test_no_network_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied) as exc:
        # http_request 是 async，用 asyncio 跑
        import asyncio
        asyncio.run(ctx.http_request("GET", "http://example.com"))
    assert exc.value.permission == "network"


def test_no_storage_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.storage_get("k")
    with pytest.raises(PermissionDenied):
        ctx.storage_set("k", "v")


def test_no_filesystem_read_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.read_file("/etc/passwd")
    with pytest.raises(PermissionDenied):
        ctx.list_dir("/tmp")


def test_no_filesystem_write_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.write_file("/tmp/x", "x")


def test_no_process_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        import asyncio
        asyncio.run(ctx.run_command("echo hi"))


def test_no_clipboard_permission_denied(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.clipboard_get()
    with pytest.raises(PermissionDenied):
        ctx.clipboard_set("x")


def test_known_permissions_complete():
    expected = {"network", "storage", "filesystem.read", "filesystem.write", "process", "clipboard"}
    assert expected.issubset(KNOWN_PERMISSIONS)


# ---------------------------------------------------------------------------
# 子命名空间权限同样生效
# ---------------------------------------------------------------------------
def test_files_subnamespace_requires_permission(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.files.read("/etc/passwd")


def test_storage_subnamespace_requires_permission(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=storage_dir
    )
    with pytest.raises(PermissionDenied):
        ctx.storage.get("k")


# ---------------------------------------------------------------------------
# Plugin Storage 隔离
# ---------------------------------------------------------------------------
def test_storage_isolated_by_plugin_id(storage_dir: Path):
    ctx_a = PluginContextImpl(
        plugin_id="pluginA", permissions=["storage"], storage_dir=storage_dir
    )
    ctx_b = PluginContextImpl(
        plugin_id="pluginB", permissions=["storage"], storage_dir=storage_dir
    )

    ctx_a.storage_set("secret", "A-value")

    # pluginB 读不到 pluginA 的数据
    assert ctx_b.storage_get("secret") is None
    # pluginA 能读到自己的数据
    assert ctx_a.storage_get("secret") == "A-value"


def test_storage_persists_across_instances(storage_dir: Path):
    ctx1 = PluginContextImpl(
        plugin_id="p", permissions=["storage"], storage_dir=storage_dir
    )
    ctx1.storage_set("k", "v1")

    ctx2 = PluginContextImpl(
        plugin_id="p", permissions=["storage"], storage_dir=storage_dir
    )
    assert ctx2.storage_get("k") == "v1"


def test_storage_uses_per_plugin_directory(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="myplugin", permissions=["storage"], storage_dir=storage_dir
    )
    ctx.storage_set("k", "v")
    # 应该有 myplugin 目录
    plugin_dir = storage_dir / "myplugin"
    assert plugin_dir.is_dir()
    assert (plugin_dir / "store.json").is_file()


# ---------------------------------------------------------------------------
# PluginContext 元信息
# ---------------------------------------------------------------------------
def test_context_plugin_id_and_permissions(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="myid", permissions=["network"], storage_dir=storage_dir
    )
    assert ctx.plugin_id == "myid"
    assert ctx.permissions == ["network"]
    assert ctx.logger is not None


def test_get_config_from_manifest_config(storage_dir: Path):
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], config={"timeout": 5}, storage_dir=storage_dir
    )
    assert ctx.get_config("timeout") == 5
    assert ctx.get_config("missing", "default") == "default"
