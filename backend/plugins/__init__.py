"""WeCi 插件系统。

核心组件：
  - :class:`PluginLoader`   发现、加载、卸载、安装、移除插件
  - :class:`PluginRegistry` 统一管理插件与工具，暴露 ``tool_registry`` 给 Agent
  - :class:`Manifest`       manifest 校验
  - :class:`PluginContextImpl` 插件上下文实现（受权限约束）

Agent **只**与 ``PluginRegistry.tool_registry`` 交互，不感知具体插件。
"""

from backend.plugins.loader import PluginLoader
from backend.plugins.manifest import Manifest, ManifestError
from backend.plugins.registry import PluginEntry, PluginRegistry

__all__ = [
    "PluginLoader",
    "PluginRegistry",
    "PluginEntry",
    "Manifest",
    "ManifestError",
]
