"""Agent Run Manager 测试。

覆盖：
- Run 创建
- 状态机合法 / 非法转换
- Tool Call 记录（running → done / error）
- completed / failed / cancelled 终态
- SSE / 服务中断后残留 running 标记为 failed
- Provider 错误 / Tool 错误分类
- 持久化与恢复
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from backend.services.agent_run import (
    MAX_RUNS_PER_CONVERSATION,
    AgentRunManager,
)


@pytest.fixture()
def manager(tmp_path):
    path = tmp_path / "agent_runs.json"
    return AgentRunManager(config_path=path)


def _create(manager: AgentRunManager, conv: str = "conv-1", **kwargs):
    return manager.create(conversation_id=conv, **kwargs)


def test_create_run(manager):
    run = _create(manager)
    assert run.id.startswith("run-")
    assert run.conversation_id == "conv-1"
    assert run.status == "pending"
    assert run.started_at
    assert run.tool_calls == []
    assert run.events and run.events[0].type == "run.created"
    # 已持久化
    assert manager.config_path.exists()
    reloaded = AgentRunManager(config_path=manager.config_path)
    assert reloaded.get(run.id) is not None


def test_state_transitions(manager):
    run = _create(manager)
    assert manager.start(run.id) is True
    assert run.status == "running"
    assert run.events[-1].type == "run.started"

    # pending → running 之后不能再 start
    assert manager.start(run.id) is False
    assert run.status == "running"

    # 终态后不再允许转换
    assert manager.complete(run.id, result="ok", steps=1) is True
    assert run.status == "completed"
    assert manager.cancel(run.id) is False
    assert manager.fail(run.id, error="x") is False


def test_invalid_transition_completed_to_running(manager):
    run = _create(manager)
    manager.start(run.id)
    manager.complete(run.id, result="ok")
    assert run.status == "completed"
    # 已完成不能再转 running
    assert manager.start(run.id) is False


def test_tool_call_recording(manager):
    run = _create(manager)
    manager.start(run.id)
    manager.set_step(run.id, step=1, max_steps=10)
    assert run.current_step == 1

    manager.add_tool_call(
        run.id, call_id="call-1", name="calculator", step=1, arguments={"a": 1}
    )
    tc = run.tool_calls[-1]
    assert tc.status == "running"
    assert tc.name == "calculator"
    assert run.events[-1].type == "tool.called"

    manager.complete_tool_call(run.id, "call-1", output="2", duration_ms=3.0)
    assert tc.status == "done"
    assert tc.output == "2"
    assert tc.duration_ms == 3.0
    assert run.events[-1].type == "tool.completed"

    manager.add_tool_call(run.id, call_id="call-2", name="bad", step=2)
    manager.fail_tool_call(run.id, "call-2", error="boom", duration_ms=1.0)
    bad = run.tool_calls[-1]
    assert bad.status == "error"
    assert bad.error == "boom"
    assert run.events[-1].type == "tool.failed"


def test_completed(manager):
    run = _create(manager)
    manager.start(run.id)
    manager.append_result(run.id, "hello ")
    manager.append_result(run.id, "world")
    assert manager.complete(run.id, steps=3) is True
    assert run.status == "completed"
    assert run.result == "hello world"
    assert run.steps == 3
    assert run.finished_at
    assert run.events[-1].type == "run.completed"
    assert run.public_dict()["tool_count"] == 0
    assert run.public_dict()["result_summary"] == "hello world"


def test_failed(manager):
    run = _create(manager)
    manager.start(run.id)
    assert manager.fail(run.id, error="boom", error_type="provider") is True
    assert run.status == "failed"
    assert run.error_type == "provider"
    assert run.error == "boom"
    assert run.events[-1].type == "run.failed"


def test_cancelled(manager):
    run = _create(manager)
    manager.start(run.id)
    # 设置取消事件前未取消
    assert manager.is_cancelled(run.id) is False
    assert manager.cancel(run.id) is True
    assert run.status == "cancelled"
    assert run.error_type == "cancelled"
    assert manager.is_cancelled(run.id) is True
    # 再次取消返回 False（已终态）
    assert manager.cancel(run.id) is False
    assert run.events[-1].type == "run.cancelled"


def test_cancel_signal_wakes_loop(manager):
    run = _create(manager)
    manager.start(run.id)
    event = manager.cancel_event(run.id)

    async def wait_then_cancel():
        await asyncio.sleep(0.01)
        manager.cancel(run.id)

    async def main():
        await asyncio.gather(event.wait(), wait_then_cancel())
        return event.is_set()

    assert asyncio.run(main()) is True


def test_sse_interruption_marks_running_as_failed(manager):
    """模拟前端 SSE 断开 / 服务重启后，残留 running 的 Run 被标记为 failed。"""
    run = _create(manager)
    manager.start(run.id)
    assert run.status == "running"
    # 直接写入磁盘后重新加载（模拟服务重启）
    raw = json.loads(manager.config_path.read_text(encoding="utf-8"))
    assert raw["conversations"]["conv-1"][0]["status"] == "running"

    reloaded = AgentRunManager(config_path=manager.config_path)
    restored = reloaded.get(run.id)
    assert restored is not None
    assert restored.status == "failed"
    assert restored.error_type == "network"


def test_provider_error_type(manager):
    run = _create(manager)
    manager.start(run.id)
    manager.fail(run.id, error="429 Too Many Requests", error_type="provider")
    assert run.error_type == "provider"
    # 未知错误类型归一化为 unknown
    manager2 = AgentRunManager(config_path=manager.config_path.parent / "r2.json")
    r2 = _create(manager2)
    manager2.start(r2.id)
    manager2.fail(r2.id, error="x", error_type="definitely_not_a_type")
    assert r2.error_type == "unknown"


def test_tool_error_is_tool_call_level(manager):
    run = _create(manager)
    manager.start(run.id)
    manager.add_tool_call(run.id, call_id="c1", name="web_search", step=1)
    manager.fail_tool_call(run.id, "c1", error="网络超时", duration_ms=200)
    # 工具失败不自动终结 Run（由 Agent Loop 决定是否继续）
    assert run.status == "running"
    assert run.tool_calls[-1].status == "error"


def test_list_by_conversation_and_limit(manager):
    conv = "c-many"
    ids = []
    for i in range(5):
        r = manager.create(conversation_id=conv)
        ids.append(r.id)
        time.sleep(0.001)  # 确保 started_at 有差异
    runs = manager.list_by_conversation(conv, limit=3)
    assert len(runs) == 3
    # 最新在前
    assert runs[0].id == ids[-1]
    assert manager.get_current(conv) is not None  # 仍有 pending
    manager.start(runs[0].id)
    manager.complete(runs[0].id)
    # 仍有 4 个 pending → current 存在
    assert manager.get_current(conv) is not None


def test_max_runs_per_conversation_trims(manager):
    conv = "c-trim"
    for i in range(MAX_RUNS_PER_CONVERSATION + 5):
        manager.create(conversation_id=conv)
    ids = manager._by_conversation[conv]
    assert len(ids) == MAX_RUNS_PER_CONVERSATION
    assert len(manager._runs) == MAX_RUNS_PER_CONVERSATION


def test_persistence_recovery(manager):
    run = _create(manager, model="gpt-test", provider_id="p1", max_steps=7)
    manager.start(run.id)
    manager.set_step(run.id, step=2, max_steps=7)
    manager.add_tool_call(run.id, call_id="t1", name="calc", step=2, arguments={"x": 1})
    manager.complete_tool_call(run.id, "t1", output="2", duration_ms=1.2)
    manager.complete(run.id, result="done", steps=2)

    reloaded = AgentRunManager(config_path=manager.config_path)
    restored = reloaded.get(run.id)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.model == "gpt-test"
    assert restored.provider_id == "p1"
    assert restored.max_steps == 7
    assert restored.steps == 2
    assert restored.result == "done"
    assert len(restored.tool_calls) == 1
    assert restored.tool_calls[0].name == "calc"
    assert restored.tool_calls[0].output == "2"
    # 事件序列完整保留
    event_types = [e.type for e in restored.events]
    assert event_types[0] == "run.created"
    assert event_types[-1] == "run.completed"
    assert "tool.called" in event_types
    assert "tool.completed" in event_types


def test_public_dict_no_sensitive_and_summary(manager):
    run = _create(manager, model="m", provider_id="p")
    manager.start(run.id)
    manager.append_result(run.id, "first line\nsecond line")
    manager.complete(run.id)
    data = run.public_dict()
    assert data["result_summary"] == "first line"
    assert data["tool_count"] == 0
    assert isinstance(data["duration_ms"], (int, float))
    # 不应包含 API Key 等敏感字段（结构中本身没有，但确认字段白名单）
    for key in data:
        assert "key" not in key.lower() or key == "provider_id"
