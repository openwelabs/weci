"""WeCi Plugin SDK · 权限词汇表与错误类型。

权限采用 ``<scope>:<action>`` 形式，例如 ``workspace:read``。这是 **唯一的**
权限词汇来源：插件 manifest 声明权限、Tool 声明 ``permissions``、Core 的
Permission Manager 判定权限，都使用这里的常量。

执行侧的强制逻辑在 ``backend/services/permissions.py``（SDK 不依赖 Core）。

历史兼容：早期 manifest 使用扁平权限（``network`` / ``filesystem.read`` 等），
``LEGACY_ALIASES`` 把它们映射为标准权限名，旧插件无需修改即可继续工作。

> 安全边界：第三方 Python 插件本质上可以执行任意代码，权限系统是「约定 +
> 运行时拦截」，并非沙箱。安装插件即意味着信任其代码。
"""

from __future__ import annotations

# 风险等级：low = 默认允许；medium / high = 默认需要用户确认
PERMISSION_RISK: dict[str, str] = {
    "workspace:read": "low",
    "workspace:write": "high",
    "workspace:delete": "high",
    "command:execute": "high",
    "network:access": "high",
    "memory:read": "low",
    "memory:write": "medium",
    "mcp:use": "high",
    "plugin:use": "low",
    "clipboard:read": "medium",
    "clipboard:write": "medium",
    "storage:read": "low",
    "storage:write": "low",
}

# 各权限的人类可读说明
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "workspace:read": "读取 Workspace 内的文件",
    "workspace:write": "写入 / 修改 Workspace 内的文件",
    "workspace:delete": "删除 Workspace 内的文件",
    "command:execute": "在本机执行 Shell 命令",
    "network:access": "访问网络（发起 HTTP 请求 / 联网搜索）",
    "memory:read": "读取用户长期记忆",
    "memory:write": "写入或删除用户长期记忆",
    "mcp:use": "调用 MCP Server 提供的工具",
    "plugin:use": "使用插件提供的工具",
    "clipboard:read": "读取系统剪贴板",
    "clipboard:write": "写入系统剪贴板",
    "storage:read": "读取插件私有存储",
    "storage:write": "写入插件私有存储",
}

# 标准权限集合
SCOPED_PERMISSIONS: frozenset[str] = frozenset(PERMISSION_RISK)

# 历史（扁平）权限 → 标准权限
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "network": ("network:access",),
    "filesystem.read": ("workspace:read",),
    "filesystem.write": ("workspace:write",),
    "filesystem.delete": ("workspace:delete",),
    "process": ("command:execute",),
    "clipboard": ("clipboard:read", "clipboard:write"),
    "memory": ("memory:read", "memory:write"),
    "storage": ("storage:read", "storage:write"),
}

# Manifest 允许声明的全部权限（新版 + 历史别名）
KNOWN_PERMISSIONS: frozenset[str] = SCOPED_PERMISSIONS | frozenset(LEGACY_ALIASES)

# 默认自动放行的低风险权限；其余一律走确认流程（Default Deny 的落地方式）
DEFAULT_AUTO_ALLOW: frozenset[str] = frozenset(
    {name for name, risk in PERMISSION_RISK.items() if risk == "low"}
)


def normalize_permissions(permissions) -> list[str]:
    """把权限声明（含历史别名）展开为标准权限名，保持顺序并去重。"""
    result: list[str] = []
    for raw in permissions or ():
        name = str(raw).strip()
        if not name:
            continue
        for item in LEGACY_ALIASES.get(name, (name,)):
            if item in PERMISSION_RISK and item not in result:
                result.append(item)
    return result


def describe_permission(permission: str) -> dict[str, str]:
    return {
        "permission": permission,
        "description": PERMISSION_DESCRIPTIONS.get(permission, permission),
        "risk": PERMISSION_RISK.get(permission, "high"),
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
    "PERMISSION_RISK",
    "PERMISSION_DESCRIPTIONS",
    "SCOPED_PERMISSIONS",
    "KNOWN_PERMISSIONS",
    "LEGACY_ALIASES",
    "DEFAULT_AUTO_ALLOW",
    "normalize_permissions",
    "describe_permission",
    "PermissionDenied",
    "PluginPermissionError",
]
