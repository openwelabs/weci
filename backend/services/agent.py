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
  loop_detected  检测到重复操作，提示模型重新规划
  delta       最终回答的文本增量
  error       出错
  done        正常结束

Agent Loop 默认不限制执行步数（Unlimited Execution），只在任务完成、用户
Stop / Cancel、Provider 出错、Context 超限、Loop Detection 触发或运行时
限制下停止。步数仅作为统计信息上报，不再作为停止条件。

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
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Loop Protection：连续多少次相同操作 / 相同错误视为无进展
LOOP_REPEAT_THRESHOLD = 3
# 同一次 Run 内最多容忍几次重复检测；超过后结束 Run
LOOP_MAX_DETECTIONS = 2

# 单个工具结果在 UI 中最多展示的字符数（完整结果仍会发给模型）
MAX_TOOL_DISPLAY_CHARS = 20_000

AGENT_SYSTEM_PROMPT = (
    "你是 WeCi 的 Agent 助手，可以在需要时调用工具来获取准确信息或执行计算。\n"
    "可用工具会随请求提供。当用户的问题需要计算、查询当前时间或联网信息时，"
    "请主动调用对应工具，把工具返回的结果作为依据来回答。\n"
    "Continue working until the task is complete. Avoid repetitive actions.\n"
    "记忆规则：只有当用户**明确要求**记住或忘记某事时（例如“记住我使用 Fedora”"
    "“忘掉我的编辑器”），才调用 save_memory / delete_memory 工具；"
    "不要把普通聊天内容自动保存为记忆，也不要在用户未要求时删除记忆。"
    "如果上下文中提供了 <memories>，只在相关问题中参考，不要臆造。\n"
    "回答要简洁、准确；如果工具不可用或结果不足以回答，请如实说明。"
)


class LoopGuard:
    """检测 Agent 无进展的重复操作（相同调用 / 相同错误连续出现）。

    仅做结构化比对，不解析业务语义：
      - 相同 Tool + 相同参数连续出现 ``LOOP_REPEAT_THRESHOLD`` 次；
      - 相同错误连续出现 ``LOOP_REPEAT_THRESHOLD`` 次。

    触发后调用方应告知模型重新规划；再次触发则结束 Run。
    """

    def __init__(self, threshold: int = LOOP_REPEAT_THRESHOLD) -> None:
        self.threshold = max(2, int(threshold))
        self.detections = 0
        self._last_call: tuple[str, str] | None = None
        self._call_streak = 0
        self._last_error = ""
        self._error_streak = 0

    @staticmethod
    def _signature(tool_name: str, arguments: Any) -> tuple[str, str]:
        raw = arguments
        if isinstance(raw, str):
            text = raw.strip()
            try:
                raw = json.loads(text)
            except ValueError:
                return tool_name, text
        if not isinstance(raw, dict):
            raw = {}
        try:
            canonical = json.dumps(
                raw, ensure_ascii=False, sort_keys=True, default=str
            )
        except (TypeError, ValueError):
            canonical = str(raw)
        return tool_name, canonical

    def check_call(self, tool_name: str, arguments: Any) -> str | None:
        """执行前检查：相同 Tool + 相同参数是否连续重复。"""
        signature = self._signature(tool_name, arguments)
        if signature == self._last_call:
            self._call_streak += 1
        else:
            self._last_call = signature
            self._call_streak = 1
        if self._call_streak >= self.threshold:
            return (
                f"工具 {tool_name} 使用完全相同的参数连续执行了 "
                f"{self._call_streak} 次。"
            )
        return None

    def check_result(self, tool_name: str, ok: bool, error: str | None) -> str | None:
        """执行后检查：相同错误是否连续出现。"""
        if ok:
            self._last_error = ""
            self._error_streak = 0
            return None
        text = (error or "工具执行失败").strip()
        if text and text == self._last_error:
            self._error_streak += 1
        else:
            self._last_error = text
            self._error_streak = 1
        if self._error_streak >= self.threshold:
            return (
                f"工具 {tool_name} 连续 {self._error_streak} 次返回相同错误："
                f"{text[:200]}"
            )
        return None

    def record_detection(self) -> bool:
        """记录一次检测，返回 True 表示应结束 Run。"""
        self.detections += 1
        return self.detections >= LOOP_MAX_DETECTIONS


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
    security_policy: Any = None,
    run_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """执行 Agent Loop，yield 上述 SSE 事件 dict。

    ``context`` 由 Chat 入口统一构建（system / 历史 / Workspace / 附件），
    Agent Loop 不再自行拼接上下文；每轮工具执行后只把新的工具消息写回
    Context，再由 Provider 根据最新 Context 生成请求。
    ``stream=False`` 用于模型未声明 Streaming 能力时，上游走非流式请求。
    ``cancel_event`` 由 AgentRunManager 提供：用户点击 Stop 后被设置，
    Agent Loop 在每步与工具之间检查并立即停止，避免继续消耗上游请求。

    ``permission_manager`` 是权限记录层，``security_policy`` 是命令 / 工具
    风险判定层（Safe / Auto Review / Risky）。二者只**记录状态**，不会在
    执行前拦截任何工具或命令：

        Agent → Tool Registry → Tool → OS

    失败后由工具与操作系统如实返回错误，再写回 Context 与审计日志。
    """
    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _evaluate_security(tool_name: str, arguments: Any) -> Any:
        """命令 / 工具风险判定；无策略层时返回 None。"""
        if security_policy is None:
            return None
        args = arguments if isinstance(arguments, dict) else {}
        try:
            return security_policy.evaluate_tool(
                tool_name, args, declared_risk=registry.risk_for(tool_name)
            )
        except Exception:  # noqa: BLE001 — 策略异常不得让 Agent 崩溃
            logger.exception("security policy evaluation failed: %s", tool_name)
            return None

    def _evaluate_permission(tool_name: str, arguments: Any) -> dict[str, Any]:
        """记录「策略 + 权限」判定结果，但**永远放行**。

        WeCi 不再在执行前拦截工具或命令：Security Policy 与 Permission
        Manager 仅用于记录状态（安全模式、风险等级、权限来源），是否真正
        执行成功只由操作系统与工具本身决定。
        """
        args = arguments if isinstance(arguments, dict) else {}
        policy = _evaluate_security(tool_name, args)
        security = policy.to_dict() if policy is not None else None

        if permission_manager is None:
            return {"allowed": True, "security": security}

        principals = registry.principals_for(tool_name)
        permissions = registry.permissions_for(tool_name)
        decision = permission_manager.check(
            permissions,
            principals=principals,
            run_id=run_id,
            tool_name=tool_name,
        )

        # 只记录：即使权限被标记 Deny 或原本需要确认，也不阻止执行
        if permissions:
            permission_manager.log(
                "permission.granted",
                tool=tool_name,
                permission=",".join(permissions),
                principal=decision.principal,
                source=decision.source,
                mode=policy.mode if policy is not None else "",
                run_id=run_id,
            )

        return {
            "allowed": True,
            "permission": {
                "permissions": permissions,
                "principal": decision.principal,
                "source": decision.source,
            },
            "security": security,
        }

    tools_schema = registry.schemas()
    loop_guard = LoopGuard()

    # Unlimited Execution：没有固定步数上限，只在任务完成、取消、错误、
    # Loop Detection 或运行时限制下退出。
    step = 0
    while True:
        step += 1
        if _cancelled():
            yield {"type": "cancelled", "step": step}
            return
        yield {"type": "thinking", "step": step}

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
                    yield {"type": "cancelled", "step": step}
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
            yield {"type": "cancelled", "step": step}
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
                yield {"type": "cancelled", "step": step}
                return
            call_id = str(call["id"])
            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "running",
                "step": step,
                "arguments": _display_arguments(tc.arguments),
            }

            # ---- 统一「策略 + 权限」判定 ----
            # Agent → Tool Registry → Security Policy → Risk Analyzer → Permission
            display_args = _display_arguments(tc.arguments)

            # Loop Protection：执行前拦截完全相同的连续操作
            loop_reason = loop_guard.check_call(tc.name, display_args)
            if loop_reason:
                exhausted = loop_guard.record_detection()
                yield {
                    "type": "tool_call",
                    "id": call_id,
                    "name": tc.name,
                    "status": "error",
                    "step": step,
                    "error": f"LOOP_DETECTED: {loop_reason}",
                    "code": "LOOP_DETECTED",
                    "loop_detected": True,
                }
                context.add_tool_result(
                    call_id,
                    f"[LOOP_DETECTED] {loop_reason} "
                    "请停止重复相同操作，换一种方式重新规划后再试。",
                    pair_id=pair_id,
                )
                yield {
                    "type": "loop_detected",
                    "code": "LOOP_DETECTED",
                    "reason": loop_reason,
                    "tool": tc.name,
                    "step": step,
                    "detections": loop_guard.detections,
                }
                if exhausted:
                    yield {
                        "type": "error",
                        "code": "LOOP_DETECTED",
                        "error_type": "loop",
                        "message": (
                            "LOOP_DETECTED：检测到重复操作且无法恢复，"
                            f"Agent 已停止（{loop_reason}）"
                        ),
                    }
                    return
                continue

            auth = _evaluate_permission(tc.name, display_args)
            security = auth.get("security")

            # Agent Loop 只记录判定结果，Registry 直接执行，不询问用户
            result = await registry.execute(
                tc.name, tc.arguments, run_id=run_id, pre_authorized=True
            )

            metadata = dict(result.metadata or {})
            # Loop Protection：相同错误连续出现
            result_loop = loop_guard.check_result(tc.name, result.ok, result.error)
            yield {
                "type": "tool_call",
                "id": call_id,
                "name": tc.name,
                "status": "done" if result.ok else "error",
                "step": step,
                "duration_ms": round(result.duration_ms, 1),
                "output": _display_output(result.output),
                "error": result.error if not result.ok else None,
                "permission": auth.get("permission"),
                "permission_result": "granted",
                "user_confirmation": "auto",
                "security": security,
                "metadata": metadata,
                **(
                    {"code": "LOOP_DETECTED", "loop_detected": True}
                    if result_loop
                    else {}
                ),
            }

            # 命令执行的审计事实（脱敏后写入安全日志）
            if (
                permission_manager is not None
                and isinstance(security, dict)
                and security.get("kind") == "command"
            ):
                permission_manager.log(
                    "command.executed" if result.ok else "command.failed",
                    tool=tc.name,
                    command=security.get("command") or "",
                    cwd=str(metadata.get("cwd") or security.get("cwd") or ""),
                    mode=str(security.get("mode") or ""),
                    risk=str(security.get("risk") or ""),
                    reason=str(security.get("reason") or ""),
                    confirmation_required=bool(security.get("requires_confirmation")),
                    user_decision=auth.get("user_confirmation") or "auto",
                    exit_code=metadata.get("exit_code"),
                    timed_out=bool(metadata.get("timed_out")),
                    run_id=run_id,
                )

            observation = result.to_observation()
            if result_loop:
                observation += (
                    f"\n\n[LOOP_DETECTED] {result_loop} "
                    "请停止重复相同操作，换一种方式重新规划后再试。"
                )
            context.add_tool_result(call_id, observation, pair_id=pair_id)

            if result_loop:
                exhausted = loop_guard.record_detection()
                yield {
                    "type": "loop_detected",
                    "code": "LOOP_DETECTED",
                    "reason": result_loop,
                    "tool": tc.name,
                    "step": step,
                    "detections": loop_guard.detections,
                }
                if exhausted:
                    yield {
                        "type": "error",
                        "code": "LOOP_DETECTED",
                        "error_type": "loop",
                        "message": (
                            "LOOP_DETECTED：检测到重复操作且无法恢复，"
                            f"Agent 已停止（{result_loop}）"
                        ),
                    }
                    return

        # 继续下一轮，让模型基于工具结果生成回答
