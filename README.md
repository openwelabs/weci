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

**Image input (Vision)**

- Attach images by clicking the image button or dragging them onto the input box
- Preview attachments before sending; remove any image with one click
- Sent images render inside the message and are stored with the chat
- Works in both Chat and Agent mode; requests use the OpenAI-compatible `image_url` content format
- Models without Vision support return a clear hint instead of a silent failure

**File attachments (phase 1)**

- Attach `.txt`, `.md`, `.json`, `.csv`, `.py`, `.js`, `.ts`, `.html`, `.css` and `.pdf` by click or drag-and-drop
- File name and size are shown before sending; remove any attachment with one click
- Text files are decoded server-side and injected as delimited context; PDFs use basic text extraction (`pypdf`)
- Works in Chat and Agent mode; Agent sees the file text without any file-handling code in the Agent Loop
- Clear errors for unsupported types, oversized files and unreadable PDFs

**Context Manager**

- Chat and Agent share one context-building path instead of assembling messages separately
- Manages conversation history, Workspace, image / file attachments and Agent tool results in one place
- Estimates tokens against the model context window; when over budget, older content is compressed while system settings, the current request and recent messages are kept
- Duplicate attachments / Workspace content are injected only once

**Memory (phase 1)**

- Save small long-term facts only when you explicitly ask (e.g. "remember I use Fedora")
- Agent gets `save_memory` / `delete_memory` tools; chat content is never saved automatically
- Memories are scoped to `user` / `workspace` / `conversation` and injected as system context when relevant
- A dedicated Memory page lets you view, create, edit and delete them
- Stored in `.weci/memory.json`; separate from chat history and Workspace files

**MCP servers (phase 1)**

- Connect WeCi as an **MCP client** to local stdio MCP servers (`command` / `args` / `environment`)
- On connect, WeCi fetches the server's tools and registers them into the same `ToolRegistry` the Agent already uses
- MCP tools use the server's own JSON Schema; results are converted into the unified `ToolResult`
- A dedicated MCP page lets you add, edit, connect / disconnect, enable / disable and delete servers
- Server lifecycle (start / stop / reconnect / crash) is owned by an independent MCP Manager
- A disconnected or crashed server only fails its own tool calls — the Agent and other tools keep working

**Model providers**

- One Provider Manager for all model services: API Endpoint, API Key, Models and Capabilities in a single place
- Add, edit and delete providers; pick a default provider and separate Chat / Agent models
- Declare `text` / `vision` / `tool_calling` / `streaming` per model; missing capabilities are reported before a request is sent
- "Test provider" and "Test model" buttons with clear, actionable error messages
- The model menu groups models by provider and shows `✓ Vision`, `✓ Tool Calling`, `✓ Streaming` at a glance
- Keys are stored server-side in `.weci/providers.json` (mode `0600`) and never returned to the browser
- The previous Endpoint / Key / Model fields remain available under "Compatibility mode"

**Permissions & security**

- One **Permission Manager** records every tool call: built-in tools, plugin tools and MCP tools share the same audit trail
- Scoped permissions: `workspace:read` / `workspace:write` / `workspace:delete` / `command:execute` / `network:access` / `memory:read` / `memory:write` / `mcp:use` / `plugin:use`
- **WeCi never intercepts**: Permission Manager and Security Policy only record state; they do not block tool execution
- **Three recording modes**: Safe (records "would require review") / Auto Review (records safe / dangerous) / Risky (default: records as unrestricted). Switching modes does not change execution
- Command risk is decided by a structured parser (`shlex` tokens + pipelines / redirects / `sh -c`): `echo "rm -rf"` is not treated as a deletion, while `rm -rf ./build` is
- **No Workspace path limits**: absolute paths, `..`, symlinks and paths outside Workspace go straight to the OS; file tools and `run_command` are never rejected for their path
- `run_command` sanitizes the environment, supports timeout and cancellation, and records command / cwd / mode / risk / reason / exit code
- A dedicated Permissions page shows Workspace, Providers, Plugins, MCP servers, tool permissions and a redacted security log; Allow / Deny are recorded preferences only
- The security log never contains API keys, tokens, passwords, cookies or authorization headers

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
  [Agent Loop](#agent-loop), feeds tool results back to the model, and keeps going
  until the task is done. There is no max step count; runaway behaviour is bounded
  by user Stop, tool timeouts, Loop Detection and Context Management.

In Agent mode the UI renders an **Agent execution trace** above the reply. It only
shows what actually happened — never the model's hidden chain-of-thought:

- **Run status**: `N tool calls · Running...` while executing, `N tool calls · 2m 31s` when done,
  `N tool calls · Stopped` after cancel / interruption. No fake max step is shown.
- **Tool cards**: status icon (spinner / success / failure / interrupted), tool name,
  status and duration; failures show the error reason directly.
- **Arguments & results**: collapsed by default, expandable per tool card. Long output
  (over 20k characters) is truncated and scrolls inside the card.
- **Interruption**: Stop aborts the stream immediately and marks running tools as
  interrupted; after a page refresh or network loss, unfinished replies are also marked
  as interrupted instead of spinning forever.

## Official skills (built-in, enabled by default)

| Skill | Tools | Description |
| --- | --- | --- |
| Calculator | `calculator` | Safe calculator (AST-based, no `eval`) |
| Current Time | `current_time` | Current date, time, and timezone |
| Web Search | `web_search`, `fetch_url` | Search the web (DuckDuckGo, no key) and fetch page content |
| Computer | `run_command`, `read_file`, `write_file`, `list_dir`, `clipboard_get`, `clipboard_set` | Run shell commands, read/write files, manage clipboard |
| Memory | `save_memory`, `delete_memory` | Save / delete long-term memories only when the user explicitly asks |

> The **Computer** skill lets the Agent execute commands and modify files on your
> machine. Enable it only if you trust what the Agent will do.

## Requirements

- Python 3.10+ (tested on 3.14)
- An OpenAI-compatible model endpoint (OpenAI, DeepSeek, Qwen, Ollama, LM Studio, etc.)

## Installation

### Option 1: Install from source (recommended)

```bash
git clone https://github.com/openwelabs/weci.git
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
│   │   ├── memory.py           # /api/memories CRUD
│   │   ├── mcp.py              # /api/mcp/servers REST API
│   │   └── system.py           # GET /api/health, /api/config, POST /api/test-connection
│   ├── mcp/                    # MCP client (stdio JSON-RPC 2.0)
│   │   ├── client.py           # StdioMcpClient: initialize / tools/list / tools/call
│   │   └── manager.py          # McpManager: config, lifecycle, tool registration
│   ├── services/
│   │   ├── context.py          # Context Manager: unified context, token budget, compression, dedupe
│   │   ├── memory.py           # Memory store: scopes, limits, JSON persistence
│   │   ├── message.py          # Message / image / file models + OpenAI-compatible conversion
│   │   ├── openai_client.py    # OpenAI-compatible client (streaming tool-call parsing)
│   │   ├── agent.py            # Agent Loop: unlimited execution, loop guard, SSE
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
│   │       ├── memory/
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

Plugins declare capabilities in `permissions`; every tool call is additionally
checked by the shared **Permission Manager** before it runs. Scoped permission
names are preferred, while legacy names are normalized automatically.

| Permission | Capability |
| --- | --- |
| `network:access` | `context.http_request()` |
| `storage:read` / `storage:write` | `context.storage_get()` / `storage_set()` |
| `workspace:read` | `context.read_file()` / `list_dir()` |
| `workspace:write` | `context.write_file()` / `append_file()` |
| `workspace:delete` | `context.delete_file()` |
| `command:execute` | `context.run_command()` |
| `clipboard:access` | `context.clipboard_get()` / `clipboard_set()` |
| `memory:read` / `memory:write` | `context.memory.list()` / `save()` / `delete()` |
| `mcp:use` | MCP tools registered from an MCP server |

Legacy names (`network`, `storage`, `filesystem.read`, `filesystem.write`,
`filesystem.delete`, `process`, `clipboard`, `memory`) are normalized to the
scoped equivalents on load, so existing plugins keep working.

Calls without the declared permission raise `PermissionDenied`.


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

### Develop your own plugin

Full documentation: [`docs/plugins/`](docs/plugins/README.md)

The recommended toolchain is
[**weci-maker**](https://github.com/openwelabs/weci-maker) — the official plugin
development CLI (think Cargo for WeCi plugins):

```bash
# 0. Install weci-maker once (it reuses WeCi's SDK & loader)
pip install -e ~/repos/weci
pip install -e ~/repos/weci-maker

# 1. Scaffold a plugin project (manifest.json + plugin.py + tests/)
weci-maker new my-plugin
cd my-plugin

# 2. Edit manifest.json (id, name, version, permissions)

# 3. Implement your tool in plugin.py

# 4. Check (manifest, entry module, WeCiPlugin subclass, tool schemas)
weci-maker check

# 5. Run the plugin's own tests
python -m pytest tests/

# 6. Build a distributable .wcp package
weci-maker build            # → dist/my-plugin-0.1.0.wcp

# 7. Install into ~/.weci/plugins/ (same as `weci plugin install`)
weci-maker install dist/my-plugin-0.1.0.wcp
```

Restart WeCi and your tool appears in the Agent automatically.

Without weci-maker, the manual flow still works: copy
`examples/plugins/template/`, then `weci plugin validate ./my-plugin` and
`weci plugin install ./my-plugin`.

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
write assistant tool_calls + tool results back to the ContextManager
   ↓
build() a compressed Context and send it to the model again (back to "model reasons")
   ↓
until the task completes / user Stop / provider error / loop detected / context full
```

The Agent runs with **Unlimited Execution** by default: there is no max step
count. Steps, tool-call count and duration are statistics only, never stop
conditions. The real boundaries are user Stop + Security Policy + Tool Timeout +
Loop Detection + Context Manager.

Loop Protection detects consecutive identical Tool + arguments (3 by default) and
consecutive identical errors. It returns a structured `LOOP_DETECTED` error so the
model can replan; repeated detections in one Run end it with `error_type=loop`.

## MCP (Model Context Protocol)

WeCi can act as an **MCP client**. External MCP servers expose tools, and WeCi
registers those tools into the same `ToolRegistry` used by plugins — the Agent
still only knows `ToolRegistry`:

```
                 WeCi Agent
                     ↓
                ToolRegistry
                ↙         ↘
          WeCi Plugin     MCP  (backend/mcp/)
                            ↓
                       MCP Server
```

```
Plugin Tool ──┐
Built-in Tool ─┼→ ToolRegistry → Agent
MCP Tool ─────┘
```

**Phase 1 scope**

- Local **stdio** servers only: WeCi spawns the server as a subprocess and talks
  newline-delimited JSON-RPC 2.0 over stdin / stdout (stderr is logs only).
- Protocol subset: `initialize` → `notifications/initialized` → `tools/list`
  (with `nextCursor` pagination) → `tools/call`.
- No Marketplace, no remote server management, no OAuth, no auto-discovery and
  **no sandboxing** — an MCP server is a local command you choose to trust.

**Configuration**

Servers are stored in `.weci/mcp_servers.json` and managed from the MCP page in
the top bar (or the Settings panel). Each entry has:

| Field | Description |
| --- | --- |
| `name` | Display name (also the base of the tool prefix) |
| `command` | Executable to spawn, e.g. `npx`, `python3`, `uvx` |
| `args` | Argument list passed to the command |
| `environment` | Extra environment variables (`KEY=VALUE`), merged over the server process env |
| `enabled` | Disabled servers are stopped and their tools are unregistered |

```bash
# Start a server via the REST API
curl -X POST http://127.0.0.1:8000/api/mcp/servers \
  -H 'Content-Type: application/json' \
  -d '{"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}'
```

**Lifecycle**

`backend/mcp/manager.py` `McpManager` owns the whole lifecycle
(`disabled` → `stopped` → `connecting` → `ready` / `error`):

- On FastAPI startup, every enabled server is connected; on shutdown all
  subprocesses are closed.
- Connecting fetches the tool list and registers `McpTool` instances into
  `ToolRegistry`; disconnecting / disabling unregisters them.
- Tool names are namespaced as `mcp__<server_id>__<tool>` (sanitized, max 64
  chars) so plugin tools are never overwritten; conflicts are skipped and logged.
- If the server exits unexpectedly, pending calls fail with a clear error and
  only that server's tools stop working — the Agent itself keeps running.

**Tool mapping**

- Arguments: the server's own `inputSchema` is used as the tool schema
  (OpenAI-compatible); WeCi passes arguments through and the server validates.
- Results: `content[].text` parts are concatenated, `resource.text` is included,
  images / audio become `[image: mime]` / `[audio: mime]` placeholders, and
  `isError: true` maps to a failed `ToolResult`.

**REST API**

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/mcp/servers` | List servers with status, tools and last error |
| `POST` | `/api/mcp/servers` | Add a server and connect immediately |
| `PATCH` | `/api/mcp/servers/{id}` | Update fields (reconnects when command / args / env change) |
| `DELETE` | `/api/mcp/servers/{id}` | Stop and remove a server |
| `POST` | `/api/mcp/servers/{id}/connect` | Connect / reconnect a server |
| `POST` | `/api/mcp/servers/{id}/disconnect` | Stop a server and unregister its tools |

## Context Manager

Everything sent to the model goes through `backend/services/context.py`:

```
Conversation + Workspace + Attachment + Tool Result
        ↓
  ContextManager (unified structure + token budget + compression / dedupe)
        ↓
  ModelContext (final message list)
        ↓
  Provider (openai_client)
        ↓
       Model
```

- **One entry point**: Chat and Agent both call `ContextManager.from_chat_messages()`; each Agent round writes tool calls and results back to the same Context and calls `build()` again.
- **Priority**: system context → current user request → recent messages → required tool results → older messages.
- **Budget**: the context window is inferred from the model name (conservative known limits for `gpt-3.5` / `deepseek`, etc.) minus reserved output tokens. Tune it with `WECI_CONTEXT_*`.
- **Compression**: when over budget, images in older messages are removed first, then long file / tool outputs are truncated, then the oldest messages are dropped. System settings, the current request and recent messages are handled last.
- **Dedupe**: identical file content, identical images and Workspace / attachment duplicates are injected only once.
- **Workspace**: `POST /api/chat` accepts an optional `workspace` object (`name` / `root` / `description` / `instructions` / `files`) and injects it as system context. Phase 1 is text-only; no file sync or indexing.
- **Memory**: `POST /api/chat` accepts `user_id` / `workspace_id` / `conversation_id`; memories visible to that scope are injected as a `<memories>` system block. Count and length are capped by `WECI_CONTEXT_MEMORY_*`.

## Model Providers

All model configuration lives in one place:

```
Provider Manager → Model → Capabilities → Chat / Agent / Multimodal
```

Chat, Agent and image input no longer keep their own Endpoint / Key / Model settings. `POST /api/chat` accepts an optional `provider_id`; when omitted, the default provider is used. Requests that only send `api_base` + `model` keep working unchanged (compatibility path).

**Data model**

| Level | Fields |
| --- | --- |
| Provider | `id`, `name`, `description`, `api_base`, `enabled`, default provider flag |
| Provider models | `default_model`, `chat_model`, `agent_model` (used by Chat / Agent respectively) |
| Model | `id`, `display_name`, `capabilities`, `context_window`, `max_tokens` |
| Capability | `text`, `vision`, `tool_calling`, `streaming` |

Capabilities default to `true`; `vision` is inferred from the model name (`gpt-4o`, `claude`, `gemini`, `qwen-vl`, `llava`, …) and can be overridden per model. If a model declares `streaming: false`, WeCi falls back to a non-streaming request instead of failing.

**Capability checks** happen before the upstream call: a model without `text` cannot chat, a model without `vision` rejects image attachments, and Agent mode requires `tool_calling`. The API returns `400` with a readable explanation.

**Endpoints**

| Method | Path |
| --- | --- |
| `GET` | `/api/providers` |
| `POST` | `/api/providers` |
| `PATCH` / `DELETE` | `/api/providers/{provider_id}` |
| `POST` | `/api/providers/{provider_id}/models` |
| `PATCH` / `DELETE` | `/api/providers/{provider_id}/models/{model_id}` |
| `POST` | `/api/providers/{provider_id}/test` |
| `POST` | `/api/providers/{provider_id}/models/{model_id}/test` |

**API Key security**

- Provider keys are stored server-side in `.weci/providers.json` with `0600` permissions.
- Public responses only include `has_api_key` — never the key itself, and keys never appear in logs, errors or SSE events.
- Updating a provider: omit `api_key` to keep it, send `""` to clear it.
- If a provider has no key, the existing `X-WeCi-API-Key` header / server env key is used as a fallback.

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
| `WECI_MAX_IMAGE_BYTES` | `5242880` (5 MB) | Max decoded size of a single image attachment |
| `WECI_MAX_IMAGES_PER_MESSAGE` | `4` | Max images in one user message |
| `WECI_MAX_FILE_BYTES` | `5242880` (5 MB) | Max size of a single file attachment |
| `WECI_MAX_FILES_PER_MESSAGE` | `4` | Max file attachments in one user message |
| `WECI_MAX_FILE_TEXT_CHARS` | `100000` | Max extracted characters per file (text or PDF) |
| `WECI_CONTEXT_WINDOW_TOKENS` | `128000` | Default context window; models with a known smaller window override it |
| `WECI_CONTEXT_RESERVE_TOKENS` | `4096` | Tokens reserved for model output (request `max_tokens` wins when set) |
| `WECI_CONTEXT_MIN_RECENT_MESSAGES` | `6` | Recent messages kept whenever possible while compressing |
| `WECI_CONTEXT_IMAGE_TOKENS` | `1000` | Estimated tokens per image in the budget |
| `WECI_CONTEXT_WORKSPACE_MAX_CHARS` | `20000` | Max characters injected from the Workspace |
| `WECI_CONTEXT_MEMORY_MAX_ITEMS` | `20` | Max memories injected into one request |
| `WECI_CONTEXT_MEMORY_MAX_CHARS` | `4000` | Max characters of injected memories |
| `WECI_MEMORY_MAX_PER_SCOPE` | `50` | Max memories stored per user / workspace / conversation scope |
| `WECI_MEMORY_MAX_CONTENT_CHARS` | `500` | Max characters per memory |
| `WECI_MCP_CONNECT_TIMEOUT` | `20` | MCP `initialize` / `tools/list` timeout (seconds) |
| `WECI_MCP_CALL_TIMEOUT` | `120` | Timeout for a single MCP `tools/call` (seconds) |
| `WECI_CORS_ORIGINS` | `*` | Allowed CORS origins, comma-separated |
| `WECI_WEB_SEARCH_PROVIDER` | `duckduckgo` | Search provider: `duckduckgo` (no key) or `tavily` |
| `TAVILY_API_KEY` | empty | Required when provider is `tavily` |
| `WECI_PLUGINS_DIR` | `~/.weci/plugins` | User plugins directory |

## Data & privacy

- Chat history, settings, and theme are stored only in the browser's `localStorage`.
- Long-term memories are stored server-side in `.weci/memory.json` on your machine, separate from chat history; you can review and delete them at any time.
- Provider definitions and their API keys are stored server-side in `.weci/providers.json` (mode `0600`); the browser only receives `has_api_key`.
- MCP server definitions are stored in `.weci/mcp_servers.json`. Connecting a server runs **its own command as a local subprocess** with the permissions of your user — only add servers you trust, and note that phase 1 provides no sandbox.
- In compatibility mode the browser API Key is sent to **your own** local backend, which forwards it to the model service.
- No analytics, telemetry, or third-party reporting.
- On shared machines, prefer putting the key in the server `.env` rather than the browser.

## Known limitations

- No multi-user or auth — use only on localhost or a trusted network; do not expose to the public internet.
- Chat history does not sync across devices.
- Token estimates use a conservative heuristic (not the model's official tokenizer); very long chats are compressed automatically, so older messages or images may be omitted.
- Agent mode requires a model with `tool_calling` enabled in its Provider configuration.
- Image input requires a model with `vision` enabled in its Provider configuration. Models without it are rejected before the request with a clear message.
- Provider support is limited to OpenAI-compatible APIs (Endpoint + `/chat/completions`); vendor-specific native APIs are not implemented.
- Model capabilities are declarations, not verified facts. WeCi trusts what you configure and only checks them before sending a request.
- File attachments are limited to the extensions listed above; Office formats, archives and binary files are not supported yet. Scanned PDFs without a text layer produce no extractable text.
- File text is sent as part of the user message. Files whose extracted text exceeds the per-file character limit are rejected with a clear error instead of being chunked via RAG.
- Some upstream services return malformed streaming `tool_calls`; the backend falls back to non-streaming automatically.
- MCP support is limited to local stdio servers; remote (HTTP / SSE) transports, OAuth, auto-discovery and sandboxing are not implemented yet. A server's tools are only as safe as the command you configured.

## Roadmap

- Plugin marketplace / remote download
- MCP remote transports (HTTP / SSE) and server discovery
- Plugin sandboxing (subprocess / WASM / container)
- RAG / vector database
- File workspace (multi-turn file management, Office formats)
- Multi-modal (audio / video input)
- User login & multi-user isolation
- Cloud sync

## License

[MIT](./LICENSE) © 2026 WeCi contributors
