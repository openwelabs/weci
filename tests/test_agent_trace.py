"""Agent 执行过程（SSE 事件）测试。

覆盖：
- 工具调用参数 / 输出 / 耗时 / 步骤随事件推送
- Unlimited Execution：不设最大步数，可远超 10 / 50 / 100 次 Tool Call
- Loop Protection：连续相同调用 / 连续相同错误 → LOOP_DETECTED
- running → done / error 状态流转
- 超长工具输出截断
- 工具失败原因透传
"""

from __future__ import annotations

import asyncio

import backend.services.agent as agent_module
from backend.services.context import ContextManager
from backend.services.openai_client import ToolCall
from weci_sdk.tool import ToolExecutionResult


class FakeRegistry:
    """只实现 run_agent 需要的两个方法。"""

    def __init__(self, results: dict[str, ToolExecutionResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, str]] = []

    def schemas(self):
        return []

    async def execute(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        return self.results.get(
            name,
            ToolExecutionResult(ok=True, output=f"{name} ok", duration_ms=1.5),
        )


def _provider(rounds: list[list[dict]]) -> object:
    """按调用轮次依次产出预设事件。"""

    state = {"index": 0}

    async def fake_stream(_messages, **_kwargs):
        index = state["index"]
        state["index"] += 1
        for event in rounds[index]:
            yield event

    return fake_stream


def _run(monkeypatch, rounds, registry, cancel_event=None):
    monkeypatch.setattr(agent_module, "stream_chat_with_tools", _provider(rounds))
    context = ContextManager.from_openai_messages(
        [{"role": "user", "content": "hi"}],
        system_prompt=agent_module.AGENT_SYSTEM_PROMPT,
    )

    async def collect():
        events = []
        async for event in agent_module.run_agent(
            context,
            api_base="http://example.com/v1",
            api_key="",
            model="mock",
            temperature=0.7,
            max_tokens=None,
            registry=registry,
            cancel_event=cancel_event,
        ):
            events.append(event)
        return events

    return asyncio.run(collect())


def _tool_round(call_id: str, name: str = "noop", arguments: str = "{}") -> list[dict]:
    return [{"tool_calls": [ToolCall(id=call_id, name=name, arguments=arguments)]}]


def test_tool_call_events_include_arguments_output_and_steps(monkeypatch) -> None:
    registry = FakeRegistry(
        {
            "read_file": ToolExecutionResult(
                ok=True, output="README content", duration_ms=12.34
            )
        }
    )
    rounds = [
        [
            {
                "tool_calls": [
                    ToolCall(
                        id="call-1",
                        name="read_file",
                        arguments='{"path": "README.md"}',
                    )
                ]
            }
        ],
        [{"delta": "最终回答"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    types = [event["type"] for event in events]
    assert types == ["thinking", "tool_call", "tool_call", "thinking", "delta", "done"]

    thinking = events[0]
    assert thinking["step"] == 1
    assert "max_steps" not in thinking

    running = events[1]
    assert running["status"] == "running"
    assert running["step"] == 1
    assert "max_steps" not in running
    assert running["arguments"] == {"path": "README.md"}

    done = events[2]
    assert done["status"] == "done"
    assert done["output"] == "README content"
    assert done["duration_ms"] == 12.3
    assert done["error"] is None

    assert events[-1] == {"type": "done", "steps": 2}


def test_tool_error_event_exposes_reason(monkeypatch) -> None:
    registry = FakeRegistry(
        {
            "run_command": ToolExecutionResult(
                ok=False, output="", error="命令不存在", duration_ms=3
            )
        }
    )
    rounds = [
        _tool_round("c2", "run_command"),
        [{"delta": "工具失败了"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    finished = [
        e for e in events if e.get("type") == "tool_call" and e["status"] != "running"
    ][0]
    assert finished["status"] == "error"
    assert finished["error"] == "命令不存在"
    assert finished["output"] == ""


def test_long_tool_output_is_truncated(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "MAX_TOOL_DISPLAY_CHARS", 10)
    registry = FakeRegistry(
        {"big": ToolExecutionResult(ok=True, output="x" * 30, duration_ms=1)}
    )
    rounds = [_tool_round("c3", "big"), [{"delta": "ok"}]]

    events = _run(monkeypatch, rounds, registry)
    done = [e for e in events if e.get("type") == "tool_call" and e["status"] == "done"][0]
    assert done["output"].startswith("x" * 10)
    assert "输出过长" in done["output"]


def test_invalid_arguments_json_is_passed_through(monkeypatch) -> None:
    registry = FakeRegistry()
    rounds = [
        _tool_round("c4", "noop", "not-json"),
        [{"delta": "ok"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    running = [
        e for e in events if e.get("type") == "tool_call" and e["status"] == "running"
    ][0]
    assert running["arguments"] == "not-json"


def _unlimited_rounds(count: int) -> list[list[dict]]:
    """生成 count 次互不相同的工具调用，用于验证不存在固定步数上限。"""
    rounds = [
        _tool_round(f"call-{i}", "noop", f'{{"i": {i}}}') for i in range(count)
    ]
    rounds.append([{"delta": "全部完成"}])
    return rounds


def test_agent_can_exceed_ten_tool_calls(monkeypatch) -> None:
    registry = FakeRegistry()
    events = _run(monkeypatch, _unlimited_rounds(12), registry)
    assert len(registry.calls) == 12
    assert [e["type"] for e in events].count("tool_call") == 24
    assert events[-1] == {"type": "done", "steps": 13}


def test_agent_can_exceed_fifty_tool_calls(monkeypatch) -> None:
    registry = FakeRegistry()
    events = _run(monkeypatch, _unlimited_rounds(55), registry)
    assert len(registry.calls) == 55
    assert events[-1] == {"type": "done", "steps": 56}
    assert all(e.get("error_type") != "loop" for e in events)


def test_agent_can_exceed_hundred_tool_calls(monkeypatch) -> None:
    registry = FakeRegistry()
    events = _run(monkeypatch, _unlimited_rounds(120), registry)
    assert len(registry.calls) == 120
    assert events[-1] == {"type": "done", "steps": 121}


def test_repeated_identical_call_triggers_loop_detection(monkeypatch) -> None:
    """连续相同 Tool + 相同参数：第 3 次拦截并返回 LOOP_DETECTED。"""
    registry = FakeRegistry()
    rounds = [_tool_round(f"same-{i}") for i in range(3)]
    rounds.append([{"delta": "换一种方式"}])

    events = _run(monkeypatch, rounds, registry)
    # 前两次正常执行，第三次被 Loop Protection 拦截
    assert len(registry.calls) == 2

    loop_events = [e for e in events if e["type"] == "loop_detected"]
    assert len(loop_events) == 1
    assert loop_events[0]["code"] == "LOOP_DETECTED"
    assert loop_events[0]["detections"] == 1

    blocked = [
        e
        for e in events
        if e.get("type") == "tool_call" and e.get("code") == "LOOP_DETECTED"
    ]
    assert blocked and blocked[0]["status"] == "error"
    assert blocked[0]["error"].startswith("LOOP_DETECTED")
    # 触发后仍让模型继续，最终正常结束
    assert events[-1]["type"] == "done"


def test_repeated_identical_call_ends_run_when_unrecoverable(monkeypatch) -> None:
    """同一次 Run 内重复检测达到上限：结构化错误结束 Run。"""
    registry = FakeRegistry()
    rounds = [_tool_round(f"same-{i}") for i in range(4)]

    events = _run(monkeypatch, rounds, registry)
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "loop"
    assert events[-1]["code"] == "LOOP_DETECTED"
    assert "LOOP_DETECTED" in events[-1]["message"]


def test_repeated_identical_error_triggers_loop_detection(monkeypatch) -> None:
    registry = FakeRegistry(
        {
            "run_command": ToolExecutionResult(
                ok=False, output="", error="命令不存在", duration_ms=1
            )
        }
    )
    # 参数各不相同（绕开相同调用检测），但错误完全相同
    rounds = [
        _tool_round(f"cmd-{i}", "run_command", f'{{"command": "cmd-{i}"}}')
        for i in range(3)
    ]
    rounds.append([{"delta": "换一种方式"}])

    events = _run(monkeypatch, rounds, registry)
    loop_events = [e for e in events if e["type"] == "loop_detected"]
    assert len(loop_events) == 1
    assert "相同错误" in loop_events[0]["reason"]
    assert events[-1]["type"] == "done"


def test_cancel_stops_before_next_tool(monkeypatch) -> None:
    """取消后不再启动新的 Tool，事件以 cancelled 结束。"""
    registry = FakeRegistry()
    cancel_event = asyncio.Event()
    cancel_event.set()

    events = _run(
        monkeypatch,
        [_tool_round("c1"), [{"delta": "不会执行"}]],
        registry,
        cancel_event=cancel_event,
    )
    assert events[0]["type"] == "cancelled"
    assert registry.calls == []


def test_no_tool_call_keeps_chat_like_flow(monkeypatch) -> None:
    registry = FakeRegistry()
    events = _run(monkeypatch, [[{"delta": "直接回答"}]], registry)
    assert events == [
        {"type": "thinking", "step": 1},
        {"type": "delta", "text": "直接回答"},
        {"type": "done", "steps": 1},
    ]
    assert registry.calls == []
