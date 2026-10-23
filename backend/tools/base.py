"""工具基类（向后兼容层）。

WeCi 的工具基类现在统一定义在 :mod:`sdk.tool`，内置插件与第三方插件都依赖
``sdk.Tool``。此模块保留原导入路径 ``backend.tools.base`` 以兼容历史代码。
"""

from weci_sdk.tool import Tool, ToolExecutionResult, _safe_error_message

__all__ = ["Tool", "ToolExecutionResult"]
