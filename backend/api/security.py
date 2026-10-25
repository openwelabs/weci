"""Agent 命令执行安全策略 API。

端点：
  GET  /api/security            当前模式 + 三种模式说明
  POST /api/security/mode       切换 Safe / Auto Review / Risky

安全模式只决定「什么时候需要用户确认」，不限制 Agent 能操作哪些路径。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["security"])


class ModePayload(BaseModel):
    mode: str = Field(..., max_length=32)


def _policy(request: Request):
    policy = getattr(request.app.state, "security_policy", None)
    if policy is None:
        raise HTTPException(status_code=503, detail="安全策略管理器未初始化。")
    return policy


@router.get("/security")
async def get_security(request: Request) -> dict[str, Any]:
    policy = _policy(request)
    snapshot = policy.snapshot()
    registry = getattr(request.app.state, "tool_registry", None)
    tools: list[dict[str, Any]] = []
    if registry is not None:
        for name in registry.names():
            risk = registry.risk_for(name)
            if risk:
                tools.append({"name": name, "risk": risk})
    snapshot["tools"] = tools
    return snapshot


@router.post("/security/mode")
async def set_security_mode(payload: ModePayload, request: Request) -> dict[str, Any]:
    policy = _policy(request)
    if not policy.set_mode(payload.mode):
        raise HTTPException(
            status_code=400, detail=f"未知安全模式：{payload.mode}"
        )
    snapshot = policy.snapshot()
    snapshot["ok"] = True
    return snapshot
