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

**模型设置**

- 设置页：API Endpoint、API Key、Model、Temperature、Max Tokens
- 「测试连接」：一键检测 Endpoint / Key / 模型可用性，并拉取远端模型列表
- 顶栏模型胶囊快速切换最近使用的模型
- 设置保存后立即生效；Key 默认隐藏，只保存在浏览器（或服务端 `.env`）

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
  [Agent Loop](#agent-loop)，把工具结果再交回模型继续推理，最多循环
  `MAX_AGENT_STEPS`（默认 10）步，防止无限运行。

Agent 模式下，前端会在 AI 消息正文上方显示简洁的工具调用卡片，仅展示
**工具名称、执行状态、耗时**，不暴露参数或返回内容。

## 官方技能（内置，默认开启）

| 技能 | 工具 | 说明 |
| --- | --- | --- |
| Calculator | `calculator` | 安全计算器（AST 解析，禁用 eval） |
| Current Time | `current_time` | 当前日期、时间与时区 |
| Web Search | `web_search`, `fetch_url` | 联网搜索（DuckDuckGo，免 Key）并抓取页面内容 |
| Computer | `run_command`, `read_file`, `write_file`, `list_dir`, `clipboard_get`, `clipboard_set` | 执行命令、读写文件、剪贴板 |

> **Computer** 技能让 Agent 能在你的机器上执行命令、修改文件。
> 请在信任 Agent 行为的前提下启用。

## 环境要求

- Python 3.10+（已在 3.14 上验证）
- 一个 OpenAI-compatible 模型服务端点（OpenAI、DeepSeek、Qwen、Ollama、LM Studio 等）

## 安装

### 方式一：从源码安装（推荐）

```bash
git clone https://github.com/your-name/weci.git
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
│   │   └── system.py           # GET /api/health、/api/config，POST /api/test-connection
│   ├── services/
│   │   ├── openai_client.py    # OpenAI-compatible 上游客户端（含工具调用流式解析）
│   │   ├── agent.py            # Agent Loop：MAX_AGENT_STEPS、工具编排、SSE 事件
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
│   │       └── computer/
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

插件在 manifest 的 `permissions` 中声明所需能力，`PluginContext` 在调用时校验：

| 权限 | 能力 |
| --- | --- |
| `network` | `context.http_request()` |
| `storage` | `context.storage_get()` / `storage_set()` |
| `filesystem.read` | `context.read_file()` / `list_dir()` |
| `filesystem.write` | `context.write_file()` |
| `process` | `context.run_command()` |
| `clipboard` | `context.clipboard_get()` / `clipboard_set()` |

未声明权限的调用会抛 `PluginPermissionError`。

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

```bash
# 1. 复制模板
cp -r examples/plugins/template my-plugin
cd my-plugin

# 2. 修改 manifest.json（id、name、version、permissions）

# 3. 在 plugin.py 中实现工具

# 4. 安装前校验
weci plugin validate ./my-plugin

# 5. 安装并启用
weci plugin install ./my-plugin
weci plugin enable my-plugin

# 6. 查看详情
weci plugin info my-plugin
```

重启 WeCi 后，你的工具会自动出现在 Agent 中。

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
把 assistant tool_calls + tool 结果追加到消息
   ↓
再次发送给模型（回到「模型分析」）
   ↓
步数 ≥ MAX_AGENT_STEPS？→ 发出 max_steps 事件并结束
```

`MAX_AGENT_STEPS = 10`（可在 `agent.py` 顶部修改）。每一步对应一次模型调用。

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
| `WECI_CORS_ORIGINS` | `*` | 允许的跨域来源，逗号分隔 |
| `WECI_WEB_SEARCH_PROVIDER` | `duckduckgo` | 搜索 Provider：`duckduckgo`（免 Key）或 `tavily` |
| `TAVILY_API_KEY` | 空 | 当 provider 为 `tavily` 时必填 |
| `WECI_PLUGINS_DIR` | `~/.weci/plugins` | 用户插件目录 |

## 数据与隐私

- 对话历史、设置、主题都只保存在当前浏览器的 `localStorage`。
- 浏览器中的 API Key 会随每次请求发送到**你自己的**本地后端，再由后端转发给模型服务。
- 本应用不包含统计、遥测或任何第三方上报。
- 若在多人共用的电脑上使用，建议把 Key 放在服务端 `.env`，不要在浏览器中保存。

## 已知限制

- 没有多用户与鉴权；请仅在本机或可信网络中使用，不要直接暴露到公网。
- 对话历史不会跨设备同步。
- 每次请求都会发送完整上下文；超长对话会受模型上下文窗口限制。
- Agent 模式依赖上游模型支持 OpenAI `tools` / function calling；不支持的模型在 Chat 模式下工作正常，Agent 模式可能无法触发工具调用。
- 部分上游服务的流式 `tool_calls` 格式不规范时，后端会自动退回非流式调用。

## Roadmap

- 插件市场与远程下载
- MCP（Model Context Protocol）工具接入
- 插件沙箱隔离（subprocess / WASM / container）
- RAG / 向量数据库
- 多模态（图片输入）
- 用户登录与多用户隔离
- 云端同步

## License

[MIT](./LICENSE) © 2026 WeCi contributors
