"""PluginContext 的 Core 实现。

插件拿到的是这个对象（但只看到 ``sdk.context.PluginContext`` 接口）。
所有受权限保护的能力在调用前检查插件是否声明了对应权限；文件与命令
额外经过 Workspace 安全边界与命令脱敏审计。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from weci_sdk.context import FileCap, HttpCap, MemoryCap, PluginContext, StorageCap
from weci_sdk.permissions import PermissionDenied as PluginPermissionError, normalize_permissions

from backend.config import settings
from backend.services.memory import (
    MemoryScopeContext,
    MemoryStore,
    current_memory_scope,
    default_memory_store,
    resolve_scope_id,
)
from backend.services.permissions import redact, sanitize_env
from backend.services.workspace import WorkspaceGuard, WorkspaceSecurityError


class PluginContextImpl(PluginContext):
    """Core 提供的插件上下文实现。"""

    def __init__(
        self,
        plugin_id: str,
        permissions: list[str],
        *,
        config: dict[str, Any] | None = None,
        storage_dir: Path | None = None,
        memory_store: MemoryStore | None = None,
        event_bus: Any = None,
        http_client: httpx.AsyncClient | None = None,
        workspace_guard: WorkspaceGuard | None = None,
        permission_manager: Any = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._permissions = list(permissions)
        self._config = dict(config or {})
        self._storage_dir = storage_dir
        self._memory_store = memory_store or default_memory_store
        self._event_bus = event_bus
        # 复用 Core 的共享 http client；若未提供则按需创建
        self._http_client = http_client
        self._logger = logging.getLogger(f"weci.plugin.{plugin_id}")
        self._storage_cache: dict[str, Any] | None = None
        # Workspace 安全边界：所有文件操作都被限制在 Root 之内
        self._workspace = workspace_guard or WorkspaceGuard()
        # 统一权限层（可选）：用于记录删除等高风险安全事件
        self._permission_manager = permission_manager

    # ------------------------------------------------------------------
    # 权限检查
    # ------------------------------------------------------------------
    def _require(self, permission: str, capability: str) -> None:
        """检查插件是否被授予 ``permission``。

        同时接受新权限（``workspace:read``）与历史别名（``filesystem.read``）：
        两者都会先归一化为标准权限名再比较，因此旧插件无需改动。
        """
        required = normalize_permissions([permission])
        missing = [p for p in required if p not in self._granted]
        if missing:
            raise PluginPermissionError(self._plugin_id, missing[0], capability)

    @property
    def _granted(self) -> set[str]:
        return set(normalize_permissions(self._permissions))

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

        def delete(self, path: str) -> bool:
            return self._ctx.delete_file(path)

    class _Storage(StorageCap):
        def __init__(self, ctx: "PluginContextImpl") -> None:
            self._ctx = ctx

        def get(self, key: str, default: Any = None) -> Any:
            return self._ctx.storage_get(key, default)

        def set(self, key: str, value: Any) -> None:
            self._ctx.storage_set(key, value)

    class _Memory(MemoryCap):
        def __init__(self, ctx: "PluginContextImpl") -> None:
            self._ctx = ctx

        def save(
            self, key: str, content: str, *, scope: str = "user"
        ) -> dict[str, Any]:
            return self._ctx.memory_save(key, content, scope=scope)

        def delete(self, key: str, *, scope: str = "user") -> bool:
            return self._ctx.memory_delete(key, scope=scope)

        def list(self, *, scope: str = "user") -> list[dict[str, Any]]:
            return self._ctx.memory_list(scope=scope)

    @property
    def http(self) -> HttpCap:
        return self._Http(self)

    @property
    def files(self) -> FileCap:
        return self._Files(self)

    @property
    def storage(self) -> StorageCap:
        return self._Storage(self)

    @property
    def memory(self) -> MemoryCap:
        return self._Memory(self)

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

    # ---- 长期记忆（需 memory 权限，scope 由当前请求决定）----
    def _scope_id(self, scope: str) -> tuple[str, MemoryScopeContext]:
        context = current_memory_scope()
        return resolve_scope_id(scope, context), context

    def memory_save(
        self, key: str, content: str, *, scope: str = "user"
    ) -> dict[str, Any]:
        self._require("memory", "context.memory.save")
        scope_id, _ = self._scope_id(scope)
        entry = self._memory_store.save(scope, scope_id, key, content)
        return entry.model_dump()

    def memory_delete(self, key: str, *, scope: str = "user") -> bool:
        self._require("memory", "context.memory.delete")
        scope_id, _ = self._scope_id(scope)
        return self._memory_store.delete_by_key(scope, scope_id, key)

    def memory_list(self, *, scope: str = "user") -> list[dict[str, Any]]:
        self._require("memory", "context.memory.list")
        scope_id, _ = self._scope_id(scope)
        return [
            entry.model_dump()
            for entry in self._memory_store.list(scope=scope, scope_id=scope_id)
        ]

    # ---- 进程（需 command:execute 权限）----
    async def run_command(
        self,
        command: str | list[str],
        *,
        cwd: str | None = None,
        timeout: float = 30.0,
        input: str | None = None,
    ) -> dict[str, Any]:
        self._require("command:execute", "context.run_command")

        # 工作目录必须落在 Workspace 内，不能用 cwd 绕出 Root
        workdir = self._workspace.resolve(
            cwd or ".", permission="command:execute"
        )
        if not workdir.is_dir():
            raise WorkspaceSecurityError(
                "命令的工作目录不在 Workspace 内或不存在，已被拒绝。",
                permission="command:execute",
            )

        # 不把 API Key / Token / Cookie 等注入命令环境
        env = sanitize_env()
        display = redact(
            command if isinstance(command, str) else " ".join(str(c) for c in command)
        )[:500]

        started = time.perf_counter()
        if isinstance(command, str):
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE if input is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input.encode("utf-8") if input is not None else None),
                timeout=timeout,
            )
            returncode = proc.returncode or 0
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            returncode, stdout_b, stderr_b, timed_out = -1, b"", b"", True
        except asyncio.CancelledError:
            # 用户取消 Agent Run：终止子进程，避免留下孤儿命令
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        limit = settings.command_max_output_chars
        result = {
            "returncode": returncode,
            "exit_code": returncode,
            "stdout": stdout_b.decode("utf-8", errors="replace")[:limit],
            "stderr": (
                f"command timed out after {timeout}s"
                if timed_out
                else stderr_b.decode("utf-8", errors="replace")[:limit]
            ),
            "command": display,
            "cwd": str(workdir),
            "duration_ms": duration_ms,
            "timed_out": timed_out,
        }
        if self._permission_manager is not None:
            self._permission_manager.log(
                "command.executed",
                plugin=self._plugin_id,
                command=display,
                cwd=str(workdir),
                exit_code=returncode,
                duration_ms=duration_ms,
                timed_out=timed_out,
            )
        return result

    # ---- 文件系统（Workspace 安全边界）----
    def read_file(self, path: str) -> str:
        self._require("workspace:read", "context.read_file")
        target = self._workspace.for_read(path)
        return target.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        self._require("workspace:write", "context.write_file")
        target = self._workspace.for_write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def append_file(self, path: str, content: str) -> None:
        self._require("workspace:write", "context.append_file")
        target = self._workspace.for_write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)

    def delete_file(self, path: str) -> bool:
        # 删除比写入更危险：使用独立权限，并记录安全事件
        self._require("workspace:delete", "context.delete_file")
        target = self._workspace.for_delete(path)
        target.unlink()
        if self._permission_manager is not None:
            self._permission_manager.log(
                "workspace.file_deleted",
                plugin=self._plugin_id,
                path=self._workspace.relative(path, permission="workspace:delete"),
            )
        return True

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        self._require("workspace:read", "context.list_dir")
        target = self._workspace.for_listing(path)
        result: list[dict[str, Any]] = []
        for entry in target.iterdir():
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
