"""MCP（Model Context Protocol）客户端支持。

WeCi 作为 MCP Client，通过 stdio 连接本地 MCP Server，把其 Tools 注册到
统一的 ``ToolRegistry``，Agent 仍然只与 ToolRegistry 交互。
"""

from backend.mcp.client import McpError, StdioMcpClient
from backend.mcp.manager import McpConfigError, McpManager, McpServerConfig, McpTool

__all__ = [
    "McpError",
    "StdioMcpClient",
    "McpConfigError",
    "McpManager",
    "McpServerConfig",
    "McpTool",
]
