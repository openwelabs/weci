"""多模态消息模型与 OpenAI-compatible 适配。

职责边界（图片 / 文件逻辑只存在于这里，不进入 Agent Loop）：

    前端 UI
      ↓  POST /api/chat  { role, content, attachments[], files[] }
    Message / ImageAttachment / FileAttachment（本模块：领域模型 + 校验）
      ↓  build_openai_messages()
    Provider（openai_client）
      ↓
    模型 API

消息内容按 ``text | image | file`` 组织。图片转换为 OpenAI 的
``image_url`` content part；文件在服务端提取文本后转换为 ``text``
content part（chat/completions 没有通用的文件 part），因此模型与
Agent 看到的是同一份上下文。

Agent 与插件系统看到的始终是 ``{"role", "content"}`` 字典，不感知附件。
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from backend.config import settings

# 允许的图片类型（OpenAI-compatible Vision 普遍支持的格式）
ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)

# 单条消息的图片数量与单张图片大小上限（可在测试中 monkeypatch）
MAX_IMAGES_PER_MESSAGE = settings.max_images_per_message
MAX_IMAGE_BYTES = settings.max_image_bytes

# 第一阶段支持的文件类型：纯文本类直接解码，PDF 提取文本
SUPPORTED_TEXT_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".html": "text/html",
    ".css": "text/css",
}
SUPPORTED_FILE_EXTENSIONS = frozenset(SUPPORTED_TEXT_EXTENSIONS) | {".pdf"}

# 文件附件限制（可在测试中 monkeypatch）
MAX_FILES_PER_MESSAGE = settings.max_files_per_message
MAX_FILE_BYTES = settings.max_file_bytes
MAX_FILE_TEXT_CHARS = settings.max_file_text_chars

_DATA_URL_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?;base64,", re.IGNORECASE)
_BASE64_RE = re.compile(r"[^A-Za-z0-9+/=]")

# 通过文件头嗅探真实类型，避免声明与实际不符
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class AttachmentError(ValueError):
    """附件不合法。错误信息对人类可读，可直接返回给前端。"""


class ImageAttachment(BaseModel):
    """一张待发送的图片。

    ``data`` 为 base64 字符串（允许携带 ``data:image/png;base64,`` 前缀，
    后端会统一剥离）。``mime_type`` 可省略，此时以文件头嗅探结果为准。
    """

    data: str = Field(min_length=1, description="base64 图片数据，可带 data URL 前缀。")
    mime_type: str | None = Field(default=None, description="MIME 类型，如 image/png。")
    name: str | None = Field(default=None, max_length=255, description="原始文件名。")


class FileAttachment(BaseModel):
    """一个待发送的文件附件（第一阶段：文本类与 PDF）。

    ``data`` 为原始字节的 base64（允许携带 data URL 前缀）。文本类文件
    由服务端按扩展名解码；PDF 由服务端提取文本后作为上下文传给模型。
    """

    data: str = Field(min_length=1, description="base64 文件数据，可带 data URL 前缀。")
    name: str = Field(min_length=1, max_length=255, description="原始文件名，含扩展名。")
    mime_type: str | None = Field(default=None, description="MIME 类型，如 text/plain。")


class ChatMessage(BaseModel):
    """一条对话消息：文字 + 可选的图片 / 文件附件。"""

    role: Literal["system", "user", "assistant"]
    content: str = ""
    attachments: list[ImageAttachment] = Field(default_factory=list)
    files: list[FileAttachment] = Field(default_factory=list)


@dataclass(frozen=True)
class ValidatedImage:
    """校验通过的图片（后续只在此结构上做格式转换）。"""

    mime_type: str
    data: str  # 纯 base64，不含前缀
    name: str | None
    size: int  # 解码后的字节数

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"


@dataclass(frozen=True)
class ValidatedFile:
    """校验并提取完成后的文件。``text`` 即发送给模型的上下文。"""

    name: str
    mime_type: str
    size: int  # 原始字节数
    text: str


def _sniff_mime(raw: bytes) -> str | None:
    for magic, mime in _MAGIC_SIGNATURES:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(attachment: ImageAttachment, *, index: int) -> ValidatedImage:
    """校验并规范化一张图片。失败抛 :class:`AttachmentError`。"""
    label = f"第 {index + 1} 张图片"

    data = (attachment.data or "").strip()
    match = _DATA_URL_RE.match(data)
    declared_mime = None
    if match:
        declared_mime = (match.group(1) or "").lower() or None
        data = data[match.end() :]
    data = _BASE64_RE.sub("", data)
    if not data:
        raise AttachmentError(f"{label}：缺少图片数据。")

    # 提前拦截超大输入，避免无意义的解码开销（base64 约为原始大小的 4/3）
    if len(data) > MAX_IMAGE_BYTES * 4 // 3 + 4096:
        raise AttachmentError(
            f"{label}：图片过大，单张不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB。"
        )

    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError(f"{label}：图片数据不是有效的 base64。") from exc
    if not raw:
        raise AttachmentError(f"{label}：图片内容为空。")
    if len(raw) > MAX_IMAGE_BYTES:
        raise AttachmentError(
            f"{label}：图片过大，单张不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB。"
        )

    # 文件头嗅探优先于声明值，避免扩展名 / MIME 与实际内容不符
    mime_type = _sniff_mime(raw) or declared_mime or (attachment.mime_type or "").lower()
    if not mime_type:
        raise AttachmentError(f"{label}：无法识别图片格式。")
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        allowed = "、".join(sorted(ALLOWED_IMAGE_MIME_TYPES))
        raise AttachmentError(f"{label}：不支持的图片格式 {mime_type}，仅支持 {allowed}。")

    return ValidatedImage(
        mime_type=mime_type,
        data=data,
        name=attachment.name,
        size=len(raw),
    )


def _decode_base64_payload(data: str, *, label: str, max_bytes: int) -> bytes:
    """解码 base64 附件数据，并校验大小。失败抛 AttachmentError。"""
    value = (data or "").strip()
    match = _DATA_URL_RE.match(value)
    if match:
        value = value[match.end() :]
    value = _BASE64_RE.sub("", value)
    if not value:
        raise AttachmentError(f"{label}：缺少文件数据。")

    # 提前拦截超大输入（base64 约为原始大小的 4/3）
    if len(value) > max_bytes * 4 // 3 + 4096:
        raise AttachmentError(
            f"{label}：文件过大，单个文件不能超过 {max_bytes // (1024 * 1024)} MB。"
        )
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError(f"{label}：文件数据不是有效的 base64。") from exc
    if not raw:
        raise AttachmentError(f"{label}：文件内容为空。")
    if len(raw) > max_bytes:
        raise AttachmentError(
            f"{label}：文件过大，单个文件不能超过 {max_bytes // (1024 * 1024)} MB。"
        )
    return raw


def _decode_text_bytes(raw: bytes, *, label: str) -> str:
    """把文本类文件解码为字符串（UTF-8 优先，兼容 GB18030）。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttachmentError(f"{label}：无法解码文件内容，请使用 UTF-8 或 GBK 编码。")


def _extract_pdf_text(raw: bytes, *, label: str) -> str:
    """提取 PDF 文本（第一阶段只做基础纯文本抽取）。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖已声明
        raise AttachmentError(
            "服务端未安装 PDF 解析组件（pypdf），请安装后重试。"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise AttachmentError(f"{label}：PDF 已加密，无法读取内容。") from exc
        chunks = [page.extract_text() or "" for page in reader.pages]
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError(
            f"{label}：PDF 解析失败，文件可能已损坏或不是有效的 PDF。"
        ) from exc
    return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def validate_file(attachment: FileAttachment, *, index: int) -> ValidatedFile:
    """校验并提取一个文件的内容。失败抛 :class:`AttachmentError`。"""
    label = f"第 {index + 1} 个文件"
    name = Path((attachment.name or "").strip()).name or (attachment.name or "").strip()
    if not name:
        raise AttachmentError(f"{label}：缺少文件名。")

    extension = Path(name).suffix.lower()
    if extension not in SUPPORTED_FILE_EXTENSIONS:
        allowed = "、".join(sorted(SUPPORTED_FILE_EXTENSIONS))
        shown = extension or "（无扩展名）"
        raise AttachmentError(
            f"{label}：不支持的文件类型 {shown}，仅支持 {allowed}。"
        )

    raw = _decode_base64_payload(
        attachment.data, label=label, max_bytes=MAX_FILE_BYTES
    )

    if extension == ".pdf":
        text = _extract_pdf_text(raw, label=label)
    else:
        text = _decode_text_bytes(raw, label=label)

    text = text.replace("\x00", "").strip()
    if not text:
        raise AttachmentError(f"{label}：文件内容为空。")
    if len(text) > MAX_FILE_TEXT_CHARS:
        raise AttachmentError(
            f"{label}：文件内容过长（提取后超过 {MAX_FILE_TEXT_CHARS} 个字符），"
            "请上传更小的文件或先精简内容。"
        )

    mime_type = SUPPORTED_TEXT_EXTENSIONS.get(extension) or (
        attachment.mime_type or "application/pdf"
    )
    return ValidatedFile(
        name=name,
        mime_type=mime_type,
        size=len(raw),
        text=text,
    )


def format_file_block(file: ValidatedFile, *, index: int) -> str:
    """把文件内容包装成带边界的文本块，便于模型区分正文与附件。"""
    safe_name = file.name.replace('"', "'")
    return (
        f'<file index="{index + 1}" name="{safe_name}" type="{file.mime_type}">\n'
        f"{file.text}\n"
        "</file>"
    )


def validate_message(
    message: ChatMessage,
) -> tuple[str, list[ValidatedImage], list[ValidatedFile]]:
    """校验一条消息，返回 (纯文本, 图片列表, 文件列表)。"""
    text = (message.content or "").strip()
    if not message.attachments and not message.files:
        return text, [], []

    if message.role != "user":
        raise AttachmentError("只有用户消息可以包含图片或文件。")
    if len(message.attachments) > MAX_IMAGES_PER_MESSAGE:
        raise AttachmentError(
            f"单条消息最多支持 {MAX_IMAGES_PER_MESSAGE} 张图片，"
            f"当前 {len(message.attachments)} 张。"
        )
    if len(message.files) > MAX_FILES_PER_MESSAGE:
        raise AttachmentError(
            f"单条消息最多支持 {MAX_FILES_PER_MESSAGE} 个文件，"
            f"当前 {len(message.files)} 个。"
        )

    images = [
        validate_image(attachment, index=index)
        for index, attachment in enumerate(message.attachments)
    ]
    files = [
        validate_file(attachment, index=index)
        for index, attachment in enumerate(message.files)
    ]
    return text, images, files


def to_openai_content(
    text: str,
    images: list[ValidatedImage],
    files: list[ValidatedFile] | None = None,
) -> Any:
    """转换为 OpenAI-compatible 的 content。

    没有附件时保持纯字符串，确保纯文本模型与历史行为完全一致。
    有附件时按 ``text → image → file`` 的顺序生成 content parts。
    """
    files = files or []
    if not images and not files:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(
        {"type": "image_url", "image_url": {"url": image.data_url}}
        for image in images
    )
    parts.extend(
        {"type": "text", "text": format_file_block(file, index=index)}
        for index, file in enumerate(files)
    )
    return parts


def build_openai_messages(messages: Iterable[ChatMessage]) -> list[dict[str, Any]]:
    """把领域消息转换为上游请求格式，同时过滤空消息。

    这是图片 / 文件进入 Provider 的唯一入口；Agent 与插件系统不参与其中。
    """
    built: list[dict[str, Any]] = []
    for message in messages:
        text, images, files = validate_message(message)
        if not text and not images and not files:
            continue
        built.append(
            {
                "role": message.role,
                "content": to_openai_content(text, images, files),
            }
        )
    return built


def messages_have_images(messages: Iterable[ChatMessage]) -> bool:
    return any(message.attachments for message in messages)
