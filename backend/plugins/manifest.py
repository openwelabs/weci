"""插件 Manifest 加载与校验。

每个插件目录必须包含 ``manifest.json``，声明 id、name、version、api_version、
entry、permissions 等元信息。Loader 根据 manifest 决定是否加载。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdk.permissions import KNOWN_PERMISSIONS

# WeCi 当前支持的 Plugin API 版本。插件声明的 api_version 必须在此集合内。
SUPPORTED_API_VERSIONS: frozenset[str] = frozenset({"0.1"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

REQUIRED_FIELDS = ("id", "name", "version", "api_version", "entry")


class ManifestError(ValueError):
    """Manifest 不合法。"""


@dataclass
class Manifest:
    """解析并校验后的插件清单。"""

    id: str
    name: str
    version: str
    api_version: str
    entry: str
    description: str = ""
    author: str = ""
    permissions: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    # 插件首次安装时的默认启用状态。高风险插件可设为 false。
    default_enabled: bool = True
    # 原始数据，供插件读取扩展字段
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ManifestError(f"manifest 缺少必填字段：{', '.join(missing)}")

        plugin_id = str(data["id"]).strip()
        if not _ID_RE.match(plugin_id):
            raise ManifestError(
                f"插件 id '{plugin_id}' 不合法：须以小写字母开头，仅含小写字母、数字、-、_，长度 2-64。"
            )

        api_version = str(data["api_version"]).strip()
        if api_version not in SUPPORTED_API_VERSIONS:
            raise ManifestError(
                f"不支持的 api_version '{api_version}'，支持：{', '.join(sorted(SUPPORTED_API_VERSIONS))}"
            )

        entry = str(data["entry"]).strip()
        if not entry or entry.startswith("/") or ".." in entry:
            raise ManifestError(f"entry '{entry}' 不合法：必须是插件目录内的相对路径。")

        permissions = list(data.get("permissions") or [])
        unknown = [p for p in permissions if p not in KNOWN_PERMISSIONS]
        if unknown:
            raise ManifestError(
                f"未知权限：{', '.join(unknown)}。支持：{', '.join(sorted(KNOWN_PERMISSIONS))}"
            )

        return cls(
            id=plugin_id,
            name=str(data["name"]).strip(),
            version=str(data["version"]).strip(),
            api_version=api_version,
            entry=entry,
            description=str(data.get("description", "")).strip(),
            author=str(data.get("author", "")).strip(),
            permissions=permissions,
            config=dict(data.get("config") or {}),
            default_enabled=bool(data.get("default_enabled", True)),
            raw=dict(data),
        )


def load_manifest(plugin_dir: Path) -> Manifest:
    """从插件目录读取并校验 manifest.json。"""
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ManifestError(f"未找到 manifest.json：{manifest_path}")
    try:
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest.json 根节点必须是对象。")
    return Manifest.from_dict(data)
