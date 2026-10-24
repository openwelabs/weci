"""文件附件（文本类 + PDF）测试。

覆盖：
- 文本文件提取并作为上下文传给模型
- 文字 / 图片 / 文件在 content 中的顺序
- 角色 / 数量 / 大小 / 内容长度 / 扩展名校验
- PDF 基础文本提取（pypdf）与损坏文件的错误
- /api/chat 对非法文件返回 400
- 纯文本与图片行为不受影响
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
    FileAttachment,
    ImageAttachment,
    build_openai_messages,
    format_file_block,
    to_openai_content,
    validate_file,
)

PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _b64(raw: bytes | str) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return base64.b64encode(raw).decode()


def _file(
    name: str = "notes.txt",
    content: bytes | str = "hello file",
    mime: str | None = None,
) -> FileAttachment:
    return FileAttachment(data=_b64(content), name=name, mime_type=mime)


def _user(content: str = "", *, files=None, images=None) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=content,
        files=files or [],
        attachments=images or [],
    )


def _text_parts(content) -> list[str]:
    return [
        part["text"] for part in content if isinstance(part, dict) and part["type"] == "text"
    ]


# ---------------------------------------------------------------------------
# 文本文件 → 上下文
# ---------------------------------------------------------------------------


def test_text_file_becomes_text_part() -> None:
    built = build_openai_messages([_user("读一下", files=[_file()])])
    assert len(built) == 1

    content = built[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "读一下"}
    block = content[1]["text"]
    assert block.startswith('<file index="1" name="notes.txt" type="text/plain">')
    assert "hello file" in block
    assert block.endswith("</file>")


def test_file_only_message() -> None:
    built = build_openai_messages([_user(files=[_file()])])
    content = built[0]["content"]
    assert isinstance(content, list)
    assert [_p["type"] for _p in content] == ["text"]
    assert "hello file" in content[0]["text"]


def test_text_image_file_order() -> None:
    built = build_openai_messages(
        [
            _user(
                "看图并读文件",
                images=[ImageAttachment(data=PNG_BASE64, mime_type="image/png")],
                files=[_file()],
            )
        ]
    )
    content = built[0]["content"]
    assert [_p["type"] for _p in content] == ["text", "image_url", "text"]


def test_multiple_files_are_numbered() -> None:
    built = build_openai_messages(
        [
            _user(
                files=[
                    _file("a.txt", "AAA"),
                    _file("b.py", "print('b')"),
                ]
            )
        ]
    )
    blocks = _text_parts(built[0]["content"])
    assert 'index="1"' in blocks[0] and "AAA" in blocks[0]
    assert 'index="2"' in blocks[1] and "print('b')" in blocks[1]


def test_gb18030_file_is_decoded() -> None:
    raw = "中文内容".encode("gb18030")
    validated = validate_file(_file("notes.txt", raw), index=0)
    assert validated.text == "中文内容"


def test_file_name_is_sanitized() -> None:
    validated = validate_file(_file("../evil.txt", "x"), index=0)
    assert validated.name == "evil.txt"
    block = format_file_block(validated, index=0)
    assert 'name="evil.txt"' in block


def test_file_with_double_quote_name_is_escaped() -> None:
    validated = validate_file(_file('a"b.txt', "x"), index=0)
    block = format_file_block(validated, index=0)
    assert 'name="a\'b.txt"' in block


def test_pdf_extraction_uses_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        message_module, "_extract_pdf_text", lambda raw, label: "PDF 里的文字"
    )
    validated = validate_file(_file("report.pdf", b"%PDF-1.4 fake"), index=0)
    assert validated.text == "PDF 里的文字"
    assert validated.mime_type == "application/pdf"


def test_broken_pdf_raises_clear_error() -> None:
    pytest.importorskip("pypdf")
    with pytest.raises(AttachmentError) as exc:
        validate_file(_file("report.pdf", b"this is not a pdf"), index=0)
    assert "PDF 解析失败" in str(exc.value)


# ---------------------------------------------------------------------------
# 校验失败
# ---------------------------------------------------------------------------


def test_unsupported_extension_rejected() -> None:
    with pytest.raises(AttachmentError) as exc:
        validate_file(_file("report.docx", "x"), index=0)
    assert "不支持的文件类型" in str(exc.value)
    assert ".pdf" in str(exc.value)


def test_document_on_assistant_role_rejected() -> None:
    with pytest.raises(AttachmentError) as exc:
        build_openai_messages(
            [
                ChatMessage(
                    role="assistant",
                    content="x",
                    files=[_file()],
                )
            ]
        )
    assert "只有用户消息" in str(exc.value)


def test_too_many_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_module, "MAX_FILES_PER_MESSAGE", 1)
    with pytest.raises(AttachmentError) as exc:
        build_openai_messages([_user(files=[_file("a.txt"), _file("b.txt")])])
    assert "最多支持 1 个文件" in str(exc.value)


def test_file_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_module, "MAX_FILE_BYTES", 4)
    with pytest.raises(AttachmentError) as exc:
        validate_file(_file("a.txt", "内容超过四个字节"), index=0)
    assert "文件过大" in str(exc.value)


def test_text_too_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_module, "MAX_FILE_TEXT_CHARS", 5)
    with pytest.raises(AttachmentError) as exc:
        validate_file(_file("a.txt", "0123456789"), index=0)
    assert "内容过长" in str(exc.value)


def test_empty_file_rejected() -> None:
    with pytest.raises(AttachmentError) as exc:
        validate_file(_file("empty.txt", "   \n"), index=0)
    assert "内容为空" in str(exc.value)


def test_invalid_base64_rejected() -> None:
    with pytest.raises(AttachmentError) as exc:
        validate_file(FileAttachment(data="abcde", name="a.txt"), index=0)
    assert "base64" in str(exc.value)


def test_missing_name_rejected() -> None:
    with pytest.raises(AttachmentError):
        validate_file(FileAttachment(data=_b64("x"), name="   "), index=0)


def test_data_url_prefix_is_accepted() -> None:
    attachment = FileAttachment(
        data=f"data:text/plain;base64,{_b64('hello')}",
        name="a.txt",
    )
    assert validate_file(attachment, index=0).text == "hello"


# ---------------------------------------------------------------------------
# 与现有能力共存
# ---------------------------------------------------------------------------


def test_plain_text_content_still_string() -> None:
    assert build_openai_messages([_user("你好")]) == [
        {"role": "user", "content": "你好"}
    ]


def test_image_only_content_still_image_part() -> None:
    content = to_openai_content(
        "", [message_module.validate_image(
            ImageAttachment(data=PNG_BASE64, mime_type="image/png"), index=0
        )]
    )
    assert isinstance(content, list)
    assert [part["type"] for part in content] == ["image_url"]


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return TestClient(app)


def _payload(files: list[dict]) -> dict:
    return {
        "messages": [{"role": "user", "content": "看看文件", "files": files}],
        "api_base": "https://example.com/v1",
        "model": "gpt-4o-mini",
    }


def test_api_rejects_unsupported_file(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json=_payload([{"data": _b64("x"), "name": "report.docx"}]),
    )
    assert response.status_code == 400
    assert "不支持的文件类型" in response.json()["detail"]


def test_api_rejects_broken_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json=_payload([{"data": _b64(b"not a pdf"), "name": "report.pdf"}]),
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_api_rejects_files_on_assistant_role(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={
            "messages": [
                {
                    "role": "assistant",
                    "content": "x",
                    "files": [{"data": _b64("x"), "name": "a.txt"}],
                }
            ],
            "api_base": "https://example.com/v1",
            "model": "gpt-4o-mini",
        },
    )
    assert response.status_code == 400
    assert "只有用户消息" in response.json()["detail"]
