"""统一 Context Manager 测试。

覆盖：
- Chat / Agent 共用的 Context 数据结构与 Provider 输入
- Workspace Context 注入，以及与附件内容的去重
- 重复附件只注入一次
- token 预算：保留最近消息 / 当前用户请求，压缩较旧内容
- 旧消息图片在超限时被省略
- Agent 工具调用与结果写回同一个 Context
"""

from __future__ import annotations

import asyncio
import base64

import backend.services.agent as agent_module
from backend.services.context import (
    ContextManager,
    ModelContext,
    WorkspaceContext,
    WorkspaceFile,
    estimate_text_tokens,
    resolve_context_window,
)
from backend.services.message import ChatMessage, FileAttachment, ImageAttachment
from backend.services.openai_client import ToolCall, resolve_messages
from weci_sdk.tool import ToolExecutionResult

PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _b64(raw: str | bytes) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return base64.b64encode(raw).decode()


def _file(name: str = "notes.txt", content: str = "hello file") -> FileAttachment:
    return FileAttachment(data=_b64(content), name=name)


def _image() -> ImageAttachment:
    return ImageAttachment(data=PNG_BASE64, mime_type="image/png", name="pixel.png")


def _user(content: str = "", *, files=None, images=None) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=content,
        files=files or [],
        attachments=images or [],
    )


def _compressing(messages: list[ChatMessage]) -> ContextManager:
    """预算很小、只保留最近 2 条的 Context，便于验证压缩。"""
    return ContextManager.from_chat_messages(
        messages,
        context_window=1_200,
        reserve_tokens=600,
        min_recent_messages=2,
    )


def _system(messages: list[dict]) -> str:
    assert messages and messages[0]["role"] == "system"
    return messages[0]["content"]


# ---------------------------------------------------------------------------
# 基础结构
# ---------------------------------------------------------------------------


def test_plain_chat_keeps_openai_compatible_shape() -> None:
    context = ContextManager.from_chat_messages(
        [_user("你好"), ChatMessage(role="assistant", content="你好！")]
    )
    built = context.build()

    assert built.to_openai_messages() == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    assert built.estimated_tokens > 0
    assert built.stats["compressed"] is False
    assert built.stats["dropped_messages"] == 0


def test_empty_context_is_detected() -> None:
    context = ContextManager.from_chat_messages([_user("   ")])
    assert context.has_messages() is False
    assert context.build().messages == []


def test_system_prompt_is_shared_and_not_duplicated() -> None:
    manager = ContextManager()
    manager.add_system("你是助手。")
    manager.add_system("你是助手。", key="system")
    built = manager.build()

    assert built.messages == [{"role": "system", "content": "你是助手。"}]


def test_provider_consumes_model_context() -> None:
    built = ContextManager.from_chat_messages([_user("hi")]).build()
    assert isinstance(built, ModelContext)
    assert resolve_messages(built) is built.messages


def test_token_estimate_and_model_window() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("你好") == 2
    assert resolve_context_window("gpt-3.5-turbo") == 16_384
    assert resolve_context_window("deepseek-chat") == 65_536
    assert resolve_context_window("some-local-model", 32_000) == 32_000


# ---------------------------------------------------------------------------
# Workspace / 附件去重
# ---------------------------------------------------------------------------


def test_workspace_context_is_injected_once() -> None:
    workspace = WorkspaceContext(
        name="weci",
        root="/home/web/repos/weci",
        instructions="回答使用中文。",
        files=[WorkspaceFile(name="README.md", content="项目说明")],
    )
    built = ContextManager.from_chat_messages(
        [_user("看看工作区")], workspace=workspace
    ).build()

    system = _system(built.messages)
    assert "<workspace" in system
    assert 'name="weci"' in system
    assert "回答使用中文。" in system
    assert "项目说明" in system
    assert system.count("<file") == 1


def test_workspace_content_duplicated_by_attachment_is_skipped() -> None:
    shared = "同一份文件内容"
    workspace = WorkspaceContext(
        name="ws",
        description="工作区说明",
        files=[WorkspaceFile(name="a.txt", content=shared)],
    )
    built = ContextManager.from_chat_messages(
        [_user("读取", files=[_file("a.txt", shared)])],
        workspace=workspace,
    ).build()

    system = _system(built.messages)
    assert "<file" not in system  # 工作区重复内容不再注入
    assert "工作区说明" in system
    joined = "\n".join(str(message["content"]) for message in built.messages)
    assert joined.count(shared) == 1
    assert built.duplicated_attachments == 1


def test_duplicate_attachments_across_turns_are_omitted() -> None:
    context = ContextManager.from_chat_messages(
        [
            _user("第一个文件", files=[_file("a.txt", "重复内容")]),
            ChatMessage(role="assistant", content="已读取"),
            _user("再看一次", files=[_file("a.txt", "重复内容")]),
        ]
    )
    built = context.build()
    joined = "\n".join(str(message["content"]) for message in built.messages)

    assert joined.count("重复内容") == 1
    assert "文件内容已省略" in joined
    assert built.duplicated_attachments == 1


# ---------------------------------------------------------------------------
# 预算 / 压缩
# ---------------------------------------------------------------------------


def test_budget_keeps_recent_and_current_request() -> None:
    messages: list[ChatMessage] = []
    for index in range(6):
        messages.append(_user(f"旧问题 {index}：" + "旧" * 200))
        messages.append(ChatMessage(role="assistant", content="旧回答 " + "答" * 200))
    messages.append(_user("当前问题"))

    built = _compressing(messages).build()

    assert built.stats["dropped_messages"] > 0
    assert built.compressed is True
    assert "上下文提示" in _system(built.messages)
    contents = [message["content"] for message in built.messages[1:]]
    assert contents[-1] == "当前问题"
    assert built.stats["dropped_messages"] >= 8
    assert all("旧问题 0" not in str(value) for value in contents)


def test_old_images_are_dropped_when_over_budget() -> None:
    built = _compressing(
        [
            _user("看这张图", images=[_image()]),
            ChatMessage(role="assistant", content="看到了"),
            _user("继续"),
        ]
    ).build()

    joined = str(built.messages)
    assert "image_url" not in joined
    assert "图片已从上下文中省略" in joined
    assert built.stats["compressed"] is True


def test_huge_current_message_is_shrunk_to_fit() -> None:
    built = _compressing([_user("当前：" + "长" * 4_000)]).build()

    assert built.estimated_tokens <= built.budget_tokens
    assert "已截断" in str(built.messages)


def test_tool_results_are_part_of_context_and_ordered() -> None:
    context = ContextManager.from_chat_messages([_user("算一下")])
    pair_id = context.add_tool_calls(
        [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "calc", "arguments": "{}"},
            }
        ]
    )
    context.add_tool_result("call-1", "结果是 42", pair_id=pair_id)

    built = context.build()
    assert [message["role"] for message in built.messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert built.messages[1]["tool_calls"][0]["id"] == "call-1"
    assert built.messages[2] == {
        "role": "tool",
        "content": "结果是 42",
        "tool_call_id": "call-1",
    }


# ---------------------------------------------------------------------------
# Agent 共用同一个 Context
# ---------------------------------------------------------------------------


class _Registry:
    def schemas(self):
        return []

    async def execute(self, _name, _arguments, **_kwargs):
        return ToolExecutionResult(ok=True, output="工具结果 42", duration_ms=1.0)


def test_agent_writes_tools_back_into_shared_context(monkeypatch) -> None:
    received: list[list[dict]] = []

    async def fake_stream(messages, **_kwargs):
        received.append(messages.to_openai_messages())
        if len(received) == 1:
            yield {
                "tool_calls": [
                    ToolCall(id="call-9", name="calc", arguments="{}"),
                ]
            }
        else:
            yield {"delta": "完成"}

    monkeypatch.setattr(agent_module, "stream_chat_with_tools", fake_stream)
    context = ContextManager.from_chat_messages(
        [_user("算一下")], system_prompt=agent_module.AGENT_SYSTEM_PROMPT
    )

    async def collect() -> list[dict]:
        return [
            event
            async for event in agent_module.run_agent(
                context,
                api_base="http://example.com/v1",
                api_key="",
                model="mock",
                temperature=0.0,
                max_tokens=None,
                registry=_Registry(),
            )
        ]

    events = asyncio.run(collect())

    assert events[-1]["type"] == "done"
    # Provider 收到的是 ModelContext 转换后的消息，且第二轮包含工具结果
    assert received[0][0]["role"] == "system"
    assert [message["role"] for message in received[1]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert received[1][-1]["content"] == "工具结果 42"
