"""WeCi Plugin SDK · 插件上下文接口。

插件通过 ``PluginContext`` 与 WeCi Core 交互，**不能**直接访问 Core 内部对象。
Core 提供受控能力：logger、config、http、storage、events。

每个能力都受 manifest 中声明的 ``permissions`` 约束；未声明权限的调用会抛
``PermissionError``。

注意：第三方 Python 插件本质上拥有执行任意 Python 的能力，因此这里的权限
系统是「约定 + 运行时拦截」，并非沙箱。安装插件即意味着信任其代码。
真正的隔离（subprocess / WASM / container）属于未来工作。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PluginContext(ABC):
    """插件运行时上下文。所有方法都受权限系统约束。"""

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def logger(self):
        """一个绑定了插件 id 的 ``logging.Logger``。"""

    # ------------------------------------------------------------------
    # 配置（只读）
    # ------------------------------------------------------------------
    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        """读取插件配置。

        配置来源：manifest.json 的 ``config`` 字段、环境变量
        ``WECI_PLUGIN_<PLUGIN_ID>_<KEY>``，以及 Core 注入的默认值。
        """

    # ------------------------------------------------------------------
    # 网络（需 network 权限）
    # ------------------------------------------------------------------
    @abstractmethod
    async def http_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发起 HTTP 请求。需 ``network`` 权限。

        返回 ``{"status": int, "headers": dict, "text": str, "json": Any | None}``。
        """

    # ------------------------------------------------------------------
    # 存储（需 storage 权限）
    # ------------------------------------------------------------------
    @abstractmethod
    def storage_get(self, key: str, default: Any = None) -> Any:
        """读取插件私有 KV 存储。需 ``storage`` 权限。"""

    @abstractmethod
    def storage_set(self, key: str, value: Any) -> None:
        """写入插件私有 KV 存储。需 ``storage`` 权限。"""

    # ------------------------------------------------------------------
    # 进程（需 process 权限）
    # ------------------------------------------------------------------
    @abstractmethod
    async def run_command(
        self,
        command: str | list[str],
        *,
        cwd: str | None = None,
        timeout: float = 30.0,
        input: str | None = None,
    ) -> dict[str, Any]:
        """执行 shell 命令。需 ``process`` 权限。

        返回 ``{"returncode": int, "stdout": str, "stderr": str}``。
        命令超时会被终止并返回非零 returncode。
        """

    # ------------------------------------------------------------------
    # 文件系统（需 filesystem.read / filesystem.write 权限）
    # ------------------------------------------------------------------
    @abstractmethod
    def read_file(self, path: str) -> str:
        """读取文本文件。需 ``filesystem.read`` 权限。"""

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """写入文本文件（覆盖）。需 ``filesystem.write`` 权限。"""

    @abstractmethod
    def list_dir(self, path: str) -> list[dict[str, Any]]:
        """列出目录内容。需 ``filesystem.read`` 权限。

        返回 ``[{"name": str, "type": "file"|"dir", "size": int}]``。
        """

    # ------------------------------------------------------------------
    # 剪贴板（需 clipboard 权限）
    # ------------------------------------------------------------------
    @abstractmethod
    def clipboard_get(self) -> str:
        """读取系统剪贴板文本。需 ``clipboard`` 权限。"""

    @abstractmethod
    def clipboard_set(self, text: str) -> None:
        """写入系统剪贴板。需 ``clipboard`` 权限。"""

    # ------------------------------------------------------------------
    # 事件总线
    # ------------------------------------------------------------------
    @abstractmethod
    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """向事件总线发布事件。"""

    @abstractmethod
    def on(self, event: str, handler) -> None:
        """订阅事件总线。"""

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def plugin_id(self) -> str:
        ...

    @property
    @abstractmethod
    def permissions(self) -> list[str]:
        """插件被授予的权限列表。"""
