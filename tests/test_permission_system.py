"""统一权限 / 安全系统测试。

WeCi 不拦截任何操作：Permission Manager 与 Security Policy 只记录状态，
工具一律直接执行，成败由操作系统 / 工具本身决定。本文件覆盖：
  - 未授权 Tool 仍然执行
  - 显式 Deny 也不会阻止 Tool 执行
  - Workspace 不限制路径（绝对路径 / ``..`` / 符号链接 / Root 外路径）
  - Plugin Manifest 的能力声明仍然生效（插件契约）
  - MCP Tool 无需授权即可执行
  - 不产生 permission.denied 记录
  - 安全日志不含 API Key / Token / Cookie
  - Agent 不再弹出权限确认，直接执行工具
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

import backend.services.agent as agent_module
from backend.mcp.manager import McpTool
from backend.plugins.context import PluginContextImpl
from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRegistry
from backend.services.agent import run_agent
from backend.services.agent_run import AgentRunManager
from backend.services.context import ContextManager
from backend.services.message import ChatMessage
from backend.services.openai_client import ToolCall
from backend.services.permissions import PermissionManager, sanitize_env
from backend.services.workspace import WorkspaceGuard
from backend.tools.registry import ToolRegistry
from weci_sdk.permissions import DEFAULT_AUTO_ALLOW, PermissionDenied
from weci_sdk.tool import Tool, ToolResult


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------
class NetArgs(BaseModel):
    text: str = "hi"


class NetTool(Tool):
    name = "net_tool"
    description = "需要联网权限的测试工具"
    args_model = NetArgs
    permissions = ("network:access",)

    async def execute(self, args: NetArgs, context=None) -> ToolResult:
        return ToolResult.success(f"net:{args.text}")


class PlainTool(Tool):
    name = "plain_tool"
    description = "不需要权限的测试工具"
    args_model = NetArgs

    async def execute(self, args: NetArgs, context=None) -> ToolResult:
        return ToolResult.success(f"plain:{args.text}")


def _manager(tmp_path: Path, *, auto_allow=None) -> PermissionManager:
    return PermissionManager(
        config_path=tmp_path / "permissions.json",
        auto_allow=set(DEFAULT_AUTO_ALLOW) if auto_allow is None else auto_allow,
        confirm_timeout=1.0,
    )


def _registry(tmp_path: Path, manager: PermissionManager, *tools: Tool) -> ToolRegistry:
    registry = ToolRegistry(permission_manager=manager)
    for tool in tools:
        registry.register(tool)
    return registry


def _context(tmp_path: Path, *, permissions: list[str], manager=None) -> PluginContextImpl:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return PluginContextImpl(
        plugin_id="test-plugin",
        permissions=permissions,
        storage_dir=tmp_path / "storage",
        workspace_guard=WorkspaceGuard(workspace),
        permission_manager=manager,
    )


# ---------------------------------------------------------------------------
# 1. Tool 权限（Default Deny + 授权后放行）
# ---------------------------------------------------------------------------
def test_unauthorized_tool_still_runs(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())

    result = asyncio.run(registry.execute("net_tool", {"text": "a"}))

    assert result.ok is True
    assert result.output == "net:a"
    assert not (result.metadata or {}).get("permission_denied")


def test_tool_allowed_after_grant(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())
    assert manager.grant("tool:net_tool", "network:access")

    result = asyncio.run(registry.execute("net_tool", {"text": "a"}))

    assert result.ok is True
    assert result.output == "net:a"


def test_tool_without_permissions_always_runs(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, PlainTool())

    result = asyncio.run(registry.execute("plain_tool", {}))

    assert result.ok is True


def test_denying_principal_does_not_block(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())
    manager.grant("tool:net_tool", "network:access")
    manager.deny("tool:net_tool", "network:access")

    result = asyncio.run(registry.execute("net_tool", {}))

    assert result.ok is True
    assert result.output == "net:hi"


# ---------------------------------------------------------------------------
# 2. Workspace 路径（只做规范化，不做范围限制）
# ---------------------------------------------------------------------------
def test_workspace_allows_paths_outside_root(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    guard = WorkspaceGuard(workspace)

    expected = (tmp_path / "outside.txt").resolve()
    assert guard.for_read("../outside.txt") == expected
    assert guard.for_write(str(tmp_path / "outside.txt")) == expected
    assert guard.for_delete("../outside.txt") == expected

    # 符号链接与绝对路径同样直接解析，不做拒绝
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert guard.for_listing(str(link / "nested" / "file.txt")) == (
        target / "nested" / "file.txt"
    ).resolve()


def test_workspace_allows_internal_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    guard = WorkspaceGuard(workspace)

    target = guard.for_write("sub/dir/file.txt")
    assert target == (workspace / "sub" / "dir" / "file.txt").resolve()


def test_workspace_allows_root_write_and_delete(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    guard = WorkspaceGuard(workspace)

    assert guard.for_write(".") == workspace.resolve()
    assert guard.for_delete(".") == workspace.resolve()


def test_delete_requires_dedicated_permission(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("hi", encoding="utf-8")

    ctx_readonly = _context(tmp_path, permissions=["workspace:read"])
    with pytest.raises(PermissionDenied):
        ctx_readonly.delete_file("note.txt")
    assert target.exists()

    ctx_delete = _context(tmp_path, permissions=["workspace:delete"])
    assert ctx_delete.delete_file("note.txt") is True
    assert not target.exists()


def test_plugin_file_write_can_leave_workspace(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["workspace:write"])

    ctx.write_file("../escape.txt", "x")

    outside = tmp_path / "escape.txt"
    assert outside.exists()
    assert outside.read_text(encoding="utf-8") == "x"


# ---------------------------------------------------------------------------
# 3. Command 执行安全
# ---------------------------------------------------------------------------
def test_command_requires_permission(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=[])

    with pytest.raises(PermissionDenied):
        asyncio.run(ctx.run_command("echo hi"))


def test_command_runs_inside_workspace_and_records_exit_code(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["command:execute"])

    result = asyncio.run(ctx.run_command("echo hi"))

    assert result["exit_code"] == 0
    assert "hi" in result["stdout"]
    assert result["cwd"] == str((tmp_path / "workspace").resolve())
    assert result["timed_out"] is False


def test_command_cwd_can_leave_workspace(tmp_path: Path) -> None:
    """Workspace 只是默认工作目录：命令的 cwd 可以指向 Workspace 之外，
    是否允许由 Security Policy Manager 按安全模式决定。"""
    ctx = _context(tmp_path, permissions=["command:execute"])

    result = asyncio.run(ctx.run_command("pwd", cwd="../"))

    assert result["exit_code"] == 0
    assert result["cwd"] == str(tmp_path.resolve())


def test_command_timeout_is_reported(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["command:execute"])

    result = asyncio.run(ctx.run_command("sleep 5", timeout=0.3))

    assert result["timed_out"] is True
    assert result["exit_code"] == -1


def test_command_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    monkeypatch.setenv("WECI_API_KEY", "weci-secret")
    monkeypatch.setenv("SAFE_VISIBLE", "ok")

    env = sanitize_env()

    assert env["SAFE_VISIBLE"] == "ok"
    assert "OPENAI_API_KEY" not in env
    assert "TAVILY_API_KEY" not in env
    assert "WECI_API_KEY" not in env


# ---------------------------------------------------------------------------
# 4. Plugin 权限
# ---------------------------------------------------------------------------
def _write_plugin(base: Path, plugin_id: str, body: str, permissions: list[str]) -> Path:
    plugin_dir = base / plugin_id
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "0.1.0",
        "api_version": "0.1",
        "entry": "plugin.py",
        "permissions": permissions,
    }
    (plugin_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


_WRITE_PLUGIN_BODY = '''
from pydantic import BaseModel
from weci_sdk import Tool, WeCiPlugin
from weci_sdk.tool import ToolResult


class WriteArgs(BaseModel):
    path: str = "a.txt"
    content: str = "x"


class WriteTool(Tool):
    name = "{tool_name}"
    description = "写文件"
    args_model = WriteArgs
    permissions = ("workspace:write",)

    async def execute(self, args, context=None):
        context.write_file(args.path, args.content)
        return ToolResult.success("ok")


class P(WeCiPlugin):
    def get_tools(self):
        return [WriteTool()]
'''


def test_plugin_permission_is_enforced(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _write_plugin(
        builtin,
        "writer",
        _WRITE_PLUGIN_BODY.format(tool_name="writer_tool"),
        permissions=["filesystem.read"],
    )

    manager = _manager(tmp_path)
    registry = ToolRegistry(permission_manager=manager)
    plugin_registry = PluginRegistry(tool_registry=registry)
    loader = PluginLoader(
        plugin_registry,
        builtin_dir=builtin,
        user_dir=tmp_path / "user",
        state_file=tmp_path / "state.json",
        storage_dir=tmp_path / "storage",
        permission_manager=manager,
        workspace_guard=WorkspaceGuard(workspace),
    )
    loader.load_all()

    result = asyncio.run(registry.execute("writer_tool", {"path": "a.txt", "content": "x"}))

    # 工具层权限被 Permission Manager 拒绝
    assert result.ok is False
    # 即使绕过权限判定，插件自身也没有声明 workspace:write
    tool = registry.get("writer_tool")
    assert tool is not None
    direct = asyncio.run(tool.run({"path": "a.txt", "content": "x"}, _plugin_context(loader, "writer")))
    assert direct.ok is False
    assert "workspace:write" in (direct.error or "")
    assert not (workspace / "a.txt").exists()


def _plugin_context(loader: PluginLoader, plugin_id: str):
    entry = loader.get_plugin(plugin_id)
    assert entry is not None and entry.instance is not None
    return getattr(entry.instance, "_context")


def test_plugin_declared_low_risk_permission_is_auto_granted(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_plugin(
        builtin,
        "reader",
        _WRITE_PLUGIN_BODY.format(tool_name="reader_tool"),
        permissions=["filesystem.read"],
    )

    manager = _manager(tmp_path)
    registry = ToolRegistry(permission_manager=manager)
    plugin_registry = PluginRegistry(tool_registry=registry)
    loader = PluginLoader(
        plugin_registry,
        builtin_dir=builtin,
        user_dir=tmp_path / "user",
        state_file=tmp_path / "state.json",
        storage_dir=tmp_path / "storage",
        permission_manager=manager,
        workspace_guard=WorkspaceGuard(workspace),
    )
    loader.load_all()

    assert "workspace:read" in manager.grants_for("plugin:reader")["allow"]


# ---------------------------------------------------------------------------
# 5. MCP 权限
# ---------------------------------------------------------------------------
def _mcp_tool(name: str = "mcp__srv__echo") -> McpTool:
    async def caller(original_name, arguments):
        return {"content": [{"type": "text", "text": "ok"}]}

    return McpTool(
        name=name,
        description="echo",
        input_schema={"type": "object", "properties": {}},
        server_id="srv",
        server_name="Test Server",
        original_name="echo",
        caller=caller,
    )


def test_mcp_tool_runs_without_permission(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = ToolRegistry(permission_manager=manager)
    tool = _mcp_tool()
    registry.register(tool, principals=["mcp:srv", f"tool:{tool.name}"])

    result = asyncio.run(registry.execute(tool.name, {}))

    assert result.ok is True
    assert not (result.metadata or {}).get("permission_denied")


# ---------------------------------------------------------------------------
# 6. 安全日志
# ---------------------------------------------------------------------------
def test_permission_is_never_denied_by_registry(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())

    result = asyncio.run(registry.execute("net_tool", {}))

    assert result.ok is True
    events = [entry["event"] for entry in manager.recent_log()]
    assert "permission.denied" not in events


def test_log_redacts_secrets(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.log(
        "command.executed",
        command=(
            "curl -H 'Authorization: Bearer sk-abcdefgh12345678' "
            "-H 'Cookie: session=abc' https://x.test?api_key=supersecret"
        ),
    )

    entry = manager.recent_log(1)[0]
    serialized = json.dumps(entry, ensure_ascii=False)
    assert "sk-abcdefgh12345678" not in serialized
    assert "supersecret" not in serialized
    assert "session=abc" not in serialized


def test_log_redacts_via_generic_fields(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.log("permission.granted", password="hunter2", token="ghp_1234567890")

    serialized = json.dumps(manager.recent_log(1)[0], ensure_ascii=False)
    assert "hunter2" not in serialized
    assert "ghp_1234567890" not in serialized


# ---------------------------------------------------------------------------
# 7. Agent Loop 与 Agent Run 集成
# ---------------------------------------------------------------------------
def _tool_call_round(tool_name: str) -> list[dict]:
    return [
        {
            "tool_calls": [
                ToolCall(
                    id="call-1",
                    name=tool_name,
                    arguments=json.dumps({"text": "a"}),
                )
            ]
        }
    ]


def _fake_provider(rounds: list[list[dict]], monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"index": 0}

    async def fake_stream(_messages, **_kwargs):
        index = state["index"]
        state["index"] += 1
        for event in rounds[index]:
            yield event

    monkeypatch.setattr(agent_module, "stream_chat_with_tools", fake_stream)


def _chat_context() -> ContextManager:
    return ContextManager.from_chat_messages(
        [ChatMessage(role="user", content="hi")],
        system_prompt="test",
    )


def _run_agent_events(manager: PermissionManager, registry: ToolRegistry, run_id: str) -> list[dict]:
    async def run() -> list[dict]:
        events: list[dict] = []
        stream = run_agent(
            _chat_context(),
            api_base="http://x",
            api_key="k",
            model="m",
            temperature=0.0,
            max_tokens=None,
            registry=registry,
            permission_manager=manager,
            run_id=run_id,
        )
        async for event in stream:
            events.append(event)
        await stream.aclose()
        return events

    return asyncio.run(run())


def test_agent_runs_tool_without_permission_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())
    _fake_provider([_tool_call_round("net_tool"), [{"delta": "done"}]], monkeypatch)

    events = _run_agent_events(manager, registry, "run-1")

    assert "permission_request" not in [event["type"] for event in events]
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done and done[0]["permission_result"] == "granted"
    assert done[0]["user_confirmation"] == "auto"
    assert done[0]["output"] == "net:a"


def test_agent_does_not_prompt_twice_and_records_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    registry = _registry(tmp_path, manager, NetTool())
    _fake_provider([_tool_call_round("net_tool"), [{"delta": "done"}]], monkeypatch)

    events = _run_agent_events(manager, registry, "run-2")

    assert [e["type"] for e in events].count("permission_request") == 0
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert done and done[0]["permission_result"] == "granted"
    # 记录状态时不写入持久授权，也不依赖 Run 级授权
    assert manager.grants_for("tool:net_tool")["allow"] == []
    assert "run-2" not in manager._run_grants  # noqa: SLF001


def test_agent_run_records_permission_events(tmp_path: Path) -> None:
    manager = AgentRunManager(config_path=tmp_path / "runs.json")
    run = manager.create(conversation_id="c1", model="m")
    manager.start(run.id)

    manager.add_tool_call(
        run.id, call_id="call-1", name="net_tool", step=1, arguments={"text": "a"}
    )
    manager.record_permission_request(
        run.id,
        request_id="perm-1",
        tool="net_tool",
        permission="network:access",
        detail="访问网络",
    )
    manager.record_tool_permission(
        run.id,
        "call-1",
        permissions=["network:access"],
        result="granted",
        user_confirmation="run",
    )
    manager.complete_tool_call(run.id, "call-1", output="ok", duration_ms=1.0)

    saved = manager.get(run.id)
    assert saved is not None
    event_types = [ev.type for ev in saved.events]
    assert "permission.requested" in event_types
    assert "permission.granted" in event_types

    record = saved.tool_calls[0]
    assert record.permissions == ["network:access"]
    assert record.permission_result == "granted"
    assert record.user_confirmation == "run"

    public = saved.public_dict()
    assert public["tool_calls"][0]["permission_result"] == "granted"


def test_agent_run_records_denied_permission(tmp_path: Path) -> None:
    manager = AgentRunManager(config_path=tmp_path / "runs.json")
    run = manager.create(conversation_id="c1")
    manager.start(run.id)
    manager.add_tool_call(run.id, call_id="call-1", name="net_tool", step=1)

    manager.record_tool_permission(
        run.id,
        "call-1",
        permissions=["network:access"],
        result="denied",
        user_confirmation="deny",
        event="permission.denied",
        detail="用户拒绝",
    )

    saved = manager.get(run.id)
    assert saved is not None
    assert saved.tool_calls[0].permission_result == "denied"
    assert any(ev.type == "permission.denied" for ev in saved.events)


# ---------------------------------------------------------------------------
# 8. 权限目录 / 持久化
# ---------------------------------------------------------------------------
def test_catalog_marks_high_risk_default_ask(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    catalog = {item["permission"]: item for item in manager.catalog()}

    assert catalog["network:access"]["default"] == "ask"
    assert catalog["workspace:read"]["default"] == "allow"
    assert catalog["command:execute"]["risk"] == "high"


def test_grants_persist_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "permissions.json"
    first = PermissionManager(config_path=path, auto_allow=set())
    first.grant("plugin:writer", "workspace:write")

    second = PermissionManager(config_path=path, auto_allow=set())
    assert "workspace:write" in second.grants_for("plugin:writer")["allow"]


def test_reset_clears_grants(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.grant("tool:t", "network:access")

    manager.reset("tool:t", "network:access")

    assert manager.grants_for("tool:t")["allow"] == []


def test_legacy_permissions_are_normalized(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    decisions = manager.evaluate(["filesystem.read"], principals=["plugin:x"])

    assert decisions[0].permission == "workspace:read"
    assert decisions[0].allowed is True


def test_running_command_is_killed_on_cancel(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["command:execute"])

    async def run() -> None:
        task = asyncio.ensure_future(ctx.run_command("sleep 5"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_python_sys_path_unaffected() -> None:
    """权限层不应修改解释器状态（回归保护）。"""
    assert isinstance(sys.path, list)


# ---------------------------------------------------------------------------
# 9. 不拦截：Workspace 内外文件操作、Shell 命令、连续工具调用
# ---------------------------------------------------------------------------
_FULL_PLUGIN_PERMISSIONS = [
    "filesystem.read",
    "filesystem.write",
    "filesystem.delete",
    "process",
]


def _round_with_call(call_id: str, name: str, arguments: dict) -> list[dict]:
    return [
        {
            "tool_calls": [
                ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))
            ]
        }
    ]


def test_file_write_read_modify_delete_inside_and_outside(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=_FULL_PLUGIN_PERMISSIONS)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()

    cases = [
        (str(workspace / "inside.txt"), "inside"),
        (str(outside / "outside.txt"), "outside"),
        (str(tmp_path / "absolute.txt"), "absolute"),
        ("relative.txt", "relative"),
        ("nested/deep/file.txt", "nested"),
        ("../via-dotdot.txt", "dotdot"),
    ]
    for target, content in cases:
        ctx.write_file(target, content)
        assert ctx.read_file(target) == content

    assert (tmp_path / "via-dotdot.txt").exists()

    # 修改（覆盖）
    ctx.write_file(str(outside / "outside.txt"), "updated")
    assert (outside / "outside.txt").read_text(encoding="utf-8") == "updated"

    # 删除：Workspace 内外都可以
    for target in (
        str(outside / "outside.txt"),
        str(tmp_path / "absolute.txt"),
        str(workspace / "inside.txt"),
    ):
        assert ctx.delete_file(target) is True
        assert not Path(target).exists()


def test_file_operations_follow_symlinks(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=_FULL_PLUGIN_PERMISSIONS)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    ctx.write_file(str(link / "via-link.txt"), "through link")

    assert (real / "via-link.txt").read_text(encoding="utf-8") == "through link"
    assert ctx.read_file(str(link / "via-link.txt")) == "through link"
    assert any(item["name"] == "via-link.txt" for item in ctx.list_dir(str(link)))


def test_run_command_shell_operations_inside_and_outside(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["process"])
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()

    script = (
        "mkdir -p nested && "
        "touch nested/a.txt && "
        "cp nested/a.txt nested/b.txt && "
        "mv nested/b.txt nested/c.txt && "
        "rm -f nested/a.txt && "
        "mkdir -p inner && touch inner/tmp.txt && rm -f inner/tmp.txt"
    )

    result = asyncio.run(ctx.run_command(script, cwd="../outside"))

    assert result["exit_code"] == 0, result
    assert result["cwd"] == str(outside.resolve())
    assert (outside / "nested" / "c.txt").exists()
    assert not (outside / "nested" / "a.txt").exists()
    assert (outside / "inner").is_dir()

    # 再以 Workspace 为 cwd 执行一次，验证默认目录同样不受限
    inside = asyncio.run(ctx.run_command("mkdir -p local && touch local/x && rm local/x"))
    assert inside["exit_code"] == 0, inside
    assert inside["cwd"] == str(workspace.resolve())
    assert (workspace / "local").is_dir()


def test_run_command_can_write_python_files_with_arbitrary_cwd(tmp_path: Path) -> None:
    ctx = _context(tmp_path, permissions=["process"])
    target = tmp_path / "written_by_python.py"
    code = (
        "from pathlib import Path\n"
        f"Path({str(target)!r}).write_text('print(42)\\n', encoding='utf-8')\n"
        "print('ok')\n"
    )

    result = asyncio.run(ctx.run_command([sys.executable, "-c", code], cwd="/"))

    assert result["exit_code"] == 0, result
    assert result["cwd"] == str(Path("/").resolve())
    assert target.read_text(encoding="utf-8") == "print(42)\n"


def test_computer_tools_run_through_registry_despite_denied_permissions(
    tmp_path: Path,
) -> None:
    from backend.plugins.builtin.computer.plugin import (
        DeleteFileTool,
        ListDirTool,
        ReadFileTool,
        RunCommandTool,
        WriteFileTool,
    )

    manager = _manager(tmp_path, auto_allow=set())
    ctx = _context(tmp_path, permissions=_FULL_PLUGIN_PERMISSIONS, manager=manager)
    registry = ToolRegistry(permission_manager=manager)
    tools = [WriteFileTool(), ReadFileTool(), DeleteFileTool(), ListDirTool(), RunCommandTool()]
    for tool in tools:
        registry.register(tool, ctx)
        for permission in registry.permissions_for(tool.name):
            manager.deny(f"tool:{tool.name}", permission)

    outside = tmp_path / "outside"
    outside.mkdir()
    target = str(outside / "tool.txt")

    written = asyncio.run(registry.execute("write_file", {"path": target, "content": "hi"}))
    assert written.ok is True and Path(target).exists()
    assert asyncio.run(registry.execute("read_file", {"path": target})).output == "hi"

    listed = asyncio.run(registry.execute("list_dir", {"path": str(outside)}))
    assert listed.ok is True and "tool.txt" in listed.output

    command = asyncio.run(
        registry.execute("run_command", {"command": "pwd", "cwd": "/"})
    )
    assert command.ok is True
    assert command.metadata.get("cwd") == str(Path("/").resolve())

    deleted = asyncio.run(registry.execute("delete_file", {"path": target}))
    assert deleted.ok is True and not Path(target).exists()


def test_agent_runs_consecutive_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, auto_allow=set())
    registry = _registry(tmp_path, manager, NetTool(), PlainTool())
    _fake_provider(
        [
            _round_with_call("c1", "net_tool", {"text": "one"}),
            _round_with_call("c2", "plain_tool", {"text": "two"}),
            _round_with_call("c3", "net_tool", {"text": "three"}),
            [{"delta": "done"}],
        ],
        monkeypatch,
    )

    events = _run_agent_events(manager, registry, "run-multi")

    assert "permission_request" not in [event["type"] for event in events]
    done = [e for e in events if e["type"] == "tool_call" and e["status"] == "done"]
    assert [e["name"] for e in done] == ["net_tool", "plain_tool", "net_tool"]
    assert [e["output"] for e in done] == ["net:one", "plain:two", "net:three"]
    assert all(e["permission_result"] == "granted" for e in done)
