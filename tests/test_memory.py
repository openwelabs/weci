"""Memory（长期记忆）测试。

覆盖：
- Memory 数据结构与 JSON 持久化（创建 / 更新 / 删除 / 按 key upsert）
- scope 隔离（user / workspace / conversation）与数量 / 长度限额
- Context Manager 注入 Memory（数量、长度、去重、转义）
- 插件 memory 权限与内置 memory 插件工具
- /api/memories CRUD
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.memory import router as memory_router
from backend.plugins.context import PluginContextImpl
from backend.services.context import ContextManager
from backend.services.memory import (
    MemoryError,
    MemoryLimitError,
    MemoryNotFound,
    MemoryScopeContext,
    MemoryStore,
    normalize_key,
    resolve_scope_id,
    use_memory_scope,
)
from backend.services.message import ChatMessage
from weci_sdk.permissions import PermissionDenied

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_MEMORY_DIR = PROJECT_ROOT / "backend" / "plugins" / "builtin" / "memory"


def _user(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


def _store(tmp_path: Path, **kwargs) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.json", **kwargs)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


def test_create_list_update_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.save("user", "u1", "Operating System", "Fedora")

    assert entry.scope == "user"
    assert entry.scope_id == "u1"
    assert entry.key == "operating_system"  # 规范化
    assert entry.content == "Fedora"
    assert entry.created_at == entry.updated_at

    listed = store.list(scope="user", scope_id="u1")
    assert [item.id for item in listed] == [entry.id]

    updated = store.update(entry.id, content="Fedora 41")
    assert updated.content == "Fedora 41"
    assert updated.updated_at >= entry.updated_at

    assert store.delete(entry.id) is True
    assert store.delete(entry.id) is False
    assert store.list() == []


def test_save_same_key_updates_instead_of_growing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.save("user", "u1", "editor", "neovim")
    second = store.save("user", "u1", "editor", "emacs")

    assert first.id == second.id
    assert second.content == "emacs"
    assert len(store.list(scope="user", scope_id="u1")) == 1


def test_scopes_are_isolated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.save("user", "u1", "language", "中文")
    workspace = store.save("workspace", "w1", "language", "English")
    conversation = store.save("conversation", "c1", "language", "日本語")

    assert {entry.id for entry in store.list(scope="user", scope_id="u1")} == {user.id}
    assert {entry.id for entry in store.list(scope="workspace", scope_id="w1")} == {
        workspace.id
    }
    assert {entry.id for entry in store.list(scope="conversation", scope_id="c1")} == {
        conversation.id
    }
    assert len(store.list()) == 3


def test_for_context_returns_user_workspace_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("user", "u1", "a", "user-value")
    store.save("workspace", "w1", "b", "workspace-value")
    store.save("conversation", "c1", "c", "conversation-value")
    store.save("user", "u2", "d", "other-user")

    entries = store.for_context(user_id="u1", workspace_id="w1", conversation_id="c1")
    assert [entry.key for entry in entries] == ["a", "b", "c"]

    only_user = store.for_context(user_id="u1")
    assert [entry.key for entry in only_user] == ["a"]


def test_limits(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries_per_scope=2, max_content_chars=10)
    store.save("user", "u1", "a", "1")
    store.save("user", "u1", "b", "2")
    with pytest.raises(MemoryLimitError):
        store.save("user", "u1", "c", "3")
    # 更新同 key 不受条数限制
    assert store.save("user", "u1", "a", "11").content == "11"
    with pytest.raises(MemoryLimitError):
        store.save("user", "u1", "d", "x" * 11)


def test_invalid_input(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(MemoryError):
        store.save("unknown", "u1", "a", "v")  # type: ignore[arg-type]
    with pytest.raises(MemoryError):
        store.save("user", "", "a", "v")
    with pytest.raises(MemoryError):
        store.save("user", "u1", "  ", "v")
    with pytest.raises(MemoryError):
        store.save("user", "u1", "a", "   ")
    with pytest.raises(MemoryNotFound):
        store.update("missing", content="x")


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    first = MemoryStore(path)
    entry = first.save("user", "u1", "os", "Fedora")

    second = MemoryStore(path)
    loaded = second.get(entry.id)
    assert loaded is not None
    assert loaded.content == "Fedora"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["memories"][0]["key"] == "os"


def test_corrupted_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text("{not-json", encoding="utf-8")
    assert MemoryStore(path).list() == []


def test_normalize_key_and_resolve_scope_id() -> None:
    assert normalize_key("  Operating System ") == "operating_system"
    context = MemoryScopeContext(user_id="u1", workspace_id="w1", conversation_id="c1")
    assert resolve_scope_id("user", context) == "u1"
    assert resolve_scope_id("workspace", context) == "w1"
    assert resolve_scope_id("conversation", context) == "c1"
    assert resolve_scope_id("user", MemoryScopeContext()) == "default"
    with pytest.raises(MemoryError):
        resolve_scope_id("workspace", MemoryScopeContext())


# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------


def test_context_injects_memories(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("user", "u1", "operating_system", "Fedora")
    store.save("workspace", "w1", "language", "中文")
    memories = store.for_context(user_id="u1", workspace_id="w1")

    built = ContextManager.from_chat_messages(
        [_user("我的系统是什么？")], memories=memories
    ).build()

    system = built.messages[0]["content"]
    assert "<memories" in system
    assert 'key="operating_system"' in system
    assert "Fedora" in system
    assert "中文" in system
    assert built.memory_count == 2
    assert built.stats["memory_count"] == 2
    # Memory 与聊天记录分离：只出现在 system context 中
    assert built.messages[-1] == {"role": "user", "content": "我的系统是什么？"}


def test_context_limits_memory_count_and_length(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.save("user", "u1", f"k{index}", "内容" * 50)

    manager = ContextManager()
    count = manager.add_memories(store.list(), max_items=3, max_chars=2_000)
    assert count == 3
    assert manager.memory_count == 3

    built = manager.build()
    assert "k0" in str(built.messages)
    assert "k3" not in str(built.messages)


def test_context_escapes_memory_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("user", "u1", "tag", "<b>&</b>")

    built = ContextManager.from_chat_messages(
        [_user("hi")], memories=store.list()
    ).build()

    system = built.messages[0]["content"]
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in system
    assert "<b>" not in system


def test_context_without_memories_has_no_memory_block() -> None:
    built = ContextManager.from_chat_messages([_user("hi")]).build()
    assert built.memory_count == 0
    assert "<memories" not in json.dumps(built.messages)


# ---------------------------------------------------------------------------
# 插件上下文与内置插件
# ---------------------------------------------------------------------------


def test_memory_permission_required(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = PluginContextImpl(
        plugin_id="p", permissions=[], storage_dir=tmp_path, memory_store=store
    )
    with pytest.raises(PermissionDenied):
        ctx.memory_save("k", "v")
    with pytest.raises(PermissionDenied):
        ctx.memory_delete("k")


def test_plugin_context_memory_uses_request_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = PluginContextImpl(
        plugin_id="p", permissions=["memory"], storage_dir=tmp_path, memory_store=store
    )
    scope = MemoryScopeContext(user_id="u1", conversation_id="c1")

    with use_memory_scope(scope):
        saved = ctx.memory.save("os", "Fedora")
        assert saved["scope"] == "user"
        assert ctx.memory.list() == [saved]
        assert ctx.memory.save("topic", "记忆", scope="conversation")["scope_id"] == "c1"
        assert ctx.memory_delete("os") is True
        assert ctx.memory_delete("os") is False


def _load_builtin_memory_plugin():
    path = BUILTIN_MEMORY_DIR / "plugin.py"
    sys.path.insert(0, str(BUILTIN_MEMORY_DIR))
    try:
        spec = importlib.util.spec_from_file_location("weci_memory_plugin_test", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        # Pydantic 需要模块在 sys.modules 中才能解析字符串注解
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_builtin_memory_manifest_and_tools() -> None:
    from backend.plugins.manifest import load_manifest

    manifest = load_manifest(BUILTIN_MEMORY_DIR)
    assert manifest.id == "memory"
    assert manifest.api_version == "0.1"
    assert manifest.permissions == ["memory"]

    module = _load_builtin_memory_plugin()
    tools = module.MemoryPlugin().get_tools()
    assert [tool.name for tool in tools] == ["save_memory", "delete_memory"]


def test_builtin_memory_tools_execute(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = PluginContextImpl(
        plugin_id="memory",
        permissions=["memory"],
        storage_dir=tmp_path,
        memory_store=store,
    )
    module = _load_builtin_memory_plugin()

    with use_memory_scope(MemoryScopeContext(user_id="u1")):
        saved = asyncio.run(
            module.SaveMemoryTool().execute(
                module.SaveMemoryArgs(key="operating_system", content="Fedora"), ctx
            )
        )
        assert saved.success is True
        assert "Fedora" in saved.content
        assert store.find("user", "u1", "operating_system") is not None

        deleted = asyncio.run(
            module.DeleteMemoryTool().execute(
                module.DeleteMemoryArgs(key="operating_system"), ctx
            )
        )
        assert deleted.success is True
        assert store.find("user", "u1", "operating_system") is None


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path):
    app = FastAPI()
    app.include_router(memory_router, prefix="/api")
    app.state.memory_store = MemoryStore(tmp_path / "memory.json")
    return TestClient(app)


def test_api_crud(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/memories",
        json={"key": "os", "content": "Fedora", "user_id": "u1"},
    )
    assert created.status_code == 200
    entry = created.json()["memory"]
    assert entry["scope"] == "user"
    assert entry["key"] == "os"

    listed = api_client.get("/api/memories", params={"user_id": "u1"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["memories"]] == [entry["id"]]

    updated = api_client.patch(
        f"/api/memories/{entry['id']}", json={"content": "Fedora 41"}
    )
    assert updated.status_code == 200
    assert updated.json()["memory"]["content"] == "Fedora 41"

    deleted = api_client.delete(f"/api/memories/{entry['id']}")
    assert deleted.status_code == 200
    assert api_client.get("/api/memories", params={"user_id": "u1"}).json()[
        "memories"
    ] == []


def test_api_scope_requires_context(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/memories",
        json={"key": "k", "content": "v", "scope": "workspace", "workspace_id": ""},
    )
    assert response.status_code == 400
    assert "Workspace" in response.json()["detail"]


def test_api_missing_memory_returns_404(api_client: TestClient) -> None:
    assert api_client.patch("/api/memories/nope", json={"content": "x"}).status_code == 404
    assert api_client.delete("/api/memories/nope").status_code == 404
