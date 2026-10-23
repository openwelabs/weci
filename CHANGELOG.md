# Changelog

All notable changes to WeCi are documented here.

## [0.5.0] — Plugin SDK v0.1 正式化

### 新增

- **`weci_sdk` 正式公开包**：第三方插件使用 `from weci_sdk import WeCiPlugin, Tool, ToolResult, PluginContext`；`sdk` 保留为向后兼容别名
- **`ToolResult` 统一工具返回类型**：`ToolResult.success(content, **meta)` / `ToolResult.error(msg)`，Agent 层统一处理
- **`ToolContext` 类型**：工具执行时可用的上下文接口
- **PluginContext 子命名空间**：`context.http.request()`、`context.files.read/write/list_dir()`、`context.storage.get/set()`（扁平方法保留兼容）
- **`PermissionDenied` 异常**（`PluginPermissionError` 别名）
- **Manifest 严格校验**：语义化版本、entry 路径安全（禁止 `..`）、未知字段向前兼容保留
- **生命周期状态机**：`LifecycleState`（discovered → validating → loading → registering → ready → disabled → failed → unloaded）
- **插件存储按 plugin_id 隔离**：`<data>/plugins/data/<plugin_id>/store.json`
- **CLI 新命令**：`weci plugin info <id>`、`weci plugin validate <path>`
- **Plugin Template**：`examples/plugins/template/` 可直接复制开发
- **pytest 测试套件**：94 个测试覆盖 manifest、加载/卸载、工具注册/执行、权限拒绝、存储隔离、损坏插件、CLI、示例与模板
- **插件开发文档**：`docs/plugins/`（README、quickstart、manifest、sdk、tools、permissions）

### 变更

- 内置插件全部改用 `weci_sdk` 导入
- 工具 `execute` 返回值支持 `str` / `dict` / `ToolResult`，由 `run()` 归一化
- `frontend/` 移入 `backend/frontend/`，随包发布

### 安全

- 明确权限系统是能力控制接口，非 Python 沙箱；文档与 UI 提示"安装插件即信任代码"

## [0.4.0] — 插件系统 v0.1

- 引入 Plugin SDK、PluginLoader、PluginRegistry、ToolRegistry
- 4 个内置插件：calculator、current_time、web_search（含 fetch_url）、computer
- 权限系统：network / storage / filesystem / process / clipboard
- 前端插件管理页面
- `weci` CLI：serve、plugin list/install/remove/enable/disable
- 示例插件：examples/plugins/hello

## [0.3.0] — Agent 模式

- 新增 Agent 模式，支持工具调用循环
- 工具执行流式展示
- 内置 calculator / current_time / web_search 工具

## [0.2.0] — Chat 模式

- 流式对话、Markdown 渲染、代码高亮、LaTeX
- 会话管理、模型设置、测试连接
- 深色/浅色主题、响应式布局

## [0.1.0] — 初始版本

- 基础 Chat 界面与 OpenAI-compatible 后端对接
