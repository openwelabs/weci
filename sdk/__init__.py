"""WeCi Plugin SDK —— 插件开发者唯一需要依赖的公开 API。

第三方插件**只应**从 ``sdk`` 导入，不应导入 ``backend.*`` 内部模块。

使用::

    from sdk import WeCiPlugin, Tool, PluginContext, ToolExecutionResult
    from pydantic import BaseModel, Field
"""

from sdk.context import PluginContext
from sdk.permissions import (
    KNOWN_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    PluginPermissionError,
)
from sdk.plugin import WeCiPlugin
from sdk.tool import Tool, ToolExecutionResult

__all__ = [
    "WeCiPlugin",
    "Tool",
    "ToolExecutionResult",
    "PluginContext",
    "PluginPermissionError",
    "KNOWN_PERMISSIONS",
    "PERMISSION_DESCRIPTIONS",
]

__version__ = "0.1.0"
