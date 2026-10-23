"""OpenAI-compatible 流式客户端。

只依赖 httpx，不绑定任何厂商 SDK；任何兼容
`POST {base}/chat/completions` + SSE 的服务都可以接入。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.config import settings


class UpstreamError(Exception):
    """模型服务返回的错误（连接失败、鉴权失败、业务错误等）。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def build_chat_completions_url(api_base: str) -> str:
    """把用户填写的 Endpoint 规范化为 chat/completions 地址。

    兼容三种常见写法：
      https://api.openai.com               -> https://api.openai.com/v1/chat/completions
      https://api.openai.com/v1            -> https://api.openai.com/v1/chat/completions
      https://host/v1/chat/completions     -> 原样使用
    """
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise UpstreamError("API Endpoint 不能为空。")
    if not base.startswith(("http://", "https://")):
        raise UpstreamError("API Endpoint 必须以 http:// 或 https:// 开头。")
    if base.endswith("/chat/completions"):
        return base
    if urlparse(base).path in ("", "/"):
        return f"{base}/v1/chat/completions"
    return f"{base}/chat/completions"


def build_models_url(api_base: str) -> str:
    """把 Endpoint 规范化为 models 列表地址（用于“测试连接”）。"""
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise UpstreamError("API Endpoint 不能为空。")
    if not base.startswith(("http://", "https://")):
        raise UpstreamError("API Endpoint 必须以 http:// 或 https:// 开头。")
    if base.endswith("/chat/completions"):
        return base[: -len("/chat/completions")] + "/models"
    if urlparse(base).path in ("", "/"):
        return f"{base}/v1/models"
    return f"{base}/models"


def _extract_error_message(body: str, status_code: int) -> str:
    """尽量从上游错误响应中提取可读信息。"""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str) and error:
                return error
            if data.get("message"):
                return str(data["message"])
    except (ValueError, TypeError):
        pass
    text = body.strip()
    if len(text) > 300:
        text = text[:300] + "…"
    return text or f"模型服务返回了 HTTP {status_code}。"


async def stream_chat(
    messages: list[dict[str, str]],
    *,
    api_base: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """向上游发起流式请求，逐段 yield 文本增量。"""
    url = build_chat_completions_url(api_base)

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(
        connect=settings.connect_timeout,
        read=settings.read_timeout,
        write=30.0,
        pool=settings.connect_timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise UpstreamError(
                        _extract_error_message(body, response.status_code),
                        status_code=response.status_code,
                    )

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        return

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(chunk, dict) and chunk.get("error"):
                        error = chunk["error"]
                        message = (
                            error.get("message")
                            if isinstance(error, dict)
                            else str(error)
                        )
                        raise UpstreamError(message or "模型服务返回了错误。")

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content

    except UpstreamError:
        raise
    except httpx.TimeoutException as exc:
        raise UpstreamError("模型服务响应超时，请稍后重试。") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(f"无法连接到模型服务：{exc}") from exc


def _auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


# ---------------------------------------------------------------------------
# Agent 模式：带工具调用的对话
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """从模型响应中解析出的一次工具调用。"""

    id: str
    name: str
    arguments: str  # JSON 字符串，由 Registry 解析校验


def _build_payload(
    messages: list[dict[str, Any]],
    *,
    model: str,
    temperature: float,
    max_tokens: int | None,
    stream: bool,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


async def stream_chat_with_tools(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    api_base: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """带工具的流式请求。

    Yield 两种事件：
      - {"delta": str}             文本增量（最终回答）
      - {"tool_calls": [ToolCall]} 本轮到的工具调用（在流结束时一次性 yield）

    若模型服务不支持流式 tool calling（返回 400），自动退回非流式请求，
    把最终文本或工具调用同样以这两种事件返回，保证上层 Agent Loop 逻辑一致。
    """
    url = build_chat_completions_url(api_base)
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(
        connect=settings.connect_timeout,
        read=settings.read_timeout,
        write=30.0,
        pool=settings.connect_timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream(
                "POST",
                url,
                json=_build_payload(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    tools=tools,
                ),
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise UpstreamError(
                        _extract_error_message(body, response.status_code),
                        status_code=response.status_code,
                    )

                # 增量解析 tool_calls
                calls: dict[int, dict[str, Any]] = {}
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk, dict) and chunk.get("error"):
                        error = chunk["error"]
                        message = (
                            error.get("message")
                            if isinstance(error, dict)
                            else str(error)
                        )
                        raise UpstreamError(message or "模型服务返回了错误。")

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    content = delta.get("content")
                    if content:
                        yield {"delta": content}

                    tool_calls_delta = delta.get("tool_calls")
                    if tool_calls_delta:
                        for tc in tool_calls_delta:
                            idx = int(tc.get("index", 0))
                            slot = calls.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]

                if calls:
                    ordered = [calls[i] for i in sorted(calls)]
                    yield {
                        "tool_calls": [
                            ToolCall(
                                id=c.get("id", ""),
                                name=c.get("name", ""),
                                arguments=c.get("arguments", ""),
                            )
                            for c in ordered
                        ]
                    }
                return

    except UpstreamError as exc:
        # 某些 OpenAI-compatible 服务不支持“流式 + tools”，退回非流式
        if exc.status_code == 400 and (
            "tool" in (exc.message or "").lower()
            or "function" in (exc.message or "").lower()
        ):
            async for event in _non_stream_with_tools(
                messages,
                tools=tools,
                api_base=api_base,
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield event
            return
        raise
    except httpx.TimeoutException:
        raise UpstreamError("模型服务响应超时，请稍后重试。")
    except httpx.HTTPError as exc:
        raise UpstreamError(f"无法连接到模型服务：{exc}")


async def _non_stream_with_tools(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    api_base: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
) -> AsyncIterator[dict[str, Any]]:
    """非流式兜底：把结果转成与流式一致的事件。"""
    url = build_chat_completions_url(api_base)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(
        connect=settings.connect_timeout,
        read=settings.read_timeout,
        write=30.0,
        pool=settings.connect_timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                url,
                json=_build_payload(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    tools=tools,
                ),
                headers=headers,
            )
            if response.status_code >= 400:
                body = response.content.decode("utf-8", errors="replace")
                raise UpstreamError(
                    _extract_error_message(body, response.status_code),
                    status_code=response.status_code,
                )
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls")
            if tool_calls:
                yield {
                    "tool_calls": [
                        ToolCall(
                            id=tc.get("id", ""),
                            name=(tc.get("function") or {}).get("name", ""),
                            arguments=(tc.get("function") or {}).get("arguments", ""),
                        )
                        for tc in tool_calls
                    ]
                }
            else:
                content = message.get("content")
                if content:
                    yield {"delta": content}
    except UpstreamError:
        raise
    except httpx.TimeoutException:
        raise UpstreamError("模型服务响应超时，请稍后重试。")
    except httpx.HTTPError as exc:
        raise UpstreamError(f"无法连接到模型服务：{exc}")


def _auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def check_connection(
    *, api_base: str, api_key: str, model: str
) -> dict[str, Any]:
    """测试与模型服务的连通性，返回结构化结果（不抛异常）。

    优先调用 OpenAI-compatible 的 `GET {base}/models`；
    若服务不支持该路由（404/405），则退回一次极小的 chat 请求。
    """
    result: dict[str, Any] = {"ok": False, "message": "", "latency_ms": None}
    started = time.perf_counter()

    def succeed(models: list[str] | None) -> dict[str, Any]:
        result["ok"] = True
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        result["models"] = models
        return result

    def fail(message: str) -> dict[str, Any]:
        result["ok"] = False
        result["message"] = message
        result["latency_ms"] = round((time.perf_counter() - started) * 1000)
        return result

    timeout = httpx.Timeout(
        connect=settings.connect_timeout,
        read=20.0,
        write=20.0,
        pool=settings.connect_timeout,
    )
    headers = _auth_headers(api_key)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # 1) 标准路径：GET /models
            response = await client.get(build_models_url(api_base), headers=headers)

            if response.status_code < 400:
                models: list[str] = []
                try:
                    data = response.json()
                    items = data.get("data") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        models = [
                            str(item["id"])
                            for item in items
                            if isinstance(item, dict) and item.get("id")
                        ][:60]
                except (ValueError, TypeError):
                    pass
                return succeed(models)

            body = response.content.decode("utf-8", errors="replace")
            if response.status_code in (401, 403):
                detail = _extract_error_message(body, response.status_code)
                return fail(f"API Key 无效或权限不足（HTTP {response.status_code}）：{detail}")

            if response.status_code not in (404, 405):
                return fail(_extract_error_message(body, response.status_code))

            # 2) 服务没有 /models：退回一次极小的非流式 chat 请求
            if not model:
                return fail(
                    "该服务未提供 /models 接口，且未填写 Model，无法进一步测试。"
                )
            chat_response = await client.post(
                build_chat_completions_url(api_base),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "stream": False,
                },
                headers={**headers, "Content-Type": "application/json"},
            )
            if chat_response.status_code < 400:
                return succeed(None)
            chat_body = chat_response.content.decode("utf-8", errors="replace")
            if chat_response.status_code in (401, 403):
                detail = _extract_error_message(chat_body, chat_response.status_code)
                return fail(
                    f"API Key 无效或权限不足（HTTP {chat_response.status_code}）：{detail}"
                )
            return fail(_extract_error_message(chat_body, chat_response.status_code))

    except UpstreamError as exc:
        return fail(exc.message)
    except httpx.TimeoutException:
        return fail("连接超时，请检查 Endpoint 是否可达。")
    except httpx.HTTPError as exc:
        return fail(f"无法连接到模型服务：{exc}")
