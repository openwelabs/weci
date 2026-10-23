"""插件管理 API：供前端设置页与 CLI 调用。

端点：
  GET    /api/plugins            列出所有插件（含状态、权限、工具）
  POST   /api/plugins/{id}/enable    启用插件
  POST   /api/plugins/{id}/disable   禁用插件
  DELETE /api/plugins/{id}           移除用户插件（内置插件不可移除）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.plugins.registry import PluginEntry

router = APIRouter(tags=["plugins"])


def _entry_to_dict(entry: PluginEntry) -> dict[str, Any]:
    manifest = entry.manifest or {}
    return {
        "id": entry.id,
        "name": manifest.get("name", entry.id),
        "version": manifest.get("version", "?"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "api_version": manifest.get("api_version", "?"),
        "permissions": list(manifest.get("permissions") or []),
        "tools": list(entry.tools),
        "status": entry.status,  # loaded / failed / disabled
        "lifecycle_state": entry.lifecycle_state,
        "enabled": entry.enabled,
        "error": entry.error,
        "path": entry.path,
        "is_builtin": _is_builtin(entry.path),
    }


def _is_builtin(path: str) -> bool:
    from pathlib import Path

    builtin = Path(__file__).resolve().parent.parent / "plugins" / "builtin"
    try:
        Path(path).resolve().relative_to(builtin)
        return True
    except ValueError:
        return False


@router.get("/plugins")
async def list_plugins(request: Request) -> dict[str, Any]:
    registry = request.app.state.plugin_registry
    plugins = [_entry_to_dict(e) for e in registry.list_plugins()]
    return {"plugins": plugins}


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    loader = request.app.state.plugin_loader
    if not loader.enable_plugin(plugin_id):
        raise HTTPException(status_code=404, detail=f"插件不存在或无法启用：{plugin_id}")
    entry = loader.get_plugin(plugin_id)
    return {"ok": True, "plugin": _entry_to_dict(entry) if entry else None}


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    loader = request.app.state.plugin_loader
    if not loader.disable_plugin(plugin_id):
        raise HTTPException(status_code=404, detail=f"插件不存在或无法禁用：{plugin_id}")
    entry = loader.get_plugin(plugin_id)
    return {"ok": True, "plugin": _entry_to_dict(entry) if entry else None}


@router.delete("/plugins/{plugin_id}")
async def remove_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    loader = request.app.state.plugin_loader
    entry = loader.get_plugin(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}")
    if _is_builtin(entry.path):
        raise HTTPException(status_code=400, detail="内置插件不可移除。")
    if not loader.remove_plugin(plugin_id):
        raise HTTPException(status_code=500, detail="移除插件失败。")
    return {"ok": True, "removed": plugin_id}
