# Changelog

All notable changes to WeCi are documented here.

## [0.14.0] — 统一权限 / 安全系统

### 新增

- **Permission Manager**（`backend/services/permissions.py`）：所有工具调用的唯一权限判定入口，链路为 `Request → Permission Manager → Tool Registry → Tool / Plugin / MCP`
- **权限模型（带 scope）**：`workspace:read` / `workspace:write` / `workspace:delete` / `command:execute` / `network:access` / `memory:read` / `memory:write` / `mcp:use` / `plugin:use` / `storage:read` / `storage:write` / `clipboard:access` / `filesystem:*`，并区分 `low` / `medium` / `high` 风险
- **Tool Permission Metadata**：`Tool.permissions` 在注册时声明所需权限，Registry 统一按声明判定，Agent 不感知具体权限逻辑
- **Default Deny + 最小权限**：未声明的权限默认禁止；仅低风险权限默认放行，中 / 高风险需用户确认
- **Agent 权限确认**：SSE 下发 `permission_request` 事件，前端展示「Agent 请求执行 XXX」，支持 **Allow once / Allow for this Run / Deny**
- **Workspace 沙箱**（`backend/services/workspace.py`）：统一 `resolve` → `resolve(strict=False)` → `relative_to(root)` 校验，拒绝 `../` 逃逸、NUL 路径与 Root 外访问，读写删权限分离
- **Command Execution 安全**：`run_command` 明确 cwd 且必须位于 Workspace 内，环境变量经 `sanitize_env()` 过滤密钥，支持 timeout 与 cancellation，记录 command / exit_code / stdout / stderr，不注入 API Key
- **Plugin 权限**：Manifest `permissions` 在加载时归一化校验，插件工具执行必须经过 Permission Manager，UI 可查看插件声明的权限
- **MCP 权限**：MCP 工具统一使用 `mcp:use` 权限，连接成功后记录安全日志，不绕过 Permission Manager
- **Permission UI**：新增「权限与安全」全屏页面，展示 Workspace / Provider / Plugin / MCP / 工具权限 / 安全日志，支持 Allow / Deny / Reset
- **Security Logging**：记录 `permission.granted` / `permission.denied` / `command.executed` / `workspace.file_deleted` / `plugin.loaded` / `mcp.connected`，日志统一脱敏（API Key / Token / Password / Cookie / Authorization）
- **Agent Run 权限审计**：`ToolCallRecord` 新增 `permissions` / `permission_result` / `user_confirmation`，Run History 可展示「请求什么权限 → 是否允许 → 执行结果」
- **持久化**：`.weci/permissions.json`，原子写入 + `chmod 0600`
- **REST API**：`GET /api/permissions`、`POST /api/permissions/grant|deny|reset`、`GET /api/permissions/requests`、`POST /api/permissions/requests/{id}/resolve`、`GET /api/permissions/log`
- **测试**：新增 `tests/test_permission_system.py`（30 用例），覆盖未授权拒绝、授权后放行、路径逃逸、删除 / 命令权限、插件与 MCP 权限、日志脱敏、Agent Run 权限事件

### 兼容

- 旧插件权限名（`network` / `filesystem.read` 等）通过 `normalize_permissions()` 自动归一化，无需修改
- 复用现有 Agent Loop、Tool Registry、Plugin System、MCP、Workspace、Agent Run，未重新设计既有架构

## [0.13.0] — Agent Run 管理

### 新增

- **Agent Run Manager**（`backend/services/agent_run.py`）：把一次 Agent 执行升级为具有生命周期的 Run，数据结构包含 `id` / `conversation_id` / `status` / `started_at` / `finished_at` / `current_step` / `tool_calls` / `result` / `error` / `error_type` 等字段
- **状态机**：`pending → running → {completed, failed, cancelled}`，非法转换返回 `False`，终态不可再变更
- **统一事件模型**：`run.created` / `run.started` / `step.started` / `tool.called` / `tool.completed` / `tool.failed` / `run.completed` / `run.failed` / `run.cancelled`
- **可靠取消**：`POST /api/agent/runs/{run_id}/cancel` 设置 `asyncio.Event` 取消信号，Agent Loop 在每步 / 工具间检查并尽早退出；Stop 按钮先取消 Run 再断开 SSE
- **SSE 与 Run 解耦**：Run 状态保存在 Manager 中，前端刷新或 SSE 中断不会把 Run 误判为 `completed`；服务重启后残留的 `running` / `pending` 统一标记为 `failed`（`error_type=network`）
- **错误分类**：`provider` / `tool` / `mcp` / `workspace` / `context` / `cancelled` / `network` / `unknown`，UI 显示可读信息，不暴露 API Key 或内部路径
- **持久化**：复用 JSON 文件模式，保存在 `.weci/agent_runs.json`，原子写入 + `chmod 0600`，每会话最多 100 条
- **REST API**：`GET /api/agent/runs?conversation_id=&limit=`、`GET /api/agent/runs/{run_id}`、`POST /api/agent/runs/{run_id}/cancel`
- **前端 Run History 面板**：展示时间、状态、时长、工具数、结果摘要，可展开查看完整工具调用（参数 / 输出 / 错误 / 耗时）与最终结果
- **测试**：新增 `tests/test_agent_run.py`（15 用例），覆盖创建、状态转换、工具记录、completed / failed / cancelled、SSE 中断、Provider 错误、Tool 错误、持久化恢复

### 复用

- 复用现有 `agent.py` Agent Loop、`Tool Registry`、`Plugin System`、`MCP Tool`、`Provider/Model`、`Context Manager`、`Workspace`，未重新实现工具或 Agent 系统

## [0.12.0] — 统一模型 Provider 管理

### 新增

- **Provider Manager**：`backend/services/providers.py` 新增 `ProviderConfig` / `ModelConfig` / `ResolvedModel` 与 `ProviderManager`，把 Endpoint、API Key、模型与能力收敛为唯一来源：`Provider Manager → Model → Capabilities → Chat / Agent / Multimodal`
- **能力声明**：每个模型声明 `text` / `vision` / `tool_calling` / `streaming`；`vision` 按模型名启发式推断，可在 UI 覆盖
- **能力预检**：`POST /api/chat` 在请求上游前检查 `text` / `vision` / `tool_calling`，不满足时返回 `400` 与可读说明，不再等到上游报错
- **Chat / Agent 模型**：Provider 支持 `default_model`、`chat_model`、`agent_model` 分别配置
- **非流式兜底**：模型声明 `streaming: false` 时，Chat 走 `complete_chat`，Agent 走非流式工具请求
- **测试连接**：`test_provider` 检查 Endpoint / Key / 模型列表，`test_model` 发送最小非流式请求，区分 401/403、404 与其他错误
- **REST API**：`/api/providers` 的增删改查、模型增删改与两个测试端点（共 9 个）
- **前端 Provider 管理页**：Provider 与模型表单、能力勾选、默认 / Chat / Agent 模型、测试、编辑与删除
- **模型菜单能力展示**：按 Provider 分组，展示 `✓ Vision`、`✓ Tool Calling`、`✓ Streaming`
- **兼容模式**：设置页原有 Endpoint / Key / Model 输入保留在 `<details>` 中，旧请求与 `/api/test-connection` 行为不变

### 安全

- Provider API Key 仅保存在服务端 `.weci/providers.json`，原子写入后 best-effort `chmod 0600`
- 公开响应只包含 `has_api_key`；Key 不进入日志、错误信息或 SSE 事件
- Provider 未配置 Key 时回退请求头 `X-WeCi-API-Key` 或 `settings.api_key`

### 变更

- `backend/api/chat.py`：新增可选 `provider_id`；使用 Provider 解析结果；SSE `start` 事件增加 `provider_id` / `provider_name` / `capabilities`
- `backend/services/agent.py`：`run_agent(..., stream=True)` 透传给上游客户端
- `backend/services/openai_client.py`：新增 `complete_chat`、`check_model`、`stream_chat_with_tools(stream=...)`；`payload_has_images` 公开化
- `backend/api/system.py`：`GET /api/config` 追加 `has_provider` / `default_provider_id`（原字段不变）
- `backend/main.py`：初始化并挂载 `ProviderManager`
- 版本号升至 0.12.0

### 测试

- 新增 `tests/test_providers.py`：Provider / 模型 CRUD、持久化重载、`resolve` 优先级、能力推断与覆盖、Key 不泄露、测试连接、REST API、Chat 能力拦截与兼容路径
- 全量测试通过；Plugin、MCP、Tool、Memory、Context Manager 与多模态行为不变

## [0.11.0] — MCP（Model Context Protocol）支持

### 新增

- **MCP stdio 客户端**：`backend/mcp/client.py` 实现 `StdioMcpClient`，通过 stdin / stdout 收发按行分隔的 JSON-RPC 2.0，支持 `initialize` → `notifications/initialized` → `tools/list`（含 `nextCursor` 分页）→ `tools/call`
- **MCP Manager**：`backend/mcp/manager.py` 实现 `McpManager`，统一管理配置持久化、Server 生命周期（`disabled` / `stopped` / `connecting` / `ready` / `error`）、启停 / 重连与工具注册
- **MCP Tool 转换**：`McpTool` 直接使用 Server 的 `inputSchema` 作为工具 schema，执行结果统一转换为 `ToolResult`（拼接 `content[].text`、`resource.text`，图片 / 音频转为占位，`isError` 映射为失败）
- **工具注册**：连接成功后把 MCP Tools 注册进现有 `ToolRegistry`，工具名统一为 `mcp__<server_id>__<tool>`（净化、截断到 64 字符），冲突时跳过并记录日志
- **REST API**：`GET / POST /api/mcp/servers`、`PATCH / DELETE /api/mcp/servers/{id}`、`POST /api/mcp/servers/{id}/connect`、`POST /api/mcp/servers/{id}/disconnect`
- **FastAPI lifespan**：应用启动时连接所有已启用的 MCP Server，关闭时统一终止子进程
- **前端 MCP 管理页**：顶栏入口 + 设置页入口，支持添加、编辑、连接 / 断开、启用 / 禁用与删除；展示状态、命令、工具与最近错误，并明确提示子进程无沙箱

### 变更

- `backend/main.py`：使用 `lifespan` 初始化 `McpManager(plugin_registry.tool_registry)`，新增 MCP 路由
- 配置新增 `WECI_MCP_CONNECT_TIMEOUT`（默认 20 秒）与 `WECI_MCP_CALL_TIMEOUT`（默认 120 秒）
- 版本号升至 0.11.0

### 修复

- **插件模块注册**：`PluginLoader` 在加载插件模块时先写入 `sys.modules`，修复内置 Memory 插件的 Pydantic 参数模型（引用 `MemoryScopeArg`）无法重建、导致 Agent 模式生成工具 schema 时报错的问题

### 测试

- 新增 `tests/test_mcp.py`（16 项）：客户端连接 / 工具列表 / 调用 / 关闭、命令缺失、进程退出、请求超时；Manager 注册与注销、错误与崩溃映射、启用 / 禁用持久化、连接失败、删除、更新重连、启动 / 停止、非法配置、与插件工具共存、工具名生成；API CRUD / 启停 / 连接 / 断开 / 404 / 422 / 400 校验
- 新增 `tests/fixtures/fake_mcp_server.py` 作为 stdio 测试 Server
- 全量 203 个测试通过；Plugin、Tool、Agent、Workspace、Memory 与 Chat 行为不变

### 未实现（按计划留待后续）

- MCP Marketplace、远程 MCP Server 管理平台、OAuth、自动发现、Server 沙箱、远程（HTTP / SSE）传输

## [0.10.0] — 基础 Memory 系统

### 新增

- **Memory 数据结构**：`backend/services/memory.py` 定义 `MemoryEntry`（`id` / `scope` / `scope_id` / `key` / `content` / `created_at` / `updated_at`），独立保存在 `.weci/memory.json`
- **Memory 增删改查**：`MemoryStore` 支持 `save`（同 scope + key 自动 upsert）、`update`、`delete`、`delete_by_key`、`list`、`get`、`find`、`clear`
- **scope 隔离**：支持 `user` / `workspace` / `conversation`，配合 `scope_id` 分别隔离；默认 user 为 `default`
- **REST API**：`GET /api/memories`、`POST /api/memories`、`PATCH /api/memories/{id}`、`DELETE /api/memories/{id}`
- **Context Manager 注入**：新增 `add_memories()`，按请求的 `user` + `workspace` + `conversation` 读取可见记忆，渲染为 `<memories>` 系统上下文，并受条数 / 总字符限制
- **Agent 工具**：新增内置 Memory 插件，提供 `save_memory` / `delete_memory`；仅在用户明确要求记住或忘记时调用
- **Plugin SDK**：`PluginContext` 新增 `memory` 能力与 `memory` 权限，内置与第三方插件使用同一套 API
- **前端记忆页**：顶栏入口 + 设置页入口，支持查看、新建、修改、删除；请求携带浏览器级 `user_id` 与当前 `conversation_id`
- **上下文统计**：SSE `start` 的 `context` 新增 `memory_count`

### 变更

- `backend/api/chat.py`：请求新增可选 `user_id` / `workspace_id` / `conversation_id`，并在 SSE 请求内用 `use_memory_scope()` 绑定当前 scope，供 Agent 工具读取
- `backend/services/agent.py`：系统提示补充记忆规则（只在用户明确要求时保存 / 删除，不自动保存聊天内容）
- `backend/plugins/loader.py`：`PluginLoader` 支持注入 `MemoryStore`
- 版本号升至 0.10.0

### 配置

- 新增 `WECI_CONTEXT_MEMORY_MAX_ITEMS`、`WECI_CONTEXT_MEMORY_MAX_CHARS`、`WECI_MEMORY_MAX_PER_SCOPE`、`WECI_MEMORY_MAX_CONTENT_CHARS`

### 测试

- 新增 `tests/test_memory.py`（20 项）：存储 CRUD、同 key upsert、scope 隔离、`for_context` 可见性、限额、损坏文件容错、Context 注入与截断 / 转义、`memory` 权限、请求 scope 绑定、内置插件 manifest 与工具执行、API CRUD / 校验 / 404
- 全量 187 个测试通过；Chat、Agent、Context Manager、Workspace 与插件系统行为不变

### 未实现（按计划留待后续）

- 向量数据库、Embedding、RAG、自动分析聊天生成长期记忆、云端同步

## [0.9.0] — 统一 Context Manager

### 新增

- **统一 Context 数据结构**：`backend/services/context.py` 定义 `ContextManager` / `ModelContext`，所有发给模型的内容只有一条链路：`Conversation + Workspace + Attachment + Tool Result → Context → Model`
- **Chat / Agent 共用**：Chat 与 Agent 都通过 `ContextManager.from_chat_messages()` 构建上下文；Agent 每轮把 tool_calls / tool 结果写回同一个 Context，不再自行拼接 system 与历史
- **Workspace Context**：`POST /api/chat` 支持可选 `workspace`（`name` / `root` / `description` / `instructions` / `files`），作为 System Context 注入
- **token 预算**：按模型名推断上下文窗口，扣除预留输出 tokens 后计算可用预算；上下文窗口最小值与常用模型上限均可在配置中调整
- **自动压缩**：超限时优先省略较旧消息中的图片、截断过长的文件 / 工具结果，再从最旧的消息开始省略；保留 System Context、当前用户请求与最近消息
- **内容去重**：相同图片 / 文件内容以及 Workspace 与附件重复内容只注入一次
- **上下文统计**：SSE `start` 事件新增 `context` 字段（估算 tokens、预算、省略条数、是否压缩），不包含消息正文

### 变更

- `backend/api/chat.py`：先解析模型配置，再统一构建 Context；Chat 与 Agent 都向 Provider 传入 `ModelContext`
- `backend/services/agent.py`：`run_agent()` 改为接收 `ContextManager`，移除内部 system prompt 拼接与消息数组维护；工具调用 id 缺失时生成稳定 id
- `backend/services/openai_client.py`：`stream_chat()` / `stream_chat_with_tools()` 接受 `ModelContext`，由 Provider 统一生成最终请求消息
- 版本号升至 0.9.0

### 配置

- 新增 `WECI_CONTEXT_WINDOW_TOKENS`、`WECI_CONTEXT_RESERVE_TOKENS`、`WECI_CONTEXT_MIN_RECENT_MESSAGES`、`WECI_CONTEXT_IMAGE_TOKENS`、`WECI_CONTEXT_WORKSPACE_MAX_CHARS`

### 测试

- 新增 `tests/test_context.py`（13 项）：Context 结构、Provider 输入、Workspace 注入与去重、重复附件省略、token 预算与压缩、旧图片省略、当前消息截断、工具结果顺序、Agent 共用 Context
- `tests/test_agent_trace.py` 适配 `ContextManager` 入口
- 全量 158 个测试通过；纯文本 Chat、多模态与插件系统行为不变

### 未实现（按计划留待后续）

- 长期 Memory、RAG / 向量数据库、完整文件工作区与 Workspace 文件同步

## [0.8.0] — Agent 执行过程可视化

### 新增

- **Agent 执行过程区块**：执行中显示 `第 N / 10 步 · 执行中`，完成显示 `已完成 · 共 N 步`，达到上限显示 `达到最大步数（10）`，停止 / 中断显示 `已停止`
- **工具调用卡片**：状态图标（执行中转圈 / 成功 / 失败 / 已中断）、工具名、所属步骤、状态与耗时；失败时直接显示错误原因
- **参数与结果折叠**：默认收起，点击「参数」「结果」展开查看；超过 2 万字符的输出截断并在卡片内滚动（传给模型的仍是完整结果）
- **Stop / Cancel**：停止后立即中止流式请求，正在运行的工具卡片标记为「已中断」
- **刷新 / 断网保护**：`pagehide` 时保存中断状态，未完成的回复与工具统一标记为「已中断」，不会永久停留在执行中
- **失败原因展示**：Agent 整体失败时在消息内显示可读原因，工具失败则显示在对应卡片上
- **只展示事实**：仅呈现 Tool Call、参数、返回结果、状态与最终回答，不展示模型隐藏推理过程

### 变更

- `backend/services/agent.py`：`tool_call` 事件新增 `step` / `max_steps` / `arguments` / `output`，`done` 事件新增 `steps`；展示输出上限 `MAX_TOOL_DISPLAY_CHARS = 20_000`
- `backend/api/chat.py`：客户端断开时通过 `request.is_disconnected()` 与 `agent_stream.aclose()` 终止 Agent Loop
- 前端 `main.js` / `ui.js` / `store.js` 统一 Agent 状态机与渲染，修复完成事件覆盖工具参数导致「参数」消失的问题
- 版本号升至 0.8.0

### 测试

- 新增 `tests/test_agent_trace.py`（6 项）：SSE 事件序列与参数/输出、工具失败原因、长输出截断、非法 JSON 参数、步数上限、无工具调用回退
- 全量 154 个测试通过；普通 Chat 模式行为不变

## [0.7.0] — 文件附件（第一阶段）

### 新增

- **文件附件模型**：`FileAttachment` / `ValidatedFile`，消息结构扩展为 `text | image | file`
- **支持类型**：`.txt` `.md` `.json` `.csv` `.py` `.js` `.ts` `.html` `.css` `.pdf`
- **文本文件上下文**：服务端解码（UTF-8 优先，兼容 GB18030）后以 `<file>` 边界块注入模型上下文
- **PDF 基础提取**：使用 `pypdf` 提取纯文本；加密、损坏或无文本层的 PDF 给出明确错误
- **前端上传**：新增文件按钮，支持点击 / 拖拽，发送前显示文件名与大小，可逐个删除
- **消息展示**：文件以附件卡片形式显示在用户消息中，并随会话持久化到 `localStorage`
- **Agent 支持**：文件文本随消息进入 Agent，Agent Loop 与插件系统零改动
- **配置项**：`WECI_MAX_FILE_BYTES`、`WECI_MAX_FILES_PER_MESSAGE`、`WECI_MAX_FILE_TEXT_CHARS`
- **测试**：`tests/test_file_attachments.py` 覆盖提取、校验、PDF、API 400 与图片/纯文本回归（共 139 个测试通过）

### 变更

- `backend/services/message.py` 的 `validate_message()` 返回 `(text, images, files)`，`to_openai_content()` 按 `text → image → file` 生成 content parts
- `pyproject.toml` 新增依赖 `pypdf>=4.0,<6.0`

### 未实现（按计划留待后续）

- RAG / 向量数据库、云端文件存储、Office 全格式解析、文件同步、完整 Workspace

## [0.6.0] — 图片输入（Vision）第一阶段

### 新增

- **多模态消息模型**：`backend/services/message.py` 定义 `ChatMessage` / `ImageAttachment` / `ValidatedImage`，统一完成附件校验与 OpenAI-compatible 转换
- **图片上传**：输入框旁的图片按钮选择文件，或直接拖拽图片到输入区
- **发送前预览与删除**：附件缩略图条支持逐张移除
- **消息内图片展示**：发送后图片与文字一同渲染，并随会话保存到 `localStorage`
- **Vision Provider 适配**：有图片时转换为 `[{type:"text"}, {type:"image_url"}]` content 数组；纯文本消息仍发送字符串，历史行为不变
- **Agent 模式支持图片**：Chat 与 Agent 共用同一转换入口，图片逻辑不进入 Agent Loop
- **不支持视觉的提示**：上游以 400/413/415/422 或图片相关错误拒绝时，追加"请确认模型支持 Vision"
- **前端压缩**：最长边 1568px，PNG 保留 PNG，其余转 JPEG；单张上限 5 MB，单条最多 4 张
- **配置项**：`WECI_MAX_IMAGE_BYTES`、`WECI_MAX_IMAGES_PER_MESSAGE`
- **测试**：`tests/test_multimodal.py` 覆盖转换、校验、Vision 提示与 API 400（共 116 个测试通过）

### 变更

- `backend/services/openai_client.py` 的消息类型放宽为 `list[dict[str, Any]]`，错误构造统一走 `build_upstream_error()`
- `backend/api/chat.py` 改用 `services/message.py` 的领域模型，移除本地重复的消息清洗逻辑

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
