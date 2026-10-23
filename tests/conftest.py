"""WeCi Plugin SDK v0.1 测试套件。

覆盖：manifest 校验、API version 兼容、插件加载/卸载、工具注册/执行、
权限拒绝、插件存储隔离、损坏插件处理、CLI 命令、示例与模板插件。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_plugin_dir(tmp_path: Path) -> Path:
    """创建一个空的临时插件根目录。"""
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture
def make_plugin():
    """创建一个最小可用插件目录的工厂 fixture。"""

    def _make(
        base: Path,
        plugin_id: str,
        *,
        name: str | None = None,
        version: str = "0.1.0",
        api_version: str = "0.1",
        entry: str = "plugin.py",
        permissions: list[str] | None = None,
        default_enabled: bool = True,
        plugin_body: str = "",
    ) -> Path:
        d = base / plugin_id
        d.mkdir(parents=True, exist_ok=True)

        manifest = {
            "id": plugin_id,
            "name": name or plugin_id,
            "version": version,
            "api_version": api_version,
            "entry": entry,
            "permissions": permissions or [],
            "default_enabled": default_enabled,
        }
        (d / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        body = plugin_body or f'''
from weci_sdk import WeCiPlugin

class {plugin_id.replace("-", "_").title().replace("_","")}Plugin(WeCiPlugin):
    def get_tools(self):
        return []
'''
        (d / entry).write_text(body, encoding="utf-8")
        return d

    return _make


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    d = tmp_path / "storage"
    d.mkdir()
    return d
