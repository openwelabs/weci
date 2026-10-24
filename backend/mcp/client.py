"""最小 MCP stdio 客户端。

MCP（Model Context Protocol）的 stdio transport 是「换行分隔的 JSON-RPC 2.0」：
客户端启动 Server 子进程，通过 stdin 写请求、stdout 读响应，stderr 仅用于日志。

本模块只实现第一阶段需要的最小协议子集：
  - ``initialize`` 握手（含 ``notifications/initialized``）
  - ``tools/list``（支持 nextCursor 分页）
  - ``tools/call``

不引入官方 SDK，避免为本地优先的 WeCi 增加重量级依赖；协议交互集中在这里，
上层 MCP Manager 不需要了解 JSON-RPC 细节。

约定：
  - 单个 Server 由一个 ``StdioMcpClient`` 实例负责，生命周期由 MCP Manager 管理。
  - 所有异常（启动失败、超时、Server 断开、JSON-RPC error）统一收敛为 ``McpError``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from backend.config import settings

logger = logging.getLogger("weci.mcp")

# 客户端支持的协议版本；Server 可以在 initialize 响应中返回自己的版本
MCP_PROTOCOL_VERSION = "2024-11-05"

# stderr 只保留最近若干行，用于连接失败时给出可读提示
_STDERR_TAIL_LINES = 20


class McpError(RuntimeError):
    """MCP 连接 / 请求 / 协议错误。"""


def _shorten(text: Any, limit: int = 300) -> str:
    value = str(text).strip()
    if len(value) > limit:
        return value[:limit] + "…"
    return value


def _format_rpc_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("data") or "未知错误"
        code = error.get("code")
        if code is not None:
            return _shorten(f"MCP Server 返回错误（{code}）：{message}")
        return _shorten(f"MCP Server 返回错误：{message}")
    return _shorten(f"MCP Server 返回错误：{error}")


class StdioMcpClient:
    """连接单个本地 MCP Server（stdio transport）。"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        name: str = "mcp",
        connect_timeout: float | None = None,
        request_timeout: float | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.extra_env = dict(env or {})
        self.connect_timeout = connect_timeout or settings.mcp_connect_timeout
        self.request_timeout = request_timeout or settings.mcp_call_timeout

        self._proc: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._disconnect_reason: str | None = None
        self._stderr_tail: list[str] = []
        self._server_info: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return (
            self._proc is not None
            and self._proc.returncode is None
            and not self._closed
        )

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    def last_stderr(self) -> str:
        return "\n".join(self._stderr_tail[-5:])

    # ------------------------------------------------------------------
    # 连接 / 关闭
    # ------------------------------------------------------------------
    async def connect(self) -> dict[str, Any]:
        """启动 Server 子进程并完成 MCP 握手。"""
        if self.alive:
            return self.server_info

        self._closed = False
        self._disconnect_reason = None
        self._stderr_tail.clear()

        env = os.environ.copy()
        env.update(self.extra_env)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            raise McpError(f"找不到 MCP Server 命令：{self.command}") from None
        except OSError as exc:
            raise McpError(f"启动 MCP Server 失败：{_shorten(exc)}") from None

        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "weci", "version": settings.version},
                },
                timeout=self.connect_timeout,
            )
            self._server_info = result if isinstance(result, dict) else {}
            await self._notify("notifications/initialized", {})
        except Exception:
            await self.close()
            raise

        logger.info(
            "MCP Server 已连接: %s (%s)",
            self.name,
            self._server_info.get("serverInfo", {}).get("name", "unknown"),
        )
        return self.server_info

    async def close(self) -> None:
        """关闭子进程并释放所有等待中的请求。"""
        self._closed = True
        self._fail_pending("MCP 连接已关闭。")

        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except ProcessLookupError:
                        pass

        for task in (self._read_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._read_task, self._stderr_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._read_task = None
        self._stderr_task = None

    # ------------------------------------------------------------------
    # 协议方法
    # ------------------------------------------------------------------
    async def list_tools(self) -> list[dict[str, Any]]:
        """获取 Server 提供的工具（自动处理 nextCursor 分页）。"""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        # 分页上限防御：避免异常 Server 无限返回 cursor
        for _ in range(20):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            page = result.get("tools") if isinstance(result, dict) else None
            if isinstance(page, list):
                tools.extend(item for item in page if isinstance(item, dict))
            next_cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """调用 Server 上的工具，返回原始 ``tools/call`` 结果。"""
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        if not isinstance(result, dict):
            raise McpError("MCP Server 返回了无法识别的工具结果。")
        return result

    # ------------------------------------------------------------------
    # JSON-RPC 内部实现
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.alive:
            raise McpError(self._disconnect_reason or "MCP Server 未连接。")

        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            await self._send(payload)
            message = await asyncio.wait_for(
                future, timeout=timeout or self.request_timeout
            )
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise McpError(f"MCP 请求超时：{method}") from None
        except BaseException:
            self._pending.pop(request_id, None)
            raise

        if "error" in message:
            raise McpError(_format_rpc_error(message.get("error")))
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.alive or self._proc is None or self._proc.stdin is None:
            raise McpError(self._disconnect_reason or "MCP Server 未连接。")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            async with self._write_lock:
                self._proc.stdin.write(line.encode("utf-8"))
                await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._on_disconnect("MCP Server 已断开。")
            raise McpError("MCP Server 已断开。") from None
        except OSError as exc:
            raise McpError(f"写入 MCP Server 失败：{_shorten(exc)}") from None

    async def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("[mcp:%s] 忽略非 JSON 输出: %s", self.name, _shorten(text, 200))
                    continue
                if not isinstance(message, dict):
                    continue

                msg_id = message.get("id")
                if msg_id is not None and ("result" in message or "error" in message):
                    future = self._pending.pop(msg_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                else:
                    # 服务端通知（如 notifications/tools/list_changed）第一阶段仅记录
                    logger.debug(
                        "[mcp:%s] 通知: %s", self.name, message.get("method")
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("[mcp:%s] 读取循环异常", self.name, exc_info=True)
        finally:
            if not self._closed:
                detail = self.last_stderr()
                reason = "MCP Server 进程已退出。"
                if detail:
                    reason = f"{reason} {_shorten(detail)}"
                self._on_disconnect(reason)

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                self._stderr_tail.append(text)
                del self._stderr_tail[:-_STDERR_TAIL_LINES]
                logger.debug("[mcp:%s] %s", self.name, text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    def _on_disconnect(self, reason: str) -> None:
        if self._closed:
            return
        self._disconnect_reason = reason
        self._fail_pending(reason)

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(McpError(reason))
