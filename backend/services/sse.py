"""SSE 事件编码。

前后端约定的事件类型：
  start         开始 {"model", "mode"}
  thinking      Agent 当前步骤 {"step"}（仅统计，不作为步数上限）
  tool_call     工具调用（running 含参数；done / error 含输出、耗时）
  loop_detected 检测到重复操作 {"code", "reason", "tool", "step", "detections"}
  delta         文本增量 {"text": "..."}
  cancelled     已取消（用户 Stop / Cancel）
  error         错误 {"message", "error_type"}
  done          正常结束 {"steps"}

Agent 采用 Unlimited Execution：``thinking`` 中的 ``step`` 与 ``done`` 中的
``steps`` 只用于统计展示，不存在最大步数。
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
