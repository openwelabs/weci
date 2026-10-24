"""MCP 支持测试：stdio 客户端、Manager 生命周期、工具转换与 REST API。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.api.mcp import router as mcp_router
from backend.mcp.client import McpError, StdioMcpClient
from backend.mcp.manager import McpConfigError, McpManager, build_tool_name
from weci_sdk.tool import Tool, ToolResult

FIXTURE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _client(**kwargs) -> StdioMcpClient:
    options = {"connect_timeout": 10, "request_timeout": 10}
    options.update(kwargs)
    return StdioMcpClient(sys.executable, [str(FIXTURE)], **options)


def _manager(
    tmp_path: Path, registry=None, **kwargs
) -> tuple[McpManager, "ToolRegistry"]:  # noqa: F821
    from backend.tools.registry import ToolRegistry

    tool_registry = registry or ToolRegistry()
    manager = McpManager(
        tool_registry,
        config_path=tmp_path / "mcp_servers.json",
        connect_timeout=10,
        call_timeout=10,
        **kwargs,
    )
    return manager, tool_registry


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
def test_client_connect_lists_and_calls_tools() -> None:
    async def scenario() -> None:
        client = _client()
        info = await client.connect()
        assert info["serverInfo"]["name"] == "fake-mcp"
        assert client.alive is True

        tools = await client.list_tools()
        assert {item["name"] for item in tools} == {"echo", "fail", "sleep", "crash"}
        echo = next(item for item in tools if item["name"] == "echo")
        assert echo["inputSchema"]["properties"]["text"]["type"] == "string"

        result = await client.call_tool("echo", {"text": "hi"})
        assert result["content"][0]["text"] == "echo: hi"

        await client.close()
        assert client.alive is False

        with pytest.raises(McpError):
            await client.call_tool("echo", {"text": "after-close"})

    asyncio.run(scenario())


def test_client_missing_command_raises() -> None:
    async def scenario() -> None:
        client = StdioMcpClient("weci-command-that-does-not-exist", [], connect_timeout=5)
        with pytest.raises(McpError, match="找不到"):
            await client.connect()

    asyncio.run(scenario())


def test_client_detects_server_exit() -> None:
    async def scenario() -> None:
        client = _client()
        await client.connect()
        with pytest.raises(McpError, match="断开|退出"):
            await client.call_tool("crash", {})
        await client.close()

    asyncio.run(scenario())


def test_client_request_timeout() -> None:
    async def scenario() -> None:
        client = _client(request_timeout=0.2)
        await client.connect()
        try:
            with pytest.raises(McpError, match="超时"):
                await client.call_tool("sleep", {"seconds": 2})
        finally:
            await client.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Manager：配置与生命周期
# ---------------------------------------------------------------------------
def test_manager_add_connects_and_registers_tool(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        server = await manager.add_server(
            name="Fake MCP",
            command=sys.executable,
            args=[str(FIXTURE)],
        )
        assert server["id"] == "fake-mcp"
        assert server["status"] == "ready"
        assert server["enabled"] is True
        assert server["tool_names"]
        assert "mcp__fake-mcp__echo" in registry.names()

        schemas = registry.schemas()
        echo_schema = next(
            item
            for item in schemas
            if item["function"]["name"] == "mcp__fake-mcp__echo"
        )
        assert echo_schema["function"]["parameters"]["type"] == "object"
        assert "properties" in echo_schema["function"]["parameters"]

        result = await registry.execute("mcp__fake-mcp__echo", {"text": "hello"})
        assert result.ok is True
        assert result.output == "echo: hello"

        await manager.stop()
        assert registry.names() == []

    asyncio.run(scenario())


def test_manager_maps_tool_errors_to_tool_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        await manager.add_server(
            name="Fake MCP",
            command=sys.executable,
            args=[str(FIXTURE)],
        )
        result = await registry.execute("mcp__fake-mcp__fail", {})
        assert result.ok is False
        assert "工具内部错误" in (result.error or "")

        # Server 进程退出后，后续调用必须返回结构化错误而不是抛异常
        crashed = await registry.execute("mcp__fake-mcp__crash", {})
        assert crashed.ok is False
        assert "断开" in (crashed.error or "") or "退出" in (crashed.error or "")

        await manager.stop()

    asyncio.run(scenario())


def test_manager_disable_unregisters_and_persists(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        await manager.add_server(
            name="Fake MCP", command=sys.executable, args=[str(FIXTURE)]
        )
        server = await manager.set_enabled("fake-mcp", False)
        assert server["status"] == "disabled"
        assert server["tools"] == []
        assert "mcp__fake-mcp__echo" not in registry.names()

        reloaded, _ = _manager(tmp_path)
        listed = reloaded.list_servers()
        assert listed[0]["enabled"] is False
        assert listed[0]["status"] == "disabled"

    asyncio.run(scenario())


def test_manager_connect_failure_sets_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        server = await manager.add_server(
            name="Broken", command="weci-no-such-command"
        )
        assert server["status"] == "error"
        assert "找不到" in (server["error"] or "")
        assert server["tool_names"] == []
        assert registry.names() == []

    asyncio.run(scenario())


def test_manager_remove_server(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        await manager.add_server(
            name="Fake MCP", command=sys.executable, args=[str(FIXTURE)]
        )
        assert await manager.remove_server("fake-mcp") is True
        assert manager.list_servers() == []
        assert registry.names() == []
        assert await manager.remove_server("fake-mcp") is False

    asyncio.run(scenario())


def test_manager_update_command_reconnects(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        await manager.add_server(
            name="Fake MCP", command=sys.executable, args=[str(FIXTURE)]
        )
        server = await manager.update_server(
            "fake-mcp", command=sys.executable, args=[str(FIXTURE)]
        )
        assert server["status"] == "ready"
        assert "mcp__fake-mcp__echo" in registry.names()

        stopped = await manager.update_server("fake-mcp", enabled=False)
        assert stopped["status"] == "disabled"
        assert registry.names() == []

    asyncio.run(scenario())


def test_manager_start_connects_enabled_servers(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, registry = _manager(tmp_path)
        await manager.add_server(
            name="Fake MCP", command=sys.executable, args=[str(FIXTURE)]
        )
        await manager.stop()
        assert registry.names() == []

        await manager.start()
        assert "mcp__fake-mcp__echo" in registry.names()
        await manager.stop()

    asyncio.run(scenario())


def test_manager_rejects_invalid_config(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, _ = _manager(tmp_path)
        with pytest.raises(McpConfigError):
            await manager.add_server(name="   ", command="echo")
        with pytest.raises(McpConfigError):
            await manager.add_server(name="x", command="echo", env={"NOT VALID": "1"})
        with pytest.raises(McpConfigError):
            await manager.connect("missing")
        assert manager.list_servers() == []

    asyncio.run(scenario())


def test_manager_coexists_with_existing_tools(tmp_path: Path) -> None:
    class PingArgs(BaseModel):
        pass

    class PingTool(Tool):
        name = "plugin_ping"
        description = "existing plugin tool"
        args_model = PingArgs

        async def execute(self, args, context=None):  # noqa: ANN001
            return ToolResult.success("pong")

    async def scenario() -> None:
        from backend.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(PingTool())
        manager, _ = _manager(tmp_path, registry=registry)
        await manager.add_server(
            name="Fake MCP", command=sys.executable, args=[str(FIXTURE)]
        )
        assert "plugin_ping" in registry.names()
        assert "mcp__fake-mcp__echo" in registry.names()

        plugin_result = await registry.execute("plugin_ping", None)
        assert plugin_result.ok is True
        assert plugin_result.output == "pong"

        await manager.stop()
        # 只注销 MCP 工具，原有插件工具保持不变
        assert registry.names() == ["plugin_ping"]

    asyncio.run(scenario())


def test_build_tool_name_sanitizes_and_truncates() -> None:
    assert build_tool_name("srv", "read.file") == "mcp__srv__read_file"
    long_name = build_tool_name("srv", "x" * 200)
    assert len(long_name) == 64
    assert long_name.startswith("mcp__srv__")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.fixture
def api_client(tmp_path: Path):
    from backend.tools.registry import ToolRegistry

    registry = ToolRegistry()
    app = FastAPI()
    app.include_router(mcp_router, prefix="/api")
    app.state.mcp_manager = McpManager(
        registry,
        config_path=tmp_path / "mcp_servers.json",
        connect_timeout=10,
        call_timeout=10,
    )
    with TestClient(app) as client:
        yield client


def test_api_crud_and_lifecycle(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/mcp/servers",
        json={"name": "Fake MCP", "command": sys.executable, "args": [str(FIXTURE)]},
    )
    assert created.status_code == 200
    server = created.json()["server"]
    assert server["status"] == "ready"
    assert "mcp__fake-mcp__echo" in server["tool_names"]

    listed = api_client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert len(listed.json()["servers"]) == 1

    disabled = api_client.patch(
        "/api/mcp/servers/fake-mcp", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["server"]["status"] == "disabled"

    enabled = api_client.patch(
        "/api/mcp/servers/fake-mcp", json={"enabled": True}
    )
    assert enabled.json()["server"]["status"] == "ready"

    disconnected = api_client.post("/api/mcp/servers/fake-mcp/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["server"]["status"] == "stopped"

    connected = api_client.post("/api/mcp/servers/fake-mcp/connect")
    assert connected.status_code == 200
    assert connected.json()["server"]["status"] == "ready"

    removed = api_client.delete("/api/mcp/servers/fake-mcp")
    assert removed.status_code == 200
    assert api_client.get("/api/mcp/servers").json()["servers"] == []


def test_api_not_found_and_validation(api_client: TestClient) -> None:
    assert api_client.patch("/api/mcp/servers/nope", json={"enabled": False}).status_code == 404
    assert api_client.delete("/api/mcp/servers/nope").status_code == 404
    assert api_client.post("/api/mcp/servers/nope/connect").status_code == 404
    assert api_client.post("/api/mcp/servers", json={"name": "x"}).status_code == 422
    assert (
        api_client.post(
            "/api/mcp/servers", json={"name": "x", "command": "echo", "env": {"BAD KEY": "1"}}
        ).status_code
        == 400
    )
