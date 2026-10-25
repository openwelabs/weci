# WeCi

> 一个简洁、现代、开源、本地优先的 AI 对话客户端 + Agent 工作台。

WeCi 做两件事：**安静地聊天**，以及在 **Agent 模式**下让 AI 自主调用工具完成任务。
没有账号系统、没有数据库、没有多余的面板。对话历史保存在浏览器本地，
模型服务由你自己提供（任何 OpenAI-compatible API）。

[English](README.md) · [简体中文](README.zh-CN.md)

## 功能

**会话管理**

- 新建、切换、删除、重命名会话（双击标题也可重命名）
- 根据第一条消息自动生成会话标题
- 自动保存，刷新不丢失：历史保存在浏览器 `localStorage`

**消息**

- 流式回复（SSE），逐字渲染，生成中显示耗时与停止按钮
- 重新生成 AI 回复；编辑用户消息并重新发送（自动截断后续消息）
- 复制整条消息；代码块独立复制按钮
- Markdown 渲染：标题、列表、引用、表格、链接等
- 代码块语法高亮 + 语言标签
- LaTeX 数学公式（KaTeX）：行内 `$...$` 与块级 `$$...$$`
- 网络 / API 出错时显示清晰错误卡片，可一键重试

**图片输入（Vision）**

- 点击输入框的图片按钮选择图片，或直接把图片拖进输入框
- 发送前可预览、逐张删除附件
- 发送后图片正常展示在消息中，并随会话一起保存在本地
- Chat 与 Agent 模式都支持；请求使用 OpenAI-compatible 的 `image_url` 内容格式
- 不支持视觉的模型会返回明确提示，而不是静默失败

**文件附件（第一阶段）**

- 点击文件按钮或拖拽上传 `.txt` / `.md` / `.json` / `.csv` / `.py` / `.js` / `.ts` / `.html` / `.css` / `.pdf`
- 发送前显示文件名与大小，可逐张删除附件
- 文本类文件由服务端解码后作为带边界的上下文注入；PDF 使用 `pypdf` 做基础文本提取
- Chat 与 Agent 模式都支持；Agent 直接看到文件文本，Agent Loop 内没有任何文件处理逻辑
- 不支持的类型、超大文件、无法解析的 PDF 都会给出明确错误

**上下文管理（Context Manager）**

- Chat 与 Agent 共用同一套上下文构建逻辑，不再各自拼接消息
- 统一管理历史消息、Workspace、图片 / 文件附件与 Agent 工具结果
- 按模型上下文窗口估算 token；超出时自动压缩较早内容，保留系统设定、当前请求与最近消息
- 同一份附件 / Workspace 内容只注入一次，避免多轮重复占用上下文

**记忆（Memory，第一阶段）**

- 只在你明确要求时保存少量长期信息（例如「记住我使用 Fedora」）
- Agent 提供 `save_memory` / `delete_memory` 工具；普通聊天内容不会被自动保存
- 记忆按 `user` / `workspace` / `conversation` 隔离，需要时由 Context Manager 注入系统上下文
- 独立的记忆管理页可查看、新建、修改和删除记忆
- 保存在 `.weci/memory.json`，与聊天历史、Workspace 文件互不混用

**MCP Server（第一阶段）**

- WeCi 作为 **MCP Client** 连接本地 stdio MCP Server（`command` / `args` / `environment`）
- 连接后自动获取 Server 提供的 Tools，并注册进 Agent 已有的 `ToolRegistry`
- MCP Tool 直接使用 Server 自身的 JSON Schema；返回结果统一转换为 `ToolResult`
- 独立的 MCP 管理页支持添加、编辑、连接 / 断开、启用 / 禁用与删除 Server
- Server 生命周期（启动 / 停止 / 重连 / 崩溃）由独立的 MCP Manager 管理
- Server 断开或崩溃只影响它自己的工具调用，Agent 与其他工具继续工作

**模型 Provider**

- 统一的 Provider Manager：API Endpoint、API Key、模型与能力集中在一处管理
- 新增 / 编辑 / 删除 Provider，设置默认 Provider，并分别配置 Chat 模型与 Agent 模型
- 每个模型可声明 `text` / `vision` / `tool_calling` / `streaming` 四项能力；缺少能力时在发送请求前就给出明确提示
- 「测试 Provider」与「测试 Model」按钮，失败原因清晰可操作
- 模型菜单按 Provider 分组，直接展示 `✓ Vision`、`✓ Tool Calling`、`✓ Streaming`
- Key 保存在服务端 `.weci/providers.json`（权限 `0600`），不会返回给浏览器
- 旧的 Endpoint / Key / Model 输入保留在「兼容模式」中

**权限与安全**

- 统一的 **Permission Manager**：内置工具、插件工具与 MCP 工具共用同一套权限记录
- 带 scope 的权限：`workspace:read` / `workspace:write` / `workspace:delete` / `command:execute` / `network:access` / `memory:read` / `memory:write` / `mcp:use` / `plugin:use`
- **WeCi 不拦截任何操作**：Permission Manager 与 Security Policy 只记录状态，不阻止工具执行
- **三种记录模式**：Safe（记录“原本需要确认”）/ Auto Review（记录安全 / 危险判定）/ Risky（默认，记录为全放开）。切换模式不会改变执行结果
- 命令风险由结构化解析器判定（`shlex` 分词 + 管道 / 重定向 / `sh -c` 解析），`echo "rm -rf"` 不会被误判为删除，`rm -rf ./build` 会被识别
- **Workspace 不做路径限制**：绝对路径、`..`、符号链接与 Workspace 外路径都直接交给操作系统；文件工具与 `run_command` 不因路径被拒绝
- `run_command` 环境变量经脱敏过滤，支持 timeout 与取消，并记录 command / cwd / 模式 / 风险 / 原因 / exitCode
- 独立的「权限与安全」页面展示 Workspace、Provider、插件、MCP Server、工具权限与脱敏安全日志；Allow / Deny 仅记录偏好
- 安全日志永不包含 API Key、Token、Password、Cookie 或 Authorization

**界面**

- 深色 / 浅色主题
- 桌面端侧栏可收起；窄屏侧栏变为抽屉
- 响应式布局：桌面、平板、手机均可用
- 多行输入框：Enter 发送、Shift + Enter 换行、自动滚动到最新消息
- 快捷键：`Ctrl/⌘ + K` 新建对话、`Ctrl/⌘ + B` 收起/展开侧栏、`Esc` 关闭弹层

## Chat 模式 vs Agent 模式

顶栏提供 **Chat / Agent** 模式切换，每个会话会记住自己的模式。

- **Chat**：普通对话。模型的回复直接以流式 SSE 返回，不会调用工具。
- **Agent**：允许模型在需要时调用已注册的工具。后端运行一个
  [Agent Loop](#agent-loop)，把工具结果再交回模型继续推理，直到任务完成。
  没有最大步数限制，真正的边界来自用户 Stop、Tool Timeout、Loop Detection 与
  Context Management。

Agent 模式下，助手消息正文上方会显示「Agent 执行过程」，只呈现实际发生的执行事实，
不展示模型的隐藏推理过程：

- **执行状态**：运行中显示 `N tool calls · Running...`；完成后显示 `N tool calls · 2m 31s`；
  停止或中断显示 `N tool calls · 已停止`。不展示任何虚假的最大步数。
- **工具卡片**：展示状态图标（执行中转圈 / 成功 / 失败 / 已中断）、工具名、状态与耗时；
  失败时直接显示错误原因。
- **参数与结果**：默认折叠，点击「参数」「结果」可展开查看；超长结果（超过 2 万字符）
  会被截断并在卡片内滚动。
- **中断处理**：点击停止会立即中止流式请求，正在运行的卡片标记为「已中断」；
  页面刷新或网络断开后，未完成的回复同样标记为中断，不会永久停留在执行中。

## 官方技能（内置，默认开启）

| 技能 | 工具 | 说明 |
| --- | --- | --- |
| Calculator | `calculator` | 安全计算器（AST 解析，禁用 eval） |
| Current Time | `current_time` | 当前日期、时间与时区 |
| Web Search | `web_search`, `fetch_url` | 联网搜索（DuckDuckGo，免 Key）并抓取页面内容 |
| Computer | `run_command`, `read_file`, `write_file`, `list_dir`, `clipboard_get`, `clipboard_set` | 执行命令、读写文件、剪贴板 |
| Memory | `save_memory`, `delete_memory` | 仅在用户明确要求时保存 / 删除长期记忆 |

> **Computer** 技能让 Agent 能在你的机器上执行命令、修改文件。
> 请在信任 Agent 行为的前提下启用。

## 环境要求

- Python 3.10+（已在 3.14 上验证）
- 一个 OpenAI-compatible 模型服务端点（OpenAI、DeepSeek、Qwen、Ollama、LM Studio 等）

## 安装

### 方式一：从源码安装（推荐）

```bash
git clone https://github.com/openwelabs/weci.git
cd weci

python3 -m venv .venv
source .venv/bin/activate

pip install .
```

### 方式二：可编辑安装（开发用）

```bash
pip install -e .
```

## 快速开始

```bash
weci serve
```

打开 <http://127.0.0.1:8000>，点击右上角齿轮图标，填写：

- **API Endpoint**：例如 `https://api.openai.com/v1`（也可以填完整的 `.../v1/chat/completions`）
- **API Key**：云端服务的密钥（只保存在浏览器，不会写入源码）；Ollama / LM Studio 等本地服务可留空
- **Model**：例如 `gpt-4o-mini`、`deepseek-chat`、`qwen-plus`、`llama3.1`
- **Max Tokens**：留空表示不限制

点击「测试连接」确认配置可用——成功时会显示延迟并列出远端可用模型。

### 推荐：把 Key 放在服务端

```bash
cp .env.example .env
```

```dotenv
WECI_API_KEY=sk-your-key
WECI_DEFAULT_API_BASE=https://api.openai.com/v1
WECI_DEFAULT_MODEL=gpt-4o-mini
```

前端设置页中的 Key 留空时，会自动使用服务端 Key。`.env` 已被 `.gitignore` 忽略。

常见本地 Endpoint：

- Ollama：`http://127.0.0.1:11434/v1`，Model 例如 `llama3.1`
- LM Studio：`http://127.0.0.1:1234/v1`，Model 以 LM Studio 加载的模型名为准

### 开发模式

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

前端是纯静态文件，修改后刷新页面即可，无需构建。

## 命令行

```bash
weci serve                          # 启动 Web 服务
weci plugin list                    # 列出所有插件（状态、权限、工具）
weci plugin install <path>          # 安装本地插件目录
weci plugin remove <id>             # 移除用户插件（内置插件不可移除）
weci plugin enable <id>             # 启用插件
weci plugin disable <id>            # 禁用插件
```

## 项目结构

```
weci/
├── backend/
│   ├── main.py                 # FastAPI 入口：CORS、静态前端托管
│   ├── config.py               # 环境变量配置（无硬编码 Key）
│   ├── cli.py                  # weci CLI（serve、插件管理）
│   ├── api/
│   │   ├── chat.py             # POST /api/chat，SSE 流式转发（Chat / Agent）
│   │   ├── plugins.py          # 插件管理 REST API
│   │   ├── memory.py           # /api/memories CRUD
│   │   ├── mcp.py              # /api/mcp/servers REST API
│   │   └── system.py           # GET /api/health、/api/config，POST /api/test-connection
│   ├── mcp/                    # MCP 客户端（stdio JSON-RPC 2.0）
│   │   ├── client.py           # StdioMcpClient：initialize / tools/list / tools/call
│   │   └── manager.py          # McpManager：配置、生命周期、工具注册
│   ├── services/
│   │   ├── context.py          # Context Manager：统一 Context、token 预算、压缩、去重
│   │   ├── memory.py           # Memory 存储：scope 隔离、限额、JSON 持久化
│   │   ├── message.py          # 消息 / 图片 / 文件附件模型与 OpenAI-compatible 转换
│   │   ├── openai_client.py    # OpenAI-compatible 上游客户端（含工具调用流式解析）
│   │   ├── agent.py            # Agent Loop：Unlimited Execution、Loop Guard、SSE 事件
│   │   └── sse.py              # SSE 事件编码
│   ├── plugins/                # 插件系统核心
│   │   ├── manifest.py         # manifest.json 加载与校验
│   │   ├── loader.py           # PluginLoader：发现/加载/卸载/安装/移除
│   │   ├── registry.py         # PluginRegistry（插件 + 工具统一管理）
│   │   ├── context.py          # PluginContextImpl（受权限约束的上下文）
│   │   └── builtin/            # 内置插件（与第三方插件使用同一套 API）
│   │       ├── calculator/
│   │       ├── current_time/
│   │       ├── web_search/
│   │       ├── computer/
│   │       └── memory/
│   ├── tools/                  # 工具基础设施（ToolRegistry 供 Agent 使用）
│   │   ├── base.py             # 从 sdk.tool 重新导出（向后兼容）
│   │   ├── registry.py         # ToolRegistry：注册、查询、执行
│   │   └── permission.py       # ToolPermission
│   └── frontend/               # 静态前端（由 FastAPI 托管）
├── sdk/                        # Plugin SDK（插件唯一依赖的公开 API）
│   ├── plugin.py               # WeCiPlugin 基类
│   ├── tool.py                 # Tool 基类 + ToolExecutionResult
│   ├── context.py              # PluginContext 接口
│   └── permissions.py          # 权限常量
├── examples/
│   └── plugins/hello/          # 示例插件（hello_world 工具）
├── .env.example
├── LICENSE
├── README.md
└── README.zh-CN.md
```

## 插件系统

WeCi 的 Agent 能力完全由**插件系统**驱动。核心设计原则：

> **Agent 永远只与 `ToolRegistry` 交互，不感知任何具体工具或插件。**
> 新增 100 个插件，Agent Core 不需要修改一行代码。

```
Model Provider
      ↓
   Agent (services/agent.py)           ← 只调用 registry.schemas() / registry.execute()
      ↓
ToolRegistry (tools/registry.py)       ← 工具来自已启用的插件
      ↑
PluginRegistry (plugins/registry.py)   ← 管理插件生命周期，把插件工具注入 ToolRegistry
      ↑
PluginLoader (plugins/loader.py)       ← 扫描目录、校验 manifest、加载模块
      ↑
Plugin (sdk.WeCiPlugin)                ← 用户/内置插件，提供 Tool
      ↑
Tool (sdk.Tool)                        ← name / description / args_model / execute()
```

内置插件（`backend/plugins/builtin/`）与第三方用户插件使用**完全相同**的 Plugin API。

### SDK（`sdk/`）

插件**只应**依赖 `sdk` 包，不应导入 `backend.*` 内部模块。SDK 提供：

| 类 | 说明 |
| --- | --- |
| `WeCiPlugin` | 插件基类。实现 `on_load(context)` / `on_unload()` / `get_tools()` |
| `Tool` | 工具基类。声明 `name` / `description` / `args_model` / `execute(args, context)` |
| `ToolExecutionResult` | 工具执行的结构化结果 |
| `PluginContext` | 插件运行时上下文接口（logger / config / http / storage / process / fs / clipboard / events） |
| `PluginPermissionError` | 未授权能力调用时抛出 |

工具的 `args_model`（Pydantic）是参数 Schema 与运行时校验的**单一真源**：
OpenAI `parameters` 由 `model_json_schema()` 自动派生，调用时由 `model_validate()` 校验。

### Manifest

每个插件目录必须包含 `manifest.json`：

```json
{
  "id": "hello",
  "name": "Hello Plugin",
  "version": "0.1.0",
  "description": "示例插件",
  "author": "Your Name",
  "api_version": "0.1",
  "entry": "plugin.py",
  "permissions": [],
  "default_enabled": true
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 唯一标识，小写字母开头，2-64 字符，仅含 `a-z0-9_-` |
| `api_version` | 插件 API 版本，当前支持 `0.1` |
| `entry` | 入口模块相对路径（如 `plugin.py`） |
| `permissions` | 声明所需权限，见下方 |
| `default_enabled` | 首次安装时是否默认启用（默认 `true`） |

Loader 会校验 manifest；不合法的插件标记为 `failed`，**不影响 WeCi 启动**。

### 生命周期

```
Discover → Validate → Load → Register → Ready → Disable → Unload
```

- WeCi 启动时自动扫描 `backend/plugins/builtin/` 与用户插件目录。
- 加载失败的插件在日志中记录 `Plugin <id> failed to load: <error>`，前端显示 ⚠。
- 启用/禁用状态持久化在 `<项目>/.weci/plugin_state.json`。

### 权限系统

插件在 manifest 的 `permissions` 中声明所需能力，`PluginContext` 在调用时校验；
同时每次工具调用都会先经过统一的 **Permission Manager**。推荐使用带 scope 的
新权限名，旧权限名会自动归一化。

| 权限 | 能力 |
| --- | --- |
| `network:access` | `context.http_request()` |
| `storage:read` / `storage:write` | `context.storage_get()` / `storage_set()` |
| `workspace:read` | `context.read_file()` / `list_dir()` |
| `workspace:write` | `context.write_file()` / `append_file()` |
| `workspace:delete` | `context.delete_file()` |
| `command:execute` | `context.run_command()` |
| `clipboard:access` | `context.clipboard_get()` / `clipboard_set()` |
| `memory:read` / `memory:write` | `context.memory_list()` / `memory_save()` / `memory_delete()` |
| `mcp:use` | MCP Server 注册的工具 |

`network`、`storage`、`filesystem.read`、`filesystem.write`、`filesystem.delete`、
`process`、`clipboard`、`memory` 等旧名称会在加载时自动归一化为上述新权限，
现有插件无需修改。

未声明权限的调用会抛 `PermissionDenied`。


> **信任模型**：第三方 Python 插件本质上可执行任意 Python 代码，因此权限系统是
> 「约定 + 运行时拦截」，**并非沙箱**。安装插件即意味着信任其代码。真正的隔离
> （subprocess / WASM / container）属于未来工作。请勿安装来源不明的插件。

### 如何开发一个插件

1. 创建插件目录，包含 `manifest.json` 与入口文件：

```
my_plugin/
├── manifest.json
└── plugin.py
```

2. `plugin.py`：

```python
from pydantic import BaseModel, Field
from sdk import Tool, WeCiPlugin

class HelloArgs(BaseModel):
    name: str = Field(default="World", description="要问候的名字。")

class HelloWorldTool(Tool):
    name = "hello_world"
    description = "向指定名字打招呼。"
    args_model = HelloArgs

    async def execute(self, args: HelloArgs, context=None) -> str:
        return f"Hello, {args.name or 'World'}!"

class MyPlugin(WeCiPlugin):
    def on_load(self, context):
        context.logger.info("my plugin loaded")

    def get_tools(self):
        return [HelloWorldTool()]
```

3. 安装到用户插件目录：

```bash
weci plugin install ./my_plugin
# 或
cp -r my_plugin ~/.weci/plugins/
```

重启 WeCi 后，Agent 即可看到 `hello_world` 工具。完整示例见
[`examples/plugins/hello/`](examples/plugins/hello/)。

### 开发自己的插件

完整文档：[`docs/plugins/`](docs/plugins/README.md)

推荐使用官方开发工具
[**weci-maker**](https://github.com/openwelabs/weci-maker)（类似 Cargo 的插件 CLI）：

```bash
# 0. 先安装一次 weci-maker（它复用 WeCi 的 SDK 与加载器）
pip install -e ~/repos/weci
pip install -e ~/repos/weci-maker

# 1. 创建插件项目（manifest.json + plugin.py + tests/）
weci-maker new my-plugin
cd my-plugin

# 2. 修改 manifest.json（id、name、version、permissions）

# 3. 在 plugin.py 中实现工具

# 4. 检查（manifest、入口模块、WeCiPlugin 子类、工具 schema）
weci-maker check

# 5. 运行插件自测试
python -m pytest tests/

# 6. 构建可分发的 .wcp 插件包
weci-maker build            # → dist/my-plugin-0.1.0.wcp

# 7. 安装到 ~/.weci/plugins/（与 `weci plugin install` 等价）
weci-maker install dist/my-plugin-0.1.0.wcp
```

重启 WeCi 后，你的工具会自动出现在 Agent 中。

不使用 weci-maker 也可以手动开发：复制 `examples/plugins/template/`，
然后 `weci plugin validate ./my-plugin` 校验、`weci plugin install ./my-plugin` 安装。

### Agent Loop

`backend/services/agent.py` 中的 `run_agent()` 是一个 async generator：

```
用户消息
   ↓
模型分析（带 tools schema，schema 来自已启用插件）
   ↓
模型返回 tool_calls？── 否 ──→ 直接输出内容 → done
   ↓ 是
逐个执行 tool（ToolRegistry → execute）
   ↓
把 assistant tool_calls + tool 结果写回 ContextManager
   ↓
重新 build() 生成压缩后的 Context，再次发送给模型（回到「模型分析」）
   ↓
直到任务完成 / 用户 Stop / Provider 错误 / Loop Detection / Context 超限
```

Agent 默认 **Unlimited Execution**：没有最大步数，每一步对应一次模型调用。
步数、Tool Call 数量与耗时只作为统计信息展示，不再是停止条件。真正的边界是
用户 Stop + Security Policy + Tool Timeout + Loop Detection + Context Manager。

Loop Protection 会检测连续相同的 Tool + 参数（默认 3 次）与连续相同错误，
触发时返回结构化错误 `LOOP_DETECTED` 让模型重新规划；同一 Run 内重复触发
则结束 Run（`error_type=loop`）。

## MCP（Model Context Protocol）

WeCi 可以作为 **MCP Client**。外部 MCP Server 提供的工具会被注册进插件使用的同一个
`ToolRegistry`，Agent 依然只依赖 `ToolRegistry`：

```
                 WeCi Agent
                     ↓
                ToolRegistry
                ↙         ↘
          WeCi Plugin     MCP（backend/mcp/）
                            ↓
                       MCP Server
```

```
Plugin Tool ──┐
Built-in Tool ─┼→ ToolRegistry → Agent
MCP Tool ─────┘
```

**第一阶段范围**

- 仅支持本地 **stdio** Server：WeCi 以子进程方式启动 Server，通过 stdin / stdout 收发按行分隔的 JSON-RPC 2.0（stderr 仅作日志）。
- 协议子集：`initialize` → `notifications/initialized` → `tools/list`（支持 `nextCursor` 分页）→ `tools/call`。
- 不包含 Marketplace、远程 Server 管理、OAuth、自动发现，也**不提供沙箱隔离** —— 添加 MCP Server 即表示信任它执行的本地命令。

**配置**

Server 配置保存在 `.weci/mcp_servers.json`，可在顶栏的 MCP 页面（或设置面板入口）管理：

| 字段 | 说明 |
| --- | --- |
| `name` | 显示名称（同时作为工具前缀的一部分） |
| `command` | 要启动的可执行命令，如 `npx`、`python3`、`uvx` |
| `args` | 传给命令的参数列表 |
| `environment` | 额外环境变量（`KEY=VALUE`），会覆盖进程原有同名变量 |
| `enabled` | 禁用时停止 Server 并注销其工具 |

```bash
# 通过 REST API 添加并立即连接
curl -X POST http://127.0.0.1:8000/api/mcp/servers \
  -H 'Content-Type: application/json' \
  -d '{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}'
```

**生命周期**

`backend/mcp/manager.py` 中的 `McpManager` 管理完整生命周期
（`disabled` → `stopped` → `connecting` → `ready` / `error`）：

- FastAPI 启动时连接所有已启用的 Server，关闭时统一终止子进程。
- 连接成功后获取工具列表，把 `McpTool` 注册进 `ToolRegistry`；断开 / 禁用时注销。
- 工具名统一为 `mcp__<server_id>__<tool>`（净化并截断到 64 字符），不会覆盖插件工具；冲突时跳过并记录日志。
- Server 意外退出时，正在进行的调用会收到明确错误，且只有该 Server 的工具失效，Agent 本身继续运行。

**工具映射**

- 参数：直接使用 Server 的 `inputSchema` 作为工具 schema（OpenAI 兼容），WeCi 透传参数，由 Server 校验。
- 结果：拼接 `content[].text`，包含 `resource.text`；图片 / 音频转为 `[image: mime]` / `[audio: mime]` 占位；`isError: true` 映射为失败的 `ToolResult`。

**REST API**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/mcp/servers` | 列出 Server（状态、工具、最近错误） |
| `POST` | `/api/mcp/servers` | 添加 Server 并立即连接 |
| `PATCH` | `/api/mcp/servers/{id}` | 更新配置（命令 / 参数 / 环境变化时自动重连） |
| `DELETE` | `/api/mcp/servers/{id}` | 停止并删除 Server |
| `POST` | `/api/mcp/servers/{id}/connect` | 连接 / 重连 Server |
| `POST` | `/api/mcp/servers/{id}/disconnect` | 断开 Server 并注销其工具 |

## 上下文管理（Context Manager）

所有发送给模型的内容统一经过 `backend/services/context.py`：

```
Conversation + Workspace + Attachment + Tool Result
        ↓
  ContextManager（统一结构 + token 预算 + 压缩 / 去重）
        ↓
  ModelContext（最终消息列表）
        ↓
  Provider（openai_client）
        ↓
       Model
```

- **统一入口**：Chat 与 Agent 都调用 `ContextManager.from_chat_messages()`；Agent 每轮把工具调用与结果写回同一个 Context 后重新 `build()`。
- **优先级**：System Context → 当前用户请求 → 最近消息 → 必要 Tool Results → 较旧消息。
- **预算**：按模型名推断上下文窗口（如 `gpt-3.5` / `deepseek` 取更保守上限），扣除预留输出 tokens 后得到可用预算；可用 `WECI_CONTEXT_*` 调整。
- **压缩**：超限时先去掉较旧消息中的图片、截断过长的文件 / 工具结果，再从最旧的消息开始省略；系统设定、当前请求与最近消息最后才处理。
- **去重**：相同文件内容、相同图片以及 Workspace 与附件重复内容只注入一次。
- **Workspace**：`POST /api/chat` 可传可选 `workspace` 对象（`name` / `root` / `description` / `instructions` / `files`），作为 System Context 注入。第一阶段只支持文本，不做文件同步或索引。
- **Memory**：按请求的 `user_id` / `workspace_id` / `conversation_id` 读取可见记忆，渲染为 `<memories>` 系统上下文；条数与字符数由 `WECI_CONTEXT_MEMORY_*` 控制。

## 模型 Provider

所有模型配置收敛到一条链路：

```
Provider Manager → Model → Capabilities → Chat / Agent / Multimodal
```

Chat、Agent 与图片输入不再各自维护 Endpoint / Key / Model。`POST /api/chat` 新增可选字段 `provider_id`；未传时使用默认 Provider。只传 `api_base` + `model` 的旧请求完全保持兼容。

**数据结构**

| 层级 | 字段 |
| --- | --- |
| Provider | `id`、`name`、`description`、`api_base`、`enabled`、是否默认 |
| Provider 模型配置 | `default_model`、`chat_model`、`agent_model`（Chat / Agent 分别使用） |
| Model | `id`、`display_name`、`capabilities`、`context_window`、`max_tokens` |
| Capability | `text`、`vision`、`tool_calling`、`streaming` |

能力默认均为 `true`；`vision` 会按模型名推断（`gpt-4o`、`claude`、`gemini`、`qwen-vl`、`llava` 等），也可以按模型手动覆盖。模型声明 `streaming: false` 时，WeCi 会自动退回非流式请求，而不是直接失败。

**能力预检**在请求上游之前完成：缺少 `text` 不能对话；缺少 `vision` 会拒绝图片；Agent 模式要求 `tool_calling`。不满足时 API 直接返回 `400` 并附带可读说明。

**端点**

| 方法 | 路径 |
| --- | --- |
| `GET` | `/api/providers` |
| `POST` | `/api/providers` |
| `PATCH` / `DELETE` | `/api/providers/{provider_id}` |
| `POST` | `/api/providers/{provider_id}/models` |
| `PATCH` / `DELETE` | `/api/providers/{provider_id}/models/{model_id}` |
| `POST` | `/api/providers/{provider_id}/test` |
| `POST` | `/api/providers/{provider_id}/models/{model_id}/test` |

**API Key 安全**

- Provider 的 Key 保存在服务端 `.weci/providers.json`，写入后设置 `0600` 权限。
- 公开响应只包含 `has_api_key`，不会出现明文 Key；日志、错误信息与 SSE 事件同样不包含 Key。
- 更新 Provider 时省略 `api_key` 表示不修改，传 `""` 表示清除。
- Provider 未配置 Key 时，回退使用请求头 `X-WeCi-API-Key` 或服务端环境变量。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WECI_HOST` | `127.0.0.1` | 监听地址；局域网访问可设为 `0.0.0.0` |
| `WECI_PORT` | `8000` | 监听端口 |
| `WECI_DEFAULT_API_BASE` | `https://api.openai.com/v1` | 默认 Endpoint |
| `WECI_DEFAULT_MODEL` | `gpt-4o-mini` | 默认模型 |
| `WECI_DEFAULT_TEMPERATURE` | `0.7` | 默认温度 |
| `WECI_DEFAULT_MAX_TOKENS` | 空（不限制） | 默认 max_tokens |
| `WECI_API_KEY` | 空 | 服务端 Key；也兼容 `OPENAI_API_KEY` |
| `WECI_CONNECT_TIMEOUT` | `10` | 上游连接超时（秒） |
| `WECI_READ_TIMEOUT` | `300` | 上游流式读取超时（秒） |
| `WECI_MAX_IMAGE_BYTES` | `5242880`（5 MB） | 单张图片解码后的最大字节数 |
| `WECI_MAX_IMAGES_PER_MESSAGE` | `4` | 单条用户消息最多图片数 |
| `WECI_MAX_FILE_BYTES` | `5242880`（5 MB） | 单个文件附件的最大字节数 |
| `WECI_MAX_FILES_PER_MESSAGE` | `4` | 单条用户消息最多文件数 |
| `WECI_MAX_FILE_TEXT_CHARS` | `100000` | 单个文件提取后的最大字符数（文本 / PDF） |
| `WECI_CONTEXT_WINDOW_TOKENS` | `128000` | 默认上下文窗口；具体模型会取更保守的已知上限 |
| `WECI_CONTEXT_RESERVE_TOKENS` | `4096` | 预留的模型输出 tokens（请求显式 max_tokens 时以请求为准） |
| `WECI_CONTEXT_MIN_RECENT_MESSAGES` | `6` | 压缩时尽量保留的最近消息条数 |
| `WECI_CONTEXT_IMAGE_TOKENS` | `1000` | 单张图片在预算中的估算 tokens |
| `WECI_CONTEXT_WORKSPACE_MAX_CHARS` | `20000` | Workspace 文本注入的最大字符数 |
| `WECI_CONTEXT_MEMORY_MAX_ITEMS` | `20` | 单次注入上下文的最大记忆条数 |
| `WECI_CONTEXT_MEMORY_MAX_CHARS` | `4000` | 单次注入上下文的记忆总字符上限 |
| `WECI_MEMORY_MAX_PER_SCOPE` | `50` | 单个 scope 内最多保存的记忆条数 |
| `WECI_MEMORY_MAX_CONTENT_CHARS` | `500` | 单条记忆内容的最大字符数 |
| `WECI_MCP_CONNECT_TIMEOUT` | `20` | MCP `initialize` / `tools/list` 超时（秒） |
| `WECI_MCP_CALL_TIMEOUT` | `120` | 单次 MCP `tools/call` 超时（秒） |
| `WECI_CORS_ORIGINS` | `*` | 允许的跨域来源，逗号分隔 |
| `WECI_WEB_SEARCH_PROVIDER` | `duckduckgo` | 搜索 Provider：`duckduckgo`（免 Key）或 `tavily` |
| `TAVILY_API_KEY` | 空 | 当 provider 为 `tavily` 时必填 |
| `WECI_PLUGINS_DIR` | `~/.weci/plugins` | 用户插件目录 |

## 数据与隐私

- 对话历史、设置、主题都只保存在当前浏览器的 `localStorage`。
- 记忆（Memory）保存在本地后端 `.weci/memory.json`，不会进入聊天历史，也不会写入 Workspace 文件。
- Provider 配置及其 API Key 保存在服务端 `.weci/providers.json`（权限 `0600`）；浏览器只拿到 `has_api_key`。
- MCP Server 配置保存在 `.weci/mcp_servers.json`。连接 Server 会以**你当前用户的权限**把它的命令作为本地子进程运行，请只添加可信的 Server；第一阶段不提供沙箱隔离。
- 兼容模式下，浏览器中的 API Key 会随每次请求发送到**你自己的**本地后端，再由后端转发给模型服务。
- 本应用不包含统计、遥测或任何第三方上报。
- 若在多人共用的电脑上使用，建议把 Key 放在服务端 `.env`，不要在浏览器中保存。

## 已知限制

- 没有多用户与鉴权；请仅在本机或可信网络中使用，不要直接暴露到公网。
- 对话历史不会跨设备同步。
- token 估算使用保守的启发式（非模型官方 tokenizer）；超长对话会自动压缩，较早的消息或图片可能被省略。
- Agent 模式要求模型在 Provider 配置中启用 `tool_calling`。
- 图片输入要求模型在 Provider 配置中启用 `vision`；未启用时会在请求前直接给出明确提示
- Provider 仅支持 OpenAI-compatible API（Endpoint + `/chat/completions`）；暂不支持各厂商原生 API
- 模型能力是声明值而非自动探测结果；WeCi 信任你的配置，只在发送请求前做检查
- 文件附件仅支持上文列出的扩展名；暂不支持 Office 格式、压缩包与二进制文件。没有文本层的扫描版 PDF 提取不到内容。
- 文件文本直接随用户消息发送；提取后超过单文件字符上限的文件会给出明确错误，而不是通过 RAG 分块召回。
- 部分上游服务的流式 `tool_calls` 格式不规范时，后端会自动退回非流式调用。
- MCP 目前仅支持本地 stdio Server；远程（HTTP / SSE）传输、OAuth、自动发现与沙箱隔离尚未实现。Server 工具的安全性取决于你配置的命令。

## Roadmap

- 插件市场与远程下载
- MCP 远程传输（HTTP / SSE）与 Server 自动发现
- 插件沙箱隔离（subprocess / WASM / container）
- RAG / 向量数据库
- 文件工作区（多轮文件管理与 Office 格式）
- 多模态（音频 / 视频输入）
- 用户登录与多用户隔离
- 云端同步

## License

[MIT](./LICENSE) © 2026 WeCi contributors
