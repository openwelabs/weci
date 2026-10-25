"""WeCi 应用入口。

启动：
    python -m backend.main
或开发模式：
    uvicorn backend.main:app --reload

插件系统：
  - 启动时自动扫描并加载内置插件（backend/plugins/builtin/）与用户插件
    （~/.weci/plugins/），加载失败的插件不会影响 WeCi 启动。
  - Agent 只与 PluginRegistry.tool_registry 交互，不感知具体插件。
MCP：
  - 启动时连接 ``.weci/mcp_servers.json`` 中已启用的本地 stdio Server，
    其 Tools 同样注册进同一个 ToolRegistry，Agent 无感知。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.agent_runs import router as agent_runs_router
from backend.api.chat import router as chat_router
from backend.api.mcp import router as mcp_router
from backend.api.memory import router as memory_router
from backend.api.permissions import router as permissions_router
from backend.api.plugins import router as plugins_router
from backend.api.providers import router as providers_router
from backend.api.security import router as security_router
from backend.api.system import router as system_router
from backend.config import FRONTEND_DIR, settings
from backend.mcp.manager import McpManager
from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRegistry
from backend.services.agent_run import AgentRunManager
from backend.services.memory import MemoryStore
from backend.services.permissions import PermissionManager
from backend.services.providers import ProviderManager
from backend.services.security_policy import SecurityPolicyManager
from backend.services.workspace import WorkspaceGuard
from backend.tools.registry import ToolRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger("weci")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """启动时连接已启用的 MCP Server，关闭时断开全部连接。"""
    manager: McpManager | None = getattr(app.state, "mcp_manager", None)
    if manager is not None:
        await manager.start()
    try:
        yield
    finally:
        if manager is not None:
            await manager.stop()


def build_plugin_system(
    memory_store=None,
    permission_manager: PermissionManager | None = None,
    workspace_guard: WorkspaceGuard | None = None,
    security_policy: SecurityPolicyManager | None = None,
) -> tuple[PluginRegistry, PluginLoader]:
    """组合根：构建插件注册表与加载器，自动加载所有插件。"""
    # 所有工具（内置 / 插件 / MCP）执行前都经同一个 Permission Manager
    tool_registry = ToolRegistry(
        permission_manager=permission_manager, security_policy=security_policy
    )
    plugin_registry = PluginRegistry(tool_registry=tool_registry)
    loader = PluginLoader(
        plugin_registry,
        memory_store=memory_store,
        permission_manager=permission_manager,
        workspace_guard=workspace_guard,
    )

    logger.info("开始加载插件（内置 + 用户）...")
    loader.load_all()

    loaded = [p.id for p in plugin_registry.list_plugins() if p.status == "loaded"]
    failed = [p.id for p in plugin_registry.list_plugins() if p.status == "failed"]
    logger.info("已加载工具: %s", ", ".join(tool_registry.names()) or "无")
    if failed:
        logger.warning("加载失败的插件: %s", ", ".join(failed))
    logger.info("已启用插件: %s", ", ".join(loaded) or "无")

    return plugin_registry, loader


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.version,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    memory_store = MemoryStore()
    # 统一权限层：Tool Registry / Plugin / MCP 共用的唯一判定入口
    permission_manager = PermissionManager(
        confirm_timeout=settings.permission_confirm_timeout
    )
    workspace_guard = WorkspaceGuard(settings.workspace_root)
    # 命令执行安全策略：Safe / Auto Review / Risky（叠加在权限层之上）
    security_policy = SecurityPolicyManager(default_mode=settings.security_mode)
    plugin_registry, plugin_loader = build_plugin_system(
        memory_store,
        permission_manager=permission_manager,
        workspace_guard=workspace_guard,
        security_policy=security_policy,
    )
    app.state.memory_store = memory_store
    app.state.permission_manager = permission_manager
    app.state.security_policy = security_policy
    app.state.workspace_guard = workspace_guard
    app.state.plugin_registry = plugin_registry
    app.state.plugin_loader = plugin_loader
    # Agent 仍只看到 ToolRegistry，不感知插件
    app.state.tool_registry = plugin_registry.tool_registry

    # Provider 是模型配置的唯一来源：Chat / Agent / 多模态都从这里解析
    app.state.provider_manager = ProviderManager()

    # Agent Run 管理器：Agent 执行的生命周期、状态与历史（与 SSE 解耦）
    app.state.agent_run_manager = AgentRunManager()

    # MCP 与插件系统并列：都把工具注册进同一个 ToolRegistry
    app.state.mcp_manager = McpManager(
        plugin_registry.tool_registry, permission_manager=permission_manager
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(permissions_router, prefix="/api")
    app.include_router(security_router, prefix="/api")
    app.include_router(agent_runs_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(plugins_router, prefix="/api")
    app.include_router(mcp_router, prefix="/api")

    # 生产/本地一体启动：直接托管纯静态前端；前后端代码仍然完全分离
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    else:
        logger.warning("frontend directory not found: %s", FRONTEND_DIR)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
