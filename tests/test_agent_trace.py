"""Agent 执行过程（SSE 事件）测试。

覆盖：
- 工具调用参数 / 输出 / 耗时 / 步骤随事件推送
- running → done / error 状态流转
- 超长工具输出截断
- 达到最大步数时的事件序列
- 工具失败原因透传
"""

from __future__ import annotations

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


def _run(monkeypatch, rounds, registry, max_steps: int = 10):
    monkeypatch.setattr(agent_module, "stream_chat_with_tools", _provider(rounds))
    monkeypatch.setattr(agent_module, "MAX_AGENT_STEPS", max_steps)
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
        ):
            events.append(event)
        return events

    import asyncio

    return asyncio.run(collect())


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
    assert thinking["step"] == 1 and thinking["max_steps"] == 10

    running = events[1]
    assert running["status"] == "running"
    assert running["step"] == 1
    assert running["max_steps"] == 10
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
        [{"tool_calls": [ToolCall(id="c2", name="run_command", arguments="{}")]}],
        [{"delta": "工具失败了"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    finished = [e for e in events if e.get("type") == "tool_call" and e["status"] != "running"][0]
    assert finished["status"] == "error"
    assert finished["error"] == "命令不存在"
    assert finished["output"] == ""


def test_long_tool_output_is_truncated(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "MAX_TOOL_DISPLAY_CHARS", 10)
    registry = FakeRegistry(
        {"big": ToolExecutionResult(ok=True, output="x" * 30, duration_ms=1)}
    )
    rounds = [
        [{"tool_calls": [ToolCall(id="c3", name="big", arguments="{}")]}],
        [{"delta": "ok"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    done = [e for e in events if e.get("type") == "tool_call" and e["status"] == "done"][0]
    assert done["output"].startswith("x" * 10)
    assert "输出过长" in done["output"]


def test_invalid_arguments_json_is_passed_through(monkeypatch) -> None:
    registry = FakeRegistry()
    rounds = [
        [{"tool_calls": [ToolCall(id="c4", name="noop", arguments="not-json")]}],
        [{"delta": "ok"}],
    ]

    events = _run(monkeypatch, rounds, registry)
    running = [e for e in events if e.get("type") == "tool_call" and e["status"] == "running"][0]
    assert running["arguments"] == "not-json"


def test_max_steps_event_sequence(monkeypatch) -> None:
    registry = FakeRegistry()

    def tool_round(call_id: str):
        return [{"tool_calls": [ToolCall(id=call_id, name="noop", arguments="{}")]}]

    rounds = [tool_round("a"), tool_round("b")]
    events = _run(monkeypatch, rounds, registry, max_steps=2)

    assert [e["type"] for e in events].count("tool_call") == 4
    assert events[-2]["type"] == "max_steps"
    assert events[-2]["steps"] == 2
    assert events[-1] == {"type": "done", "steps": 2}


def test_no_tool_call_keeps_chat_like_flow(monkeypatch) -> None:
    registry = FakeRegistry()
    events = _run(monkeypatch, [[{"delta": "直接回答"}]], registry)
    assert events == [
        {"type": "thinking", "step": 1, "max_steps": 10},
        {"type": "delta", "text": "直接回答"},
        {"type": "done", "steps": 1},
    ]
    assert registry.calls == []
