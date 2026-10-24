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
  getActiveProvider,
  resolveProviderModel,
  getModelCapabilities,
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
  getUserId,
} from './store.js';
import {
  fetchServerConfig,
  streamChat,
  testConnection,
  fetchProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  createProviderModel,
  updateProviderModel,
  deleteProviderModel,
  testProvider,
  testProviderModel,
  fetchRuns,
  fetchRun,
  cancelRun,
  fetchPermissions,
  grantPermission,
  denyPermission,
  resetPermissions,
  resolvePermissionRequest,
  fetchSecurityLog,
  fetchPlugins,
  enablePlugin,
  disablePlugin,
  removePlugin,
  fetchMemories,
  createMemory,
  updateMemory,
  deleteMemory,
  fetchMcpServers,
  createMcpServer,
  updateMcpServer,
  deleteMcpServer,
  connectMcpServer,
  disconnectMcpServer,
} from './api.js';
import { createProse, enhanceMarkdown, appendStreamCursor, typingEl } from './markdown.js';
import {
  toast,
  copyText,
  createMessageEl,
  createToolCallsEl,
  createPermissionRequestsEl,
  createImageGrid,
  createFileList,
  buildErrorElement,
  createHistoryItem,
  createHistoryRenameInput,
  createEditEditor,
} from './ui.js';
import {
  MAX_IMAGES,
  MAX_FILES,
  AttachmentError,
  createAttachment,
  createFileAttachment,
  toApiAttachment,
  toApiFile,
  formatBytes,
} from './attachments.js';

const MAX_INPUT_HEIGHT = 200;
const STREAM_RENDER_INTERVAL = 70; // ms，控制流式 Markdown 重绘频率
const MODEL_PRESETS = ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'deepseek-chat', 'qwen-plus', 'llama3.1'];
let pageUnloading = false; // 页面正在刷新 / 关闭，此时的中断属于“页面中断”而非用户停止

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
  composerWrap: document.querySelector('.composer-wrap'),
  input: document.getElementById('input'),
  attachBtn: document.getElementById('attachBtn'),
  fileBtn: document.getElementById('fileBtn'),
  attachStrip: document.getElementById('attachStrip'),
  fileInput: document.getElementById('fileInput'),
  docInput: document.getElementById('docInput'),
  sendBtn: document.getElementById('sendBtn'),
  stopBtn: document.getElementById('stopBtn'),
  composerHint: document.getElementById('composerHint'),
  runHistoryPanel: document.getElementById('runHistoryPanel'),
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
  topMemoryBtn: document.getElementById('topMemoryBtn'),
  memoryPage: document.getElementById('memoryPage'),
  memoryBackBtn: document.getElementById('memoryBackBtn'),
  memoryRefreshBtn: document.getElementById('memoryRefreshBtn'),
  memoryList: document.getElementById('memoryList'),
  memoryForm: document.getElementById('memoryForm'),
  memoryKey: document.getElementById('memoryKey'),
  memoryContent: document.getElementById('memoryContent'),
  memoryScope: document.getElementById('memoryScope'),
  memoryScopeHint: document.getElementById('memoryScopeHint'),
  settingsOpenMemoryBtn: document.getElementById('settingsOpenMemoryBtn'),
  topMcpBtn: document.getElementById('topMcpBtn'),
  mcpPage: document.getElementById('mcpPage'),
  mcpBackBtn: document.getElementById('mcpBackBtn'),
  mcpRefreshBtn: document.getElementById('mcpRefreshBtn'),
  mcpList: document.getElementById('mcpList'),
  mcpForm: document.getElementById('mcpForm'),
  mcpEditId: document.getElementById('mcpEditId'),
  mcpName: document.getElementById('mcpName'),
  mcpCommand: document.getElementById('mcpCommand'),
  mcpArgs: document.getElementById('mcpArgs'),
  mcpEnv: document.getElementById('mcpEnv'),
  mcpFormHint: document.getElementById('mcpFormHint'),
  mcpSubmitBtn: document.getElementById('mcpSubmitBtn'),
  mcpCancelEditBtn: document.getElementById('mcpCancelEditBtn'),
  settingsOpenMcpBtn: document.getElementById('settingsOpenMcpBtn'),
  providersBtn: document.getElementById('providersBtn'),
  providersPage: document.getElementById('providersPage'),
  providersBackBtn: document.getElementById('providersBackBtn'),
  providersRefreshBtn: document.getElementById('providersRefreshBtn'),
  providerList: document.getElementById('providerList'),
  providerForm: document.getElementById('providerForm'),
  providerEditId: document.getElementById('providerEditId'),
  providerName: document.getElementById('providerName'),
  providerBase: document.getElementById('providerBase'),
  providerApiKey: document.getElementById('providerApiKey'),
  providerDescription: document.getElementById('providerDescription'),
  providerEnabled: document.getElementById('providerEnabled'),
  providerDefault: document.getElementById('providerDefault'),
  providerFormHint: document.getElementById('providerFormHint'),
  providerSubmitBtn: document.getElementById('providerSubmitBtn'),
  providerCancelEditBtn: document.getElementById('providerCancelEditBtn'),
  providerModelForm: document.getElementById('providerModelForm'),
  modelEditId: document.getElementById('modelEditId'),
  modelProvider: document.getElementById('modelProvider'),
  modelId: document.getElementById('modelId'),
  modelDisplayName: document.getElementById('modelDisplayName'),
  modelContextWindow: document.getElementById('modelContextWindow'),
  modelMaxTokens: document.getElementById('modelMaxTokens'),
  modelCapText: document.getElementById('modelCapText'),
  modelCapVision: document.getElementById('modelCapVision'),
  modelCapTools: document.getElementById('modelCapTools'),
  modelCapStreaming: document.getElementById('modelCapStreaming'),
  modelFormHint: document.getElementById('modelFormHint'),
  modelSubmitBtn: document.getElementById('modelSubmitBtn'),
  modelCancelEditBtn: document.getElementById('modelCancelEditBtn'),
  settingProvider: document.getElementById('settingProvider'),
  settingProviderModel: document.getElementById('settingProviderModel'),
  settingProviderHint: document.getElementById('settingProviderHint'),
  settingModelHint: document.getElementById('settingModelHint'),
  settingCapabilities: document.getElementById('settingCapabilities'),
  serverProviderBadge: document.getElementById('serverProviderBadge'),
  testProviderBtn: document.getElementById('testProviderBtn'),
  providerTestResult: document.getElementById('providerTestResult'),
  openProvidersBtn: document.getElementById('openProvidersBtn'),
  compatDetails: document.getElementById('compatDetails'),
  permissionsBtn: document.getElementById('permissionsBtn'),
  topPermissionsBtn: document.getElementById('topPermissionsBtn'),
  permissionsPage: document.getElementById('permissionsPage'),
  permissionsBackBtn: document.getElementById('permissionsBackBtn'),
  permissionsRefreshBtn: document.getElementById('permissionsRefreshBtn'),
  permOverview: document.getElementById('permOverview'),
  permGrants: document.getElementById('permGrants'),
  permCatalog: document.getElementById('permCatalog'),
  permTools: document.getElementById('permTools'),
  permLog: document.getElementById('permLog'),
  permResetAllBtn: document.getElementById('permResetAllBtn'),
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

  // 先放 Agent 执行过程（工具调用卡片，若有）
  const existingTrace = content.querySelector('.agent-trace');
  if (existingTrace) existingTrace.remove();
  const toolCallsEl = createToolCallsEl(message.toolCalls, {
    state: message.agentState,
    step: message.agentStep,
    maxSteps: message.agentMaxSteps,
  });
  if (toolCallsEl) content.insertBefore(toolCallsEl, content.firstChild);

  const existingPermissions = content.querySelector('.perm-requests');
  if (existingPermissions) existingPermissions.remove();
  const permissionEl = createPermissionRequestsEl(
    message.permissionRequests,
    handlePermissionResolve,
  );
  if (permissionEl) {
    content.insertBefore(
      permissionEl,
      toolCallsEl ? toolCallsEl.nextSibling : content.firstChild,
    );
  }

  // 再处理正文（typing / prose / error / stopped）
  let proseWrap = content.querySelector('.prose');
  let errorEl = content.querySelector('.msg__error');
  let noteEl = content.querySelector('.msg__note');
  let typing = content.querySelector('.typing');

  if (!message.content) {
    if (proseWrap) proseWrap.remove();
    if (message.error) {
      if (noteEl) noteEl.remove();
      if (!errorEl) {
        errorEl = buildErrorElement(message);
        content.appendChild(errorEl);
      }
    } else if (message.stopped) {
      if (errorEl) errorEl.remove();
      if (!noteEl) {
        noteEl = document.createElement('p');
        noteEl.className = 'msg__note';
        content.appendChild(noteEl);
      }
      noteEl.textContent = message.interrupted
        ? '该回复已中断（页面刷新或网络断开），可重新生成。'
        : '已停止生成。';
    } else {
      if (errorEl) errorEl.remove();
      if (noteEl) noteEl.remove();
      if (!typing) {
        typing = typingEl();
        content.appendChild(typing);
      }
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
          onResolvePermission: handlePermissionResolve,
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

const CAPABILITY_LABELS = {
  text: 'Text',
  vision: 'Vision',
  tool_calling: 'Tool Calling',
  streaming: 'Streaming',
};

function missingCapabilities(capabilities) {
  if (!capabilities) return [];
  return Object.keys(CAPABILITY_LABELS).filter(
    (key) => capabilities[key] === false,
  );
}

function updateTopbar() {
  const conversation = getActiveConversation();
  dom.chatTitle.textContent = conversation ? conversation.title : '新对话';

  const mode = (conversation && conversation.mode) || state.settings.mode || 'chat';
  const effective = getEffectiveSettings(mode);
  dom.modelChipText.textContent = effective.providerName
    ? `${effective.providerName} · ${effective.model || '未配置模型'}`
    : effective.model || '未配置模型';
  const configured = canChat();
  const missing = missingCapabilities(effective.capabilities);
  dom.modelChip.classList.toggle('is-missing', !configured);
  dom.modelChip.classList.toggle('is-limited', configured && missing.length > 0);

  const lines = [];
  if (effective.providerName) {
    lines.push(`Provider：${effective.providerName}`);
    lines.push(`Endpoint：${effective.apiBase}`);
    lines.push(`Model：${effective.model || '未配置'}`);
    if (effective.capabilities) {
      lines.push(
        `能力：${Object.keys(CAPABILITY_LABELS)
          .map(
            (key) =>
              `${effective.capabilities[key] === false ? '✗' : '✓'} ${CAPABILITY_LABELS[key]}`,
          )
          .join(' · ')}`,
      );
    }
    if (missing.length) {
      lines.push(`能力缺失：${missing.map((key) => CAPABILITY_LABELS[key]).join('、')}`);
    }
  } else {
    lines.push(`Endpoint：${effective.apiBase || '未配置'}`);
    lines.push(`Model：${effective.model || '未配置'}`);
  }
  if (!configured) lines.push('点击打开设置');
  dom.modelChip.title = lines.join('\n');
}

function capabilityBlockMessage(mode, hasImages) {
  const effective = getEffectiveSettings(mode);
  if (!effective.providerId || !effective.capabilities) return null;
  const name = effective.model || '当前模型';
  if (effective.capabilities.text === false) {
    return `当前模型「${name}」未声明 Text 能力，无法用于对话。请在 Provider 管理中启用 Text 或切换模型。`;
  }
  if (hasImages && effective.capabilities.vision === false) {
    return `当前模型「${name}」未声明 Vision（图片输入）能力，无法发送图片。请在 Provider 管理中启用 Vision 或切换模型。`;
  }
  if (mode === 'agent' && effective.capabilities.tool_calling === false) {
    return `当前模型「${name}」未声明 Tool Calling 能力，Agent 模式不可用。请切换模型或在 Provider 管理中启用 Tool Calling。`;
  }
  return null;
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
  const provider = getActiveProvider();
  if (provider) {
    state.settings.model = resolveProviderModel(provider, mode);
  }
  saveSettings();
  const conversation = getActiveConversation();
  if (conversation) {
    conversation.mode = mode;
    saveState();
  }
  syncModeToggle();
  updateTopbar();
  updateConfigTip();
}

/* --------------------------------------------------------------------------
   会话操作
   -------------------------------------------------------------------------- */

function startNewChat() {
  if (state.streaming) stopStreaming();
  clearPendingAttachments();
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
  clearPendingAttachments();
  state.activeId = id;
  state.stickToBottom = true;
  saveState();
  renderHistory();
  renderChat({ bottom: true });
  closeSidebar();
  loadConversationRuns(id);
  window.setTimeout(() => dom.input.focus({ preventScroll: true }), 60);
}

/** 从后端拉取当前会话的 Agent Run 历史（刷新后可恢复）。 */
async function loadConversationRuns(conversationId) {
  if (!conversationId) return;
  try {
    const runs = await fetchRuns(conversationId, 100);
    state.runs[conversationId] = runs;
    renderRunHistory();
  } catch (error) {
    console.warn('[WeCi] failed to load agent runs', error);
  }
}

/** 渲染 Agent Run 历史面板。 */
function renderRunHistory() {
  const panel = dom.runHistoryPanel;
  if (!panel) return;
  const conversation = getActiveConversation();
  const runs = conversation ? state.runs[conversation.id] || [] : [];
  if (!runs.length) {
    panel.hidden = true;
    panel.replaceChildren();
    return;
  }
  panel.hidden = false;
  const items = runs.map((run) => {
    const row = document.createElement('div');
    row.className = 'run-history-item';
    row.dataset.runId = run.id;
    const statusLabel = {
      pending: '等待中',
      running: '运行中',
      completed: '完成',
      failed: '失败',
      cancelled: '已取消',
    }[run.status] || run.status;
    const duration =
      typeof run.duration_ms === 'number'
        ? run.duration_ms >= 1000
          ? `${(run.duration_ms / 1000).toFixed(1)}s`
          : `${Math.round(run.duration_ms)}ms`
        : '—';
    const summary = run.result_summary || run.error || '（无输出）';
    row.innerHTML = `
      <div class="run-history-head">
        <span class="run-status run-status--${run.status}">${escapeHtml(statusLabel)}</span>
        <span class="run-time">${escapeHtml(formatHistoryTime(run.started_at))}</span>
        <span class="run-meta">${run.tool_count || 0} 个工具 · ${escapeHtml(duration)}</span>
        <button class="run-toggle" type="button" aria-label="展开">›</button>
      </div>
      <div class="run-history-summary">${escapeHtml(summary)}</div>
      <div class="run-history-detail" hidden></div>
    `;
    row.querySelector('.run-history-head').addEventListener('click', () => {
      const detail = row.querySelector('.run-history-detail');
      const expanded = !detail.hidden;
      detail.hidden = expanded;
      row.querySelector('.run-toggle').textContent = expanded ? '›' : '⌄';
      if (!expanded) renderRunDetail(detail, run);
    });
    return row;
  });
  panel.replaceChildren(...items);
}

async function renderRunDetail(container, run) {
  container.textContent = '加载中…';
  try {
    const full = await fetchRun(run.id);
    if (!full) {
      container.textContent = '无法加载详情。';
      return;
    }
    const steps = full.steps || full.current_step || 0;
    const tools = Array.isArray(full.tool_calls) ? full.tool_calls : [];
    const blocks = [`<div class="run-detail-meta">模型：${escapeHtml(full.model || '—')} · 步数：${steps}${full.error ? ` · 错误：${escapeHtml(full.error)}` : ''}</div>`];
    if (full.result) {
      blocks.push(`<div class="run-detail-result">${escapeHtml(full.result)}</div>`);
    }
    tools.forEach((tc) => {
      const statusText = { done: '成功', error: '失败', running: '运行中', cancelled: '已取消' }[tc.status] || tc.status;
      const args = tc.arguments == null ? '' : typeof tc.arguments === 'string' ? tc.arguments : JSON.stringify(tc.arguments);
      const permissions = Array.isArray(tc.permissions) ? tc.permissions : [];
      const permissionResult = {
        granted: '已授权',
        denied: '已拒绝',
        not_required: '无需授权',
      }[tc.permission_result] || '';
      const confirmation = {
        once: '仅本次允许',
        run: '本次 Run 允许',
        deny: '用户拒绝',
        timeout: '确认超时',
      }[tc.user_confirmation] || '';
      const permissionLine = permissions.length
        ? `<div class="run-tool-permission">权限：${permissions
            .map((item) => `<code>${escapeHtml(item)}</code>`)
            .join(' ')}${permissionResult ? ` · ${escapeHtml(permissionResult)}` : ''}${
            confirmation ? ` · ${escapeHtml(confirmation)}` : ''
          }</div>`
        : '';
      blocks.push(`
        <div class="run-tool run-tool--${tc.status}">
          <div class="run-tool-head"><b>${escapeHtml(tc.name)}</b> <span>${escapeHtml(statusText)}</span> · 步骤 ${tc.step ?? '—'}${typeof tc.duration_ms === 'number' ? ` · ${Math.round(tc.duration_ms)}ms` : ''}</div>
          ${permissionLine}
          ${args ? `<pre class="run-tool-args">${escapeHtml(args)}</pre>` : ''}
          ${tc.output ? `<pre class="run-tool-output">${escapeHtml(tc.output)}</pre>` : ''}
          ${tc.error ? `<div class="run-tool-error">${escapeHtml(tc.error)}</div>` : ''}
        </div>
      `);
    });
    container.innerHTML = blocks.join('');
  } catch (error) {
    container.textContent = '加载详情失败。';
  }
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
   附件（待发送）
   -------------------------------------------------------------------------- */

let attachmentBusy = false;

function renderPendingAttachments() {
  if (!dom.attachStrip) return;
  const images = state.pendingAttachments;
  const docs = state.pendingFiles;
  dom.attachStrip.replaceChildren();
  dom.attachStrip.hidden = images.length === 0 && docs.length === 0;

  if (!dom.attachStrip.hidden) {
    if (images.length) {
      const grid = createImageGrid(images, {
        variant: 'composer',
        onRemove: removePendingAttachment,
      });
      if (grid) dom.attachStrip.appendChild(grid);
    }

    if (docs.length) {
      const fileList = createFileList(docs, {
        onRemove: removePendingFile,
      });
      if (fileList) dom.attachStrip.appendChild(fileList);
    }

    const note = document.createElement('p');
    note.className = 'attach-strip__note';
    const counts = [];
    if (images.length) counts.push(`${images.length}/${MAX_IMAGES} 张图片`);
    if (docs.length) counts.push(`${docs.length}/${MAX_FILES} 个文件`);
    const total = [...images, ...docs].reduce(
      (sum, item) => sum + (item.size || 0),
      0,
    );
    note.textContent = `已添加 ${counts.join(' · ')}${
      total ? ` · ${formatBytes(total)}` : ''
    }`;
    dom.attachStrip.appendChild(note);
  }

  updateSendState();
}

function clearPendingAttachments() {
  if (!state.pendingAttachments.length && !state.pendingFiles.length) {
    renderPendingAttachments();
    return;
  }
  state.pendingAttachments = [];
  state.pendingFiles = [];
  renderPendingAttachments();
}

function removePendingAttachment(attachment) {
  state.pendingAttachments = state.pendingAttachments.filter(
    (item) => item.id !== attachment.id,
  );
  renderPendingAttachments();
}

function removePendingFile(file) {
  state.pendingFiles = state.pendingFiles.filter((item) => item.id !== file.id);
  renderPendingAttachments();
}

async function addImageFiles(files) {
  const room = MAX_IMAGES - state.pendingAttachments.length;
  if (room <= 0) {
    toast(`一条消息最多添加 ${MAX_IMAGES} 张图片。`, 'error');
    return;
  }
  const selected = files.slice(0, room);
  if (files.length > room) {
    toast(
      `最多 ${MAX_IMAGES} 张图片，已忽略多出的 ${files.length - room} 张。`,
      'error',
      4000,
    );
  }

  for (const file of selected) {
    try {
      state.pendingAttachments.push(await createAttachment(file));
      renderPendingAttachments();
    } catch (error) {
      toast(
        error instanceof AttachmentError
          ? error.message
          : '无法处理这张图片，请换一张。',
        'error',
        4200,
      );
    }
  }
}

async function addDocuments(files) {
  const room = MAX_FILES - state.pendingFiles.length;
  if (room <= 0) {
    toast(`一条消息最多添加 ${MAX_FILES} 个文件。`, 'error');
    return;
  }
  const selected = files.slice(0, room);
  if (files.length > room) {
    toast(
      `最多 ${MAX_FILES} 个文件，已忽略多出的 ${files.length - room} 个。`,
      'error',
      4000,
    );
  }

  for (const file of selected) {
    try {
      state.pendingFiles.push(await createFileAttachment(file));
      renderPendingAttachments();
    } catch (error) {
      toast(
        error instanceof AttachmentError
          ? error.message
          : '无法读取这个文件，请换一个。',
        'error',
        4200,
      );
    }
  }
}

/** 统一入口：按文件类型拆分为图片与文档。 */
async function addFiles(fileList) {
  const files = Array.from(fileList || []).filter(Boolean);
  if (!files.length) return;

  const images = files.filter((file) => file.type.startsWith('image/'));
  const docs = files.filter((file) => !file.type.startsWith('image/'));

  attachmentBusy = true;
  updateSendState();
  try {
    if (docs.length) await addDocuments(docs);
    if (images.length) await addImageFiles(images);
  } finally {
    attachmentBusy = false;
    renderPendingAttachments();
  }
}

/* --------------------------------------------------------------------------
   发送 / 重新生成 / 编辑重发
   -------------------------------------------------------------------------- */

function buildApiMessages(conversation, excludeId) {
  return conversation.messages
    .filter((message) => message.id !== excludeId)
    .filter((message) => {
      const hasText = Boolean(message.content && message.content.trim());
      const hasImages = Array.isArray(message.attachments) && message.attachments.length;
      const hasFiles = Array.isArray(message.files) && message.files.length;
      return hasText || hasImages || hasFiles;
    })
    .filter((message) => !(message.role === 'assistant' && message.error))
    .map((message) => {
      const payload = { role: message.role, content: message.content || '' };
      if (Array.isArray(message.attachments) && message.attachments.length) {
        payload.attachments = message.attachments.map(toApiAttachment);
      }
      if (Array.isArray(message.files) && message.files.length) {
        payload.files = message.files.map(toApiFile);
      }
      return payload;
    });
}

/** 会话没有文字时，用附件类型生成默认标题。 */
function makeAttachmentTitle(text, images, files) {
  if (text) return makeTitle(text);
  if (images.length) return '图片对话';
  if (files.length) return makeTitle(files[0].name);
  return '新对话';
}

async function handleSend() {
  if (state.streaming) return;
  const text = dom.input.value.trim();
  const attachments = state.pendingAttachments.slice();
  const files = state.pendingFiles.slice();
  if (!text && !attachments.length && !files.length) return;

  if (!canChat()) {
    openSettings();
    toast(
      state.providers.length
        ? '请先启用 Provider 并配置可用模型与 API Key'
        : '请先完成模型配置：Endpoint、Model 与 API Key',
      'error',
      4200,
    );
    return;
  }

  const sendMode = getActiveConversation()?.mode || state.settings.mode || 'chat';
  const capabilityError = capabilityBlockMessage(sendMode, attachments.length > 0);
  if (capabilityError) {
    toast(capabilityError, 'error', 5200);
    return;
  }

  const fallbackTitle = makeAttachmentTitle(text, attachments, files);
  const conversation =
    getActiveConversation() || createConversation(fallbackTitle);
  const now = new Date().toISOString();

  if (!conversation.messages.some((message) => message.role === 'user')) {
    conversation.title = fallbackTitle;
  }

  conversation.messages.push({
    id: uid(),
    role: 'user',
    content: text,
    createdAt: now,
    ...(attachments.length ? { attachments } : {}),
    ...(files.length ? { files } : {}),
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
  clearPendingAttachments();
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
  const lastUser = [...conversation.messages]
    .reverse()
    .find((message) => message.role === 'user');
  const hasImages = Boolean(
    lastUser && Array.isArray(lastUser.attachments) && lastUser.attachments.length,
  );
  const capabilityError = capabilityBlockMessage(
    conversation.mode || state.settings.mode || 'chat',
    hasImages,
  );
  if (capabilityError) {
    toast(capabilityError, 'error', 5200);
    return;
  }
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
  const capabilityError = capabilityBlockMessage(
    conversation.mode || state.settings.mode || 'chat',
    Boolean(message.attachments && message.attachments.length),
  );
  if (capabilityError) {
    toast(capabilityError, 'error', 5200);
    renderChat();
    return;
  }

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
  if (dom.attachBtn) dom.attachBtn.disabled = active;
  if (dom.fileBtn) dom.fileBtn.disabled = active;
  if (dom.fileInput) dom.fileInput.disabled = active;
  if (dom.docInput) dom.docInput.disabled = active;

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
  const runId = state.currentRunId;
  state.controller = null;
  state.currentRunId = null;
  // 先通知后端取消 Run，再断开 SSE；后端 Runtime 收到取消信号后会停止。
  if (runId) cancelRun(runId).catch(() => {});
  if (controller) controller.abort();
  setStreaming(false);
}

async function streamReply(conversation, assistant) {
  const mode = conversation.mode || state.settings.mode || 'chat';
  const effective = getEffectiveSettings(mode);
  const messages = buildApiMessages(conversation, assistant.id);
  if (!messages.length) {
    assistant.error = '没有可发送的消息。';
    renderChat();
    return;
  }

  assistant.toolCalls = [];
  assistant.permissionRequests = [];
  if (mode === 'agent') {
    assistant.agentState = 'running';
    assistant.agentStep = 0;
    assistant.agentMaxSteps = 0;
  }

  const token = ++state.streamToken;
  const controller = new AbortController();
  state.controller = controller;
  state.streamingMessage = assistant;
  state.currentRunId = null;
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

  /** 请求中断 / 异常时，把仍在运行的工具标记为已中断，避免 UI 卡在“执行中”。 */
  const settleRunningToolCalls = (reason) => {
    if (!Array.isArray(assistant.toolCalls)) return;
    assistant.toolCalls.forEach((tc) => {
      if (tc.status === 'running') {
        tc.status = 'interrupted';
        tc.error = tc.error || reason;
      }
    });
  };

  try {
    const stream = streamChat({
      messages,
      model: effective.model,
      apiBase: effective.apiBase,
      // Provider 模式：apiKey 为空且不发送浏览器 Key
      apiKey: effective.apiKey,
      providerId: effective.providerId,
      temperature: effective.temperature,
      maxTokens: effective.maxTokens,
      mode,
      userId: getUserId(),
      conversationId: conversation.id,
      signal: controller.signal,
    });

    for await (const event of stream) {
      if (token !== state.streamToken) break; // 已有更新的请求接管

      if (event.type === 'start') {
        if (typeof event.run_id === 'string') {
          state.currentRunId = event.run_id;
          assistant.runId = event.run_id;
        }
      } else if (event.type === 'delta' && typeof event.text === 'string' && event.text) {
        state.agentStatus = null;
        assistant.content += event.text;
        scheduleAssistantRender(assistant);
        scrollToBottomIfStuck();
        scheduleSave();
      } else if (event.type === 'thinking') {
        if (typeof event.step === 'number') assistant.agentStep = event.step;
        if (typeof event.max_steps === 'number') {
          assistant.agentMaxSteps = event.max_steps;
        }
        if (mode === 'agent') assistant.agentState = 'running';
        state.agentStatus = `Agent 正在思考…（第 ${event.step} 步）`;
        dom.composerHint.textContent = state.agentStatus;
        updateAssistantNode(assistant, Boolean(assistant.content));
        scrollToBottomIfStuck();
      } else if (event.type === 'tool_call') {
        const tc = {
          id: event.id,
          name: event.name,
          status: event.status,
          step: typeof event.step === 'number' ? event.step : null,
          durationMs: typeof event.duration_ms === 'number' ? event.duration_ms : null,
          output: typeof event.output === 'string' ? event.output : null,
          error: event.error || null,
        };
        // running 事件带参数、done/error 事件带结果；不要用空值覆盖已有内容
        if (event.arguments !== undefined) tc.arguments = event.arguments;
        upsertToolCall(tc);
        if (typeof event.step === 'number') assistant.agentStep = event.step;
        if (typeof event.max_steps === 'number') {
          assistant.agentMaxSteps = event.max_steps;
        }
        if (mode === 'agent') assistant.agentState = 'running';
        if (event.status === 'running') {
          state.agentStatus = `正在调用 ${event.name}…`;
        } else {
          state.agentStatus = null;
        }
        dom.composerHint.textContent = state.agentStatus || '正在生成…';
        updateAssistantNode(assistant, Boolean(assistant.content));
        scrollToBottomIfStuck();
        scheduleSave();
      } else if (event.type === 'permission_request') {
        const request = {
          id: event.id,
          tool: event.tool || '',
          permission: event.permission || '',
          title: event.title || '',
          detail: event.detail || '',
          risk: event.risk || 'high',
          status: 'pending',
        };
        if (!Array.isArray(assistant.permissionRequests)) {
          assistant.permissionRequests = [];
        }
        assistant.permissionRequests.push(request);
        if (mode === 'agent') assistant.agentState = 'running';
        state.agentStatus = `等待你确认 ${request.tool || '工具'} 的权限…`;
        dom.composerHint.textContent = state.agentStatus;
        updateAssistantNode(assistant, Boolean(assistant.content));
        scrollToBottomIfStuck();
      } else if (event.type === 'max_steps') {
        assistant.agentState = 'max_steps';
        if (typeof event.steps === 'number') assistant.agentStep = event.steps;
        if (typeof event.max_steps === 'number') {
          assistant.agentMaxSteps = event.max_steps;
        }
        const note = event.message || '已达到最大执行步数，Agent 已停止。';
        assistant.content = (assistant.content || '') + `\n\n> ${note}`;
        scheduleAssistantRender(assistant);
      } else if (event.type === 'done') {
        if (mode === 'agent') {
          assistant.agentState = 'done';
          if (typeof event.steps === 'number') assistant.agentStep = event.steps;
        }
      } else if (event.type === 'cancelled') {
        // 后端确认 Run 已取消：标记为停止，不再当作失败
        assistant.stopped = true;
        if (mode === 'agent') assistant.agentState = 'stopped';
        settleRunningToolCalls('执行已取消。');
        break;
      } else if (event.type === 'error') {
        throw new Error(event.message || '模型服务返回错误。');
      }
    }
  } catch (error) {
    if (pageUnloading) {
      // 页面正在刷新 / 关闭：统一按“中断”处理，不写成连接失败
      assistant.stopped = true;
      assistant.interrupted = true;
      if (mode === 'agent') assistant.agentState = 'stopped';
      settleRunningToolCalls('页面刷新或网络中断，工具执行状态未知。');
    } else if (error && error.name === 'AbortError') {
      assistant.stopped = true;
      if (mode === 'agent') assistant.agentState = 'stopped';
      settleRunningToolCalls('执行已停止。');
    } else if (error instanceof TypeError) {
      assistant.error =
        '无法连接本地后端服务，请确认 FastAPI 已启动（默认 http://127.0.0.1:8000）。';
      if (mode === 'agent') assistant.agentState = 'error';
      settleRunningToolCalls('连接中断，工具执行状态未知。');
    } else {
      assistant.error =
        error && error.message ? error.message : '生成失败，请稍后重试。';
      if (mode === 'agent') assistant.agentState = 'error';
      settleRunningToolCalls('执行中断，工具状态未知。');
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
    // 流已结束：未被用户处理的权限请求视为已超时
    if (Array.isArray(assistant.permissionRequests)) {
      assistant.permissionRequests.forEach((request) => {
        if (request.status === 'pending') request.status = 'timeout';
      });
    }
    saveState();

    if (token === state.streamToken) {
      state.controller = null;
      state.streamingMessage = null;
      state.currentRunId = null;
      setStreaming(false);
      renderHistory();
      renderChat({ bottom: state.stickToBottom });
      // 刷新 Run 历史（本次执行已落盘到后端）
      if (mode === 'agent') loadConversationRuns(conversation.id);
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
   Provider / Model 设置联动
   -------------------------------------------------------------------------- */

function setProviderTestResult(text, kind) {
  dom.providerTestResult.textContent = text || '';
  dom.providerTestResult.dataset.state = kind || '';
}

function providerForSettingsForm() {
  if (!state.providers.length) return null;
  const id = dom.settingProvider.value;
  if (!id) return state.providers.find((item) => item.is_default) || null;
  return state.providers.find((item) => item.id === id) || null;
}

function syncSettingsProviderSelect(preferredId) {
  const options = [];
  if (!state.providers.length) {
    options.push({ value: '', label: '未配置 Provider（使用兼容模式）' });
  } else {
    options.push({ value: '', label: '默认 Provider（自动）' });
    state.providers.forEach((provider) => {
      const suffix = `${provider.is_default ? ' · 默认' : ''}${provider.enabled ? '' : ' · 已禁用'}`;
      options.push({ value: provider.id, label: `${provider.name}${suffix}` });
    });
  }

  dom.settingProvider.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      return option;
    }),
  );

  const valid = state.providers.some((item) => item.id === preferredId);
  dom.settingProvider.value = valid ? preferredId : '';
  dom.settingProvider.disabled = state.providers.length === 0;
}

function syncSettingsModelSelect(preferredModel) {
  const provider = providerForSettingsForm();
  if (!provider) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '使用兼容模式中的 Model';
    dom.settingProviderModel.replaceChildren(option);
    dom.settingProviderModel.disabled = true;
    dom.settingModelHint.textContent =
      '还没有 Provider。可在 Provider 管理中创建，或使用下方兼容模式。';
    dom.testProviderBtn.hidden = true;
    renderSettingCapabilities(null, '');
    return;
  }

  const models = Array.isArray(provider.models) ? provider.models : [];
  const options = models.length
    ? models.map((model) => ({
        value: model.id,
        label: model.display_name
          ? `${model.display_name}（${model.id}）`
          : model.id,
      }))
    : [{ value: '', label: '尚未配置模型' }];

  dom.settingProviderModel.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      return option;
    }),
  );

  let target = '';
  if (preferredModel && models.some((model) => model.id === preferredModel)) {
    target = preferredModel;
  } else {
    target = resolveProviderModel(provider, state.settings.mode || 'chat');
  }
  dom.settingProviderModel.value = target;
  dom.settingProviderModel.disabled = !models.length;
  dom.testProviderBtn.hidden = false;

  const refs = [];
  if (provider.chat_model) refs.push(`Chat：${provider.chat_model}`);
  if (provider.agent_model) refs.push(`Agent：${provider.agent_model}`);
  dom.settingModelHint.textContent = models.length
    ? `当前 Provider 默认模型${refs.length ? `（${refs.join(' · ')}）` : ''}。可在 Provider 管理中调整。`
    : '该 Provider 还没有模型，请先在 Provider 管理中添加。';

  renderSettingCapabilities(provider, target);
}

function renderSettingCapabilities(provider, model) {
  const capabilities = getModelCapabilities(provider, model);
  if (!capabilities) {
    dom.settingCapabilities.hidden = true;
    dom.settingCapabilities.replaceChildren();
    return;
  }
  dom.settingCapabilities.hidden = false;
  dom.settingCapabilities.replaceChildren(
    ...Object.keys(CAPABILITY_LABELS).map((key) => {
      const badge = document.createElement('span');
      badge.className = `cap-badge${capabilities[key] === false ? ' cap-badge--off' : ' cap-badge--on'}`;
      badge.textContent = `${capabilities[key] === false ? '✗' : '✓'} ${CAPABILITY_LABELS[key]}`;
      return badge;
    }),
  );
}

let providerTestBusy = false;

async function handleTestProvider() {
  if (providerTestBusy) return;
  const provider = providerForSettingsForm();
  if (!provider) {
    setProviderTestResult('请先选择或创建 Provider。', 'error');
    return;
  }

  providerTestBusy = true;
  dom.testProviderBtn.disabled = true;
  dom.testProviderBtn.classList.add('is-loading');
  setProviderTestResult('正在测试…', 'pending');

  try {
    const result = await testProvider(provider.id, {
      model: dom.settingProviderModel.value || undefined,
    });
    if (result && result.ok) {
      const parts = ['Provider 连接成功'];
      if (typeof result.latency_ms === 'number') parts.push(`${result.latency_ms}ms`);
      if (Array.isArray(result.models)) {
        parts.push(`发现 ${result.models.length} 个模型`);
      }
      if (result.model) parts.push(`模型 ${result.model}`);
      setProviderTestResult(parts.join(' · '), 'ok');
      if (Array.isArray(result.models) && result.models.length) {
        renderModelDatalist(result.models);
      }
    } else {
      setProviderTestResult((result && result.message) || 'Provider 测试失败。', 'error');
    }
  } catch (error) {
    setProviderTestResult(
      error instanceof TypeError
        ? '无法连接本地后端服务，请确认 FastAPI 已启动。'
        : error.message || '测试失败。',
      'error',
    );
  } finally {
    providerTestBusy = false;
    dom.testProviderBtn.disabled = false;
    dom.testProviderBtn.classList.remove('is-loading');
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

/* --------------------------------------------------------------------------
   长期记忆（Memory）
   -------------------------------------------------------------------------- */

let memoryOpener = null;

const MEMORY_SCOPE_LABELS = {
  user: '用户',
  workspace: 'Workspace',
  conversation: '当前会话',
};

function memoryContext() {
  const conversation = getActiveConversation();
  return {
    userId: getUserId(),
    workspaceId: '',
    conversationId: conversation ? conversation.id : '',
  };
}

function updateMemoryScopeHint() {
  const conversation = getActiveConversation();
  const hasConversation = Boolean(conversation);
  if (dom.memoryScope) {
    const option = dom.memoryScope.querySelector('option[value="conversation"]');
    if (option) option.disabled = !hasConversation;
    if (!hasConversation && dom.memoryScope.value === 'conversation') {
      dom.memoryScope.value = 'user';
    }
  }
  if (dom.memoryScopeHint) {
    dom.memoryScopeHint.textContent = hasConversation
      ? '「当前会话」记忆仅在这个会话中生效。'
      : '当前还没有会话：只能保存到「用户」范围。';
  }
}

async function loadMemories() {
  const list = dom.memoryList;
  if (!list) return;
  list.innerHTML = '<p class="plugins__empty">正在加载记忆…</p>';
  updateMemoryScopeHint();
  try {
    const memories = await fetchMemories(memoryContext());
    renderMemories(memories);
  } catch (error) {
    list.innerHTML = `<p class="plugins__empty plugins__empty--error">加载记忆失败：${escapeHtml(error.message || error)}</p>`;
  }
}

function renderMemories(memories) {
  const list = dom.memoryList;
  if (!list) return;
  if (!memories.length) {
    list.innerHTML = '<p class="plugins__empty">还没有记忆。在 Agent 模式中对 WeCi 说「记住……」即可保存，或在上方手动添加。</p>';
    return;
  }

  list.innerHTML = '';
  for (const entry of memories) {
    const card = document.createElement('div');
    card.className = 'memory-card';
    card.dataset.id = entry.id;
    card.innerHTML = `
      <div class="memory-card__head">
        <div class="memory-card__title">
          <code class="memory-card__key">${escapeHtml(entry.key)}</code>
          <span class="memory-card__scope">${escapeHtml(MEMORY_SCOPE_LABELS[entry.scope] || entry.scope)}</span>
        </div>
        <div class="plugin-card__actions">
          <button class="ghost-btn" data-memory-action="edit" type="button">修改</button>
          <button class="ghost-btn ghost-btn--danger" data-memory-action="delete" type="button">删除</button>
        </div>
      </div>
      <div class="memory-card__body">
        <p class="memory-card__content">${escapeHtml(entry.content)}</p>
        <p class="memory-card__meta">更新于 ${escapeHtml(formatHistoryTime(entry.updated_at))}</p>
      </div>
    `;
    const editBtn = card.querySelector('[data-memory-action="edit"]');
    const deleteBtn = card.querySelector('[data-memory-action="delete"]');
    editBtn.addEventListener('click', () => startMemoryEdit(entry, card));
    deleteBtn.addEventListener('click', () => removeMemory(entry, card));
    list.appendChild(card);
  }
}

function startMemoryEdit(entry, card) {
  if (card.classList.contains('is-editing')) return;
  const body = card.querySelector('.memory-card__body');
  if (!body) return;
  card.classList.add('is-editing');
  body.innerHTML = `
    <label class="memory-card__field">
      <span>标识（key）</span>
      <input class="memory-card__input" data-field="key" maxlength="64" value="${escapeHtml(entry.key)}">
    </label>
    <label class="memory-card__field">
      <span>内容</span>
      <input class="memory-card__input" data-field="content" maxlength="500" value="${escapeHtml(entry.content)}">
    </label>
    <div class="memory-card__edit-actions">
      <button class="ghost-btn" data-memory-action="save" type="button">保存</button>
      <button class="ghost-btn" data-memory-action="cancel" type="button">取消</button>
    </div>
  `;
  const keyInput = body.querySelector('[data-field="key"]');
  const contentInput = body.querySelector('[data-field="content"]');
  keyInput.focus();

  body.querySelector('[data-memory-action="cancel"]').addEventListener('click', () => {
    loadMemories();
  });
  body.querySelector('[data-memory-action="save"]').addEventListener('click', async () => {
    const key = keyInput.value.trim();
    const content = contentInput.value.trim();
    if (!key || !content) {
      toast('标识和内容都不能为空。', 'error');
      return;
    }
    try {
      await updateMemory(entry.id, { key, content });
      toast('记忆已更新', 'success', 2000);
      await loadMemories();
    } catch (error) {
      toast(`更新失败：${error.message || error}`, 'error');
    }
  });
}

async function removeMemory(entry, card) {
  if (!confirm(`确定删除记忆「${entry.key}」吗？`)) return;
  try {
    await deleteMemory(entry.id);
    card.remove();
    toast('记忆已删除', 'success', 2000);
    if (dom.memoryList && !dom.memoryList.querySelector('.memory-card')) {
      dom.memoryList.innerHTML = '<p class="plugins__empty">还没有记忆。在 Agent 模式中对 WeCi 说「记住……」即可保存，或在上方手动添加。</p>';
    }
  } catch (error) {
    toast(`删除失败：${error.message || error}`, 'error');
  }
}

async function handleMemorySubmit(event) {
  event.preventDefault();
  const key = dom.memoryKey.value.trim();
  const content = dom.memoryContent.value.trim();
  const scope = dom.memoryScope.value;
  if (!key || !content) {
    toast('标识和内容都不能为空。', 'error');
    return;
  }
  const context = memoryContext();
  if (scope === 'conversation' && !context.conversationId) {
    toast('当前没有可用的会话，请先开始一个对话。', 'error');
    return;
  }
  try {
    await createMemory({ key, content, scope, ...context });
    dom.memoryKey.value = '';
    dom.memoryContent.value = '';
    toast('记忆已保存', 'success', 2000);
    await loadMemories();
  } catch (error) {
    toast(`保存失败：${error.message || error}`, 'error');
  }
}

function openMemoryPage() {
  closeSettings();
  closePluginsPage();
  closeMcpPage();
  closeProvidersPage();
  closePermissionsPage();
  closeSidebar();
  toggleModelMenu(false);
  dom.chatScroll.hidden = true;
  dom.composerForm.parentElement.hidden = true;
  dom.memoryPage.hidden = false;
  memoryOpener = document.activeElement;
  loadMemories();
  window.requestAnimationFrame(() => {
    dom.memoryBackBtn.focus({ preventScroll: true });
  });
}

function closeMemoryPage() {
  if (dom.memoryPage.hidden) return;
  dom.memoryPage.hidden = true;
  dom.chatScroll.hidden = false;
  dom.composerForm.parentElement.hidden = false;
  if (memoryOpener && typeof memoryOpener.focus === 'function') {
    memoryOpener.focus({ preventScroll: true });
  }
}

/* --------------------------------------------------------------------------
   MCP Server
   -------------------------------------------------------------------------- */

let mcpOpener = null;
let mcpServers = [];

const MCP_STATUS_LABELS = {
  ready: '已连接',
  connecting: '连接中',
  error: '连接失败',
  stopped: '已断开',
  disabled: '已禁用',
};

function parseMcpArgs(value) {
  return String(value || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function parseMcpEnv(value) {
  const env = {};
  for (const line of String(value || '').split('\n')) {
    const text = line.trim();
    if (!text) continue;
    const index = text.indexOf('=');
    if (index <= 0) throw new Error(`环境变量格式应为 KEY=VALUE：${text}`);
    const key = text.slice(0, index).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      throw new Error(`无效的环境变量名：${key}`);
    }
    env[key] = text.slice(index + 1).trim();
  }
  return env;
}

function formatMcpEnv(env) {
  return Object.entries(env || {})
    .map(([key, value]) => `${key}=${value}`)
    .join('\n');
}

function resetMcpForm() {
  if (!dom.mcpForm) return;
  dom.mcpForm.reset();
  dom.mcpEditId.value = '';
  dom.mcpSubmitBtn.textContent = '添加并连接';
  dom.mcpCancelEditBtn.hidden = true;
  dom.mcpFormHint.textContent = '保存后会立即尝试连接。';
}

async function loadMcpServers() {
  const list = dom.mcpList;
  if (!list) return;
  list.innerHTML = '<p class="plugins__empty">正在加载 MCP Server…</p>';
  try {
    mcpServers = await fetchMcpServers();
    renderMcpServers(mcpServers);
  } catch (error) {
    list.innerHTML = `<p class="plugins__empty plugins__empty--error">加载 MCP Server 失败：${escapeHtml(error.message || error)}</p>`;
  }
}

function renderMcpServers(servers) {
  const list = dom.mcpList;
  if (!list) return;
  if (!servers.length) {
    list.innerHTML = '<p class="plugins__empty">还没有配置 MCP Server。填写名称与命令后添加，WeCi 会自动连接并获取工具。</p>';
    return;
  }

  list.innerHTML = '';
  for (const server of servers) {
    const card = document.createElement('div');
    card.className = 'plugin-card';
    if (server.status === 'error') card.classList.add('plugin-card--failed');
    if (server.status === 'disabled') card.classList.add('plugin-card--disabled');

    const statusLabel = MCP_STATUS_LABELS[server.status] || server.status;
    const toolsHtml = (server.tools && server.tools.length)
      ? server.tools
        .map((tool) => `<code class="plugin-card__tool" title="${escapeHtml(tool.original_name || '')}">${escapeHtml(tool.name)}</code>`)
        .join(' ')
      : '<span class="plugins__hint">无</span>';
    const commandLine = [server.command, ...(server.args || [])].join(' ');
    const errorHtml = server.error
      ? `<div class="plugin-card__error"><svg class="icon" viewBox="0 0 24 24"><use href="#i-alert"/></svg> ${escapeHtml(server.error)}</div>`
      : '';

    const connectBtn = !server.enabled
      ? ''
      : server.status === 'ready'
        ? `<button class="ghost-btn" data-mcp-action="disconnect" data-id="${escapeHtml(server.id)}" type="button">断开</button>`
        : `<button class="ghost-btn" data-mcp-action="connect" data-id="${escapeHtml(server.id)}" type="button">连接</button>`;
    const toggleBtn = server.enabled
      ? `<button class="ghost-btn ghost-btn--danger" data-mcp-action="disable" data-id="${escapeHtml(server.id)}" type="button">禁用</button>`
      : `<button class="ghost-btn" data-mcp-action="enable" data-id="${escapeHtml(server.id)}" type="button">启用</button>`;

    card.innerHTML = `
      <div class="plugin-card__head">
        <div class="plugin-card__title">
          <strong>${escapeHtml(server.name)}</strong>
          <span class="plugin-card__version">${escapeHtml(server.id)}</span>
        </div>
        <span class="plugin-card__status plugin-card__status--${escapeHtml(server.status)}">${statusLabel}</span>
      </div>
      <p class="plugin-card__desc mcp-card__command"><code>${escapeHtml(commandLine)}</code></p>
      <div class="plugin-card__meta">
        <div><span class="plugins__hint">工具：</span>${toolsHtml}</div>
      </div>
      ${errorHtml}
      <div class="plugin-card__actions">
        <button class="ghost-btn" data-mcp-action="edit" data-id="${escapeHtml(server.id)}" type="button">编辑</button>
        ${connectBtn}
        ${toggleBtn}
        <button class="ghost-btn ghost-btn--danger" data-mcp-action="remove" data-id="${escapeHtml(server.id)}" type="button">删除</button>
      </div>
    `;
    list.appendChild(card);
  }

  list.querySelectorAll('[data-mcp-action]').forEach((btn) => {
    btn.addEventListener('click', () => handleMcpAction(btn.dataset.mcpAction, btn.dataset.id));
  });
}

function startMcpEdit(server) {
  if (!server) return;
  dom.mcpEditId.value = server.id;
  dom.mcpName.value = server.name;
  dom.mcpCommand.value = server.command;
  dom.mcpArgs.value = (server.args || []).join('\n');
  dom.mcpEnv.value = formatMcpEnv(server.env);
  dom.mcpSubmitBtn.textContent = '保存修改';
  dom.mcpCancelEditBtn.hidden = false;
  dom.mcpFormHint.textContent = '修改命令、参数或环境变量后会自动重连。';
  dom.mcpName.focus();
}

async function handleMcpSubmit(event) {
  event.preventDefault();

  let args;
  let env;
  try {
    args = parseMcpArgs(dom.mcpArgs.value);
    env = parseMcpEnv(dom.mcpEnv.value);
  } catch (error) {
    toast(error.message || String(error), 'error');
    return;
  }

  const name = dom.mcpName.value.trim();
  const command = dom.mcpCommand.value.trim();
  if (!name || !command) {
    toast('名称和命令不能为空', 'error');
    return;
  }

  const editId = dom.mcpEditId.value;
  dom.mcpSubmitBtn.disabled = true;
  try {
    const payload = { name, command, args, env };
    const result = editId
      ? await updateMcpServer(editId, payload)
      : await createMcpServer(payload);
    const server = result.server || {};
    if (server.status === 'error') {
      toast(`保存成功，但连接失败：${server.error || '未知错误'}`, 'error');
    } else {
      toast(editId ? '已保存并重连' : '已添加并连接', 'success');
    }
    resetMcpForm();
    await loadMcpServers();
  } catch (error) {
    toast(error.message || String(error), 'error');
  } finally {
    dom.mcpSubmitBtn.disabled = false;
  }
}

async function handleMcpAction(action, serverId) {
  try {
    if (action === 'edit') {
      startMcpEdit(mcpServers.find((item) => item.id === serverId));
      return;
    }
    if (action === 'remove') {
      if (!confirm(`确定删除 MCP Server「${serverId}」吗？其工具将立即不可用。`)) return;
      await deleteMcpServer(serverId);
      toast('已删除 MCP Server', 'success');
      if (dom.mcpEditId.value === serverId) resetMcpForm();
    } else if (action === 'connect') {
      const result = await connectMcpServer(serverId);
      if (result.ok) toast('已连接 MCP Server', 'success');
      else toast(`连接失败：${result.server?.error || '未知错误'}`, 'error');
    } else if (action === 'disconnect') {
      await disconnectMcpServer(serverId);
      toast('已断开 MCP Server');
    } else if (action === 'enable') {
      const result = await updateMcpServer(serverId, { enabled: true });
      if (result.server?.status === 'error') {
        toast(`已启用，但连接失败：${result.server.error || '未知错误'}`, 'error');
      } else {
        toast('已启用 MCP Server', 'success');
      }
    } else if (action === 'disable') {
      if (!confirm(`确定禁用「${serverId}」吗？禁用后其工具将不可用。`)) return;
      await updateMcpServer(serverId, { enabled: false });
      toast('已禁用 MCP Server');
    }
    await loadMcpServers();
  } catch (error) {
    toast(error.message || String(error), 'error');
  }
}

function openMcpPage() {
  closeSettings();
  closePluginsPage();
  closeMemoryPage();
  closeProvidersPage();
  closePermissionsPage();
  closeSidebar();
  toggleModelMenu(false);
  dom.chatScroll.hidden = true;
  dom.composerForm.parentElement.hidden = true;
  dom.mcpPage.hidden = false;
  mcpOpener = document.activeElement;
  resetMcpForm();
  loadMcpServers();
  window.requestAnimationFrame(() => {
    dom.mcpBackBtn.focus({ preventScroll: true });
  });
}

function closeMcpPage() {
  if (dom.mcpPage.hidden) return;
  dom.mcpPage.hidden = true;
  dom.chatScroll.hidden = false;
  dom.composerForm.parentElement.hidden = false;
  if (mcpOpener && typeof mcpOpener.focus === 'function') {
    mcpOpener.focus({ preventScroll: true });
  }
}

/* --------------------------------------------------------------------------
   Provider 管理页
   -------------------------------------------------------------------------- */

let providersOpener = null;
let providerBusy = false;

function getProviderById(providerId) {
  return state.providers.find((item) => item.id === providerId) || null;
}

function providerCapabilityChips(model) {
  const caps = (model && model.capabilities) || {};
  return ['text', 'vision', 'tool_calling', 'streaming']
    .map((key) => {
      const on = caps[key] !== false;
      return `<span class="cap-badge${on ? ' cap-badge--on' : ' cap-badge--off'}">${on ? '✓' : '✗'} ${CAPABILITY_LABELS[key]}</span>`;
    })
    .join(' ');
}

function modelRowHtml(model) {
  const caps = providerCapabilityChips(model);
  const meta = [];
  if (typeof model.context_window === 'number') {
    meta.push(`Context ${model.context_window}`);
  }
  if (typeof model.max_tokens === 'number') {
    meta.push(`Max Tokens ${model.max_tokens}`);
  }
  return `
    <div class="provider-model" data-model-id="${escapeHtml(model.id)}">
      <div class="provider-model__main">
        <div class="provider-model__title">
          <strong>${escapeHtml(model.display_name || model.id)}</strong>
          ${model.display_name ? `<code>${escapeHtml(model.id)}</code>` : ''}
        </div>
        <div class="provider-model__caps">${caps}</div>
        ${meta.length ? `<div class="provider-model__meta">${escapeHtml(meta.join(' · '))}</div>` : ''}
      </div>
      <div class="provider-model__actions">
        <button class="ghost-btn" data-model-action="test" type="button">测试</button>
        <button class="ghost-btn" data-model-action="edit" type="button">编辑</button>
        <button class="ghost-btn ghost-btn--danger" data-model-action="delete" type="button">删除</button>
      </div>
    </div>
  `;
}

function providerCardHtml(provider) {
  const models = Array.isArray(provider.models) ? provider.models : [];
  const statusLabel = provider.enabled ? '已启用' : '已禁用';
  const statusClass = provider.enabled ? 'loaded' : 'disabled';
  const keyLabel = provider.has_api_key ? '已保存' : '未保存';
  const refs = [
    `默认：${provider.default_model || '未设置'}`,
    `Chat：${provider.chat_model || '未设置'}`,
    `Agent：${provider.agent_model || '未设置'}`,
  ].join(' · ');

  return `
    <div class="plugin-card provider-card${provider.enabled ? '' : ' plugin-card--disabled'}" data-provider-id="${escapeHtml(provider.id)}">
      <div class="plugin-card__head">
        <div class="plugin-card__title">
          <strong>${escapeHtml(provider.name)}</strong>
          <span class="plugin-card__version">${escapeHtml(provider.id)}</span>
          ${provider.is_default ? '<span class="plugin-card__badge plugin-card__badge--official">默认</span>' : ''}
          <span class="plugin-card__badge">Key ${keyLabel}</span>
        </div>
        <span class="plugin-card__status plugin-card__status--${statusClass}">${statusLabel}</span>
      </div>
      ${provider.description ? `<p class="plugin-card__desc">${escapeHtml(provider.description)}</p>` : ''}
      <div class="plugin-card__meta provider-card__meta">
        <div><span class="plugins__hint">Endpoint：</span><code class="provider-card__endpoint">${escapeHtml(provider.api_base)}</code></div>
        <div><span class="plugins__hint">模型：</span>${escapeHtml(refs)}</div>
      </div>
      <div class="provider-models">
        <div class="provider-models__head">模型（${models.length}）</div>
        ${
          models.length
            ? models.map((model) => modelRowHtml(model)).join('')
            : '<p class="plugins__hint provider-models__empty">该 Provider 还没有模型，请通过下方表单添加。</p>'
        }
      </div>
      <div class="plugin-card__actions">
        <button class="ghost-btn" data-provider-action="add-model" type="button">添加模型</button>
        <button class="ghost-btn" data-provider-action="test" type="button">测试</button>
        <button class="ghost-btn" data-provider-action="edit" type="button">编辑</button>
        <button class="ghost-btn" data-provider-action="toggle" type="button">${provider.enabled ? '禁用' : '启用'}</button>
        <button class="ghost-btn ghost-btn--danger" data-provider-action="delete" type="button">删除</button>
      </div>
    </div>
  `;
}

function renderProviders() {
  if (!dom.providerList) return;
  if (!state.providers.length) {
    dom.providerList.innerHTML =
      '<p class="plugins__empty">还没有配置 Provider。填写上方表单创建一个，然后添加模型。</p>';
    if (dom.providerModelForm) dom.providerModelForm.hidden = true;
    return;
  }
  dom.providerList.innerHTML = state.providers
    .map((provider) => providerCardHtml(provider))
    .join('');
  if (dom.providerModelForm) dom.providerModelForm.hidden = false;

  dom.providerList
    .querySelectorAll('[data-provider-action]')
    .forEach((btn) => {
      btn.addEventListener('click', () => {
        const card = btn.closest('[data-provider-id]');
        if (card) {
          handleProviderAction(btn.dataset.providerAction, card.dataset.providerId);
        }
      });
    });

  dom.providerList.querySelectorAll('[data-model-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = btn.closest('[data-provider-id]');
      const row = btn.closest('[data-model-id]');
      if (card && row) {
        handleModelAction(
          btn.dataset.modelAction,
          card.dataset.providerId,
          row.dataset.modelId,
        );
      }
    });
  });
}

function renderModelFormProviders(preferredProviderId) {
  if (!dom.modelProvider) return;
  const current = preferredProviderId || dom.modelProvider.value;
  const options = state.providers.map((provider) => ({
    value: provider.id,
    label: `${provider.name}${provider.enabled ? '' : '（已禁用）'}`,
  }));
  dom.modelProvider.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      return option;
    }),
  );
  if (current && state.providers.some((item) => item.id === current)) {
    dom.modelProvider.value = current;
  }
}

async function loadProviders() {
  if (!dom.providerList) return;
  try {
    const providers = await fetchProviders();
    state.providers = providers;

    if (
      state.settings.providerId &&
      !providers.some((item) => item.id === state.settings.providerId)
    ) {
      state.settings.providerId = '';
      saveSettings();
    }

    renderProviders();
    renderModelFormProviders();
    updateTopbar();
    updateConfigTip();
  } catch (error) {
    dom.providerList.innerHTML = `<p class="plugins__empty plugins__empty--error">加载 Provider 失败：${escapeHtml(error.message || error)}</p>`;
  }
}

function resetProviderForm() {
  dom.providerForm.reset();
  dom.providerEditId.value = '';
  dom.providerEnabled.checked = true;
  dom.providerDefault.checked = false;
  dom.providerApiKey.value = '';
  dom.providerApiKey.placeholder = '留空表示不修改';
  dom.providerSubmitBtn.textContent = '添加 Provider';
  dom.providerCancelEditBtn.hidden = true;
  dom.providerFormHint.textContent = '新增 Provider 后，还需要至少添加一个 Model。';
}

function startProviderEdit(provider) {
  if (!provider) return;
  dom.providerEditId.value = provider.id;
  dom.providerName.value = provider.name;
  dom.providerBase.value = provider.api_base;
  dom.providerApiKey.value = '';
  dom.providerApiKey.placeholder = provider.has_api_key
    ? '已保存 Key，留空表示不修改'
    : '还未保存 Key，留空表示不设置';
  dom.providerDescription.value = provider.description || '';
  dom.providerEnabled.checked = provider.enabled;
  dom.providerDefault.checked = provider.is_default;
  dom.providerSubmitBtn.textContent = '保存修改';
  dom.providerCancelEditBtn.hidden = false;
  dom.providerFormHint.textContent = provider.is_default
    ? '这是当前默认 Provider；取消勾选后，服务端会自动选择新的默认 Provider。'
    : 'Key 留空表示不修改，填写新值会覆盖旧 Key。';
  dom.providerName.focus();
  dom.providerForm.scrollIntoView({ block: 'nearest' });
}

async function handleProviderSubmit(event) {
  event.preventDefault();
  if (providerBusy) return;

  const name = dom.providerName.value.trim();
  const apiBase = dom.providerBase.value.trim().replace(/\/+$/, '');
  if (!name) {
    toast('Provider 名称不能为空。', 'error');
    dom.providerName.focus();
    return;
  }
  if (!/^https?:\/\//i.test(apiBase)) {
    toast('Endpoint 必须以 http:// 或 https:// 开头。', 'error');
    dom.providerBase.focus();
    return;
  }

  const editId = dom.providerEditId.value;
  const apiKey = dom.providerApiKey.value.trim();
  const payload = {
    name,
    api_base: apiBase,
    description: dom.providerDescription.value.trim(),
    enabled: dom.providerEnabled.checked,
    is_default: dom.providerDefault.checked,
  };
  // 编辑时留空 = 不修改；新增时留空 = 不保存 Key
  if (!editId || apiKey) payload.api_key = apiKey;

  providerBusy = true;
  dom.providerSubmitBtn.disabled = true;
  try {
    if (editId) {
      await updateProvider(editId, payload);
      toast('Provider 已更新', 'success', 2200);
    } else {
      await createProvider(payload);
      toast('Provider 已创建，请继续添加模型', 'success', 2600);
    }
    resetProviderForm();
    await loadProviders();
    if (editId && state.settings.providerId === editId) {
      state.settings.model = getEffectiveSettings().model;
      saveSettings();
    }
  } catch (error) {
    toast(error.message || String(error), 'error', 4200);
  } finally {
    providerBusy = false;
    dom.providerSubmitBtn.disabled = false;
  }
}

async function handleProviderAction(action, providerId) {
  const provider = getProviderById(providerId);
  if (!provider) return;

  if (action === 'edit') {
    startProviderEdit(provider);
    return;
  }
  if (action === 'add-model') {
    resetModelForm(providerId);
    dom.modelProvider.focus();
    dom.providerModelForm.scrollIntoView({ block: 'nearest' });
    return;
  }

  try {
    if (action === 'test') {
      const result = await testProvider(providerId);
      if (result && result.ok) {
        const parts = [`${provider.name} 连接成功`];
        if (typeof result.latency_ms === 'number') parts.push(`${result.latency_ms}ms`);
        if (Array.isArray(result.models)) {
          parts.push(`发现 ${result.models.length} 个模型`);
        }
        toast(parts.join(' · '), 'success', 3200);
      } else {
        toast(`测试失败：${(result && result.message) || '未知错误'}`, 'error', 4200);
      }
    } else if (action === 'toggle') {
      await updateProvider(providerId, { enabled: !provider.enabled });
      toast(provider.enabled ? 'Provider 已禁用' : 'Provider 已启用', 'success');
      await loadProviders();
    } else if (action === 'delete') {
      if (!confirm(`确定删除 Provider「${provider.name}」吗？其模型配置会一并删除。`)) return;
      await deleteProvider(providerId);
      toast('Provider 已删除', 'success');
      if (dom.providerEditId.value === providerId) resetProviderForm();
      if (state.settings.providerId === providerId) {
        state.settings.providerId = '';
        saveSettings();
      }
      await loadProviders();
    }
  } catch (error) {
    toast(error.message || String(error), 'error', 4200);
  }
}

function resetModelForm(providerId) {
  dom.providerModelForm.reset();
  dom.modelEditId.value = '';
  dom.providerModelForm.dataset.editProvider = '';
  dom.modelProvider.disabled = false;
  dom.modelSubmitBtn.textContent = '添加 Model';
  dom.modelCancelEditBtn.hidden = true;
  dom.modelFormHint.textContent = '能力声明会用于发送前预检，也会显示在模型菜单中。';
  renderModelFormProviders(providerId || undefined);
}

function startModelEdit(providerId, model) {
  if (!model) return;
  dom.providerModelForm.hidden = false;
  renderModelFormProviders(providerId);
  dom.modelProvider.value = providerId;
  dom.modelProvider.disabled = true;
  dom.providerModelForm.dataset.editProvider = providerId;
  dom.modelEditId.value = model.id;
  dom.modelId.value = model.id;
  dom.modelDisplayName.value = model.display_name || '';
  const caps = model.capabilities || {};
  dom.modelCapText.checked = caps.text !== false;
  dom.modelCapVision.checked = caps.vision !== false;
  dom.modelCapTools.checked = caps.tool_calling !== false;
  dom.modelCapStreaming.checked = caps.streaming !== false;
  dom.modelContextWindow.value =
    typeof model.context_window === 'number' ? String(model.context_window) : '';
  dom.modelMaxTokens.value =
    typeof model.max_tokens === 'number' ? String(model.max_tokens) : '';
  dom.modelSubmitBtn.textContent = '保存修改';
  dom.modelCancelEditBtn.hidden = false;
  dom.modelFormHint.textContent = `正在编辑 ${providerId} · ${model.id}；重命名会同步清理 Provider 的模型引用。`;
  dom.modelId.focus();
  dom.providerModelForm.scrollIntoView({ block: 'nearest' });
}

async function handleModelSubmit(event) {
  event.preventDefault();
  if (providerBusy) return;

  const providerId = dom.modelProvider.value;
  if (!providerId || !getProviderById(providerId)) {
    toast('请选择所属 Provider。', 'error');
    return;
  }

  const modelId = dom.modelId.value.trim();
  if (!modelId) {
    toast('模型名不能为空。', 'error');
    dom.modelId.focus();
    return;
  }

  const parseOptional = (input, label, min, max) => {
    const raw = input.value.trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < min || value > max) {
      throw new Error(`${label} 需要是 ${min}–${max} 之间的整数。`);
    }
    return value;
  };

  let contextWindow;
  let maxTokens;
  try {
    contextWindow = parseOptional(dom.modelContextWindow, 'Context Window', 1024, 2000000);
    maxTokens = parseOptional(dom.modelMaxTokens, 'Max Tokens', 1, 200000);
  } catch (error) {
    toast(error.message, 'error');
    return;
  }

  const payload = {
    display_name: dom.modelDisplayName.value.trim(),
    capabilities: {
      text: dom.modelCapText.checked,
      vision: dom.modelCapVision.checked,
      tool_calling: dom.modelCapTools.checked,
      streaming: dom.modelCapStreaming.checked,
    },
    context_window: contextWindow,
    max_tokens: maxTokens,
  };

  const editId = dom.modelEditId.value;
  if (editId) {
    if (modelId !== editId) payload.id = modelId;
  } else {
    payload.id = modelId;
  }

  providerBusy = true;
  dom.modelSubmitBtn.disabled = true;
  try {
    if (editId) {
      await updateProviderModel(providerId, editId, payload);
      toast('模型已更新', 'success', 2200);
    } else {
      await createProviderModel(providerId, payload);
      toast('模型已添加', 'success', 2200);
    }
    resetModelForm(providerId);
    await loadProviders();
    if (state.settings.providerId === providerId) {
      state.settings.model = getEffectiveSettings().model;
      saveSettings();
    }
  } catch (error) {
    toast(error.message || String(error), 'error', 4200);
  } finally {
    providerBusy = false;
    dom.modelSubmitBtn.disabled = false;
  }
}

async function handleModelAction(action, providerId, modelId) {
  const provider = getProviderById(providerId);
  const model = provider
    ? (provider.models || []).find((item) => item.id === modelId)
    : null;
  if (!provider || !model) return;

  if (action === 'edit') {
    startModelEdit(providerId, model);
    return;
  }

  try {
    if (action === 'test') {
      const result = await testProviderModel(providerId, modelId);
      if (result && result.ok) {
        const parts = [`模型 ${modelId} 可用`];
        if (typeof result.latency_ms === 'number') parts.push(`${result.latency_ms}ms`);
        toast(parts.join(' · '), 'success', 3200);
      } else {
        toast(`模型测试失败：${(result && result.message) || '未知错误'}`, 'error', 4600);
      }
    } else if (action === 'delete') {
      if (!confirm(`确定删除模型「${modelId}」吗？引用它的 Chat / Agent 模型会被清理。`)) return;
      await deleteProviderModel(providerId, modelId);
      toast('模型已删除', 'success');
      if (dom.modelEditId.value === modelId) resetModelForm(providerId);
      await loadProviders();
      if (state.settings.providerId === providerId) {
        state.settings.model = getEffectiveSettings().model;
        saveSettings();
      }
    }
  } catch (error) {
    toast(error.message || String(error), 'error', 4200);
  }
}

function openProvidersPage() {
  closeSettings();
  closePluginsPage();
  closeMemoryPage();
  closeMcpPage();
  closePermissionsPage();
  closeSidebar();
  toggleModelMenu(false);
  dom.chatScroll.hidden = true;
  dom.composerForm.parentElement.hidden = true;
  dom.providersPage.hidden = false;
  providersOpener = document.activeElement;
  resetProviderForm();
  resetModelForm();
  loadProviders();
  window.requestAnimationFrame(() => {
    dom.providersBackBtn.focus({ preventScroll: true });
  });
}

function closeProvidersPage() {
  if (dom.providersPage.hidden) return;
  dom.providersPage.hidden = true;
  dom.chatScroll.hidden = false;
  dom.composerForm.parentElement.hidden = false;
  if (providersOpener && typeof providersOpener.focus === 'function') {
    providersOpener.focus({ preventScroll: true });
  }
}

/* --------------------------------------------------------------------------
   权限与安全

   统一查看 / 授权内置工具、插件工具与 MCP 工具所需的权限。
   后端所有敏感字段均已脱敏（不含 API Key / Token）。
   -------------------------------------------------------------------------- */

let permissionsOpener = null;
let permissionsData = null;

const PERMISSION_RISK_TEXT = { low: '低风险', medium: '中风险', high: '高风险' };

const SECURITY_EVENT_TEXT = {
  'permission.granted': '授权通过',
  'permission.denied': '权限拒绝',
  'permission.requested': '请求确认',
  'permission.timeout': '确认超时',
  'permission.cancelled': '确认取消',
  'command.executed': '执行命令',
  'workspace.file_deleted': '删除文件',
  'plugin.loaded': '加载插件',
  'mcp.connected': '连接 MCP',
};

function permissionGrantState(principal, permission) {
  const entry =
    (permissionsData && permissionsData.grants && permissionsData.grants[principal]) || {};
  return {
    allowed: (entry.allow || []).includes(permission),
    denied: (entry.deny || []).includes(permission),
  };
}

async function handlePermissionResolve(requestId, decision) {
  const conversation = getActiveConversation();
  if (!conversation) return;
  let target = null;
  let request = null;
  conversation.messages.forEach((message) => {
    if (!Array.isArray(message.permissionRequests)) return;
    const item = message.permissionRequests.find((req) => req.id === requestId);
    if (item) {
      target = message;
      request = item;
    }
  });
  if (!request) return;
  try {
    await resolvePermissionRequest(requestId, decision);
    request.status = decision === 'deny' ? 'denied' : 'granted';
    request.decision = decision;
    if (target && target.agentState === 'running') {
      state.agentStatus =
        decision === 'deny' ? '已拒绝权限请求，Agent 继续执行…' : '已允许，Agent 继续执行…';
      dom.composerHint.textContent = state.agentStatus;
    }
    if (target) updateAssistantNode(target, Boolean(target.content));
    scheduleSave();
  } catch (error) {
    request.status = 'timeout';
    toast(error && error.message ? error.message : '权限请求已失效。', 'error');
    if (target) updateAssistantNode(target, Boolean(target.content));
  }
}

async function updatePermissionGrant(principal, permission, action) {
  try {
    if (action === 'deny') {
      await denyPermission({ principal, permission });
    } else {
      await grantPermission({ principal, permission, scope: 'always' });
    }
    toast(action === 'deny' ? `已拒绝 ${permission}` : `已允许 ${permission}`, 'success');
    await loadPermissions();
  } catch (error) {
    toast(error && error.message ? error.message : '权限更新失败。', 'error');
  }
}

async function resetPermissionGrant(principal, permission) {
  try {
    await resetPermissions(permission ? { principal, permission } : { principal });
    toast(permission ? `已重置 ${permission}` : '已重置该主体的全部授权', 'success');
    await loadPermissions();
  } catch (error) {
    toast(error && error.message ? error.message : '重置失败。', 'error');
  }
}

/** 构建一组权限标签，并附带 允许 / 拒绝 / 重置 操作。 */
function buildPermissionChips(permissions, principal) {
  const wrap = document.createElement('div');
  wrap.className = 'perm-chips';
  const items = Array.isArray(permissions) ? permissions : [];
  if (!items.length) {
    const none = document.createElement('span');
    none.className = 'perm-chips__none';
    none.textContent = '无需额外权限';
    wrap.appendChild(none);
    return wrap;
  }
  items.forEach((permission) => {
    const state = permissionGrantState(principal, permission);
    const chip = document.createElement('span');
    chip.className = 'perm-chip';
    if (state.allowed) chip.classList.add('perm-chip--allowed');
    if (state.denied) chip.classList.add('perm-chip--denied');

    const label = document.createElement('code');
    label.textContent = permission;
    chip.appendChild(label);

    const allowBtn = document.createElement('button');
    allowBtn.type = 'button';
    allowBtn.className = 'perm-chip__btn';
    allowBtn.textContent = state.allowed ? '已允许' : '允许';
    allowBtn.disabled = state.allowed;
    allowBtn.addEventListener('click', () =>
      updatePermissionGrant(principal, permission, 'allow'),
    );
    chip.appendChild(allowBtn);

    const denyBtn = document.createElement('button');
    denyBtn.type = 'button';
    denyBtn.className = 'perm-chip__btn perm-chip__btn--deny';
    denyBtn.textContent = state.denied ? '已拒绝' : '拒绝';
    denyBtn.disabled = state.denied;
    denyBtn.addEventListener('click', () =>
      updatePermissionGrant(principal, permission, 'deny'),
    );
    chip.appendChild(denyBtn);

    if (state.allowed || state.denied) {
      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'perm-chip__btn perm-chip__btn--reset';
      resetBtn.textContent = '重置';
      resetBtn.addEventListener('click', () => resetPermissionGrant(principal, permission));
      chip.appendChild(resetBtn);
    }

    wrap.appendChild(chip);
  });
  return wrap;
}

function permSectionTitle(text, hint) {
  const wrap = document.createElement('div');
  wrap.className = 'perm-group__head';
  const title = document.createElement('h4');
  title.textContent = text;
  wrap.appendChild(title);
  if (hint) {
    const note = document.createElement('span');
    note.className = 'plugins__hint';
    note.textContent = hint;
    wrap.appendChild(note);
  }
  return wrap;
}

function permEntityRow({ id, name, meta, permissions, principal, tools }) {
  const row = document.createElement('div');
  row.className = 'perm-entity';

  const head = document.createElement('div');
  head.className = 'perm-entity__head';
  const title = document.createElement('strong');
  title.textContent = name || id;
  head.appendChild(title);
  const idEl = document.createElement('code');
  idEl.textContent = id;
  head.appendChild(idEl);
  if (meta) {
    const metaEl = document.createElement('span');
    metaEl.className = 'perm-entity__meta';
    metaEl.textContent = meta;
    head.appendChild(metaEl);
  }
  row.appendChild(head);

  if (Array.isArray(tools) && tools.length) {
    const toolsEl = document.createElement('div');
    toolsEl.className = 'perm-entity__tools';
    toolsEl.textContent = `工具：${tools.join(' · ')}`;
    row.appendChild(toolsEl);
  }

  row.appendChild(buildPermissionChips(permissions, principal));
  return row;
}

/** 主体名：优先取插件 / MCP 主体，否则退回工具主体。 */
function primaryPrincipal(principals, toolName) {
  const list = Array.isArray(principals) ? principals : [];
  return list.find((item) => item && !item.startsWith('tool:')) || `tool:${toolName}`;
}

function renderPermissions(data, log) {
  permissionsData = data;
  const grants = data.grants || {};
  const providers = Array.isArray(data.providers) ? data.providers : [];
  const plugins = Array.isArray(data.plugins) ? data.plugins : [];
  const servers = Array.isArray(data.mcp_servers) ? data.mcp_servers : [];
  const tools = Array.isArray(data.tools) ? data.tools : [];
  const workspace = data.workspace || {};

  // 概览
  if (dom.permOverview) {
    const defaultProvider = providers.find((item) => item.default) || providers[0];
    const enabledPlugins = plugins.filter((item) => item.enabled);
    const connectedServers = servers.filter((item) =>
      ['ready', 'connected', 'running'].includes(item.status),
    );
    dom.permOverview.innerHTML = `
      <div class="perm-overview__grid">
        <div class="perm-overview__card">
          <span class="perm-overview__label">Workspace</span>
          <strong class="perm-overview__value">${escapeHtml(workspace.name || '—')}</strong>
          <span class="perm-overview__hint">${escapeHtml(workspace.root || '未配置')}</span>
        </div>
        <div class="perm-overview__card">
          <span class="perm-overview__label">Provider</span>
          <strong class="perm-overview__value">${escapeHtml(defaultProvider ? defaultProvider.name : '未配置')}</strong>
          <span class="perm-overview__hint">${providers.length} 个 Provider · API Key 仅存于服务端</span>
        </div>
        <div class="perm-overview__card">
          <span class="perm-overview__label">插件</span>
          <strong class="perm-overview__value">${enabledPlugins.length} / ${plugins.length} 已启用</strong>
          <span class="perm-overview__hint">${escapeHtml(enabledPlugins.map((item) => item.name).join(' · ') || '暂无插件')}</span>
        </div>
        <div class="perm-overview__card">
          <span class="perm-overview__label">MCP Server</span>
          <strong class="perm-overview__value">${connectedServers.length} / ${servers.length} 已连接</strong>
          <span class="perm-overview__hint">MCP 工具统一使用 mcp:use 权限</span>
        </div>
      </div>
    `;
  }

  // 已授权限
  if (dom.permGrants) {
    const entries = Object.keys(grants).filter(
      (key) => (grants[key].allow || []).length || (grants[key].deny || []).length,
    );
    if (!entries.length) {
      dom.permGrants.innerHTML = '<p class="plugins__empty">暂无授权记录</p>';
    } else {
      dom.permGrants.innerHTML = '';
      entries.forEach((principal) => {
        const entry = grants[principal];
        const card = document.createElement('div');
        card.className = 'perm-grant';

        const head = document.createElement('div');
        head.className = 'perm-grant__head';
        const code = document.createElement('code');
        code.textContent = principal;
        head.appendChild(code);
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'perm-chip__btn perm-chip__btn--reset';
        resetBtn.textContent = '重置';
        resetBtn.addEventListener('click', () => resetPermissionGrant(principal));
        head.appendChild(resetBtn);
        card.appendChild(head);

        [
          ['允许', entry.allow || [], 'allow'],
          ['拒绝', entry.deny || [], 'deny'],
        ].forEach(([label, list, kind]) => {
          if (!list.length) return;
          const line = document.createElement('div');
          line.className = 'perm-grant__line';
          const labelEl = document.createElement('span');
          labelEl.className = `perm-grant__label perm-grant__label--${kind}`;
          labelEl.textContent = label;
          line.appendChild(labelEl);
          list.forEach((permission) => {
            const tag = document.createElement('span');
            tag.className = `perm-tag perm-tag--${kind}`;
            tag.textContent = permission;
            line.appendChild(tag);
          });
          card.appendChild(line);
        });

        dom.permGrants.appendChild(card);
      });
    }
  }

  // 权限目录
  if (dom.permCatalog) {
    const catalog = Array.isArray(data.catalog) ? data.catalog : [];
    dom.permCatalog.innerHTML = catalog
      .map(
        (item) => `
        <div class="perm-row">
          <div class="perm-row__main">
            <code>${escapeHtml(item.permission)}</code>
            <span class="perm-risk perm-risk--${escapeHtml(item.risk)}">${escapeHtml(
              PERMISSION_RISK_TEXT[item.risk] || item.risk,
            )}</span>
            <span class="perm-default${item.default === 'allow' ? '' : ' perm-default--ask'}">${
              item.default === 'allow' ? '默认允许' : '默认询问'
            }</span>
          </div>
          <p class="perm-row__desc">${escapeHtml(item.description || '')}</p>
        </div>`,
      )
      .join('');
  }

  // 插件 / MCP / 工具
  if (dom.permTools) {
    dom.permTools.replaceChildren();

    if (plugins.length) {
      dom.permTools.appendChild(permSectionTitle('插件', '安装插件即信任其代码'));
      plugins.forEach((plugin) => {
        dom.permTools.appendChild(
          permEntityRow({
            id: `plugin:${plugin.id}`,
            name: plugin.name,
            meta: `v${plugin.version} · ${plugin.enabled ? '已启用' : '已禁用'} · ${plugin.status}`,
            permissions: plugin.permissions,
            principal: `plugin:${plugin.id}`,
            tools: plugin.tools,
          }),
        );
      });
    }

    if (servers.length) {
      dom.permTools.appendChild(permSectionTitle('MCP Server', '本地 stdio 子进程'));
      servers.forEach((server) => {
        dom.permTools.appendChild(
          permEntityRow({
            id: `mcp:${server.id}`,
            name: server.name,
            meta: `${server.status}${server.enabled ? '' : ' · 已禁用'}`,
            permissions: server.permissions,
            principal: `mcp:${server.id}`,
            tools: server.tools,
          }),
        );
      });
    }

    dom.permTools.appendChild(permSectionTitle('工具', '内置 / 插件 / MCP 统一注册'));
    if (!tools.length) {
      const empty = document.createElement('p');
      empty.className = 'plugins__empty';
      empty.textContent = '暂无已注册工具';
      dom.permTools.appendChild(empty);
    } else {
      tools.forEach((tool) => {
        dom.permTools.appendChild(
          permEntityRow({
            id: tool.name,
            name: tool.name,
            meta: (tool.principals || []).join(' · '),
            permissions: tool.permissions,
            principal: primaryPrincipal(tool.principals, tool.name),
          }),
        );
      });
    }
  }

  // 安全日志
  if (dom.permLog) {
    const entries = Array.isArray(log) ? log : [];
    if (!entries.length) {
      dom.permLog.innerHTML = '<p class="plugins__empty">暂无安全事件</p>';
    } else {
      dom.permLog.innerHTML = entries
        .map((entry) => {
          const fields = Object.entries(entry)
            .filter(([key]) => key !== 'event' && key !== 'timestamp')
            .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : String(value)}`)
            .join(' · ');
          return `
            <div class="perm-log__row">
              <span class="perm-log__event">${escapeHtml(
                SECURITY_EVENT_TEXT[entry.event] || entry.event,
              )}</span>
              <span class="perm-log__time">${escapeHtml(formatHistoryTime(entry.timestamp))}</span>
              <span class="perm-log__detail">${escapeHtml(fields || '—')}</span>
            </div>`;
        })
        .join('');
    }
  }
}

async function loadPermissions() {
  try {
    const [data, log] = await Promise.all([fetchPermissions(), fetchSecurityLog(80)]);
    renderPermissions(data, log);
  } catch (error) {
    console.warn('[WeCi] failed to load permissions', error);
    if (dom.permOverview) {
      dom.permOverview.innerHTML =
        '<p class="plugins__empty">权限服务不可用，请确认后端已启动。</p>';
    }
  }
}

function openPermissionsPage() {
  closeSettings();
  closePluginsPage();
  closeMemoryPage();
  closeMcpPage();
  closeProvidersPage();
  closeSidebar();
  toggleModelMenu(false);
  dom.chatScroll.hidden = true;
  dom.composerForm.parentElement.hidden = true;
  dom.permissionsPage.hidden = false;
  permissionsOpener = document.activeElement;
  loadPermissions();
  window.requestAnimationFrame(() => {
    dom.permissionsBackBtn.focus({ preventScroll: true });
  });
}

function closePermissionsPage() {
  if (!dom.permissionsPage || dom.permissionsPage.hidden) return;
  dom.permissionsPage.hidden = true;
  dom.chatScroll.hidden = false;
  dom.composerForm.parentElement.hidden = false;
  if (permissionsOpener && typeof permissionsOpener.focus === 'function') {
    permissionsOpener.focus({ preventScroll: true });
  }
}

function openSettings() {
  closeProvidersPage();
  closePermissionsPage();
  const effective = getEffectiveSettings();
  dom.settingEndpoint.value = state.settings.apiBase || state.server.apiBase || '';
  dom.settingApiKey.value = state.settings.apiKey || '';
  dom.settingModel.value = state.settings.model || state.server.model || '';
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
  setProviderTestResult('', '');
  renderModelDatalist();

  const active = getActiveProvider();
  syncSettingsProviderSelect(active ? active.id : '');
  syncSettingsModelSelect(active ? effective.model : '');
  renderSettingCapabilities(active, effective.model);
  dom.serverProviderBadge.hidden = !state.providers.length;
  dom.testProviderBtn.hidden = !active;
  dom.compatDetails.open = state.providers.length === 0;

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
  closeMemoryPage();
  closeMcpPage();
  closeProvidersPage();
  closePermissionsPage();
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

  const provider = providerForSettingsForm();
  if (provider) {
    const model = dom.settingProviderModel.value.trim();
    if (!model) {
      toast('该 Provider 还没有模型，请先在 Provider 管理中添加 Model。', 'error');
      return;
    }
    state.settings = {
      ...state.settings,
      providerId: dom.settingProvider.value || '',
      model,
      temperature,
      maxTokens,
    };
    rememberModel(model);
    saveSettings();
    updateTopbar();
    updateConfigTip();
    closeSettings();
    toast(`设置已保存，当前使用 ${provider.name} · ${model}`, 'success', 2600);
    return;
  }

  // 兼容模式：旧版 Endpoint / Key / Model
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

  state.settings = {
    ...state.settings,
    providerId: '',
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

function modelCapabilityLine(model) {
  const caps = (model && model.capabilities) || {};
  const capsRow = document.createElement('div');
  capsRow.className = 'model-menu__caps';
  ['vision', 'tool_calling', 'streaming'].forEach((key) => {
    const on = caps[key] !== false;
    const item = document.createElement('span');
    item.className = `model-menu__cap${on ? ' is-on' : ' is-off'}`;
    item.textContent = `${on ? '✓' : '✗'} ${CAPABILITY_LABELS[key]}`;
    capsRow.appendChild(item);
  });
  return capsRow;
}

function renderModelMenu() {
  dom.modelMenu.replaceChildren();
  const effective = getEffectiveSettings();

  if (state.providers.length) {
    state.providers.forEach((provider) => {
      const heading = document.createElement('div');
      heading.className = 'model-menu__group';
      heading.textContent = provider.name;
      if (provider.is_default) {
        const badge = document.createElement('span');
        badge.className = 'model-menu__group-badge';
        badge.textContent = '默认';
        heading.appendChild(badge);
      }
      if (!provider.enabled) {
        const badge = document.createElement('span');
        badge.className = 'model-menu__group-badge model-menu__group-badge--off';
        badge.textContent = '已禁用';
        heading.appendChild(badge);
      }
      dom.modelMenu.appendChild(heading);

      const models = Array.isArray(provider.models) ? provider.models : [];
      if (!models.length) {
        const empty = document.createElement('p');
        empty.className = 'model-menu__empty';
        empty.textContent = '该 Provider 还没有模型。';
        dom.modelMenu.appendChild(empty);
        return;
      }

      models.forEach((model) => {
        const active =
          provider.id === effective.providerId && model.id === effective.model;
        const item = document.createElement('button');
        item.type = 'button';
        item.className = `model-menu__item model-menu__model${active ? ' is-active' : ''}${
          provider.enabled ? '' : ' is-disabled'
        }`;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(active));
        item.disabled = !provider.enabled;
        item.appendChild(icon('check'));

        const body = document.createElement('div');
        body.className = 'model-menu__main';
        const name = document.createElement('div');
        name.className = 'model-menu__model-name';
        name.textContent = model.display_name || model.id;
        if (model.display_name && model.display_name !== model.id) {
          const raw = document.createElement('span');
          raw.className = 'model-menu__model-raw';
          raw.textContent = model.id;
          name.appendChild(raw);
        }
        body.appendChild(name);
        body.appendChild(modelCapabilityLine(model));
        item.appendChild(body);

        item.addEventListener('click', () => {
          if (!provider.enabled) return;
          chooseProviderModel(provider.id, model.id);
        });
        dom.modelMenu.appendChild(item);
      });
    });
  } else {
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
  }

  const divider = document.createElement('div');
  divider.className = 'model-menu__divider';
  dom.modelMenu.appendChild(divider);

  if (state.providers.length) {
    const manageProviders = document.createElement('button');
    manageProviders.type = 'button';
    manageProviders.className = 'model-menu__item model-menu__manage';
    manageProviders.appendChild(icon('cpu'));
    const providersText = document.createElement('span');
    providersText.textContent = 'Provider 管理…';
    manageProviders.appendChild(providersText);
    manageProviders.addEventListener('click', () => {
      toggleModelMenu(false);
      openProvidersPage();
    });
    dom.modelMenu.appendChild(manageProviders);
  }

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
  state.settings.providerId = '';
  rememberModel(name);
  saveSettings();
  toggleModelMenu(false);
  updateTopbar();
  updateConfigTip();
  toast(`已切换模型：${name}`, 'success', 2000);
}

function chooseProviderModel(providerId, model) {
  const provider = state.providers.find((item) => item.id === providerId);
  state.settings.providerId = providerId;
  state.settings.model = model;
  rememberModel(model);
  saveSettings();
  toggleModelMenu(false);
  updateTopbar();
  updateConfigTip();
  toast(
    `已切换模型：${provider ? provider.name : providerId} · ${model}`,
    'success',
    2000,
  );
}

/* --------------------------------------------------------------------------
   输入与侧栏
   -------------------------------------------------------------------------- */

function autoGrow() {
  dom.input.style.height = 'auto';
  dom.input.style.height = `${Math.min(dom.input.scrollHeight, MAX_INPUT_HEIGHT)}px`;
}

function updateSendState() {
  const hasText = Boolean(dom.input.value.trim());
  const hasImages = state.pendingAttachments.length > 0;
  const hasFiles = state.pendingFiles.length > 0;
  dom.sendBtn.disabled =
    state.streaming ||
    attachmentBusy ||
    (!hasText && !hasImages && !hasFiles);
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

  dom.attachBtn.addEventListener('click', () => {
    if (state.streaming) return;
    dom.fileInput.click();
  });

  dom.fileInput.addEventListener('change', () => {
    const files = Array.from(dom.fileInput.files || []);
    dom.fileInput.value = '';
    if (files.length) addFiles(files);
  });

  dom.fileBtn.addEventListener('click', () => {
    if (state.streaming) return;
    dom.docInput.click();
  });

  dom.docInput.addEventListener('change', () => {
    const files = Array.from(dom.docInput.files || []);
    dom.docInput.value = '';
    if (files.length) addFiles(files);
  });

  if (dom.composerWrap) {
    let dragDepth = 0;
    const carriesFiles = (event) =>
      Array.from((event.dataTransfer && event.dataTransfer.types) || []).includes(
        'Files',
      );

    dom.composerWrap.addEventListener('dragenter', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth += 1;
      dom.composerWrap.classList.add('is-dragover');
    });

    dom.composerWrap.addEventListener('dragover', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });

    dom.composerWrap.addEventListener('dragleave', () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) dom.composerWrap.classList.remove('is-dragover');
    });

    dom.composerWrap.addEventListener('drop', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth = 0;
      dom.composerWrap.classList.remove('is-dragover');
      if (state.streaming) {
        toast('正在生成中，暂时无法添加附件。', 'error');
        return;
      }
      const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
      if (!files.length) return;
      addFiles(files);
    });
  }

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

  dom.topMemoryBtn.addEventListener('click', openMemoryPage);
  dom.memoryBackBtn.addEventListener('click', closeMemoryPage);
  dom.memoryRefreshBtn.addEventListener('click', loadMemories);
  dom.memoryForm.addEventListener('submit', handleMemorySubmit);
  dom.settingsOpenMemoryBtn.addEventListener('click', openMemoryPage);

  dom.topMcpBtn.addEventListener('click', openMcpPage);
  dom.mcpBackBtn.addEventListener('click', closeMcpPage);
  dom.mcpRefreshBtn.addEventListener('click', loadMcpServers);
  dom.mcpForm.addEventListener('submit', handleMcpSubmit);
  dom.mcpCancelEditBtn.addEventListener('click', resetMcpForm);
  dom.settingsOpenMcpBtn.addEventListener('click', openMcpPage);

  dom.providersBtn.addEventListener('click', openProvidersPage);
  dom.providersBackBtn.addEventListener('click', closeProvidersPage);
  dom.providersRefreshBtn.addEventListener('click', loadProviders);

  dom.permissionsBtn.addEventListener('click', openPermissionsPage);
  dom.topPermissionsBtn.addEventListener('click', openPermissionsPage);
  dom.permissionsBackBtn.addEventListener('click', closePermissionsPage);
  dom.permissionsRefreshBtn.addEventListener('click', loadPermissions);
  dom.permResetAllBtn.addEventListener('click', async () => {
    try {
      await resetPermissions();
      toast('已重置全部授权', 'success');
      await loadPermissions();
    } catch (error) {
      toast(error && error.message ? error.message : '重置失败。', 'error');
    }
  });

  dom.providerForm.addEventListener('submit', handleProviderSubmit);
  dom.providerCancelEditBtn.addEventListener('click', resetProviderForm);
  dom.providerModelForm.addEventListener('submit', handleModelSubmit);
  dom.modelCancelEditBtn.addEventListener('click', () => resetModelForm());
  dom.settingProvider.addEventListener('change', () => {
    syncSettingsModelSelect();
    dom.settingProviderHint.textContent = state.providers.length
      ? '选择默认 Provider 时自动使用服务端默认；Key 由服务端保存。'
      : '还没有 Provider，请使用下方兼容模式或先创建 Provider。';
  });
  dom.settingProviderModel.addEventListener('change', () => {
    renderSettingCapabilities(
      providerForSettingsForm(),
      dom.settingProviderModel.value,
    );
  });
  dom.testProviderBtn.addEventListener('click', handleTestProvider);
  dom.openProvidersBtn.addEventListener('click', openProvidersPage);

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
      if (!dom.memoryPage.hidden) {
        closeMemoryPage();
      } else if (!dom.mcpPage.hidden) {
        closeMcpPage();
      } else if (!dom.providersPage.hidden) {
        closeProvidersPage();
      } else if (!dom.pluginsPage.hidden) {
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

  // 页面刷新 / 关闭时，把正在生成的回复标记为中断后再保存，
  // 避免控制台网络错误把消息写成“执行失败”，也避免 UI 永久停留在运行状态。
  window.addEventListener('pagehide', () => {
    pageUnloading = true;
    const message = state.streamingMessage;
    if (message) {
      message.stopped = true;
      message.interrupted = true;
      if (message.agentState === 'running') message.agentState = 'stopped';
    }
    if (state.controller) {
      state.controller.abort();
      state.controller = null;
    }
    saveState();
  });

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
      hasProvider: Boolean(data.has_provider),
      defaultProviderId:
        typeof data.default_provider_id === 'string' ? data.default_provider_id : '',
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
  loadProviders();

  if (isNarrowScreen()) {
    dom.input.blur();
  } else {
    dom.input.focus({ preventScroll: true });
  }
}

init();
