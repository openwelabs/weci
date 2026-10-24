"""MCP Server 管理 API。

端点：
  GET    /api/mcp/servers                    列出所有 MCP Server（含状态与工具）
  POST   /api/mcp/servers                    新增 Server（默认立即连接）
  PATCH  /api/mcp/servers/{server_id}        修改配置 / 启用禁用
  DELETE /api/mcp/servers/{server_id}        删除 Server
  POST   /api/mcp/servers/{server_id}/connect     连接
  POST   /api/mcp/servers/{server_id}/disconnect  断开
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.mcp.manager import McpConfigError, McpManager

router = APIRouter(tags=["mcp"])


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    description: str = Field(default="", max_length=200)


class McpServerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    command: str | None = Field(default=None, min_length=1, max_length=512)
    args: list[str] | None = Field(default=None, max_length=64)
    env: dict[str, str] | None = None
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=200)


def _manager(request: Request) -> McpManager:
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP 管理器未初始化。")
    return manager


@router.get("/mcp/servers")
async def list_servers(request: Request) -> dict[str, Any]:
    return {"servers": _manager(request).list_servers()}


@router.post("/mcp/servers")
async def create_server(
    payload: McpServerCreateRequest, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        server = await manager.add_server(
            name=payload.name,
            command=payload.command,
            args=payload.args,
            env=payload.env,
            enabled=payload.enabled,
            description=payload.description,
        )
    except McpConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "server": server}


@router.patch("/mcp/servers/{server_id}")
async def update_server(
    server_id: str, payload: McpServerUpdateRequest, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        server = await manager.update_server(
            server_id,
            name=payload.name,
            command=payload.command,
            args=payload.args,
            env=payload.env,
            enabled=payload.enabled,
            description=payload.description,
        )
    except McpConfigError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"ok": True, "server": server}


@router.delete("/mcp/servers/{server_id}")
async def delete_server(server_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if not await manager.remove_server(server_id):
        raise HTTPException(status_code=404, detail=f"MCP Server 不存在：{server_id}")
    return {"ok": True, "removed": server_id}


@router.post("/mcp/servers/{server_id}/connect")
async def connect_server(server_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    try:
        server = await manager.connect(server_id)
    except McpConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": server.get("status") == "ready", "server": server}


@router.post("/mcp/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    try:
        server = await manager.disconnect(server_id)
    except McpConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "server": server}
