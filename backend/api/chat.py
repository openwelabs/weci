"""对话接口：接收浏览器请求，以 SSE 转发上游模型的流式输出。

上下文统一由 ``services/context.py`` 的 ``ContextManager`` 管理：
历史消息、Workspace、图片 / 文件附件、Agent 工具结果都先进入同一个
Context，再交给 Provider 生成最终模型请求。Chat 与 Agent 共用这条链路。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.services.agent import AGENT_SYSTEM_PROMPT, MAX_AGENT_STEPS, run_agent
from backend.services.agent_run import AgentRunManager
from backend.services.context import ContextManager, WorkspaceContext
from backend.services.memory import (
    MemoryScopeContext,
    default_memory_store,
    use_memory_scope,
)
from backend.services.message import AttachmentError, ChatMessage
from backend.services.openai_client import UpstreamError, complete_chat, stream_chat
from backend.services.providers import ProviderError, ResolvedModel
from backend.services.sse import sse_event
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _event_permissions(event: dict) -> list[str]:
    """从 tool_call 事件里提取工具声明的权限（用于 Run 审计）。"""
    permission = event.get("permission")
    if isinstance(permission, dict):
        permissions = permission.get("permissions")
        if isinstance(permissions, list):
            return [str(item) for item in permissions]
    return []


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    # 可选：显式指定 Provider；未指定时使用默认 Provider（若有）
    provider_id: str | None = Field(default=None, max_length=64)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200000)
    api_base: str | None = None
    mode: Literal["chat", "agent"] = "chat"
    # 当前 Workspace 上下文（第一阶段：说明 + 指定文件文本）
    workspace: WorkspaceContext | None = None
    # Memory 隔离标识：user / workspace / conversation 各自独立
    user_id: str = Field(default="default", max_length=128)
    workspace_id: str = Field(default="", max_length=128)
    conversation_id: str = Field(default="", max_length=128)
    # 可选：显式指定模型上下文窗口（tokens），默认按模型名推断
    context_window: int | None = Field(default=None, ge=1024, le=2_000_000)


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    x_weci_api_key: str | None = Header(default=None, alias="X-WeCi-API-Key"),
) -> StreamingResponse:
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
    effective_context_window = payload.context_window
    capabilities: dict[str, bool] | None = None
    resolved: ResolvedModel | None = None

    # Provider 是模型配置的唯一来源；未配置 Provider 时沿用旧兼容路径。
    provider_manager = getattr(request.app.state, "provider_manager", None)
    if provider_manager is not None:
        try:
            resolved = provider_manager.resolve(
                payload.provider_id, payload.model, payload.mode
            )
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if resolved is not None:
        api_base = resolved.api_base
        api_key = resolved.api_key or api_key
        model = resolved.model
        capabilities = resolved.capabilities
        effective_context_window = payload.context_window or resolved.context_window
        if payload.max_tokens is None and resolved.max_tokens is not None:
            max_tokens = resolved.max_tokens

    if not api_base:
        raise HTTPException(
            status_code=400, detail="API Endpoint 未配置，请先在设置中填写。"
        )
    if not model:
        raise HTTPException(status_code=400, detail="Model 不能为空。")

    # 发送前按模型声明的能力拦截，避免等到上游请求失败才发现。
    if capabilities is not None:
        if not capabilities.get("text", False):
            raise HTTPException(
                status_code=400,
                detail=f"当前模型「{model}」未声明 Text 能力，无法用于对话。",
            )
        if payload.mode == "agent" and not capabilities.get("tool_calling", False):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"当前模型「{model}」未声明 Tool Calling 能力，Agent 模式不可用。"
                    "请在 Provider 管理中启用该能力，或切换到支持工具调用的模型。"
                ),
            )
        has_images = any(message.attachments for message in payload.messages)
        if has_images and not capabilities.get("vision", False):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"当前模型「{model}」未声明 Vision（图片输入）能力，无法处理图片。"
                    "请移除图片，或在 Provider 管理中启用该能力 / 切换到支持视觉的模型。"
                ),
            )

    use_stream = True if resolved is None else resolved.supports("streaming")

    # Chat 与 Agent 共用同一个 Context：历史 / Workspace / 附件 / 工具结果
    scope = MemoryScopeContext(
        user_id=payload.user_id or "default",
        workspace_id=payload.workspace_id
        or (payload.workspace.name if payload.workspace else "")
        or "",
        conversation_id=payload.conversation_id or "",
    )
    memory_store = getattr(request.app.state, "memory_store", default_memory_store)
    memories = memory_store.for_context(
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        conversation_id=scope.conversation_id,
        max_items=settings.context_memory_max_items,
    )
    try:
        context = ContextManager.from_chat_messages(
            payload.messages,
            system_prompt=(
                AGENT_SYSTEM_PROMPT if payload.mode == "agent" else None
            ),
            workspace=payload.workspace,
            memories=memories,
            model=model,
            context_window=effective_context_window,
            reserve_tokens=max_tokens or 0,
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not context.has_messages():
        raise HTTPException(status_code=400, detail="消息内容不能为空。")

    registry: ToolRegistry = request.app.state.tool_registry
    run_manager: AgentRunManager | None = getattr(
        request.app.state, "agent_run_manager", None
    )
    # 统一权限层：所有工具（内置 / 插件 / MCP）都经它判定
    permission_manager = getattr(request.app.state, "permission_manager", None)

    async def event_stream() -> AsyncIterator[str]:
        # Memory scope 仅在本次请求内有效，供 Agent 工具读写当前用户的记忆
        with use_memory_scope(scope):
            built = context.build()

            # Agent 模式：先创建 Run，让生命周期与 SSE 解耦
            run = None
            cancel_event = None
            if payload.mode == "agent" and run_manager is not None:
                run = run_manager.create(
                    conversation_id=payload.conversation_id or "",
                    model=model,
                    provider_id=resolved.provider_id if resolved is not None else "",
                    mode="agent",
                    max_steps=MAX_AGENT_STEPS,
                )
                run_manager.start(run.id)
                cancel_event = run_manager.cancel_event(run.id)

            # 上下文预算 / 压缩情况对前端可见，但不包含任何消息正文与 Key
            start_event: dict[str, object] = {
                "type": "start",
                "model": model,
                "mode": payload.mode,
                "context": built.stats,
            }
            if resolved is not None:
                start_event["provider_id"] = resolved.provider_id
                start_event["provider_name"] = resolved.provider_name
                start_event["capabilities"] = capabilities
            if run is not None:
                start_event["run_id"] = run.id
            yield sse_event(start_event)

            def _finalize_run(reason: str, error_type: str) -> None:
                if run is None or run_manager is None:
                    return
                if run.status in ("completed", "failed", "cancelled"):
                    return
                if error_type == "cancelled" or run_manager.is_cancelled(run.id):
                    run_manager.cancel(run.id)
                else:
                    run_manager.fail(run.id, error=reason, error_type=error_type)

            try:
                if payload.mode == "agent":
                    agent_stream = run_agent(
                        context,
                        api_base=api_base,
                        api_key=api_key,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        registry=registry,
                        stream=use_stream,
                        cancel_event=cancel_event,
                        permission_manager=permission_manager,
                        run_id=run.id if run is not None else "",
                    )
                    accumulated = ""
                    try:
                        async for event in agent_stream:
                            # 把 Agent 事件写入 Run 状态，再透传给前端
                            if run is not None and run_manager is not None:
                                etype = event.get("type")
                                if etype == "thinking":
                                    run_manager.set_step(
                                        run.id,
                                        int(event.get("step") or 0),
                                        int(event["max_steps"]) if "max_steps" in event else None,
                                    )
                                elif etype == "tool_call":
                                    call_id = str(event.get("id") or "")
                                    if event.get("status") == "running":
                                        run_manager.add_tool_call(
                                            run.id,
                                            call_id=call_id,
                                            name=str(event.get("name") or ""),
                                            step=int(event.get("step") or 0),
                                            arguments=event.get("arguments"),
                                        )
                                    elif event.get("status") == "done":
                                        run_manager.record_tool_permission(
                                            run.id,
                                            call_id,
                                            permissions=_event_permissions(event),
                                            result=str(
                                                event.get("permission_result") or "granted"
                                            ),
                                            user_confirmation=event.get("user_confirmation"),
                                        )
                                        run_manager.complete_tool_call(
                                            run.id,
                                            call_id,
                                            output=event.get("output") or "",
                                            duration_ms=event.get("duration_ms"),
                                        )
                                    elif event.get("status") == "error":
                                        if event.get("permission_result") == "denied":
                                            run_manager.record_tool_permission(
                                                run.id,
                                                call_id,
                                                permissions=_event_permissions(event),
                                                result="denied",
                                                user_confirmation=event.get(
                                                    "user_confirmation"
                                                ),
                                                event="permission.denied",
                                                detail=str(event.get("error") or ""),
                                            )
                                        run_manager.fail_tool_call(
                                            run.id,
                                            call_id,
                                            error=event.get("error") or "工具执行失败",
                                            duration_ms=event.get("duration_ms"),
                                        )
                                elif etype == "permission_request":
                                    run_manager.record_permission_request(
                                        run.id,
                                        request_id=str(event.get("id") or ""),
                                        tool=str(event.get("tool") or ""),
                                        permission=str(event.get("permission") or ""),
                                        detail=str(event.get("title") or ""),
                                    )
                                elif etype == "delta":
                                    text = event.get("text")
                                    if text:
                                        accumulated += text
                                        run_manager.append_result(run.id, text)
                                elif etype == "done":
                                    run_manager.complete(
                                        run.id,
                                        result=accumulated,
                                        steps=int(event.get("steps") or 0),
                                    )
                                elif etype == "max_steps":
                                    note = event.get("message") or "已达到最大执行步数。"
                                    run_manager.complete(
                                        run.id,
                                        result=f"{accumulated}\n\n{note}",
                                        steps=int(event.get("steps") or MAX_AGENT_STEPS),
                                    )
                                elif etype == "error":
                                    run_manager.fail(
                                        run.id,
                                        error=event.get("message") or "Agent 执行失败",
                                        error_type="provider",
                                    )
                                elif etype == "cancelled":
                                    if run.status != "cancelled":
                                        run_manager.cancel(run.id)
                                event["run_id"] = run.id

                            # SSE 只是传输层：断开不等于完成
                            if await request.is_disconnected():
                                logger.info("客户端已断开，停止 Agent Loop")
                                _finalize_run("连接中断，运行已停止。", "network")
                                return
                            yield sse_event(event)
                    finally:
                        await agent_stream.aclose()
                else:
                    if use_stream:
                        async for text in stream_chat(
                            built,
                            api_base=api_base,
                            api_key=api_key,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ):
                            yield sse_event({"type": "delta", "text": text})
                    else:
                        # 模型未声明 Streaming：一次性返回完整回答
                        answer = await complete_chat(
                            built,
                            api_base=api_base,
                            api_key=api_key,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        if answer:
                            yield sse_event({"type": "delta", "text": answer})
                    yield sse_event({"type": "done"})
            except asyncio.CancelledError:
                # 浏览器点击“停止生成”或断开连接
                _finalize_run("执行已停止。", "cancelled")
                raise
            except UpstreamError as exc:
                _finalize_run(exc.message, "provider")
                err_payload: dict[str, object] = {"type": "error", "message": exc.message}
                if run is not None:
                    err_payload["run_id"] = run.id
                yield sse_event(err_payload)
            except Exception:
                logger.exception("Unexpected error while streaming chat")
                _finalize_run("服务端发生未知错误，请稍后重试。", "unknown")
                err_payload = {
                    "type": "error",
                    "message": "服务端发生未知错误，请稍后重试。",
                }
                if run is not None:
                    err_payload["run_id"] = run.id
                yield sse_event(err_payload)
            finally:
                # 兜底：任何未到达终态的 Run 都不能停留在 running
                _finalize_run("运行异常终止。", "network")
                # 清理本次 Run 的临时授权与未决确认请求
                if permission_manager is not None and run is not None:
                    permission_manager.end_run(run.id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
