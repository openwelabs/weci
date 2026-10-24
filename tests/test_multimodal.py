"""多模态（图片输入）测试。

覆盖：
- 纯文本消息保持原有字符串 content（不改变历史行为）
- 图片消息转换为 OpenAI-compatible content 数组
- 附件校验（角色 / 数量 / 大小 / base64 / 格式）
- 上游拒绝图片请求时附加 Vision 提示
- /api/chat 对非法附件返回 400
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.services.message as message_module
from backend.api.chat import router as chat_router
from backend.services.message import (
    AttachmentError,
    ChatMessage,
    ImageAttachment,
    build_openai_messages,
    messages_have_images,
    validate_image,
)
from backend.services.openai_client import build_upstream_error

# 1x1 透明 PNG
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_BYTES = base64.b64decode(PNG_BASE64)


def _image(data: str = PNG_BASE64, mime_type: str | None = "image/png") -> ImageAttachment:
    return ImageAttachment(data=data, mime_type=mime_type, name="pixel.png")


def _user(content: str = "", with_image: bool = False) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=content,
        attachments=[_image()] if with_image else [],
    )


# ---------------------------------------------------------------------------
# 纯文本兼容
# ---------------------------------------------------------------------------


def test_plain_text_content_remains_string() -> None:
    built = build_openai_messages([_user("你好")])
    assert built == [{"role": "user", "content": "你好"}]


def test_plain_text_ignores_attachment_field_default() -> None:
    built = build_openai_messages(
        [
            ChatMessage(role="system", content="系统提示"),
            ChatMessage(role="assistant", content="上一轮回答"),
        ]
    )
    assert built == [
        {"role": "system", "content": "系统提示"},
        {"role": "assistant", "content": "上一轮回答"},
    ]


def test_empty_messages_are_filtered() -> None:
    assert build_openai_messages([_user("   ")]) == []


def test_messages_have_images() -> None:
    assert messages_have_images([_user("hi", with_image=True)]) is True
    assert messages_have_images([_user("hi")]) is False


# ---------------------------------------------------------------------------
# 多模态转换
# ---------------------------------------------------------------------------


def test_text_and_image_build_content_parts() -> None:
    built = build_openai_messages([_user("描述这张图", with_image=True)])
    assert len(built) == 1
    assert built[0]["role"] == "user"

    parts = built[0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "描述这张图"}
    assert parts[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"},
    }


def test_image_only_message_builds_single_part() -> None:
    built = build_openai_messages([_user(with_image=True)])
    parts = built[0]["content"]
    assert isinstance(parts, list)
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"


def test_data_url_prefix_is_accepted_and_normalized() -> None:
    attachment = ImageAttachment(
        data=f"data:image/png;base64,{PNG_BASE64}",
        mime_type="image/jpeg",  # 声明与内容不符
    )
    validated = validate_image(attachment, index=0)
    assert validated.mime_type == "image/png"
    assert validated.data == PNG_BASE64
    assert validated.data_url.startswith("data:image/png;base64,")


def test_text_is_trimmed() -> None:
    built = build_openai_messages([_user("  看图  ", with_image=True)])
    assert built[0]["content"][0] == {"type": "text", "text": "看图"}


# ---------------------------------------------------------------------------
# 校验失败
# ---------------------------------------------------------------------------


def test_assistant_message_cannot_carry_images() -> None:
    with pytest.raises(AttachmentError) as exc:
        build_openai_messages(
            [
                ChatMessage(
                    role="assistant",
                    content="x",
                    attachments=[_image()],
                )
            ]
        )
    assert "只有用户消息" in str(exc.value)


def test_too_many_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_module, "MAX_IMAGES_PER_MESSAGE", 1)
    with pytest.raises(AttachmentError) as exc:
        build_openai_messages(
            [ChatMessage(role="user", content="", attachments=[_image(), _image()])]
        )
    assert "最多支持 1 张图片" in str(exc.value)


def test_image_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_module, "MAX_IMAGE_BYTES", 32)
    with pytest.raises(AttachmentError) as exc:
        validate_image(_image(), index=0)
    assert "图片过大" in str(exc.value)


def test_invalid_base64_rejected() -> None:
    with pytest.raises(AttachmentError) as exc:
        validate_image(_image(data="abcde", mime_type="image/png"), index=0)
    assert "base64" in str(exc.value)


def test_unsupported_mime_rejected() -> None:
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>').decode()
    with pytest.raises(AttachmentError) as exc:
        validate_image(_image(data=svg, mime_type="image/svg+xml"), index=0)
    assert "不支持的图片格式" in str(exc.value)


def test_unknown_format_without_mime_rejected() -> None:
    payload = base64.b64encode(b"not an image at all").decode()
    with pytest.raises(AttachmentError) as exc:
        validate_image(_image(data=payload, mime_type=None), index=0)
    assert "无法识别图片格式" in str(exc.value)


def test_empty_image_data_rejected() -> None:
    with pytest.raises(AttachmentError):
        validate_image(_image(data="   "), index=0)


# ---------------------------------------------------------------------------
# 上游错误 → Vision 提示
# ---------------------------------------------------------------------------


def _image_messages() -> list[dict[str, object]]:
    return build_openai_messages([_user("看看这张图", with_image=True)])


def test_upstream_error_adds_vision_hint_for_image_request() -> None:
    error = build_upstream_error(
        '{"error": {"message": "invalid image_url format"}}',
        400,
        messages=_image_messages(),
        model="qwen-vl-max",
    )
    assert "invalid image_url format" in error.message
    assert "Vision" in error.message
    assert "qwen-vl-max" in error.message
    assert error.status_code == 400


def test_upstream_error_adds_hint_on_413_without_keywords() -> None:
    error = build_upstream_error(
        "payload too large",
        413,
        messages=_image_messages(),
    )
    assert "Vision" in error.message


def test_upstream_error_without_images_is_unchanged() -> None:
    error = build_upstream_error(
        '{"error": {"message": "invalid model"}}',
        400,
        messages=build_openai_messages([_user("你好")]),
        model="text-only",
    )
    assert error.message == "invalid model"
    assert "Vision" not in error.message


def test_upstream_error_hint_skipped_for_unrelated_status_with_images() -> None:
    error = build_upstream_error(
        "rate limit exceeded",
        429,
        messages=_image_messages(),
    )
    assert "Vision" not in error.message


# ---------------------------------------------------------------------------
# API 层：非法附件返回 400
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return TestClient(app)


def test_api_rejects_invalid_attachment(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "看图",
                    "attachments": [{"data": "abcde", "mime_type": "image/png"}],
                }
            ],
            "api_base": "https://example.com/v1",
            "model": "gpt-4o-mini",
        },
    )
    assert response.status_code == 400
    assert "base64" in response.json()["detail"]


def test_api_rejects_images_on_assistant_role(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={
            "messages": [
                {
                    "role": "assistant",
                    "content": "x",
                    "attachments": [{"data": PNG_BASE64, "mime_type": "image/png"}],
                }
            ],
            "api_base": "https://example.com/v1",
            "model": "gpt-4o-mini",
        },
    )
    assert response.status_code == 400
    assert "只有用户消息" in response.json()["detail"]


def test_api_rejects_empty_messages(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "  "}]},
    )
    assert response.status_code == 400
    assert "不能为空" in response.json()["detail"]
