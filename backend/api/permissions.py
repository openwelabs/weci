"""统一权限 / 安全 API。

端点：
  GET  /api/permissions                 权限目录 + 已授权限 + Provider / Plugin /
                                        MCP / Workspace / Tool 快照
  POST /api/permissions/grant           Allow（always / 本次 Run）
  POST /api/permissions/deny            Deny（持久拒绝）
  POST /api/permissions/reset           Reset（单个 / 全部）
  GET  /api/permissions/requests        尚未确认的权限请求
  POST /api/permissions/requests/{id}/resolve
                                        Allow once / Allow for this Run / Deny
  GET  /api/permissions/log             脱敏后的安全日志

所有返回都不包含 API Key：Provider 只暴露公开字段，日志经 PermissionManager
统一脱敏。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from weci_sdk.permissions import normalize_permissions

router = APIRouter(tags=["permissions"])


class GrantPayload(BaseModel):
    principal: str = Field(default="global", max_length=128)
    permission: str = Field(..., max_length=64)
    scope: Literal["always", "run"] = "always"
    run_id: str = Field(default="", max_length=64)


class DenyPayload(BaseModel):
    principal: str = Field(default="global", max_length=128)
    permission: str = Field(..., max_length=64)


class ResetPayload(BaseModel):
    principal: str | None = Field(default=None, max_length=128)
    permission: str | None = Field(default=None, max_length=64)


class ResolvePayload(BaseModel):
    decision: Literal["once", "run", "deny"]


def _manager(request: Request):
    manager = getattr(request.app.state, "permission_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="权限管理器未初始化。")
    return manager


def _plugins(request: Request) -> list[dict[str, Any]]:
    registry = getattr(request.app.state, "plugin_registry", None)
    if registry is None:
        return []
    items: list[dict[str, Any]] = []
    for entry in registry.list_plugins():
        manifest = entry.manifest or {}
        declared = normalize_permissions(manifest.get("permissions") or [])
        items.append(
            {
                "id": entry.id,
                "name": manifest.get("name", entry.id),
                "version": manifest.get("version", "?"),
                "enabled": entry.enabled,
                "status": entry.status,
                "permissions": declared,
                "permission_details": _manager(request).describe(declared),
                "tools": list(entry.tools),
            }
        )
    return items


def _mcp_servers(request: Request) -> list[dict[str, Any]]:
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        return []
    servers: list[dict[str, Any]] = []
    for server in manager.list_servers():
        servers.append(
            {
                "id": server.get("id"),
                "name": server.get("name"),
                "status": server.get("status"),
                "enabled": server.get("enabled"),
                "tools": list(server.get("tool_names") or server.get("tools") or []),
                "permissions": ["mcp:use"],
            }
        )
    return servers


def _tools(request: Request) -> list[dict[str, Any]]:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        return []
    items: list[dict[str, Any]] = []
    for name in registry.names():
        permissions = normalize_permissions(registry.permissions_for(name))
        items.append(
            {
                "name": name,
                "principals": registry.principals_for(name),
                "permissions": permissions,
            }
        )
    return items


def _providers(request: Request) -> list[dict[str, Any]]:
    manager = getattr(request.app.state, "provider_manager", None)
    if manager is None:
        return []
    # public_dict 已过滤 api_key
    return manager.list_providers()


@router.get("/permissions")
async def get_permissions(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    workspace = getattr(request.app.state, "workspace_guard", None)
    return {
        "catalog": manager.catalog(),
        "grants": manager.all_grants(),
        "pending_requests": manager.pending_requests(),
        "providers": _providers(request),
        "plugins": _plugins(request),
        "mcp_servers": _mcp_servers(request),
        "workspace": workspace.describe() if workspace is not None else {},
        "tools": _tools(request),
    }


@router.post("/permissions/grant")
async def grant(payload: GrantPayload, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    permission = payload.permission.strip()
    if payload.scope == "run":
        if not payload.run_id:
            raise HTTPException(status_code=400, detail="本次 Run 授权必须提供 run_id。")
        manager.grant_for_run(payload.run_id, permission)
        manager.log(
            "permission.granted",
            permission=permission,
            principal=payload.principal,
            scope="run",
            run_id=payload.run_id,
        )
        return {"ok": True, "scope": "run"}

    if not manager.grant(payload.principal, permission):
        raise HTTPException(status_code=400, detail=f"未知权限：{permission}")
    return {
        "ok": True,
        "scope": "always",
        "grants": manager.grants_for(payload.principal),
    }


@router.post("/permissions/deny")
async def deny(payload: DenyPayload, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if not manager.deny(payload.principal, payload.permission):
        raise HTTPException(status_code=400, detail=f"未知权限：{payload.permission}")
    return {"ok": True, "grants": manager.grants_for(payload.principal)}


@router.post("/permissions/reset")
async def reset(payload: ResetPayload, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    manager.reset(payload.principal, payload.permission)
    return {"ok": True, "grants": manager.all_grants()}


@router.get("/permissions/requests")
async def pending_requests(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return {"requests": manager.pending_requests()}


@router.post("/permissions/requests/{request_id}/resolve")
async def resolve_request(
    request_id: str, payload: ResolvePayload, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    if not manager.resolve(request_id, payload.decision):
        raise HTTPException(
            status_code=404,
            detail="权限请求不存在或已失效（可能已超时 / 已取消）。",
        )
    return {"ok": True, "decision": payload.decision}


@router.get("/permissions/log")
async def security_log(
    request: Request, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    manager = _manager(request)
    return {"log": manager.recent_log(limit)}
