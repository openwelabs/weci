"""SSE 事件编码。

前后端约定的事件类型：
  start  开始生成
  delta  文本增量 {"text": "..."}
  error  错误 {"message": "..."}
  done   正常结束
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
