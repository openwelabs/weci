"""插件 Manifest 加载与校验。

每个插件目录必须包含 ``manifest.json``，声明 id、name、version、api_version、
entry、permissions 等元信息。Loader 根据 manifest 决定是否加载。

校验规则：
  - 必填字段：id、name、version、api_version、entry
  - id：小写字母开头，仅含 a-z0-9_-，长度 2-64
  - version：语义化版本（如 0.1.0、1.2.3）
  - api_version：必须在 Core 支持的集合内（当前 0.1）
  - entry：插件目录内的相对路径，不允许绝对路径或 ..
  - permissions：必须全部为已知权限
  - 未知字段：保留在 ``raw`` 中，不阻断加载（向前兼容）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weci_sdk.permissions import KNOWN_PERMISSIONS

# WeCi 当前支持的 Plugin API 版本。插件声明的 api_version 必须在此集合内。
SUPPORTED_API_VERSIONS: frozenset[str] = frozenset({"0.1"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
# 语义化版本：主版本.次版本.修订号，可带预发布后缀（如 0.1.0-beta.1）
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(-(0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(\.(0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
    r"(\+[0-9a-zA-Z-]+(\.[0-9a-zA-Z-]+)*)?$"
)

REQUIRED_FIELDS = ("id", "name", "version", "api_version", "entry")
# 已知字段集合（用于识别未知字段）
KNOWN_FIELDS = frozenset(
    {
        "id", "name", "version", "api_version", "entry",
        "description", "author", "permissions", "config", "default_enabled",
    }
)


class ManifestError(ValueError):
    """Manifest 不合法。错误信息对人类可读。"""


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
    # 未知字段（向前兼容），插件可通过 raw 读取
    unknown_fields: dict[str, Any] = field(default_factory=dict)
    # 原始数据
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        if not isinstance(data, dict):
            raise ManifestError("manifest.json 根节点必须是对象。")

        missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ManifestError(f"manifest 缺少必填字段：{', '.join(missing)}")

        plugin_id = str(data["id"]).strip()
        if not _ID_RE.match(plugin_id):
            raise ManifestError(
                f"插件 id '{plugin_id}' 不合法：须以小写字母开头，"
                f"仅含小写字母、数字、-、_，长度 2-64。"
            )

        version = str(data["version"]).strip()
        if not _VERSION_RE.match(version):
            raise ManifestError(
                f"版本号 '{version}' 不合法：须为语义化版本（如 0.1.0、1.2.3）。"
            )

        api_version = str(data["api_version"]).strip()
        if api_version not in SUPPORTED_API_VERSIONS:
            raise ManifestError(
                f"不支持的 api_version '{api_version}'，"
                f"当前支持：{', '.join(sorted(SUPPORTED_API_VERSIONS))}"
            )

        entry = str(data["entry"]).strip()
        if not entry or entry.startswith("/") or entry.startswith("\\") or ".." in entry.split("/"):
            raise ManifestError(
                f"entry '{entry}' 不合法：必须是插件目录内的相对路径，"
                f"不允许绝对路径或 .. 上级引用。"
            )

        permissions = data.get("permissions") or []
        if not isinstance(permissions, list):
            raise ManifestError("permissions 必须是字符串数组。")
        for p in permissions:
            if not isinstance(p, str):
                raise ManifestError(f"权限必须是字符串，收到：{p!r}")
        unknown_perms = [p for p in permissions if p not in KNOWN_PERMISSIONS]
        if unknown_perms:
            raise ManifestError(
                f"未知权限：{', '.join(unknown_perms)}。"
                f"支持：{', '.join(sorted(KNOWN_PERMISSIONS))}"
            )

        config = data.get("config") or {}
        if not isinstance(config, dict):
            raise ManifestError("config 必须是对象。")

        unknown_fields = {
            k: v for k, v in data.items() if k not in KNOWN_FIELDS
        }

        return cls(
            id=plugin_id,
            name=str(data["name"]).strip(),
            version=version,
            api_version=api_version,
            entry=entry,
            description=str(data.get("description", "")).strip(),
            author=str(data.get("author", "")).strip(),
            permissions=list(permissions),
            config=dict(config),
            default_enabled=bool(data.get("default_enabled", True)),
            unknown_fields=unknown_fields,
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
    except OSError as exc:
        raise ManifestError(f"无法读取 manifest.json：{exc}") from exc
    return Manifest.from_dict(data)
