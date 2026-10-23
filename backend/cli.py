"""WeCi CLI。

支持命令：
  weci serve                       启动 Web 服务
  weci plugin list                 列出所有插件
  weci plugin install <path>       安装本地插件目录到 ~/.weci/plugins/
  weci plugin remove <id>          移除用户插件
  weci plugin enable <id>          启用插件
  weci plugin disable <id>         禁用插件

也可通过 ``python -m backend.cli ...`` 调用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.plugins.loader import PluginLoader
from backend.plugins.registry import PluginRegistry
from backend.tools.registry import ToolRegistry


def _build_loader() -> PluginLoader:
    tool_registry = ToolRegistry()
    plugin_registry = PluginRegistry(tool_registry=tool_registry)
    return PluginLoader(plugin_registry)


# ---------------------------------------------------------------------------
# plugin 子命令
# ---------------------------------------------------------------------------
def cmd_plugin_list(args) -> int:
    loader = _build_loader()
    loader.load_all()
    plugins = loader.get_plugins()
    if not plugins:
        print("（未发现任何插件）")
        return 0

    # 表格输出
    rows = []
    for p in plugins:
        m = p.manifest or {}
        rows.append(
            {
                "id": p.id,
                "name": m.get("name", p.id),
                "version": m.get("version", "?"),
                "status": p.status,
                "permissions": ",".join(m.get("permissions") or []) or "-",
                "tools": ",".join(p.tools) or "-",
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    headers = ["ID", "名称", "版本", "状态", "权限", "工具"]
    widths = [
        max(len(h), max((len(str(r[k])) for r in rows), default=0))
        for h, k in zip(headers, ["id", "name", "version", "status", "permissions", "tools"])
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(r["id"], r["name"], r["version"], r["status"], r["permissions"], r["tools"]))
    return 0


def cmd_plugin_install(args) -> int:
    loader = _build_loader()
    loader.load_all()
    try:
        entry = loader.install_plugin(args.path)
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1
    if entry is None:
        print("安装失败：插件加载出错，请查看日志。", file=sys.stderr)
        return 1
    print(f"已安装插件：{entry.id} v{entry.manifest.get('version', '?')}  状态：{entry.status}")
    return 0


def cmd_plugin_remove(args) -> int:
    loader = _build_loader()
    loader.load_all()
    if not loader.remove_plugin(args.id):
        print(f"移除失败：插件 {args.id} 不存在或是内置插件。", file=sys.stderr)
        return 1
    print(f"已移除插件：{args.id}")
    return 0


def cmd_plugin_enable(args) -> int:
    loader = _build_loader()
    loader.load_all()
    if not loader.enable_plugin(args.id):
        print(f"启用失败：插件 {args.id} 不存在。", file=sys.stderr)
        return 1
    print(f"已启用插件：{args.id}")
    return 0


def cmd_plugin_disable(args) -> int:
    loader = _build_loader()
    loader.load_all()
    if not loader.disable_plugin(args.id):
        print(f"禁用失败：插件 {args.id} 不存在。", file=sys.stderr)
        return 1
    print(f"已禁用插件：{args.id}")
    return 0


# ---------------------------------------------------------------------------
# serve 子命令
# ---------------------------------------------------------------------------
def cmd_serve(args) -> int:
    import uvicorn

    from backend.config import settings

    uvicorn.run(
        "backend.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weci", description="WeCi CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = sub.add_parser("serve", help="启动 WeCi Web 服务")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    # plugin
    p_plugin = sub.add_parser("plugin", help="插件管理")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_cmd", required=True)

    p_list = plugin_sub.add_parser("list", help="列出所有插件")
    p_list.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_list.set_defaults(func=cmd_plugin_list)

    p_install = plugin_sub.add_parser("install", help="安装本地插件目录")
    p_install.add_argument("path", help="插件目录路径（含 manifest.json）")
    p_install.set_defaults(func=cmd_plugin_install)

    p_remove = plugin_sub.add_parser("remove", help="移除用户插件")
    p_remove.add_argument("id")
    p_remove.set_defaults(func=cmd_plugin_remove)

    p_enable = plugin_sub.add_parser("enable", help="启用插件")
    p_enable.add_argument("id")
    p_enable.set_defaults(func=cmd_plugin_enable)

    p_disable = plugin_sub.add_parser("disable", help="禁用插件")
    p_disable.add_argument("id")
    p_disable.set_defaults(func=cmd_plugin_disable)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
