/* ==========================================================================
   WeCi · 通用 UI 组件

   负责可复用的 DOM 构建：toast、复制、消息节点、历史列表项、
   内联重命名输入框、消息编辑器。业务编排（点击后做什么）由 main.js
   通过 handlers 回调注入，保持本模块无业务状态。
   ========================================================================== */

import { icon, formatClock } from './utils.js';
import { createProse, enhanceMarkdown, typingEl } from './markdown.js';
import { formatBytes } from './attachments.js';

/* --------------------------------------------------------------------------
   Toast 通知
   -------------------------------------------------------------------------- */

export function toast(message, type = 'info', duration = 3200) {
  const stack = document.getElementById('toastStack');
  if (!stack) return;

  const node = document.createElement('div');
  node.className = `toast${type === 'error' ? ' toast--error' : ''}`;
  node.setAttribute('role', type === 'error' ? 'alert' : 'status');

  const iconName = type === 'error' ? 'alert' : type === 'success' ? 'check' : 'spark';
  node.appendChild(icon(iconName));

  const text = document.createElement('span');
  text.textContent = message;
  node.appendChild(text);

  while (stack.children.length >= 4) {
    stack.firstElementChild.remove();
  }
  stack.appendChild(node);

  window.setTimeout(() => {
    node.classList.add('is-leaving');
    window.setTimeout(() => node.remove(), 220);
  }, duration);
}

/* --------------------------------------------------------------------------
   剪贴板
   -------------------------------------------------------------------------- */

export async function copyText(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制', 'success', 1800);
  } catch {
    // 兼容非安全上下文
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      toast('已复制', 'success', 1800);
    } catch {
      toast('复制失败，请手动选择文本', 'error');
    }
    area.remove();
  }
}

/* --------------------------------------------------------------------------
   工具调用卡片（Agent 模式）

   只展示实际发生的执行事实：工具名、参数、返回结果、状态与耗时。
   不展示模型的隐藏推理过程。参数与结果默认折叠，长输出在展开区域内滚动。
   -------------------------------------------------------------------------- */

const TOOL_STATUS_LABEL = {
  running: '执行中',
  done: '已完成',
  error: '失败',
  interrupted: '已中断',
};

const AGENT_STATE_LABEL = {
  running: '执行中',
  done: '已完成',
  error: '执行失败',
  stopped: '已停止',
};

/** 把毫秒格式化为 12s / 2m 31s，用于 Agent 执行耗时。 */
function formatDuration(value) {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms <= 0) return '';
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function formatArguments(value) {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return '';
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return trimmed;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function createDetails(label, text, countText) {
  const details = document.createElement('details');
  details.className = 'tool-call__details';

  const summary = document.createElement('summary');
  summary.className = 'tool-call__summary';

  const title = document.createElement('span');
  title.className = 'tool-call__summary-label';
  title.textContent = label;
  summary.appendChild(title);

  if (countText) {
    const meta = document.createElement('span');
    meta.className = 'tool-call__summary-meta';
    meta.textContent = countText;
    summary.appendChild(meta);
  }

  details.appendChild(summary);

  const pre = document.createElement('pre');
  pre.className = 'tool-call__pre';
  pre.textContent = text;
  details.appendChild(pre);

  return details;
}

/**
 * 构建一条工具调用的卡片。
 * 默认只显示工具名 / 步骤 / 状态 / 耗时；参数与结果折叠在 details 中。
 */
export function createToolCallCard(tc) {
  const status = tc.status || 'running';
  const card = document.createElement('div');
  card.className = `tool-call tool-call--${status}`;
  card.dataset.toolCallId = tc.id;

  const head = document.createElement('div');
  head.className = 'tool-call__head';

  const stateIcon = document.createElement('span');
  stateIcon.className = `tool-call__state tool-call__state--${status}`;
  if (status === 'running') {
    const spinner = document.createElement('span');
    spinner.className = 'tool-call__spinner';
    stateIcon.appendChild(spinner);
    stateIcon.setAttribute('aria-label', '执行中');
  } else {
    stateIcon.appendChild(icon(status === 'done' ? 'check' : 'alert'));
    stateIcon.setAttribute('aria-label', TOOL_STATUS_LABEL[status] || status);
  }
  head.appendChild(stateIcon);

  const name = document.createElement('span');
  name.className = 'tool-call__name';
  name.textContent = tc.name;
  head.appendChild(name);

  if (typeof tc.step === 'number' && tc.step > 0) {
    const step = document.createElement('span');
    step.className = 'tool-call__step';
    step.textContent = `第 ${tc.step} 步`;
    head.appendChild(step);
  }

  const statusLabel = document.createElement('span');
  statusLabel.className = `tool-call__status tool-call__status--${status}`;
  statusLabel.textContent = TOOL_STATUS_LABEL[status] || status;
  head.appendChild(statusLabel);

  if (typeof tc.durationMs === 'number' && status !== 'running') {
    const dur = document.createElement('span');
    dur.className = 'tool-call__duration';
    dur.textContent = `${tc.durationMs.toFixed(0)}ms`;
    head.appendChild(dur);
  }

  card.appendChild(head);

  if (status === 'error' && tc.error) {
    const err = document.createElement('div');
    err.className = 'tool-call__error';
    err.textContent = tc.error;
    card.appendChild(err);
  }

  const args = formatArguments(tc.arguments);
  if (args) {
    card.appendChild(createDetails('参数', args, `${args.length} 字符`));
  }

  if (typeof tc.output === 'string' && tc.output.length) {
    card.appendChild(createDetails('结果', tc.output, `${tc.output.length} 字符`));
  } else if (status === 'done') {
    const empty = document.createElement('div');
    empty.className = 'tool-call__empty';
    empty.textContent = '（无输出）';
    card.appendChild(empty);
  }

  return card;
}

/**
 * 构建 Agent 执行过程：头部状态 + 工具调用卡片列表。
 * options.state / options.durationMs 用于展示当前阶段与耗时；
 * 工具调用数量直接来自列表，不展示任何“最大步数”。
 */
export function createToolCallsEl(toolCalls, options = {}) {
  const items = Array.isArray(toolCalls) ? toolCalls.filter(Boolean) : [];
  const state = options.state;
  const hasHeader = items.length > 0 || state === 'running';
  if (!items.length && !hasHeader) return null;

  const wrap = document.createElement('div');
  wrap.className = 'agent-trace';

  if (hasHeader) {
    const head = document.createElement('div');
    head.className = `agent-trace__head agent-trace__head--${state || 'idle'}`;

    const badge = document.createElement('span');
    badge.className = 'agent-trace__icon';
    badge.appendChild(icon('spark'));
    head.appendChild(badge);

    const title = document.createElement('span');
    title.className = 'agent-trace__title';
    title.textContent = 'Agent 执行过程';
    head.appendChild(title);

    const count = items.length;
    const countText = `${count} tool calls`;
    const duration = formatDuration(options.durationMs);
    let statusText = AGENT_STATE_LABEL[state] || '';
    if (state === 'running') {
      statusText = count ? `${countText} · Running...` : 'Running...';
    } else if (state === 'done') {
      statusText = count
        ? `${countText}${duration ? ` · ${duration}` : ''}`
        : '已完成';
    } else if (state === 'stopped') {
      statusText = count ? `${countText} · 已停止` : '已停止';
    } else if (state === 'error') {
      statusText = count ? `${countText} · 执行失败` : '执行失败';
    }
    if (statusText) {
      const stateEl = document.createElement('span');
      stateEl.className = 'agent-trace__state';
      stateEl.textContent = statusText;
      head.appendChild(stateEl);
    }

    wrap.appendChild(head);
  }

  if (items.length) {
    const list = document.createElement('div');
    list.className = 'tool-calls';
    items.forEach((tc) => list.appendChild(createToolCallCard(tc)));
    wrap.appendChild(list);
  }

  return wrap;
}

/* --------------------------------------------------------------------------
   权限确认卡片（Agent 高风险操作 / 高风险命令）

   展示“Agent 请求执行 XXX”，按钮由服务端下发：
     - 权限请求：Allow once / Allow for this Run / Deny
     - 命令确认（安全模式）：Allow Once / Deny（不记住批准）
   已处理（granted / denied / timeout）后只展示结果，不再提供按钮。
   -------------------------------------------------------------------------- */

const PERMISSION_RISK_LABEL = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  safe: '安全',
  dangerous: '高风险',
};

const PERMISSION_STATUS_LABEL = {
  pending: '等待确认',
  granted: '已允许',
  denied: '已拒绝',
  timeout: '已超时',
};

const DECISION_LABEL = {
  once: '允许一次',
  run: '本次 Run 允许',
  deny: '拒绝',
};

const DECISION_VARIANT = {
  once: 'primary',
  run: '',
  deny: 'danger',
};

export function createPermissionCard(request, onResolve) {
  const status = request.status || 'pending';
  const card = document.createElement('div');
  card.className = `perm-card perm-card--${status}`;
  card.dataset.requestId = request.id;

  const head = document.createElement('div');
  head.className = 'perm-card__head';

  const stateIcon = document.createElement('span');
  stateIcon.className = 'perm-card__icon';
  stateIcon.appendChild(icon(status === 'granted' ? 'check' : 'shield'));
  head.appendChild(stateIcon);

  const isCommand = request.kind === 'command';
  const title = document.createElement('span');
  title.className = 'perm-card__title';
  title.textContent =
    request.title ||
    (isCommand ? 'Agent 请求执行命令' : `Agent 请求执行 ${request.tool || '工具'}`);
  head.appendChild(title);

  const risk = PERMISSION_RISK_LABEL[request.risk];
  if (risk) {
    const riskEl = document.createElement('span');
    riskEl.className = `perm-card__risk perm-card__risk--${request.risk}`;
    riskEl.textContent = risk;
    head.appendChild(riskEl);
  }

  const statusEl = document.createElement('span');
  statusEl.className = `perm-card__status perm-card__status--${status}`;
  statusEl.textContent = PERMISSION_STATUS_LABEL[status] || status;
  head.appendChild(statusEl);

  card.appendChild(head);

  // 命令确认：完整展示将要执行的命令与风险原因
  if (request.command) {
    const label = document.createElement('div');
    label.className = 'perm-card__label';
    label.textContent = 'Agent wants to run:';
    card.appendChild(label);

    const command = document.createElement('pre');
    command.className = 'perm-card__command';
    command.textContent = request.command;
    card.appendChild(command);
  }

  if (request.reason) {
    const reason = document.createElement('div');
    reason.className = 'perm-card__reason';
    reason.textContent = `原因：${request.reason}`;
    card.appendChild(reason);
  }

  if (request.mode) {
    const mode = document.createElement('div');
    mode.className = 'perm-card__mode';
    mode.textContent = `安全模式：${request.mode}`;
    card.appendChild(mode);
  }

  if (!isCommand) {
    const meta = document.createElement('div');
    meta.className = 'perm-card__meta';
    const permCode = document.createElement('code');
    permCode.textContent = request.permission || '';
    meta.append('权限：', permCode);
    if (request.tool) {
      const toolCode = document.createElement('code');
      toolCode.textContent = request.tool;
      meta.append(' · 工具：', toolCode);
    }
    card.appendChild(meta);
  }

  if (!isCommand && request.detail) {
    const detail = document.createElement('pre');
    detail.className = 'perm-card__detail';
    detail.textContent = request.detail;
    card.appendChild(detail);
  }

  if (status === 'pending' && typeof onResolve === 'function') {
    const actions = document.createElement('div');
    actions.className = 'perm-card__actions';
    const options =
      Array.isArray(request.options) && request.options.length
        ? request.options
        : ['once', 'run', 'deny'];
    options.forEach((decision) => {
      const label = DECISION_LABEL[decision];
      if (!label) return;
      const variant = DECISION_VARIANT[decision];
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `perm-btn${variant ? ` perm-btn--${variant}` : ''}`;
      btn.textContent = label;
      // 不默认选中任何按钮，必须由用户主动点击
      btn.addEventListener('click', () => {
        actions.querySelectorAll('button').forEach((item) => {
          item.disabled = true;
        });
        onResolve(request.id, decision);
      });
      actions.appendChild(btn);
    });
    card.appendChild(actions);
  }

  return card;
}

export function createPermissionRequestsEl(requests, onResolve) {
  const items = Array.isArray(requests) ? requests.filter(Boolean) : [];
  if (!items.length) return null;
  const wrap = document.createElement('div');
  wrap.className = 'perm-requests';
  items.forEach((req) => wrap.appendChild(createPermissionCard(req, onResolve)));
  return wrap;
}

/* --------------------------------------------------------------------------
   图片附件展示（消息内 / 输入框预览）
   -------------------------------------------------------------------------- */

/**
 * 构建图片网格。
 * options.variant：'message'（消息内）| 'composer'（输入框预览，更小）
 * options.onRemove：传入后为每张图片显示移除按钮
 */
export function createImageGrid(attachments, options = {}) {
  const items = Array.isArray(attachments)
    ? attachments.filter((item) => item && typeof item.dataUrl === 'string')
    : [];
  if (!items.length) return null;

  const variant = options.variant === 'composer' ? 'composer' : 'message';
  const grid = document.createElement('div');
  grid.className = `media-grid media-grid--${variant}`;
  grid.dataset.count = String(items.length);

  items.forEach((attachment) => {
    const tile = document.createElement('figure');
    tile.className = 'media-tile';

    const image = document.createElement('img');
    image.src = attachment.dataUrl;
    image.alt = attachment.name || '图片';
    image.loading = 'lazy';
    image.decoding = 'async';
    tile.appendChild(image);

    if (typeof options.onRemove === 'function') {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'media-tile__remove';
      remove.setAttribute('aria-label', `移除图片：${attachment.name || 'image'}`);
      remove.appendChild(icon('close'));
      remove.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        options.onRemove(attachment);
      });
      tile.appendChild(remove);
    }

    grid.appendChild(tile);
  });

  return grid;
}

/**
 * 构建文件附件列表。
 * options.onRemove：传入后为每个文件显示移除按钮。
 */
export function createFileList(files, options = {}) {
  const items = Array.isArray(files)
    ? files.filter((file) => file && typeof file.name === 'string')
    : [];
  if (!items.length) return null;

  const list = document.createElement('div');
  list.className = 'file-list';

  items.forEach((file) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.title = file.name;

    const iconWrap = document.createElement('span');
    iconWrap.className = 'file-chip__icon';
    iconWrap.appendChild(icon('file'));
    chip.appendChild(iconWrap);

    const meta = document.createElement('span');
    meta.className = 'file-chip__meta';

    const name = document.createElement('span');
    name.className = 'file-chip__name';
    name.textContent = file.name;
    meta.appendChild(name);

    const size = document.createElement('span');
    size.className = 'file-chip__size';
    size.textContent = formatBytes(file.size) || '文件';
    meta.appendChild(size);

    chip.appendChild(meta);

    if (typeof options.onRemove === 'function') {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'file-chip__remove';
      remove.setAttribute('aria-label', `移除文件：${file.name}`);
      remove.appendChild(icon('close'));
      remove.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        options.onRemove(file);
      });
      chip.appendChild(remove);
    }

    list.appendChild(chip);
  });

  return list;
}

/* --------------------------------------------------------------------------
   消息节点
   -------------------------------------------------------------------------- */

export function buildErrorElement(message) {
  const error = document.createElement('div');
  error.className = 'msg__error';
  error.setAttribute('role', 'alert');
  error.appendChild(icon('alert'));

  const body = document.createElement('div');
  body.className = 'msg__error-body';

  const title = document.createElement('strong');
  title.textContent = '回复中断';
  body.appendChild(title);

  const text = document.createElement('span');
  text.textContent = message.error || '生成失败，请稍后重试。';
  body.appendChild(text);

  if (message.role === 'assistant') {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'msg__retry';
    retry.dataset.action = 'retry';
    retry.dataset.messageId = message.id;
    retry.appendChild(icon('refresh'));
    const label = document.createElement('span');
    label.textContent = '重试';
    retry.appendChild(label);
    body.appendChild(retry);
  }

  error.appendChild(body);
  return error;
}

function metaAction(action, messageId, iconName, label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'msg__action';
  button.dataset.action = action;
  button.dataset.messageId = messageId;
  button.setAttribute('aria-label', label);
  button.appendChild(icon(iconName));
  const text = document.createElement('span');
  text.textContent = label;
  button.appendChild(text);
  return button;
}

/**
 * 构建一条消息的 DOM 节点。
 * options.isLastAssistant：是否为当前会话最后一条 AI 回复（决定是否显示“重新生成”）。
 */
export function createMessageEl(message, options = {}) {
  const isAssistant = message.role === 'assistant';
  const article = document.createElement('article');
  article.className = `msg msg--${message.role}`;
  article.dataset.messageId = message.id;
  article.setAttribute('aria-label', isAssistant ? 'WeCi 的回复' : '你的消息');

  if (isAssistant) {
    const avatar = document.createElement('div');
    avatar.className = 'msg__avatar';
    avatar.appendChild(icon('spark'));
    article.appendChild(avatar);
  }

  const body = document.createElement('div');
  body.className = 'msg__body';

  if (isAssistant) {
    const content = document.createElement('div');
    content.className = 'msg__content';

    // Agent 工具调用卡片（在正文之前）
    const toolCallsEl = createToolCallsEl(message.toolCalls, {
      state: message.agentState,
      durationMs: message.agentDurationMs,
    });
    if (toolCallsEl) content.appendChild(toolCallsEl);

    const permissionEl = createPermissionRequestsEl(
      message.permissionRequests,
      options.onResolvePermission,
    );
    if (permissionEl) content.appendChild(permissionEl);

    if (message.content) {
      const prose = createProse(message.content);
      enhanceMarkdown(prose);
      content.appendChild(prose);
    } else if (message.error) {
      content.appendChild(buildErrorElement(message));
    } else if (message.stopped) {
      const note = document.createElement('p');
      note.className = 'msg__note';
      note.textContent = message.interrupted
        ? '该回复已中断（页面刷新或网络断开），可重新生成。'
        : '已停止生成。';
      content.appendChild(note);
    } else {
      content.appendChild(typingEl());
    }

    if (message.content && message.error) {
      content.appendChild(buildErrorElement(message));
    }

    body.appendChild(content);
  } else {
    const bubble = document.createElement('div');
    bubble.className = 'msg__bubble';

    const media = createImageGrid(message.attachments);
    const fileList = createFileList(message.files);
    if (media || fileList) {
      bubble.classList.add('msg__bubble--media');
      if (media) bubble.appendChild(media);
      if (fileList) bubble.appendChild(fileList);
    }

    if (message.content) {
      const text = document.createElement('div');
      text.className = 'msg__text';
      text.textContent = message.content;
      bubble.appendChild(text);
    }

    body.appendChild(bubble);
  }

  const meta = document.createElement('div');
  meta.className = 'msg__meta';

  const time = document.createElement('time');
  time.dateTime = message.createdAt;
  time.textContent = formatClock(message.createdAt);
  meta.appendChild(time);

  if (message.stopped) {
    const badge = document.createElement('span');
    badge.className = 'msg__badge';
    badge.textContent = '已停止';
    meta.appendChild(badge);
  }

  if (message.edited) {
    const badge = document.createElement('span');
    badge.className = 'msg__badge';
    badge.textContent = '已编辑';
    meta.appendChild(badge);
  }

  if (message.content) {
    meta.appendChild(metaAction('copy-message', message.id, 'copy', '复制'));
  }

  if (isAssistant && options.isLastAssistant && message.content && !message.error) {
    meta.appendChild(metaAction('regenerate', message.id, 'refresh', '重新生成'));
  }

  if (!isAssistant && message.content) {
    meta.appendChild(metaAction('edit-message', message.id, 'edit', '编辑'));
  }

  body.appendChild(meta);
  article.appendChild(body);
  return article;
}

/* --------------------------------------------------------------------------
   历史会话列表项（含重命名、删除入口）
   handlers: { onSelect, onRename, onDelete }
   -------------------------------------------------------------------------- */

export function createHistoryItem(conversation, isActive, handlers) {
  const item = document.createElement('div');
  item.className = `history__item${isActive ? ' is-active' : ''}`;
  item.dataset.conversationId = conversation.id;

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'history__main';

  const title = document.createElement('span');
  title.className = 'history__title';
  title.textContent = conversation.title;
  main.appendChild(title);

  const time = document.createElement('span');
  time.className = 'history__time';
  time.textContent = handlers.formatTime(conversation.updatedAt);
  main.appendChild(time);

  main.addEventListener('click', () => handlers.onSelect(conversation.id));
  main.addEventListener('dblclick', (event) => {
    event.preventDefault();
    handlers.onRename(conversation.id);
  });

  const rename = document.createElement('button');
  rename.type = 'button';
  rename.className = 'history__rename';
  rename.setAttribute('aria-label', `重命名对话：${conversation.title}`);
  rename.setAttribute('title', '重命名');
  rename.appendChild(icon('edit'));
  rename.addEventListener('click', (event) => {
    event.stopPropagation();
    handlers.onRename(conversation.id);
  });

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'history__delete';
  remove.setAttribute('aria-label', `删除对话：${conversation.title}`);
  remove.setAttribute('title', '删除');
  remove.appendChild(icon('trash'));
  remove.addEventListener('click', (event) => {
    event.stopPropagation();
    handlers.onDelete(conversation.id);
  });

  item.append(main, rename, remove);
  return item;
}

/**
 * 内联重命名输入框。Enter / 失焦提交，Esc 取消。
 */
export function createHistoryRenameInput(initial, { onCommit, onCancel }) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'history__rename-input';
  input.value = initial;
  input.maxLength = 60;
  input.setAttribute('aria-label', '会话标题');

  let settled = false;
  const commit = () => {
    if (settled) return;
    settled = true;
    onCommit(input.value);
  };
  const cancel = () => {
    if (settled) return;
    settled = true;
    onCancel();
  };

  input.addEventListener('keydown', (event) => {
    event.stopPropagation();
    if (event.key === 'Enter') {
      event.preventDefault();
      commit();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    }
  });
  input.addEventListener('click', (event) => event.stopPropagation());
  input.addEventListener('blur', commit);
  return input;
}

/* --------------------------------------------------------------------------
   用户消息内联编辑器
   -------------------------------------------------------------------------- */

export function createEditEditor(initial, { onSubmit, onCancel }) {
  const wrap = document.createElement('div');
  wrap.className = 'msg__edit';

  const textarea = document.createElement('textarea');
  textarea.value = initial;
  textarea.rows = 3;
  textarea.setAttribute('aria-label', '编辑消息');
  wrap.appendChild(textarea);

  const grow = () => {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 260)}px`;
  };
  textarea.addEventListener('input', grow);
  window.requestAnimationFrame(grow);

  const actions = document.createElement('div');
  actions.className = 'msg__edit-actions';

  const hint = document.createElement('span');
  hint.className = 'msg__edit-hint';
  hint.textContent = 'Ctrl + Enter 保存 · Esc 取消';
  actions.appendChild(hint);

  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'ghost-btn';
  cancel.textContent = '取消';
  cancel.addEventListener('click', onCancel);
  actions.appendChild(cancel);

  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'primary-btn';
  save.textContent = '保存并重新发送';
  save.addEventListener('click', () => onSubmit(textarea.value));
  actions.appendChild(save);

  textarea.addEventListener('keydown', (event) => {
    event.stopPropagation();
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      onSubmit(textarea.value);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
    }
  });

  wrap.appendChild(actions);
  return wrap;
}
