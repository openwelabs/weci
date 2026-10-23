# WeCi

> A clean, modern, open-source, local-first AI chat client + Agent workbench.

WeCi does two things: **chat quietly**, and in **Agent mode** let the AI call
tools autonomously to get things done. No accounts, no database, no extra
panels. Conversation history lives in your browser; the model service is yours
(any OpenAI-compatible API).

[English](README.md) · [简体中文](README.zh-CN.md)

## Features

**Conversations**

- New, switch, delete, rename chats (double-click the title to rename)
- Auto-generated title from the first message
- Auto-saved to browser `localStorage` — survives refresh

**Messages**

- Streaming replies (SSE), token-by-token rendering, with elapsed time and a stop button
- Regenerate AI replies; edit a user message and resend (truncates later messages)
- Copy a whole message; per-block copy button for code
- Markdown: headings, lists, quotes, tables, links
- Code syntax highlighting with language label
- LaTeX math (KaTeX): inline `$...$` and block `$$...$$`
- Clear error cards with one-click retry on network/API failures

**Model settings**

- API Endpoint, API Key, Model, Temperature, Max Tokens
- "Test connection" — checks Endpoint/Key/Model and lists remote models
- Top-bar model chip to quickly switch recently used models
- Settings apply immediately; Key is hidden by default and stored only in the browser (or server `.env`)

**UI**

- Dark / light theme
- Collapsible sidebar on desktop; drawer on narrow screens
- Responsive: desktop, tablet, phone
- Multi-line input: Enter to send, Shift+Enter for newline, auto-scroll
- Shortcuts: `Ctrl/⌘ + K` new chat, `Ctrl/⌘ + B` toggle sidebar, `Esc` close overlays

## Chat vs Agent mode

The top bar has a **Chat / Agent** switch; each chat remembers its own mode.

- **Chat**: plain conversation. The model's reply streams back via SSE with no tool calls.
- **Agent**: the model may call registered tools. The backend runs an
  [Agent Loop](#agent-loop), feeds tool results back to the model, and repeats
  up to `MAX_AGENT_STEPS` (default 10) to prevent runaway loops.

In Agent mode the UI shows compact tool-call cards above the AI message with
**tool name, status, and duration only** — parameters and raw output are not shown.

## Official skills (built-in, enabled by default)

| Skill | Tools | Description |
| --- | --- | --- |
| Calculator | `calculator` | Safe calculator (AST-based, no `eval`) |
| Current Time | `current_time` | Current date, time, and timezone |
| Web Search | `web_search`, `fetch_url` | Search the web (DuckDuckGo, no key) and fetch page content |
| Computer | `run_command`, `read_file`, `write_file`, `list_dir`, `clipboard_get`, `clipboard_set` | Run shell commands, read/write files, manage clipboard |

> The **Computer** skill lets the Agent execute commands and modify files on your
> machine. Enable it only if you trust what the Agent will do.

## Requirements

- Python 3.10+ (tested on 3.14)
- An OpenAI-compatible model endpoint (OpenAI, DeepSeek, Qwen, Ollama, LM Studio, etc.)

## Installation

### Option 1: Install from source (recommended)

```bash
git clone https://github.com/your-name/weci.git
cd weci

python3 -m venv .venv
source .venv/bin/activate

pip install .
```

### Option 2: Editable install (for development)

```bash
pip install -e .
```

## Quick start

```bash
weci serve
```

Open <http://127.0.0.1:8000>, click the gear icon (top right) and fill in:

- **API Endpoint**: e.g. `https://api.openai.com/v1` (or the full `.../v1/chat/completions`)
- **API Key**: your key (stored only in the browser); leave empty for local services like Ollama / LM Studio
- **Model**: e.g. `gpt-4o-mini`, `deepseek-chat`, `qwen-plus`, `llama3.1`
- **Max Tokens**: leave empty for no limit

Click **Test connection** to verify — on success it shows latency and lists available models.

### Put the key on the server (recommended)

```bash
cp .env.example .env
```

```dotenv
WECI_API_KEY=sk-your-key
WECI_DEFAULT_API_BASE=https://api.openai.com/v1
WECI_DEFAULT_MODEL=gpt-4o-mini
```

When the browser Key field is empty, the server-side key is used. `.env` is gitignored.

Common local endpoints:

- Ollama: `http://127.0.0.1:11434/v1`, model e.g. `llama3.1`
- LM Studio: `http://127.0.0.1:1234/v1`, model as loaded in LM Studio

### Development mode

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The frontend is pure static files — just refresh the page after editing, no build step.

## CLI

```bash
weci serve                          # Start the web server
weci plugin list                    # List all plugins (status, permissions, tools)
weci plugin install <path>          # Install a local plugin directory
weci plugin remove <id>             # Remove a user plugin (built-in cannot be removed)
weci plugin enable <id>             # Enable a plugin
weci plugin disable <id>            # Disable a plugin
```

## Project layout

```
weci/
├── backend/
│   ├── main.py                 # FastAPI entry: CORS, static frontend serving
│   ├── config.py               # Environment variable config (no hardcoded keys)
│   ├── cli.py                  # `weci` CLI (serve, plugin management)
│   ├── api/
│   │   ├── chat.py             # POST /api/chat, SSE streaming (Chat / Agent)
│   │   ├── plugins.py          # Plugin management REST API
│   │   └── system.py           # GET /api/health, /api/config, POST /api/test-connection
│   ├── services/
│   │   ├── openai_client.py    # OpenAI-compatible client (streaming tool-call parsing)
│   │   ├── agent.py            # Agent Loop: MAX_AGENT_STEPS, tool orchestration, SSE
│   │   └── sse.py              # SSE event encoding
│   ├── plugins/                # Plugin system core
│   │   ├── manifest.py         # manifest.json loading & validation
│   │   ├── loader.py           # PluginLoader: discover / load / unload / install / remove
│   │   ├── registry.py         # PluginRegistry (plugins + tools unified management)
│   │   ├── context.py          # PluginContextImpl (permission-gated capabilities)
│   │   └── builtin/            # Built-in plugins (same API as third-party)
│   │       ├── calculator/
│   │       ├── current_time/
│   │       ├── web_search/
│   │       └── computer/
│   ├── tools/                  # Tool infrastructure (ToolRegistry for the Agent)
│   │   ├── base.py             # Re-export from sdk.tool (backward compat)
│   │   ├── registry.py         # ToolRegistry: register, query, execute
│   │   └── permission.py       # ToolPermission
│   └── frontend/               # Static frontend (served by FastAPI)
├── sdk/                        # Plugin SDK (the only public API plugins should depend on)
│   ├── plugin.py               # WeCiPlugin base class
│   ├── tool.py                 # Tool base class + ToolExecutionResult
│   ├── context.py              # PluginContext interface
│   └── permissions.py          # Permission constants
├── examples/
│   └── plugins/hello/          # Example plugin (hello_world tool)
├── .env.example
├── LICENSE
├── README.md
└── README.zh-CN.md
```

## Plugin system

WeCi's Agent capabilities are entirely driven by the **plugin system**. The core
principle:

> **The Agent only ever talks to `ToolRegistry` — it knows nothing about specific
> tools or plugins.** Add 100 plugins and the Agent Core needs zero changes.

```
Model Provider
      ↓
   Agent (services/agent.py)           ← only calls registry.schemas() / registry.execute()
      ↓
ToolRegistry (tools/registry.py)       ← tools come from enabled plugins
      ↑
PluginRegistry (plugins/registry.py)   ← manages plugin lifecycle, injects tools into ToolRegistry
      ↑
PluginLoader (plugins/loader.py)       ← scans dirs, validates manifest, loads modules
      ↑
Plugin (sdk.WeCiPlugin)                ← user / built-in plugin, provides Tools
      ↑
Tool (sdk.Tool)                        ← name / description / args_model / execute()
```

Built-in plugins (`backend/plugins/builtin/`) and third-party user plugins use
the **exact same** Plugin API.

### SDK (`sdk/`)

Plugins **should** only depend on the `sdk` package, never import `backend.*`.

| Class | Description |
| --- | --- |
| `WeCiPlugin` | Plugin base class. Implement `on_load(context)` / `on_unload()` / `get_tools()` |
| `Tool` | Tool base class. Declare `name` / `description` / `args_model` / `execute(args, context)` |
| `ToolExecutionResult` | Structured tool execution result |
| `PluginContext` | Plugin runtime context (logger / config / http / storage / process / fs / clipboard / events) |
| `PluginPermissionError` | Raised when an unauthorised capability is called |

A tool's `args_model` (Pydantic) is the single source of truth for both the
OpenAI `parameters` schema (`model_json_schema()`) and runtime validation
(`model_validate()`).

### Manifest

Each plugin directory must contain `manifest.json`:

```json
{
  "id": "hello",
  "name": "Hello Plugin",
  "version": "0.1.0",
  "description": "Example plugin",
  "author": "Your Name",
  "api_version": "0.1",
  "entry": "plugin.py",
  "permissions": [],
  "default_enabled": true
}
```

| Field | Description |
| --- | --- |
| `id` | Unique ID, lowercase letter first, 2–64 chars, `a-z0-9_-` only |
| `api_version` | Plugin API version, currently `0.1` |
| `entry` | Entry module relative path (e.g. `plugin.py`) |
| `permissions` | Declared capabilities (see below) |
| `default_enabled` | Whether the plugin is enabled on first install (default `true`) |

An invalid manifest marks the plugin as `failed` — **it never breaks WeCi startup**.

### Lifecycle

```
Discover → Validate → Load → Register → Ready → Disable → Unload
```

- WeCi auto-scans `backend/plugins/builtin/` and the user plugins dir on startup.
- Failed plugins are logged as `Plugin <id> failed to load: <error>` and shown with ⚠ in the UI.
- Enable/disable state is persisted in `<project>/.weci/plugin_state.json`.

### Permissions

Plugins declare capabilities in `permissions`; `PluginContext` enforces at call time:

| Permission | Capability |
| --- | --- |
| `network` | `context.http_request()` |
| `storage` | `context.storage_get()` / `storage_set()` |
| `filesystem.read` | `context.read_file()` / `list_dir()` |
| `filesystem.write` | `context.write_file()` |
| `process` | `context.run_command()` |
| `clipboard` | `context.clipboard_get()` / `clipboard_set()` |

Calls without the declared permission raise `PluginPermissionError`.

> **Trust model**: A third-party Python plugin can execute arbitrary Python. The
> permission system is **declaration + runtime checks**, **not a sandbox**.
> Installing a plugin means trusting its code. True isolation
> (subprocess / WASM / container) is future work. Do not install plugins from
> untrusted sources.

### Writing a plugin

1. Create a plugin directory with `manifest.json` and an entry file:

```
my_plugin/
├── manifest.json
└── plugin.py
```

2. `plugin.py`:

```python
from pydantic import BaseModel, Field
from sdk import Tool, WeCiPlugin

class HelloArgs(BaseModel):
    name: str = Field(default="World", description="Name to greet.")

class HelloWorldTool(Tool):
    name = "hello_world"
    description = "Greet a name."
    args_model = HelloArgs

    async def execute(self, args: HelloArgs, context=None) -> str:
        return f"Hello, {args.name or 'World'}!"

class MyPlugin(WeCiPlugin):
    def on_load(self, context):
        context.logger.info("my plugin loaded")

    def get_tools(self):
        return [HelloWorldTool()]
```

3. Install:

```bash
weci plugin install ./my_plugin
# or
cp -r my_plugin ~/.weci/plugins/
```

Restart WeCi and the Agent sees `hello_world`. Full example in
[`examples/plugins/hello/`](examples/plugins/hello/).

### Agent Loop

`backend/services/agent.py` `run_agent()` is an async generator:

```
user message
   ↓
model reasons (with tools schema from enabled plugins)
   ↓
model returns tool_calls? ── no ──→ output content → done
   ↓ yes
execute each tool (ToolRegistry → execute)
   ↓
append assistant tool_calls + tool results to messages
   ↓
send to model again (back to "model reasons")
   ↓
steps ≥ MAX_AGENT_STEPS? → emit max_steps event and stop
```

`MAX_AGENT_STEPS = 10` (edit at the top of `agent.py`). Each step is one model call.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `WECI_HOST` | `127.0.0.1` | Listen address; use `0.0.0.0` for LAN access |
| `WECI_PORT` | `8000` | Listen port |
| `WECI_DEFAULT_API_BASE` | `https://api.openai.com/v1` | Default endpoint |
| `WECI_DEFAULT_MODEL` | `gpt-4o-mini` | Default model |
| `WECI_DEFAULT_TEMPERATURE` | `0.7` | Default temperature |
| `WECI_DEFAULT_MAX_TOKENS` | empty (no limit) | Default max_tokens |
| `WECI_API_KEY` | empty | Server-side key; `OPENAI_API_KEY` also accepted |
| `WECI_CONNECT_TIMEOUT` | `10` | Upstream connect timeout (seconds) |
| `WECI_READ_TIMEOUT` | `300` | Upstream streaming read timeout (seconds) |
| `WECI_CORS_ORIGINS` | `*` | Allowed CORS origins, comma-separated |
| `WECI_WEB_SEARCH_PROVIDER` | `duckduckgo` | Search provider: `duckduckgo` (no key) or `tavily` |
| `TAVILY_API_KEY` | empty | Required when provider is `tavily` |
| `WECI_PLUGINS_DIR` | `~/.weci/plugins` | User plugins directory |

## Data & privacy

- Chat history, settings, and theme are stored only in the browser's `localStorage`.
- The browser API Key is sent to **your own** local backend, which forwards it to the model service.
- No analytics, telemetry, or third-party reporting.
- On shared machines, prefer putting the key in the server `.env` rather than the browser.

## Known limitations

- No multi-user or auth — use only on localhost or a trusted network; do not expose to the public internet.
- Chat history does not sync across devices.
- Each request sends the full context; very long chats hit the model's context window.
- Agent mode requires an upstream model that supports OpenAI `tools` / function calling.
- Some upstream services return malformed streaming `tool_calls`; the backend falls back to non-streaming automatically.

## Roadmap

- Plugin marketplace / remote download
- MCP (Model Context Protocol) support
- Plugin sandboxing (subprocess / WASM / container)
- RAG / vector database
- Multi-modal (image input)
- User login & multi-user isolation
- Cloud sync

## License

[MIT](./LICENSE) © 2026 WeCi contributors
