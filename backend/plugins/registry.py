"""Plugin Registry：统一管理插件与其提供的工具。

对外暴露：
  - 插件级：register_plugin / unregister_plugin / get_plugin / list_plugins
  - 工具级：register_tool / unregister_tool / get_tool / list_tools
  - Agent 通过 ``tool_registry`` 属性获取工具注册表（不感知插件存在）

插件生命周期：Discover → Validate → Load → Register → Ready → Disable → Unload
加载失败的插件不会让 WeCi 崩溃，只会记录错误并标记为 failed。
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

from weci_sdk.plugin import WeCiPlugin
from weci_sdk.tool import Tool
from backend.tools.registry import ToolRegistry

logger = logging.getLogger("weci.plugins")


class LifecycleState(str, enum.Enum):
    """插件生命周期状态。

    DISCOVER → VALIDATE → LOAD → REGISTER → READY → DISABLE → UNLOAD
    """

    DISCOVERED = "discovered"
    VALIDATING = "validating"
    LOADING = "loading"
    REGISTERING = "registering"
    READY = "ready"          # 已启用并可服务
    DISABLED = "disabled"    # 已禁用（工具未注册）
    FAILED = "failed"        # 加载/校验失败
    UNLOADED = "unloaded"    # 已卸载


@dataclass
class PluginEntry:
    """注册表中的插件条目。"""

    id: str
    manifest: dict[str, Any]
    instance: WeCiPlugin | None
    path: str
    enabled: bool = True
    status: str = "loaded"  # loaded / failed / disabled（运行态摘要，向后兼容）
    lifecycle_state: str = LifecycleState.READY.value
    error: str | None = None
    tools: list[str] = field(default_factory=list)


class PluginRegistry:
    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self._plugins: dict[str, PluginEntry] = {}
        # name -> plugin_id（工具名到所属插件的映射，便于卸载）
        self._tool_owner: dict[str, str] = {}
        self.tool_registry: ToolRegistry = tool_registry or ToolRegistry()

    # ------------------------------------------------------------------
    # 插件管理
    # ------------------------------------------------------------------
    def register_plugin(
        self,
        plugin_id: str,
        manifest: dict[str, Any],
        instance: WeCiPlugin | None,
        path: str,
        *,
        enabled: bool = True,
        error: str | None = None,
    ) -> PluginEntry:
        entry = PluginEntry(
            id=plugin_id,
            manifest=manifest,
            instance=instance,
            path=path,
            enabled=enabled,
            status="failed" if error else ("disabled" if not enabled else "loaded"),
            lifecycle_state=(
                LifecycleState.FAILED.value if error
                else LifecycleState.DISABLED.value if not enabled
                else LifecycleState.READY.value
            ),
            error=error,
        )
        self._plugins[plugin_id] = entry

        # 注册该插件提供的工具
        if instance is not None and enabled and not error:
            self._register_plugin_tools(entry)

        return entry

    def unregister_plugin(self, plugin_id: str) -> bool:
        entry = self._plugins.get(plugin_id)
        if entry is None:
            return False
        # 先卸载其工具
        for tool_name in list(entry.tools):
            self.unregister_tool(tool_name)
        try:
            if entry.instance is not None:
                entry.instance.on_unload()
        except Exception:  # noqa: BLE001
            logger.exception("插件 %s on_unload 出错", plugin_id)
        entry.lifecycle_state = LifecycleState.UNLOADED.value
        del self._plugins[plugin_id]
        return True

    def get_plugin(self, plugin_id: str) -> PluginEntry | None:
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[PluginEntry]:
        return list(self._plugins.values())

    def enable_plugin(self, plugin_id: str) -> bool:
        entry = self._plugins.get(plugin_id)
        if entry is None or entry.instance is None:
            return False
        entry.enabled = True
        entry.status = "loaded"
        entry.lifecycle_state = LifecycleState.READY.value
        entry.error = None
        self._register_plugin_tools(entry)
        logger.info("插件已启用: %s", plugin_id)
        return True

    def disable_plugin(self, plugin_id: str) -> bool:
        entry = self._plugins.get(plugin_id)
        if entry is None:
            return False
        for tool_name in list(entry.tools):
            self.unregister_tool(tool_name)
        entry.enabled = False
        entry.status = "disabled"
        entry.lifecycle_state = LifecycleState.DISABLED.value
        logger.info("插件已禁用: %s", plugin_id)
        return True

    # ------------------------------------------------------------------
    # 工具管理（由插件注册，Agent 不直接调用）
    # ------------------------------------------------------------------
    def register_tool(self, tool: Tool, plugin_id: str, context=None) -> None:
        if tool.name in self._tool_owner:
            raise ValueError(f"工具名冲突：{tool.name} 已由插件 {self._tool_owner[tool.name]} 注册")
        self.tool_registry.register(tool, context)
        self._tool_owner[tool.name] = plugin_id
        entry = self._plugins.get(plugin_id)
        if entry is not None and tool.name not in entry.tools:
            entry.tools.append(tool.name)

    def unregister_tool(self, name: str) -> None:
        self.tool_registry.unregister(name)
        owner = self._tool_owner.pop(name, None)
        if owner:
            entry = self._plugins.get(owner)
            if entry and name in entry.tools:
                entry.tools.remove(name)

    def get_tool(self, name: str) -> Tool | None:
        return self.tool_registry.get(name)

    def list_tools(self) -> list[str]:
        return self.tool_registry.names()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _register_plugin_tools(self, entry: PluginEntry) -> None:
        if entry.instance is None:
            return
        try:
            tools = entry.instance.get_tools() or []
        except Exception:  # noqa: BLE001
            logger.exception("插件 %s get_tools 出错", entry.id)
            entry.status = "failed"
            entry.error = "get_tools() 抛出异常"
            return
        for tool in tools:
            try:
                self.register_tool(tool, entry.id, context=getattr(entry.instance, "_context", None))
            except ValueError as exc:
                logger.error("插件 %s 注册工具 %s 失败：%s", entry.id, tool.name, exc)
