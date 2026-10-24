"""MCP Manager：MCP Server 的配置、连接与生命周期管理。

职责边界（与 Plugin System 完全独立，Agent 无感知）：
  - 读写 ``.weci/mcp_servers.json`` 中的 Server 配置
  - 启动 / 连接 / 断开 MCP Server（第一阶段只支持本地 stdio）
  - 拉取 Server 的 Tools，转换成 WeCi ``Tool`` 并注册到 ``ToolRegistry``
  - 启用 / 禁用 Server；断开或失败时注销其工具并记录错误

Agent 仍然只依赖 ``ToolRegistry``：

    Plugin Tool ──┐
    Built-in Tool ─┼→ ToolRegistry → Agent
    MCP Tool ─────┘
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.config import PROJECT_DIR, settings
from backend.mcp.client import McpError, StdioMcpClient
from backend.tools.registry import ToolRegistry
from weci_sdk.tool import Tool, ToolResult

logger = logging.getLogger("weci.mcp")

DATA_DIR = PROJECT_DIR / ".weci"
DEFAULT_MCP_CONFIG_FILE = DATA_DIR / "mcp_servers.json"

# 单个 Server 一次能注册的工具数量上限，避免异常 Server 撑爆上下文
MAX_TOOLS_PER_SERVER = 128

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9_-]+")
_TOOL_NAME_INVALID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

STATUS_DISABLED = "disabled"
STATUS_STOPPED = "stopped"
STATUS_CONNECTING = "connecting"
STATUS_READY = "ready"
STATUS_ERROR = "error"


class McpConfigError(ValueError):
    """MCP Server 配置不合法。"""


# ---------------------------------------------------------------------------
# 配置与运行态
# ---------------------------------------------------------------------------
@dataclass
class McpServerConfig:
    id: str
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "McpServerConfig":
        if not isinstance(raw, dict):
            raise McpConfigError("MCP Server 配置必须是对象。")
        server_id = _clean_str(raw.get("id")) or slugify(_clean_str(raw.get("name")))
        name = _clean_str(raw.get("name")) or server_id
        command = _clean_str(raw.get("command"))
        if not command:
            raise McpConfigError("MCP Server 缺少 command。")
        args = raw.get("args")
        env = raw.get("env")
        return cls(
            id=server_id,
            name=name,
            command=command,
            args=[str(item) for item in args] if isinstance(args, list) else [],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            enabled=bool(raw.get("enabled", True)),
            description=_clean_str(raw.get("description")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "enabled": self.enabled,
            "description": self.description,
        }


@dataclass
class McpServerState:
    config: McpServerConfig
    status: str = STATUS_STOPPED
    error: str | None = None
    client: StdioMcpClient | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    last_connected_at: str | None = None


# ---------------------------------------------------------------------------
# MCP Tool → WeCi Tool
# ---------------------------------------------------------------------------
class _McpArgs(BaseModel):
    """MCP 工具参数：Schema 由 Server 提供，这里仅透传任意键值。"""

    model_config = ConfigDict(extra="allow")


class McpTool(Tool):
    """把 MCP Server 的一个 Tool 包装成 WeCi Tool。

    - ``get_schema`` 直接返回 MCP 的 ``inputSchema``，与现有 Tool 的
      OpenAI function calling schema 结构一致。
    - 执行结果统一转换为 ``ToolResult``，再经 ``Tool.run`` 归一化为
      ``ToolExecutionResult``。
    - ``permissions`` 声明 ``mcp:use``：MCP 工具不会绕过 Permission Manager，
      首次调用需要用户授权。
    """

    args_model = _McpArgs
    permissions = ("mcp:use",)

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None,
        server_id: str,
        server_name: str,
        original_name: str,
        caller: Any,
    ) -> None:
        self.name = name
        self.description = description
        self._input_schema = dict(input_schema or {})
        self.server_id = server_id
        self.server_name = server_name
        self.original_name = original_name
        self._caller = caller

    def get_schema(self) -> dict[str, Any]:
        schema = dict(self._input_schema)
        schema.pop("$schema", None)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return schema

    async def execute(self, args: BaseModel, context: Any = None) -> ToolResult:
        arguments = args.model_dump() if hasattr(args, "model_dump") else dict(args)
        try:
            result = await self._caller(self.original_name, arguments)
        except McpError as exc:
            return ToolResult.error(str(exc))
        return _convert_call_result(result)


def _convert_call_result(result: Any) -> ToolResult:
    """把 MCP ``tools/call`` 结果转换为统一 ``ToolResult``。"""
    if not isinstance(result, dict):
        return ToolResult.success(_stringify(result))

    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(str(block.get("text") or ""))
                elif block_type == "resource":
                    resource = block.get("resource")
                    if isinstance(resource, dict) and resource.get("text") is not None:
                        parts.append(str(resource.get("text")))
                    else:
                        parts.append(_stringify(resource))
                elif block_type in {"image", "audio"}:
                    label = "图片" if block_type == "image" else "音频"
                    mime = block.get("mimeType") or "未知类型"
                    parts.append(f"[{label}：{mime}]")
                else:
                    parts.append(_stringify(block))
            else:
                parts.append(_stringify(block))

    if not parts and result.get("structuredContent") is not None:
        parts.append(_stringify(result.get("structuredContent")))

    text = "\n".join(item for item in parts if item)
    if result.get("isError"):
        return ToolResult.error(text or "MCP 工具执行失败。")
    return ToolResult.success(text)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class McpManager:
    """管理所有 MCP Server 的生命周期与工具注册。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        config_path: str | Path | None = None,
        connect_timeout: float | None = None,
        call_timeout: float | None = None,
        permission_manager: Any = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.config_path = Path(config_path) if config_path else DEFAULT_MCP_CONFIG_FILE
        self.connect_timeout = connect_timeout or settings.mcp_connect_timeout
        self.call_timeout = call_timeout or settings.mcp_call_timeout
        # 统一权限层：MCP 工具不绕过 Permission Manager
        self.permission_manager = permission_manager

        self._states: dict[str, McpServerState] = {}
        self._file_lock = threading.RLock()
        self._load_configs()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_servers(self) -> list[dict[str, Any]]:
        return [self._state_to_dict(state) for state in self._states.values()]

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        state = self._states.get(server_id)
        return self._state_to_dict(state) if state else None

    def has_server(self, server_id: str) -> bool:
        return server_id in self._states

    # ------------------------------------------------------------------
    # 配置 CRUD（新增 / 修改 / 删除会同步生命周期）
    # ------------------------------------------------------------------
    async def add_server(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        enabled: bool = True,
        description: str = "",
    ) -> dict[str, Any]:
        name = _validate_name(name)
        command = _validate_command(command)
        args = _validate_args(args)
        env = _validate_env(env)
        description = _clean_str(description)[:200]

        server_id = self._unique_id(slugify(name))
        config = McpServerConfig(
            id=server_id,
            name=name,
            command=command,
            args=args,
            env=env,
            enabled=enabled,
            description=description,
        )
        state = McpServerState(
            config=config,
            status=STATUS_STOPPED if enabled else STATUS_DISABLED,
        )
        self._states[server_id] = state
        self._persist()
        logger.info("MCP Server 已添加: %s (%s)", server_id, command)

        if enabled:
            await self.connect(server_id)
        return self._state_to_dict(state)

    async def update_server(
        self,
        server_id: str,
        *,
        name: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        enabled: bool | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        state = self._require_state(server_id)
        config = state.config

        connection_changed = False
        if name is not None:
            config.name = _validate_name(name)
        if command is not None:
            new_command = _validate_command(command)
            connection_changed = connection_changed or new_command != config.command
            config.command = new_command
        if args is not None:
            new_args = _validate_args(args)
            connection_changed = connection_changed or new_args != config.args
            config.args = new_args
        if env is not None:
            new_env = _validate_env(env)
            connection_changed = connection_changed or new_env != config.env
            config.env = new_env
        if description is not None:
            config.description = _clean_str(description)[:200]

        enabling_changed = False
        if enabled is not None and bool(enabled) != config.enabled:
            config.enabled = bool(enabled)
            enabling_changed = True

        # 先断开旧的连接（参数变化时避免继续使用旧进程）
        if connection_changed or enabling_changed or not config.enabled:
            await self._disconnect_state(state)

        self._persist()

        if config.enabled:
            if connection_changed or enabling_changed or state.status != STATUS_READY:
                await self.connect(server_id)
        return self._state_to_dict(state)

    async def remove_server(self, server_id: str) -> bool:
        state = self._states.get(server_id)
        if state is None:
            return False
        await self._disconnect_state(state)
        self._states.pop(server_id, None)
        self._persist()
        logger.info("MCP Server 已移除: %s", server_id)
        return True

    async def set_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        return await self.update_server(server_id, enabled=enabled)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """连接所有已启用的 Server（单个失败不影响其他 Server 与 WeCi）。"""
        for state in list(self._states.values()):
            if not state.config.enabled:
                state.status = STATUS_DISABLED
                continue
            try:
                await self.connect(state.config.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP Server %s 启动失败: %s", state.config.id, exc)

    async def stop(self) -> None:
        """关闭所有连接并注销工具。"""
        for state in list(self._states.values()):
            await self._disconnect_state(state)

    async def connect(self, server_id: str) -> dict[str, Any]:
        state = self._require_state(server_id)
        if state.status == STATUS_READY and state.client and state.client.alive:
            return self._state_to_dict(state)

        await self._disconnect_state(state)
        if not state.config.enabled:
            state.status = STATUS_DISABLED
            return self._state_to_dict(state)

        state.status = STATUS_CONNECTING
        state.error = None
        client = StdioMcpClient(
            state.config.command,
            state.config.args,
            state.config.env,
            name=state.config.id,
            connect_timeout=self.connect_timeout,
            request_timeout=self.call_timeout,
        )
        try:
            await client.connect()
            definitions = await client.list_tools()
        except McpError as exc:
            await client.close()
            state.client = None
            state.status = STATUS_ERROR
            state.error = str(exc)
            logger.warning("MCP Server %s 连接失败: %s", server_id, exc)
            return self._state_to_dict(state)
        except Exception as exc:  # noqa: BLE001
            await client.close()
            state.client = None
            state.status = STATUS_ERROR
            state.error = f"连接 MCP Server 失败：{exc}"
            logger.exception("MCP Server %s 连接异常", server_id)
            return self._state_to_dict(state)

        state.client = client
        state.server_info = client.server_info
        state.last_connected_at = _now()
        self._register_tools(state, definitions)
        state.status = STATUS_READY
        if self.permission_manager is not None:
            self.permission_manager.log(
                "mcp.connected",
                server=state.config.id,
                command=state.config.command,
                tools=len(state.tool_names),
            )
            self.permission_manager.grant_declared(
                f"mcp:{state.config.id}", ["mcp:use"]
            )
        logger.info(
            "MCP Server 就绪: %s，工具: %s",
            server_id,
            ", ".join(state.tool_names) or "无",
        )
        return self._state_to_dict(state)

    async def disconnect(self, server_id: str) -> dict[str, Any]:
        state = self._require_state(server_id)
        await self._disconnect_state(state)
        return self._state_to_dict(state)

    async def reconnect(self, server_id: str) -> dict[str, Any]:
        state = self._require_state(server_id)
        await self._disconnect_state(state)
        return await self.connect(server_id)

    # ------------------------------------------------------------------
    # 内部：连接 / 注销
    # ------------------------------------------------------------------
    async def _disconnect_state(self, state: McpServerState) -> None:
        for tool_name in list(state.tool_names):
            self.tool_registry.unregister(tool_name)
        state.tool_names.clear()
        state.tools = []

        client = state.client
        state.client = None
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                logger.debug("关闭 MCP 连接失败: %s", state.config.id, exc_info=True)

        state.status = STATUS_STOPPED if state.config.enabled else STATUS_DISABLED
        state.error = None

    def _register_tools(
        self, state: McpServerState, definitions: list[dict[str, Any]]
    ) -> None:
        client = state.client
        if client is None:
            return

        existing = set(self.tool_registry.names())
        for definition in definitions[:MAX_TOOLS_PER_SERVER]:
            original_name = _clean_str(definition.get("name"))
            if not original_name:
                continue

            tool_name = build_tool_name(state.config.id, original_name)
            if tool_name in existing:
                logger.warning(
                    "MCP 工具名冲突，已跳过: %s（Server %s）",
                    tool_name,
                    state.config.id,
                )
                continue

            input_schema = definition.get("inputSchema")
            description = _clean_str(definition.get("description")) or (
                f"MCP 工具 {original_name}（来自 {state.config.name}）"
            )
            tool = McpTool(
                name=tool_name,
                description=description[:1024],
                input_schema=input_schema if isinstance(input_schema, dict) else {},
                server_id=state.config.id,
                server_name=state.config.name,
                original_name=original_name,
                caller=client.call_tool,
            )
            try:
                # MCP 工具同样进入 Permission Manager：主体为 mcp:<server_id>
                self.tool_registry.register(
                    tool,
                    principals=[f"mcp:{state.config.id}", f"tool:{tool_name}"],
                )
            except ValueError as exc:
                logger.warning("注册 MCP 工具失败: %s", exc)
                continue

            existing.add(tool_name)
            state.tool_names.append(tool_name)
            state.tools.append(
                {
                    "name": tool_name,
                    "original_name": original_name,
                    "description": description,
                }
            )

    # ------------------------------------------------------------------
    # 内部：配置持久化
    # ------------------------------------------------------------------
    def _load_configs(self) -> None:
        raw = self._read_config()
        servers = raw.get("servers") if isinstance(raw, dict) else None
        if not isinstance(servers, list):
            return
        for item in servers:
            try:
                config = McpServerConfig.from_dict(item)
            except McpConfigError as exc:
                logger.warning("忽略无效的 MCP Server 配置: %s", exc)
                continue
            if config.id in self._states:
                logger.warning("MCP Server id 重复，已跳过: %s", config.id)
                continue
            self._states[config.id] = McpServerState(
                config=config,
                status=STATUS_STOPPED if config.enabled else STATUS_DISABLED,
            )

    def _read_config(self) -> dict[str, Any]:
        with self._file_lock:
            if not self.config_path.is_file():
                return {}
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("无法读取 MCP 配置 %s: %s", self.config_path, exc)
                return {}
        return data if isinstance(data, dict) else {}

    def _persist(self) -> None:
        payload = {
            "version": 1,
            "servers": [state.config.to_dict() for state in self._states.values()],
        }
        with self._file_lock:
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
                temp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temp.replace(self.config_path)
            except OSError as exc:
                logger.warning("无法写入 MCP 配置 %s: %s", self.config_path, exc)

    # ------------------------------------------------------------------
    # 内部：辅助
    # ------------------------------------------------------------------
    def _require_state(self, server_id: str) -> McpServerState:
        state = self._states.get(server_id)
        if state is None:
            raise McpConfigError(f"MCP Server 不存在：{server_id}")
        return state

    def _unique_id(self, base: str) -> str:
        candidate = base or "server"
        if candidate not in self._states:
            return candidate
        for index in range(2, 1000):
            name = f"{candidate}-{index}"
            if name not in self._states:
                return name
        raise McpConfigError("无法生成唯一的 MCP Server id。")

    def _state_to_dict(self, state: McpServerState) -> dict[str, Any]:
        config = state.config
        return {
            "id": config.id,
            "name": config.name,
            "description": config.description,
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env),
            "enabled": config.enabled,
            "status": state.status,
            "error": state.error,
            "tools": [dict(item) for item in state.tools],
            "tool_names": list(state.tool_names),
            "server_info": dict(state.server_info),
            "last_connected_at": state.last_connected_at,
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def build_tool_name(server_id: str, tool_name: str) -> str:
    """把 MCP 工具名转换为兼容 OpenAI function calling 的名称。"""
    raw = f"mcp__{server_id}__{tool_name}"
    cleaned = _TOOL_NAME_INVALID_RE.sub("_", raw)
    return cleaned[:64]


def slugify(value: str) -> str:
    slug = _SLUG_INVALID_RE.sub("-", (value or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "server")[:48]


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _validate_name(name: str) -> str:
    value = _clean_str(name)
    if not value:
        raise McpConfigError("MCP Server 名称不能为空。")
    if len(value) > 64:
        raise McpConfigError("MCP Server 名称不能超过 64 个字符。")
    return value


def _validate_command(command: str) -> str:
    value = _clean_str(command)
    if not value:
        raise McpConfigError("MCP Server 命令不能为空。")
    if len(value) > 512:
        raise McpConfigError("MCP Server 命令过长。")
    return value


def _validate_args(args: list[str] | None) -> list[str]:
    if args is None:
        return []
    if not isinstance(args, list):
        raise McpConfigError("MCP Server args 必须是字符串数组。")
    if len(args) > 64:
        raise McpConfigError("MCP Server args 过多（最多 64 个）。")
    result = []
    for item in args:
        value = str(item)
        if len(value) > 512:
            raise McpConfigError("MCP Server 单个参数过长。")
        result.append(value)
    return result


def _validate_env(env: dict[str, str] | None) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise McpConfigError("MCP Server environment 必须是键值对象。")
    if len(env) > 32:
        raise McpConfigError("MCP Server environment 变量过多（最多 32 个）。")
    result: dict[str, str] = {}
    for key, value in env.items():
        name = str(key).strip()
        if not _ENV_KEY_RE.match(name):
            raise McpConfigError(f"无效的环境变量名：{name}")
        text = str(value)
        if len(text) > 2048:
            raise McpConfigError(f"环境变量 {name} 的值过长。")
        result[name] = text
    return result
