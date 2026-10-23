"""WeCi Plugin SDK · 权限常量与错误类型。

插件在 manifest.json 的 ``permissions`` 字段中声明所需权限。
Core 在创建 ``PluginContext`` 时按声明授予，未声明的能力调用会被拒绝。

这是「能力控制接口」，**不是 Python 沙箱**。第三方 Python 插件本质上可以执行
任意代码，权限系统只提供约定级别的运行时拦截。安装插件即意味着信任其代码。
"""

from __future__ import annotations

# 第一版支持的权限集合（Core 会校验 manifest 中的权限是否在此集合内）
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


class PermissionDenied(PermissionError):
    """插件尝试调用未授权能力时抛出。

    这是第三方插件最常遇到的权限错误类型，也可作为 SDK 公开名称使用。
    ``PluginPermissionError`` 是它的别名，保留向后兼容。
    """

    def __init__(self, plugin_id: str, permission: str, capability: str) -> None:
        self.plugin_id = plugin_id
        self.permission = permission
        self.capability = capability
        super().__init__(
            f"插件 '{plugin_id}' 未声明 '{permission}' 权限，无法调用 {capability}。"
        )


# 向后兼容别名
PluginPermissionError = PermissionDenied


__all__ = [
    "KNOWN_PERMISSIONS",
    "PERMISSION_DESCRIPTIONS",
    "PermissionDenied",
    "PluginPermissionError",
]
