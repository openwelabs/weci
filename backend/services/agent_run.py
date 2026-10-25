"""Agent Run 管理层：把一次 Agent 执行升级为有生命周期、状态和历史的 Run。

职责边界：

    Agent UI
        ↓
    Agent Run Manager（本模块：状态机、工具记录、持久化、取消信号）
        ↓
    Agent Runtime / Agent Loop（agent.py）
        ↓
    Tool Registry → Tools / Plugins / MCP

Run 与 SSE 解耦：SSE 只是事件传输层；Run 的状态保存在本管理器中，
即使前端刷新 / SSE 中断，Run 状态也不会丢失或被误判为 completed。

持久化复用项目现有 JSON 文件模式（与 memory / providers / mcp 一致），
保存在 ``.weci/agent_runs.json``，不引入数据库。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from backend.config import PROJECT_DIR

logger = logging.getLogger("weci.agent_run")

DATA_DIR = PROJECT_DIR / ".weci"
DEFAULT_RUNS_FILE = DATA_DIR / "agent_runs.json"

RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
RUN_STATUSES: tuple[str, ...] = ("pending", "running", "completed", "failed", "cancelled")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# 合法状态转换
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

MAX_RUNS_PER_CONVERSATION = 100
# Run 中保存的工具输出上限（公开响应进一步截断，内部仍可保留）
MAX_RUN_TOOL_OUTPUT_CHARS = 200_000
MAX_RUN_RESULT_CHARS = 200_000
# 避免单个 Run 的事件日志无限增长；Unlimited Execution 下一次长 Run 可能
# 执行上百次工具调用，这里留出足够空间让完整过程可回溯。
MAX_RUN_EVENTS = 2_000

# 错误分类（UI 友好，不暴露敏感信息）
ERROR_TYPES = (
    "provider",
    "tool",
    "mcp",
    "workspace",
    "context",
    "cancelled",
    "network",
    "loop",
    "unknown",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    if not text or len(text) <= limit:
        return text or ""
    return text[:limit] + f"\n…（已截断，原 {len(text)} 字符）"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class ToolCallRecord:
    """一次工具调用的完整记录。"""

    id: str
    name: str
    status: Literal["running", "done", "error", "cancelled"]
    step: int
    arguments: Any = None
    output: str | None = None
    error: str | None = None
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    duration_ms: float | None = None
    # 权限审计：工具声明 / 实际使用的权限、判定结果与用户确认方式
    permissions: list[str] = field(default_factory=list)
    permission_result: str | None = None  # granted / denied / not_required
    user_confirmation: str | None = None  # once / run / deny / timeout / auto
    # 命令执行审计（Security Policy Manager）
    security_mode: str | None = None  # safe / auto_review / risky
    risk: str | None = None  # safe / dangerous
    risk_reason: str | None = None
    confirmation_required: bool | None = None
    command: str | None = None
    cwd: str | None = None
    exit_code: int | None = None
    timed_out: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        if isinstance(data.get("output"), str):
            data["output"] = _truncate(data["output"], 20_000)
        return data


@dataclass
class RunEvent:
    """Run 生命周期事件（统一事件模型）。"""

    type: str
    timestamp: str = field(default_factory=_now_iso)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "timestamp": self.timestamp, "data": self.data}


@dataclass
class AgentRun:
    """一次 Agent 执行的完整状态。"""

    id: str
    conversation_id: str
    status: RunStatus
    started_at: str
    finished_at: str | None = None
    current_step: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    result: str = ""
    error: str | None = None
    error_type: str | None = None
    mode: str = "agent"
    model: str = ""
    provider_id: str = ""
    steps: int = 0
    events: list[RunEvent] = field(default_factory=list)

    def add_event(self, event_type: str, **data: Any) -> None:
        if len(self.events) >= MAX_RUN_EVENTS:
            # 保留最早与最新，丢弃中间，避免无限增长
            self.events = self.events[:1] + self.events[-(MAX_RUN_EVENTS // 2) :]
        self.events.append(RunEvent(type=event_type, data=data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_step": self.current_step,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "result": _truncate(self.result, MAX_RUN_RESULT_CHARS),
            "error": self.error,
            "error_type": self.error_type,
            "mode": self.mode,
            "model": self.model,
            "provider_id": self.provider_id,
            "steps": self.steps,
            "events": [ev.to_dict() for ev in self.events],
        }

    def public_dict(self) -> dict[str, Any]:
        """可安全返回前端的结构（不包含 API Key）。"""
        data = self.to_dict()
        data["tool_calls"] = [tc.public_dict() for tc in self.tool_calls]
        # 结果摘要用于历史列表预览
        data["result_summary"] = (
            _truncate(self.result.strip(), 200).splitlines()[0] if self.result else ""
        )
        data["tool_count"] = len(self.tool_calls)
        data["duration_ms"] = self._duration_ms()
        return data

    def _duration_ms(self) -> float | None:
        if not self.started_at:
            return None
        end = self.finished_at or _now_iso()
        try:
            start = datetime.fromisoformat(self.started_at)
            finish = datetime.fromisoformat(end)
            return round((finish - start).total_seconds() * 1000, 1)
        except ValueError:
            return None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentRun":
        tool_calls = [
            ToolCallRecord(**tc)
            for tc in (raw.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        events = [
            RunEvent(type=ev["type"], timestamp=ev.get("timestamp", _now_iso()), data=ev.get("data") or {})
            for ev in (raw.get("events") or [])
            if isinstance(ev, dict) and ev.get("type")
        ]
        return cls(
            id=str(raw["id"]),
            conversation_id=str(raw.get("conversation_id", "")),
            status=raw.get("status", "failed") if raw.get("status") in RUN_STATUSES else "failed",
            started_at=raw.get("started_at") or _now_iso(),
            finished_at=raw.get("finished_at"),
            current_step=int(raw.get("current_step") or 0),
            tool_calls=tool_calls,
            result=str(raw.get("result") or ""),
            error=raw.get("error"),
            error_type=raw.get("error_type"),
            mode=str(raw.get("mode") or "agent"),
            model=str(raw.get("model") or ""),
            provider_id=str(raw.get("provider_id") or ""),
            steps=int(raw.get("steps") or 0),
            events=events,
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class AgentRunManager:
    """管理 Agent Run 的生命周期、状态机与持久化。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_RUNS_FILE
        self._runs: dict[str, AgentRun] = {}
        self._by_conversation: dict[str, list[str]] = {}
        # 活跃 Run 的取消信号（不持久化）
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(str(run_id))

    def list_by_conversation(
        self, conversation_id: str, *, limit: int = 50
    ) -> list[AgentRun]:
        ids = self._by_conversation.get(str(conversation_id), [])
        runs = [self._runs[rid] for rid in ids if rid in self._runs]
        # 最新的在前
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    def get_current(self, conversation_id: str) -> AgentRun | None:
        for run in self.list_by_conversation(conversation_id, limit=MAX_RUNS_PER_CONVERSATION):
            if run.status in ("pending", "running"):
                return run
        return None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def create(
        self,
        *,
        conversation_id: str,
        model: str = "",
        provider_id: str = "",
        mode: str = "agent",
    ) -> AgentRun:
        run = AgentRun(
            id=f"run-{uuid.uuid4().hex[:12]}",
            conversation_id=str(conversation_id),
            status="pending",
            started_at=_now_iso(),
            model=model,
            provider_id=provider_id,
            mode=mode,
        )
        run.add_event("run.created", model=model, provider_id=provider_id, mode=mode)
        with self._lock:
            self._runs[run.id] = run
            ids = self._by_conversation.setdefault(run.conversation_id, [])
            ids.append(run.id)
            # 限制每个会话的历史数量
            if len(ids) > MAX_RUNS_PER_CONVERSATION:
                removed = ids[: len(ids) - MAX_RUNS_PER_CONVERSATION]
                for rid in removed:
                    self._runs.pop(rid, None)
                    self._cancel_events.pop(rid, None)
                self._by_conversation[run.conversation_id] = ids[-MAX_RUNS_PER_CONVERSATION:]
            self._persist()
        return run

    def start(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is None or not self._transition(run, "running"):
            return False
        run.add_event("run.started")
        self._persist()
        return True

    def set_step(self, run_id: str, step: int) -> None:
        run = self.get(run_id)
        if run is None:
            return
        run.current_step = int(step)
        run.add_event("step.started", step=run.current_step)
        self._persist()

    def add_tool_call(
        self,
        run_id: str,
        *,
        call_id: str,
        name: str,
        step: int,
        arguments: Any = None,
    ) -> None:
        run = self.get(run_id)
        if run is None:
            return
        run.tool_calls.append(
            ToolCallRecord(
                id=str(call_id),
                name=name,
                status="running",
                step=int(step),
                arguments=arguments,
            )
        )
        run.add_event("tool.called", id=call_id, name=name, step=step)
        self._persist()

    def record_tool_permission(
        self,
        run_id: str,
        call_id: str,
        *,
        permissions: list[str],
        result: str,
        user_confirmation: str | None = None,
        event: str = "permission.granted",
        detail: str = "",
    ) -> None:
        """记录工具调用的权限判定结果，供 Run History 审计。"""
        run = self.get(run_id)
        if run is None:
            return
        tc = next((c for c in run.tool_calls if c.id == str(call_id)), None)
        if tc is not None:
            tc.permissions = [str(p) for p in permissions]
            tc.permission_result = result
            tc.user_confirmation = user_confirmation
        run.add_event(
            event,
            id=call_id,
            name=tc.name if tc is not None else "",
            permission=",".join(permissions),
            result=result,
            user_confirmation=user_confirmation,
            detail=detail,
        )
        self._persist()

    def record_permission_request(
        self,
        run_id: str,
        *,
        request_id: str,
        tool: str,
        permission: str,
        detail: str = "",
        kind: str = "permission",
        risk: str = "",
        reason: str = "",
        command: str = "",
        mode: str = "",
    ) -> None:
        """用户确认请求已下发前端。"""
        run = self.get(run_id)
        if run is None:
            return
        run.add_event(
            "permission.requested",
            request_id=request_id,
            tool=tool,
            permission=permission,
            detail=detail,
            kind=kind,
            risk=risk,
            reason=reason,
            command=command,
            mode=mode,
        )
        self._persist()

    def record_tool_security(
        self,
        run_id: str,
        call_id: str,
        *,
        security: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        user_decision: str | None = None,
    ) -> None:
        """记录一次工具 / 命令的安全策略判定与执行事实，供 Run History 审计。

        ``security`` 来自 Security Policy Manager 的判定结果（mode / risk /
        reason / command / cwd / requires_confirmation）；``metadata`` 来自
        ToolResult（exit_code / cwd / timed_out）。两者都不包含 API Key。
        """
        run = self.get(run_id)
        if run is None:
            return
        tc = next((c for c in run.tool_calls if c.id == str(call_id)), None)
        if tc is None:
            return
        security = security if isinstance(security, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        if not security and not metadata and not user_decision:
            # 没有策略判定、命令执行事实或用户决策时不制造噪音事件
            return
        if security:
            tc.security_mode = str(security.get("mode") or "") or None
            tc.risk = str(security.get("risk") or "") or None
            tc.risk_reason = str(security.get("reason") or "") or None
            tc.confirmation_required = bool(security.get("requires_confirmation"))
            if security.get("command"):
                tc.command = str(security.get("command"))
        if metadata.get("command") and not tc.command:
            tc.command = str(metadata.get("command"))
        if metadata.get("cwd") or security.get("cwd"):
            tc.cwd = str(metadata.get("cwd") or security.get("cwd"))
        exit_code = metadata.get("exit_code")
        if isinstance(exit_code, int):
            tc.exit_code = exit_code
        if "timed_out" in metadata:
            tc.timed_out = bool(metadata.get("timed_out"))
        if user_decision:
            tc.user_confirmation = user_decision
        run.add_event(
            "tool.security",
            id=call_id,
            name=tc.name,
            mode=tc.security_mode,
            risk=tc.risk,
            reason=tc.risk_reason,
            confirmation_required=tc.confirmation_required,
            user_decision=user_decision,
            command=tc.command,
            cwd=tc.cwd,
            exit_code=tc.exit_code,
        )
        self._persist()

    def complete_tool_call(
        self,
        run_id: str,
        call_id: str,
        *,
        output: str = "",
        duration_ms: float | None = None,
        status: Literal["done", "cancelled"] = "done",
    ) -> None:
        run = self.get(run_id)
        if run is None:
            return
        tc = next((c for c in run.tool_calls if c.id == str(call_id)), None)
        if tc is None:
            return
        tc.status = status
        tc.output = _truncate(output or "", MAX_RUN_TOOL_OUTPUT_CHARS)
        tc.finished_at = _now_iso()
        tc.duration_ms = duration_ms
        run.add_event("tool.completed", id=call_id, name=tc.name, status=status)
        self._persist()

    def fail_tool_call(
        self,
        run_id: str,
        call_id: str,
        *,
        error: str,
        duration_ms: float | None = None,
    ) -> None:
        run = self.get(run_id)
        if run is None:
            return
        tc = next((c for c in run.tool_calls if c.id == str(call_id)), None)
        if tc is None:
            return
        tc.status = "error"
        tc.error = str(error)
        tc.finished_at = _now_iso()
        tc.duration_ms = duration_ms
        run.add_event("tool.failed", id=call_id, name=tc.name, error=str(error))
        self._persist()

    def append_result(self, run_id: str, text: str) -> None:
        run = self.get(run_id)
        if run is None or not text:
            return
        run.result += text
        # 不每次持久化（delta 高频），由 complete/fail 统一落盘

    def complete(self, run_id: str, *, result: str | None = None, steps: int | None = None) -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        if result is not None:
            run.result = result
        if steps is not None:
            run.steps = int(steps)
        if not self._transition(run, "completed"):
            return False
        run.finished_at = _now_iso()
        run.add_event("run.completed", steps=run.steps)
        self._persist()
        return True

    def fail(self, run_id: str, *, error: str, error_type: str = "unknown") -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        run.error = str(error)
        run.error_type = error_type if error_type in ERROR_TYPES else "unknown"
        if not self._transition(run, "failed"):
            return False
        run.finished_at = _now_iso()
        run.add_event("run.failed", error_type=run.error_type, error=str(error))
        self._persist()
        return True

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        if run.status in _TERMINAL_STATUSES:
            return False
        run.error_type = "cancelled"
        if not self._transition(run, "cancelled"):
            return False
        run.finished_at = _now_iso()
        run.add_event("run.cancelled")
        # 唤醒等待中的 Agent Loop
        self._cancel_events.setdefault(run.id, asyncio.Event()).set()
        self._persist()
        return True

    # ------------------------------------------------------------------
    # 取消信号
    # ------------------------------------------------------------------
    def cancel_event(self, run_id: str) -> asyncio.Event:
        return self._cancel_events.setdefault(str(run_id), asyncio.Event())

    def is_cancelled(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is not None and run.status == "cancelled":
            return True
        event = self._cancel_events.get(str(run_id))
        return bool(event and event.is_set())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _transition(run: AgentRun, target: str) -> bool:
        if target not in _ALLOWED_TRANSITIONS.get(run.status, frozenset()):
            return False
        run.status = target  # type: ignore[assignment]
        return True

    def _load(self) -> None:
        if not self.config_path.exists():
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("加载 Agent Run 历史失败，将以空状态启动：%s", exc)
            return
        conversations = raw.get("conversations") if isinstance(raw, dict) else None
        if not isinstance(conversations, dict):
            return
        for conv_id, run_list in conversations.items():
            if not isinstance(run_list, list):
                continue
            ids: list[str] = []
            for item in run_list:
                if not isinstance(item, dict):
                    continue
                try:
                    run = AgentRun.from_dict(item)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("跳过损坏的 Agent Run：%s", exc)
                    continue
                # 服务重启后，残留的 running / pending 不可能仍在执行，
                # 统一标记为 failed，避免 UI 永久卡在运行中。
                if run.status in ("running", "pending"):
                    run.status = "failed"
                    run.error = "服务重启，运行已中断。"
                    run.error_type = "network"
                    run.finished_at = run.finished_at or _now_iso()
                self._runs[run.id] = run
                ids.append(run.id)
            if ids:
                self._by_conversation[str(conv_id)] = ids

    def _persist(self) -> None:
        conversations: dict[str, list[dict[str, Any]]] = {}
        for conv_id, ids in self._by_conversation.items():
            runs = [self._runs[rid].to_dict() for rid in ids if rid in self._runs]
            if runs:
                conversations[conv_id] = runs
        payload = {"conversations": conversations}
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            logger.warning("持久化 Agent Run 失败：%s", exc)
