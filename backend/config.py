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
    version: str = "0.14.0"

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

    # 多模态图片限制：单张大小 / 单条消息张数
    max_image_bytes: int = _env_int("WECI_MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    max_images_per_message: int = _env_int("WECI_MAX_IMAGES_PER_MESSAGE", 4)

    # 文件附件限制：单个文件大小 / 条数 / 提取后文本长度
    max_file_bytes: int = _env_int("WECI_MAX_FILE_BYTES", 5 * 1024 * 1024)
    max_files_per_message: int = _env_int("WECI_MAX_FILES_PER_MESSAGE", 4)
    max_file_text_chars: int = _env_int("WECI_MAX_FILE_TEXT_CHARS", 100_000)

    # Context Manager：模型上下文预算与压缩策略
    # 上下文窗口（tokens）；具体模型会在该值基础上取更保守的已知上限
    context_window_tokens: int = _env_int("WECI_CONTEXT_WINDOW_TOKENS", 128_000)
    # 预留给模型输出的 tokens（默认值；请求里显式 max_tokens 时以请求为准）
    context_reserve_tokens: int = _env_int("WECI_CONTEXT_RESERVE_TOKENS", 4_096)
    # 无论是否压缩，都尽量保留的最近消息条数
    context_min_recent_messages: int = _env_int("WECI_CONTEXT_MIN_RECENT_MESSAGES", 6)
    # 单张图片在预算中的估算 tokens（无需下载即可估算）
    context_image_tokens: int = _env_int("WECI_CONTEXT_IMAGE_TOKENS", 1_000)
    # Workspace 文本注入上限（字符），避免工作区内容挤占对话空间
    context_workspace_max_chars: int = _env_int("WECI_CONTEXT_WORKSPACE_MAX_CHARS", 20_000)
    # 单次请求最多注入多少条 Memory，以及注入文本的字符上限
    context_memory_max_items: int = _env_int("WECI_CONTEXT_MEMORY_MAX_ITEMS", 20)
    context_memory_max_chars: int = _env_int("WECI_CONTEXT_MEMORY_MAX_CHARS", 4_000)

    # Memory：每个 scope 的条数上限与单条内容长度上限（默认不无限增长）
    memory_max_per_scope: int = _env_int("WECI_MEMORY_MAX_PER_SCOPE", 50)
    memory_max_content_chars: int = _env_int("WECI_MEMORY_MAX_CONTENT_CHARS", 500)

    # MCP：本地 stdio Server 的连接 / 调用超时（秒）
    mcp_connect_timeout: float = _env_float("WECI_MCP_CONNECT_TIMEOUT", 20.0)
    mcp_call_timeout: float = _env_float("WECI_MCP_CALL_TIMEOUT", 120.0)

    # Workspace 安全边界：所有文件读写都必须落在这个 Root 之内
    workspace_root: str = _env("WECI_WORKSPACE_ROOT", str(PROJECT_DIR))
    # 权限确认等待上限（秒）；超时按默认拒绝处理
    permission_confirm_timeout: float = _env_float("WECI_PERMISSION_CONFIRM_TIMEOUT", 120.0)
    # 命令执行的默认超时与输出保留上限
    command_default_timeout: float = _env_float("WECI_COMMAND_TIMEOUT", 30.0)
    command_max_output_chars: int = _env_int("WECI_COMMAND_MAX_OUTPUT_CHARS", 20_000)

    cors_origins: tuple[str, ...] = _env_origins()

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


settings = Settings()
