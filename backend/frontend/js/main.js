/* ==========================================================================
   WeCi · 应用编排入口

   职责：DOM 引用、主题、侧栏、会话操作、流式输出编排、设置面板、
   模型快速切换、快捷键、启动初始化。
   可复用的状态 / 网络 / 渲染能力分别在 store.js / api.js / markdown.js / ui.js。
   ========================================================================== */

import { uid, clamp, makeTitle, formatHistoryTime, isMacLike, icon, escapeHtml } from './utils.js';
import {
  STORAGE,
  DEFAULT_TEMPERATURE,
  MAX_RENAME_LENGTH,
  state,
  getActiveConversation,
  getEffectiveSettings,
  endpointIsLocal,
  canChat,
  rememberModel,
  loadState,
  saveState,
  scheduleSave,
  loadSettings,
  saveSettings,
  setPersistNotifier,
  loadSidebarPref,
  saveSidebarPref,
} from './store.js';
import { fetchServerConfig, streamChat, testConnection, fetchPlugins, enablePlugin, disablePlugin, removePlugin } from './api.js';
import { createProse, enhanceMarkdown, appendStreamCursor, typingEl } from './markdown.js';
import {
  toast,
  copyText,
  createMessageEl,
  createToolCallsEl,
  buildErrorElement,
  createHistoryItem,
  createHistoryRenameInput,
  createEditEditor,
} from './ui.js';

const MAX_INPUT_HEIGHT = 200;
const STREAM_RENDER_INTERVAL = 70; // ms，控制流式 Markdown 重绘频率
const MODEL_PRESETS = ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'deepseek-chat', 'qwen-plus', 'llama3.1'];

const dom = {
  app: document.getElementById('app'),
  sidebar: document.getElementById('sidebar'),
  sidebarClose: document.getElementById('sidebarClose'),
  sidebarCollapse: document.getElementById('sidebarCollapse'),
  sidebarExpand: document.getElementById('sidebarExpand'),
  scrim: document.getElementById('scrim'),
  menuBtn: document.getElementById('menuBtn'),
  brandLink: document.getElementById('brandLink'),
  newChatBtn: document.getElementById('newChatBtn'),
  historyList: document.getElementById('historyList'),
  settingsBtn: document.getElementById('settingsBtn'),
  themeBtn: document.getElementById('themeBtn'),
  themeBtnLabel: document.getElementById('themeBtnLabel'),
  chatTitle: document.getElementById('chatTitle'),
  modeSwitch: document.getElementById('modeSwitch'),
  modelSwitch: document.getElementById('modelSwitch'),
  modelChip: document.getElementById('modelChip'),
  modelChipText: document.getElementById('modelChipText'),
  modelMenu: document.getElementById('modelMenu'),
  topThemeBtn: document.getElementById('topThemeBtn'),
  topSettingsBtn: document.getElementById('topSettingsBtn'),
  topPluginsBtn: document.getElementById('topPluginsBtn'),
  chatScroll: document.getElementById('chatScroll'),
  emptyState: document.getElementById('emptyState'),
  configTip: document.getElementById('configTip'),
  configTipBtn: document.getElementById('configTipBtn'),
  suggestions: document.getElementById('suggestions'),
  messages: document.getElementById('messages'),
  composerForm: document.getElementById('composerForm'),
  input: document.getElementById('input'),
  sendBtn: document.getElementById('sendBtn'),
  stopBtn: document.getElementById('stopBtn'),
  composerHint: document.getElementById('composerHint'),
  settingsModal: document.getElementById('settingsModal'),
  settingsForm: document.getElementById('settingsForm'),
  settingEndpoint: document.getElementById('settingEndpoint'),
  settingApiKey: document.getElementById('settingApiKey'),
  toggleKeyBtn: document.getElementById('toggleKeyBtn'),
  settingModel: document.getElementById('settingModel'),
  modelPresets: document.getElementById('modelPresets'),
  settingTemperature: document.getElementById('settingTemperature'),
  tempValue: document.getElementById('tempValue'),
  settingMaxTokens: document.getElementById('settingMaxTokens'),
  testConnBtn: document.getElementById('testConnBtn'),
  testConnResult: document.getElementById('testConnResult'),
  serverKeyBadge: document.getElementById('serverKeyBadge'),
  clearAllBtn: document.getElementById('clearAllBtn'),
  pluginsPage: document.getElementById('pluginsPage'),
  pluginsBackBtn: document.getElementById('pluginsBackBtn'),
  pluginsList: document.getElementById('pluginsList'),
  pluginsRefreshBtn: document.getElementById('pluginsRefreshBtn'),
  settingsOpenPluginsBtn: document.getElementById('settingsOpenPluginsBtn'),
};

/* --------------------------------------------------------------------------
   主题
   -------------------------------------------------------------------------- */

function applyTheme(theme, persist = true) {
  state.theme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = state.theme;
  if (persist) {
    try {
      localStorage.setItem(STORAGE.theme, state.theme);
    } catch {
      /* 忽略存储失败 */
    }
  }
  syncThemeControls();
}

function syncThemeControls() {
  const isDark = state.theme === 'dark';
  dom.themeBtnLabel.textContent = isDark ? '浅色模式' : '深色模式';
  dom.themeBtn.setAttribute('aria-pressed', String(isDark));
  dom.topThemeBtn.setAttribute(
    'aria-label',
    isDark ? '切换到浅色主题' : '切换到深色主题',
  );
  dom.settingsModal.querySelectorAll('[data-theme-choice]').forEach((button) => {
    const active = button.dataset.themeChoice === state.theme;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function toggleTheme() {
  applyTheme(state.theme === 'dark' ? 'light' : 'dark');
}

/* --------------------------------------------------------------------------
   消息渲染
   -------------------------------------------------------------------------- */

function findMessageEl(id) {
  return (
    Array.from(dom.messages.children).find(
      (node) => node.dataset.messageId === id,
    ) || null
  );
}

function updateAssistantNode(message, streaming) {
  const article = findMessageEl(message.id);
  if (!article) return;
  const content = article.querySelector('.msg__content');
  if (!content) return;

  // 先放工具调用卡片（若有）
  const existingTools = content.querySelector('.tool-calls');
  if (existingTools) existingTools.remove();
  if (message.toolCalls && message.toolCalls.length) {
    const toolCallsEl = createToolCallsEl(message.toolCalls);
    if (toolCallsEl) content.insertBefore(toolCallsEl, content.firstChild);
  }

  // 再处理正文（typing / prose / error / stopped）
  let proseWrap = content.querySelector('.prose');
  let errorEl = content.querySelector('.msg__error');
  let noteEl = content.querySelector('.msg__note');
  let typing = content.querySelector('.typing');

  if (!message.content) {
    if (proseWrap) proseWrap.remove();
    if (message.error) {
      if (!errorEl) {
        errorEl = buildErrorElement(message);
        content.appendChild(errorEl);
      }
    } else if (message.stopped) {
      if (errorEl) errorEl.remove();
      if (!noteEl) {
        noteEl = document.createElement('p');
        noteEl.className = 'msg__note';
        noteEl.textContent = '已停止生成。';
        content.appendChild(noteEl);
      }
    } else if (!message.toolCalls?.length) {
      if (errorEl) errorEl.remove();
      if (!typing) {
        typing = typingEl();
        content.appendChild(typing);
      }
    } else if (typing) {
      typing.remove();
    }
    return;
  }

  if (typing) typing.remove();
  if (noteEl) noteEl.remove();

  if (!proseWrap) {
    proseWrap = createProse(message.content);
    content.appendChild(proseWrap);
  } else {
    const next = createProse(message.content);
    proseWrap.replaceWith(next);
    proseWrap = next;
  }
  if (streaming) appendStreamCursor(proseWrap);
  else enhanceMarkdown(proseWrap);

  if (errorEl) errorEl.remove();
  if (message.content && message.error) {
    errorEl = buildErrorElement(message);
    content.appendChild(errorEl);
  }
}

function scheduleAssistantRender(message) {
  state.pendingMessage = message;
  if (state.renderTimer !== null) return;

  const wait = Math.max(
    0,
    STREAM_RENDER_INTERVAL - (performance.now() - state.lastRenderAt),
  );
  state.renderTimer = window.setTimeout(() => {
    state.renderTimer = null;
    state.lastRenderAt = performance.now();
    const target = state.pendingMessage;
    state.pendingMessage = null;
    if (!target) return;
    updateAssistantNode(target, true);
    if (state.stickToBottom) scrollToBottom();
  }, wait);
}

function scrollToBottom() {
  dom.chatScroll.scrollTop = dom.chatScroll.scrollHeight;
}

function isNearBottom() {
  const el = dom.chatScroll;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 90;
}

function scrollToBottomIfStuck() {
  if (state.stickToBottom) scrollToBottom();
}

function renderChat({ bottom = false } = {}) {
  const conversation = getActiveConversation();
  const hasMessages = Boolean(conversation && conversation.messages.length > 0);

  dom.emptyState.hidden = hasMessages;
  dom.messages.replaceChildren();

  if (conversation) {
    const lastAssistant = [...conversation.messages]
      .reverse()
      .find((message) => message.role === 'assistant');
    const fragment = document.createDocumentFragment();
    conversation.messages.forEach((message) => {
      fragment.appendChild(
        createMessageEl(message, {
          isLastAssistant: Boolean(lastAssistant && message.id === lastAssistant.id),
        }),
      );
    });
    dom.messages.appendChild(fragment);
  }

  updateTopbar();
  updateConfigTip();
  syncModeToggle();

  if (bottom) {
    state.stickToBottom = true;
    scrollToBottom();
  } else if (state.stickToBottom) {
    scrollToBottom();
  }
}

function renderHistory() {
  dom.historyList.replaceChildren();
  const sorted = [...state.conversations].sort(
    (a, b) => new Date(b.updatedAt) - new Date(a.updatedAt),
  );

  if (!sorted.length) {
    const empty = document.createElement('p');
    empty.className = 'history__empty';
    empty.textContent = '还没有对话。开始和 WeCi 聊聊吧。';
    dom.historyList.appendChild(empty);
    return;
  }

  sorted.forEach((conversation) => {
    dom.historyList.appendChild(
      createHistoryItem(conversation, conversation.id === state.activeId, {
        formatTime: formatHistoryTime,
        onSelect: selectConversation,
        onRename: startRenameConversation,
        onDelete: deleteConversation,
      }),
    );
  });
}

function updateTopbar() {
  const conversation = getActiveConversation();
  dom.chatTitle.textContent = conversation ? conversation.title : '新对话';

  const effective = getEffectiveSettings();
  dom.modelChipText.textContent = effective.model || '未配置模型';
  const configured = canChat();
  dom.modelChip.classList.toggle('is-missing', !configured);
  dom.modelChip.title = configured
    ? `${effective.model}\n${effective.apiBase}`
    : '模型尚未配置完整，点击打开设置';
}

function updateConfigTip() {
  dom.configTip.hidden = canChat();
}

/* --------------------------------------------------------------------------
   对话模式（Chat / Agent）
   -------------------------------------------------------------------------- */

function syncModeToggle() {
  const conversation = getActiveConversation();
  const mode = (conversation && conversation.mode) || state.settings.mode || 'chat';
  dom.modeSwitch.querySelectorAll('.mode-switch__btn').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.mode === mode);
  });
}

function setMode(mode) {
  if (mode !== 'chat' && mode !== 'agent') return;
  state.settings.mode = mode;
  saveSettings();
  const conversation = getActiveConversation();
  if (conversation) {
    conversation.mode = mode;
    saveState();
  }
  syncModeToggle();
}

/* --------------------------------------------------------------------------
   会话操作
   -------------------------------------------------------------------------- */

function startNewChat() {
  if (state.streaming) stopStreaming();
  state.activeId = null;
  state.stickToBottom = true;
  saveState();
  renderHistory();
  renderChat({ bottom: true });
  closeSidebar();
  window.setTimeout(() => dom.input.focus({ preventScroll: true }), 60);
}

function selectConversation(id) {
  if (state.streaming) stopStreaming();
  state.activeId = id;
  state.stickToBottom = true;
  saveState();
  renderHistory();
  renderChat({ bottom: true });
  closeSidebar();
  window.setTimeout(() => dom.input.focus({ preventScroll: true }), 60);
}

function deleteConversation(id) {
  const target = state.conversations.find((item) => item.id === id);
  if (!target) return;
  if (!window.confirm(`删除对话「${target.title}」？此操作不可恢复。`)) return;

  if (state.activeId === id && state.streaming) stopStreaming();
  state.conversations = state.conversations.filter((item) => item.id !== id);
  if (state.activeId === id) state.activeId = null;
  saveState();
  renderHistory();
  renderChat();
  toast('对话已删除', 'success');
}

function startRenameConversation(id) {
  const conversation = state.conversations.find((item) => item.id === id);
  if (!conversation) return;
  const item = dom.historyList.querySelector(`[data-conversation-id="${id}"]`);
  if (!item || item.dataset.renaming === 'true') return;
  item.dataset.renaming = 'true';

  const input = createHistoryRenameInput(conversation.title, {
    onCommit: (value) => {
      const title = value.trim();
      if (title && title !== conversation.title) {
        conversation.title =
          title.length > MAX_RENAME_LENGTH ? title.slice(0, MAX_RENAME_LENGTH) : title;
        saveState();
        toast('已重命名', 'success', 1600);
      }
      renderHistory();
      updateTopbar();
    },
    onCancel: () => {
      renderHistory();
    },
  });

  item.replaceChildren(input);
  input.focus();
  input.select();
}

function createConversation(firstMessage) {
  const now = new Date().toISOString();
  const conversation = {
    id: uid(),
    title: makeTitle(firstMessage),
    messages: [],
    mode: state.settings.mode || 'chat',
    createdAt: now,
    updatedAt: now,
  };
  state.conversations.unshift(conversation);
  state.activeId = conversation.id;
  return conversation;
}

function clearAllConversations() {
  if (!state.conversations.length) {
    toast('当前没有本地对话。', 'info');
    return;
  }
  if (!window.confirm('清除全部本地对话？此操作不可恢复。')) return;
  if (state.streaming) stopStreaming();
  state.conversations = [];
  state.activeId = null;
  saveState();
  renderHistory();
  renderChat();
  toast('已清除全部对话', 'success');
}

/* --------------------------------------------------------------------------
   发送 / 重新生成 / 编辑重发
   -------------------------------------------------------------------------- */

function buildApiMessages(conversation, excludeId) {
  return conversation.messages
    .filter((message) => message.id !== excludeId)
    .filter((message) => message.content && message.content.trim())
    .filter((message) => !(message.role === 'assistant' && message.error))
    .map((message) => ({ role: message.role, content: message.content }));
}

async function handleSend() {
  if (state.streaming) return;
  const text = dom.input.value.trim();
  if (!text) return;

  if (!canChat()) {
    openSettings();
    toast('请先完成模型配置：Endpoint、Model 与 API Key', 'error', 4200);
    return;
  }

  const conversation = getActiveConversation() || createConversation(text);
  const now = new Date().toISOString();

  if (!conversation.messages.some((message) => message.role === 'user')) {
    conversation.title = makeTitle(text);
  }

  conversation.messages.push({
    id: uid(),
    role: 'user',
    content: text,
    createdAt: now,
  });

  const assistant = {
    id: uid(),
    role: 'assistant',
    content: '',
    createdAt: now,
  };
  conversation.messages.push(assistant);
  conversation.updatedAt = now;

  dom.input.value = '';
  autoGrow();
  updateSendState();
  state.stickToBottom = true;

  saveState();
  renderHistory();
  renderChat({ bottom: true });

  await streamReply(conversation, assistant);
}

async function regenerateAssistant(messageId) {
  if (state.streaming) {
    toast('正在生成中，请稍后再试。', 'error');
    return;
  }
  if (!canChat()) {
    openSettings();
    toast('请先完成模型配置后再重新生成。', 'error');
    return;
  }

  const conversation = getActiveConversation();
  if (!conversation) return;
  const index = conversation.messages.findIndex(
    (message) => message.id === messageId,
  );
  if (index === -1 || index !== conversation.messages.length - 1) {
    toast('只能重新生成最近一条回复。', 'error');
    return;
  }

  const target = conversation.messages[index];
  target.content = '';
  delete target.error;
  delete target.stopped;
  target.createdAt = new Date().toISOString();
  conversation.updatedAt = target.createdAt;

  saveState();
  renderChat({ bottom: true });
  await streamReply(conversation, target);
}

function startEditMessage(messageId) {
  if (state.streaming) {
    toast('正在生成中，请先停止或等待完成。', 'error');
    return;
  }
  const conversation = getActiveConversation();
  if (!conversation) return;
  const index = conversation.messages.findIndex(
    (message) => message.id === messageId,
  );
  if (index === -1) return;
  const message = conversation.messages[index];
  if (message.role !== 'user') return;

  const article = findMessageEl(messageId);
  if (!article || article.classList.contains('is-editing')) return;
  article.classList.add('is-editing');

  const body = article.querySelector('.msg__body');
  if (!body) return;

  const editor = createEditEditor(message.content, {
    onSubmit: (text) => {
      const value = text.trim();
      if (!value) {
        toast('消息不能为空。', 'error');
        return;
      }
      applyEditMessage(conversation, index, value);
    },
    onCancel: () => {
      article.classList.remove('is-editing');
      editor.remove();
    },
  });

  body.appendChild(editor);
  const textarea = editor.querySelector('textarea');
  if (textarea) {
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }
}

async function applyEditMessage(conversation, index, text) {
  if (!canChat()) {
    openSettings();
    toast('请先完成模型配置后再发送。', 'error');
    renderChat();
    return;
  }

  const message = conversation.messages[index];
  message.content = text;
  message.edited = true;

  // 编辑会丢弃该消息之后的所有内容
  conversation.messages = conversation.messages.slice(0, index + 1);

  // 编辑的是首条用户消息时，同步刷新自动生成的标题
  const isFirstUserMessage = !conversation.messages
    .slice(0, index)
    .some((item) => item.role === 'user');
  if (isFirstUserMessage) {
    conversation.title = makeTitle(text);
  }

  const now = new Date().toISOString();
  const assistant = {
    id: uid(),
    role: 'assistant',
    content: '',
    createdAt: now,
  };
  conversation.messages.push(assistant);
  conversation.updatedAt = now;

  state.stickToBottom = true;
  saveState();
  renderHistory();
  renderChat({ bottom: true });

  await streamReply(conversation, assistant);
}

/* --------------------------------------------------------------------------
   流式输出编排
   -------------------------------------------------------------------------- */

let streamTicker = null;

function setStreaming(active) {
  state.streaming = active;
  dom.sendBtn.hidden = active;
  dom.stopBtn.hidden = !active;

  if (streamTicker) {
    window.clearInterval(streamTicker);
    streamTicker = null;
  }
  if (active) {
    const startedAt = Date.now();
    state.agentStatus = null;
    dom.composerHint.textContent = '正在生成…';
    streamTicker = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      if (state.agentStatus) {
        dom.composerHint.textContent = state.agentStatus;
      } else if (seconds >= 1) {
        dom.composerHint.textContent = `正在生成… ${seconds}s · 点击停止可中断`;
      }
    }, 500);
  } else {
    state.agentStatus = null;
    dom.composerHint.textContent = 'Enter 发送 · Shift + Enter 换行';
  }
  updateSendState();
}

function stopStreaming() {
  const controller = state.controller;
  state.controller = null;
  if (controller) controller.abort();
  setStreaming(false);
}

async function streamReply(conversation, assistant) {
  const effective = getEffectiveSettings();
  const messages = buildApiMessages(conversation, assistant.id);
  if (!messages.length) {
    assistant.error = '没有可发送的消息。';
    renderChat();
    return;
  }

  const mode = conversation.mode || state.settings.mode || 'chat';
  assistant.toolCalls = [];

  const token = ++state.streamToken;
  const controller = new AbortController();
  state.controller = controller;
  setStreaming(true);
  state.stickToBottom = true;

  const upsertToolCall = (tc) => {
    if (!assistant.toolCalls) assistant.toolCalls = [];
    const existing = assistant.toolCalls.find((item) => item.id === tc.id);
    if (existing) {
      Object.assign(existing, tc);
    } else {
      assistant.toolCalls.push({ ...tc });
    }
  };

  try {
    const stream = streamChat({
      messages,
      model: effective.model,
      apiBase: effective.apiBase,
      apiKey: state.settings.apiKey,
      temperature: effective.temperature,
      maxTokens: effective.maxTokens,
      mode,
      signal: controller.signal,
    });

    for await (const event of stream) {
      if (token !== state.streamToken) break; // 已有更新的请求接管

      if (event.type === 'delta' && typeof event.text === 'string' && event.text) {
        state.agentStatus = null;
        assistant.content += event.text;
        scheduleAssistantRender(assistant);
        scrollToBottomIfStuck();
        scheduleSave();
      } else if (event.type === 'thinking') {
        state.agentStatus = `Agent 正在思考…（第 ${event.step} 步）`;
        dom.composerHint.textContent = state.agentStatus;
      } else if (event.type === 'tool_call') {
        const tc = {
          id: event.id,
          name: event.name,
          status: event.status,
          durationMs: typeof event.duration_ms === 'number' ? event.duration_ms : null,
          error: event.error || null,
        };
        upsertToolCall(tc);
        if (event.status === 'running') {
          state.agentStatus = `正在调用 ${event.name}…`;
        } else {
          state.agentStatus = null;
        }
        dom.composerHint.textContent = state.agentStatus || '正在生成…';
        updateAssistantNode(assistant, Boolean(assistant.content));
        scrollToBottomIfStuck();
        scheduleSave();
      } else if (event.type === 'max_steps') {
        const note = event.message || '已达到最大执行步数，Agent 已停止。';
        assistant.content = (assistant.content || '') + `\n\n> ${note}`;
      } else if (event.type === 'error') {
        throw new Error(event.message || '模型服务返回错误。');
      }
    }
  } catch (error) {
    if (error && error.name === 'AbortError') {
      assistant.stopped = true;
    } else if (error instanceof TypeError) {
      assistant.error =
        '无法连接本地后端服务，请确认 FastAPI 已启动（默认 http://127.0.0.1:8000）。';
    } else {
      assistant.error =
        error && error.message ? error.message : '生成失败，请稍后重试。';
    }
  } finally {
    // 丢弃尚未执行的流式重绘，避免它覆盖最终的完整渲染
    if (state.renderTimer !== null) {
      window.clearTimeout(state.renderTimer);
      state.renderTimer = null;
    }
    state.pendingMessage = null;

    if (!assistant.content && !assistant.error && !assistant.stopped) {
      assistant.error = '没有收到任何回复，请重试。';
    }

    conversation.updatedAt = new Date().toISOString();
    saveState();

    if (token === state.streamToken) {
      state.controller = null;
      setStreaming(false);
      renderHistory();
      renderChat({ bottom: state.stickToBottom });
      updateTopbar();
      syncModeToggle();
      window.setTimeout(() => dom.input.focus({ preventScroll: true }), 60);
    }
  }
}

/* --------------------------------------------------------------------------
   设置面板
   -------------------------------------------------------------------------- */

function renderModelDatalist(remoteModels) {
  const seen = new Set();
  const options = [];
  const push = (value) => {
    const name = (value || '').trim();
    if (name && !seen.has(name)) {
      seen.add(name);
      options.push(name);
    }
  };
  (Array.isArray(remoteModels) ? remoteModels : []).forEach(push);
  state.settings.models.forEach(push);
  MODEL_PRESETS.forEach(push);

  dom.modelPresets.replaceChildren(
    ...options.map((name) => {
      const option = document.createElement('option');
      option.value = name;
      return option;
    }),
  );
}

function setTestResult(text, kind) {
  dom.testConnResult.textContent = text || '';
  dom.testConnResult.dataset.state = kind || '';
}

let testBusy = false;

async function handleTestConnection() {
  if (testBusy) return;
  const apiBase = dom.settingEndpoint.value.trim().replace(/\/+$/, '');
  const model = dom.settingModel.value.trim();
  const apiKey = dom.settingApiKey.value.trim();

  if (!/^https?:\/\//i.test(apiBase)) {
    setTestResult('请先填写合法的 API Endpoint（http:// 或 https://）。', 'error');
    dom.settingEndpoint.focus();
    return;
  }

  testBusy = true;
  dom.testConnBtn.disabled = true;
  dom.testConnBtn.classList.add('is-loading');
  setTestResult('正在连接…', 'pending');

  try {
    const result = await testConnection({ apiBase, model, apiKey });
    if (result && result.ok) {
      const parts = ['连接成功'];
      if (typeof result.latency_ms === 'number') parts.push(`${result.latency_ms}ms`);
      if (Array.isArray(result.models)) {
        parts.push(
          result.models.length ? `发现 ${result.models.length} 个模型` : '模型列表为空',
        );
      } else {
        parts.push('已验证对话接口');
      }
      setTestResult(parts.join(' · '), 'ok');
      if (Array.isArray(result.models) && result.models.length) {
        renderModelDatalist(result.models);
      }
    } else {
      setTestResult((result && result.message) || '连接失败。', 'error');
    }
  } catch (error) {
    setTestResult(
      error instanceof TypeError
        ? '无法连接本地后端服务，请确认 FastAPI 已启动。'
        : error.message || '测试失败。',
      'error',
    );
  } finally {
    testBusy = false;
    dom.testConnBtn.disabled = false;
    dom.testConnBtn.classList.remove('is-loading');
  }
}

/* --------------------------------------------------------------------------
   插件管理
   -------------------------------------------------------------------------- */

async function loadPlugins() {
  const list = dom.pluginsList;
  if (!list) return;
  list.innerHTML = '<p class="plugins__empty">正在加载插件…</p>';
  try {
    const plugins = await fetchPlugins();
    renderPlugins(plugins);
  } catch (error) {
    list.innerHTML = `<p class="plugins__empty plugins__empty--error">加载插件失败：${error.message || error}</p>`;
  }
}

function renderPlugins(plugins) {
  const list = dom.pluginsList;
  if (!list) return;
  if (!plugins.length) {
    list.innerHTML = '<p class="plugins__empty">未发现任何插件。</p>';
    return;
  }

  list.innerHTML = '';

  const official = plugins.filter((p) => p.is_builtin);
  const user = plugins.filter((p) => !p.is_builtin);

  const groups = [];
  if (official.length) groups.push({ title: '官方技能', items: official });
  if (user.length) groups.push({ title: '用户插件', items: user });

  for (const group of groups) {
    const heading = document.createElement('h3');
    heading.className = 'plugins__group-title';
    heading.textContent = `${group.title}（${group.items.length}）`;
    list.appendChild(heading);

    for (const p of group.items) {
      const card = document.createElement('div');
      card.className = 'plugin-card';
      if (p.status === 'failed') card.classList.add('plugin-card--failed');
      if (p.status === 'disabled') card.classList.add('plugin-card--disabled');

      const statusLabel = {
        loaded: '已启用',
        disabled: '已禁用',
        failed: '加载失败',
      }[p.status] || p.status;

      const toolsHtml = (p.tools && p.tools.length)
        ? p.tools.map((t) => `<code class="plugin-card__tool">${escapeHtml(t)}</code>`).join(' ')
        : '<span class="plugins__hint">无</span>';

      const permsHtml = (p.permissions && p.permissions.length)
        ? p.permissions.map((perm) => `<span class="plugin-card__perm">${escapeHtml(perm)}</span>`).join(' ')
        : '<span class="plugins__hint">无</span>';

      const errorHtml = p.error
        ? `<div class="plugin-card__error"><svg class="icon" viewBox="0 0 24 24"><use href="#i-alert"/></svg> ${escapeHtml(p.error)}</div>`
        : '';

      const builtinBadge = p.is_builtin
        ? '<span class="plugin-card__badge plugin-card__badge--official">官方技能</span>'
        : '<span class="plugin-card__badge">用户插件</span>';
      const removeBtn = p.is_builtin
        ? ''
        : `<button class="ghost-btn ghost-btn--danger" data-action="remove" data-id="${escapeHtml(p.id)}" type="button">移除</button>`;
      const toggleBtn = p.status === 'disabled'
        ? `<button class="ghost-btn" data-action="enable" data-id="${escapeHtml(p.id)}" type="button">启用</button>`
        : `<button class="ghost-btn" data-action="disable" data-id="${escapeHtml(p.id)}" type="button">禁用</button>`;

      card.innerHTML = `
        <div class="plugin-card__head">
          <div class="plugin-card__title">
            <strong>${escapeHtml(p.name)}</strong>
            ${builtinBadge}
            <span class="plugin-card__version">v${escapeHtml(p.version)}</span>
          </div>
          <span class="plugin-card__status plugin-card__status--${p.status}">${statusLabel}</span>
        </div>
        <p class="plugin-card__desc">${escapeHtml(p.description || '')}</p>
        <div class="plugin-card__meta">
          <div><span class="plugins__hint">工具：</span>${toolsHtml}</div>
          <div><span class="plugins__hint">权限：</span>${permsHtml}</div>
        </div>
        ${errorHtml}
        <div class="plugin-card__actions">
          ${toggleBtn}
          ${removeBtn}
        </div>
      `;
      list.appendChild(card);
    }
  }

  // 绑定按钮
  list.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => handlePluginAction(btn.dataset.action, btn.dataset.id));
  });
}

async function handlePluginAction(action, pluginId) {
  try {
    if (action === 'enable') {
      await enablePlugin(pluginId);
      toast(`已启用插件：${pluginId}`);
    } else if (action === 'disable') {
      if (!confirm(`确定禁用插件「${pluginId}」吗？禁用后其工具将不可用。`)) return;
      await disablePlugin(pluginId);
      toast(`已禁用插件：${pluginId}`);
    } else if (action === 'remove') {
      if (!confirm(`确定移除插件「${pluginId}」吗？此操作不可撤销。`)) return;
      await removePlugin(pluginId);
      toast(`已移除插件：${pluginId}`);
    }
    await loadPlugins();
  } catch (error) {
    toast(`操作失败：${error.message || error}`, 'error');
  }
}

function openSettings() {
  const effective = getEffectiveSettings();
  dom.settingEndpoint.value = effective.apiBase || '';
  dom.settingApiKey.value = state.settings.apiKey || '';
  dom.settingModel.value = effective.model || '';
  dom.settingTemperature.value = String(effective.temperature);
  dom.tempValue.textContent = Number(effective.temperature).toFixed(1);
  dom.settingMaxTokens.value =
    typeof effective.maxTokens === 'number' ? String(effective.maxTokens) : '';
  dom.serverKeyBadge.hidden = !state.server.hasKey;
  dom.settingApiKey.placeholder = state.server.hasKey
    ? '留空则使用服务端环境变量中的 Key'
    : 'sk-…（本地服务可留空）';
  dom.settingApiKey.type = 'password';
  dom.toggleKeyBtn.textContent = '显示';
  setTestResult('', '');
  renderModelDatalist();

  state.settingsOpener = document.activeElement;
  syncThemeControls();
  closeSidebar();
  toggleModelMenu(false);
  dom.settingsModal.hidden = false;

  window.requestAnimationFrame(() => {
    const panel = dom.settingsModal.querySelector('.modal__panel');
    if (panel) {
      panel.setAttribute('tabindex', '-1');
      panel.focus({ preventScroll: true });
    }
  });
}

function openPluginsPage() {
  closeSettings();
  closeSidebar();
  toggleModelMenu(false);
  dom.chatScroll.hidden = true;
  dom.composerForm.parentElement.hidden = true;
  dom.pluginsPage.hidden = false;
  state.pluginsOpener = document.activeElement;
  loadPlugins();
  window.requestAnimationFrame(() => {
    dom.pluginsBackBtn.focus({ preventScroll: true });
  });
}

function closePluginsPage() {
  if (dom.pluginsPage.hidden) return;
  dom.pluginsPage.hidden = true;
  dom.chatScroll.hidden = false;
  dom.composerForm.parentElement.hidden = false;
  if (state.pluginsOpener && typeof state.pluginsOpener.focus === 'function') {
    state.pluginsOpener.focus({ preventScroll: true });
  }
}

function closeSettings() {
  if (dom.settingsModal.hidden) return;
  dom.settingsModal.hidden = true;
  if (state.settingsOpener && typeof state.settingsOpener.focus === 'function') {
    state.settingsOpener.focus({ preventScroll: true });
  }
}

function saveSettingsFromForm(event) {
  event.preventDefault();

  const apiBase = dom.settingEndpoint.value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(apiBase)) {
    toast('API Endpoint 必须以 http:// 或 https:// 开头。', 'error');
    dom.settingEndpoint.focus();
    return;
  }

  const model = dom.settingModel.value.trim();
  if (!model) {
    toast('请填写 Model 名称。', 'error');
    dom.settingModel.focus();
    return;
  }

  const apiKey = dom.settingApiKey.value.trim();
  if (!apiKey && !state.server.hasKey && !endpointIsLocal(apiBase)) {
    toast('云端模型服务需要 API Key；本地服务可以留空。', 'error');
    dom.settingApiKey.focus();
    return;
  }

  const rawTemperature = Number(dom.settingTemperature.value);
  const temperature = Number.isFinite(rawTemperature)
    ? clamp(rawTemperature, 0, 2)
    : DEFAULT_TEMPERATURE;

  const rawMaxTokens = dom.settingMaxTokens.value.trim();
  let maxTokens = null;
  if (rawMaxTokens) {
    const parsed = Number(rawMaxTokens);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 200000) {
      toast('Max Tokens 需要是 1–200000 之间的整数。', 'error');
      dom.settingMaxTokens.focus();
      return;
    }
    maxTokens = parsed;
  }

  state.settings = {
    ...state.settings,
    apiBase,
    apiKey,
    model,
    temperature,
    maxTokens,
  };
  rememberModel(model);
  saveSettings();
  updateTopbar();
  updateConfigTip();
  closeSettings();
  toast('设置已保存，立即生效', 'success', 2200);
}

/* --------------------------------------------------------------------------
   模型快速切换（顶栏下拉）
   -------------------------------------------------------------------------- */

function toggleModelMenu(force) {
  const show = typeof force === 'boolean' ? force : dom.modelMenu.hidden;
  if (show) {
    renderModelMenu();
    dom.modelMenu.hidden = false;
    dom.modelChip.setAttribute('aria-expanded', 'true');
  } else {
    dom.modelMenu.hidden = true;
    dom.modelChip.setAttribute('aria-expanded', 'false');
  }
}

function renderModelMenu() {
  dom.modelMenu.replaceChildren();
  const effective = getEffectiveSettings();
  const models = state.settings.models;

  if (!models.length) {
    const empty = document.createElement('p');
    empty.className = 'model-menu__empty';
    empty.textContent = '暂无最近使用的模型，可在设置中添加。';
    dom.modelMenu.appendChild(empty);
  } else {
    models.forEach((name) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `model-menu__item${name === effective.model ? ' is-active' : ''}`;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(name === effective.model));
      item.appendChild(icon('check'));
      const text = document.createElement('span');
      text.textContent = name;
      item.appendChild(text);
      item.addEventListener('click', () => chooseModel(name));
      dom.modelMenu.appendChild(item);
    });
  }

  const divider = document.createElement('div');
  divider.className = 'model-menu__divider';
  dom.modelMenu.appendChild(divider);

  const manage = document.createElement('button');
  manage.type = 'button';
  manage.className = 'model-menu__item model-menu__manage';
  manage.appendChild(icon('settings'));
  const manageText = document.createElement('span');
  manageText.textContent = '管理模型…';
  manage.appendChild(manageText);
  manage.addEventListener('click', () => {
    toggleModelMenu(false);
    openSettings();
  });
  dom.modelMenu.appendChild(manage);
}

function chooseModel(name) {
  state.settings.model = name;
  rememberModel(name);
  saveSettings();
  toggleModelMenu(false);
  updateTopbar();
  toast(`已切换模型：${name}`, 'success', 2000);
}

/* --------------------------------------------------------------------------
   输入与侧栏
   -------------------------------------------------------------------------- */

function autoGrow() {
  dom.input.style.height = 'auto';
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, MAX_INPUT_HEIGHT)}px`;
}

function updateSendState() {
  dom.sendBtn.disabled = state.streaming || !dom.input.value.trim();
}

function isNarrowScreen() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function openSidebar() {
  dom.app.classList.add('is-sidebar-open');
  dom.scrim.hidden = false;
}

function closeSidebar() {
  dom.app.classList.remove('is-sidebar-open');
  dom.scrim.hidden = true;
}

function setSidebarCollapsed(collapsed, persist = true) {
  state.sidebarCollapsed = collapsed;
  dom.app.classList.toggle('is-sidebar-collapsed', collapsed);
  if (persist) saveSidebarPref(collapsed);
}

function toggleSidebar() {
  if (isNarrowScreen()) {
    if (dom.app.classList.contains('is-sidebar-open')) closeSidebar();
    else openSidebar();
  } else {
    setSidebarCollapsed(!state.sidebarCollapsed);
  }
}

/* --------------------------------------------------------------------------
   事件绑定与启动
   -------------------------------------------------------------------------- */

function bindEvents() {
  dom.composerForm.addEventListener('submit', (event) => {
    event.preventDefault();
    handleSend();
  });

  dom.input.addEventListener('input', () => {
    autoGrow();
    updateSendState();
  });

  dom.input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      handleSend();
    }
  });

  dom.sendBtn.addEventListener('click', (event) => {
    event.preventDefault();
    handleSend();
  });

  dom.stopBtn.addEventListener('click', stopStreaming);

  dom.newChatBtn.addEventListener('click', startNewChat);
  dom.brandLink.addEventListener('click', (event) => {
    event.preventDefault();
    startNewChat();
  });

  dom.menuBtn.addEventListener('click', openSidebar);
  dom.sidebarClose.addEventListener('click', closeSidebar);
  dom.scrim.addEventListener('click', closeSidebar);
  dom.sidebarCollapse.addEventListener('click', () => setSidebarCollapsed(true));
  dom.sidebarExpand.addEventListener('click', () => setSidebarCollapsed(false));

  dom.modeSwitch.addEventListener('click', (event) => {
    const btn = event.target.closest('.mode-switch__btn');
    if (!btn) return;
    setMode(btn.dataset.mode);
  });

  dom.settingsBtn.addEventListener('click', openSettings);
  dom.topSettingsBtn.addEventListener('click', openSettings);
  dom.configTipBtn.addEventListener('click', openSettings);

  dom.topPluginsBtn.addEventListener('click', openPluginsPage);
  dom.pluginsBackBtn.addEventListener('click', closePluginsPage);
  dom.settingsOpenPluginsBtn.addEventListener('click', openPluginsPage);

  dom.modelChip.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleModelMenu();
  });
  dom.modelMenu.addEventListener('click', (event) => event.stopPropagation());
  document.addEventListener('click', (event) => {
    if (!dom.modelMenu.hidden && !event.target.closest('#modelSwitch')) {
      toggleModelMenu(false);
    }
  });

  dom.themeBtn.addEventListener('click', toggleTheme);
  dom.topThemeBtn.addEventListener('click', toggleTheme);

  dom.settingsModal.querySelectorAll('[data-close-settings]').forEach((node) => {
    node.addEventListener('click', closeSettings);
  });

  dom.settingsForm.addEventListener('submit', saveSettingsFromForm);
  dom.testConnBtn.addEventListener('click', handleTestConnection);

  dom.settingTemperature.addEventListener('input', () => {
    dom.tempValue.textContent = Number(dom.settingTemperature.value).toFixed(1);
  });

  dom.toggleKeyBtn.addEventListener('click', () => {
    const show = dom.settingApiKey.type === 'password';
    dom.settingApiKey.type = show ? 'text' : 'password';
    dom.toggleKeyBtn.textContent = show ? '隐藏' : '显示';
  });

  dom.settingsModal.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.addEventListener('click', () => {
      applyTheme(button.dataset.themeChoice);
    });
  });

  dom.clearAllBtn.addEventListener('click', clearAllConversations);
  dom.pluginsRefreshBtn.addEventListener('click', loadPlugins);

  dom.suggestions.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-prompt]');
    if (!button) return;
    dom.input.value = button.dataset.prompt || '';
    autoGrow();
    updateSendState();
    handleSend();
  });

  dom.messages.addEventListener('click', (event) => {
    const codeCopy = event.target.closest('[data-code-copy]');
    if (codeCopy) {
      const pre = codeCopy.closest('pre');
      const code = pre ? pre.querySelector('code') : null;
      if (code) copyText(code.innerText);
      return;
    }

    const action = event.target.closest('[data-action]');
    if (!action) return;
    const messageId = action.dataset.messageId;

    if (action.dataset.action === 'retry' || action.dataset.action === 'regenerate') {
      regenerateAssistant(messageId);
      return;
    }
    if (action.dataset.action === 'edit-message') {
      startEditMessage(messageId);
      return;
    }
    if (action.dataset.action === 'copy-message') {
      const conversation = getActiveConversation();
      const message = conversation
        ? conversation.messages.find((item) => item.id === messageId)
        : null;
      if (message) copyText(message.content);
    }
  });

  dom.chatScroll.addEventListener(
    'scroll',
    () => {
      state.stickToBottom = isNearBottom();
    },
    { passive: true },
  );

  document.addEventListener('keydown', (event) => {
    const mod = event.metaKey || event.ctrlKey;
    const key = event.key.toLowerCase();

    if (mod && key === 'k') {
      event.preventDefault();
      startNewChat();
      return;
    }
    if (mod && key === 'b') {
      event.preventDefault();
      toggleSidebar();
      return;
    }
    if (event.key === 'Escape') {
      if (!dom.pluginsPage.hidden) {
        closePluginsPage();
      } else if (!dom.settingsModal.hidden) {
        closeSettings();
      } else if (!dom.modelMenu.hidden) {
        toggleModelMenu(false);
      } else if (dom.app.classList.contains('is-sidebar-open')) {
        closeSidebar();
      }
    }
  });

  window.addEventListener('pagehide', saveState);

  if (isMacLike) {
    const kbd = dom.newChatBtn.querySelector('.new-chat__kbd');
    if (kbd) kbd.textContent = '⌘ K';
    const kbdNewChat = document.getElementById('kbdNewChat');
    if (kbdNewChat) kbdNewChat.textContent = '⌘ + K';
    const kbdSidebar = document.getElementById('kbdSidebar');
    if (kbdSidebar) kbdSidebar.textContent = '⌘ + B';
    dom.sidebarCollapse.title = '收起侧栏（⌘ B）';
    dom.sidebarExpand.title = '展开侧栏（⌘ B）';
  } else {
    dom.sidebarCollapse.title = '收起侧栏（Ctrl + B）';
    dom.sidebarExpand.title = '展开侧栏（Ctrl + B）';
  }
}

async function loadServerConfig() {
  try {
    const data = await fetchServerConfig();
    state.server = {
      apiBase: typeof data.api_base === 'string' ? data.api_base : '',
      model: typeof data.model === 'string' ? data.model : '',
      temperature:
        typeof data.temperature === 'number'
          ? data.temperature
          : DEFAULT_TEMPERATURE,
      maxTokens: typeof data.max_tokens === 'number' ? data.max_tokens : null,
      hasKey: Boolean(data.has_server_key),
    };
  } catch (error) {
    console.warn('[WeCi] failed to load server config', error);
    if (!state.configErrorShown && location.protocol !== 'file:') {
      state.configErrorShown = true;
      toast(
        '无法连接后端服务，请确认 FastAPI 已启动并访问 http://127.0.0.1:8000。',
        'error',
        5200,
      );
    }
  }
  updateTopbar();
  updateConfigTip();
}

function init() {
  loadSettings();
  loadState();
  setPersistNotifier((message) => toast(message, 'error'));
  bindEvents();
  applyTheme(state.theme, false);
  setSidebarCollapsed(loadSidebarPref(), false);
  renderHistory();
  renderChat();
  autoGrow();
  updateSendState();
  updateTopbar();
  updateConfigTip();
  loadServerConfig();

  if (isNarrowScreen()) {
    dom.input.blur();
  } else {
    dom.input.focus({ preventScroll: true });
  }
}

init();
