"""WeCi Plugin SDK —— 插件开发者唯一需要依赖的公开 API。

第三方插件**只应**从 ``weci_sdk`` 导入，不应导入 ``backend.*`` 内部模块。
为兼容已有代码，也可以从 ``sdk`` 导入（``sdk`` 是 ``weci_sdk`` 的别名包）。

使用::

    from weci_sdk import WeCiPlugin, Tool, ToolContext, PluginContext, ToolResult
    from weci_sdk.permissions import KNOWN_PERMISSIONS, PermissionDenied
    from pydantic import BaseModel, Field

版本说明：
  - ``__version__`` 是 SDK 自身版本号。
  - 插件声明的 ``api_version``（见 manifest.json）是 Plugin API 兼容版本，
    当前支持 ``0.1``。Core 内部重构不会改变 SDK 公开 API。
"""

from weci_sdk.context import FileCap, HttpCap, PluginContext, StorageCap, ToolContext
from weci_sdk.permissions import (
    KNOWN_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    PermissionDenied,
    PluginPermissionError,
)
from weci_sdk.plugin import WeCiPlugin
from weci_sdk.tool import Tool, ToolExecutionResult, ToolResult

__all__ = [
    "WeCiPlugin",
    "Tool",
    "ToolResult",
    "ToolExecutionResult",
    "ToolContext",
    "PluginContext",
    "HttpCap",
    "FileCap",
    "StorageCap",
    "PermissionDenied",
    "PluginPermissionError",
    "KNOWN_PERMISSIONS",
    "PERMISSION_DESCRIPTIONS",
]

# SDK 自身版本号（与 manifest 中的 api_version 不同）
__version__ = "0.1.0"
