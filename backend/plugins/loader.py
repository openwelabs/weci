"""Plugin Loader：发现、校验、加载、卸载插件。

插件目录来源：
  - 内置：``backend/plugins/builtin/``
  - 用户：``~/.weci/plugins/``（可通过环境变量 ``WECI_PLUGINS_DIR`` 覆盖）

加载流程：
  1. 扫描目录，逐个子目录读取 manifest.json
  2. 校验 manifest（必填字段、id 格式、api_version 兼容性、权限合法性）
  3. 加载入口模块（importlib），实例化插件类
  4. 创建 PluginContext，调用 on_load
  5. 注册到 PluginRegistry（其工具自动进入 ToolRegistry）
  6. 任何一步失败 → 记录错误，标记 failed，**不影响其他插件与 WeCi 启动**
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from weci_sdk.plugin import WeCiPlugin
from backend.plugins.context import PluginContextImpl
from backend.plugins.manifest import Manifest, ManifestError, load_manifest
from backend.plugins.registry import PluginEntry, PluginRegistry

logger = logging.getLogger("weci.plugins")

# 项目根目录（backend/plugins/loader.py → ../../..）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 默认用户插件目录（可通过 WECI_PLUGINS_DIR 覆盖）
DEFAULT_USER_PLUGINS_DIR = Path.home() / ".weci" / "plugins"
# 运行时数据放在项目目录下，使部署自包含
DATA_DIR = PROJECT_ROOT / ".weci"
STATE_FILE = DATA_DIR / "plugin_state.json"
# 插件私有存储：<DATA_DIR>/plugins/data/<plugin_id>/store.json
DEFAULT_STORAGE_DIR = DATA_DIR / "plugins" / "data"


class PluginLoader:
    def __init__(
        self,
        registry: PluginRegistry,
        *,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        state_file: Path | None = None,
        storage_dir: Path | None = None,
        memory_store: Any = None,
        permission_manager: Any = None,
        workspace_guard: Any = None,
    ) -> None:
        self.registry = registry
        # Memory 存储由 Core 注入，插件通过 context.memory 访问
        self.memory_store = memory_store
        # 统一权限层：插件声明的低风险权限在加载时自动登记，高风险需用户确认
        self.permission_manager = permission_manager
        self.workspace_guard = workspace_guard
        self.builtin_dir = builtin_dir or (
            Path(__file__).resolve().parent / "builtin"
        )
        self.user_dir = user_dir or Path(
            __import__("os").getenv("WECI_PLUGINS_DIR", str(DEFAULT_USER_PLUGINS_DIR))
        )
        self.state_file = state_file or STATE_FILE
        self.storage_dir = storage_dir or DEFAULT_STORAGE_DIR

        # 确保项目根目录在 sys.path 中，使插件能 `from weci_sdk import ...`
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------
    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_file.is_file():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, state: dict[str, dict[str, Any]]) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            # 状态持久化失败不应阻断插件操作（沙箱/只读环境下常见）；
            # 仅记录警告，运行期内存状态仍然有效。
            logger.warning("无法写入插件状态文件 %s：%s", self.state_file, exc)

    def _is_enabled(self, plugin_id: str, default: bool = True) -> bool:
        state = self._load_state()
        entry = state.get(plugin_id)
        if entry is None:
            return default
        return bool(entry.get("enabled", default))

    def _set_enabled(self, plugin_id: str, enabled: bool) -> None:
        state = self._load_state()
        state.setdefault(plugin_id, {})["enabled"] = enabled
        self._save_state(state)

    # ------------------------------------------------------------------
    # 发现与加载
    # ------------------------------------------------------------------
    def discover(self) -> list[Path]:
        """返回所有插件目录（内置 + 用户），用户目录覆盖同 id 的内置插件。"""
        dirs: dict[str, Path] = {}
        for base in (self.builtin_dir, self.user_dir):
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "manifest.json").is_file():
                    # 后扫描的（用户）覆盖先扫描的（内置）
                    dirs[child.name] = child
        return list(dirs.values())

    def load_all(self) -> None:
        """加载所有发现的插件。单个失败不影响整体。"""
        for plugin_dir in self.discover():
            try:
                self.load_plugin(plugin_dir)
            except Exception:  # noqa: BLE001
                logger.exception("加载插件目录失败: %s", plugin_dir)

    def load_plugin(self, path: str | Path) -> PluginEntry | None:
        """加载单个插件目录。"""
        plugin_dir = Path(path).resolve()
        if not plugin_dir.is_dir():
            logger.error("插件目录不存在: %s", plugin_dir)
            return None

        # 1. 校验 manifest
        try:
            manifest = load_manifest(plugin_dir)
        except ManifestError as exc:
            logger.error("Plugin %s failed to load: %s", plugin_dir.name, exc)
            # 注册一个 failed 条目，便于前端展示
            return self.registry.register_plugin(
                plugin_id=plugin_dir.name,
                manifest={"id": plugin_dir.name, "name": plugin_dir.name, "version": "?", "api_version": "?", "entry": "?"},
                instance=None,
                path=str(plugin_dir),
                enabled=False,
                error=str(exc),
            )

        # 检查是否已加载（重复 id）
        existing = self.registry.get_plugin(manifest.id)
        if existing is not None:
            logger.warning("插件 id 冲突，跳过: %s (来自 %s)", manifest.id, plugin_dir)
            return existing

        enabled = self._is_enabled(manifest.id, default=manifest.default_enabled)

        # 2. 加载入口模块
        entry_path = plugin_dir / manifest.entry
        if not entry_path.is_file():
            msg = f"入口文件不存在：{manifest.entry}"
            logger.error("Plugin %s failed to load: %s", manifest.id, msg)
            return self.registry.register_plugin(
                plugin_id=manifest.id,
                manifest=manifest.raw,
                instance=None,
                path=str(plugin_dir),
                enabled=False,
                error=msg,
            )

        try:
            module = _load_module_from_file(manifest.id, entry_path)
        except Exception as exc:  # noqa: BLE001
            msg = f"加载入口模块失败：{exc}"
            logger.error("Plugin %s failed to load: %s", manifest.id, msg)
            return self.registry.register_plugin(
                plugin_id=manifest.id,
                manifest=manifest.raw,
                instance=None,
                path=str(plugin_dir),
                enabled=False,
                error=msg,
            )

        # 3. 实例化插件类（模块中第一个 WeCiPlugin 子类）
        instance = _instantiate_plugin(module, manifest)
        if instance is None:
            msg = "未在入口模块中找到 WeCiPlugin 子类"
            logger.error("Plugin %s failed to load: %s", manifest.id, msg)
            return self.registry.register_plugin(
                plugin_id=manifest.id,
                manifest=manifest.raw,
                instance=None,
                path=str(plugin_dir),
                enabled=False,
                error=msg,
            )

        instance.manifest = manifest.raw

        # 4. 创建上下文并调用 on_load
        context = PluginContextImpl(
            plugin_id=manifest.id,
            permissions=manifest.permissions,
            config=manifest.config,
            storage_dir=self.storage_dir,
            memory_store=self.memory_store,
            workspace_guard=self.workspace_guard,
            permission_manager=self.permission_manager,
        )
        instance._context = context  # 供工具执行时使用  # noqa: SLF001
        try:
            instance.on_load(context)
        except Exception as exc:  # noqa: BLE001
            msg = f"on_load 抛出异常：{exc}"
            logger.error("Plugin %s failed to load: %s", manifest.id, msg)
            return self.registry.register_plugin(
                plugin_id=manifest.id,
                manifest=manifest.raw,
                instance=None,
                path=str(plugin_dir),
                enabled=False,
                error=msg,
            )

        # 5. 注册到 Registry（内部会注册工具）
        entry = self.registry.register_plugin(
            plugin_id=manifest.id,
            manifest=manifest.raw,
            instance=instance,
            path=str(plugin_dir),
            enabled=enabled,
        )
        if not enabled:
            self.registry.disable_plugin(manifest.id)

        logger.info(
            "插件已加载: %s v%s (%s) 工具: %s",
            manifest.id,
            manifest.version,
            "启用" if enabled else "已禁用",
            ", ".join(entry.tools) or "无",
        )
        if self.permission_manager is not None:
            self.permission_manager.log(
                "plugin.loaded",
                plugin=manifest.id,
                version=manifest.version,
                permissions=",".join(manifest.permissions) or "-",
                enabled=enabled,
            )
            if enabled:
                self.permission_manager.grant_declared(
                    f"plugin:{manifest.id}", manifest.permissions
                )
        return entry

    # ------------------------------------------------------------------
    # 卸载 / 重载
    # ------------------------------------------------------------------
    def unload_plugin(self, plugin_id: str) -> bool:
        return self.registry.unregister_plugin(plugin_id)

    def reload_plugin(self, plugin_id: str) -> PluginEntry | None:
        entry = self.registry.get_plugin(plugin_id)
        if entry is None:
            return None
        path = entry.path
        self.unload_plugin(plugin_id)
        return self.load_plugin(path)

    def get_plugins(self) -> list[PluginEntry]:
        return self.registry.list_plugins()

    def get_plugin(self, plugin_id: str) -> PluginEntry | None:
        return self.registry.get_plugin(plugin_id)

    # ------------------------------------------------------------------
    # 启用 / 禁用（带状态持久化）
    # ------------------------------------------------------------------
    def enable_plugin(self, plugin_id: str) -> bool:
        if self.registry.enable_plugin(plugin_id):
            self._set_enabled(plugin_id, True)
            entry = self.registry.get_plugin(plugin_id)
            if self.permission_manager is not None and entry is not None:
                self.permission_manager.grant_declared(
                    f"plugin:{plugin_id}",
                    (entry.manifest or {}).get("permissions") or [],
                )
            return True
        return False

    def disable_plugin(self, plugin_id: str) -> bool:
        if self.registry.disable_plugin(plugin_id):
            self._set_enabled(plugin_id, False)
            return True
        return False

    # ------------------------------------------------------------------
    # 安装 / 移除（用户插件目录）
    # ------------------------------------------------------------------
    def install_plugin(self, source_path: str | Path) -> PluginEntry | None:
        """把一个本地插件目录复制到用户插件目录并加载。"""
        src = Path(source_path).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"插件源目录不存在：{src}")
        if not (src / "manifest.json").is_file():
            raise FileNotFoundError(f"源目录缺少 manifest.json：{src}")

        manifest = load_manifest(src)
        dest = self.user_dir / manifest.id
        if dest.exists():
            raise FileExistsError(f"用户插件目录已存在同名插件：{dest}")

        self.user_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        return self.load_plugin(dest)

    def remove_plugin(self, plugin_id: str) -> bool:
        """从用户插件目录移除插件并卸载。"""
        entry = self.registry.get_plugin(plugin_id)
        if entry is None:
            return False
        # 不允许移除内置插件
        builtin_root = self.builtin_dir.resolve()
        try:
            Path(entry.path).resolve().relative_to(builtin_root)
            logger.error("不能移除内置插件: %s", plugin_id)
            return False
        except ValueError:
            pass

        self.unload_plugin(plugin_id)
        plugin_dir = Path(entry.path)
        if plugin_dir.is_dir():
            shutil.rmtree(plugin_dir, ignore_errors=True)
        # 清理状态
        state = self._load_state()
        state.pop(plugin_id, None)
        self._save_state(state)
        return True


def _load_module_from_file(module_name: str, path: Path):
    """从文件路径加载 Python 模块。"""
    spec = importlib.util.spec_from_file_location(f"weci_plugin_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建模块 spec：{path}")
    module = importlib.util.module_from_spec(spec)
    # 先注册到 sys.modules：pydantic 解析前向引用（如 Literal 别名）时需要按模块名查找命名空间
    sys.modules[spec.name] = module
    # 把插件所在目录加入 sys.path，便于插件 import 同目录的其他模块
    plugin_dir = str(path.parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _instantiate_plugin(module, manifest: Manifest) -> WeCiPlugin | None:
    """在模块中查找第一个 WeCiPlugin 子类并实例化。"""
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, WeCiPlugin)
            and obj is not WeCiPlugin
        ):
            return obj()
    return None
