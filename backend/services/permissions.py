"""统一权限 / 安全管理层。

架构（所有工具必须经过这里，Chat / Agent / Plugin / MCP 不再各自判断）：

    Request
        ↓
    Permission Manager（本模块）
        ↓
    Tool Registry
        ↓
    Tool / Plugin / MCP

设计原则：
  - **Default Deny**：没有声明、也没有授权的权限默认禁止。
  - **最小权限**：工具只声明自己真正需要的权限（``Tool.permissions``）。
  - **可审计**：grant / deny / 命令执行 / 文件删除都会写入安全日志，
    且日志经过脱敏，绝不包含 API Key、Token、Cookie、Authorization。
  - **不硬编码进 Agent**：Agent Loop 只调用 Manager，不判断具体权限。

权限采用 ``<scope>:<action>`` 形式，例如 ``workspace:read``。插件 manifest 中
历史的扁平权限（``network`` / ``filesystem.read`` 等）通过别名自动映射到新权限，
保持向后兼容。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.config import PROJECT_DIR
from weci_sdk.permissions import (
    DEFAULT_AUTO_ALLOW,
    KNOWN_PERMISSIONS,
    LEGACY_ALIASES,
    PERMISSION_DESCRIPTIONS,
    PERMISSION_RISK,
    SCOPED_PERMISSIONS,
    normalize_permissions,
)

logger = logging.getLogger("weci.security")

DATA_DIR = PROJECT_DIR / ".weci"
DEFAULT_PERMISSION_FILE = DATA_DIR / "permissions.json"

GRANT_SCOPES: tuple[str, ...] = ("always", "run", "once")

# 等待用户确认的超时（秒）。超时即视为拒绝（Default Deny）。
DEFAULT_CONFIRM_TIMEOUT = 120.0

# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+=/]{8,}"),
    re.compile(r"(?i)\b(x-api-key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)\b(authorization\s*[:=]\s*)\S+"),
    re.compile(r"(?i)\b(cookie\s*[:=]\s*)\S+"),
    re.compile(r"(?i)\b(password\s*[:=]\s*)\S+"),
    re.compile(r"(?i)\b(token\s*[:=]\s*)\S+"),
)
_SECRET_ENV_KEYS = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|credential|cookie|authorization)"
)
# 敏感字段名：即使值本身看不出是密钥，也要整段脱敏
_SENSITIVE_FIELD = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|credential|cookie|authorization)"
)


def redact(text: str) -> str:
    """把文本中的密钥类信息替换为 ``***``。"""
    if not text:
        return ""
    result = str(text)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda m: f"{m.group(1)}***", result)
    return result


def sanitize_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """生成不包含任何密钥类变量的子进程环境。

    Provider 的 API Key、Token 等绝不能注入到 Agent 执行的命令里。
    """
    safe: dict[str, str] = {}
    for key, value in (env if env is not None else os.environ).items():
        if _SECRET_ENV_KEYS.search(key or ""):
            continue
        if (key or "").upper().startswith("WECI_"):
            continue
        safe[key] = value
    # 保证 PATH 一定存在，否则命令无法定位可执行文件
    if "PATH" not in safe and os.environ.get("PATH"):
        safe["PATH"] = os.environ["PATH"]
    return safe


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PermissionDecision:
    """一次权限判定结果。"""

    allowed: bool
    permission: str
    principal: str
    reason: str
    source: str  # default / granted / run / once / denied / timeout / invalid
    needs_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "permission": self.permission,
            "principal": self.principal,
            "reason": self.reason,
            "source": self.source,
        }

    @property
    def denied(self) -> bool:
        return not self.allowed


@dataclass
class PermissionRequest:
    """等待用户确认的权限请求。"""

    id: str
    run_id: str
    principal: str
    permission: str
    tool_name: str
    title: str
    detail: str
    created_at: str = field(default_factory=_now_iso)
    decision: str | None = None
    future: asyncio.Future | None = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "principal": self.principal,
            "permission": self.permission,
            "tool": self.tool_name,
            "title": self.title,
            "detail": self.detail,
            "created_at": self.created_at,
            "risk": PERMISSION_RISK.get(self.permission, "high"),
        }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class PermissionManager:
    """统一的权限判定、授权存储与确认请求管理。"""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        auto_allow: Iterable[str] | None = None,
        confirm_timeout: float = DEFAULT_CONFIRM_TIMEOUT,
    ) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_PERMISSION_FILE
        self._auto_allow: set[str] = (
            set(DEFAULT_AUTO_ALLOW) if auto_allow is None else set(auto_allow)
        )
        self.confirm_timeout = float(confirm_timeout)
        # principal -> {"allow": [...], "deny": [...]}
        self._grants: dict[str, dict[str, list[str]]] = {}
        # run_id -> 本次 Run 内临时授权的权限
        self._run_grants: dict[str, set[str]] = {}
        # request_id -> PermissionRequest
        self._requests: dict[str, PermissionRequest] = {}
        # 安全日志环形缓冲（供 UI 展示，已脱敏）
        self._log: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def evaluate(
        self,
        permissions: Iterable[str],
        *,
        principals: Iterable[str] | str,
        run_id: str = "",
        tool_name: str = "",
    ) -> list[PermissionDecision]:
        """逐个判定工具声明的权限。不会阻塞、不会弹确认。"""
        principal_list = (
            [principals] if isinstance(principals, str) else list(principals)
        )
        decisions: list[PermissionDecision] = []
        for permission in normalize_permissions(permissions):
            decisions.append(
                self._evaluate_one(
                    permission,
                    principal_list=principal_list,
                    run_id=run_id,
                    tool_name=tool_name,
                )
            )
        return decisions

    def _evaluate_one(
        self,
        permission: str,
        *,
        principal_list: list[str],
        run_id: str,
        tool_name: str,
    ) -> PermissionDecision:
        principal = principal_list[0] if principal_list else "global"

        def _decision(allowed: bool, reason: str, source: str, confirm: bool = False):
            return PermissionDecision(
                allowed=allowed,
                permission=permission,
                principal=principal,
                reason=reason,
                source=source,
                needs_confirmation=confirm,
            )

        # 1) 显式拒绝优先级最高
        for candidate in principal_list:
            if permission in self._grants.get(candidate, {}).get("deny", []):
                return _decision(
                    False, f"权限 {permission} 已被禁用，请在权限设置中重新授权。", "denied"
                )

        # 2) 会话级（本次 Run）授权
        if run_id and permission in self._run_grants.get(run_id, set()):
            return _decision(True, "", "run")

        # 3) 持久授权（含 global）
        for candidate in principal_list:
            if permission in self._grants.get(candidate, {}).get("allow", []):
                return _decision(True, "", "granted")

        # 4) 低风险默认放行
        if permission in self._auto_allow:
            return _decision(True, "", "default")

        # 5) 需要用户确认
        label = PERMISSION_DESCRIPTIONS.get(permission, permission)
        return _decision(
            False,
            f"需要用户授权：{label}（{permission}）",
            "confirm",
            confirm=True,
        )

    def check(
        self,
        permissions: Iterable[str],
        *,
        principals: Iterable[str] | str,
        run_id: str = "",
        tool_name: str = "",
    ) -> PermissionDecision:
        """便捷方法：返回第一个未通过判定的权限结果。"""
        for decision in self.evaluate(
            permissions, principals=principals, run_id=run_id, tool_name=tool_name
        ):
            if not decision.allowed:
                return decision
        return PermissionDecision(
            allowed=True,
            permission="",
            principal=principals if isinstance(principals, str) else (list(principals) or ["global"])[0],
            reason="",
            source="granted",
        )

    # ------------------------------------------------------------------
    # 确认请求
    # ------------------------------------------------------------------
    def create_request(
        self,
        *,
        permission: str,
        principals: Iterable[str] | str,
        run_id: str = "",
        tool_name: str = "",
        detail: str = "",
    ) -> PermissionRequest:
        principal = principals if isinstance(principals, str) else (
            (list(principals) or ["global"])[0]
        )
        label = PERMISSION_DESCRIPTIONS.get(permission, permission)
        title = f"Agent 请求执行：{label}"
        request = PermissionRequest(
            id=f"perm-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            principal=principal,
            permission=permission,
            tool_name=tool_name,
            title=title,
            detail=redact(detail or ""),
            future=asyncio.get_event_loop().create_future()
            if _has_running_loop()
            else None,
        )
        with self._lock:
            self._requests[request.id] = request
        return request

    async def wait(
        self, request: PermissionRequest, cancel_event: asyncio.Event | None = None
    ) -> PermissionDecision:
        """等待用户确认；超时 / 取消统一视为拒绝。"""
        principal = request.principal
        if request.future is None:
            return PermissionDecision(
                allowed=False,
                permission=request.permission,
                principal=principal,
                reason="当前环境不支持交互式授权，已按默认拒绝处理。",
                source="invalid",
            )

        waiters: list[asyncio.Future] = [request.future]
        cancel_future: asyncio.Future | None = None
        if cancel_event is not None:
            cancel_future = asyncio.ensure_future(cancel_event.wait())
            waiters.append(cancel_future)

        try:
            done, _ = await asyncio.wait(
                waiters, timeout=self.confirm_timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            if cancel_future is not None and not cancel_future.done():
                cancel_future.cancel()

        self._drop_request(request.id)

        if cancel_future is not None and cancel_future in done:
            return PermissionDecision(
                allowed=False,
                permission=request.permission,
                principal=principal,
                reason="执行已取消。",
                source="cancelled",
            )
        if request.future not in done:
            self.log("permission.timeout", permission=request.permission, principal=principal)
            return PermissionDecision(
                allowed=False,
                permission=request.permission,
                principal=principal,
                reason="等待用户授权超时，已按默认拒绝处理。",
                source="timeout",
            )

        decision = request.decision or "deny"
        if decision == "deny":
            self.log("permission.denied", permission=request.permission, principal=principal)
            return PermissionDecision(
                allowed=False,
                permission=request.permission,
                principal=principal,
                reason=f"用户拒绝了权限 {request.permission}。",
                source="denied",
            )

        # once → 只对本次调用有效；run → 本次 Run 内有效
        if decision == "run" and request.run_id:
            self.grant_for_run(request.run_id, request.permission)
        self.log(
            "permission.granted",
            permission=request.permission,
            principal=principal,
            scope=decision,
        )
        return PermissionDecision(
            allowed=True,
            permission=request.permission,
            principal=principal,
            reason="",
            source=decision,
        )

    def resolve(self, request_id: str, decision: str) -> bool:
        """由 REST API 调用：用户点击 Allow once / Allow for this Run / Deny。"""
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.future is None or request.future.done():
                return False
            request.decision = decision if decision in ("once", "run", "deny") else "deny"
            request.future.set_result(True)
        return True

    def pending_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                req.public_dict()
                for req in self._requests.values()
                if req.future is not None and not req.future.done()
            ]

    def _drop_request(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    # ------------------------------------------------------------------
    # 授权管理
    # ------------------------------------------------------------------
    def grant(self, principal: str, permission: str, scope: str = "always") -> bool:
        permission = str(permission).strip()
        if permission not in PERMISSION_RISK:
            return False
        principal = str(principal or "global")
        if scope == "run":
            self.grant_for_run(principal, permission)
            return True
        with self._lock:
            entry = self._grants.setdefault(principal, {"allow": [], "deny": []})
            entry.setdefault("allow", [])
            entry.setdefault("deny", [])
            if permission not in entry["allow"]:
                entry["allow"].append(permission)
            if permission in entry["deny"]:
                entry["deny"].remove(permission)
            self._persist()
        self.log("permission.granted", permission=permission, principal=principal, scope=scope)
        return True

    def deny(self, principal: str, permission: str) -> bool:
        permission = str(permission).strip()
        if permission not in PERMISSION_RISK:
            return False
        principal = str(principal or "global")
        with self._lock:
            entry = self._grants.setdefault(principal, {"allow": [], "deny": []})
            entry.setdefault("allow", [])
            entry.setdefault("deny", [])
            if permission not in entry["deny"]:
                entry["deny"].append(permission)
            if permission in entry["allow"]:
                entry["allow"].remove(permission)
            self._persist()
        self.log("permission.denied", permission=permission, principal=principal, scope="always")
        return True

    def reset(self, principal: str | None = None, permission: str | None = None) -> bool:
        with self._lock:
            if principal is None:
                self._grants.clear()
            elif permission is None:
                self._grants.pop(str(principal), None)
            else:
                entry = self._grants.get(str(principal))
                if entry is None:
                    return False
                for key in ("allow", "deny"):
                    if permission in entry.get(key, []):
                        entry[key].remove(permission)
                if not entry.get("allow") and not entry.get("deny"):
                    self._grants.pop(str(principal), None)
            self._persist()
        self.log("permission.reset", principal=principal or "*", permission=permission or "*")
        return True

    def grant_for_run(self, run_id: str, permission: str) -> None:
        if not run_id:
            return
        with self._lock:
            self._run_grants.setdefault(str(run_id), set()).add(str(permission))

    def grant_declared(self, principal: str, permissions: Iterable[str]) -> list[str]:
        """加载插件 / 连接 MCP Server 时登记其声明的权限。

        **只自动授予低风险权限**；高风险权限（写文件、删除、执行命令、联网、
        使用 MCP）仍然需要用户在首次调用时确认，避免「装上插件就拿到无限权限」。
        返回被自动授予的权限列表。
        """
        granted: list[str] = []
        for permission in normalize_permissions(permissions):
            if PERMISSION_RISK.get(permission) != "low":
                continue
            with self._lock:
                entry = self._grants.setdefault(str(principal), {"allow": [], "deny": []})
                if permission in entry.get("deny", []):
                    continue
                if permission not in entry.setdefault("allow", []):
                    entry["allow"].append(permission)
            granted.append(permission)
        if granted:
            self._persist()
            self.log("permission.granted", principal=principal, scope="declared", permissions=",".join(granted))
        return granted

    def grant_permissions_for_run(self, run_id: str, permissions: Iterable[str]) -> None:
        for permission in normalize_permissions(permissions):
            self.grant_for_run(run_id, permission)

    def end_run(self, run_id: str) -> None:
        """Run 结束后清理会话级授权与未决请求。"""
        if not run_id:
            return
        with self._lock:
            self._run_grants.pop(str(run_id), None)
            stale = [
                req.id
                for req in self._requests.values()
                if req.run_id == str(run_id)
            ]
            for request_id in stale:
                self._requests.pop(request_id, None)

    # ------------------------------------------------------------------
    # 查询 / 快照
    # ------------------------------------------------------------------
    def grants_for(self, principal: str) -> dict[str, list[str]]:
        entry = self._grants.get(str(principal), {})
        return {
            "allow": list(entry.get("allow", [])),
            "deny": list(entry.get("deny", [])),
        }

    def all_grants(self) -> dict[str, dict[str, list[str]]]:
        return {key: self.grants_for(key) for key in self._grants}

    def describe(self, permissions: Iterable[str]) -> list[dict[str, str]]:
        return [
            {
                "permission": permission,
                "description": PERMISSION_DESCRIPTIONS.get(permission, permission),
                "risk": PERMISSION_RISK.get(permission, "high"),
            }
            for permission in normalize_permissions(permissions)
        ]

    def catalog(self) -> list[dict[str, str]]:
        return [
            {
                "permission": permission,
                "description": PERMISSION_DESCRIPTIONS.get(permission, permission),
                "risk": PERMISSION_RISK.get(permission, "high"),
                "default": "allow" if permission in self._auto_allow else "ask",
            }
            for permission in sorted(PERMISSION_RISK)
        ]

    # ------------------------------------------------------------------
    # 安全日志
    # ------------------------------------------------------------------
    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        """记录安全事件。所有文本都会脱敏后落日志。"""
        entry: dict[str, Any] = {
            "event": event,
            "timestamp": _now_iso(),
        }
        for key, value in fields.items():
            if _SENSITIVE_FIELD.search(str(key)):
                entry[key] = "***"
            elif isinstance(value, str):
                entry[key] = redact(value)[:500]
            elif isinstance(value, (int, float, bool)) or value is None:
                entry[key] = value
            else:
                entry[key] = redact(str(value))[:500]
        with self._lock:
            self._log.append(entry)
            if len(self._log) > 500:
                del self._log[:-500]
        logger.info("security %s %s", event, json.dumps(entry, ensure_ascii=False))
        return entry

    def recent_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log[-limit:])[::-1]

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.config_path.is_file():
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("加载权限配置失败，将以默认策略启动：%s", exc)
            return
        grants = raw.get("grants") if isinstance(raw, dict) else None
        if not isinstance(grants, dict):
            return
        for principal, entry in grants.items():
            if not isinstance(entry, dict):
                continue
            self._grants[str(principal)] = {
                "allow": [p for p in entry.get("allow", []) if p in PERMISSION_RISK],
                "deny": [p for p in entry.get("deny", []) if p in PERMISSION_RISK],
            }

    def _persist(self) -> None:
        payload = {"version": 1, "grants": self.all_grants()}
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            logger.warning("持久化权限配置失败：%s", exc)


def _has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


__all__ = [
    "PermissionManager",
    "PermissionDecision",
    "PermissionRequest",
    "PERMISSION_RISK",
    "PERMISSION_DESCRIPTIONS",
    "SCOPED_PERMISSIONS",
    "KNOWN_PERMISSIONS",
    "LEGACY_ALIASES",
    "DEFAULT_AUTO_ALLOW",
    "normalize_permissions",
    "redact",
    "sanitize_env",
]
