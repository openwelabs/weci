#!/usr/bin/env python3
"""测试用的最小 MCP stdio Server（换行分隔 JSON-RPC 2.0）。

只实现测试需要的方法：initialize / tools/list / tools/call。
"""

from __future__ import annotations

import json
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "回显输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "fail",
        "description": "总是返回错误",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sleep",
        "description": "睡眠指定秒数",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
    },
    {
        "name": "crash",
        "description": "直接退出进程（模拟 Server 断开）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def result(request_id, value) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id, code, message) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                result(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": f"echo: {arguments.get('text', '')}"}
                        ],
                        "isError": False,
                    },
                )
            elif name == "fail":
                result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": "工具内部错误"}],
                        "isError": True,
                    },
                )
            elif name == "sleep":
                time.sleep(float(arguments.get("seconds", 1)))
                result(request_id, {"content": [{"type": "text", "text": "awake"}]})
            elif name == "crash":
                sys.exit(1)
            else:
                error(request_id, -32602, f"未知工具：{name}")
        elif request_id is not None:
            error(request_id, -32601, f"未知方法：{method}")


if __name__ == "__main__":
    main()
