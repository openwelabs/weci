"""统一上下文管理（Context Manager）。

发送给模型的所有内容只有一条链路，Chat 与 Agent 共用本模块：

    Conversation + Workspace + Attachment + Tool Result
        ↓  ContextManager（本模块）
    ModelContext（统一的 Context 数据结构 + token 预算）
        ↓  Provider（openai_client）
    Model

职责：
  - 把领域消息（``ChatMessage``）转换为 OpenAI-compatible 的 Context
  - 管理 Workspace 文本、图片 / 文件附件、Agent 工具调用与结果
  - 按模型上下文上限计算可用 token，超限时压缩较早内容
  - 附件与 Workspace 内容去重（同一份内容只注入一次）

优先级（预算不足时从低到高淘汰）：

    System Context
    → 当前用户请求
    → 最近消息
    → 必要的 Tool Results
    → 较旧消息

第一阶段不做长期 Memory、RAG 或向量数据库；压缩只做确定性的
截断 / 省略，不额外调用模型生成摘要。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field

from backend.config import settings
from backend.services.memory import MemoryEntry
from backend.services.message import (
    ChatMessage,
    format_file_block,
    validate_message,
)

# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------
# OpenAI-compatible 服务没有统一的 tokenizer。这里用保守的启发式估算：
# 中日韩字符按 1 token / 字，其余字符按 4 字符 / token。
_CJK_RE = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff]")

# 每条消息在请求中的固定开销
_MESSAGE_OVERHEAD_TOKENS = 4

# 较旧消息在压缩时允许保留的 token 上限
_OLD_ITEM_TOKEN_CAP = 512

_TRUNCATED_MARK = "…[已截断]"

# 已知模型的上下文窗口上限；实际窗口取配置值与这里的较小值
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-3.5", 16_384),
    ("llama3.1", 131_072),
    ("llama3", 8_192),
    ("deepseek", 65_536),
    ("qwen", 131_072),
    ("claude", 200_000),
    ("gemini", 1_000_000),
)


def estimate_text_tokens(text: str) -> int:
    """粗略估算一段文本的 token 数（保守偏高）。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def resolve_context_window(model: str, configured: int | None = None) -> int:
    """根据模型名解析上下文窗口。

    ``configured`` 是配置或请求给出的上限；若模型有更保守的已知窗口，
    取两者较小值，避免超出模型能力。
    """
    base = configured or settings.context_window_tokens
    name = (model or "").lower()
    for keyword, limit in _MODEL_CONTEXT_WINDOWS:
        if keyword in name:
            return min(base, limit)
    return base


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _truncate_text(text: str, max_tokens: int) -> str:
    """把文本截断到大约 ``max_tokens`` 个 token。"""
    if not text:
        return text
    if max_tokens <= 0:
        return ""
    if estimate_text_tokens(text) <= max_tokens:
        return text

    marker_tokens = estimate_text_tokens(_TRUNCATED_MARK)
    if max_tokens <= marker_tokens:
        return _TRUNCATED_MARK

    budget = max_tokens - marker_tokens
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_text_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + _TRUNCATED_MARK


def _content_tokens(content: Any, image_tokens: int) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                total += image_tokens
            else:
                total += estimate_text_tokens(str(part.get("text", "")))
        return total
    return estimate_text_tokens(str(content))


# ---------------------------------------------------------------------------
# Workspace Context
# ---------------------------------------------------------------------------
class WorkspaceFile(BaseModel):
    """工作区中的一个文本文件（内容直接作为上下文注入）。"""

    name: str = Field(min_length=1, max_length=255)
    content: str = ""
    mime_type: str | None = None


class WorkspaceContext(BaseModel):
    """当前 Workspace 的上下文。

    第一阶段只承载“工作区说明 + 指定文件文本”，不做文件同步、索引或
    向量化。内容会作为 System Context 注入，并与消息附件去重。
    """

    name: str | None = Field(default=None, max_length=255)
    root: str | None = Field(default=None, max_length=1024)
    description: str | None = None
    instructions: str | None = None
    files: list[WorkspaceFile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------
@dataclass
class _ContextItem:
    """Context 中的一条消息（可能来自对话、附件或工具调用）。"""

    role: str
    content: Any = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    kind: str = "message"
    pair_id: str | None = None
    order: int = 0

    def to_openai(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            message["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        return message


@dataclass
class ModelContext:
    """Provider 可以直接使用的最终 Context。"""

    messages: list[dict[str, Any]]
    estimated_tokens: int
    budget_tokens: int
    context_window: int
    reserve_tokens: int
    dropped_messages: int = 0
    compressed: bool = False
    duplicated_attachments: int = 0
    # 本次注入的长期记忆（Memory）条数
    memory_count: int = 0

    def to_openai_messages(self) -> list[dict[str, Any]]:
        return self.messages

    @property
    def stats(self) -> dict[str, Any]:
        """可安全暴露给前端 / 日志的上下文摘要。"""
        return {
            "estimated_tokens": self.estimated_tokens,
            "budget_tokens": self.budget_tokens,
            "context_window": self.context_window,
            "reserve_tokens": self.reserve_tokens,
            "dropped_messages": self.dropped_messages,
            "compressed": self.compressed,
            "duplicated_attachments": self.duplicated_attachments,
            "memory_count": self.memory_count,
        }


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------
class ContextManager:
    """统一收集并生成模型上下文。

    Chat 与 Agent 都只通过本类构造请求。Agent 在每轮工具执行后把
    tool_calls / tool 结果写回，随后重新 ``build()``，预算与压缩逻辑
    与普通 Chat 完全一致。
    """

    def __init__(
        self,
        *,
        context_window: int | None = None,
        reserve_tokens: int = 0,
        min_recent_messages: int | None = None,
        image_tokens: int | None = None,
        workspace_max_chars: int | None = None,
    ) -> None:
        self._context_window = max(
            1024, int(context_window or settings.context_window_tokens)
        )
        self._reserve_tokens = max(0, int(reserve_tokens or 0))
        self._min_recent = max(
            1,
            int(
                min_recent_messages
                if min_recent_messages is not None
                else settings.context_min_recent_messages
            ),
        )
        self._image_tokens = max(
            1, int(image_tokens or settings.context_image_tokens)
        )
        self._workspace_max_chars = max(
            256,
            int(
                workspace_max_chars
                if workspace_max_chars is not None
                else settings.context_workspace_max_chars
            ),
        )

        self._system: list[tuple[str, str]] = []
        self._system_keys: set[str] = set()
        self._system_hashes: set[str] = set()
        self._items: list[_ContextItem] = []
        self._order = 0
        # 已注入过的附件 / Workspace 内容摘要，用于去重
        self._seen_content: set[str] = set()
        self._duplicates = 0
        self._memory_count = 0

    # ------------------------------------------------------------------
    # 构建入口
    # ------------------------------------------------------------------
    @classmethod
    def from_chat_messages(
        cls,
        messages: Iterable[ChatMessage],
        *,
        system_prompt: str | None = None,
        workspace: WorkspaceContext | None = None,
        memories: Iterable[MemoryEntry] | None = None,
        model: str = "",
        context_window: int | None = None,
        reserve_tokens: int = 0,
        min_recent_messages: int | None = None,
    ) -> "ContextManager":
        """从领域消息构建 Context（Chat 与 Agent 的统一入口）。"""
        manager = cls(
            context_window=resolve_context_window(model, context_window),
            reserve_tokens=reserve_tokens,
            min_recent_messages=min_recent_messages,
        )
        if system_prompt:
            manager.add_system(system_prompt, key="system")
        for message in messages:
            manager.add_chat_message(message)
        if workspace is not None:
            manager.add_workspace(workspace)
        if memories:
            manager.add_memories(memories)
        return manager

    @classmethod
    def from_openai_messages(
        cls,
        messages: Iterable[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        context_window: int | None = None,
        reserve_tokens: int = 0,
        min_recent_messages: int | None = None,
    ) -> "ContextManager":
        """从已是 OpenAI 格式的消息构建 Context（测试与内部复用）。"""
        manager = cls(
            context_window=context_window,
            reserve_tokens=reserve_tokens,
            min_recent_messages=min_recent_messages,
        )
        if system_prompt:
            manager.add_system(system_prompt, key="system")
        pending_pair: str | None = None
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "")
            content = raw.get("content")
            if role == "system":
                manager.add_system(str(content or ""))
                pending_pair = None
            elif role == "tool":
                manager._append(  # noqa: SLF001 — 同类内部构造
                    _ContextItem(
                        role="tool",
                        content=content or "",
                        tool_call_id=raw.get("tool_call_id"),
                        kind="tool",
                        pair_id=pending_pair,
                    )
                )
            elif role == "assistant" and raw.get("tool_calls"):
                pending_pair = manager.add_tool_calls(raw["tool_calls"])
            else:
                pending_pair = None
                manager._append(
                    _ContextItem(role=role or "user", content=content or "")
                )
        return manager

    # ------------------------------------------------------------------
    # 写入 Context
    # ------------------------------------------------------------------
    def add_system(self, text: str, *, key: str | None = None) -> None:
        value = (text or "").strip()
        if not value:
            return
        content_hash = _digest(value)
        dedupe_key = key or f"system:{content_hash}"
        if dedupe_key in self._system_keys or content_hash in self._system_hashes:
            return
        self._system_keys.add(dedupe_key)
        self._system_hashes.add(content_hash)
        self._system.append((dedupe_key, value))

    def add_chat_message(self, message: ChatMessage) -> bool:
        """加入一条领域消息；附件在这里完成校验、去重与格式转换。"""
        text, images, files = validate_message(message)

        if message.role == "system":
            self.add_system(text)
            return bool(text)

        if not text and not images and not files:
            return False

        notes: list[str] = []
        image_parts: list[dict[str, Any]] = []
        for image in images:
            digest = "image:" + _digest(image.data)
            if digest in self._seen_content:
                self._duplicates += 1
                notes.append(
                    f"[图片已省略：与先前附件内容重复（{image.name or 'image'}）]"
                )
                continue
            self._seen_content.add(digest)
            image_parts.append(
                {"type": "image_url", "image_url": {"url": image.data_url}}
            )

        file_parts: list[dict[str, Any]] = []
        for index, file in enumerate(files):
            digest = "file:" + _digest(file.text)
            if digest in self._seen_content:
                self._duplicates += 1
                notes.append(f"[文件内容已省略：与先前附件内容重复（{file.name}）]")
                continue
            self._seen_content.add(digest)
            file_parts.append(
                {
                    "type": "text",
                    "text": format_file_block(file, index=index),
                }
            )

        combined = "\n".join(part for part in [text, *notes] if part)
        if image_parts or file_parts:
            content: Any = []
            if combined:
                content.append({"type": "text", "text": combined})
            content.extend(image_parts)
            content.extend(file_parts)
        else:
            content = combined

        self._append(_ContextItem(role=message.role, content=content))
        return True

    def add_workspace(self, workspace: WorkspaceContext) -> None:
        """把 Workspace 作为 System Context 注入；重复内容只注入一次。"""
        attrs: list[str] = []
        if workspace.name:
            attrs.append(f'name="{_xml_attr(workspace.name)}"')
        if workspace.root:
            attrs.append(f'root="{_xml_attr(workspace.root)}"')
        opening = f"<workspace {' '.join(attrs)}>" if attrs else "<workspace>"

        blocks: list[str] = [opening]
        if workspace.description:
            blocks.append(f"<description>\n{workspace.description.strip()}\n</description>")
        if workspace.instructions:
            blocks.append(
                f"<instructions>\n{workspace.instructions.strip()}\n</instructions>"
            )

        injected = 0
        for file in workspace.files:
            content = (file.content or "").strip()
            if not content:
                continue
            digest = "file:" + _digest(content)
            if digest in self._seen_content:
                self._duplicates += 1
                continue
            self._seen_content.add(digest)
            injected += 1
            blocks.append(
                f'<file index="{injected}" name="{_xml_attr(file.name)}"'
                f' type="{_xml_attr(file.mime_type or "text/plain")}">\n'
                f"{content}\n</file>"
            )
        blocks.append("</workspace>")

        if injected == 0 and not workspace.description and not workspace.instructions:
            return

        rendered = "\n".join(blocks)
        if len(rendered) > self._workspace_max_chars:
            rendered = (
                rendered[: self._workspace_max_chars]
                + "\n…（工作区内容过长，已截断）\n</workspace>"
            )
        self.add_system(rendered, key="workspace")

    def add_memories(
        self,
        memories: Iterable[MemoryEntry],
        *,
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> int:
        """把长期记忆（Memory）作为 System Context 注入。

        Memory 与 Conversation History、Workspace 文件相互独立：
          - 只有用户明确要求记住的内容才会出现在这里
          - 受条数与总字符数双重上限约束，避免无限增长
          - 同一 scope 下重复 key 只注入一次
        """
        limit_items = max(
            1,
            int(
                max_items
                if max_items is not None
                else settings.context_memory_max_items
            ),
        )
        limit_chars = max(
            128,
            int(
                max_chars
                if max_chars is not None
                else settings.context_memory_max_chars
            ),
        )

        header = (
            "<memories note=\"用户明确要求记住的长期信息；"
            "仅在与当前问题相关时使用，不要臆造，也不要把它当作聊天记录\">"
        )
        footer = "</memories>"
        # 预留 header / footer 的字符预算
        remaining = limit_chars - len(header) - len(footer) - 16
        blocks: list[str] = []
        seen: set[tuple[str, str]] = set()
        for entry in memories:
            if len(blocks) >= limit_items or remaining <= 0:
                break
            marker = (entry.scope, entry.key)
            if marker in seen:
                continue
            content = (entry.content or "").strip()
            if not content:
                continue
            seen.add(marker)

            prefix = f'<memory scope="{_xml_attr(entry.scope)}" key="{_xml_attr(entry.key)}">'
            suffix = "</memory>"
            allowance = remaining - len(prefix) - len(suffix) - 1
            if allowance <= 0:
                break
            if len(content) > allowance:
                content = content[: max(1, allowance - 1)] + "…"
            block = f"{prefix}{_xml_text(content)}{suffix}"
            blocks.append(block)
            remaining -= len(block) + 1

        if not blocks:
            return 0
        self.add_system("\n".join([header, *blocks, footer]), key="memories")
        self._memory_count = len(blocks)
        return self._memory_count

    def add_tool_calls(self, tool_calls: Iterable[dict[str, Any]]) -> str:
        """加入一条带 tool_calls 的 assistant 消息，返回配对 id。"""
        calls = [dict(call) for call in tool_calls if isinstance(call, dict)]
        if not calls:
            return ""
        self._order += 1
        pair_id = f"tool-pair-{self._order}"
        self._append(
            _ContextItem(
                role="assistant",
                content=None,
                tool_calls=calls,
                kind="tool_call",
                pair_id=pair_id,
            )
        )
        return pair_id

    def add_tool_result(
        self,
        call_id: str,
        content: str,
        *,
        pair_id: str | None = None,
    ) -> None:
        """加入工具返回结果（tool 角色），供下一轮模型读取。"""
        self._append(
            _ContextItem(
                role="tool",
                content=content or "",
                tool_call_id=call_id,
                kind="tool",
                pair_id=pair_id,
            )
        )

    def has_messages(self) -> bool:
        return bool(self._items)

    @property
    def duplicated_attachments(self) -> int:
        return self._duplicates

    @property
    def memory_count(self) -> int:
        return self._memory_count

    # ------------------------------------------------------------------
    # 生成最终 Context
    # ------------------------------------------------------------------
    def build(self) -> ModelContext:
        window = self._context_window
        reserve = self._reserve_tokens or settings.context_reserve_tokens
        reserve = min(max(0, int(reserve)), window // 2)
        budget = max(256, window - reserve)

        system_text = "\n\n".join(text for _, text in self._system).strip()
        items = [_copy_item(item) for item in self._items]
        positions = {id(item): index for index, item in enumerate(items)}
        compressed = False
        dropped = 0

        current_user = max(
            (index for index, item in enumerate(items) if item.role == "user"),
            default=None,
        )
        recent_start = max(0, len(items) - self._min_recent)
        keep_index = (
            recent_start if current_user is None else min(recent_start, current_user)
        )

        # 1) 较旧消息：先去掉图片、截断过长内容（附件 / 工具结果）
        for index, item in enumerate(items):
            if index >= keep_index or index == current_user:
                continue
            if _drop_images(item):
                compressed = True
            if _shrink_item(item, _OLD_ITEM_TOKEN_CAP, self._image_tokens):
                compressed = True

        # 2) 按优先级丢弃“较旧消息”，再考虑较早的工具结果
        units = _group_units(items)
        unit_start = {id(unit): positions[id(unit[0])] for unit in units}

        def priority(unit: list[_ContextItem]) -> int:
            if current_user is not None and any(
                positions[id(item)] == current_user for item in unit
            ):
                return 1  # 当前用户请求
            if unit_start[id(unit)] >= keep_index:
                return 1  # 最近消息
            if all(
                item.role == "tool"
                or (item.role == "assistant" and item.tool_calls)
                for item in unit
            ):
                return 2  # 必要的 Tool Results
            return 3  # 较旧消息

        priorities = {id(unit): priority(unit) for unit in units}
        kept = list(units)

        def kept_items() -> list[_ContextItem]:
            return [item for unit in units if id(unit) in {id(u) for u in kept} for item in unit]

        def total_tokens() -> int:
            base = (
                estimate_text_tokens(system_text) + _MESSAGE_OVERHEAD_TOKENS
                if system_text
                else 0
            )
            return base + sum(
                _item_tokens(item, self._image_tokens) for item in kept_items()
            )

        total = total_tokens()
        drop_candidates = sorted(
            (unit for unit in units if priorities[id(unit)] >= 2),
            key=lambda unit: (-priorities[id(unit)], unit_start[id(unit)]),
        )
        for unit in drop_candidates:
            if total <= budget:
                break
            total -= sum(_item_tokens(item, self._image_tokens) for item in unit)
            kept.remove(unit)
            dropped += len(unit)
            compressed = True

        # 3) 仍需压缩：保留最近消息，但缩短其内容
        if compressed:
            note = (
                "[上下文提示] 为适配模型的上下文限制"
                f"（约 {window:,} tokens），已压缩较早的对话内容"
                + (f"（省略 {dropped} 条消息）" if dropped else "")
                + "。"
            )
            system_text = f"{system_text}\n\n{note}".strip()
            total = total_tokens()

        shrinkable = sorted(
            (
                item
                for unit in units
                if id(unit) in {id(u) for u in kept} and priorities[id(unit)] == 1
                for item in unit
                if current_user is None or positions[id(item)] != current_user
            ),
            key=lambda item: positions[id(item)],
        )
        for item in shrinkable:
            if total <= budget:
                break
            before = _item_tokens(item, self._image_tokens)
            target = max(16, before - (total - budget))
            if _shrink_item(item, target, self._image_tokens):
                total -= before - _item_tokens(item, self._image_tokens)
                compressed = True

        # 4) 最后才动当前用户请求与 System Context
        if total > budget and current_user is not None:
            item = items[current_user]
            before = _item_tokens(item, self._image_tokens)
            target = max(32, before - (total - budget))
            if _shrink_item(item, target, self._image_tokens):
                total -= before - _item_tokens(item, self._image_tokens)
                compressed = True

        if total > budget and system_text:
            before = estimate_text_tokens(system_text)
            system_text = _truncate_text(system_text, max(16, before - (total - budget)))
            total = total_tokens()
            compressed = True

        output = kept_items()
        messages: list[dict[str, Any]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.extend(item.to_openai() for item in output)

        estimated = (
            (estimate_text_tokens(system_text) + _MESSAGE_OVERHEAD_TOKENS if system_text else 0)
            + sum(_item_tokens(item, self._image_tokens) for item in output)
        )
        return ModelContext(
            messages=messages,
            estimated_tokens=estimated,
            budget_tokens=budget,
            context_window=window,
            reserve_tokens=reserve,
            dropped_messages=dropped,
            compressed=compressed,
            duplicated_attachments=self._duplicates,
            memory_count=self._memory_count,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _append(self, item: _ContextItem) -> None:
        self._order += 1
        item.order = self._order
        self._items.append(item)


# ---------------------------------------------------------------------------
# 压缩 / 分组工具
# ---------------------------------------------------------------------------
def _copy_item(item: _ContextItem) -> _ContextItem:
    content = item.content
    if isinstance(content, list):
        content = [dict(part) if isinstance(part, dict) else part for part in content]
    return _ContextItem(
        role=item.role,
        content=content,
        tool_calls=item.tool_calls,
        tool_call_id=item.tool_call_id,
        kind=item.kind,
        pair_id=item.pair_id,
        order=item.order,
    )


def _item_tokens(item: _ContextItem, image_tokens: int) -> int:
    total = _MESSAGE_OVERHEAD_TOKENS + _content_tokens(item.content, image_tokens)
    if item.tool_calls:
        total += estimate_text_tokens(json.dumps(item.tool_calls, ensure_ascii=False))
    return total


def _group_units(items: list[_ContextItem]) -> list[list[_ContextItem]]:
    """把 assistant tool_calls 与其后的 tool 结果合并为一个不可拆分的单元。"""
    units: list[list[_ContextItem]] = []
    index = 0
    while index < len(items):
        item = items[index]
        if item.role == "assistant" and item.tool_calls:
            unit = [item]
            index += 1
            while index < len(items) and items[index].role == "tool":
                unit.append(items[index])
                index += 1
            units.append(unit)
        else:
            units.append([item])
            index += 1
    return units


def _drop_images(item: _ContextItem) -> bool:
    content = item.content
    if not isinstance(content, list):
        return False
    if not any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    ):
        return False
    kept = [
        part
        for part in content
        if not (isinstance(part, dict) and part.get("type") == "image_url")
    ]
    kept.append({"type": "text", "text": "[图片已从上下文中省略]"})
    item.content = kept
    return True


def _shrink_item(item: _ContextItem, max_tokens: int, image_tokens: int) -> bool:
    """截断一条消息的内容，使其接近 ``max_tokens``。返回是否发生变化。"""
    if max_tokens < 1:
        max_tokens = 1
    if _item_tokens(item, image_tokens) <= max_tokens:
        return False

    content = item.content
    if isinstance(content, list):
        parts = content
        if any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in parts
        ):
            parts = [
                part
                for part in parts
                if not (isinstance(part, dict) and part.get("type") == "image_url")
            ]
            parts.append({"type": "text", "text": "[图片已从上下文中省略]"})
            item.content = parts
            if _item_tokens(item, image_tokens) <= max_tokens:
                return True

        text_parts = [
            part
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        if not text_parts:
            return True

        text_tokens = sum(
            estimate_text_tokens(str(part.get("text", ""))) for part in text_parts
        )
        fixed = _item_tokens(item, image_tokens) - text_tokens
        allowance = max(1, max_tokens - fixed)
        for part in text_parts:
            current = estimate_text_tokens(str(part.get("text", "")))
            share = (
                max(1, int(allowance * current / text_tokens)) if text_tokens else 1
            )
            part["text"] = _truncate_text(str(part.get("text", "")), share)
        return True

    if isinstance(content, str):
        item.content = _truncate_text(
            content, max(1, max_tokens - _MESSAGE_OVERHEAD_TOKENS)
        )
        return True

    # tool_calls 的 arguments 不做截断，避免模型看到残缺的调用记录
    return False


def _xml_attr(value: str) -> str:
    return str(value).replace('"', "'").replace("\n", " ")


def _xml_text(value: str) -> str:
    """转义记忆内容，避免破坏上下文中的标签结构。"""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


__all__ = [
    "ContextManager",
    "ModelContext",
    "WorkspaceContext",
    "WorkspaceFile",
    "estimate_text_tokens",
    "resolve_context_window",
]
