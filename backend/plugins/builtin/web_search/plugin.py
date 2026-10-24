"""内置 Web Search 插件。

支持两种 Provider（通过环境变量选择）：
  - ``duckduckgo``（默认）：抓取 DuckDuckGo Lite 页面解析，**免 API Key**。
  - ``tavily``：需设置 ``TAVILY_API_KEY``。

通过环境变量 ``WECI_WEB_SEARCH_PROVIDER`` 切换。
未配置且默认 provider 不可用时，安全返回「未配置」，绝不伪造结果。
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel, Field

from weci_sdk import Tool, WeCiPlugin


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# DuckDuckGo Lite（免 key）
# --------------------------------------------------------------------------
_DDG_URL = "https://lite.duckduckgo.com/lite/"


class DuckDuckGoProvider(SearchProvider):
    """抓取 DuckDuckGo Lite 页面，解析结果。免 API Key。"""

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
            },
        ) as client:
            response = await client.post(
                _DDG_URL,
                data={"q": query, "kl": "us-en"},
            )
            response.raise_for_status()
        return self._parse(response.text, limit)

    @staticmethod
    def _parse(html: str, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        # DuckDuckGo Lite 的结果块：
        #   <a ... href="URL" class='result-link'>TITLE</a>
        # 后面紧跟 <td class='result-snippet'>SNIPPET</td>
        pattern = re.compile(
            r"""<a[^>]*href=(['"])([^'"]+)\1[^>]*class=(['"])result-link\3[^>]*>(.*?)</a>""",
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r"""<td[^>]*class=(['"])result-snippet\1[^>]*>(.*?)</td>""",
            re.DOTALL,
        )
        link_matches = list(pattern.finditer(html))
        snippet_matches = list(snippet_pattern.finditer(html))
        for i, m in enumerate(link_matches[:limit]):
            url = m.group(2)
            title = re.sub(r"<[^>]+>", "", m.group(4)).strip()
            snippet = ""
            if i < len(snippet_matches):
                snippet = re.sub(r"<[^>]+>", "", snippet_matches[i].group(2)).strip()
            if title and url and not url.startswith("//"):
                results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results


# --------------------------------------------------------------------------
# Tavily（需 TAVILY_API_KEY）
# --------------------------------------------------------------------------
class TavilyProvider(SearchProvider):
    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": self._api_key, "query": query, "max_results": limit},
            )
            response.raise_for_status()
            data = response.json()
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            )
            for item in data.get("results", [])
        ]


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="搜索关键词。")
    limit: int = Field(default=5, ge=1, le=10, description="返回结果数量，1-10。")


class WebSearchTool(Tool):
    name = "web_search"
    description = "通过搜索引擎查询互联网上的最新信息。当问题需要实时、最新或不确定的事实时使用。"
    args_model = WebSearchArgs
    permissions = ("network:access",)

    def __init__(self, provider: SearchProvider | None = None) -> None:
        self._provider = provider

    async def execute(self, args: WebSearchArgs, context=None) -> str:
        if self._provider is None:
            return (
                "web_search 工具当前未配置搜索 Provider，无法执行真实搜索。"
                "请基于已有知识回答，或告知用户需要联网搜索。"
            )
        try:
            results = await self._provider.search(args.query, args.limit)
        except Exception as exc:  # noqa: BLE001
            return f"搜索请求失败：{str(exc)[:200]}"
        if not results:
            return f"搜索“{args.query}”未返回结果。"
        lines = [f"搜索“{args.query}”的结果（共 {len(results)} 条）："]
        for i, item in enumerate(results, 1):
            lines.append(f"{i}. {item.title}")
            lines.append(f"   链接：{item.url}")
            if item.snippet:
                lines.append(f"   摘要：{item.snippet}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# fetch_url：抓取网页正文
# --------------------------------------------------------------------------
class FetchUrlArgs(BaseModel):
    url: str = Field(..., description="要抓取的网页 URL。", max_length=2000)
    max_chars: int = Field(default=8000, ge=200, le=30000, description="返回文本的最大字符数。")


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = "抓取指定 URL 的网页内容并提取正文文本。用于获取搜索结果链接里的详细信息。"
    args_model = FetchUrlArgs
    permissions = ("network:access",)

    async def execute(self, args: FetchUrlArgs, context=None) -> str:
        if not re.match(r"^https?://", args.url, re.IGNORECASE):
            return "URL 必须以 http:// 或 https:// 开头。"
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                    ),
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            ) as client:
                response = await client.get(args.url)
                response.raise_for_status()
            text = _extract_text(response.text)
        except Exception as exc:  # noqa: BLE001
            return f"抓取失败：{str(exc)[:200]}"
        if not text.strip():
            return "页面内容为空或无法提取文本。"
        if len(text) > args.max_chars:
            text = text[: args.max_chars] + "\n…（内容已截断）"
        return text


def _extract_text(html: str) -> str:
    """从 HTML 中提取可见正文文本（去掉 script/style/标签，压缩空白）。"""
    # 去掉 script、style、noscript、head
    text = re.sub(
        r"<(script|style|noscript|head|template)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 去掉 HTML 注释
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # 块级元素后加换行
    text = re.sub(r"</(p|div|br|li|h[1-6]|tr|section|article|header|footer|nav|aside)>", "\n", text, flags=re.IGNORECASE)
    # 去掉所有标签
    text = re.sub(r"<[^>]+>", "", text)
    # 解码常见 HTML 实体
    import html as html_lib

    text = html_lib.unescape(text)
    # 压缩空白行
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------
def _build_provider() -> SearchProvider | None:
    provider_name = os.getenv("WECI_WEB_SEARCH_PROVIDER", "duckduckgo").strip().lower()
    if provider_name == "tavily":
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            return None
        return TavilyProvider(api_key)
    if provider_name == "duckduckgo":
        return DuckDuckGoProvider()
    return None


class WebSearchPlugin(WeCiPlugin):
    def on_load(self, context) -> None:
        provider = _build_provider()
        self._search_tool = WebSearchTool(provider=provider)
        self._fetch_tool = FetchUrlTool()
        if provider is None:
            context.logger.warning("web_search: 未配置有效 Provider，搜索工具不可用。")
        else:
            context.logger.info("web_search: 使用 Provider %s", provider.__class__.__name__)

    def get_tools(self) -> list[Tool]:
        return [self._search_tool, self._fetch_tool]
