"""统一模型 Provider 管理。

Provider / Model / Capability 的唯一来源，Chat、Agent 与多模态都从这里
解析 endpoint、API Key 与模型能力，不再各自维护配置：

    Provider Manager
          ↓
        Model
          ↓
      Capabilities
          ↓
    Chat / Agent / Multimodal

配置保存在项目内的 ``.weci/providers.json``，API Key 只写不读：公开
响应只包含 ``has_api_key``，日志与错误信息中不出现明文 Key。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from backend.config import PROJECT_DIR, settings
from backend.services.openai_client import check_connection, check_model

logger = logging.getLogger("weci.providers")

DATA_DIR = PROJECT_DIR / ".weci"
DEFAULT_PROVIDER_CONFIG_FILE = DATA_DIR / "providers.json"

CAPABILITY_KEYS = ("text", "vision", "tool_calling", "streaming")
CAPABILITY_LABELS = {
    "text": "Text",
    "vision": "Vision",
    "tool_calling": "Tool Calling",
    "streaming": "Streaming",
}

# 模型名启发式：只用于生成默认能力，用户可在 UI 中覆盖
_VISION_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-4-vision",
    "claude",
    "gemini",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "llava",
    "vision",
    "-vl",
    "omni",
)

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9_-]+")

_UNSET = object()


class ProviderError(ValueError):
    """Provider / Model 配置不合法，或无法解析出可用模型。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
def infer_capabilities(model_name: str) -> dict[str, bool]:
    """按模型名推断默认能力（保守、可被用户覆盖）。"""
    name = (model_name or "").lower()
    vision = any(hint in name for hint in _VISION_HINTS)
    return {
        "text": True,
        "vision": vision,
        "tool_calling": True,
        "streaming": True,
    }


def _normalize_capabilities(
    raw: Any, model_name: str, *, base: dict[str, bool] | None = None
) -> dict[str, bool]:
    """在推断值（或已有值）之上覆盖显式声明的能力。"""
    values = dict(base) if base is not None else infer_capabilities(model_name)
    if not isinstance(raw, dict):
        return values
    for key in CAPABILITY_KEYS:
        if key in raw:
            values[key] = bool(raw[key])
    return values


@dataclass
class ModelConfig:
    """一个模型及其能力声明。"""

    id: str
    display_name: str = ""
    capabilities: dict[str, bool] = field(
        default_factory=lambda: {key: True for key in CAPABILITY_KEYS}
    )
    context_window: int | None = None
    max_tokens: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelConfig":
        if not isinstance(raw, dict):
            raise ProviderError("模型配置必须是对象。")
        model_id = _validate_model_id(_clean_str(raw.get("id") or raw.get("name")))
        return cls(
            id=model_id,
            display_name=_clean_str(raw.get("display_name"))[:120],
            capabilities=_normalize_capabilities(raw.get("capabilities"), model_id),
            context_window=_optional_int(
                raw.get("context_window"), 1024, 2_000_000, "context window"
            ),
            max_tokens=_optional_int(raw.get("max_tokens"), 1, 200_000, "max tokens"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "capabilities": {key: bool(self.capabilities.get(key, False)) for key in CAPABILITY_KEYS},
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
        }

    def public_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass
class ProviderConfig:
    """一个 OpenAI-compatible Provider 及其模型列表。"""

    id: str
    name: str
    api_base: str
    api_key: str = ""
    models: list[ModelConfig] = field(default_factory=list)
    default_model: str = ""
    chat_model: str = ""
    agent_model: str = ""
    enabled: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderConfig":
        if not isinstance(raw, dict):
            raise ProviderError("Provider 配置必须是对象。")
        name = _validate_name(raw.get("name") or raw.get("id"))
        provider_id = _clean_str(raw.get("id")) or slugify(name)
        api_base = _validate_api_base(raw.get("api_base"))
        models: list[ModelConfig] = []
        seen: set[str] = set()
        for item in raw.get("models") or []:
            try:
                model = ModelConfig.from_dict(item)
            except ProviderError as exc:
                logger.warning("忽略无效的模型配置（Provider %s）：%s", provider_id, exc)
                continue
            if model.id in seen:
                logger.warning("Provider %s 的模型 id 重复，已跳过: %s", provider_id, model.id)
                continue
            seen.add(model.id)
            models.append(model)
        provider = cls(
            id=provider_id,
            name=name,
            api_base=api_base,
            api_key=_clean_str(raw.get("api_key")),
            models=models,
            enabled=bool(raw.get("enabled", True)),
            description=_clean_str(raw.get("description"))[:200],
        )
        provider.default_model = _model_ref(
            provider, raw.get("default_model"), fallback=provider._first_model()
        )
        provider.chat_model = _model_ref(
            provider, raw.get("chat_model"), fallback=provider.default_model
        )
        provider.agent_model = _model_ref(
            provider, raw.get("agent_model"), fallback=provider.default_model
        )
        return provider

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "enabled": self.enabled,
            "default_model": self.default_model,
            "chat_model": self.chat_model,
            "agent_model": self.agent_model,
            "models": [model.to_dict() for model in self.models],
        }

    def public_dict(self, *, is_default: bool = False) -> dict[str, Any]:
        """可安全返回给前端 / CLI 的结构（不含 API Key）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "api_base": self.api_base,
            "enabled": self.enabled,
            "is_default": is_default,
            "has_api_key": bool(self.api_key),
            "default_model": self.default_model,
            "chat_model": self.chat_model,
            "agent_model": self.agent_model,
            "models": [model.public_dict() for model in self.models],
        }

    def find_model(self, model_id: str) -> ModelConfig | None:
        name = _clean_str(model_id)
        return next((model for model in self.models if model.id == name), None)

    def mode_model(self, mode: str = "chat") -> str:
        if mode == "agent":
            return self.agent_model or self.default_model
        return self.chat_model or self.default_model

    def _first_model(self) -> str:
        return self.models[0].id if self.models else ""


@dataclass
class ResolvedModel:
    """一次请求最终使用的 Provider / Model / Capability 组合。"""

    provider_id: str
    provider_name: str
    api_base: str
    api_key: str
    model: str
    capabilities: dict[str, bool]
    context_window: int | None = None
    max_tokens: int | None = None

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class ProviderManager:
    """管理 Provider 配置、模型能力与请求解析。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = (
            Path(config_path) if config_path else DEFAULT_PROVIDER_CONFIG_FILE
        )
        self._providers: dict[str, ProviderConfig] = {}
        self._default_id: str | None = None
        self._file_lock = threading.RLock()
        self._load_configs()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_providers(self) -> list[dict[str, Any]]:
        return [
            provider.public_dict(is_default=provider.id == self._current_default_id())
            for provider in self._providers.values()
        ]

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        provider = self._providers.get(_clean_str(provider_id))
        if provider is None:
            return None
        return provider.public_dict(
            is_default=provider.id == self._current_default_id()
        )

    def default_provider(self) -> ProviderConfig | None:
        provider = self._providers.get(self._default_id or "")
        if provider is not None and provider.enabled:
            return provider
        return next(
            (item for item in self._providers.values() if item.enabled), None
        )

    def public_config(self) -> dict[str, Any]:
        default = self.default_provider()
        return {
            "has_provider": bool(self._providers),
            "default_provider_id": default.id if default else None,
        }

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------
    def add_provider(
        self,
        *,
        name: str,
        api_base: str,
        api_key: str = "",
        models: Iterable[dict[str, Any]] | None = None,
        default_model: str = "",
        chat_model: str = "",
        agent_model: str = "",
        enabled: bool = True,
        description: str = "",
        is_default: bool = False,
    ) -> dict[str, Any]:
        clean_name = _validate_name(name)
        provider_id = self._unique_id(slugify(clean_name))
        provider = ProviderConfig(
            id=provider_id,
            name=clean_name,
            api_base=_validate_api_base(api_base),
            api_key=_clean_str(api_key),
            enabled=bool(enabled),
            description=_clean_str(description)[:200],
        )
        for item in models or []:
            model = _build_model(item)
            if provider.find_model(model.id):
                raise ProviderError(f"模型已存在：{model.id}")
            provider.models.append(model)

        provider.default_model = _model_ref(
            provider, default_model, fallback=provider._first_model()
        )
        provider.chat_model = _model_ref(
            provider, chat_model, fallback=provider.default_model
        )
        provider.agent_model = _model_ref(
            provider, agent_model, fallback=provider.default_model
        )

        self._providers[provider_id] = provider
        if is_default or not self._providers or self._current_default_id() is None:
            self._default_id = provider_id
        self._ensure_default()
        self._persist()
        logger.info("Provider 已添加: %s", provider_id)
        return provider.public_dict(is_default=provider.id == self._current_default_id())

    def update_provider(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        enabled: bool | None = None,
        description: str | None = None,
        default_model: str | None = None,
        chat_model: str | None = None,
        agent_model: str | None = None,
        is_default: bool | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_id)

        if name is not None:
            provider.name = _validate_name(name)
        if api_base is not None:
            provider.api_base = _validate_api_base(api_base)
        if api_key is not None:
            # 传空字符串表示清除；None 表示不修改
            provider.api_key = _clean_str(api_key)
        if enabled is not None:
            provider.enabled = bool(enabled)
        if description is not None:
            provider.description = _clean_str(description)[:200]
        if default_model is not None:
            provider.default_model = _model_ref(provider, default_model, fallback="")
        if chat_model is not None:
            provider.chat_model = _model_ref(provider, chat_model, fallback="")
        if agent_model is not None:
            provider.agent_model = _model_ref(provider, agent_model, fallback="")

        if is_default is True:
            self._default_id = provider.id
        elif is_default is False and self._default_id == provider.id:
            self._default_id = None
        self._ensure_default()
        self._persist()
        logger.info("Provider 已更新: %s", provider.id)
        return provider.public_dict(is_default=provider.id == self._current_default_id())

    def remove_provider(self, provider_id: str) -> bool:
        provider = self._providers.pop(_clean_str(provider_id), None)
        if provider is None:
            return False
        if self._default_id == provider.id:
            self._default_id = None
        self._ensure_default()
        self._persist()
        logger.info("Provider 已删除: %s", provider.id)
        return True

    def set_default(self, provider_id: str) -> dict[str, Any]:
        return self.update_provider(provider_id, is_default=True)

    # ------------------------------------------------------------------
    # Model CRUD
    # ------------------------------------------------------------------
    def add_model(
        self,
        provider_id: str,
        *,
        model_id: str,
        display_name: str = "",
        capabilities: dict[str, bool] | None = None,
        context_window: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_id)
        model = _build_model(
            {
                "id": model_id,
                "display_name": display_name,
                "capabilities": capabilities,
                "context_window": context_window,
                "max_tokens": max_tokens,
            }
        )
        if provider.find_model(model.id):
            raise ProviderError(f"模型已存在：{model.id}")
        provider.models.append(model)
        if not provider.default_model:
            provider.default_model = model.id
        if not provider.chat_model:
            provider.chat_model = provider.default_model
        if not provider.agent_model:
            provider.agent_model = provider.default_model
        self._persist()
        logger.info("Provider %s 新增模型: %s", provider.id, model.id)
        return provider.public_dict(is_default=provider.id == self._current_default_id())

    def update_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        new_id: Any = _UNSET,
        display_name: Any = _UNSET,
        capabilities: Any = _UNSET,
        context_window: Any = _UNSET,
        max_tokens: Any = _UNSET,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_id)
        model = provider.find_model(model_id)
        if model is None:
            raise ProviderError(f"模型不存在：{model_id}")

        old_id = model.id
        if new_id is not _UNSET and new_id is not None:
            candidate = _validate_model_id(_clean_str(new_id))
            if candidate != old_id and provider.find_model(candidate):
                raise ProviderError(f"模型已存在：{candidate}")
            model.id = candidate
        if display_name is not _UNSET:
            model.display_name = _clean_str(display_name)[:120]
        if capabilities is not _UNSET:
            model.capabilities = _normalize_capabilities(
                capabilities, model.id, base=model.capabilities
            )
        if context_window is not _UNSET:
            model.context_window = _optional_int(
                context_window, 1024, 2_000_000, "context window"
            )
        if max_tokens is not _UNSET:
            model.max_tokens = _optional_int(
                max_tokens, 1, 200_000, "max tokens"
            )

        if model.id != old_id:
            for attr in ("default_model", "chat_model", "agent_model"):
                if getattr(provider, attr) == old_id:
                    setattr(provider, attr, model.id)
        self._persist()
        logger.info("Provider %s 更新模型: %s", provider.id, model.id)
        return provider.public_dict(is_default=provider.id == self._current_default_id())

    def remove_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        provider = self._require_provider(provider_id)
        model = provider.find_model(model_id)
        if model is None:
            raise ProviderError(f"模型不存在：{model_id}")
        provider.models = [item for item in provider.models if item.id != model.id]
        fallback = provider._first_model()
        if provider.default_model == model.id:
            provider.default_model = fallback
        if provider.chat_model == model.id or not provider.chat_model:
            provider.chat_model = provider.default_model
        if provider.agent_model == model.id or not provider.agent_model:
            provider.agent_model = provider.default_model
        self._persist()
        logger.info("Provider %s 删除模型: %s", provider.id, model.id)
        return provider.public_dict(is_default=provider.id == self._current_default_id())

    # ------------------------------------------------------------------
    # 请求解析
    # ------------------------------------------------------------------
    def resolve(
        self,
        provider_id: str | None = None,
        model: str | None = None,
        mode: str = "chat",
    ) -> ResolvedModel | None:
        """把请求解析为统一的 Provider / Model / Capability。

        返回 ``None`` 表示没有 Provider 参与，调用方应沿用旧的
        ``api_base + model`` 兼容路径。
        """
        clean_model = _clean_str(model)
        clean_provider = _clean_str(provider_id)

        if clean_provider:
            provider = self._providers.get(clean_provider)
            if provider is None:
                raise ProviderError(f"Provider 不存在：{clean_provider}")
            if not provider.enabled:
                raise ProviderError(f"Provider 已禁用：{provider.name}")
            return self._resolve_in_provider(provider, clean_model, mode)

        if clean_model:
            # 兼容路径：旧前端 / 旧 API 直接传 model 与 api_base
            return None

        provider = self.default_provider()
        if provider is None:
            return None
        return self._resolve_in_provider(provider, "", mode)

    def _resolve_in_provider(
        self, provider: ProviderConfig, model: str, mode: str
    ) -> ResolvedModel:
        target = model or provider.mode_model(mode) or provider.default_model
        if not target and len(provider.models) == 1:
            target = provider.models[0].id
        if not target:
            raise ProviderError(
                f"Provider「{provider.name}」尚未配置模型，请先在 Provider 管理中添加 Model。"
            )
        config = provider.find_model(target)
        if config is None:
            raise ProviderError(
                f"模型「{target}」不在 Provider「{provider.name}」的模型列表中。"
            )
        return ResolvedModel(
            provider_id=provider.id,
            provider_name=provider.name,
            api_base=provider.api_base,
            api_key=provider.api_key,
            model=config.id,
            capabilities=dict(config.capabilities),
            context_window=config.context_window,
            max_tokens=config.max_tokens,
        )

    # ------------------------------------------------------------------
    # 测试连接
    # ------------------------------------------------------------------
    async def test_provider(
        self,
        provider_id: str,
        *,
        model: str | None = None,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_id)
        key = self._effective_key(provider, api_key_override)
        target = _clean_str(model) or provider.mode_model("chat") or provider.default_model
        result = await check_connection(
            api_base=provider.api_base, api_key=key, model=target
        )
        result.setdefault("models", None)
        result["provider_id"] = provider.id
        result["provider_name"] = provider.name
        result["model"] = target or None
        return result

    async def test_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_id)
        model = provider.find_model(model_id)
        if model is None:
            raise ProviderError(f"模型不存在：{model_id}")
        key = self._effective_key(provider, api_key_override)
        result = await check_model(
            api_base=provider.api_base, api_key=key, model=model.id
        )
        result["provider_id"] = provider.id
        result["provider_name"] = provider.name
        result["model"] = model.id
        result["capabilities"] = dict(model.capabilities)
        return result

    # ------------------------------------------------------------------
    # 内部：配置持久化
    # ------------------------------------------------------------------
    def _load_configs(self) -> None:
        raw = self._read_config()
        providers = raw.get("providers") if isinstance(raw, dict) else None
        if isinstance(raw, dict):
            self._default_id = _clean_str(raw.get("default_provider")) or None
        if not isinstance(providers, list):
            return
        for item in providers:
            try:
                provider = ProviderConfig.from_dict(item)
            except ProviderError as exc:
                logger.warning("忽略无效的 Provider 配置: %s", exc)
                continue
            if provider.id in self._providers:
                logger.warning("Provider id 重复，已跳过: %s", provider.id)
                continue
            self._providers[provider.id] = provider
        self._ensure_default()

    def _read_config(self) -> dict[str, Any]:
        with self._file_lock:
            if not self.config_path.is_file():
                return {}
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("无法读取 Provider 配置 %s: %s", self.config_path, exc)
                return {}
        return data if isinstance(data, dict) else {}

    def _persist(self) -> None:
        payload = {
            "version": 1,
            "default_provider": self._current_default_id(),
            "providers": [provider.to_dict() for provider in self._providers.values()],
        }
        with self._file_lock:
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
                temp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temp.replace(self.config_path)
                try:
                    os.chmod(self.config_path, 0o600)
                except OSError:
                    pass
            except OSError as exc:
                logger.warning("无法写入 Provider 配置 %s: %s", self.config_path, exc)

    # ------------------------------------------------------------------
    # 内部：辅助
    # ------------------------------------------------------------------
    def _require_provider(self, provider_id: str) -> ProviderConfig:
        provider = self._providers.get(_clean_str(provider_id))
        if provider is None:
            raise ProviderError(f"Provider 不存在：{provider_id}")
        return provider

    def _current_default_id(self) -> str | None:
        provider = self._providers.get(self._default_id or "")
        return provider.id if provider is not None else None

    def _ensure_default(self) -> None:
        if self._current_default_id() is not None:
            return
        preferred = next(
            (item for item in self._providers.values() if item.enabled), None
        )
        if preferred is None:
            preferred = next(iter(self._providers.values()), None)
        self._default_id = preferred.id if preferred is not None else None

    def _effective_key(self, provider: ProviderConfig, override: str | None) -> str:
        if override is not None and _clean_str(override):
            return _clean_str(override)
        return provider.api_key or settings.api_key

    def _unique_id(self, base: str) -> str:
        candidate = base or "provider"
        if candidate not in self._providers:
            return candidate
        for index in range(2, 1000):
            name = f"{candidate}-{index}"
            if name not in self._providers:
                return name
        raise ProviderError("无法生成唯一的 Provider id。")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def slugify(value: str) -> str:
    slug = _SLUG_INVALID_RE.sub("-", (value or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "provider")[:48]


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _validate_name(value: Any) -> str:
    name = _clean_str(value)
    if not name:
        raise ProviderError("Provider 名称不能为空。")
    if len(name) > 64:
        raise ProviderError("Provider 名称不能超过 64 个字符。")
    return name


def _validate_api_base(value: Any) -> str:
    base = _clean_str(value)
    if not base:
        raise ProviderError("API Endpoint 不能为空。")
    if len(base) > 1024:
        raise ProviderError("API Endpoint 过长。")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProviderError("API Endpoint 必须以 http:// 或 https:// 开头。")
    return base.rstrip("/")


def _validate_model_id(value: Any) -> str:
    model_id = _clean_str(value)
    if not model_id:
        raise ProviderError("模型名称不能为空。")
    if len(model_id) > 200:
        raise ProviderError("模型名称不能超过 200 个字符。")
    if "\n" in model_id or "\r" in model_id:
        raise ProviderError("模型名称不能包含换行符。")
    return model_id


def _optional_int(value: Any, minimum: int, maximum: int, label: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"{label} 必须是整数。") from exc
    if parsed < minimum or parsed > maximum:
        raise ProviderError(f"{label} 必须在 {minimum}–{maximum} 之间。")
    return parsed


def _build_model(raw: dict[str, Any]) -> ModelConfig:
    if not isinstance(raw, dict):
        raise ProviderError("模型配置必须是对象。")
    model_id = _validate_model_id(raw.get("id") or raw.get("name"))
    return ModelConfig(
        id=model_id,
        display_name=_clean_str(raw.get("display_name"))[:120],
        capabilities=_normalize_capabilities(raw.get("capabilities"), model_id),
        context_window=_optional_int(
            raw.get("context_window"), 1024, 2_000_000, "context window"
        ),
        max_tokens=_optional_int(raw.get("max_tokens"), 1, 200_000, "max tokens"),
    )


def _model_ref(
    provider: ProviderConfig, value: Any, *, fallback: str
) -> str:
    name = _clean_str(value)
    if not name:
        return fallback
    if provider.find_model(name) is None:
        raise ProviderError(
            f"模型「{name}」不在 Provider「{provider.name}」的模型列表中。"
        )
    return name


__all__ = [
    "CAPABILITY_KEYS",
    "CAPABILITY_LABELS",
    "ModelConfig",
    "ProviderConfig",
    "ProviderError",
    "ProviderManager",
    "ResolvedModel",
    "infer_capabilities",
    "slugify",
]
