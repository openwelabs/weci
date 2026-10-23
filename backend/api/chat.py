"""对话接口：接收浏览器请求，以 SSE 转发上游模型的流式输出。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.services.agent import run_agent
from backend.services.openai_client import UpstreamError, stream_chat
from backend.services.sse import sse_event
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200000)
    api_base: str | None = None
    mode: Literal["chat", "agent"] = "chat"


def _clean_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """过滤空消息，避免把空 assistant 占位发给上游。"""
    cleaned: list[dict[str, str]] = []
    for item in messages:
        content = item.content.strip()
        if content:
            cleaned.append({"role": item.role, "content": content})
    return cleaned


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    x_weci_api_key: str | None = Header(default=None, alias="X-WeCi-API-Key"),
) -> StreamingResponse:
    messages = _clean_messages(payload.messages)
    if not messages:
        raise HTTPException(status_code=400, detail="消息内容不能为空。")

    api_key = (x_weci_api_key or "").strip() or settings.api_key
    api_base = (payload.api_base or settings.default_api_base).strip()
    model = (payload.model or settings.default_model).strip()
    temperature = (
        payload.temperature
        if payload.temperature is not None
        else settings.default_temperature
    )
    max_tokens = (
        payload.max_tokens
        if payload.max_tokens is not None
        else settings.default_max_tokens
    )

    if not api_base:
        raise HTTPException(
            status_code=400, detail="API Endpoint 未配置，请先在设置中填写。"
        )
    if not model:
        raise HTTPException(status_code=400, detail="Model 不能为空。")

    registry: ToolRegistry = request.app.state.tool_registry

    async def event_stream() -> AsyncIterator[str]:
        yield sse_event({"type": "start", "model": model, "mode": payload.mode})
        try:
            if payload.mode == "agent":
                async for event in run_agent(
                    messages,
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    registry=registry,
                ):
                    yield sse_event(event)
            else:
                async for text in stream_chat(
                    messages,
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield sse_event({"type": "delta", "text": text})
                yield sse_event({"type": "done"})
        except asyncio.CancelledError:
            # 浏览器点击“停止生成”或断开连接，直接关闭上游请求
            raise
        except UpstreamError as exc:
            yield sse_event({"type": "error", "message": exc.message})
        except Exception:
            logger.exception("Unexpected error while streaming chat")
            yield sse_event(
                {"type": "error", "message": "服务端发生未知错误，请稍后重试。"}
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
