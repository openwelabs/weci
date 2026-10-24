"""Agent Loop。

负责把“用户消息 → 模型 → 工具 → 模型 → 最终回答”串起来，并通过
SSE 事件把过程状态实时推给前端。

上下文统一由 ``services/context.py`` 的 ``ContextManager`` 管理：
system、历史消息、附件、Workspace 与工具结果都在同一个 Context 中，
每轮工具执行后写回新的 tool 消息并重新 ``build()``，与 Chat 共用同一套
预算与压缩逻辑。

对外只暴露安全、简洁的执行状态事件（不包含 chain-of-thought）：
  start       开始
  thinking    模型正在处理（第 N 步）
  tool_call   工具调用：running（含参数）/ done（含输出）/ error（含原因）
  delta       最终回答的文本增量
  max_steps   达到最大步数，提前结束
  error       出错
  done        正常结束

工具的参数与返回结果是用户可核查的执行事实，会通过 SSE 传给前端；
服务端会把过长的输出截断后再发送，完整内容仅用于模型上下文。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from backend.services.context import ContextManager
from backend.services.openai_client import (
    ToolCall,
    UpstreamError,
    stream_chat_with_tools,
)
from backend.services.permissions import redact
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 10

# 单个工具结果在 UI 中最多展示的字符数（完整结果仍会发给模型）
MAX_TOOL_DISPLAY_CHARS = 20_000

AGENT_SYSTEM_PROMPT = (
    "你是 WeCi 的 Agent 助手，可以在需要时调用工具来获取准确信息或执行计算。\n"
    "可用工具会随请求提供。当用户的问题需要计算、查询当前时间或联网信息时，"
    "请主动调用对应工具，把工具返回的结果作为依据来回答。\n"
    "记忆规则：只有当用户**明确要求**记住或忘记某事时（例如“记住我使用 Fedora”"
    "“忘掉我的编辑器”），才调用 save_memory / delete_memory 工具；"
    "不要把普通聊天内容自动保存为记忆，也不要在用户未要求时删除记忆。"
    "如果上下文中提供了 <memories>，只在相关问题中参考，不要臆造。\n"
    "回答要简洁、准确；如果工具不可用或结果不足以回答，请如实说明。"
)


def _display_arguments(raw: str | dict[str, Any] | None) -> Any:
    """把工具参数转换为可展示的结构（解析失败时保留原始字符串）。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    return parsed if parsed is not None else {}


def _display_output(output: str) -> str:
    """截断过长的工具输出后再发送给前端。"""
    text = output or ""
    if len(text) <= MAX_TOOL_DISPLAY_CHARS:
        return text
    return (
        text[:MAX_TOOL_DISPLAY_CHARS]
        + f"\n\n…（输出过长，已省略后续 {len(text) - MAX_TOOL_DISPLAY_CHARS} 个字符）"
    )


def _describe_action(tool_name: str, arguments: Any) -> str:
    """生成给用户看的、已脱敏的操作描述（用于权限确认卡片）。"""
    if not isinstance(arguments, dict):
        return f"调用工具 {tool_name}"

    if tool_name == "run_command":
        command = arguments.get("command")
        if command:
            return f"执行命令：{redact(str(command))[:300]}"

    path = arguments.get("path") or arguments.get("file")
    if tool_name == "delete_file":
        return f"删除 Workspace 文件：{path}" if path else "删除 Workspace 文件"
    if tool_name == "write_file":
        content = str(arguments.get("content") or "")
        return (
            f"写入 Workspace 文件：{path}（{len(content)} 字符）"
            if path
            else "写入 Workspace 文件"
        )
    if tool_name in ("read_file", "list_dir") and path:
        return f"访问 Workspace 路径：{path}"

    target = arguments.get("url") or arguments.get("query")
    if target:
        return f"访问网络：{redact(str(target))[:200]}"

    keys = ", ".join(list(arguments)[:4])
    return f"调用工具 {tool_name}" + (f"（参数：{keys}）" if keys else "")


async def run_agent(
    context: ContextManager,
    *,
    api_base: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
    registry: ToolRegistry,
    stream: bool = True,
    cancel_event: asyncio.Event | None = None,
    permission_manager: Any = None,
    run_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """执行 Agent Loop，yield 上述 SSE 事件 dict。

    ``context`` 由 Chat 入口统一构建（system / 历史 / Workspace / 附件），
    Agent Loop 不再自行拼接上下文；每轮工具执行后只把新的工具消息写回
    Context，再由 Provider 根据最新 Context 生成请求。
    ``stream=False`` 用于模型未声明 Streaming 能力时，上游走非流式请求。
    ``cancel_event`` 由 AgentRunManager 提供：用户点击 Stop 后被设置，
    Agent Loop 在每步与工具之间检查并立即停止，避免继续消耗上游请求。

    ``permission_manager`` 是统一权限层。工具执行前先按 ``Tool.permissions``
    判定；高风险权限会 yield ``permission_request`` 事件并等待用户确认
    （Allow once / Allow for this Run / Deny），**不**默认放行。
    Agent Loop 本身不做任何权限判断，只负责把判定结果转成事件。
    """
    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _evaluate_permission(tool_name: str, arguments: Any) -> dict[str, Any]:
        """在工具执行前做统一权限判定（不阻塞）。

        返回值：
          - ``allowed=True``：可以执行，附 ``permission`` 判定信息
          - ``allowed=False``：直接拒绝，附 ``message``
          - ``needs_confirmation=True``：需要用户确认，附 ``request``，
            由调用方 yield ``permission_request`` 事件后再 ``await wait()``
        """
        if permission_manager is None:
            return {"allowed": True}
        principals = registry.principals_for(tool_name)
        permissions = registry.permissions_for(tool_name)
        decision = permission_manager.check(
            permissions,
            principals=principals,
            run_id=run_id,
            tool_name=tool_name,
        )
        if decision.allowed:
            if permissions:
                permission_manager.log(
                    "permission.granted",
                    tool=tool_name,
                    permission=",".join(permissions),
                    principal=decision.principal,
                    source=decision.source,
                    run_id=run_id,
                )
            return {
                "allowed": True,
                "permission": {
                    "permissions": permissions,
                    "principal": decision.principal,
                    "source": decision.source,
                },
            }
        if not decision.needs_confirmation:
            permission_manager.log(
                "permission.denied",
                tool=tool_name,
                permission=decision.permission,
                principal=decision.principal,
                source=decision.source,
                run_id=run_id,
            )
            return {
                "allowed": False,
                "permission": decision.to_dict(),
                "message": decision.reason,
            }

        # 高风险权限：先交给前端确认，再阻塞等待结果
        request = permission_manager.create_request(
            permission=decision.permission,
            principals=principals,
            run_id=run_id,
            tool_name=tool_name,
            detail=_describe_action(tool_name, arguments),
        )
        return {
            "allowed": False,
            "needs_confirmation": True,
            "request": request,
            "permission": {
                "permissions": permissions,
                "principal": decision.principal,
                "source": decision.source,
            },
        }

    tools_schema = registry.schemas()

    for step in range(1, MAX_AGENT_STEPS + 1):
        if _cancelled():
            yield {"type": "cancelled", "step": step, "max_steps": MAX_AGENT_STEPS}
            return
        yield {"type": "thinking", "step": step, "max_steps": MAX_AGENT_STEPS}

        round_tool_calls: list[ToolCall] | None = None

        try:
            async for event in stream_chat_with_tools(
                context.build(),
                tools=tools_schema,
                api_base=api_base,
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
            ):
                if _cancelled():
                    yield {"type": "cancelled", "step": step, "max_steps": MAX_AGENT_STEPS}
                    return
                if "delta" in event:
                    yield {"type": "delta", "text": event["delta"]}
                elif "tool_calls" in event:
                    round_tool_calls = event["tool_calls"]
        except UpstreamError as exc:
            yield {"type": "error", "message": exc.message}
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent step %s failed", step)
            yield {"type": "error", "message": "Agent 执行过程中发生未知错误。"}
            return

        # 本轮没有工具调用 → 最终回答已通过 delta 输出，结束
        if not round_tool_calls:
            yield {"type": "done", "steps": step}
            return

        if _cancelled():
            yield {"type": "cancelled", "step": step, "max_steps": MAX_AGENT_STEPS}
            return

        # ---- 执行工具调用 ----
        # 先把带 tool_calls 的 assistant 消息记入统一 Context
        normalized_calls: list[dict[str, Any]] = []
        for index, tc in enumerate(round_tool_calls):
            call_id = tc.id or f"call-{step}-{index}"
            normalized_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
            )
        pair_id = context.add_tool_calls(normalized_calls)

        for tc, call in zip(round_tool_calls, normalized_calls):
            if _cancelled():
                yield {"type": "cancelled", "step": step, "max_steps": MAX_AGENT_STEPS}
                return
            call_id = str(call["id"])
            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "running",
                "step": step,
                "max_steps": MAX_AGENT_STEPS,
                "arguments": _display_arguments(tc.arguments),
            }

            # ---- 统一权限判定（Agent → Tool Registry → Permission Manager → Tool）----
            auth = _evaluate_permission(tc.name, tc.arguments)
            if auth.get("needs_confirmation"):
                request = auth["request"]
                yield {
                    "type": "permission_request",
                    "call_id": call_id,
                    "step": step,
                    "max_steps": MAX_AGENT_STEPS,
                    **request.public_dict(),
                }
                resolved = await permission_manager.wait(request, cancel_event)
                if _cancelled():
                    yield {"type": "cancelled", "step": step, "max_steps": MAX_AGENT_STEPS}
                    return
                auth = {
                    "allowed": resolved.allowed,
                    "permission": resolved.to_dict(),
                    "message": resolved.reason,
                    "user_confirmation": request.decision or "timeout",
                }

            if not auth.get("allowed", True):
                message = auth.get("message") or "缺少执行该工具所需的权限。"
                yield {
                    "type": "tool_call",
                    "id": call_id,
                    "name": tc.name,
                    "status": "error",
                    "step": step,
                    "max_steps": MAX_AGENT_STEPS,
                    "error": message,
                    "permission": auth.get("permission"),
                    "permission_result": "denied",
                    "user_confirmation": auth.get("user_confirmation"),
                }
                context.add_tool_result(
                    call_id,
                    f"[工具执行失败] {message}",
                    pair_id=pair_id,
                )
                continue

            # Agent Loop 已在本层完成判定，Registry 不再重复询问用户
            result = await registry.execute(
                tc.name, tc.arguments, run_id=run_id, pre_authorized=True
            )

            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "done" if result.ok else "error",
                "step": step,
                "max_steps": MAX_AGENT_STEPS,
                "duration_ms": round(result.duration_ms, 1),
                "output": _display_output(result.output),
                "error": result.error if not result.ok else None,
                "permission": auth.get("permission"),
                "permission_result": "granted",
                "user_confirmation": auth.get("user_confirmation"),
            }

            context.add_tool_result(
                call_id,
                result.to_observation(),
                pair_id=pair_id,
            )

        # 继续下一轮，让模型基于工具结果生成回答

    # 达到最大步数
    yield {
        "type": "max_steps",
        "message": f"已达到最大执行步数（{MAX_AGENT_STEPS}），Agent 已停止。",
        "steps": MAX_AGENT_STEPS,
        "max_steps": MAX_AGENT_STEPS,
    }
    yield {"type": "done", "steps": MAX_AGENT_STEPS}
