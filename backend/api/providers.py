"""模型 Provider 管理 API。

端点：
  GET    /api/providers                                  列出 Provider（含模型与能力）
  POST   /api/providers                                  新增 Provider（可同时带模型）
  PATCH  /api/providers/{provider_id}                    修改 Provider / 启用禁用 / 设为默认
  DELETE /api/providers/{provider_id}                    删除 Provider
  POST   /api/providers/{provider_id}/models             新增模型
  PATCH  /api/providers/{provider_id}/models/{model_id}  修改 / 重命名模型
  DELETE /api/providers/{provider_id}/models/{model_id}  删除模型
  POST   /api/providers/{provider_id}/test               测试 Provider 连通性
  POST   /api/providers/{provider_id}/models/{model_id}/test  测试单个模型

公开响应永远不包含 API Key，只返回 ``has_api_key``。
测试接口始终返回 200 + 结构化结果（ok/message/latency_ms/models）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services.providers import ProviderError, ProviderManager

logger = logging.getLogger("weci.providers.api")

router = APIRouter(tags=["providers"])


class ModelPayload(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=120)
    capabilities: dict[str, bool] | None = None
    context_window: int | None = None
    max_tokens: int | None = None


class ModelUpdateRequest(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    capabilities: dict[str, bool] | None = None
    context_window: int | None = None
    max_tokens: int | None = None


class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    api_base: str = Field(min_length=1, max_length=1024)
    api_key: str = Field(default="", max_length=4096)
    description: str = Field(default="", max_length=200)
    enabled: bool = True
    is_default: bool = False
    default_model: str = Field(default="", max_length=200)
    chat_model: str = Field(default="", max_length=200)
    agent_model: str = Field(default="", max_length=200)
    models: list[ModelPayload] = Field(default_factory=list, max_length=200)


class ProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    api_base: str | None = Field(default=None, min_length=1, max_length=1024)
    # None = 不修改；"" = 清除
    api_key: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    is_default: bool | None = None
    default_model: str | None = Field(default=None, max_length=200)
    chat_model: str | None = Field(default=None, max_length=200)
    agent_model: str | None = Field(default=None, max_length=200)


class ProviderTestRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    # 仅用于本次测试，不写入配置
    api_key: str | None = Field(default=None, max_length=4096)


def _manager(request: Request) -> ProviderManager:
    manager = getattr(request.app.state, "provider_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Provider 管理器未初始化。")
    return manager


def _http_error(exc: ProviderError) -> HTTPException:
    status = 404 if "不存在" in str(exc) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    return {"providers": _manager(request).list_providers()}


@router.post("/providers")
async def create_provider(
    payload: ProviderCreateRequest, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        provider = manager.add_provider(
            name=payload.name,
            api_base=payload.api_base,
            api_key=payload.api_key,
            models=[model.model_dump() for model in payload.models],
            default_model=payload.default_model,
            chat_model=payload.chat_model,
            agent_model=payload.agent_model,
            enabled=payload.enabled,
            description=payload.description,
            is_default=payload.is_default,
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "provider": provider}


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: str, payload: ProviderUpdateRequest, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        provider = manager.update_provider(
            provider_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "provider": provider}


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if not manager.remove_provider(provider_id):
        raise HTTPException(status_code=404, detail=f"Provider 不存在：{provider_id}")
    return {"ok": True, "removed": provider_id}


@router.post("/providers/{provider_id}/models")
async def create_model(
    provider_id: str, payload: ModelPayload, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        provider = manager.add_model(
            provider_id,
            model_id=payload.id,
            display_name=payload.display_name,
            capabilities=payload.capabilities,
            context_window=payload.context_window,
            max_tokens=payload.max_tokens,
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "provider": provider}


@router.patch("/providers/{provider_id}/models/{model_id:path}")
async def update_model(
    provider_id: str,
    model_id: str,
    payload: ModelUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    manager = _manager(request)
    data = payload.model_dump(exclude_unset=True)
    new_id = data.pop("id", None)
    try:
        provider = manager.update_model(
            provider_id,
            model_id,
            new_id=new_id,
            **data,
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "provider": provider}


@router.delete("/providers/{provider_id}/models/{model_id:path}")
async def delete_model(
    provider_id: str, model_id: str, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        provider = manager.remove_model(provider_id, model_id)
    except ProviderError as exc:
        raise _http_error(exc) from exc
    return {"ok": True, "provider": provider}


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str, payload: ProviderTestRequest, request: Request
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        return await manager.test_provider(
            provider_id,
            model=payload.model,
            api_key_override=payload.api_key,
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc


@router.post("/providers/{provider_id}/models/{model_id:path}/test")
async def test_model(
    provider_id: str,
    model_id: str,
    payload: ProviderTestRequest,
    request: Request,
) -> dict[str, Any]:
    manager = _manager(request)
    try:
        return await manager.test_model(
            provider_id,
            model_id,
            api_key_override=payload.api_key,
        )
    except ProviderError as exc:
        raise _http_error(exc) from exc
