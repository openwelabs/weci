/* ==========================================================================
   WeCi · 通用 UI 组件

   负责可复用的 DOM 构建：toast、复制、消息节点、历史列表项、
   内联重命名输入框、消息编辑器。业务编排（点击后做什么）由 main.js
   通过 handlers 回调注入，保持本模块无业务状态。
   ========================================================================== */

import { icon, formatClock } from './utils.js';
import { createProse, enhanceMarkdown, typingEl } from './markdown.js';

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
   -------------------------------------------------------------------------- */

const TOOL_STATUS_LABEL = {
  running: '执行中',
  done: '已完成',
  error: '失败',
};

/**
 * 构建一条工具调用的紧凑卡片。默认只展示工具名、状态、耗时，
 * 不暴露内部参数与工具返回内容（安全要求）。
 */
export function createToolCallCard(tc) {
  const card = document.createElement('div');
  card.className = `tool-call tool-call--${tc.status}`;
  card.dataset.toolCallId = tc.id;

  const head = document.createElement('div');
  head.className = 'tool-call__head';

  const cpu = document.createElement('span');
  cpu.className = 'tool-call__icon';
  cpu.appendChild(icon('cpu'));
  head.appendChild(cpu);

  const name = document.createElement('span');
  name.className = 'tool-call__name';
  name.textContent = tc.name;
  head.appendChild(name);

  const status = document.createElement('span');
  status.className = `tool-call__status tool-call__status--${tc.status}`;
  status.textContent = TOOL_STATUS_LABEL[tc.status] || tc.status;
  head.appendChild(status);

  if (tc.status === 'running') {
    const spinner = document.createElement('span');
    spinner.className = 'tool-call__spinner';
    head.appendChild(spinner);
  }

  if (typeof tc.durationMs === 'number' && tc.status !== 'running') {
    const dur = document.createElement('span');
    dur.className = 'tool-call__duration';
    dur.textContent = `${tc.durationMs.toFixed(0)}ms`;
    head.appendChild(dur);
  }

  card.appendChild(head);

  if (tc.status === 'error' && tc.error) {
    const err = document.createElement('div');
    err.className = 'tool-call__error';
    err.textContent = tc.error;
    card.appendChild(err);
  }

  return card;
}

export function createToolCallsEl(toolCalls) {
  if (!Array.isArray(toolCalls) || !toolCalls.length) return null;
  const wrap = document.createElement('div');
  wrap.className = 'tool-calls';
  toolCalls.forEach((tc) => wrap.appendChild(createToolCallCard(tc)));
  return wrap;
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
    if (message.toolCalls && message.toolCalls.length) {
      const toolCallsEl = createToolCallsEl(message.toolCalls);
      if (toolCallsEl) content.appendChild(toolCallsEl);
    }

    if (message.content) {
      const prose = createProse(message.content);
      enhanceMarkdown(prose);
      content.appendChild(prose);
    } else if (message.error) {
      content.appendChild(buildErrorElement(message));
    } else if (message.stopped) {
      const note = document.createElement('p');
      note.className = 'msg__note';
      note.textContent = '已停止生成。';
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
    bubble.textContent = message.content;
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
