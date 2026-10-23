/* ==========================================================================
   WeCi · 应用状态与 localStorage 持久化

   - 会话 / 设置 / 主题 / 侧栏偏好全部保存在浏览器本地
   - 不依赖 DOM 之外的浏览器 API，可以被任何 UI 模块安全引用
   ========================================================================== */

import { uid, clamp } from './utils.js';

export const STORAGE = {
  state: 'weci.state.v1',
  settings: 'weci.settings.v1',
  theme: 'weci.theme',
  sidebar: 'weci.sidebar',
};

export const DEFAULT_TEMPERATURE = 0.7;
export const MAX_RECENT_MODELS = 8;
export const MAX_RENAME_LENGTH = 48;

export const state = {
  conversations: [],
  activeId: null,
  settings: {
    apiBase: '',
    apiKey: '',
    model: '',
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
  },
  theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
  sidebarCollapsed: false,
  streaming: false,
  controller: null,
  streamToken: 0,
  stickToBottom: true,
  renderTimer: null,
  pendingMessage: null,
  lastRenderAt: 0,
  agentStatus: null, // Agent 模式下的状态文案（思考中 / 调用工具）
  configErrorShown: false,
  settingsOpener: null,
};

/* --------------------------------------------------------------------------
   派生状态
   -------------------------------------------------------------------------- */

export function getActiveConversation() {
  return state.conversations.find((item) => item.id === state.activeId) || null;
}

export function getEffectiveSettings() {
  const settings = state.settings;
  return {
    apiBase: settings.apiBase || state.server.apiBase || '',
    apiKey: settings.apiKey || '',
    model: settings.model || state.server.model || '',
    temperature:
      typeof settings.temperature === 'number'
        ? settings.temperature
        : typeof state.server.temperature === 'number'
          ? state.server.temperature
          : DEFAULT_TEMPERATURE,
    maxTokens:
      typeof settings.maxTokens === 'number'
        ? settings.maxTokens
        : typeof state.server.maxTokens === 'number'
          ? state.server.maxTokens
          : null,
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
  if (typeof raw.error === 'string' && raw.error) message.error = raw.error;
  if (raw.stopped === true) message.stopped = true;
  if (raw.edited === true) message.edited = true;
  if (Array.isArray(raw.toolCalls)) {
    message.toolCalls = raw.toolCalls
      .filter((tc) => tc && typeof tc === 'object' && typeof tc.name === 'string')
      .map((tc) => ({
        id: typeof tc.id === 'string' ? tc.id : uid(),
        name: tc.name,
        status: tc.status === 'done' || tc.status === 'error' ? tc.status : 'running',
        durationMs: typeof tc.durationMs === 'number' ? tc.durationMs : null,
        error: typeof tc.error === 'string' ? tc.error : null,
      }));
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
