"""Memory（长期记忆）管理 API。

供前端「记忆」页面查看、创建、修改、删除记忆。Memory 与聊天记录、Workspace
文件相互独立，存储在 ``.weci/memory.json``。

端点：
  GET    /api/memories            列出当前 user / workspace / conversation 可见的记忆
  POST   /api/memories            创建（同 scope + key 时更新）
  PATCH  /api/memories/{id}       修改 key 或 content
  DELETE /api/memories/{id}       删除
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.services.memory import (
    MemoryError,
    MemoryLimitError,
    MemoryNotFound,
    MemoryScope,
    MemoryScopeContext,
    default_memory_store,
    resolve_scope_id,
)

router = APIRouter(tags=["memories"])


class MemoryCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=4_000)
    scope: MemoryScope = "user"
    user_id: str = Field(default="default", max_length=128)
    workspace_id: str = Field(default="", max_length=128)
    conversation_id: str = Field(default="", max_length=128)


class MemoryUpdateRequest(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=64)
    content: str | None = Field(default=None, min_length=1, max_length=4_000)


def _store(request: Request):
    return getattr(request.app.state, "memory_store", default_memory_store)


def _to_dict(entry) -> dict[str, Any]:
    return entry.model_dump()


@router.get("/memories")
async def list_memories(
    request: Request,
    scope: MemoryScope | None = Query(default=None),
    user_id: str = Query(default="default", max_length=128),
    workspace_id: str = Query(default="", max_length=128),
    conversation_id: str = Query(default="", max_length=128),
) -> dict[str, Any]:
    store = _store(request)
    context = MemoryScopeContext(
        user_id=user_id or "default",
        workspace_id=workspace_id or "",
        conversation_id=conversation_id or "",
    )
    if scope is not None:
        try:
            scope_id = resolve_scope_id(scope, context)
        except MemoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        entries = store.list(scope=scope, scope_id=scope_id)
    else:
        entries = store.for_context(
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            conversation_id=context.conversation_id,
        )
    return {"memories": [_to_dict(entry) for entry in entries]}


@router.post("/memories")
async def create_memory(
    payload: MemoryCreateRequest, request: Request
) -> dict[str, Any]:
    store = _store(request)
    context = MemoryScopeContext(
        user_id=payload.user_id or "default",
        workspace_id=payload.workspace_id or "",
        conversation_id=payload.conversation_id or "",
    )
    try:
        scope_id = resolve_scope_id(payload.scope, context)
        entry = store.save(payload.scope, scope_id, payload.key, payload.content)
    except MemoryLimitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "memory": _to_dict(entry)}


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: str, payload: MemoryUpdateRequest, request: Request
) -> dict[str, Any]:
    if payload.key is None and payload.content is None:
        raise HTTPException(status_code=400, detail="请至少提供 key 或 content。")
    store = _store(request)
    try:
        entry = store.update(memory_id, key=payload.key, content=payload.content)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MemoryLimitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MemoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "memory": _to_dict(entry)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    if not store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory 不存在或已被删除。")
    return {"ok": True, "removed": memory_id}
