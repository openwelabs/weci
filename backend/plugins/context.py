"""PluginContext 的 Core 实现。

插件拿到的是这个对象（但只看到 ``sdk.context.PluginContext`` 接口）。
所有受权限保护的能力在调用前检查插件是否声明了对应权限。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import httpx

from weci_sdk.context import FileCap, HttpCap, PluginContext, StorageCap
from weci_sdk.permissions import PluginPermissionError


class PluginContextImpl(PluginContext):
    """Core 提供的插件上下文实现。"""

    def __init__(
        self,
        plugin_id: str,
        permissions: list[str],
        *,
        config: dict[str, Any] | None = None,
        storage_dir: Path | None = None,
        event_bus: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._permissions = list(permissions)
        self._config = dict(config or {})
        self._storage_dir = storage_dir
        self._event_bus = event_bus
        # 复用 Core 的共享 http client；若未提供则按需创建
        self._http_client = http_client
        self._logger = logging.getLogger(f"weci.plugin.{plugin_id}")
        self._storage_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # 权限检查
    # ------------------------------------------------------------------
    def _require(self, permission: str, capability: str) -> None:
        if permission not in self._permissions:
            raise PluginPermissionError(self._plugin_id, permission, capability)

    # ------------------------------------------------------------------
    # 子命名空间（http / files / storage）
    # ------------------------------------------------------------------
    class _Http(HttpCap):
        def __init__(self, ctx: "PluginContextImpl") -> None:
            self._ctx = ctx

        async def request(self, method, url, *, headers=None, params=None, json=None, timeout=30.0):
            return await self._ctx.http_request(
                method, url, headers=headers, params=params, json=json, timeout=timeout
            )

    class _Files(FileCap):
        def __init__(self, ctx: "PluginContextImpl") -> None:
            self._ctx = ctx

        def read(self, path: str) -> str:
            return self._ctx.read_file(path)

        def write(self, path: str, content: str) -> None:
            self._ctx.write_file(path, content)

        def list_dir(self, path: str) -> list[dict[str, Any]]:
            return self._ctx.list_dir(path)

    class _Storage(StorageCap):
        def __init__(self, ctx: "PluginContextImpl") -> None:
            self._ctx = ctx

        def get(self, key: str, default: Any = None) -> Any:
            return self._ctx.storage_get(key, default)

        def set(self, key: str, value: Any) -> None:
            self._ctx.storage_set(key, value)

    @property
    def http(self) -> HttpCap:
        return self._Http(self)

    @property
    def files(self) -> FileCap:
        return self._Files(self)

    @property
    def storage(self) -> StorageCap:
        return self._Storage(self)

    # ------------------------------------------------------------------
    # PluginContext 接口
    # ------------------------------------------------------------------
    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def permissions(self) -> list[str]:
        return list(self._permissions)

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def get_config(self, key: str, default: Any = None) -> Any:
        if key in self._config:
            return self._config[key]
        import os

        env_key = f"WECI_PLUGIN_{self._plugin_id.upper().replace('-', '_')}_{key.upper()}"
        return os.getenv(env_key, default)

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
        self._require("network", "context.http_request")
        client = self._http_client or httpx.AsyncClient(timeout=timeout)
        owns_client = self._http_client is None
        try:
            response = await client.request(
                method, url, headers=headers, params=params, json=json, timeout=timeout
            )
            body_text = response.text
            body_json: Any = None
            try:
                body_json = response.json()
            except Exception:  # noqa: BLE001
                body_json = None
            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "text": body_text,
                "json": body_json,
            }
        finally:
            if owns_client:
                await client.aclose()

    # ---- 存储（按 plugin_id 隔离的 JSON KV，插件私有）----
    def _storage_path(self) -> Path | None:
        if self._storage_dir is None:
            return None
        # 每个插件独立目录：<storage_dir>/<plugin_id>/store.json
        path = self._storage_dir / self._plugin_id / "store.json"
        return path

    def _load_storage(self) -> dict[str, Any]:
        if self._storage_cache is not None:
            return self._storage_cache
        path = self._storage_path()
        if path and path.is_file():
            try:
                self._storage_cache = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._storage_cache = {}
        else:
            self._storage_cache = {}
        return self._storage_cache

    def _persist_storage(self) -> None:
        path = self._storage_path()
        if path is None or self._storage_cache is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._storage_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def storage_get(self, key: str, default: Any = None) -> Any:
        self._require("storage", "context.storage_get")
        return self._load_storage().get(key, default)

    def storage_set(self, key: str, value: Any) -> None:
        self._require("storage", "context.storage_set")
        store = self._load_storage()
        store[key] = value
        self._persist_storage()

    # ---- 进程（需 process 权限）----
    async def run_command(
        self,
        command: str | list[str],
        *,
        cwd: str | None = None,
        timeout: float = 30.0,
        input: str | None = None,
    ) -> dict[str, Any]:
        self._require("process", "context.run_command")
        if isinstance(command, str):
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input.encode("utf-8") if input is not None else None),
                timeout=timeout,
            )
            returncode = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"command timed out after {timeout}s",
            }
        return {
            "returncode": returncode or 0,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
        }

    # ---- 文件系统 ----
    def read_file(self, path: str) -> str:
        self._require("filesystem.read", "context.read_file")
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        self._require("filesystem.write", "context.write_file")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        self._require("filesystem.read", "context.list_dir")
        result: list[dict[str, Any]] = []
        for entry in Path(path).iterdir():
            try:
                stat = entry.stat()
                result.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": stat.st_size,
                })
            except OSError:
                continue
        return result

    # ---- 剪贴板（需 clipboard 权限）----
    def clipboard_get(self) -> str:
        self._require("clipboard", "context.clipboard_get")
        return self._clipboard_action("get")

    def clipboard_set(self, text: str) -> None:
        self._require("clipboard", "context.clipboard_set")
        self._clipboard_action("set", text)

    @staticmethod
    def _clipboard_action(action: str, text: str | None = None) -> str:
        """跨平台剪贴板操作，优先使用系统命令。"""
        import shutil

        # Linux (X11/Wayland)
        if shutil.which("xclip"):
            if action == "get":
                return subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, text=True, check=False,
                ).stdout
            subprocess.run(["xclip", "-selection", "clipboard"], input=text or "", text=True, check=False)
            return ""
        if shutil.which("wl-copy") and action == "set":
            subprocess.run(["wl-copy"], input=text or "", text=True, check=False)
            return ""
        if shutil.which("wl-paste") and action == "get":
            return subprocess.run(["wl-paste"], capture_output=True, text=True, check=False).stdout
        # macOS
        if shutil.which("pbcopy") and action == "set":
            subprocess.run(["pbcopy"], input=text or "", text=True, check=False)
            return ""
        if shutil.which("pbpaste") and action == "get":
            return subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout
        # Windows
        if shutil.which("clip") and action == "set":
            subprocess.run(["clip"], input=text or "", text=True, check=False)
            return ""
        # 回退：使用 tkinter
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            if action == "get":
                result = root.clipboard_get()
            else:
                root.clipboard_clear()
                root.clipboard_append(text or "")
                root.update()
                result = ""
            root.destroy()
            return result
        except Exception:  # noqa: BLE001
            raise RuntimeError("no clipboard backend available (install xclip/wl-clipboard/pbcopy)")

    # ---- 事件总线 ----
    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.emit(self._plugin_id, event, payload or {})

    def on(self, event: str, handler) -> None:
        if self._event_bus is not None:
            self._event_bus.on(self._plugin_id, event, handler)
