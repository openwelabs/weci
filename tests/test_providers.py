"""Provider Manager 测试。

覆盖：
- Provider / Model CRUD 与持久化重载
- ``resolve`` 优先级、默认 Provider、禁用与错误路径
- 能力推断与显式覆盖
- 公开结构中的 API Key 安全（只暴露 ``has_api_key``）
- ``test_provider`` / ``test_model`` 的结构与 override Key 不落盘
- REST API 的 CRUD / 404 / 400 / Key 不泄露
- Chat 能力拦截与旧兼容路径

所有配置都写入 ``tmp_path``，不会触及真实的 ``.weci/providers.json``。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.chat as chat_module
import backend.services.providers as providers_module
from backend.api.chat import router as chat_router
from backend.api.providers import router as providers_router
from backend.services.providers import (
    ProviderError,
    ProviderManager,
    infer_capabilities,
)

# 1x1 透明 PNG
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

API_BASE = "https://api.example.com/v1"


def _manager(tmp_path: Path) -> ProviderManager:
    return ProviderManager(config_path=tmp_path / "providers.json")


def _add_provider(
    manager: ProviderManager,
    *,
    name: str = "OpenAI",
    api_key: str = "sk-original",
    models: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return manager.add_provider(
        name=name,
        api_base=kwargs.pop("api_base", API_BASE),
        api_key=api_key,
        models=models or [{"id": "gpt-4o"}],
        **kwargs,
    )


def _assert_no_api_key(value: Any) -> None:
    """递归断言结构中既没有 ``api_key`` 字段，也没有明文 Key。"""
    if isinstance(value, dict):
        assert "api_key" not in value
        for item in value.values():
            _assert_no_api_key(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_api_key(item)
    elif isinstance(value, str):
        assert "sk-secret" not in value


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------
def test_provider_crud_and_reload_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    manager = ProviderManager(config_path=path)
    created = manager.add_provider(
        name="OpenAI",
        api_base=API_BASE,
        api_key="sk-secret",
        models=[
            {"id": "gpt-4o", "context_window": 128000},
            {"id": "gpt-4o-mini"},
        ],
        default_model="gpt-4o-mini",
        chat_model="gpt-4o-mini",
        agent_model="gpt-4o",
        is_default=True,
    )

    assert created["id"] == "openai"
    assert created["is_default"] is True
    assert created["has_api_key"] is True
    assert created["default_model"] == "gpt-4o-mini"
    assert path.is_file()

    # 新实例从同一文件重新加载，配置与安全字段保持一致
    reloaded = ProviderManager(config_path=path)
    listed = reloaded.list_providers()
    assert len(listed) == 1
    assert listed[0]["id"] == "openai"
    assert listed[0]["has_api_key"] is True
    assert {m["id"] for m in listed[0]["models"]} == {"gpt-4o", "gpt-4o-mini"}
    assert reloaded.default_provider().id == "openai"

    # 修改后再次持久化
    updated = reloaded.update_provider(
        "openai", name="OpenAI Updated", api_key="", enabled=False
    )
    assert updated["name"] == "OpenAI Updated"
    assert updated["has_api_key"] is False
    assert updated["enabled"] is False

    again = ProviderManager(config_path=path)
    assert again.get_provider("openai")["name"] == "OpenAI Updated"
    assert again.get_provider("openai")["has_api_key"] is False

    # 删除后文件同步
    assert again.remove_provider("openai") is True
    assert again.remove_provider("openai") is False
    assert ProviderManager(config_path=path).list_providers() == []


def test_default_provider_switch_and_delete(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = _add_provider(manager, name="First")
    second = _add_provider(manager, name="Second")

    assert first["id"] == "first"
    assert first["is_default"] is True

    switched = manager.set_default("second")
    assert switched["is_default"] is True
    assert manager.get_provider("first")["is_default"] is False
    assert manager.default_provider().id == "second"

    # 删除默认 Provider 后自动回退到剩余可用 Provider
    assert manager.remove_provider("second") is True
    assert manager.default_provider().id == "first"
    assert manager.get_provider("first")["is_default"] is True


# ---------------------------------------------------------------------------
# Model CRUD
# ---------------------------------------------------------------------------
def test_model_crud_and_rename_syncs_references(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider = manager.add_provider(
        name="OpenAI",
        api_base=API_BASE,
        models=[{"id": "m1"}, {"id": "m2"}],
        default_model="m1",
        chat_model="m1",
        agent_model="m2",
    )
    provider_id = provider["id"]

    added = manager.add_model(provider_id, model_id="m3", display_name="Model 3")
    assert {m["id"] for m in added["models"]} == {"m1", "m2", "m3"}

    renamed = manager.update_model(provider_id, "m1", new_id="m1-renamed")
    assert renamed["default_model"] == "m1-renamed"
    assert renamed["chat_model"] == "m1-renamed"
    assert renamed["agent_model"] == "m2"
    assert {m["id"] for m in renamed["models"]} == {"m1-renamed", "m2", "m3"}

    # 重命名为已存在的模型会报错
    with pytest.raises(ProviderError, match="模型已存在"):
        manager.update_model(provider_id, "m3", new_id="m2")

    # 更新能力并删除非默认模型
    updated = manager.update_model(
        provider_id, "m2", display_name="M2", capabilities={"vision": False}
    )
    m2 = next(m for m in updated["models"] if m["id"] == "m2")
    assert m2["display_name"] == "M2"
    assert m2["capabilities"]["vision"] is False

    removed = manager.remove_model(provider_id, "m2")
    assert {m["id"] for m in removed["models"]} == {"m1-renamed", "m3"}


def test_remove_default_model_falls_back(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider = manager.add_provider(
        name="OpenAI",
        api_base=API_BASE,
        models=[{"id": "first"}, {"id": "second"}],
        default_model="first",
        chat_model="first",
        agent_model="second",
    )
    provider_id = provider["id"]

    result = manager.remove_model(provider_id, "first")
    assert result["default_model"] == "second"
    assert result["chat_model"] == "second"
    assert result["agent_model"] == "second"
    assert {m["id"] for m in result["models"]} == {"second"}

    # 删除唯一的模型后模型引用被清空
    empty = manager.remove_model(provider_id, "second")
    assert empty["models"] == []
    assert empty["default_model"] == ""
    assert empty["chat_model"] == ""
    assert empty["agent_model"] == ""


def test_duplicate_models_are_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider = manager.add_provider(
        name="OpenAI", api_base=API_BASE, models=[{"id": "m1"}]
    )
    provider_id = provider["id"]

    with pytest.raises(ProviderError, match="模型已存在"):
        manager.add_model(provider_id, model_id="m1")

    with pytest.raises(ProviderError, match="模型已存在"):
        manager.add_provider(
            name="Dup",
            api_base=API_BASE,
            models=[{"id": "same"}, {"id": "same"}],
        )


# ---------------------------------------------------------------------------
# resolve 优先级
# ---------------------------------------------------------------------------
def _resolve_provider(manager: ProviderManager) -> str:
    provider = manager.add_provider(
        name="Resolve",
        api_base=API_BASE,
        api_key="sk-original",
        models=[
            {"id": "default-m"},
            {"id": "chat-m"},
            {"id": "agent-m"},
            {"id": "explicit-m"},
        ],
        default_model="default-m",
        chat_model="chat-m",
        agent_model="agent-m",
    )
    return provider["id"]


def test_resolve_priority(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider_id = _resolve_provider(manager)

    # 显式 model 优先于 chat / agent model
    assert manager.resolve(provider_id, "explicit-m", "chat").model == "explicit-m"
    assert manager.resolve(provider_id, "explicit-m", "agent").model == "explicit-m"

    # chat / agent 各自使用对应模型
    assert manager.resolve(provider_id, None, "chat").model == "chat-m"
    assert manager.resolve(provider_id, None, "agent").model == "agent-m"

    # 清空 chat / agent 后回退到 default_model
    manager.update_provider(provider_id, chat_model="", agent_model="")
    assert manager.resolve(provider_id, None, "chat").model == "default-m"
    assert manager.resolve(provider_id, None, "agent").model == "default-m"


def test_resolve_without_provider_and_model_rules(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider_id = _resolve_provider(manager)
    manager.set_default(provider_id)

    # 无 provider_id 但给了 model → 走旧兼容路径，返回 None
    assert manager.resolve(None, "explicit-m", "chat") is None
    # 未传任何参数 → 使用默认 Provider
    resolved = manager.resolve()
    assert resolved is not None
    assert resolved.provider_id == provider_id
    assert resolved.model == "chat-m"
    assert resolved.capabilities["text"] is True

    # 没有任何 Provider → 返回 None，调用方沿用旧配置
    empty = _manager(tmp_path / "empty")
    assert empty.resolve() is None
    assert empty.resolve(None, "gpt-4o-mini", "chat") is None


def test_resolve_errors(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider_id = _resolve_provider(manager)

    with pytest.raises(ProviderError, match="不存在"):
        manager.resolve("missing", None, "chat")

    manager.update_provider(provider_id, enabled=False)
    with pytest.raises(ProviderError, match="已禁用"):
        manager.resolve(provider_id, None, "chat")

    manager.update_provider(provider_id, enabled=True)
    with pytest.raises(ProviderError, match="不在 Provider"):
        manager.resolve(provider_id, "unknown-model", "chat")

    no_models = manager.add_provider(
        name="NoModels", api_base=API_BASE, models=[]
    )
    with pytest.raises(ProviderError, match="尚未配置模型"):
        manager.resolve(no_models["id"], None, "chat")


# ---------------------------------------------------------------------------
# 能力推断
# ---------------------------------------------------------------------------
def test_infer_capabilities() -> None:
    gpt4o = infer_capabilities("gpt-4o")
    assert gpt4o["text"] is True
    assert gpt4o["vision"] is True
    assert gpt4o["tool_calling"] is True
    assert gpt4o["streaming"] is True

    llama = infer_capabilities("llama3")
    assert llama["text"] is True
    assert llama["vision"] is False
    assert llama["tool_calling"] is True
    assert llama["streaming"] is True

    assert infer_capabilities("qwen2.5-vl-72b")["vision"] is True
    assert infer_capabilities("claude-3-5-sonnet")["vision"] is True


def test_explicit_capabilities_override_inference(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    provider = manager.add_provider(
        name="Caps",
        api_base=API_BASE,
        models=[
            {
                "id": "gpt-4o",
                "capabilities": {"vision": False, "tool_calling": False},
            },
            {
                "id": "llama3",
                "capabilities": {"vision": True, "streaming": False},
            },
        ],
    )
    provider_id = provider["id"]

    vision_model = next(m for m in provider["models"] if m["id"] == "gpt-4o")
    assert vision_model["capabilities"]["vision"] is False
    assert vision_model["capabilities"]["tool_calling"] is False
    assert vision_model["capabilities"]["text"] is True

    llama_model = next(m for m in provider["models"] if m["id"] == "llama3")
    assert llama_model["capabilities"]["vision"] is True
    assert llama_model["capabilities"]["streaming"] is False

    resolved = manager.resolve(provider_id, "gpt-4o", "chat")
    assert resolved.capabilities["vision"] is False
    assert resolved.supports("text") is True


# ---------------------------------------------------------------------------
# 安全：公开结构不含 api_key
# ---------------------------------------------------------------------------
def test_public_structures_never_expose_api_key(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.add_provider(
        name="Secret",
        api_base=API_BASE,
        api_key="sk-secret-value",
        models=[{"id": "gpt-4o"}],
    )
    provider_id = created["id"]

    assert created["has_api_key"] is True
    _assert_no_api_key(created)
    _assert_no_api_key(manager.list_providers())
    _assert_no_api_key(manager.get_provider(provider_id))
    assert manager.public_config() == {
        "has_provider": True,
        "default_provider_id": provider_id,
    }

    # 持久化文件内部仍然保存 Key（仅服务端使用）
    payload = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
    assert payload["providers"][0]["api_key"] == "sk-secret-value"


# ---------------------------------------------------------------------------
# 测试连接
# ---------------------------------------------------------------------------
def test_test_provider_uses_override_without_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    provider = _add_provider(
        manager,
        models=[{"id": "chat-m"}],
        default_model="chat-m",
    )
    provider_id = provider["id"]
    calls: dict[str, Any] = {}

    async def fake_check_connection(*, api_base: str, api_key: str, model: str):
        calls.update(api_base=api_base, api_key=api_key, model=model)
        return {"ok": True, "message": "ok", "latency_ms": 7, "models": ["chat-m"]}

    monkeypatch.setattr(providers_module, "check_connection", fake_check_connection)

    result = asyncio.run(
        manager.test_provider(provider_id, api_key_override="sk-override")
    )
    assert calls == {
        "api_base": API_BASE,
        "api_key": "sk-override",
        "model": "chat-m",
    }
    assert result["ok"] is True
    assert result["latency_ms"] == 7
    assert result["models"] == ["chat-m"]
    assert result["provider_id"] == provider_id
    assert result["provider_name"] == "OpenAI"
    assert result["model"] == "chat-m"
    _assert_no_api_key(result)

    # override Key 不落盘，也不改变已保存的 Key
    payload = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
    assert payload["providers"][0]["api_key"] == "sk-original"
    assert manager.get_provider(provider_id)["has_api_key"] is True


def test_test_model_returns_structure_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    provider = _add_provider(
        manager,
        models=[{"id": "gpt-4o", "capabilities": {"vision": True}}],
    )
    provider_id = provider["id"]
    calls: dict[str, Any] = {}

    async def fake_check_model(*, api_base: str, api_key: str, model: str):
        calls.update(api_base=api_base, api_key=api_key, model=model)
        return {"ok": False, "message": "模型调用失败", "latency_ms": 3, "models": None}

    monkeypatch.setattr(providers_module, "check_model", fake_check_model)

    result = asyncio.run(
        manager.test_model(provider_id, "gpt-4o", api_key_override="sk-temp")
    )
    assert calls == {"api_base": API_BASE, "api_key": "sk-temp", "model": "gpt-4o"}
    assert result["ok"] is False
    assert result["message"] == "模型调用失败"
    assert result["latency_ms"] == 3
    assert result["provider_id"] == provider_id
    assert result["model"] == "gpt-4o"
    assert result["capabilities"]["vision"] is True
    _assert_no_api_key(result)

    payload = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
    assert payload["providers"][0]["api_key"] == "sk-original"

    with pytest.raises(ProviderError, match="模型不存在"):
        asyncio.run(manager.test_model(provider_id, "missing"))


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.fixture
def api_client(tmp_path: Path):
    app = FastAPI()
    app.include_router(providers_router, prefix="/api")
    app.state.provider_manager = ProviderManager(
        config_path=tmp_path / "providers.json"
    )
    with TestClient(app) as client:
        yield client


def test_api_provider_crud_and_default_switch(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/providers",
        json={
            "name": "OpenAI",
            "api_base": API_BASE,
            "api_key": "sk-secret",
            "models": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}],
            "default_model": "gpt-4o-mini",
            "chat_model": "gpt-4o-mini",
            "agent_model": "gpt-4o",
        },
    )
    assert created.status_code == 200
    provider = created.json()["provider"]
    assert provider["id"] == "openai"
    assert provider["is_default"] is True
    assert provider["has_api_key"] is True

    listed = api_client.get("/api/providers")
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()["providers"]] == ["openai"]

    second = api_client.post(
        "/api/providers",
        json={
            "name": "Local",
            "api_base": "http://localhost:11434/v1",
            "models": [{"id": "llama3"}],
            "is_default": True,
        },
    )
    assert second.status_code == 200
    assert second.json()["provider"]["is_default"] is True

    listed = api_client.get("/api/providers").json()["providers"]
    defaults = {p["id"]: p["is_default"] for p in listed}
    assert defaults == {"openai": False, "local": True}

    switched = api_client.patch(
        "/api/providers/openai", json={"is_default": True, "description": "主 Provider"}
    )
    assert switched.status_code == 200
    assert switched.json()["provider"]["is_default"] is True

    deleted = api_client.delete("/api/providers/local")
    assert deleted.status_code == 200
    assert [p["id"] for p in api_client.get("/api/providers").json()["providers"]] == [
        "openai"
    ]


def test_api_model_crud_and_rename(api_client: TestClient) -> None:
    api_client.post(
        "/api/providers",
        json={
            "name": "OpenAI",
            "api_base": API_BASE,
            "models": [{"id": "m1"}],
        },
    )

    added = api_client.post(
        "/api/providers/openai/models",
        json={"id": "m2", "capabilities": {"vision": False}},
    )
    assert added.status_code == 200
    assert {m["id"] for m in added.json()["provider"]["models"]} == {"m1", "m2"}

    # 重复模型 → 400
    duplicate = api_client.post("/api/providers/openai/models", json={"id": "m2"})
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.json()["detail"]

    renamed = api_client.patch(
        "/api/providers/openai/models/m1", json={"id": "m1-renamed"}
    )
    assert renamed.status_code == 200
    provider = renamed.json()["provider"]
    assert provider["default_model"] == "m1-renamed"
    assert provider["chat_model"] == "m1-renamed"
    assert provider["agent_model"] == "m1-renamed"

    updated = api_client.patch(
        "/api/providers/openai/models/m2",
        json={"display_name": "Model 2", "capabilities": {"vision": True}},
    )
    model = next(
        m for m in updated.json()["provider"]["models"] if m["id"] == "m2"
    )
    assert model["display_name"] == "Model 2"
    assert model["capabilities"]["vision"] is True

    # 删除默认模型后回退到剩余模型
    deleted = api_client.delete("/api/providers/openai/models/m1-renamed")
    assert deleted.status_code == 200
    provider = deleted.json()["provider"]
    assert provider["default_model"] == "m2"
    assert provider["chat_model"] == "m2"
    assert provider["agent_model"] == "m2"
    assert [m["id"] for m in provider["models"]] == ["m2"]


def test_api_not_found_and_bad_request(api_client: TestClient) -> None:
    assert api_client.patch("/api/providers/nope", json={"name": "x"}).status_code == 404
    assert api_client.delete("/api/providers/nope").status_code == 404
    # Provider 不存在 → 404（add_model 路径）
    assert (
        api_client.post("/api/providers/nope/models", json={"id": "m"}).status_code
        == 404
    )
    assert (
        api_client.patch(
            "/api/providers/nope/models/m", json={"display_name": "x"}
        ).status_code
        == 404
    )
    assert api_client.post("/api/providers/nope/test", json={}).status_code == 404
    assert (
        api_client.post(
            "/api/providers/nope/models/m/test", json={}
        ).status_code
        == 404
    )

    # 参数校验：Endpoint 不是 http(s) → 400
    invalid = api_client.post(
        "/api/providers", json={"name": "Bad", "api_base": "not-a-url"}
    )
    assert invalid.status_code == 400
    assert "Endpoint" in invalid.json()["detail"]

    api_client.post(
        "/api/providers",
        json={
            "name": "OpenAI",
            "api_base": API_BASE,
            "models": [{"id": "m1"}],
        },
    )
    # 引用不存在的模型 → 400
    bad_ref = api_client.patch(
        "/api/providers/openai", json={"default_model": "missing"}
    )
    assert bad_ref.status_code == 400
    assert "不在 Provider" in bad_ref.json()["detail"]

    # 模型不存在 → 404
    assert (
        api_client.patch(
            "/api/providers/openai/models/missing", json={"display_name": "x"}
        ).status_code
        == 404
    )
    assert (
        api_client.delete("/api/providers/openai/models/missing").status_code == 404
    )


def test_api_never_leaks_api_key(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/providers",
        json={
            "name": "Secret",
            "api_base": API_BASE,
            "api_key": "sk-secret",
            "models": [{"id": "gpt-4o"}],
        },
    )
    assert created.status_code == 200
    _assert_no_api_key(created.json())

    listed = api_client.get("/api/providers")
    _assert_no_api_key(listed.json())
    assert listed.json()["providers"][0]["has_api_key"] is True

    patched = api_client.patch("/api/providers/secret", json={"name": "Secret 2"})
    _assert_no_api_key(patched.json())

    # 清除 Key 后 has_api_key 变为 False
    cleared = api_client.patch("/api/providers/secret", json={"api_key": ""})
    assert cleared.status_code == 200
    assert cleared.json()["provider"]["has_api_key"] is False


def test_api_test_endpoints_return_200(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_client.post(
        "/api/providers",
        json={
            "name": "OpenAI",
            "api_base": API_BASE,
            "api_key": "sk-original",
            "models": [{"id": "gpt-4o"}],
        },
    )

    async def fake_check_connection(*, api_base: str, api_key: str, model: str):
        return {"ok": True, "message": "ok", "latency_ms": 1, "models": ["gpt-4o"]}

    async def fake_check_model(*, api_base: str, api_key: str, model: str):
        return {"ok": True, "message": "模型可用。", "latency_ms": 2, "models": None}

    monkeypatch.setattr(providers_module, "check_connection", fake_check_connection)
    monkeypatch.setattr(providers_module, "check_model", fake_check_model)

    provider_test = api_client.post(
        "/api/providers/openai/test", json={"api_key": "sk-override"}
    )
    assert provider_test.status_code == 200
    body = provider_test.json()
    assert body["ok"] is True
    assert body["provider_id"] == "openai"
    assert body["models"] == ["gpt-4o"]
    _assert_no_api_key(body)

    model_test = api_client.post(
        "/api/providers/openai/models/gpt-4o/test", json={"api_key": "sk-override"}
    )
    assert model_test.status_code == 200
    body = model_test.json()
    assert body["ok"] is True
    assert body["model"] == "gpt-4o"
    assert body["capabilities"]["vision"] is True
    _assert_no_api_key(body)

    # override Key 不落盘
    config_path = Path(api_client.app.state.provider_manager.config_path)
    persisted_payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted_payload["providers"][0]["api_key"] == "sk-original"
    assert (
        ProviderManager(config_path=config_path).get_provider("openai")["has_api_key"]
        is True
    )


# ---------------------------------------------------------------------------
# Chat 能力拦截与兼容路径
# ---------------------------------------------------------------------------
@pytest.fixture
def chat_client(tmp_path: Path):
    from backend.tools.registry import ToolRegistry

    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    app.state.provider_manager = ProviderManager(
        config_path=tmp_path / "providers.json"
    )
    app.state.tool_registry = ToolRegistry()
    with TestClient(app) as client:
        yield client, app.state.provider_manager


def _chat_model_payload(
    manager: ProviderManager,
    *,
    name: str,
    capabilities: dict[str, bool],
) -> tuple[str, str]:
    provider = manager.add_provider(
        name=name,
        api_base=API_BASE,
        models=[{"id": "model-x", "capabilities": capabilities}],
    )
    return provider["id"], "model-x"


def test_chat_rejects_image_when_vision_disabled(chat_client) -> None:
    client, manager = chat_client
    provider_id, model = _chat_model_payload(
        manager, name="NoVision", capabilities={"vision": False}
    )

    response = client.post(
        "/api/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "看看这张图",
                    "attachments": [
                        {"data": PNG_BASE64, "mime_type": "image/png"}
                    ],
                }
            ],
            "provider_id": provider_id,
            "model": model,
        },
    )
    assert response.status_code == 400
    assert "Vision" in response.json()["detail"]


def test_chat_rejects_agent_when_tool_calling_disabled(chat_client) -> None:
    client, manager = chat_client
    provider_id, model = _chat_model_payload(
        manager, name="NoTools", capabilities={"tool_calling": False}
    )

    response = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "帮我算一下"}],
            "provider_id": provider_id,
            "model": model,
            "mode": "agent",
        },
    )
    assert response.status_code == 400
    assert "Tool Calling" in response.json()["detail"]


def test_chat_rejects_when_text_disabled(chat_client) -> None:
    client, manager = chat_client
    provider_id, model = _chat_model_payload(
        manager, name="NoText", capabilities={"text": False}
    )

    response = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "provider_id": provider_id,
            "model": model,
        },
    )
    assert response.status_code == 400
    assert "Text" in response.json()["detail"]


def test_chat_legacy_path_without_provider_streams(
    chat_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, manager = chat_client
    assert manager.list_providers() == []
    captured: dict[str, Any] = {}

    async def fake_stream_chat(
        messages: Any,
        *,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int | None = None,
    ):
        captured.update(api_base=api_base, model=model)
        yield "hello"

    monkeypatch.setattr(chat_module, "stream_chat", fake_stream_chat)

    response = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "api_base": API_BASE,
            "model": "gpt-4o-mini",
        },
    )
    assert response.status_code == 200
    assert captured == {"api_base": API_BASE, "model": "gpt-4o-mini"}

    body = response.text
    assert '"type": "start"' in body
    assert '"type": "delta"' in body
    assert "hello" in body
    assert '"type": "done"' in body
