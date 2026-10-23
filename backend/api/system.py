"""系统接口：健康检查、默认配置（不含任何密钥）与连接测试。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel

from backend.config import settings
from backend.services.openai_client import check_connection

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "version": settings.version}


@router.get("/config")
async def get_config() -> dict[str, object]:
    return {
        "app": settings.app_name,
        "version": settings.version,
        "api_base": settings.default_api_base,
        "model": settings.default_model,
        "temperature": settings.default_temperature,
        "max_tokens": settings.default_max_tokens,
        "has_server_key": settings.has_api_key,
    }


class TestConnectionRequest(BaseModel):
    api_base: str | None = None
    model: str | None = None


@router.post("/test-connection")
async def test_connection(
    payload: TestConnectionRequest,
    x_weci_api_key: str | None = Header(default=None, alias="X-WeCi-API-Key"),
) -> dict[str, Any]:
    """测试与模型服务的连通性。

    始终返回 200 + 结构化结果（ok/message/latency_ms/models），
    由前端负责展示，避免把“测试失败”当作 HTTP 错误处理。
    """
    api_base = (payload.api_base or settings.default_api_base).strip()
    model = (payload.model or settings.default_model).strip()
    api_key = (x_weci_api_key or "").strip() or settings.api_key

    if not api_base:
        return {"ok": False, "message": "请先填写 API Endpoint。", "latency_ms": None, "models": None}

    result = await check_connection(api_base=api_base, api_key=api_key, model=model)
    result.setdefault("models", None)
    return result
