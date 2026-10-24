"""长期记忆（Memory）基础设施。

第一阶段只做一条最小链路：用户明确要求记住 → 保存 → 之后的对话由
Context Manager 自动提供 → 用户可以查看 / 修改 / 删除。

设计要点：
  - Memory 与 Conversation History 分离：聊天记录仍在浏览器 localStorage，
    Memory 单独存储在 ``.weci/memory.json``（JSON 文件，不引入数据库）。
  - Memory 与 Workspace 文件分离：Workspace 文件是“当前上下文”，
    Memory 是用户主动要求长期保留的少量事实。
  - 按 scope 隔离：``user`` / ``workspace`` / ``conversation``，
    每个 scope 再以 ``scope_id`` 区分具体实例。
  - 默认不自动无限增长：每个 scope 有条数上限，单条内容有长度上限；
    不会自动分析聊天内容生成 Memory。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Literal

from pydantic import BaseModel

from backend.config import PROJECT_DIR, settings

MemoryScope = Literal["user", "workspace", "conversation"]
MEMORY_SCOPES: tuple[str, ...] = ("user", "workspace", "conversation")

# 运行时数据目录（与插件状态一致，已被 .gitignore 忽略）
DATA_DIR = PROJECT_DIR / ".weci"
MEMORY_FILE = DATA_DIR / "memory.json"

_SCOPE_LABELS = {
    "user": "用户",
    "workspace": "Workspace",
    "conversation": "会话",
}

_WHITESPACE_RE = re.compile(r"\s+")


class MemoryError(ValueError):
    """Memory 操作不合法（参数、scope、内容等）。"""


class MemoryNotFound(MemoryError):
    """指定的 Memory 不存在。"""


class MemoryLimitError(MemoryError):
    """超出 Memory 数量或长度限制。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
class MemoryEntry(BaseModel):
    """一条长期记忆。"""

    id: str
    scope: MemoryScope
    scope_id: str
    key: str
    content: str
    created_at: str
    updated_at: str


class MemoryScopeContext(BaseModel):
    """当前请求可用的 scope 实例（用于把 user/workspace/conversation 映射到 scope_id）。"""

    user_id: str = "default"
    workspace_id: str = ""
    conversation_id: str = ""


# 当前请求的 Memory scope；由 Chat 入口在请求内设置，供 Agent 工具读取。
_current_scope: ContextVar[MemoryScopeContext | None] = ContextVar(
    "weci_memory_scope", default=None
)


def current_memory_scope() -> MemoryScopeContext:
    return _current_scope.get() or MemoryScopeContext()


@contextmanager
def use_memory_scope(scope: MemoryScopeContext) -> Generator[None, None, None]:
    """在请求内设置 Memory scope（Agent 工具调用时使用）。"""
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        _current_scope.reset(token)


def resolve_scope_id(scope: str, context: MemoryScopeContext) -> str:
    """把逻辑 scope 解析为具体的 scope_id。

    ``user`` 默认使用 ``default``；``workspace`` / ``conversation`` 必须存在
    对应上下文，否则拒绝保存/读取（避免把记忆写到无归属的桶里）。
    """
    if scope == "user":
        return (context.user_id or "").strip() or "default"
    if scope == "workspace":
        value = (context.workspace_id or "").strip()
        if not value:
            raise MemoryError("当前对话没有 Workspace 上下文，无法使用 workspace 记忆。")
        return value
    if scope == "conversation":
        value = (context.conversation_id or "").strip()
        if not value:
            raise MemoryError("当前对话没有会话标识，无法使用 conversation 记忆。")
        return value
    raise MemoryError(f"未知的 Memory scope：{scope}")


def normalize_key(key: str) -> str:
    """规范化 Memory key：去空白、空格转下划线、小写；长度 1-64。"""
    value = _WHITESPACE_RE.sub("_", (key or "").strip())
    if not value:
        raise MemoryError("Memory key 不能为空。")
    value = value.lower()
    if len(value) > 64:
        raise MemoryError("Memory key 过长（最多 64 个字符）。")
    if any(ord(char) < 32 for char in value):
        raise MemoryError("Memory key 不能包含控制字符。")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_scope(scope: str) -> str:
    if scope not in MEMORY_SCOPES:
        raise MemoryError(
            f"未知的 Memory scope：{scope}（支持：{', '.join(MEMORY_SCOPES)}）"
        )
    return scope


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
class MemoryStore:
    """基于 JSON 文件的 Memory 存储（按 scope + scope_id 隔离）。

    写入使用“临时文件 + 原子替换”，避免进程中途退出导致文件损坏。
    文件内容为 ``{"version": 1, "memories": [...]}``。
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_entries_per_scope: int | None = None,
        max_content_chars: int | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else MEMORY_FILE
        self._max_entries = max(
            1,
            int(
                max_entries_per_scope
                if max_entries_per_scope is not None
                else settings.memory_max_per_scope
            ),
        )
        self._max_content = max(
            1,
            int(
                max_content_chars
                if max_content_chars is not None
                else settings.memory_max_content_chars
            ),
        )
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    # ---- 读写文件 ----
    def _load(self) -> list[MemoryEntry]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        items = raw.get("memories") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        entries: list[MemoryEntry] = []
        for item in items:
            try:
                entries.append(MemoryEntry.model_validate(item))
            except Exception:  # noqa: BLE001 — 跳过损坏条目，不影响其余记忆
                continue
        return entries

    def _persist(self, entries: list[MemoryEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "memories": [entry.model_dump() for entry in entries],
        }
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self._path)

    # ---- 查询 ----
    def list(
        self, *, scope: str | None = None, scope_id: str | None = None
    ) -> list[MemoryEntry]:
        with self._lock:
            entries = self._load()
        if scope is not None:
            _validate_scope(scope)
            entries = [entry for entry in entries if entry.scope == scope]
        if scope_id is not None:
            entries = [entry for entry in entries if entry.scope_id == scope_id]
        return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self._lock:
            for entry in self._load():
                if entry.id == memory_id:
                    return entry
        return None

    def find(self, scope: str, scope_id: str, key: str) -> MemoryEntry | None:
        normalized = normalize_key(key)
        with self._lock:
            for entry in self._load():
                if (
                    entry.scope == scope
                    and entry.scope_id == scope_id
                    and entry.key == normalized
                ):
                    return entry
        return None

    def for_context(
        self,
        *,
        user_id: str = "default",
        workspace_id: str = "",
        conversation_id: str = "",
        max_items: int | None = None,
    ) -> list[MemoryEntry]:
        """加载当前请求可见的 Memory（用户 → Workspace → 会话）。"""
        entries: list[MemoryEntry] = []
        scope_ids: list[tuple[str, str]] = [
            ("user", (user_id or "").strip() or "default")
        ]
        if (workspace_id or "").strip():
            scope_ids.append(("workspace", workspace_id.strip()))
        if (conversation_id or "").strip():
            scope_ids.append(("conversation", conversation_id.strip()))

        for scope, scope_id in scope_ids:
            entries.extend(self.list(scope=scope, scope_id=scope_id))
        if max_items is not None and max_items > 0:
            entries = entries[:max_items]
        return entries

    # ---- 写入 ----
    def save(
        self,
        scope: str,
        scope_id: str,
        key: str,
        content: str,
        *,
        max_content_chars: int | None = None,
    ) -> MemoryEntry:
        """创建或更新一条 Memory（同一 scope + scope_id + key 视为同一条）。"""
        _validate_scope(scope)
        scope_id = (scope_id or "").strip()
        if not scope_id:
            raise MemoryError("Memory scope_id 不能为空。")
        normalized_key = normalize_key(key)
        text = self._clean_content(content, max_content_chars=max_content_chars)

        with self._lock:
            entries = self._load()
            now = _now()
            for index, entry in enumerate(entries):
                if (
                    entry.scope == scope
                    and entry.scope_id == scope_id
                    and entry.key == normalized_key
                ):
                    updated = entry.model_copy(
                        update={"content": text, "updated_at": now}
                    )
                    entries[index] = updated
                    self._persist(entries)
                    return updated

            group_count = sum(
                1
                for entry in entries
                if entry.scope == scope and entry.scope_id == scope_id
            )
            if group_count >= self._max_entries:
                raise MemoryLimitError(
                    f"{_SCOPE_LABELS.get(scope, scope)}记忆数量已达上限"
                    f"（{self._max_entries} 条），请先删除不再需要的记忆。"
                )

            created = MemoryEntry(
                id=uuid.uuid4().hex,
                scope=scope,
                scope_id=scope_id,
                key=normalized_key,
                content=text,
                created_at=now,
                updated_at=now,
            )
            entries.append(created)
            self._persist(entries)
            return created

    def update(
        self,
        memory_id: str,
        *,
        key: str | None = None,
        content: str | None = None,
    ) -> MemoryEntry:
        with self._lock:
            entries = self._load()
            index = next(
                (i for i, entry in enumerate(entries) if entry.id == memory_id),
                None,
            )
            if index is None:
                raise MemoryNotFound("Memory 不存在或已被删除。")
            entry = entries[index]

            updates: dict[str, str] = {"updated_at": _now()}
            if content is not None:
                updates["content"] = self._clean_content(content)
            if key is not None:
                normalized = normalize_key(key)
                if normalized != entry.key and any(
                    other.id != memory_id
                    and other.scope == entry.scope
                    and other.scope_id == entry.scope_id
                    and other.key == normalized
                    for other in entries
                ):
                    raise MemoryError(f"同一 scope 下已存在 key：{normalized}")
                updates["key"] = normalized

            updated = entry.model_copy(update=updates)
            entries[index] = updated
            self._persist(entries)
            return updated

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            entries = self._load()
            remaining = [entry for entry in entries if entry.id != memory_id]
            if len(remaining) == len(entries):
                return False
            self._persist(remaining)
            return True

    def delete_by_key(self, scope: str, scope_id: str, key: str) -> bool:
        _validate_scope(scope)
        normalized = normalize_key(key)
        with self._lock:
            entries = self._load()
            remaining = [
                entry
                for entry in entries
                if not (
                    entry.scope == scope
                    and entry.scope_id == scope_id
                    and entry.key == normalized
                )
            ]
            if len(remaining) == len(entries):
                return False
            self._persist(remaining)
            return True

    def clear(
        self, *, scope: str | None = None, scope_id: str | None = None
    ) -> int:
        with self._lock:
            entries = self._load()
            if scope is not None:
                _validate_scope(scope)
            remaining = [
                entry
                for entry in entries
                if not (
                    (scope is None or entry.scope == scope)
                    and (scope_id is None or entry.scope_id == scope_id)
                )
            ]
            removed = len(entries) - len(remaining)
            if removed:
                self._persist(remaining)
            return removed

    # ---- 内部 ----
    def _clean_content(
        self, content: str, *, max_content_chars: int | None = None
    ) -> str:
        text = (content or "").strip()
        if not text:
            raise MemoryError("Memory 内容不能为空。")
        limit = max_content_chars or self._max_content
        if len(text) > limit:
            raise MemoryLimitError(
                f"Memory 内容过长（最多 {limit} 个字符）。"
            )
        return text


# 进程级默认存储（项目根目录 .weci/memory.json）
default_memory_store = MemoryStore()


__all__ = [
    "MemoryEntry",
    "MemoryError",
    "MemoryLimitError",
    "MemoryNotFound",
    "MemoryScope",
    "MemoryScopeContext",
    "MemoryStore",
    "MEMORY_SCOPES",
    "current_memory_scope",
    "default_memory_store",
    "normalize_key",
    "resolve_scope_id",
    "use_memory_scope",
]
