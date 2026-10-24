"""Agent Run 查询与取消 API。

Run 的创建发生在 ``POST /api/chat``（Agent 模式）内部；本模块只负责
查询历史与主动取消，不暴露任何 API Key。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["agent-runs"])


def _manager(request: Request):
    manager = getattr(request.app.state, "agent_run_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Agent Run 管理器未初始化。")
    return manager


@router.get("/agent/runs")
async def list_runs(
    request: Request,
    conversation_id: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    manager = _manager(request)
    runs = manager.list_by_conversation(conversation_id, limit=limit)
    return {"runs": [run.public_dict() for run in runs]}


@router.get("/agent/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run 不存在：{run_id}")
    return {"run": run.public_dict()}


@router.post("/agent/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run 不存在：{run_id}")
    cancelled = manager.cancel(run_id)
    return {
        "ok": cancelled,
        "run_id": run_id,
        "status": manager.get(run_id).status if manager.get(run_id) else run.status,
    }
