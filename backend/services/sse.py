"""SSE 事件编码。

前后端约定的事件类型：
  start      开始 {"model", "mode"}
  thinking   Agent 当前步骤 {"step", "max_steps"}
  tool_call  工具调用（running 含参数；done / error 含输出、耗时）
  delta      文本增量 {"text": "..."}
  max_steps  达到最大步数 {"message", "steps"}
  error      错误 {"message": "..."}
  done       正常结束 {"steps"}
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
