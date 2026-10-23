"""WeCi Plugin SDK · 权限常量。

插件在 manifest.json 的 ``permissions`` 字段中声明所需权限。
Core 在创建 ``PluginContext`` 时按声明授予，未声明的能力调用会被拒绝。

当前支持的权限：
  - ``network``    允许通过 context.http_request 发起网络请求
  - ``storage``    允许读写插件私有 KV 存储
  - ``filesystem.read``  / ``filesystem.write``  预留
  - ``process``    预留
  - ``clipboard``  预留
"""

from __future__ import annotations

# 已知权限集合（Core 会校验 manifest 中的权限是否在此集合内）
KNOWN_PERMISSIONS: frozenset[str] = frozenset(
    {
        "network",
        "storage",
        "filesystem.read",
        "filesystem.write",
        "process",
        "clipboard",
    }
)

# 各权限对应的人类可读说明
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "network": "发起网络请求",
    "storage": "读写插件私有存储",
    "filesystem.read": "读取文件系统",
    "filesystem.write": "写入文件系统",
    "process": "执行系统进程",
    "clipboard": "访问剪贴板",
}


class PluginPermissionError(PermissionError):
    """插件尝试调用未授权能力时抛出。"""

    def __init__(self, plugin_id: str, permission: str, capability: str) -> None:
        self.plugin_id = plugin_id
        self.permission = permission
        self.capability = capability
        super().__init__(
            f"插件 '{plugin_id}' 未声明 '{permission}' 权限，无法调用 {capability}。"
        )
