# WeCi 插件开发文档

本目录包含 WeCi Plugin SDK v0.1 的完整开发文档。

## 文档索引

| 文档 | 说明 |
| --- | --- |
| [quickstart.md](quickstart.md) | 5 分钟快速上手：复制模板 → 写工具 → 安装运行 |
| [manifest.md](manifest.md) | `manifest.json` 字段规范与校验规则 |
| [sdk.md](sdk.md) | SDK 公开 API：`WeCiPlugin`、`Tool`、`ToolResult`、`PluginContext` |
| [tools.md](tools.md) | 工具开发：参数 schema、执行、返回值、错误处理 |
| [permissions.md](permissions.md) | 权限系统：声明、能力、拒绝、安全边界 |

## 核心概念

```
WeCi Core
   ↓
PluginLoader  ← 扫描目录、校验 manifest、加载模块
   ↓
PluginRegistry  ← 管理插件生命周期 + 工具注册
   ↓
ToolRegistry  ← Agent 唯一交互对象
   ↓
Agent
```

**关键原则**：Agent 永远只与 `ToolRegistry` 交互，不感知具体工具或插件。新增任意数量插件，Agent Core 零修改。

## 安全边界

第三方 Python 插件本质上可以执行任意 Python 代码。权限系统是**能力控制接口**，**不是 Python 沙箱**。

> **Installing a plugin means trusting its code.**

不要安装来源不明的插件。真正的隔离（subprocess / WASM / container）属于未来工作。

## 版本

- SDK 自身版本：`weci_sdk.__version__`（当前 `0.1.0`）
- Plugin API 版本：manifest 中的 `api_version`（当前支持 `0.1`）

Core 内部重构不会改变 SDK 公开 API。未来如需 breaking change，会增加新的 `api_version`。
