"""Agent Loop。

负责把“用户消息 → 模型 → 工具 → 模型 → 最终回答”串起来，并通过
SSE 事件把过程状态实时推给前端。

对外只暴露安全、简洁的状态事件：
  start       开始
  thinking    模型正在思考（第 N 步）
  tool_call   工具调用：running / done / error（含耗时）
  delta       最终回答的文本增量
  max_steps   达到最大步数，提前结束
  error       出错
  done        正常结束

绝不把模型的 chain-of-thought 或工具内部参数暴露给前端（前端只看到
工具名、状态、耗时）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from backend.services.openai_client import (
    ToolCall,
    UpstreamError,
    stream_chat_with_tools,
)
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 10

_AGENT_SYSTEM_PROMPT = (
    "你是 WeCi 的 Agent 助手，可以在需要时调用工具来获取准确信息或执行计算。\n"
    "可用工具会随请求提供。当用户的问题需要计算、查询当前时间或联网信息时，"
    "请主动调用对应工具，把工具返回的结果作为依据来回答。\n"
    "回答要简洁、准确；如果工具不可用或结果不足以回答，请如实说明。"
)


async def run_agent(
    messages: list[dict[str, str]],
    *,
    api_base: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
    registry: ToolRegistry,
) -> AsyncIterator[dict[str, Any]]:
    """执行 Agent Loop，yield 上述 SSE 事件 dict。"""
    tools_schema = registry.schemas()

    # 内部发给模型的消息序列（包含 system、历史、工具调用与结果）
    model_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
        *messages,
    ]

    for step in range(1, MAX_AGENT_STEPS + 1):
        yield {"type": "thinking", "step": step, "max_steps": MAX_AGENT_STEPS}

        round_tool_calls: list[ToolCall] | None = None

        try:
            async for event in stream_chat_with_tools(
                model_messages,
                tools=tools_schema,
                api_base=api_base,
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
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
            yield {"type": "done"}
            return

        # ---- 执行工具调用 ----
        # 先把带 tool_calls 的 assistant 消息记入模型上下文
        assistant_with_tools: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in round_tool_calls
            ],
        }
        model_messages.append(assistant_with_tools)

        for tc in round_tool_calls:
            call_id = tc.id or f"call-{step}"
            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "running",
            }

            result = await registry.execute(tc.name, tc.arguments)

            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "done" if result.ok else "error",
                "duration_ms": round(result.duration_ms, 1),
                "error": result.error if not result.ok else None,
            }

            model_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result.to_observation(),
                }
            )

        # 继续下一轮，让模型基于工具结果生成回答

    # 达到最大步数
    yield {
        "type": "max_steps",
        "message": f"已达到最大执行步数（{MAX_AGENT_STEPS}），Agent 已停止。",
    }
    yield {"type": "done"}
