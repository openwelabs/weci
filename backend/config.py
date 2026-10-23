"""WeCi 后端配置。

所有敏感信息和可调参数都来自环境变量（或项目根目录的 .env 文件），
源码中不存在任何硬编码的 API Key。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（weci/）
PROJECT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

# 读取 .env（若不存在则静默忽略）
load_dotenv(PROJECT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _env_origins() -> tuple[str, ...]:
    raw = _env("WECI_CORS_ORIGINS", "*")
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or ("*",)


def _env_optional_int(name: str) -> int | None:
    raw = _env(name)
    if raw.isdigit():
        return int(raw)
    return None


@dataclass(frozen=True)
class Settings:
    """运行期配置，来源于环境变量。"""

    app_name: str = "WeCi"
    version: str = "0.5.0"

    host: str = _env("WECI_HOST", "127.0.0.1")
    port: int = _env_int("WECI_PORT", 8000)

    # 默认模型服务；前端设置页可以覆盖
    default_api_base: str = (
        _env("WECI_DEFAULT_API_BASE")
        or _env("OPENAI_BASE_URL")
        or _env("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    )
    default_model: str = _env("WECI_DEFAULT_MODEL", "gpt-4o-mini")
    default_temperature: float = _env_float("WECI_DEFAULT_TEMPERATURE", 0.7)
    # 默认 max_tokens；留空表示不限制（由模型服务自行决定）
    default_max_tokens: int | None = _env_optional_int("WECI_DEFAULT_MAX_TOKENS")

    # 服务端 Key 为可选项：若前端在设置页填写了 Key，则以浏览器传入的为准
    api_key: str = _env("WECI_API_KEY") or _env("OPENAI_API_KEY")

    # 上游请求超时（秒）：连接 / 读取
    connect_timeout: float = _env_float("WECI_CONNECT_TIMEOUT", 10.0)
    read_timeout: float = _env_float("WECI_READ_TIMEOUT", 300.0)

    cors_origins: tuple[str, ...] = _env_origins()

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


settings = Settings()
