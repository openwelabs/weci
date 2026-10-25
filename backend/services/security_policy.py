"""Agent 命令执行安全策略（只记录，不拦截）。

本模块负责判定命令 / 工具的**风险状态**，用于审计与 UI 展示。它不再位于
执行链的拦截位置：

    Agent → Tool Registry → Tool → OS

判定结果会被记录（模式、风险等级、原因），但不会在执行前拒绝任何调用。
真正的成功与否只由操作系统权限、文件系统与命令本身决定。

三种模式（仅影响记录方式）：

  - ``safe``        记录每条命令“原本需要确认”
  - ``auto_review`` 记录安全 / 危险判定
  - ``risky``       （默认）记录为全放开

无论哪种模式，WeCi 都不会阻止命令或工具执行。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.config import PROJECT_DIR
from backend.services.command_risk import (
    RISK_DANGEROUS,
    RISK_SAFE,
    CommandRisk,
    CommandRiskAnalyzer,
)
from backend.services.permissions import redact

logger = logging.getLogger("weci.security")

DATA_DIR = PROJECT_DIR / ".weci"
DEFAULT_SECURITY_FILE = DATA_DIR / "security.json"

MODE_SAFE = "safe"
MODE_AUTO_REVIEW = "auto_review"
MODE_RISKY = "risky"
# 默认全放开：所有命令与工具直接执行，模式仅记录状态，不阻止执行
DEFAULT_SECURITY_MODE = MODE_RISKY

SECURITY_MODES: dict[str, dict[str, str]] = {
    MODE_SAFE: {
        "id": MODE_SAFE,
        "label": "Safe",
        "title": "安全模式（仅记录）",
        "description": "Records every command as requiring review. Does not block.",
        "detail": "记录每条命令的判定状态（原本需要确认），但不会阻止执行。",
    },
    MODE_AUTO_REVIEW: {
        "id": MODE_AUTO_REVIEW,
        "label": "Auto Review",
        "title": "自动审查（仅记录）",
        "description": "Records safe / dangerous classification. Does not block.",
        "detail": "记录命令的风险判定（安全 / 危险），但不会阻止执行。",
    },
    MODE_RISKY: {
        "id": MODE_RISKY,
        "label": "Risky",
        "title": "全放开（默认）",
        "description": "Everything runs. Nothing is intercepted.",
        "detail": (
            "所有命令与工具直接执行，不拦截。模式只用于记录状态；能否成功只取决于"
            "操作系统权限、文件系统与命令本身。Stop、Tool Timeout、Loop Detection "
            "与 Context 管理继续生效。"
        ),
    },
}


@dataclass
class PolicyDecision:
    """一次策略判定结果。"""

    mode: str
    risk: str
    requires_confirmation: bool
    reason: str = ""
    matched: list[str] = field(default_factory=list)
    command: str = ""
    cwd: str = ""
    tool: str = ""
    kind: str = "command"  # command / tool

    @property
    def dangerous(self) -> bool:
        return self.risk == RISK_DANGEROUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "risk": self.risk,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
            "matched": list(self.matched),
            "command": self.command,
            "cwd": self.cwd,
            "tool": self.tool,
            "kind": self.kind,
        }


class SecurityPolicyManager:
    """持有当前安全模式，并把命令 / 工具风险转成“是否需要确认”。"""

    def __init__(
        self,
        analyzer: CommandRiskAnalyzer | None = None,
        config_path: str | Path | None = None,
        *,
        default_mode: str = DEFAULT_SECURITY_MODE,
    ) -> None:
        self.analyzer = analyzer or CommandRiskAnalyzer()
        self.config_path = Path(config_path) if config_path else DEFAULT_SECURITY_FILE
        self._mode = default_mode if default_mode in SECURITY_MODES else DEFAULT_SECURITY_MODE
        self._load()

    # ------------------------------------------------------------------
    # 模式
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> bool:
        value = str(mode or "").strip().lower()
        if value not in SECURITY_MODES:
            return False
        if value == self._mode:
            return True
        self._mode = value
        self._persist()
        logger.info("security mode changed to %s", value)
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "modes": [dict(item) for item in SECURITY_MODES.values()],
            "default_mode": DEFAULT_SECURITY_MODE,
        }

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def evaluate_command(
        self,
        command: str | Iterable[str] | None,
        *,
        cwd: str = "",
        tool: str = "run_command",
    ) -> PolicyDecision:
        analysis: CommandRisk = self.analyzer.analyze(command)
        display = _display_command(command)
        return self._apply(
            analysis,
            cwd=cwd,
            tool=tool,
            kind="command",
            command=display,
        )

    def evaluate_tool(
        self,
        tool_name: str,
        arguments: Any,
        *,
        declared_risk: str | None = None,
    ) -> PolicyDecision | None:
        """评估非命令工具的声明风险。

        返回 ``None`` 表示该工具没有声明风险，交给 Permission Manager 决定。
        未来 Plugin / MCP Tool 只要声明 ``Tool.risk``，就会自动进入同一套策略。
        """
        arguments = arguments if isinstance(arguments, dict) else {}
        if tool_name == "run_command":
            command = arguments.get("command")
            cwd = str(arguments.get("cwd") or "")
            return self.evaluate_command(command, cwd=cwd, tool=tool_name)

        risk = str(declared_risk or "").strip().lower()
        if risk not in (RISK_SAFE, RISK_DANGEROUS):
            if self._mode != MODE_RISKY:
                return None
            # Risky（默认）：未声明风险的工具同样自动执行，不拦截
            return PolicyDecision(
                mode=MODE_RISKY,
                risk=RISK_SAFE,
                requires_confirmation=False,
                reason="Risky 模式：所有工具自动执行，不拦截。",
                tool=tool_name,
                kind="tool",
            )

        analysis = CommandRisk(risk=risk, reason="", matched=[])
        return self._apply(analysis, tool=tool_name, kind="tool")

    def _apply(
        self,
        analysis: CommandRisk,
        *,
        tool: str,
        kind: str,
        command: str = "",
        cwd: str = "",
    ) -> PolicyDecision:
        mode = self._mode
        reason = analysis.reason
        if mode == MODE_SAFE:
            requires = True
            reason = "Safe 模式：所有命令都需要你确认后才会执行。" + (
                f"（{analysis.reason}）" if analysis.reason else ""
            )
        elif mode == MODE_RISKY:
            requires = False
        else:  # auto_review
            requires = analysis.dangerous

        return PolicyDecision(
            mode=mode,
            risk=analysis.risk,
            requires_confirmation=requires,
            reason=reason if requires or analysis.dangerous else "",
            matched=list(analysis.matched),
            command=command,
            cwd=cwd,
            tool=tool,
            kind=kind,
        )

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.config_path.is_file():
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("加载安全策略配置失败，使用默认模式：%s", exc)
            return
        mode = raw.get("mode") if isinstance(raw, dict) else None
        if mode in SECURITY_MODES:
            self._mode = str(mode)

    def _persist(self) -> None:
        payload = {"version": 1, "mode": self._mode}
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
            logger.warning("持久化安全策略配置失败：%s", exc)


def _display_command(command: str | Iterable[str] | None) -> str:
    if command is None:
        return ""
    text = command if isinstance(command, str) else " ".join(str(item) for item in command)
    return redact(text.strip())[:1000]


__all__ = [
    "SecurityPolicyManager",
    "PolicyDecision",
    "SECURITY_MODES",
    "MODE_SAFE",
    "MODE_AUTO_REVIEW",
    "MODE_RISKY",
    "DEFAULT_SECURITY_MODE",
]
