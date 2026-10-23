# Hello Plugin —— WeCi 插件最小示例

这是一个最小可用的 WeCi 插件，演示 Plugin SDK v0.1 的核心用法。

## 功能

提供一个 `hello_world` 工具，接收 `name` 参数，返回 `Hello, {name}!`。

## 目录结构

```
hello/
├── manifest.json      # 插件清单（必填）
├── plugin.py          # 入口模块，包含 Plugin 与 Tool
├── README.md
└── tests/
    └── test_hello.py  # 插件自身测试
```

## 运行测试

```bash
cd examples/plugins/hello
python -m pytest tests/ -v
```

## 安装到 WeCi

```bash
# 方式一：用 CLI 安装
weci plugin install examples/plugins/hello

# 方式二：手动复制到用户插件目录
cp -r examples/plugins/hello ~/.weci/plugins/

# 启用（如果默认未启用）
weci plugin enable hello
```

重启 WeCi 后，Agent 即可看到 `hello_world` 工具。

## 关键点

1. **只依赖公开 SDK**：`from weci_sdk import Tool, ToolResult, WeCiPlugin`，不要导入 `backend.*`。
2. **Tool 参数用 Pydantic**：`args_model = HelloArgs` 同时生成 OpenAI schema 和运行时校验。
3. **返回 ToolResult**：`ToolResult.success(...)` / `ToolResult.error(...)`，Agent 层统一处理。
4. **权限声明**：在 `manifest.json` 的 `permissions` 中声明需要的能力；未声明则 `context.http` / `context.files` 等会抛 `PermissionDenied`。
