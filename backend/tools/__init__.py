"""WeCi 工具系统。

本包只提供工具定义与注册的基础设施，不在 import 时做任何全局副作用。
具体工具（calculator / current_time / web_search）由应用入口在组合根处
显式注册到 ToolRegistry，便于按环境裁剪与未来扩展。
"""

from backend.tools.base import Tool, ToolExecutionResult
from backend.tools.permission import (
    AllowListPermission,
    ToolPermission,
    always_allow,
)
from backend.tools.registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolPermission",
    "AllowListPermission",
    "always_allow",
]
