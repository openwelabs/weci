"""WeCi Plugin SDK · 插件基类。

所有插件继承 ``WeCiPlugin`` 并实现所需的钩子。

最小可用插件::

    from weci_sdk import WeCiPlugin, Tool

    class MyTool(Tool):
        name = "my_tool"
        description = "..."
        args_model = MyArgs
        async def execute(self, args, context):
            return "..."

    class MyPlugin(WeCiPlugin):
        def get_tools(self):
            return [MyTool()]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from weci_sdk.tool import Tool

if TYPE_CHECKING:
    from weci_sdk.context import PluginContext


class WeCiPlugin:
    """插件基类。子类按需重写钩子方法。"""

    # 插件的 manifest 信息由 Loader 注入，插件本身无需声明
    manifest: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_load(self, context: "PluginContext") -> None:
        """插件被加载后调用。可在此初始化资源、订阅事件。"""

    def on_unload(self) -> None:
        """插件被卸载前调用。释放资源。"""

    # ------------------------------------------------------------------
    # 能力提供
    # ------------------------------------------------------------------
    def get_tools(self) -> list[Tool]:
        """返回插件提供的工具列表。默认空。"""
        return []

    def get_commands(self) -> list[Any]:
        """返回插件提供的 CLI 命令。预留，本阶段暂未启用。"""
        return []

    def get_hooks(self) -> dict[str, Any]:
        """返回插件注册的钩子。预留，本阶段暂未启用。"""
        return {}

    # ------------------------------------------------------------------
    # 元信息快捷访问
    # ------------------------------------------------------------------
    @property
    def metadata(self) -> dict[str, Any]:
        """插件元信息（来自 manifest 的只读副本）。"""
        return dict(self.manifest)

    @property
    def id(self) -> str:
        return str(self.manifest.get("id", ""))

    @property
    def name(self) -> str:
        return str(self.manifest.get("name", self.id))

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "0.0.0"))

    @property
    def api_version(self) -> str:
        return str(self.manifest.get("api_version", ""))

    @property
    def author(self) -> str:
        return str(self.manifest.get("author", ""))


__all__ = ["WeCiPlugin"]
