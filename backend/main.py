"""WeCi 应用入口。

启动：
    python -m backend.main
或开发模式：
    uvicorn backend.main:app --reload

插件系统：
  - 启动时自动扫描并加载内置插件（backend/plugins/builtin/）与用户插件
    （~/.weci/plugins/），加载失败的插件不会影响 WeCi 启动。
  - Agent 只与 PluginRegistry.tool_registry 交互，不感知具体插件。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.chat import router as chat_router
from backend.api.plugins import router as plugins_router
from backend.api.system import router as system_router
from backend.config import FRONTEND_DIR, settings
from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRegistry
from backend.tools.registry import ToolRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger("weci")


def build_plugin_system() -> tuple[PluginRegistry, PluginLoader]:
    """组合根：构建插件注册表与加载器，自动加载所有插件。"""
    tool_registry = ToolRegistry()
    plugin_registry = PluginRegistry(tool_registry=tool_registry)
    loader = PluginLoader(plugin_registry)

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
    )

    plugin_registry, plugin_loader = build_plugin_system()
    app.state.plugin_registry = plugin_registry
    app.state.plugin_loader = plugin_loader
    # Agent 仍只看到 ToolRegistry，不感知插件
    app.state.tool_registry = plugin_registry.tool_registry

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(plugins_router, prefix="/api")

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
