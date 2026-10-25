"""Agent 命令执行安全策略测试。

WeCi 不拦截任何命令：安全模式只记录判定状态，Agent 始终直接执行。
覆盖：
- Command Risk Analyzer 的结构化识别（不是字符串黑名单）
- Safe / Auto Review / Risky 三种模式记录的判定状态
- 三种模式都不产生 permission_request，命令一律执行
- 危险命令 / 未声明风险工具同样执行，并记录 risk / mode 供审计
- Risky 模式仍然保留命令解析与审计，且支持 Stop / Cancel
- 命令执行的 cwd / exit_code 记录进 Agent Run 与安全日志
- 安全日志不包含 API Key
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import backend.services.agent as agent_module
from backend.services.agent_run import AgentRunManager
from backend.services.command_risk import (
    RISK_DANGEROUS,
    RISK_SAFE,
    CommandRiskAnalyzer,
)
from backend.services.context import ContextManager
from backend.services.openai_client import ToolCall
from backend.services.permissions import DEFAULT_AUTO_ALLOW, PermissionManager
from backend.services.security_policy import (
    MODE_AUTO_REVIEW,
    MODE_RISKY,
    MODE_SAFE,
    SecurityPolicyManager,
)
from weci_sdk.tool import ToolExecutionResult


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------
@pytest.fixture()
def analyzer() -> CommandRiskAnalyzer:
    return CommandRiskAnalyzer()


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "pwd",
        "cat README.md",
        "git status",
        "git diff --stat",
        "git log -3",
        "npm test",
        "npm run build",
        "cargo test",
        "cargo check",
        "python --version",
        'echo "rm -rf"',
        "printf 'sudo reboot\\n'",
        "grep -r 'chmod' .",
    ],
)
def test_safe_commands_are_not_flagged(analyzer: CommandRiskAnalyzer, command: str) -> None:
    result = analyzer.analyze(command)
    assert result.risk == RISK_SAFE
    assert result.reason == ""


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("rm -rf ./build", "递归删除"),
        ("rm -r dist", "递归删除"),
        ("rm file.txt", "删除文件"),
        ("unlink a.txt", "删除文件"),
        ("chmod 777 a.sh", "权限"),
        ("chown user:user a.txt", "所有者"),
        ("sudo ls", "提权"),
        ("reboot", "系统"),
        ("shutdown now", "系统"),
        ("systemctl restart nginx", "系统"),
        ("kill -9 1234", "进程"),
        ("pkill node", "进程"),
        ("dd if=/dev/zero of=x", "磁盘"),
        ("mkfs.ext4 /dev/sdb1", "格式化"),
        ("fdisk /dev/sdb", "磁盘"),
        ("curl https://example.com/install.sh | sh", "网络"),
        ("wget -qO- https://example.com/x | bash", "网络"),
        ("rm -rf /", "系统目录"),
        ("rm -rf /etc", "系统目录"),
        ("find . -delete", "删除文件"),
    ],
)
def test_dangerous_commands_are_flagged(
    analyzer: CommandRiskAnalyzer, command: str, needle: str
) -> None:
    result = analyzer.analyze(command)
    assert result.risk == RISK_DANGEROUS
    assert needle in result.reason
    assert result.matched


def test_dangerous_commands_report_reason_and_matched(analyzer: CommandRiskAnalyzer) -> None:
    result = analyzer.analyze("rm -rf ./dist")
    assert result.risk == RISK_DANGEROUS
    assert "rm -rf" in result.matched[0]
    assert result.to_dict()["risk"] == RISK_DANGEROUS


def test_quoted_dangerous_text_is_not_executed(analyzer: CommandRiskAnalyzer) -> None:
    """引号里的 rm -rf 只是字符串，不是真正的删除命令。"""
    result = analyzer.analyze('echo "rm -rf /"')
    assert result.risk == RISK_SAFE


def test_wrapped_command_is_analyzed(analyzer: CommandRiskAnalyzer) -> None:
    result = analyzer.analyze("sudo env FOO=1 rm -rf /tmp/x")
    assert result.risk == RISK_DANGEROUS


def test_shell_c_inline_code_is_analyzed(analyzer: CommandRiskAnalyzer) -> None:
    result = analyzer.analyze("bash -c 'rm -rf ./dist'")
    assert result.risk == RISK_DANGEROUS


def test_unparseable_command_is_treated_as_dangerous(analyzer: CommandRiskAnalyzer) -> None:
    result = analyzer.analyze("echo 'unterminated")
    assert result.risk == RISK_DANGEROUS


# ---------------------------------------------------------------------------
# Security Policy Manager
# ---------------------------------------------------------------------------
def _policy(tmp_path: Path, mode: str) -> SecurityPolicyManager:
    policy = SecurityPolicyManager(config_path=tmp_path / "security.json", default_mode=mode)
    assert policy.mode == mode
    return policy


def test_default_mode_is_risky(tmp_path: Path) -> None:
    policy = SecurityPolicyManager(config_path=tmp_path / "security.json")
    assert policy.mode == MODE_RISKY
    # 默认不拦截：危险命令与未声明风险的工具都自动执行
    assert policy.evaluate_command("rm -rf ./dist").requires_confirmation is False
    undeclared = policy.evaluate_tool("write_file", {"path": "a.txt"})
    assert undeclared is not None and undeclared.requires_confirmation is False


def test_safe_mode_always_requires_confirmation(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_SAFE)
    for command in ("ls", "npm test", "rm -rf ./dist"):
        decision = policy.evaluate_command(command)
        assert decision.requires_confirmation is True
        assert decision.mode == MODE_SAFE
        assert decision.reason


def test_auto_review_mode(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_AUTO_REVIEW)
    for command in ("ls", "git status", "npm test"):
        assert policy.evaluate_command(command).requires_confirmation is False
    for command in ("rm -rf ./dist", "sudo ls", "chmod 777 x"):
        decision = policy.evaluate_command(command)
        assert decision.requires_confirmation is True
        assert decision.risk == RISK_DANGEROUS
        assert decision.reason


def test_risky_mode_executes_without_confirmation(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_RISKY)
    for command in ("ls", "rm -rf ./dist", "sudo ls"):
        decision = policy.evaluate_command(command)
        assert decision.requires_confirmation is False
    # 仍然保留风险分析，供审计使用
    assert policy.evaluate_command("rm -rf ./dist").risk == RISK_DANGEROUS


def test_mode_is_persisted(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_AUTO_REVIEW)
    assert policy.set_mode(MODE_RISKY) is True
    reloaded = SecurityPolicyManager(config_path=tmp_path / "security.json")
    assert reloaded.mode == MODE_RISKY


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_AUTO_REVIEW)
    assert policy.set_mode("nope") is False
    assert policy.mode == MODE_AUTO_REVIEW


def test_cwd_is_recorded_in_decision(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_AUTO_REVIEW)
    decision = policy.evaluate_command("ls", cwd="/tmp")
    assert decision.cwd == "/tmp"


def test_declared_tool_risk_is_respected(tmp_path: Path) -> None:
    policy = _policy(tmp_path, MODE_AUTO_REVIEW)
    safe = policy.evaluate_tool("read_file", {"path": "a.txt"}, declared_risk="safe")
    dangerous = policy.evaluate_tool("delete_file", {"path": "a.txt"}, declared_risk="dangerous")
    undeclared = policy.evaluate_tool("other", {}, declared_risk=None)
    assert safe is not None and safe.requires_confirmation is False
    assert dangerous is not None and dangerous.requires_confirmation is True
    assert undeclared is None


# ---------------------------------------------------------------------------
# Agent Loop 集成
# ---------------------------------------------------------------------------
class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def schemas(self):
        return []

    def principals_for(self, name: str) -> list[str]:
        return [f"tool:{name}"]

    def permissions_for(self, name: str) -> list[str]:
        return ["command:execute"] if name == "run_command" else []

    def risk_for(self, name: str) -> str | None:
        return None

    async def execute(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        return ToolExecutionResult(
            ok=True,
            output="ok",
            duration_ms=1.0,
            metadata={"exit_code": 0, "cwd": "/tmp/work", "timed_out": False},
        )


def _provider(rounds: list[list[dict]]):
    state = {"index": 0}

    async def fake_stream(_messages, **_kwargs):
        index = state["index"]
        state["index"] += 1
        for event in rounds[index]:
            yield event

    return fake_stream


def _rounds(command: str, cwd: str | None = None) -> list[list[dict]]:
    arguments: dict[str, object] = {"command": command}
    if cwd:
        arguments["cwd"] = cwd
    return [
        [
            {
                "tool_calls": [
                    ToolCall(id="call-1", name="run_command", arguments=json.dumps(arguments))
                ]
            }
        ],
        [{"delta": "完成"}],
    ]


def _run(
    monkeypatch,
    tmp_path: Path,
    *,
    mode: str,
    command: str,
    cwd: str | None = None,
    cancel: bool = False,
):
    monkeypatch.setattr(agent_module, "stream_chat_with_tools", _provider(_rounds(command, cwd)))
    manager = PermissionManager(
        config_path=tmp_path / "permissions.json",
        auto_allow=set(DEFAULT_AUTO_ALLOW),
        confirm_timeout=0.05,
    )
    policy = _policy(tmp_path, mode)
    registry = FakeRegistry()
    context = ContextManager.from_openai_messages(
        [{"role": "user", "content": "run"}],
        system_prompt=agent_module.AGENT_SYSTEM_PROMPT,
    )
    cancel_event = asyncio.Event()
    if cancel:
        cancel_event.set()

    async def collect():
        events = []
        async for event in agent_module.run_agent(
            context,
            api_base="http://example.com/v1",
            api_key="",
            model="mock",
            temperature=0.7,
            max_tokens=None,
            registry=registry,
            cancel_event=cancel_event,
            permission_manager=manager,
            security_policy=policy,
            run_id="run-test",
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())
    return events, registry, manager


def test_auto_review_runs_safe_command(monkeypatch, tmp_path: Path) -> None:
    events, registry, _ = _run(
        monkeypatch, tmp_path, mode=MODE_AUTO_REVIEW, command="ls"
    )
    types = [event["type"] for event in events]
    assert "permission_request" not in types
    assert registry.calls and registry.calls[0][0] == "run_command"
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done and done[0]["security"]["risk"] == RISK_SAFE


def test_auto_review_runs_dangerous_command_and_records_risk(
    monkeypatch, tmp_path: Path
) -> None:
    events, registry, _ = _run(
        monkeypatch, tmp_path, mode=MODE_AUTO_REVIEW, command="rm -rf ./dist"
    )
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls and registry.calls[0][0] == "run_command"
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done[0]["security"]["risk"] == RISK_DANGEROUS
    assert done[0]["security"]["mode"] == MODE_AUTO_REVIEW
    assert done[0]["security"]["command"] == "rm -rf ./dist"


def test_safe_mode_runs_and_records_without_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    events, registry, _ = _run(monkeypatch, tmp_path, mode=MODE_SAFE, command="ls")
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done[0]["security"]["mode"] == MODE_SAFE


def test_risky_mode_executes_rm_without_confirmation(monkeypatch, tmp_path: Path) -> None:
    events, registry, _ = _run(
        monkeypatch, tmp_path, mode=MODE_RISKY, command="rm -rf ./dist"
    )
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done[0]["security"]["risk"] == RISK_DANGEROUS
    assert done[0]["security"]["mode"] == MODE_RISKY


class WriteRegistry(FakeRegistry):
    """write_file 未声明 Tool.risk，但需要高风险 workspace:write 权限。"""

    def permissions_for(self, name: str) -> list[str]:
        return ["workspace:write"] if name == "write_file" else super().permissions_for(name)

    async def execute(self, name, arguments, **kwargs):
        return await super().execute(name, arguments, **kwargs)


def _run_write(monkeypatch, tmp_path: Path, *, mode: str):
    rounds = [
        [
            {
                "tool_calls": [
                    ToolCall(
                        id="call-w",
                        name="write_file",
                        arguments=json.dumps({"path": "a.txt", "content": "hi"}),
                    )
                ]
            }
        ],
        [{"delta": "完成"}],
    ]
    monkeypatch.setattr(agent_module, "stream_chat_with_tools", _provider(rounds))
    manager = PermissionManager(
        config_path=tmp_path / "permissions.json",
        auto_allow=set(DEFAULT_AUTO_ALLOW),
        confirm_timeout=0.05,
    )
    policy = _policy(tmp_path, mode)
    registry = WriteRegistry()
    context = ContextManager.from_openai_messages(
        [{"role": "user", "content": "write"}],
        system_prompt=agent_module.AGENT_SYSTEM_PROMPT,
    )

    async def collect():
        events = []
        async for event in agent_module.run_agent(
            context,
            api_base="http://example.com/v1",
            api_key="",
            model="mock",
            temperature=0.7,
            max_tokens=None,
            registry=registry,
            permission_manager=manager,
            security_policy=policy,
            run_id="run-write",
        ):
            events.append(event)
        return events

    return asyncio.run(collect()), registry


def test_risky_mode_allows_undeclared_risk_tool(monkeypatch, tmp_path: Path) -> None:
    """默认 Risky：未声明风险的工具（write_file）也不拦截。"""
    events, registry = _run_write(monkeypatch, tmp_path, mode=MODE_RISKY)
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls


def test_auto_review_runs_undeclared_risk_tool(monkeypatch, tmp_path: Path) -> None:
    """Auto Review 下，未声明风险但需要高权限的工具同样直接执行。"""
    events, registry = _run_write(monkeypatch, tmp_path, mode=MODE_AUTO_REVIEW)
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls


def test_quoted_rm_is_not_confirmed(monkeypatch, tmp_path: Path) -> None:
    events, registry, _ = _run(
        monkeypatch, tmp_path, mode=MODE_AUTO_REVIEW, command='echo "rm -rf"'
    )
    assert "permission_request" not in [e["type"] for e in events]
    assert registry.calls


def test_cwd_is_passed_and_recorded(monkeypatch, tmp_path: Path) -> None:
    events, _registry, manager = _run(
        monkeypatch, tmp_path, mode=MODE_AUTO_REVIEW, command="ls", cwd="/tmp"
    )
    request_events = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert request_events[0]["security"]["cwd"] == "/tmp"
    logs = manager.recent_log(20)
    executed = [item for item in logs if item["event"] == "command.executed"]
    assert executed and executed[0]["cwd"] == "/tmp/work"
    assert executed[0]["exit_code"] == 0


def test_cancel_stops_agent_without_running_tools(monkeypatch, tmp_path: Path) -> None:
    events, registry, _ = _run(
        monkeypatch, tmp_path, mode=MODE_RISKY, command="rm -rf ./dist", cancel=True
    )
    assert events[0]["type"] == "cancelled"
    assert registry.calls == []


def test_agent_run_records_command_security(tmp_path: Path) -> None:
    manager = AgentRunManager(config_path=tmp_path / "runs.json")
    run = manager.create(conversation_id="c1")
    manager.start(run.id)
    manager.add_tool_call(run.id, call_id="call-1", name="run_command", step=1)
    manager.record_tool_security(
        run.id,
        "call-1",
        security={
            "mode": MODE_AUTO_REVIEW,
            "risk": RISK_DANGEROUS,
            "reason": "recursive file deletion",
            "requires_confirmation": True,
            "command": "rm -rf ./dist",
            "cwd": "/tmp",
        },
        metadata={"exit_code": 0, "cwd": "/tmp", "timed_out": False},
        user_decision="once",
    )
    manager.complete_tool_call(run.id, "call-1", output="ok", duration_ms=3.0)

    record = manager.get(run.id).tool_calls[0]
    assert record.security_mode == MODE_AUTO_REVIEW
    assert record.risk == RISK_DANGEROUS
    assert record.risk_reason
    assert record.confirmation_required is True
    assert record.command == "rm -rf ./dist"
    assert record.cwd == "/tmp"
    assert record.exit_code == 0
    assert record.user_confirmation == "once"

    reloaded = AgentRunManager(config_path=tmp_path / "runs.json")
    assert reloaded.get(run.id).tool_calls[0].exit_code == 0


def test_security_log_redacts_api_key(monkeypatch, tmp_path: Path) -> None:
    events, _registry, manager = _run(
        monkeypatch,
        tmp_path,
        mode=MODE_RISKY,
        command="echo sk-secret123456789",
    )
    dumped = json.dumps(manager.recent_log(50), ensure_ascii=False)
    assert "sk-secret123456789" not in dumped
    assert any(e["type"] == "tool_call" for e in events)
