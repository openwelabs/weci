"""WeCi Plugin SDK · 插件上下文接口。

插件通过 ``PluginContext`` 与 WeCi Core 交互，**不能**直接访问 Core 内部对象。
Core 提供受控能力：logger、config、http、storage、filesystem、process、clipboard、events。

每个能力都受 manifest 中声明的 ``permissions`` 约束；未声明权限的调用会抛
``PermissionDenied``。

> 安全边界：第三方 Python 插件本质上拥有执行任意 Python 的能力，因此这里的权限
> 系统是「约定 + 运行时拦截」，并非沙箱。安装插件即意味着信任其代码。
> 真正的隔离（subprocess / WASM / container）属于未来工作。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# 子命名空间接口（http / files / storage）
# ---------------------------------------------------------------------------
class HttpCap(ABC):
    """网络能力子命名空间。需 ``network`` 权限。"""

    @abstractmethod
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发起 HTTP 请求。返回 ``{"status", "headers", "text", "json"}``。"""


class FileCap(ABC):
    """文件系统能力子命名空间。"""

    @abstractmethod
    def read(self, path: str) -> str:
        """读取文本文件。需 ``filesystem.read`` 权限。"""

    @abstractmethod
    def write(self, path: str, content: str) -> None:
        """写入文本文件（覆盖）。需 ``filesystem.write`` 权限。"""

    @abstractmethod
    def list_dir(self, path: str) -> list[dict[str, Any]]:
        """列出目录内容。需 ``filesystem.read`` 权限。"""


class StorageCap(ABC):
    """插件私有 KV 存储子命名空间。需 ``storage`` 权限。"""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        ...


# ---------------------------------------------------------------------------
# ToolContext —— 工具执行时可用的上下文
# ---------------------------------------------------------------------------
class ToolContext(ABC):
    """传递给 ``Tool.execute`` 的运行时上下文。

    它提供了工具执行时可调用的所有受控能力。``PluginContext`` 是
    ``ToolContext`` 的超集（额外包含生命周期相关属性）。
    """

    # -- 元信息 --
    @property
    @abstractmethod
    def plugin_id(self) -> str:
        ...

    @property
    @abstractmethod
    def permissions(self) -> list[str]:
        """插件被授予的权限列表。"""

    # -- 日志 --
    @property
    @abstractmethod
    def logger(self):
        """绑定了插件 id 的 ``logging.Logger``。"""

    # -- 配置（只读）--
    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        ...

    # -- 网络（network）--
    @property
    @abstractmethod
    def http(self) -> HttpCap:
        ...

    @abstractmethod
    async def http_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """``context.http.request`` 的扁平别名。"""

    # -- 存储（storage）--
    @property
    @abstractmethod
    def storage(self) -> StorageCap:
        ...

    @abstractmethod
    def storage_get(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def storage_set(self, key: str, value: Any) -> None:
        ...

    # -- 文件系统（filesystem.read / filesystem.write）--
    @property
    @abstractmethod
    def files(self) -> FileCap:
        ...

    @abstractmethod
    def read_file(self, path: str) -> str:
        ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        ...

    @abstractmethod
    def list_dir(self, path: str) -> list[dict[str, Any]]:
        ...

    # -- 进程（process）--
    @abstractmethod
    async def run_command(
        self,
        command: str | list[str],
        *,
        cwd: str | None = None,
        timeout: float = 30.0,
        input: str | None = None,
    ) -> dict[str, Any]:
        """执行 shell 命令。返回 ``{"returncode", "stdout", "stderr"}``。"""

    # -- 剪贴板（clipboard）--
    @abstractmethod
    def clipboard_get(self) -> str:
        ...

    @abstractmethod
    def clipboard_set(self, text: str) -> None:
        ...

    # -- 事件总线 --
    @abstractmethod
    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        ...

    @abstractmethod
    def on(self, event: str, handler) -> None:
        ...


# ---------------------------------------------------------------------------
# PluginContext —— 插件完整上下文
# ---------------------------------------------------------------------------
class PluginContext(ToolContext):
    """插件运行时上下文。

    在 ``ToolContext`` 的基础上，生命周期钩子（``on_load``）也会收到它。
    """


__all__ = ["PluginContext", "ToolContext", "HttpCap", "FileCap", "StorageCap"]
