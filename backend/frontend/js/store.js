/* ==========================================================================
   WeCi · 应用状态与 localStorage 持久化

   - 会话 / 设置 / 主题 / 侧栏偏好全部保存在浏览器本地
   - 不依赖 DOM 之外的浏览器 API，可以被任何 UI 模块安全引用
   ========================================================================== */

import { uid, clamp } from './utils.js';
import { MAX_IMAGES, MAX_FILES } from './attachments.js';

export const STORAGE = {
  state: 'weci.state.v1',
  settings: 'weci.settings.v1',
  theme: 'weci.theme',
  sidebar: 'weci.sidebar',
  userId: 'weci.user.id',
};

export const DEFAULT_TEMPERATURE = 0.7;
export const MAX_RECENT_MODELS = 8;
export const MAX_RENAME_LENGTH = 48;
export const MAX_TOOL_OUTPUT_CHARS = 20_000; // 与后端 MAX_TOOL_DISPLAY_CHARS 保持一致

export const state = {
  conversations: [],
  activeId: null,
  providers: [], // Provider 公开配置（不含 Key，只有 has_api_key）
  settings: {
    apiBase: '',
    apiKey: '',
    model: '',
    providerId: '', // 选中的 Provider；为空时使用服务端默认 Provider
    models: [], // 最近使用过的模型，用于快速切换
    temperature: null,
    maxTokens: null,
    mode: 'chat', // 'chat' | 'agent'
  },
  server: {
    apiBase: '',
    model: '',
    temperature: DEFAULT_TEMPERATURE,
    maxTokens: null,
    hasKey: false,
    hasProvider: false,
    defaultProviderId: '',
  },
  theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
  sidebarCollapsed: false,
  streaming: false,
  controller: null,
  streamToken: 0,
  streamingMessage: null, // 正在流式生成的助手消息（页面刷新时用于标记中断）
  currentRunId: null, // 当前 Agent Run id（来自后端，用于取消 / 查询历史）
  runs: {}, // { conversationId: [run, ...] } — 仅内存，刷新后从后端拉取
  stickToBottom: true,
  renderTimer: null,
  pendingMessage: null,
  pendingAttachments: [], // 待发送的图片附件
  pendingFiles: [], // 待发送的文件附件
  lastRenderAt: 0,
  agentStatus: null, // Agent 模式下的状态文案（思考中 / 调用工具）
  configErrorShown: false,
  settingsOpener: null,
  userId: '', // 浏览器级用户标识，用于隔离 Memory（保存在 localStorage）
};

/* --------------------------------------------------------------------------
   派生状态
   -------------------------------------------------------------------------- */

export function getActiveConversation() {
  return state.conversations.find((item) => item.id === state.activeId) || null;
}

export function getActiveProvider() {
  const providers = state.providers;
  if (!providers.length) return null;
  const selected = state.settings.providerId;
  if (selected) {
    return providers.find((item) => item.id === selected) || null;
  }
  return providers.find((item) => item.is_default) || null;
}

export function resolveProviderModel(provider, mode) {
  if (!provider) return '';
  const useAgent = mode === 'agent';
  return (
    (useAgent ? provider.agent_model : provider.chat_model) ||
    provider.default_model ||
    (Array.isArray(provider.models) && provider.models.length === 1
      ? provider.models[0].id
      : '') ||
    ''
  );
}

export function getModelCapabilities(provider, model) {
  if (!provider || !model || !Array.isArray(provider.models)) return null;
  const target = provider.models.find((item) => item.id === model);
  if (!target) return null;
  const caps = target.capabilities || {};
  return {
    text: caps.text !== false,
    vision: caps.vision !== false,
    tool_calling: caps.tool_calling !== false,
    streaming: caps.streaming !== false,
  };
}

export function getEffectiveSettings(mode) {
  const settings = state.settings;
  const provider = getActiveProvider();
  const temperature =
    typeof settings.temperature === 'number'
      ? settings.temperature
      : typeof state.server.temperature === 'number'
        ? state.server.temperature
        : DEFAULT_TEMPERATURE;
  const maxTokens =
    typeof settings.maxTokens === 'number'
      ? settings.maxTokens
      : typeof state.server.maxTokens === 'number'
        ? state.server.maxTokens
        : null;

  if (provider) {
    const preferred = (settings.model || '').trim();
    const selectedModel =
      preferred &&
      Array.isArray(provider.models) &&
      provider.models.some((item) => item.id === preferred)
        ? preferred
        : '';
    const model = selectedModel
      ? selectedModel
      : resolveProviderModel(provider, mode || settings.mode || 'chat');
    return {
      providerId: provider.id,
      providerName: provider.name,
      apiBase: provider.api_base || '',
      // Provider 的 Key 保存在服务端，浏览器永远不持有
      apiKey: '',
      model,
      capabilities: getModelCapabilities(provider, model),
      temperature,
      maxTokens,
    };
  }

  return {
    providerId: '',
    providerName: '',
    apiBase: settings.apiBase || state.server.apiBase || '',
    apiKey: settings.apiKey || '',
    model: settings.model || state.server.model || '',
    capabilities: null,
    temperature,
    maxTokens,
  };
}

export function endpointIsLocal(apiBase) {
  try {
    const hostname = new URL(apiBase).hostname;
    return (
      /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1)$/i.test(hostname) ||
      hostname.endsWith('.local') ||
      /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostname)
    );
  } catch {
    return false;
  }
}

export function canChat() {
  const provider = getActiveProvider();
  if (provider) {
    if (!provider.enabled) return false;
    const effective = getEffectiveSettings(state.settings.mode || 'chat');
    if (!effective.model) return false;
    // Provider 的 Key 存在服务端；本地 Endpoint 不要求 Key。
    return Boolean(
      provider.has_api_key ||
        state.server.hasKey ||
        endpointIsLocal(effective.apiBase),
    );
  }

  const effective = getEffectiveSettings();
  if (!effective.apiBase || !effective.model) return false;
  return Boolean(
    effective.apiKey || state.server.hasKey || endpointIsLocal(effective.apiBase),
  );
}

export function rememberModel(model) {
  const name = (model || '').trim();
  if (!name) return;
  const list = state.settings.models.filter((item) => item !== name);
  list.unshift(name);
  state.settings.models = list.slice(0, MAX_RECENT_MODELS);
}

/* --------------------------------------------------------------------------
   持久化失败通知（由 main.js 注入 toast，避免 store 反向依赖 UI）
   -------------------------------------------------------------------------- */

let persistNotifier = null;

export function setPersistNotifier(notify) {
  persistNotifier = typeof notify === 'function' ? notify : null;
}

function notifyPersistError(message) {
  if (persistNotifier) persistNotifier(message);
}

/* --------------------------------------------------------------------------
   会话状态读写
   -------------------------------------------------------------------------- */

let saveTimer = null;

function normalizeAttachment(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (typeof raw.dataUrl !== 'string' || !raw.dataUrl.startsWith('data:image/')) {
    return null;
  }
  return {
    id: typeof raw.id === 'string' ? raw.id : uid(),
    name: typeof raw.name === 'string' && raw.name ? raw.name : 'image',
    mime: typeof raw.mime === 'string' ? raw.mime : 'image/png',
    dataUrl: raw.dataUrl,
    size: typeof raw.size === 'number' ? raw.size : 0,
  };
}

function normalizeFile(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (typeof raw.data !== 'string' || !raw.data) return null;
  if (typeof raw.name !== 'string' || !raw.name) return null;
  return {
    id: typeof raw.id === 'string' ? raw.id : uid(),
    name: raw.name,
    ext: typeof raw.ext === 'string' ? raw.ext : '',
    mime: typeof raw.mime === 'string' ? raw.mime : 'application/octet-stream',
    size: typeof raw.size === 'number' ? raw.size : 0,
    data: raw.data,
  };
}

function normalizeMessage(raw) {
  if (!raw || typeof raw !== 'object') return null;
  if (raw.role !== 'user' && raw.role !== 'assistant') return null;
  if (typeof raw.content !== 'string') return null;
  const message = {
    id: typeof raw.id === 'string' ? raw.id : uid(),
    role: raw.role,
    content: raw.content,
    createdAt:
      typeof raw.createdAt === 'string' ? raw.createdAt : new Date().toISOString(),
  };
  if (Array.isArray(raw.attachments)) {
    const attachments = raw.attachments
      .map(normalizeAttachment)
      .filter(Boolean)
      .slice(0, MAX_IMAGES);
    if (attachments.length) message.attachments = attachments;
  }
  if (Array.isArray(raw.files)) {
    const files = raw.files.map(normalizeFile).filter(Boolean).slice(0, MAX_FILES);
    if (files.length) message.files = files;
  }
  if (typeof raw.error === 'string' && raw.error) message.error = raw.error;
  if (raw.stopped === true) message.stopped = true;
  if (raw.edited === true) message.edited = true;
  if (Array.isArray(raw.toolCalls)) {
    message.toolCalls = raw.toolCalls
      .filter((tc) => tc && typeof tc === 'object' && typeof tc.name === 'string')
      .map((tc) => {
        // 页面刷新 / 网络中断后，残留的 running 状态一律视为已中断，
        // 避免 UI 永久停留在“执行中”。
        const interrupted = tc.status === 'running';
        return {
          id: typeof tc.id === 'string' ? tc.id : uid(),
          name: tc.name,
          status:
            tc.status === 'done' || tc.status === 'error' || tc.status === 'interrupted'
              ? tc.status
              : 'interrupted',
          step: typeof tc.step === 'number' ? tc.step : null,
          durationMs: typeof tc.durationMs === 'number' ? tc.durationMs : null,
          arguments: tc.arguments === undefined ? null : tc.arguments,
          output:
            typeof tc.output === 'string' ? tc.output.slice(0, MAX_TOOL_OUTPUT_CHARS) : null,
          error:
            typeof tc.error === 'string'
              ? tc.error
              : interrupted
                ? '页面刷新或网络中断，工具执行状态未知。'
                : null,
        };
      });
  }
  if (Array.isArray(raw.permissionRequests)) {
    // 刷新后不可能仍在等待确认：残留的 pending 一律标记为超时，
    // 避免 UI 显示可以点击但实际已失效的确认按钮。
    message.permissionRequests = raw.permissionRequests
      .filter((req) => req && typeof req === 'object' && typeof req.id === 'string')
      .map((req) => ({
        id: req.id,
        tool: typeof req.tool === 'string' ? req.tool : '',
        permission: typeof req.permission === 'string' ? req.permission : '',
        title: typeof req.title === 'string' ? req.title : '',
        detail: typeof req.detail === 'string' ? req.detail : '',
        risk:
          req.risk === 'low' ||
          req.risk === 'medium' ||
          req.risk === 'safe' ||
          req.risk === 'dangerous'
            ? req.risk
            : 'high',
        kind: req.kind === 'command' || req.kind === 'tool' ? req.kind : 'permission',
        reason: typeof req.reason === 'string' ? req.reason : '',
        command: typeof req.command === 'string' ? req.command : '',
        mode: typeof req.mode === 'string' ? req.mode : '',
        options: Array.isArray(req.options) && req.options.length ? req.options : ['once', 'deny'],
        status:
          req.status === 'granted' || req.status === 'denied' ? req.status : 'timeout',
      }));
  }
  if (raw.agentState === 'running') {
    // 刷新后不可能仍在运行；标记为已中断并给出说明。
    message.agentState = 'stopped';
    message.interrupted = true;
  } else if (
    raw.agentState === 'done' ||
    raw.agentState === 'error' ||
    raw.agentState === 'stopped'
  ) {
    message.agentState = raw.agentState;
  } else if (raw.agentState === 'max_steps') {
    // 旧版本遗留状态：Unlimited Execution 下不再存在最大步数
    message.agentState = 'stopped';
  }
  if (typeof raw.agentStep === 'number') message.agentStep = raw.agentStep;
  if (typeof raw.agentStartedAt === 'number') {
    message.agentStartedAt = raw.agentStartedAt;
  }
  if (typeof raw.agentDurationMs === 'number') {
    message.agentDurationMs = raw.agentDurationMs;
  }
  if (raw.interrupted === true) message.interrupted = true;
  // 空白的 assistant 消息通常是刷新时中断的半成品
  if (
    message.role === 'assistant' &&
    !message.content &&
    !message.error &&
    !message.stopped
  ) {
    message.stopped = true;
    message.interrupted = true;
    if (message.agentState === 'running') message.agentState = 'stopped';
  }
  return message;
}

function normalizeConversation(raw) {
  if (!raw || typeof raw !== 'object' || typeof raw.id !== 'string') return null;
  const messages = Array.isArray(raw.messages)
    ? raw.messages.map(normalizeMessage).filter(Boolean)
    : [];
  const now = new Date().toISOString();
  return {
    id: raw.id,
    title:
      typeof raw.title === 'string' && raw.title.trim() ? raw.title : '新对话',
    messages,
    mode: raw.mode === 'agent' ? 'agent' : 'chat',
    createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : now,
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : now,
  };
}

export function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE.state);
    if (raw) {
      const data = JSON.parse(raw);
      if (data && Array.isArray(data.conversations)) {
        state.conversations = data.conversations
          .map(normalizeConversation)
          .filter(Boolean);
      }
      if (data && typeof data.activeId === 'string') {
        state.activeId = data.activeId;
      }
    }
  } catch (error) {
    console.warn('[WeCi] failed to read local state', error);
  }

  if (!state.conversations.some((item) => item.id === state.activeId)) {
    state.activeId = null;
  }
}

export function saveState() {
  if (saveTimer) {
    window.clearTimeout(saveTimer);
    saveTimer = null;
  }
  try {
    localStorage.setItem(
      STORAGE.state,
      JSON.stringify({
        conversations: state.conversations,
        activeId: state.activeId,
      }),
    );
  } catch (error) {
    console.warn('[WeCi] failed to persist local state', error);
    notifyPersistError('本地存储写入失败，可能是空间已满。');
  }
}

export function scheduleSave() {
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(saveState, 500);
}

/* --------------------------------------------------------------------------
   设置读写
   -------------------------------------------------------------------------- */

export function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE.settings);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object') return;
    if (typeof data.apiBase === 'string') state.settings.apiBase = data.apiBase;
    if (typeof data.apiKey === 'string') state.settings.apiKey = data.apiKey;
    if (typeof data.model === 'string') state.settings.model = data.model;
    if (typeof data.providerId === 'string') {
      state.settings.providerId = data.providerId;
    }
    if (typeof data.temperature === 'number' && Number.isFinite(data.temperature)) {
      state.settings.temperature = clamp(data.temperature, 0, 2);
    }
    if (
      typeof data.maxTokens === 'number' &&
      Number.isInteger(data.maxTokens) &&
      data.maxTokens >= 1 &&
      data.maxTokens <= 200000
    ) {
      state.settings.maxTokens = data.maxTokens;
    }
    if (Array.isArray(data.models)) {
      state.settings.models = data.models
        .filter((item) => typeof item === 'string' && item.trim())
        .slice(0, MAX_RECENT_MODELS);
    }
    if (data.mode === 'agent' || data.mode === 'chat') {
      state.settings.mode = data.mode;
    }
  } catch (error) {
    console.warn('[WeCi] failed to read settings', error);
  }
}

export function saveSettings() {
  try {
    localStorage.setItem(STORAGE.settings, JSON.stringify(state.settings));
  } catch (error) {
    console.warn('[WeCi] failed to persist settings', error);
    notifyPersistError('设置保存失败，可能是本地存储不可用。');
  }
}

/* --------------------------------------------------------------------------
   侧栏展开 / 收起偏好
   -------------------------------------------------------------------------- */

export function loadSidebarPref() {
  try {
    return localStorage.getItem(STORAGE.sidebar) === 'collapsed';
  } catch {
    return false;
  }
}

export function saveSidebarPref(collapsed) {
  try {
    localStorage.setItem(STORAGE.sidebar, collapsed ? 'collapsed' : 'expanded');
  } catch {
    /* 忽略存储失败 */
  }
}

/* --------------------------------------------------------------------------
   用户标识（Memory 隔离）
   -------------------------------------------------------------------------- */

export function getUserId() {
  try {
    const raw = localStorage.getItem(STORAGE.userId);
    if (raw && raw.trim()) return raw.trim();
    const value = uid();
    localStorage.setItem(STORAGE.userId, value);
    return value;
  } catch {
    if (!state.userId) state.userId = uid();
    return state.userId;
  }
}
